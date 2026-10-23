"""弱気局面でモメンタムのスリーブを切り替える合成スコアを ``input`` の alpha 形式で生成する。

仮説（Daniel & Moskowitz 2016 "Momentum crashes"）: 弱気相場の後は過去の負け組が高ベータになり、モメンタムの
ロングショートは「市場の反発に売り向かう」形になる。反発局面でクラッシュするので、弱気局面ではモメンタムを外す。

``comp_ew`` は 3 スリーブ（``sleeve_a`` = リビジョン、``sleeve_b`` = モメンタム + CGO、``sleeve_d`` = 規律）の
単純平均の Blom 化と一致する。ここでは通常月は ``comp_ew`` と同じ合成にし、弱気局面の月だけ ``sleeve_b`` の
扱いを変える。

局面の判定（月末 :math:`t`、:math:`t` までに確定した情報だけを使う）:

- 弱気: 過去 ``window`` か月（:math:`t-\\text{window}+1 \\ldots t`）の BM の累積対数リターンが負
- 高ボラ: 過去 6 か月の BM 月次リターンの標準偏差が、それまでの分布の 80 パーセンタイルを超える

Examples:
    全スコアを生成::

        uv run python scripts/build_momentum_regime.py

    期間を指定::

        uv run python scripts/build_momentum_regime.py --start 200801 --end 202606
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from alphaeval.dates import to_int
from alphaeval.io import InputStore, write_score_file

logger = logging.getLogger("build_momentum_regime")

DEFAULT_INPUT_ROOT = Path("input") / "msci_india_imi"
GROUP = "comp_regime"
BENCHMARK = "msci_india"
RISK_MODEL = "GEMLT"
SLEEVES = ("sleeve_a", "sleeve_b", "sleeve_d")
MOM_SLEEVE = "sleeve_b"
RESID_SLEEVE = "resid_mom"
BEAR_WINDOW = 24
VOL_WINDOW = 6
VOL_QUANTILE = 0.8
VOL_MIN_OBS = 24
RESID_LOOKBACK = 11  # 残差モメンタム: t-11..t-1 の 11 か月（直近 1 か月を除く）
RESID_MIN_OBS = 9


@dataclass(frozen=True)
class RegimeSpec:
    """生成するスコア 1 本の定義。

    Attributes:
        name (str): スコア名（``alpha/comp_regime/{name}``）。
        state (str): 切り替えの局面（``bear`` / ``hivol`` / ``always``）。
        weights (Mapping[str, float]): 局面にあたる月のスリーブウェイト（通常月は 3 スリーブ等ウェイト）。
        description (str): README に書く説明。
    """

    name: str
    state: str
    weights: Mapping[str, float]
    description: str


REGIME_SPECS: tuple[RegimeSpec, ...] = (
    RegimeSpec(
        "comp_bear0",
        "bear",
        {"sleeve_a": 1.0, "sleeve_d": 1.0},
        "弱気局面（過去 24 か月の BM < 0）では sleeve_b を外す（本命）",
    ),
    RegimeSpec(
        "comp_bear_half",
        "bear",
        {"sleeve_a": 1.0, "sleeve_b": 0.5, "sleeve_d": 1.0},
        "弱気局面では sleeve_b のウェイトを半分にする",
    ),
    RegimeSpec(
        "comp_bear_resid",
        "bear",
        {"sleeve_a": 1.0, RESID_SLEEVE: 1.0, "sleeve_d": 1.0},
        "弱気局面では sleeve_b を残差モメンタム（GEMLT 固有リターン）に置き換える",
    ),
    RegimeSpec(
        "comp_hivol0",
        "hivol",
        {"sleeve_a": 1.0, "sleeve_d": 1.0},
        "高ボラ局面（過去 6 か月の BM ボラ > それまでの 80 パーセンタイル）では sleeve_b を外す（頑健性の確認）",
    ),
    RegimeSpec(
        "comp_ad",
        "always",
        {"sleeve_a": 1.0, "sleeve_d": 1.0},
        "常に sleeve_b を外す（条件付きにする意味の確認）",
    ),
)


def blom_score(values: pd.DataFrame) -> pd.DataFrame:
    """各行（日付）内で Blom 正規スコアに変換する。

    Args:
        values (pd.DataFrame): 日付 × 銘柄の値（欠損は NaN のまま）。

    Returns:
        pd.DataFrame: 同じ形の正規スコア。

    Examples:
        >>> df = pd.DataFrame({"A": [1.0], "B": [2.0], "C": [3.0]}, index=[202001])
        >>> blom_score(df).round(3).iloc[0].tolist()
        [-0.869, 0.0, 0.869]
    """
    ranks = values.rank(axis=1, method="average")
    n = values.notna().sum(axis=1)
    u = ranks.sub(3.0 / 8.0).div(n.add(1.0 / 4.0), axis=0)
    return pd.DataFrame(
        norm.ppf(u.to_numpy(dtype=float)), index=values.index, columns=values.columns
    )


def weighted_blend(
    sleeves: Mapping[str, pd.DataFrame], weights: Mapping[str, float]
) -> pd.DataFrame:
    """スリーブの加重平均（銘柄ごとに存在するスリーブだけでウェイトを再正規化）を Blom 化する。

    Args:
        sleeves (Mapping[str, pd.DataFrame]): スリーブ名 → 日付 × 銘柄の Blom スコア。
        weights (Mapping[str, float]): スリーブ名 → ウェイト（0 のスリーブは使わない）。

    Returns:
        pd.DataFrame: 日付 × 銘柄の Blom スコア。

    Examples:
        >>> a = pd.DataFrame({"X": [1.0], "Y": [-1.0], "Z": [0.0]}, index=[0])
        >>> b = pd.DataFrame({"X": [-1.0], "Y": [np.nan], "Z": [1.0]}, index=[0])
        >>> weighted_blend({"a": a, "b": b}, {"a": 1.0, "b": 0.0}).round(3).iloc[0].tolist()
        [0.869, -0.869, 0.0]
    """
    total: pd.DataFrame | None = None
    wsum: pd.DataFrame | None = None
    for name, w in weights.items():
        if w == 0:
            continue
        s = sleeves[name]
        part = s.fillna(0.0) * w
        wpart = s.notna().astype(float) * w
        total = part if total is None else total.add(part, fill_value=0.0)
        wsum = wpart if wsum is None else wsum.add(wpart, fill_value=0.0)
    assert total is not None and wsum is not None
    return blom_score((total / wsum).where(wsum > 0))


def benchmark_returns(benchmark: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """前月末の BM ウェイト × 当月の銘柄リターンで BM の月次リターンを作る。

    Args:
        benchmark (pd.DataFrame): 月末 × 銘柄の BM ウェイト（行 :math:`t` は :math:`t` 時点のウェイト）。
        returns (pd.DataFrame): 月末 × 銘柄のリターン（行 :math:`t` は月 :math:`t` のリターン）。

    Returns:
        pd.Series: 月末 → 月 :math:`t` の BM リターン（ウェイトが無い月は NaN）。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29"])
        >>> w = pd.DataFrame({"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=idx)
        >>> r = pd.DataFrame({"A": [0.1, 0.2], "B": [0.0, 0.0]}, index=idx)
        >>> benchmark_returns(w, r).round(3).tolist()
        [nan, 0.1]
    """
    w = benchmark.reindex(index=returns.index, columns=returns.columns).shift(1)
    avail = w.where(returns.notna())
    den = avail.sum(axis=1)
    num = (avail * returns).sum(axis=1)
    return (num / den).where(den > 0)


def bear_state(bm_returns: pd.Series, window: int = BEAR_WINDOW) -> pd.Series:
    """弱気局面: 過去 ``window`` か月の BM の累積対数リターンが負（データ不足の月は ``False``）。

    Args:
        bm_returns (pd.Series): 月末 → 月次の BM リターン。
        window (int): 判定期間（月）。

    Returns:
        pd.Series: 月末 → 弱気局面か。

    Examples:
        >>> idx = pd.date_range("2020-01-31", periods=4, freq="M")
        >>> bear_state(pd.Series([0.1, -0.2, -0.1, 0.3], index=idx), window=2).tolist()
        [False, True, True, False]
    """
    cum = np.log1p(bm_returns).rolling(window, min_periods=window).sum()
    return (cum < 0).where(cum.notna(), False).astype(bool)


def hivol_state(
    bm_returns: pd.Series,
    window: int = VOL_WINDOW,
    quantile: float = VOL_QUANTILE,
    min_obs: int = VOL_MIN_OBS,
) -> pd.Series:
    """高ボラ局面: 過去 ``window`` か月のボラが、:math:`t` までの分布の ``quantile`` を超える。

    Args:
        bm_returns (pd.Series): 月末 → 月次の BM リターン。
        window (int): ボラの計算期間（月）。
        quantile (float): しきい値のパーセンタイル。
        min_obs (int): しきい値の計算に必要なボラの最小点数。

    Returns:
        pd.Series: 月末 → 高ボラ局面か（データ不足の月は ``False``）。

    Examples:
        >>> idx = pd.date_range("2020-01-31", periods=8, freq="M")
        >>> r = pd.Series([0.01, -0.01, 0.01, -0.01, 0.01, -0.01, 0.2, -0.2], index=idx)
        >>> hivol_state(r, window=2, quantile=0.8, min_obs=3).tolist()
        [False, False, False, False, False, False, True, True]
    """
    vol = bm_returns.rolling(window, min_periods=window).std()
    thr = vol.expanding(min_periods=min_obs).quantile(quantile)
    return (vol > thr).where(thr.notna(), False).astype(bool)


def residual_momentum(
    specific: pd.DataFrame,
    lookback: int = RESID_LOOKBACK,
    min_obs: int = RESID_MIN_OBS,
) -> pd.DataFrame:
    """残差モメンタム（Blitz, Huij & Martens 2011）: 固有リターンの和 / 標準偏差（直近 1 か月を除く）。

    Args:
        specific (pd.DataFrame): 月末 × 銘柄の固有リターン（行 :math:`t` は月 :math:`t`）。
        lookback (int): 使う月数（:math:`t-\\text{lookback} \\ldots t-1`）。
        min_obs (int): 必要な最小月数。

    Returns:
        pd.DataFrame: 月末 × 銘柄のスコア。

    Examples:
        >>> idx = pd.date_range("2020-01-31", periods=4, freq="M")
        >>> s = pd.DataFrame({"A": [0.01, 0.03, 0.02, 0.5]}, index=idx)
        >>> residual_momentum(s, lookback=2, min_obs=2)["A"].round(3).tolist()
        [nan, nan, 2.828, 7.071]
    """
    lagged = specific.shift(1)
    s = lagged.rolling(lookback, min_periods=min_obs).sum()
    sd = lagged.rolling(lookback, min_periods=min_obs).std()
    return s / sd.where(sd > 0)


def build_regime_scores(
    sleeves: Mapping[str, pd.DataFrame],
    states: Mapping[str, pd.Series],
    specs: Sequence[RegimeSpec] = REGIME_SPECS,
) -> dict[str, pd.DataFrame]:
    """通常月は 3 スリーブ等ウェイト、局面にあたる月は ``spec.weights`` で合成する。

    Args:
        sleeves (Mapping[str, pd.DataFrame]): スリーブ名 → 日付 × 銘柄の Blom スコア（残差モメンタムを含む）。
        states (Mapping[str, pd.Series]): 局面名 → 月末 → bool（``always`` は全月 ``True``）。
        specs (Sequence[RegimeSpec]): スコア定義。

    Returns:
        dict[str, pd.DataFrame]: スコア名 → 日付 × 銘柄の Blom スコア。

    Examples:
        >>> out = build_regime_scores(sleeves, {"bear": bear, "hivol": hv, "always": al})  # doctest: +SKIP
    """
    base = weighted_blend(sleeves, {s: 1.0 for s in SLEEVES})
    out = {}
    for spec in specs:
        alt = weighted_blend(sleeves, spec.weights)
        flag = states[spec.state].reindex(base.index).fillna(False).astype(bool)
        cond = pd.DataFrame(
            np.repeat(flag.to_numpy()[:, None], base.shape[1], axis=1),
            index=base.index,
            columns=base.columns,
        )
        out[spec.name] = alt.where(cond, base)
    return out


def write_scores(
    scores: Mapping[str, pd.DataFrame],
    states: Mapping[str, pd.Series],
    dest_root: Path,
    specs: Sequence[RegimeSpec] = REGIME_SPECS,
) -> None:
    """スコアを ``{dest_root}/{name}/YYYYMM.dat`` に書き出し、README を作る。

    Args:
        scores (Mapping[str, pd.DataFrame]): スコア名 → 日付 × 銘柄。
        states (Mapping[str, pd.Series]): 局面名 → 月末 → bool。
        dest_root (Path): ``alpha/comp_regime`` ディレクトリ。
        specs (Sequence[RegimeSpec]): README に載せる定義。

    Returns:
        None

    Examples:
        >>> write_scores(scores, states, Path("input/msci_india_imi/alpha/comp_regime"))  # doctest: +SKIP
    """
    lines = []
    for spec in specs:
        panel = scores[spec.name]
        n, first, last = 0, None, None
        for date, row in panel.iterrows():
            v = row.dropna()
            if v.empty:
                continue
            write_score_file(
                dest_root / spec.name / f"{to_int(date)}.dat", spec.name, v
            )
            n += 1
            first = first or to_int(date)
            last = to_int(date)
        n_flag = int(states[spec.state].reindex(panel.index).fillna(False).sum())
        logger.info(
            "%s: %d ファイル（%s〜%s）、切り替え %d か月",
            spec.name,
            n,
            first,
            last,
            n_flag,
        )
        w = ", ".join(f"{k} {v:g}" for k, v in spec.weights.items())
        lines.append(
            f"| `{spec.name}` | {spec.description} | {w} | {n_flag} | {first}〜{last} |"
        )
    bear_months = [to_int(d) for d, f in states["bear"].items() if f]
    (dest_root / "README.md").write_text(
        "# 局面でモメンタムを切り替える合成スコア\n\n"
        "`scripts/build_momentum_regime.py` で `composite` のスリーブから生成。通常月は `comp_ew` と同じ"
        "（3 スリーブ等ウェイトの Blom 化）、局面にあたる月だけスリーブのウェイトを変える。"
        "局面は月末時点で確定した BM（MSCI India）の月次リターンで判定。\n\n"
        "| スコア | 定義 | 局面の月のウェイト | 切り替え月数 | 期間 |\n| --- | --- | --- | --- | --- |\n"
        + "\n".join(lines)
        + f"\n\n弱気局面の月（{len(bear_months)} か月）: "
        + ", ".join(str(m) for m in bear_months)
        + "\n",
        encoding="utf-8",
    )


def load_inputs(
    store: InputStore, start: int, end: int
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.Series]:
    """ユニバース、スリーブ（残差モメンタムを含む）、BM 月次リターンを読む。

    Args:
        store (InputStore): 入力ストア。
        start (int): 開始年月。
        end (int): 終了年月。

    Returns:
        tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.Series]: ユニバース、スリーブ名 → Blom スコア、
        BM 月次リターン。

    Examples:
        >>> univ, sleeves, bmr = load_inputs(InputStore("input/msci_india_imi"), 200801, 202606)  # doctest: +SKIP
    """
    univ = store.universe(start, end)
    mask = univ.notna()
    sleeves = {
        s: blom_score(
            store.alpha(f"composite/{s}", start, end)
            .reindex(index=univ.index, columns=univ.columns)
            .where(mask)
        )
        for s in SLEEVES
    }
    specific = store.returns(RISK_MODEL, "srtn", start, end).reindex(
        columns=univ.columns
    )
    resid = residual_momentum(specific).reindex(index=univ.index)
    sleeves[RESID_SLEEVE] = blom_score(resid.where(mask))
    bm = store.benchmark(BENCHMARK, start, end)
    rtn = store.returns(RISK_MODEL, "rtn", start, end)
    bmr = benchmark_returns(bm, rtn).reindex(univ.index)
    return univ, sleeves, bmr


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解釈する。

    Args:
        argv (Sequence[str] | None): 引数（``None`` なら ``sys.argv``）。

    Returns:
        argparse.Namespace: 解釈結果。

    Examples:
        >>> parse_args(["--start", "200801"]).start
        200801
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    p.add_argument("--start", type=int, default=200801)
    p.add_argument("--end", type=int, default=202606)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """スコアを生成して書き出す。

    Args:
        argv (Sequence[str] | None): 引数。

    Returns:
        None

    Examples:
        >>> main(["--start", "200801", "--end", "202606"])  # doctest: +SKIP
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    store = InputStore(args.input_root)
    univ, sleeves, bmr = load_inputs(store, args.start, args.end)
    states = {
        "bear": bear_state(bmr),
        "hivol": hivol_state(bmr),
        "always": pd.Series(True, index=univ.index),
    }
    scores = build_regime_scores(sleeves, states)
    base = weighted_blend(sleeves, {s: 1.0 for s in SLEEVES})
    ref = blom_score(
        store.alpha("composite/comp_ew", args.start, args.end)
        .reindex(index=univ.index, columns=univ.columns)
        .where(univ.notna())
    )
    diff = float((base - ref).abs().max().max())
    logger.info("通常月の合成と comp_ew の最大差: %.2e", diff)
    write_scores(scores, states, args.input_root / "alpha" / GROUP)


if __name__ == "__main__":
    main()
