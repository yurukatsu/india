"""基本パフォーマンス指標（Return / Risk / R/R / Turnover / TE / IR / Drawdown）。

すべて期間リターン（小数）の系列を入力とし、``periods_per_year`` で年率換算する。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def cumulative_return(returns: pd.Series) -> pd.Series:
    """累積リターン :math:`\\prod (1 + r) - 1` の系列を返す。

    Args:
        returns (pd.Series): 期間リターン（小数）。NaN は 0 として扱う。

    Returns:
        pd.Series: 累積リターン。

    Examples:
        >>> cumulative_return(pd.Series([0.1, 0.1])).round(3).tolist()
        [0.1, 0.21]
    """
    return (1.0 + returns.fillna(0.0)).cumprod() - 1.0


def annualized_return(returns: pd.Series, periods_per_year: int = 12) -> float:
    """幾何平均ベースの年率リターンを返す。

    Args:
        returns (pd.Series): 期間リターン（小数）。
        periods_per_year (int): 1 年あたりの期間数。

    Returns:
        float: 年率リターン。観測が無ければ NaN。

    Examples:
        >>> round(annualized_return(pd.Series([0.01] * 12)), 6)
        0.126825
    """
    r = returns.dropna()
    if r.empty:
        return np.nan
    growth = float((1.0 + r).prod())
    if growth <= 0:
        return -1.0
    return growth ** (periods_per_year / len(r)) - 1.0


def annualized_volatility(returns: pd.Series, periods_per_year: int = 12) -> float:
    """年率ボラティリティ（標本標準偏差 × √期間数）を返す。

    Args:
        returns (pd.Series): 期間リターン（小数）。
        periods_per_year (int): 1 年あたりの期間数。

    Returns:
        float: 年率ボラティリティ。観測が 2 未満なら NaN。

    Examples:
        >>> round(annualized_volatility(pd.Series([0.01, -0.01, 0.01, -0.01])), 4)
        0.04
    """
    r = returns.dropna()
    if len(r) < 2:
        return np.nan
    return float(r.std(ddof=1)) * np.sqrt(periods_per_year)


def max_drawdown(returns: pd.Series) -> float:
    """最大ドローダウン（負の値）を返す。

    Args:
        returns (pd.Series): 期間リターン（小数）。

    Returns:
        float: 最大ドローダウン（例: -0.25）。

    Examples:
        >>> max_drawdown(pd.Series([0.1, -0.5, 0.2]))
        -0.5
    """
    wealth = (1.0 + returns.fillna(0.0)).cumprod()
    peak = wealth.cummax()
    dd = wealth / peak - 1.0
    return float(dd.min()) if len(dd) else np.nan


def performance_summary(
    returns: pd.Series,
    benchmark: pd.Series | None = None,
    turnover: pd.Series | None = None,
    periods_per_year: int = 12,
    risk_free: float = 0.0,
) -> pd.Series:
    """1 系列のパフォーマンス要約を返す。

    Args:
        returns (pd.Series): ポートフォリオの期間リターン（小数）。
        benchmark (pd.Series | None): ベンチマークの期間リターン。指定時は超過リターン系の指標も出す。
        turnover (pd.Series | None): 片道回転率の系列（期間ごと）。
        periods_per_year (int): 1 年あたりの期間数。
        risk_free (float): 年率の無リスク金利（R/R の分子から差し引く）。

    Returns:
        pd.Series: 以下のキーを持つ Series。

            - ``ann_return``: 年率リターン（幾何）
            - ``ann_volatility``: 年率ボラティリティ
            - ``return_risk``: ``(ann_return - risk_free) / ann_volatility``
            - ``max_drawdown``, ``hit_rate``, ``best_period``, ``worst_period``, ``n_periods``
            - ``turnover_annual``: 年率片道回転率（``turnover`` 指定時）
            - ``active_return``: 超過リターンの年率算術平均（``benchmark`` 指定時）
            - ``active_return_geo``: 年率リターンの差
            - ``tracking_error``: 超過リターンの年率標準偏差
            - ``information_ratio``: ``active_return / tracking_error``
            - ``beta``: ベンチマークに対するベータ
            - ``active_hit_rate``: 超過リターン > 0 の割合

    Examples:
        >>> r = pd.Series([0.02, -0.01, 0.03, 0.01])
        >>> b = pd.Series([0.01, -0.01, 0.02, 0.0])
        >>> s = performance_summary(r, b)
        >>> round(s["tracking_error"], 4), round(s["hit_rate"], 2)
        (0.0173, 0.75)
    """
    r = returns.dropna()
    ann_ret = annualized_return(r, periods_per_year)
    ann_vol = annualized_volatility(r, periods_per_year)
    out: dict[str, float] = {
        "ann_return": ann_ret,
        "ann_volatility": ann_vol,
        "return_risk": (ann_ret - risk_free) / ann_vol
        if ann_vol and ann_vol > 0
        else np.nan,
        "max_drawdown": max_drawdown(r),
        "hit_rate": float((r > 0).mean()) if len(r) else np.nan,
        "best_period": float(r.max()) if len(r) else np.nan,
        "worst_period": float(r.min()) if len(r) else np.nan,
        "n_periods": float(len(r)),
    }
    if turnover is not None:
        to = turnover.reindex(r.index).dropna()
        out["turnover_annual"] = (
            float(to.mean()) * periods_per_year if len(to) else np.nan
        )
    if benchmark is not None:
        b = benchmark.reindex(r.index)
        both = r.notna() & b.notna()
        r2, b2 = r[both], b[both]
        active = r2 - b2
        te = annualized_volatility(active, periods_per_year)
        act = float(active.mean()) * periods_per_year if len(active) else np.nan
        var_b = float(b2.var(ddof=1)) if len(b2) > 1 else np.nan
        out.update(
            {
                "benchmark_ann_return": annualized_return(b2, periods_per_year),
                "active_return": act,
                "active_return_geo": ann_ret - annualized_return(b2, periods_per_year),
                "tracking_error": te,
                "information_ratio": act / te if te and te > 0 else np.nan,
                "beta": float(r2.cov(b2)) / var_b if var_b and var_b > 0 else np.nan,
                "active_hit_rate": float((active > 0).mean())
                if len(active)
                else np.nan,
            }
        )
    return pd.Series(out)


def performance_table(
    returns: pd.DataFrame,
    benchmark: pd.Series | None = None,
    turnover: pd.DataFrame | None = None,
    periods_per_year: int = 12,
    risk_free: float = 0.0,
) -> pd.DataFrame:
    """複数系列（列）の :func:`performance_summary` をまとめた表を返す。

    Args:
        returns (pd.DataFrame): 日付 × ポートフォリオのリターン。
        benchmark (pd.Series | None): ベンチマークリターン。
        turnover (pd.DataFrame | None): 日付 × ポートフォリオの回転率（列名は ``returns`` と対応）。
        periods_per_year (int): 1 年あたりの期間数。
        risk_free (float): 年率無リスク金利。

    Returns:
        pd.DataFrame: 指標 × ポートフォリオ。

    Examples:
        >>> r = pd.DataFrame({"Q1": [0.02, 0.01], "Q5": [-0.01, 0.0]})
        >>> performance_table(r).loc["n_periods"].tolist()
        [2.0, 2.0]
    """
    cols = {}
    for name in returns.columns:
        to = (
            turnover[name]
            if turnover is not None and name in turnover.columns
            else None
        )
        cols[name] = performance_summary(
            returns[name], benchmark, to, periods_per_year, risk_free
        )
    return pd.DataFrame(cols)
