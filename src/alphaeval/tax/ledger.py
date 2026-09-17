"""FIFO ロット台帳による税控除後リターンのシミュレーション（``docs/india_tcg.md`` §4）。

ウェイト :math:`w_{i,t}`（所与）とリターンから価格指数を作り、各リバランスで

1. グロスリターン :math:`r^p_t = \\sum_i w_{i,t-1} r_{i,t}`
2. 売買数量・回転率・売買コスト
3. 売却の FIFO ロット充当と実現損益の短期／長期区分
4. 税務年度内通算・損失繰越つきの税額
5. 税控除後の価値・リターン

を逐次計算する。ロット単位の実現損益（``realized``）を残すため、月次集計は再計算できる。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import pandas as pd

from alphaeval.dates import add_months, ensure_datetime_index
from alphaeval.tax.schedule import TaxSchedule


@dataclass
class _Lot:
    """ロット（取得日・残数量・取得単価）。"""

    lot_id: int
    acq_date: pd.Timestamp
    qty: float
    unit_cost: float


@dataclass
class _LossPool:
    """税務年度 ``fiscal_year`` に発生した未使用損失（正の値で保持）。"""

    fiscal_year: int
    short: float = 0.0
    long: float = 0.0


@dataclass
class TaxSimulationResult:
    """:func:`simulate_after_tax` の結果。

    Attributes:
        monthly (pd.DataFrame): リバランス日ごとの集計（§5.3 ``monthly``）。

            - ``r_gross`` / ``r_net_tc`` / ``r_net``: グロス／売買コスト控除後／税控除後リターン
            - ``turnover``: 片道回転率
            - ``cost`` / ``tax``: 売買コスト額・税額（``value`` と同じ単位）
            - ``gain_short`` / ``gain_long``: 当期の実現損益（短期／長期）
            - ``unrealized_short`` / ``unrealized_long``: 期末の未実現損益
            - ``liquidation_tax``: 期末に全清算した場合の税額
            - ``value``: 税・コスト控除後のポートフォリオ価値
            - ``stcg_rate`` / ``ltcg_rate`` / ``fiscal_year`` / ``n_holdings`` / ``n_lots``
        realized (pd.DataFrame): ロット単位の実現損益（§5.3 ``realized``）。
        trades (pd.DataFrame): 売買記録（§5.3 ``trades``）。
        annual (pd.DataFrame): 税務年度別集計（§5.3 ``annual``）。短期比率 ``short_ratio`` を含む。
        returns (pd.DataFrame): ``monthly`` から ``r_gross`` / ``r_net_tc`` / ``r_net`` だけを抜いた表。
    """

    monthly: pd.DataFrame
    realized: pd.DataFrame
    trades: pd.DataFrame
    annual: pd.DataFrame

    @property
    def returns(self) -> pd.DataFrame:
        """3 段階のリターン系列（最初のリバランス日を除く）。"""
        return self.monthly[["r_gross", "r_net_tc", "r_net"]].iloc[1:]


def price_index_from_returns(
    returns: pd.DataFrame, dates: pd.DatetimeIndex, columns: pd.Index | None = None
) -> pd.DataFrame:
    """リターンから各リバランス日の価格指数 :math:`P_{i,t} = \\prod_{s \\le t}(1 + r_{i,s})` を作る。

    欠損リターンは 0（価格据え置き）として扱う。配当込みリターンを使うと配当分が
    キャピタルゲインに含まれる（§5.4 の注意）。

    Args:
        returns (pd.DataFrame): 日付 × 銘柄の期間リターン（小数）。
        dates (pd.DatetimeIndex): 価格指数を評価するリバランス日。
        columns (pd.Index | None): 対象銘柄。``None`` なら ``returns`` の全列。

    Returns:
        pd.DataFrame: ``dates`` × 銘柄の価格指数（データが無い期間は 1.0）。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
        >>> r = pd.DataFrame({"A": [0.1, 0.1, -0.5]}, index=idx)
        >>> price_index_from_returns(r, idx)["A"].round(3).tolist()
        [1.1, 1.21, 0.605]
    """
    returns = ensure_datetime_index(returns)
    if columns is not None:
        returns = returns.reindex(columns=columns)
    wealth = (1.0 + returns.fillna(0.0)).cumprod()
    # 各 date について、date 以前の最終行を使う（無ければ 1.0）
    pos = wealth.index.searchsorted(dates, side="right") - 1
    out = np.ones((len(dates), wealth.shape[1]))
    arr = wealth.to_numpy(dtype=float)
    for k, p in enumerate(pos):
        if p >= 0:
            out[k] = arr[p]
    return pd.DataFrame(out, index=dates, columns=wealth.columns)


def _active_carry(
    pools: list[_LossPool], fiscal_year: int, carry_years: int
) -> tuple[float, float]:
    """失効していない繰越損失の合計 ``(short, long)`` を返す（正の値）。"""
    short = sum(
        p.short for p in pools if 0 < fiscal_year - p.fiscal_year <= carry_years
    )
    long = sum(p.long for p in pools if 0 < fiscal_year - p.fiscal_year <= carry_years)
    return short, long


def _apply_offsets(
    short: float, long: float, schedule: TaxSchedule
) -> tuple[float, float, float, float]:
    """年初来累計損益（繰越込み）に相殺ルールを適用する。

    Args:
        short (float): 短期の累計損益（繰越損失を差し引いた後）。
        long (float): 長期の累計損益。
        schedule (TaxSchedule): 相殺可否の設定。

    Returns:
        tuple[float, float, float, float]:
            ``(課税対象短期益, 課税対象長期益, 未使用短期損失, 未使用長期損失)``（損失は ≤ 0）。
    """
    s_tax, l_tax = max(short, 0.0), max(long, 0.0)
    s_loss, l_loss = min(short, 0.0), min(long, 0.0)
    if s_loss < 0 and schedule.short_offsets_long and l_tax > 0:
        use = min(-s_loss, l_tax)
        l_tax -= use
        s_loss += use
    if l_loss < 0 and schedule.long_offsets_short and s_tax > 0:
        use = min(-l_loss, s_tax)
        s_tax -= use
        l_loss += use
    return s_tax, l_tax, s_loss, l_loss


def _rebuild_pools(
    pools: list[_LossPool],
    residual_short: float,
    residual_long: float,
    fiscal_year: int,
    carry_years: int,
) -> list[_LossPool]:
    """年度末の未使用損失を発生年度別プールに再配分する（古い損失から使う前提）。

    Args:
        pools (list[_LossPool]): 前年度までのプール。
        residual_short (float): 年度末の未使用短期損失（≤ 0、繰越込み）。
        residual_long (float): 年度末の未使用長期損失（≤ 0）。
        fiscal_year (int): 終了する税務年度。
        carry_years (int): 繰越年数（失効分は捨てる）。

    Returns:
        list[_LossPool]: 新しいプール（発生年度昇順）。
    """
    prior = [p for p in pools if 0 < fiscal_year - p.fiscal_year <= carry_years]
    new: dict[int, _LossPool] = {}
    for attr, residual in (("short", residual_short), ("long", residual_long)):
        remaining = -residual
        prior_total = sum(getattr(p, attr) for p in prior)
        current = max(0.0, remaining - prior_total)
        if current > 0:
            new.setdefault(fiscal_year, _LossPool(fiscal_year))
            setattr(new[fiscal_year], attr, current)
        remaining -= current
        for p in sorted(prior, key=lambda x: x.fiscal_year, reverse=True):
            keep = min(getattr(p, attr), remaining)
            if keep > 0:
                new.setdefault(p.fiscal_year, _LossPool(p.fiscal_year))
                setattr(new[p.fiscal_year], attr, keep)
            remaining -= keep
    return [new[k] for k in sorted(new)]


def simulate_after_tax(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    schedule: TaxSchedule,
    cost_buy: float = 0.0,
    cost_sell: float = 0.0,
    initial_value: float = 1.0,
    price_index: pd.DataFrame | None = None,
    charge_initial_costs: bool = True,
) -> TaxSimulationResult:
    """ウェイト所与の税控除後リターンを FIFO ロット台帳で逐次計算する。

    Args:
        weights (pd.DataFrame): リバランス日 × 銘柄のリバランス後ウェイト（非保有は NaN / 0）。
        returns (pd.DataFrame): 日付 × 銘柄の期間リターン（小数）。価格指数の生成に使う。
        schedule (TaxSchedule): 税率スケジュールと課税ルール。
        cost_buy (float): 買付コスト率 :math:`\\kappa_{buy}`（約定金額比）。
        cost_sell (float): 売却コスト率 :math:`\\kappa_{sell}`（STT 等を含む）。
        initial_value (float): 初期ポートフォリオ価値 :math:`V_0`。
        price_index (pd.DataFrame | None): リバランス日 × 銘柄の価格。``None`` なら
            :func:`price_index_from_returns` で ``returns`` から生成する。
        charge_initial_costs (bool): 初回構築時の買付コストを計上するか。

    Returns:
        TaxSimulationResult: 月次・年次・ロット単位の結果。

    Notes:
        - 保有期間判定は日付ベース（``sell_date > acq_date + ltcg_months``）。
        - 初回は全ロットの取得日が同一になるため、最初の ``ltcg_months`` は全売却が短期になる
          （初期化バイアス）。評価はバーンイン後の期間で行うこと。
        - :math:`q_{i,t}` は §4.4 に従い :math:`V^-_t` ベースの目標数量をそのまま用いる。

    Examples:
        >>> sched = constant_tax_schedule(0.2, 0.1)  # doctest: +SKIP
        >>> res = simulate_after_tax(weights, rtn, sched, cost_buy=0.001, cost_sell=0.002)  # doctest: +SKIP
        >>> res.annual[["r_gross", "r_net", "short_ratio"]]  # doctest: +SKIP
    """
    weights = ensure_datetime_index(weights).fillna(0.0).astype(float)
    weights = weights.loc[:, (weights != 0).any(axis=0)]
    dates = weights.index
    codes = weights.columns
    if price_index is None:
        prices = price_index_from_returns(returns, dates, codes)
    else:
        prices = ensure_datetime_index(price_index).reindex(index=dates, columns=codes)
        if prices.isna().to_numpy().any():
            raise ValueError("price_index にリバランス日 × 保有銘柄の欠損があります")
    w_arr = weights.to_numpy()
    p_arr = prices.to_numpy(dtype=float)
    n_codes = len(codes)

    lots: list[deque[_Lot]] = [deque() for _ in range(n_codes)]
    pools: list[_LossPool] = []
    qty = np.zeros(n_codes)
    value = float(initial_value)
    lot_counter = 0
    fy_current: int | None = None
    cum_short = cum_long = 0.0
    tax_bar_prev = 0.0
    monthly_rows: list[dict[str, float]] = []
    realized_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    carry_in_by_fy: dict[int, tuple[float, float]] = {}
    carry_out_by_fy: dict[int, tuple[float, float]] = {}

    for k, date in enumerate(dates):
        w = w_arr[k]
        price = p_arr[k]
        value_prev = value
        # (1) グロスリターン
        if k == 0:
            r_gross = np.nan
            value_minus = value
            w_minus = np.zeros(n_codes)
        else:
            r_stock = price / p_arr[k - 1] - 1.0
            w_prev = w_arr[k - 1]
            r_gross = float((w_prev * r_stock).sum())
            value_minus = value * (1.0 + r_gross)
            grown = w_prev * (1.0 + r_stock)
            w_minus = grown / grown.sum() if grown.sum() > 0 else np.zeros(n_codes)
        turnover = 0.5 * float(np.abs(w - w_minus).sum()) if k > 0 else np.nan

        # (2) 売買数量・コスト
        target = np.where(price > 0, w * value_minus / price, 0.0)
        delta = target - qty
        sells = np.maximum(0.0, -delta)
        buys = np.maximum(0.0, delta)
        cost = float(((cost_sell * sells + cost_buy * buys) * price).sum())
        if k == 0 and not charge_initial_costs:
            cost = 0.0

        # (3) FIFO 充当と実現損益
        gain_short = gain_long = 0.0
        for i in np.flatnonzero(sells > 1e-15):
            remaining = sells[i]
            book = lots[i]
            trade_rows.append(
                {
                    "date": date,
                    "code": codes[i],
                    "side": "sell",
                    "qty": remaining,
                    "price": price[i],
                    "cost": cost_sell * remaining * price[i],
                }
            )
            while remaining > 1e-15 and book:
                lot = book[0]
                used = min(lot.qty, remaining)
                gain = (price[i] - lot.unit_cost) * used
                is_long = date > add_months(lot.acq_date, schedule.ltcg_months)
                if is_long:
                    gain_long += gain
                else:
                    gain_short += gain
                realized_rows.append(
                    {
                        "date": date,
                        "code": codes[i],
                        "lot_id": lot.lot_id,
                        "acq_date": lot.acq_date,
                        "holding_days": (date - lot.acq_date).days,
                        "qty": used,
                        "unit_cost": lot.unit_cost,
                        "sell_price": price[i],
                        "gain": gain,
                        "class": "L" if is_long else "S",
                    }
                )
                lot.qty -= used
                remaining -= used
                if lot.qty <= 1e-15:
                    book.popleft()
        for i in np.flatnonzero(buys > 1e-15):
            lots[i].append(_Lot(lot_counter, date, buys[i], price[i]))
            lot_counter += 1
            trade_rows.append(
                {
                    "date": date,
                    "code": codes[i],
                    "side": "buy",
                    "qty": buys[i],
                    "price": price[i],
                    "cost": cost_buy * buys[i] * price[i],
                }
            )

        # (4) 税額（年度内通算・繰越）
        fy = schedule.fiscal_year(date)
        if fy != fy_current:
            if fy_current is not None:
                carry_s, carry_l = _active_carry(
                    pools, fy_current, schedule.loss_carry_years
                )
                _, _, s_loss, l_loss = _apply_offsets(
                    cum_short - carry_s, cum_long - carry_l, schedule
                )
                pools = _rebuild_pools(
                    pools, s_loss, l_loss, fy_current, schedule.loss_carry_years
                )
                carry_out_by_fy[fy_current] = (abs(s_loss), abs(l_loss))
            fy_current = fy
            cum_short = cum_long = 0.0
            tax_bar_prev = 0.0
            carry_in_by_fy[fy] = _active_carry(pools, fy, schedule.loss_carry_years)
        cum_short += gain_short
        cum_long += gain_long
        carry_s, carry_l = _active_carry(pools, fy, schedule.loss_carry_years)
        s_tax, l_tax, _, _ = _apply_offsets(
            cum_short - carry_s, cum_long - carry_l, schedule
        )
        stcg_rate, ltcg_rate = schedule.rate_at(date)
        tax_bar = stcg_rate * s_tax + ltcg_rate * l_tax
        tax = tax_bar - tax_bar_prev
        tax_bar_prev = tax_bar

        # (5) 税控除後の価値
        value = value_minus - cost - tax
        qty = target

        # 未実現損益（期末保有ロット）
        unreal_short = unreal_long = 0.0
        n_lots = 0
        for i, book in enumerate(lots):
            for lot in book:
                n_lots += 1
                g = (price[i] - lot.unit_cost) * lot.qty
                if date > add_months(lot.acq_date, schedule.ltcg_months):
                    unreal_long += g
                else:
                    unreal_short += g
        monthly_rows.append(
            {
                "r_gross": r_gross,
                "r_net_tc": r_gross - cost / value_prev if k > 0 else np.nan,
                "r_net": r_gross - (cost + tax) / value_prev if k > 0 else np.nan,
                "turnover": turnover,
                "cost": cost,
                "tax": tax,
                "gain_short": gain_short,
                "gain_long": gain_long,
                "unrealized_short": unreal_short,
                "unrealized_long": unreal_long,
                "liquidation_tax": stcg_rate * max(unreal_short, 0.0)
                + ltcg_rate * max(unreal_long, 0.0),
                "value": value,
                "stcg_rate": stcg_rate,
                "ltcg_rate": ltcg_rate,
                "fiscal_year": float(fy),
                "n_holdings": float((w > 0).sum()),
                "n_lots": float(n_lots),
            }
        )

    if fy_current is not None:
        carry_s, carry_l = _active_carry(pools, fy_current, schedule.loss_carry_years)
        _, _, s_loss, l_loss = _apply_offsets(
            cum_short - carry_s, cum_long - carry_l, schedule
        )
        carry_out_by_fy[fy_current] = (abs(s_loss), abs(l_loss))

    monthly = pd.DataFrame(monthly_rows, index=dates)
    realized = pd.DataFrame(
        realized_rows,
        columns=[
            "date",
            "code",
            "lot_id",
            "acq_date",
            "holding_days",
            "qty",
            "unit_cost",
            "sell_price",
            "gain",
            "class",
        ],
    )
    trades = pd.DataFrame(
        trade_rows, columns=["date", "code", "side", "qty", "price", "cost"]
    )
    annual = _annual_summary(monthly, carry_in_by_fy, carry_out_by_fy)
    return TaxSimulationResult(
        monthly=monthly, realized=realized, trades=trades, annual=annual
    )


def _annual_summary(
    monthly: pd.DataFrame,
    carry_in: dict[int, tuple[float, float]],
    carry_out: dict[int, tuple[float, float]],
) -> pd.DataFrame:
    """月次表を税務年度で集計する。

    Args:
        monthly (pd.DataFrame): :func:`simulate_after_tax` の月次表。
        carry_in (dict[int, tuple[float, float]]): 年度 → 期首繰越損失 ``(short, long)``。
        carry_out (dict[int, tuple[float, float]]): 年度 → 期末繰越損失。

    Returns:
        pd.DataFrame: 税務年度をインデックスとする年次表。
    """
    rows = {}
    for fy, part in monthly.groupby("fiscal_year"):
        fy = int(fy)
        pos_s = part["gain_short"].clip(lower=0).sum()
        pos_l = part["gain_long"].clip(lower=0).sum()
        rows[fy] = {
            "r_gross": float((1 + part["r_gross"].fillna(0)).prod() - 1),
            "r_net_tc": float((1 + part["r_net_tc"].fillna(0)).prod() - 1),
            "r_net": float((1 + part["r_net"].fillna(0)).prod() - 1),
            "turnover": float(part["turnover"].sum()),
            "cost": float(part["cost"].sum()),
            "gain_short": float(part["gain_short"].sum()),
            "gain_long": float(part["gain_long"].sum()),
            "tax": float(part["tax"].sum()),
            "short_ratio": pos_s / (pos_s + pos_l) if (pos_s + pos_l) > 0 else np.nan,
            "carry_in_short": carry_in.get(fy, (0.0, 0.0))[0],
            "carry_in_long": carry_in.get(fy, (0.0, 0.0))[1],
            "carry_out_short": carry_out.get(fy, (0.0, 0.0))[0],
            "carry_out_long": carry_out.get(fy, (0.0, 0.0))[1],
            "n_periods": float(len(part)),
        }
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index.name = "fiscal_year"
    return out
