"""分位分析モジュールのテスト。"""

import numpy as np
import pandas as pd

from alphaeval.quantile import (
    buffered_membership,
    cap_weights,
    portfolio_returns,
    quantile_analysis,
    rank_percentile,
    turnover,
    weight_portfolio,
)

IDX2 = pd.to_datetime(["2020-01-31", "2020-02-29"])


def test_buffered_membership_matches_doc_example() -> None:
    """docs/india_tcg.md §3.3 の数値例（N=10, θ_in=0.8, θ_out=0.6）。"""
    p = pd.DataFrame(
        {"A": [0.95, 0.90], "B": [0.85, 0.75], "C": [0.70, 0.85], "D": [0.85, 0.50]},
        index=IDX2,
    )
    plain = (p >= 0.8).iloc[1]
    buffered = buffered_membership(p, 0.8, lower_out=0.6).iloc[1]
    assert plain.to_dict() == {"A": True, "B": False, "C": True, "D": False}
    assert buffered.to_dict() == {"A": True, "B": True, "C": True, "D": False}


def test_buffered_membership_drops_when_leaving_universe() -> None:
    p = pd.DataFrame(
        {"A": [0.9, np.nan, 0.7]},
        index=pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"]),
    )
    m = buffered_membership(p, 0.8, buffer=0.2)
    assert m["A"].tolist() == [True, False, False]


def test_rank_percentile_top_quintile_threshold() -> None:
    s = pd.DataFrame(
        [np.arange(10, dtype=float)], index=[202001], columns=list("ABCDEFGHIJ")
    )
    p = rank_percentile(s)
    assert int((p >= 0.8).sum(axis=1).iloc[0]) == 2


def test_cap_weights_iterative() -> None:
    w = pd.Series({"A": 0.6, "B": 0.3, "C": 0.1})
    out = cap_weights(w, 0.4)
    assert abs(out.sum() - 1) < 1e-12
    assert out.max() <= 0.4 + 1e-12
    assert np.allclose(out.reindex(["A", "B", "C"]), [0.4, 0.4, 0.2])


def test_weight_portfolio_size_and_cap() -> None:
    m = pd.DataFrame(
        {"A": [True], "B": [True], "C": [True], "D": [False]}, index=[202001]
    )
    size = pd.DataFrame(
        {"A": [80.0], "B": [15.0], "C": [5.0], "D": [100.0]}, index=[202001]
    )
    w = weight_portfolio(m, "size", size).iloc[0]
    assert abs(w["A"] - 0.8) < 1e-12 and np.isnan(w["D"])
    w = weight_portfolio(m, "size", size, max_weight=0.5).iloc[0]
    assert abs(w["A"] - 0.5) < 1e-12 and abs(w.sum() - 1) < 1e-12


def test_turnover_with_drift() -> None:
    w = pd.DataFrame({"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=IDX2)
    r = pd.DataFrame({"A": [0.0, 0.1], "B": [0.0, -0.1]}, index=IDX2)
    # ドリフト後 A=0.55, B=0.45 → 0.5/0.5 に戻すので片道 0.05
    assert abs(turnover(w, r).iloc[0] - 0.05) < 1e-12
    assert turnover(w).iloc[0] == 0.0


def test_portfolio_returns_alignment() -> None:
    idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
    w = pd.DataFrame({"A": [1.0, 0.0], "B": [0.0, 1.0]}, index=idx[:2])
    r = pd.DataFrame({"A": [0.5, 0.1, 0.3], "B": [0.5, 0.2, 0.4]}, index=idx)
    pr = portfolio_returns(w, r)
    assert pr.index.tolist() == idx[1:].tolist()
    assert abs(pr.iloc[0] - 0.1) < 1e-12  # 1月末に A 100% → 2月の A のリターン
    assert abs(pr.iloc[1] - 0.4) < 1e-12  # 2月末に B 100% → 3月の B のリターン


def test_quantile_analysis_shapes() -> None:
    rng = np.random.default_rng(0)
    idx = pd.date_range("2020-01-31", periods=6, freq="M")
    codes = [f"S{i}" for i in range(20)]
    score = pd.DataFrame(rng.normal(size=(6, 20)), index=idx, columns=codes)
    rtn = pd.DataFrame(rng.normal(0, 0.05, size=(6, 20)), index=idx, columns=codes)
    univ = pd.DataFrame(1.0 / 20, index=idx, columns=codes)
    res = quantile_analysis(score, rtn, univ, n_quantiles=4, buffer=0.1)
    assert list(res.returns.columns) == ["Q1", "Q2", "Q3", "Q4", "spread"]
    assert len(res.returns) == 5
    for q in range(1, 5):
        sums = res.weights[q].sum(axis=1)
        assert np.allclose(sums, 1.0)


def test_weight_portfolio_treats_nan_as_not_member() -> None:
    """列集合の異なる bool パネルの & で生じる NaN 列は非組入として扱う（NaN.astype(bool) は True になるため）。"""
    idx = pd.to_datetime(["2020-01-31"])
    a = pd.DataFrame({"A": [True], "B": [False]}, index=idx)
    b = pd.DataFrame({"A": [True], "C": [True]}, index=idx)
    m = a & b  # 列 B, C は NaN（float）になる
    w = weight_portfolio(m).iloc[0]
    assert w["A"] == 1.0 and w.drop("A").isna().all()


def test_cap_weights_relaxes_infeasible_cap() -> None:
    """cap < 1/n のときは 1/n に緩めて等ウェイトにする（例外にしない）。"""
    w = pd.Series({"A": 0.7, "B": 0.2, "C": 0.1})
    out = cap_weights(w, 0.1)
    assert np.allclose(out, 1 / 3)
