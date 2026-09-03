"""Information Coefficient（IC）の計算と減衰分析."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics
from .config import PERIODS_PER_YEAR


def information_coefficient(
    panel: pd.DataFrame,
    score_col: str,
    ret_col: str = "fwd_rtn",
    method: str = "spearman",
    time_level: str = "yyyymm",
    min_obs: int = 20,
) -> pd.Series:
    """月次クロスセクション IC の時系列.

    ``method="spearman"``（順位相関、外れ値に頑健）が既定. ``"pearson"`` も可.
    """
    def _corr(blk: pd.DataFrame) -> float:
        s, r = blk[score_col], blk[ret_col]
        ok = s.notna() & r.notna()
        if ok.sum() < min_obs:
            return np.nan
        return float(s[ok].corr(r[ok], method=method))

    ic = panel.groupby(level=time_level, sort=True).apply(_corr)
    ic.name = f"IC_{score_col}"
    ic.index.name = time_level
    return ic


def ic_summary(ic: pd.Series, periods: int = PERIODS_PER_YEAR) -> pd.Series:
    """IC の要約: 平均 IC・IC標準偏差・ICIR（年率）・t値（通常/Newey-West）・勝率.

    IC 系列は自己相関を持つことがあるため、有意性は ``t_stat_NW`` で見るのが安全.
    """
    x = ic.dropna()
    n = len(x)
    mean, sd = x.mean(), x.std(ddof=1)
    return pd.Series({
        "n_months": n,
        "mean_IC": mean,
        "std_IC": sd,
        "ICIR": mean / sd if sd > 0 else np.nan,
        "ICIR_ann": (mean / sd) * np.sqrt(periods) if sd > 0 else np.nan,
        "t_stat": mean / (sd / np.sqrt(n)) if sd > 0 and n > 1 else np.nan,
        "t_stat_NW": metrics.newey_west_tstat(x),
        "hit_ratio": float((x > 0).mean()),
        "autocorr_1": float(x.autocorr(1)) if n > 2 else np.nan,
    })


def ic_decay(
    panel: pd.DataFrame,
    score_col: str,
    horizons: tuple[int, ...] | list[int] = tuple(range(1, 13)),
    ret_prefix: str = "fwd_rtn_",
    method: str = "spearman",
    time_level: str = "yyyymm",
    periods: int = PERIODS_PER_YEAR,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """IC の減衰: 各ホライズン h（h ヶ月先の単月リターン）に対する IC.

    Returns
    -------
    (ic_ts, summary):
      ic_ts   : 月次 IC の時系列（列 = h）
      summary : h ごとの平均IC・t値・ICIR
    """
    ts = {}
    for h in horizons:
        col = f"{ret_prefix}{h}"
        if col not in panel.columns:
            raise KeyError(f"{col} がパネルにありません（data.add_forward_returns を先に実行）")
        ts[h] = information_coefficient(panel, score_col, col, method=method, time_level=time_level)
    ic_ts = pd.DataFrame(ts)
    summary = pd.DataFrame({h: ic_summary(ic_ts[h], periods) for h in horizons}).T
    summary.index.name = "horizon"
    return ic_ts, summary
