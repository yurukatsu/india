"""日付ユーティリティ。

``input/template`` の日付表現（``YYYYMM`` または ``YYYYMMDD`` の整数）と
``pandas.Timestamp`` を相互変換する。``YYYYMM`` は月末日として扱う。
"""

from __future__ import annotations

import pandas as pd


def to_timestamp(date: int | str | pd.Timestamp) -> pd.Timestamp:
    """``YYYYMM`` / ``YYYYMMDD`` 形式の整数（または文字列）を ``Timestamp`` に変換する。

    ``YYYYMM`` は月末日に丸める。``Timestamp`` はそのまま返す。

    Args:
        date (int | str | pd.Timestamp): 日付。

    Returns:
        pd.Timestamp: 変換後の日付。

    Raises:
        ValueError: 6 桁・8 桁のいずれでもない場合。

    Examples:
        >>> to_timestamp(202012)
        Timestamp('2020-12-31 00:00:00')
        >>> to_timestamp(20201215)
        Timestamp('2020-12-15 00:00:00')
        >>> to_timestamp("202402")
        Timestamp('2024-02-29 00:00:00')
    """
    if isinstance(date, pd.Timestamp):
        return date
    text = str(int(date))
    if len(text) == 6:
        return pd.Timestamp(
            year=int(text[:4]), month=int(text[4:6]), day=1
        ) + pd.offsets.MonthEnd(0)
    if len(text) == 8:
        return pd.Timestamp(
            year=int(text[:4]), month=int(text[4:6]), day=int(text[6:8])
        )
    raise ValueError(f"日付は YYYYMM または YYYYMMDD 形式で指定してください: {date!r}")


def to_int(date: pd.Timestamp, monthly: bool = True) -> int:
    """``Timestamp`` を ``YYYYMM``（``monthly=True``）または ``YYYYMMDD`` の整数に変換する。

    Args:
        date (pd.Timestamp): 日付。
        monthly (bool): ``True`` なら ``YYYYMM``、``False`` なら ``YYYYMMDD``。

    Returns:
        int: 整数表現。

    Examples:
        >>> to_int(pd.Timestamp("2020-12-31"))
        202012
        >>> to_int(pd.Timestamp("2020-12-31"), monthly=False)
        20201231
    """
    if monthly:
        return date.year * 100 + date.month
    return date.year * 10000 + date.month * 100 + date.day


def ensure_datetime_index[T: (pd.Series, pd.DataFrame)](obj: T) -> T:
    """インデックスが整数日付（``YYYYMM`` 等）なら ``DatetimeIndex`` に変換して返す。

    すでに ``DatetimeIndex`` の場合はそのまま返す（コピーしない）。

    Args:
        obj (pd.Series | pd.DataFrame): 日付をインデックスに持つオブジェクト。

    Returns:
        pd.Series | pd.DataFrame: ``DatetimeIndex`` を持つオブジェクト（昇順ソート済み）。

    Examples:
        >>> s = pd.Series([1.0, 2.0], index=[202011, 202012])
        >>> ensure_datetime_index(s).index[-1]
        Timestamp('2020-12-31 00:00:00')
    """
    if isinstance(obj.index, pd.DatetimeIndex):
        return obj if obj.index.is_monotonic_increasing else obj.sort_index()
    out = obj.copy()
    out.index = pd.DatetimeIndex([to_timestamp(d) for d in obj.index])
    return out.sort_index()


def add_months(date: pd.Timestamp, months: int) -> pd.Timestamp:
    """日付に月数を加える（日付ベース。月末は月末に揃えない）。

    保有期間判定（``sell_date > acq_date + 12 months``）に用いる。

    Args:
        date (pd.Timestamp): 基準日。
        months (int): 加える月数。

    Returns:
        pd.Timestamp: 加算後の日付。

    Examples:
        >>> add_months(pd.Timestamp("2020-01-31"), 12)
        Timestamp('2021-01-31 00:00:00')
        >>> add_months(pd.Timestamp("2020-02-29"), 12)
        Timestamp('2021-02-28 00:00:00')
    """
    return date + pd.DateOffset(months=months)
