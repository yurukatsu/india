"""キャピタルゲイン税率スケジュール。

税率は「譲渡日に適用される実効税率」を日付ベースで持つ。実効税率は

.. math::

    \\tau^{\\text{eff}} = \\tau^{\\text{base}} \\times (1 + \\text{surcharge}) \\times (1 + \\text{cess})

（``docs/india_tcg.md`` §1.4）。市場固有の値は :func:`india_tax_schedule` のようなプリセット
関数で与え、シミュレーション本体（:mod:`alphaeval.tax.ledger`）は :class:`TaxSchedule` にのみ依存する。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from alphaeval.dates import ensure_datetime_index


@dataclass(frozen=True)
class TaxSchedule:
    """キャピタルゲイン課税のパラメータ。

    Attributes:
        rates (pd.DataFrame): ``DatetimeIndex``（適用開始日）× ``["stcg", "ltcg"]`` の実効税率（小数）。
            各行は「その日以降の譲渡に適用される税率」で、次の行の開始日まで有効。
        ltcg_months (int): 長期判定の保有期間閾値（月）。``sell > acq + ltcg_months`` で長期。
        fiscal_year_start_month (int): 税務年度の開始月（インドは 4）。
        loss_carry_years (int): 損失繰越可能年数。
        short_offsets_long (bool): 短期損失を長期益と相殺できるか。
        long_offsets_short (bool): 長期損失を短期益と相殺できるか。

    Examples:
        >>> sched = constant_tax_schedule(0.2, 0.125)
        >>> sched.rate_at(pd.Timestamp("2025-03-31"))
        (0.2, 0.125)
        >>> sched.fiscal_year(pd.Timestamp("2025-03-31")), sched.fiscal_year(pd.Timestamp("2025-04-30"))
        (2024, 2025)
    """

    rates: pd.DataFrame
    ltcg_months: int = 12
    fiscal_year_start_month: int = 4
    loss_carry_years: int = 8
    short_offsets_long: bool = True
    long_offsets_short: bool = False

    def __post_init__(self) -> None:
        rates = ensure_datetime_index(self.rates)
        missing = {"stcg", "ltcg"} - set(rates.columns)
        if missing:
            raise ValueError(f"rates に列 {sorted(missing)} がありません")
        object.__setattr__(self, "rates", rates[["stcg", "ltcg"]].astype(float))

    def rate_at(self, date: pd.Timestamp) -> tuple[float, float]:
        """``date`` に適用される ``(stcg, ltcg)`` 実効税率を返す。

        Args:
            date (pd.Timestamp): 譲渡日。

        Returns:
            tuple[float, float]: ``(短期税率, 長期税率)``。最初の適用開始日より前は最初の行を使う。
        """
        pos = self.rates.index.searchsorted(date, side="right") - 1
        row = self.rates.iloc[max(pos, 0)]
        return float(row["stcg"]), float(row["ltcg"])

    def fiscal_year(self, date: pd.Timestamp) -> int:
        """``date`` が属する税務年度（開始年で表す）を返す。

        Args:
            date (pd.Timestamp): 日付。

        Returns:
            int: 税務年度の開始年（例: 2025-03-31 → 2024）。
        """
        return (
            date.year if date.month >= self.fiscal_year_start_month else date.year - 1
        )

    def monthly(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """各月末日に適用される税率の月次系列を返す。

        Args:
            start (pd.Timestamp): 開始日。
            end (pd.Timestamp): 終了日。

        Returns:
            pd.DataFrame: 月末 ``DatetimeIndex`` × ``["stcg", "ltcg"]``。
        """
        idx = pd.date_range(start, end, freq="M")
        rows = [self.rate_at(d) for d in idx]
        return pd.DataFrame(rows, index=idx, columns=["stcg", "ltcg"])


def constant_tax_schedule(stcg: float, ltcg: float, **kwargs: object) -> TaxSchedule:
    """全期間一定の税率スケジュールを作る。

    Args:
        stcg (float): 短期実効税率（小数）。
        ltcg (float): 長期実効税率（小数）。
        **kwargs: :class:`TaxSchedule` のその他のパラメータ。

    Returns:
        TaxSchedule: スケジュール。

    Examples:
        >>> constant_tax_schedule(0.2392, 0.1495).rate_at(pd.Timestamp("2000-01-01"))
        (0.2392, 0.1495)
    """
    rates = pd.DataFrame(
        {"stcg": [stcg], "ltcg": [ltcg]},
        index=pd.DatetimeIndex([pd.Timestamp("1900-01-01")]),
    )
    return TaxSchedule(rates=rates, **kwargs)  # type: ignore[arg-type]


def tax_schedule_from_frame(
    frame: pd.DataFrame,
    stcg_col: str = "stcg",
    ltcg_col: str = "ltcg",
    **kwargs: object,
) -> TaxSchedule:
    """日付 × 税率の表からスケジュールを作る（``india_equity_cgt_monthly.csv`` 等）。

    Args:
        frame (pd.DataFrame): 日付（``YYYYMM`` / ``YYYYMMDD`` / ``Timestamp``）をインデックスとする表。
        stcg_col (str): 短期実効税率の列名。
        ltcg_col (str): 長期実効税率の列名。
        **kwargs: :class:`TaxSchedule` のその他のパラメータ。

    Returns:
        TaxSchedule: スケジュール。

    Examples:
        >>> df = pd.DataFrame({"stcg_eff": [0.15, 0.2], "ltcg_eff": [0.1, 0.125]}, index=[201804, 202407])
        >>> tax_schedule_from_frame(df, "stcg_eff", "ltcg_eff").rate_at(pd.Timestamp("2024-08-31"))
        (0.2, 0.125)
    """
    rates = frame[[stcg_col, ltcg_col]].rename(
        columns={stcg_col: "stcg", ltcg_col: "ltcg"}
    )
    return TaxSchedule(rates=rates, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# インド（FPI）プリセット: docs/india_tcg.md §1.5
# ---------------------------------------------------------------------------
_INDIA_BASE_STCG = {
    "1993-04-01": 0.30,
    "2004-10-01": 0.10,
    "2008-04-01": 0.15,
    "2024-07-23": 0.20,
}
_INDIA_BASE_LTCG = {
    "1993-04-01": 0.10,
    "2004-10-01": 0.00,
    "2018-04-01": 0.10,
    "2024-07-23": 0.125,
}
_INDIA_CESS = {
    "1993-04-01": 0.00,
    "2004-04-01": 0.02,
    "2007-04-01": 0.03,
    "2018-04-01": 0.04,
}
#: サーチャージ（税務年度の開始年 → 率）。表に無い年度は直前の値を引き継ぐ。
_INDIA_SURCHARGE: dict[str, dict[int, float]] = {
    "company": {1993: 0.00, 2003: 0.025, 2011: 0.02, 2013: 0.05},
    "trust": {1993: 0.00, 2003: 0.10, 2009: 0.00, 2013: 0.10, 2015: 0.12, 2016: 0.15},
}


def _step_value(table: dict[str, float], date: pd.Timestamp) -> float:
    """階段関数テーブルから ``date`` 時点の値を引く。

    Args:
        table (dict[str, float]): ``{適用開始日: 値}``。
        date (pd.Timestamp): 日付。

    Returns:
        float: 適用される値。
    """
    value = 0.0
    for start, v in sorted(table.items()):
        if pd.Timestamp(start) <= date:
            value = v
    return value


def india_tax_schedule(
    fpi_type: str = "trust",
    start: str | pd.Timestamp = "1993-04-01",
    end: str | pd.Timestamp = "2030-03-31",
    surcharge_override: float | None = None,
) -> TaxSchedule:
    """インド上場株式（FPI）のキャピタルゲイン実効税率スケジュールを作る。

    ``docs/india_tcg.md`` §1.5 の法定税率・cess・サーチャージの推移から
    :math:`\\tau^{base}(1 + s)(1 + c)` を各変更日で計算する。

    Args:
        fpi_type (str): ``"trust"``（信託・AOP 扱い）または ``"company"``（外国法人扱い）。
        start (str | pd.Timestamp): 系列の開始日。
        end (str | pd.Timestamp): 系列の終了日。
        surcharge_override (float | None): サーチャージを全期間この値で固定する（カストディアン確認後の値など）。

    Returns:
        TaxSchedule: 税務年度 4〜3 月、長期閾値 12 か月、損失繰越 8 年のスケジュール。

    Examples:
        >>> s = india_tax_schedule("trust")
        >>> tuple(round(x, 4) for x in s.rate_at(pd.Timestamp("2026-03-31")))
        (0.2392, 0.1495)
        >>> s = india_tax_schedule("company")
        >>> tuple(round(x, 4) for x in s.rate_at(pd.Timestamp("2026-03-31")))
        (0.2184, 0.1365)
        >>> tuple(round(x, 4) for x in india_tax_schedule("trust").rate_at(pd.Timestamp("2015-06-30")))
        (0.173, 0.0)
    """
    if fpi_type not in _INDIA_SURCHARGE:
        raise ValueError("fpi_type は 'trust' または 'company' を指定してください")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    change_dates = {
        pd.Timestamp(d) for d in (*_INDIA_BASE_STCG, *_INDIA_BASE_LTCG, *_INDIA_CESS)
    }
    change_dates |= {
        pd.Timestamp(year=y, month=4, day=1) for y in range(start.year, end.year + 1)
    }
    change_dates = {d for d in change_dates if start <= d <= end} | {start}
    rows = {}
    for d in sorted(change_dates):
        fy = d.year if d.month >= 4 else d.year - 1
        if surcharge_override is not None:
            surcharge = surcharge_override
        else:
            surcharge = 0.0
            for y, v in sorted(_INDIA_SURCHARGE[fpi_type].items()):
                if y <= fy:
                    surcharge = v
        cess = _step_value(_INDIA_CESS, d)
        mult = (1.0 + surcharge) * (1.0 + cess)
        rows[d] = {
            "stcg": _step_value(_INDIA_BASE_STCG, d) * mult,
            "ltcg": _step_value(_INDIA_BASE_LTCG, d) * mult,
        }
    rates = pd.DataFrame.from_dict(rows, orient="index")
    rates.index = pd.DatetimeIndex(rates.index)
    return TaxSchedule(
        rates=rates, ltcg_months=12, fiscal_year_start_month=4, loss_carry_years=8
    )
