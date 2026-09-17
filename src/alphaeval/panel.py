"""ワイドパネル（日付 × 銘柄）の整列・リターン合成ユーティリティ。

規約:

- ``returns`` パネルの行 ``t`` は「``t`` で終わる期間（月）のリターン」（小数）。
- ``weights`` / ``score`` パネルの行 ``t`` は「``t`` 時点（リバランス日）で確定した値」。
- ``t`` のウェイトは ``t`` の次のリターン期間に適用される。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from alphaeval.dates import ensure_datetime_index


def align_columns(*panels: pd.DataFrame) -> list[pd.DataFrame]:
    """複数パネルの列（銘柄）を和集合に揃える。

    Args:
        *panels (pd.DataFrame): 日付 × 銘柄のパネル。

    Returns:
        list[pd.DataFrame]: 列を揃えたパネル（欠損は NaN）。

    Examples:
        >>> a = pd.DataFrame({"x": [1.0]}, index=[1])
        >>> b = pd.DataFrame({"y": [2.0]}, index=[1])
        >>> [p.columns.tolist() for p in align_columns(a, b)]
        [['x', 'y'], ['x', 'y']]
    """
    cols = sorted(set().union(*(p.columns for p in panels)))
    return [p.reindex(columns=cols) for p in panels]


def period_returns(returns: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """隣接する ``dates`` 間のリターンを複利合成する。

    出力の行 ``dates[k]``（k ≥ 1）は、区間 ``(dates[k-1], dates[k]]`` に含まれる
    ``returns`` の行を ``Π(1 + r) - 1`` で合成した値。区間内に 1 行でも NaN があれば NaN。
    区間内に行が無い場合も NaN。

    Args:
        returns (pd.DataFrame): 日付 × 銘柄のリターン（小数）。
        dates (pd.DatetimeIndex): リバランス日の昇順列。

    Returns:
        pd.DataFrame: ``dates[1:]`` × 銘柄の期間リターン。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
        >>> r = pd.DataFrame({"A": [0.1, 0.1, 0.1]}, index=idx)
        >>> period_returns(r, idx[[0, 2]]).round(4)
                       A
        2020-03-31  0.21
    """
    returns = ensure_datetime_index(returns)
    dates = pd.DatetimeIndex(dates).sort_values()
    log_r = np.log1p(returns.to_numpy(dtype=float))
    nan = np.isnan(log_r)
    cum = np.concatenate([np.zeros((1, log_r.shape[1])), np.nancumsum(log_r, axis=0)])
    cum_nan = np.concatenate(
        [np.zeros((1, log_r.shape[1]), dtype=int), np.cumsum(nan, axis=0)]
    )
    # returns.index の位置: 区間 (d_{k-1}, d_k] は行 [lo, hi) に対応
    pos = returns.index.searchsorted(dates, side="right")
    out = np.full((len(dates) - 1, log_r.shape[1]), np.nan)
    for k in range(1, len(dates)):
        lo, hi = pos[k - 1], pos[k]
        if hi <= lo:
            continue
        val = np.exp(cum[hi] - cum[lo]) - 1.0
        val[(cum_nan[hi] - cum_nan[lo]) > 0] = np.nan
        out[k - 1] = val
    return pd.DataFrame(out, index=dates[1:], columns=returns.columns)


def forward_returns(
    returns: pd.DataFrame, horizon: int = 1, skip: int = 0
) -> pd.DataFrame:
    """各行 ``t`` から見た将来 ``horizon`` 期間の複利リターンを返す。

    行 ``t``（位置 ``i``）の値は、位置 ``i+1+skip`` から ``i+skip+horizon`` までの
    ``horizon`` 行を ``Π(1 + r) - 1`` で合成したもの。窓が末尾を超える場合や
    窓内に NaN がある場合は NaN。

    Args:
        returns (pd.DataFrame): 日付 × 銘柄のリターン（小数）。行は等間隔（月次）を想定。
        horizon (int): 合成する期間数（≥ 1）。
        skip (int): 先頭で飛ばす期間数（≥ 0）。

    Returns:
        pd.DataFrame: ``returns`` と同じ形のフォワードリターン。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
        >>> r = pd.DataFrame({"A": [0.0, 0.1, 0.2]}, index=idx)
        >>> forward_returns(r, 1)["A"].round(3).tolist()
        [0.1, 0.2, nan]
        >>> forward_returns(r, 2)["A"].round(3).tolist()
        [0.32, nan, nan]
    """
    if horizon < 1 or skip < 0:
        raise ValueError("horizon は 1 以上、skip は 0 以上で指定してください")
    returns = ensure_datetime_index(returns)
    log_r = np.log1p(returns.to_numpy(dtype=float))
    nan = np.isnan(log_r)
    n = log_r.shape[0]
    cum = np.concatenate([np.zeros((1, log_r.shape[1])), np.nancumsum(log_r, axis=0)])
    cum_nan = np.concatenate(
        [np.zeros((1, log_r.shape[1]), dtype=int), np.cumsum(nan, axis=0)]
    )
    out = np.full_like(log_r, np.nan)
    for i in range(n):
        lo, hi = i + 1 + skip, i + 1 + skip + horizon
        if hi > n:
            break
        val = np.exp(cum[hi] - cum[lo]) - 1.0
        val[(cum_nan[hi] - cum_nan[lo]) > 0] = np.nan
        out[i] = val
    return pd.DataFrame(out, index=returns.index, columns=returns.columns)


def next_dates(
    from_dates: pd.DatetimeIndex, reference: pd.DatetimeIndex
) -> pd.DatetimeIndex:
    """``from_dates`` の各日付より後で最初に現れる ``reference`` の日付を返す。

    Args:
        from_dates (pd.DatetimeIndex): 基準日列。
        reference (pd.DatetimeIndex): 参照する日付列（昇順）。

    Returns:
        pd.DatetimeIndex: 各基準日の「次の参照日」。無い場合は ``NaT``。

    Examples:
        >>> ref = pd.to_datetime(["2020-01-31", "2020-02-29"])
        >>> next_dates(pd.to_datetime(["2020-01-31", "2020-02-29"]), ref).tolist()
        [Timestamp('2020-02-29 00:00:00'), NaT]
    """
    pos = reference.searchsorted(from_dates, side="right")
    vals = [reference[p] if p < len(reference) else pd.NaT for p in pos]
    return pd.DatetimeIndex(vals)


def mask_to_universe(
    panel: pd.DataFrame, universe: pd.DataFrame | None
) -> pd.DataFrame:
    """``universe`` パネルで非 NaN の (日付, 銘柄) だけを残す。

    Args:
        panel (pd.DataFrame): 日付 × 銘柄の値。
        universe (pd.DataFrame | None): 日付 × 銘柄のユニバース（非構成銘柄は NaN）。
            ``None`` なら ``panel`` をそのまま返す。

    Returns:
        pd.DataFrame: ユニバース外を NaN にしたパネル（``panel`` の日付 × ``universe`` との共通列）。

    Examples:
        >>> p = pd.DataFrame({"A": [1.0], "B": [2.0]}, index=[1])
        >>> u = pd.DataFrame({"A": [0.5], "B": [float("nan")]}, index=[1])
        >>> mask_to_universe(p, u)["B"].isna().all()
        True
    """
    if universe is None:
        return panel
    univ = universe.reindex(index=panel.index, columns=panel.columns)
    return panel.where(univ.notna())
