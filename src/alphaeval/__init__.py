"""alphaeval: ``input/template`` スキーマに基づくアルファ評価ライブラリ。

特定のデータセットに依存せず、日付 × 銘柄のワイドパネル（``pandas.DataFrame``）で動作する。

主な入口:

- :class:`alphaeval.io.InputStore`: ``univ`` / ``bm`` / ``alpha`` / ``risk_models`` の読み込み
- :func:`alphaeval.coverage.coverage`: カバレッジ
- :func:`alphaeval.ic.ic_decay`: IC
- :func:`alphaeval.quantile.quantile_analysis`: 分位分析（バッファ付き対応）
- :func:`alphaeval.metrics.performance_table`: Return / Risk / R/R / Turnover / TE / IR
- :func:`alphaeval.tax.simulate_after_tax`: FIFO ロット台帳による税控除後評価
- :func:`alphaeval.pipeline.evaluate_alpha`: 上記の一括実行
"""

from alphaeval.coverage import coverage, coverage_summary
from alphaeval.ic import ic_decay, ic_summary, information_coefficient
from alphaeval.io import InputStore
from alphaeval.metrics import performance_summary, performance_table
from alphaeval.panel import forward_returns, period_returns
from alphaeval.pipeline import AlphaEvaluation, evaluate_alpha
from alphaeval.quantile import (
    QuantileResult,
    buffered_membership,
    cap_weights,
    portfolio_returns,
    quantile_analysis,
    rank_percentile,
    turnover,
    weight_portfolio,
)
from alphaeval.tax import (
    TaxSchedule,
    TaxSimulationResult,
    constant_tax_schedule,
    india_tax_schedule,
    simulate_after_tax,
    tax_schedule_from_frame,
)

__all__ = [
    "AlphaEvaluation",
    "InputStore",
    "QuantileResult",
    "TaxSchedule",
    "TaxSimulationResult",
    "buffered_membership",
    "cap_weights",
    "constant_tax_schedule",
    "coverage",
    "coverage_summary",
    "evaluate_alpha",
    "forward_returns",
    "ic_decay",
    "ic_summary",
    "india_tax_schedule",
    "information_coefficient",
    "performance_summary",
    "performance_table",
    "period_returns",
    "portfolio_returns",
    "quantile_analysis",
    "rank_percentile",
    "simulate_after_tax",
    "tax_schedule_from_frame",
    "turnover",
    "weight_portfolio",
]
