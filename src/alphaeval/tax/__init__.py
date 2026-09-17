"""キャピタルゲイン課税の評価（税率スケジュール、FIFO ロット台帳シミュレーション）。"""

from alphaeval.tax.ledger import TaxSimulationResult, simulate_after_tax
from alphaeval.tax.schedule import (
    TaxSchedule,
    constant_tax_schedule,
    india_tax_schedule,
    tax_schedule_from_frame,
)

__all__ = [
    "TaxSchedule",
    "TaxSimulationResult",
    "constant_tax_schedule",
    "india_tax_schedule",
    "simulate_after_tax",
    "tax_schedule_from_frame",
]
