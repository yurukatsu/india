"""リターン系列の要約統計."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PERIODS_PER_YEAR


def ann_return(r: pd.Series, periods: int = PERIODS_PER_YEAR) -> float:
    """幾何平均年率リターン."""
    r = r.dropna()
    if len(r) == 0:
        return np.nan
    return float((1.0 + r).prod() ** (periods / len(r)) - 1.0)


def ann_vol(r: pd.Series, periods: int = PERIODS_PER_YEAR) -> float:
    return float(r.dropna().std(ddof=1) * np.sqrt(periods))


def newey_west_tstat(r: pd.Series, lags: int | None = None) -> float:
    """平均 = 0 に対する Newey-West 補正 t 値."""
    x = r.dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 3:
        return np.nan
    if lags is None:
        lags = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    e = x - x.mean()
    gamma0 = (e @ e) / n
    s = gamma0
    for l in range(1, lags + 1):
        g = (e[l:] @ e[:-l]) / n
        s += 2.0 * (1.0 - l / (lags + 1.0)) * g
    se = np.sqrt(max(s, 0.0) / n)
    return float(x.mean() / se) if se > 0 else np.nan


def max_drawdown(r: pd.Series) -> float:
    cum = (1.0 + r.dropna()).cumprod()
    return float((cum / cum.cummax() - 1.0).min())


def summarize(r: pd.Series, periods: int = PERIODS_PER_YEAR, nw_lags: int | None = None) -> pd.Series:
    """1本のリターン系列の要約."""
    r = r.dropna()
    vol = ann_vol(r, periods)
    ar = ann_return(r, periods)
    return pd.Series({
        "n_months": len(r),
        "ann_return": ar,
        "ann_vol": vol,
        "IR": ar / vol if vol > 0 else np.nan,
        "mean_monthly": r.mean(),
        "t_stat_NW": newey_west_tstat(r, nw_lags),
        "hit_ratio": float((r > 0).mean()),
        "skew": float(r.skew()) if len(r) > 2 else np.nan,
        "worst_month": float(r.min()),
        "max_drawdown": max_drawdown(r),
    })


def summarize_frame(df: pd.DataFrame, periods: int = PERIODS_PER_YEAR, nw_lags: int | None = None) -> pd.DataFrame:
    """列ごとに summarize を適用."""
    return pd.DataFrame({c: summarize(df[c], periods, nw_lags) for c in df.columns}).T
