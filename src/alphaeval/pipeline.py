"""アルファ評価パイプライン: カバレッジ・IC・分位分析・基本指標・税控除後評価を一括で実行する。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from alphaeval.coverage import coverage, coverage_summary
from alphaeval.dates import ensure_datetime_index
from alphaeval.ic import ic_decay, ic_summary
from alphaeval.metrics import performance_summary, performance_table
from alphaeval.quantile import QuantileResult, portfolio_returns, quantile_analysis
from alphaeval.tax import TaxSchedule, TaxSimulationResult, simulate_after_tax


@dataclass
class AlphaEvaluation:
    """:func:`evaluate_alpha` の結果。

    Attributes:
        coverage (pd.DataFrame): 日付ごとのカバレッジ。
        coverage_summary (pd.Series): カバレッジ要約。
        ic (pd.DataFrame): 日付 × ホライズンの IC。
        ic_summary (pd.DataFrame): ホライズン × 統計量。
        quantiles (dict[float, QuantileResult]): バッファ幅 → 分位分析結果。
        benchmark_returns (pd.Series | None): ベンチマークリターン系列。
        universe_returns (pd.Series | None): ユニバース（ウェイト加重）リターン系列。
        tax (dict[tuple[float, int], TaxSimulationResult]): ``(バッファ幅, 分位)`` → 税シミュレーション結果。
        summary (pd.DataFrame): バッファ幅 × 分位ごとの基本指標（税控除後を含む）。
    """

    coverage: pd.DataFrame
    coverage_summary: pd.Series
    ic: pd.DataFrame
    ic_summary: pd.DataFrame
    quantiles: dict[float, QuantileResult]
    benchmark_returns: pd.Series | None
    universe_returns: pd.Series | None
    tax: dict[tuple[float, int], TaxSimulationResult] = field(default_factory=dict)
    summary: pd.DataFrame = field(default_factory=pd.DataFrame)


def evaluate_alpha(
    score: pd.DataFrame,
    universe: pd.DataFrame,
    returns: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    size: pd.DataFrame | None = None,
    n_quantiles: int = 5,
    buffers: Sequence[float] = (0.0,),
    scheme: str = "equal",
    max_weight: float | None = None,
    higher_is_better: bool = True,
    ic_horizons: Sequence[int] = (1, 3, 6, 12),
    ic_method: str = "spearman",
    tax_schedule: TaxSchedule | None = None,
    tax_quantiles: Sequence[int] = (1,),
    cost_buy: float = 0.0,
    cost_sell: float = 0.0,
    periods_per_year: int = 12,
    burn_in: int = 0,
) -> AlphaEvaluation:
    """1 本のスコアを一通り評価する。

    Args:
        score (pd.DataFrame): 日付 × 銘柄のスコア。
        universe (pd.DataFrame): 日付 × 銘柄のユニバースウェイト（非構成銘柄は NaN）。
        returns (pd.DataFrame): 日付 × 銘柄の期間リターン（小数）。
        benchmark (pd.DataFrame | None): 日付 × 銘柄のベンチマークウェイト。
        size (pd.DataFrame | None): 時価総額パネル（size 系ウェイト用。``None`` なら ``universe``）。
        n_quantiles (int): 分位数。
        buffers (Sequence[float]): 試すバッファ幅（0 は通常分位）。
        scheme (str): ``"equal"`` / ``"size"`` / ``"sqrt_size"``。
        max_weight (float | None): 分位内 1 銘柄上限。
        higher_is_better (bool): スコアが高いほど良いか。
        ic_horizons (Sequence[int]): IC を計算するホライズン。
        ic_method (str): ``"spearman"`` または ``"pearson"``。
        tax_schedule (TaxSchedule | None): 指定時は ``tax_quantiles`` の分位について税控除後評価を行う。
        tax_quantiles (Sequence[int]): 税シミュレーションを行う分位番号。
        cost_buy (float): 買付コスト率。
        cost_sell (float): 売却コスト率。
        periods_per_year (int): 年率換算の期間数。
        burn_in (int): 指標計算から除外する先頭期間数（税評価の初期化バイアス対策。§5.4）。

    Returns:
        AlphaEvaluation: 評価結果。

    Examples:
        >>> from alphaeval.io import InputStore  # doctest: +SKIP
        >>> store = InputStore("input/msci_india_imi")  # doctest: +SKIP
        >>> res = evaluate_alpha(
        ...     store.alpha("core/roe_act", 200812, 202606), store.universe(200812, 202606),
        ...     store.returns("GEMLTL", "rtn", 200812, 202607), store.benchmark("msci_india", 200812, 202606),
        ...     buffers=(0.0, 0.1, 0.2), tax_schedule=india_tax_schedule(), cost_buy=0.001, cost_sell=0.002,
        ... )  # doctest: +SKIP
        >>> res.summary  # doctest: +SKIP
    """
    score = ensure_datetime_index(score)
    universe = ensure_datetime_index(universe)
    returns = ensure_datetime_index(returns)
    score = score.reindex(index=universe.index)

    cov = coverage(score, universe)
    ic = ic_decay(score, returns, ic_horizons, universe, ic_method)
    ic_sum = pd.DataFrame({h: ic_summary(ic[h], periods_per_year) for h in ic.columns})

    bm_ret = (
        portfolio_returns(ensure_datetime_index(benchmark), returns)
        if benchmark is not None
        else None
    )
    univ_ret = portfolio_returns(universe, returns)

    quantiles: dict[float, QuantileResult] = {}
    tax_results: dict[tuple[float, int], TaxSimulationResult] = {}
    rows: dict[tuple[str, str], pd.Series] = {}
    for b in buffers:
        qres = quantile_analysis(
            score,
            returns,
            universe,
            n_quantiles,
            b,
            scheme,
            size,
            max_weight,
            higher_is_better,
        )
        quantiles[b] = qres
        ret = qres.returns.iloc[burn_in:]
        to = qres.turnover.iloc[burn_in:]
        bm_slice = bm_ret.reindex(ret.index) if bm_ret is not None else None
        table = performance_table(ret, bm_slice, to, periods_per_year)
        for col in table.columns:
            rows[(f"buffer={b:g}", col)] = table[col]
        if tax_schedule is not None:
            for q in tax_quantiles:
                sim = simulate_after_tax(
                    qres.weights[q], returns, tax_schedule, cost_buy, cost_sell
                )
                tax_results[(b, q)] = sim
                label = qres.labels[q - 1]
                net = sim.returns.iloc[burn_in:]
                base = rows[(f"buffer={b:g}", label)]
                extra = {}
                for stage in ("r_net_tc", "r_net"):
                    ps = performance_summary(
                        net[stage], bm_slice, None, periods_per_year
                    )
                    extra[f"{stage}_ann_return"] = ps["ann_return"]
                    extra[f"{stage}_return_risk"] = ps["return_risk"]
                    if bm_slice is not None:
                        extra[f"{stage}_active_return"] = ps["active_return"]
                        extra[f"{stage}_information_ratio"] = ps["information_ratio"]
                annual = sim.annual
                extra["tax_drag_annual"] = (
                    base["ann_return"]
                    - extra["r_net_ann_return"]
                    - (base["ann_return"] - extra["r_net_tc_ann_return"])
                )
                extra["cost_drag_annual"] = (
                    base["ann_return"] - extra["r_net_tc_ann_return"]
                )
                extra["short_ratio_mean"] = annual["short_ratio"].mean()
                extra["liquidation_tax_ratio_end"] = float(
                    sim.monthly["liquidation_tax"].iloc[-1]
                    / sim.monthly["value"].iloc[-1]
                )
                rows[(f"buffer={b:g}", label)] = pd.concat([base, pd.Series(extra)])
    summary = pd.DataFrame(rows)
    summary.columns = pd.MultiIndex.from_tuples(
        summary.columns, names=["buffer", "portfolio"]
    )
    return AlphaEvaluation(
        coverage=cov,
        coverage_summary=coverage_summary(cov),
        ic=ic,
        ic_summary=ic_sum,
        quantiles=quantiles,
        benchmark_returns=bm_ret,
        universe_returns=univ_ret,
        tax=tax_results,
        summary=summary,
    )
