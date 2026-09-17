"""IC（Information Coefficient）分析。

スコア ``s_t`` と将来リターン ``r_{t→t+h}`` のクロスセクション相関を日付ごとに計算する。
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from alphaeval.dates import ensure_datetime_index
from alphaeval.panel import forward_returns, mask_to_universe


def _row_corr(x: np.ndarray, y: np.ndarray, min_obs: int) -> np.ndarray:
    """行ごとのピアソン相関（NaN はペアワイズで除外）。

    Args:
        x (np.ndarray): 2 次元配列（日付 × 銘柄）。
        y (np.ndarray): 同形の配列。
        min_obs (int): 相関を計算する最小観測数。

    Returns:
        np.ndarray: 行ごとの相関（観測不足は NaN）。
    """
    out = np.full(x.shape[0], np.nan)
    for i in range(x.shape[0]):
        m = ~(np.isnan(x[i]) | np.isnan(y[i]))
        if m.sum() < min_obs:
            continue
        xi, yi = x[i, m], y[i, m]
        xi = xi - xi.mean()
        yi = yi - yi.mean()
        den = np.sqrt((xi * xi).sum() * (yi * yi).sum())
        if den > 0:
            out[i] = (xi * yi).sum() / den
    return out


def information_coefficient(
    score: pd.DataFrame,
    forward_return: pd.DataFrame,
    universe: pd.DataFrame | None = None,
    method: str = "spearman",
    min_obs: int = 10,
) -> pd.Series:
    """日付ごとの IC を計算する。

    Args:
        score (pd.DataFrame): 日付 × 銘柄のスコア。
        forward_return (pd.DataFrame): 日付 × 銘柄のフォワードリターン
            （行 ``t`` = ``t`` 時点から見た将来リターン。:func:`alphaeval.panel.forward_returns`）。
        universe (pd.DataFrame | None): ユニバース。指定時はユニバース内の銘柄に限定する。
        method (str): ``"spearman"``（順位相関）または ``"pearson"``。
        min_obs (int): IC を計算する最小銘柄数。

    Returns:
        pd.Series: 日付をインデックスとする IC（名前 = ``method``）。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31"])
        >>> s = pd.DataFrame({"A": [1.0], "B": [2.0], "C": [3.0]}, index=idx)
        >>> f = pd.DataFrame({"A": [0.01], "B": [0.02], "C": [0.05]}, index=idx)
        >>> information_coefficient(s, f, min_obs=3).iloc[0]
        1.0
    """
    score = ensure_datetime_index(score)
    forward_return = ensure_datetime_index(forward_return)
    score = mask_to_universe(score, universe)
    cols = sorted(set(score.columns) & set(forward_return.columns))
    s = score.reindex(columns=cols)
    f = forward_return.reindex(index=s.index, columns=cols)
    if method == "spearman":
        s = s.rank(axis=1)
        f = f.rank(axis=1)
        # 片方が NaN の銘柄を両方から外して順位を取り直す
        both = s.notna() & f.notna()
        s = s.where(both).rank(axis=1)
        f = f.where(both).rank(axis=1)
    elif method != "pearson":
        raise ValueError("method は 'spearman' または 'pearson' を指定してください")
    ic = _row_corr(s.to_numpy(dtype=float), f.to_numpy(dtype=float), min_obs)
    return pd.Series(ic, index=s.index, name=method)


def ic_summary(ic: pd.Series, periods_per_year: int = 12) -> pd.Series:
    """IC 系列の要約統計を返す。

    Args:
        ic (pd.Series): :func:`information_coefficient` の戻り値。
        periods_per_year (int): 年率換算に用いる期間数（月次なら 12）。

    Returns:
        pd.Series: 以下のキーを持つ Series。

            - ``mean``: 平均 IC
            - ``std``: IC の標準偏差
            - ``icir``: ``mean / std``
            - ``icir_annualized``: ``icir × sqrt(periods_per_year)``
            - ``t_stat``: ``mean / (std / sqrt(n))``
            - ``hit_rate``: IC > 0 の割合
            - ``n``: 有効観測数
            - ``autocorr_1``: IC の 1 期ラグ自己相関

    Examples:
        >>> ic = pd.Series([0.05, 0.02, -0.01, 0.04])
        >>> ic_summary(ic)["hit_rate"]
        0.75
    """
    x = ic.dropna()
    n = len(x)
    mean = x.mean() if n else np.nan
    std = x.std(ddof=1) if n > 1 else np.nan
    icir = mean / std if std and std > 0 else np.nan
    return pd.Series(
        {
            "mean": mean,
            "std": std,
            "icir": icir,
            "icir_annualized": icir * np.sqrt(periods_per_year)
            if np.isfinite(icir)
            else np.nan,
            "t_stat": icir * np.sqrt(n) if np.isfinite(icir) else np.nan,
            "hit_rate": float((x > 0).mean()) if n else np.nan,
            "n": float(n),
            "autocorr_1": x.autocorr(1) if n > 2 and std > 0 else np.nan,
        }
    )


def ic_decay(
    score: pd.DataFrame,
    returns: pd.DataFrame,
    horizons: Sequence[int] = (1, 3, 6, 12),
    universe: pd.DataFrame | None = None,
    method: str = "spearman",
    min_obs: int = 10,
) -> pd.DataFrame:
    """複数ホライズンの IC 系列を並べて返す。

    Args:
        score (pd.DataFrame): 日付 × 銘柄のスコア。
        returns (pd.DataFrame): 日付 × 銘柄の期間リターン（行 ``t`` = ``t`` で終わる期間）。
        horizons (Sequence[int]): フォワードリターンの期間数。
        universe (pd.DataFrame | None): ユニバース。
        method (str): ``"spearman"`` または ``"pearson"``。
        min_obs (int): 最小銘柄数。

    Returns:
        pd.DataFrame: 日付 × ホライズン（列名 ``"{h}"``）の IC。

    Examples:
        >>> ic_decay(score, rtn, horizons=(1, 3), universe=univ).mean()  # doctest: +SKIP
    """
    out = {}
    for h in horizons:
        fwd = forward_returns(returns, horizon=h)
        out[str(h)] = information_coefficient(score, fwd, universe, method, min_obs)
    return pd.DataFrame(out)
