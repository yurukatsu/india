"""report モジュールのテスト（合成データ）。"""

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd

from alphaeval import (
    compute_alpha_report,
    constant_tax_schedule,
    plot_cross_alpha_bars,
    plot_cumulative,
    summary_table,
)
from alphaeval.report import cumulate


def _synthetic(seed: int = 0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-31", periods=30, freq="M")
    codes = [f"S{i:02d}" for i in range(40)]
    score = pd.DataFrame(rng.normal(size=(30, 40)), index=idx, columns=codes)
    noise = rng.normal(0, 0.05, size=(30, 40))
    # スコアが翌月リターンに弱く効くように
    drtn = pd.DataFrame(
        noise + 0.01 * score.shift(1).fillna(0).to_numpy(), index=idx, columns=codes
    )
    srtn = drtn - drtn.mean(axis=1).to_numpy()[:, None]
    univ = pd.DataFrame(rng.uniform(1, 10, size=(30, 40)), index=idx, columns=codes)
    bm = univ.iloc[:, :20]
    bm = bm.div(bm.sum(axis=1), axis=0)  # ベンチマークは合計 1
    return score, univ, bm, {"drtn": drtn, "srtn": srtn}


def test_cumulate() -> None:
    s = pd.Series([0.1, np.nan, 0.1])
    assert np.allclose(cumulate(s, "compound"), [0.1, 0.1, 0.21])
    assert np.allclose(cumulate(s, "sum"), [0.1, 0.1, 0.2])


def test_compute_report_and_outputs() -> None:
    score, univ, bm, returns = _synthetic()
    rep = compute_alpha_report(
        "x",
        score,
        univ,
        bm,
        returns,
        tax_schedule=constant_tax_schedule(0.2, 0.1),
        cost_buy=0.001,
        cost_sell=0.001,
        burn_in=3,
    )
    assert rep.top == "Q5" and rep.bottom == "Q1"
    assert set(rep.series) == {
        "ew_excess",
        "spread",
        "cw_excess",
        "after_tax_excess",
        "ic",
    }
    assert rep.kinds == ["drtn", "srtn"] and rep.cumulation == {
        "drtn": "compound",
        "srtn": "sum",
    }
    ew = rep.series["ew_excess"]["drtn"]
    # 分位リターンは weights の 2 番目の日付から（最後の日付は翌月リターンが無い）→ 29 行、バーンイン 3 で 26 行
    assert len(ew) == 26 and ew.index[0] == pd.Timestamp("2020-05-31")
    # 税後 = グロス − (コスト + 税)/V。税は損失実現月に負（戻し）になり得るので、平均ドラッグが正であることを確認
    net = rep.series["after_tax_excess"]["drtn"]
    assert (ew - net).mean() > 0 and (net < ew).any()
    # srtn の税後はグロス srtn 超過から同じドラッグを引く
    drag = ew - net
    assert np.allclose(
        rep.series["ew_excess"]["srtn"] - rep.series["after_tax_excess"]["srtn"], drag
    )
    tbl = summary_table({"x": rep, "y": rep})
    assert ("drtn", "Q5 EW 超過 (vs BM)") in tbl.columns and (
        "共通",
        "税ドラッグ",
    ) in tbl.columns
    assert tbl.loc["x", ("共通", "回転率 EW")] > 0
    fig = plot_cumulative({"x": rep, "y": rep})
    assert len(fig.axes) == 6 * 2
    fig2 = plot_cross_alpha_bars({"x": rep, "y": rep})
    assert len(fig2.axes) == 5 * 2
