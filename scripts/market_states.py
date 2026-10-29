"""市場の状態変数（月次）を共有パネル（``build_market_panel.py``）から作る。

``experiments/market_basics/03_state_variables`` で使った定義を関数にしたもの。状態は 2 種類ある。

- **月末に確定する状態**（予測・ルール化に使える）: 月末 :math:`t` までのデータだけで作り、月 :math:`t+1` のリターンの条件に使う。
  ``rv21`` / ``rv63``（市場の実現ボラ）、``avg_corr``（平均ペア相関）、``breadth_200``（200 日線を上回る比率）、
  ``hl52``（52 週高値更新 − 安値更新の比率）、``size_cyc``（EW − CW の 12 か月）、``value_shock``（売買代金ショック）、
  ``bear24``（BM の過去 24 か月累積が負）、``drawdown``（BM の高値からの下落率）。
- **同じ月の状態**（記述用。その月に何が起きていたか）: 月 :math:`t` のリターンと同じ月に観測する。
  ``mkt``（BM リターン）、``size_spread``（EW − CW）、``win_share``（BM に勝った銘柄の比率）、``csd``（横断面 SD）、
  ``top10_minus_rest``（BM 上位 10 銘柄 − それ以外）、``fx``（USD/INR の変化率、正 = INR 安）。

すべて月末の ``DatetimeIndex``。構成銘柄は「月末 :math:`t-1` のユニバース → 月 :math:`t` の営業日」。

Examples:
    ノートブックから::

        from build_market_panel import load_panel
        from market_states import lagged_states, same_month_states
        panel = load_panel("../../data/market_panel", fields=("ret", "close", "value"))
        lag = lagged_states(panel)
        now = same_month_states(panel, "../../data/fx.csv")
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from build_market_panel import MarketPanel

BM_INDEX = "cw_msci"  # MSCI India プロキシ（大型 + 中型の時価総額加重）


def last_trading_days(days: pd.DatetimeIndex) -> pd.Series:
    """各月の最終営業日。

    Args:
        days (pd.DatetimeIndex): 営業日。

    Returns:
        pd.Series: 月末日付 → その月の最終営業日。

    Examples:
        >>> d = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03"])
        >>> last_trading_days(d).dt.strftime("%m-%d").tolist()
        ['01-31', '02-03']
    """
    s = pd.Series(days, index=days).groupby(days.to_period("M")).max()
    s.index = s.index.to_timestamp(how="end").normalize()
    return s


def at_month_end(daily: pd.Series, last_day: pd.Series) -> pd.Series:
    """日次の系列から各月の最終営業日の値を取り、月末日付で返す。

    Args:
        daily (pd.Series): 営業日の系列。
        last_day (pd.Series): :func:`last_trading_days` の出力。

    Returns:
        pd.Series: 月末日付の系列。

    Examples:
        >>> d = pd.to_datetime(["2020-01-30", "2020-01-31"])
        >>> at_month_end(pd.Series([1.0, 2.0], index=d), last_trading_days(d)).tolist()
        [2.0]
    """
    return pd.Series(
        daily.reindex(last_day.to_numpy()).to_numpy(), index=last_day.index
    )


def realized_vol(index_d: pd.Series, last_day: pd.Series, window: int) -> pd.Series:
    """指数の日次リターンの実現ボラ（年率、月末に確定）。

    Args:
        index_d (pd.Series): 指数の日次リターン。
        last_day (pd.Series): 各月の最終営業日。
        window (int): 窓（営業日）。

    Returns:
        pd.Series: 月末 → 年率ボラ。

    Examples:
        >>> rv = realized_vol(panel.daily_index["cw_imi"], last_trading_days(panel.days), 63)  # doctest: +SKIP
    """
    rv = index_d.rolling(window, min_periods=int(window * 0.8)).std() * np.sqrt(252)
    return at_month_end(rv, last_day)


def average_pair_correlation(
    ret_d: pd.DataFrame, member_m: pd.DataFrame, last_day: pd.Series, window: int = 63
) -> pd.Series:
    """月末 :math:`t` の構成銘柄の、直近 ``window`` 営業日の平均ペア相関（近似）。

    等ウェイトポートフォリオの分散 / 個別銘柄の標準偏差の平均の 2 乗で近似する（銘柄数が多いときの平均相関）。

    Args:
        ret_d (pd.DataFrame): 営業日 × 銘柄の日次リターン。
        member_m (pd.DataFrame): 月末 × 銘柄の真偽値（月末 :math:`t` の構成銘柄）。
        last_day (pd.Series): 各月の最終営業日。
        window (int): 窓（営業日）。

    Returns:
        pd.Series: 月末 → 平均ペア相関。

    Examples:
        >>> rho = average_pair_correlation(ret_d, cap.notna(), last_trading_days(ret_d.index))  # doctest: +SKIP
    """
    days = ret_d.index
    pos = pd.Series(np.arange(len(days)), index=days)
    col = pd.Series(np.arange(ret_d.shape[1]), index=ret_d.columns)
    arr = ret_d.to_numpy()
    out = {}
    for m, d in last_day.items():
        if m not in member_m.index:
            continue
        e = pos[d]
        if e < window:
            continue
        names = member_m.columns[member_m.loc[m].fillna(False).to_numpy(bool)]
        cols = col[names.intersection(ret_d.columns)].to_numpy()
        win = arr[e - window + 1 : e + 1][:, cols]
        win = win[:, np.isfinite(win).sum(axis=0) >= int(window * 0.8)]
        if win.shape[1] < 20:
            continue
        sd = np.nanstd(win, axis=0, ddof=1)
        port = np.nanmean(win, axis=1)
        out[m] = np.var(port, ddof=1) / np.mean(sd) ** 2
    return pd.Series(out, dtype=float)


def breadth_above_ma(
    close_d: pd.DataFrame,
    member_m: pd.DataFrame,
    last_day: pd.Series,
    window: int = 200,
) -> pd.Series:
    """月末 :math:`t` の構成銘柄のうち、終値が ``window`` 日移動平均を上回る比率。

    Args:
        close_d (pd.DataFrame): 営業日 × 銘柄の終値。
        member_m (pd.DataFrame): 月末 × 銘柄の真偽値。
        last_day (pd.Series): 各月の最終営業日。
        window (int): 移動平均の日数。

    Returns:
        pd.Series: 月末 → 比率。

    Examples:
        >>> b = breadth_above_ma(close_d, cap.notna(), last_trading_days(close_d.index))  # doctest: +SKIP
    """
    px = close_d.where(close_d > 0).ffill(limit=5)
    ma = px.rolling(window, min_periods=int(window * 0.9)).mean()
    above = (px > ma).astype(float).where(ma.notna())
    snap = above.reindex(last_day.to_numpy())
    snap.index = last_day.index
    mem = (
        member_m.reindex(index=snap.index, columns=snap.columns)
        .fillna(False)
        .astype(bool)
    )
    s = snap.where(mem)
    return s.mean(axis=1).where(s.notna().sum(axis=1) >= 20)


def high_low_52w(close_d: pd.DataFrame, member_m: pd.DataFrame) -> pd.Series:
    """月 :math:`t` に 52 週高値を更新した銘柄の比率 − 安値を更新した比率（構成銘柄は月末 :math:`t-1`）。

    Args:
        close_d (pd.DataFrame): 営業日 × 銘柄の終値。
        member_m (pd.DataFrame): 月末 × 銘柄の真偽値。

    Returns:
        pd.Series: 月末 → 比率の差。

    Examples:
        >>> hl = high_low_52w(close_d, cap.notna())  # doctest: +SKIP
    """
    px = close_d.where(close_d > 0).ffill(limit=5)
    pmax = px.shift(1).rolling(252, min_periods=200).max()
    pmin = px.shift(1).rolling(252, min_periods=200).min()
    per = px.index.to_period("M")
    hi = (px >= pmax).where(pmax.notna()).astype(float).groupby(per).max()
    lo = (px <= pmin).where(pmin.notna()).astype(float).groupby(per).max()
    for x in (hi, lo):
        x.index = x.index.to_timestamp(how="end").normalize()
    mem = (
        member_m.shift(1)
        .reindex(index=hi.index, columns=hi.columns)
        .fillna(False)
        .astype(bool)
    )
    return hi.where(mem).mean(axis=1) - lo.where(mem).mean(axis=1)


def value_shock(value_d: pd.DataFrame, member_d: pd.DataFrame) -> pd.Series:
    """構成銘柄の合計売買代金（月平均）/ 過去 12 か月平均 の対数。

    Args:
        value_d (pd.DataFrame): 営業日 × 銘柄の売買代金。
        member_d (pd.DataFrame): 営業日 × 銘柄の真偽値（その日の構成銘柄）。

    Returns:
        pd.Series: 月末 → 対数比。

    Examples:
        >>> vs = value_shock(value_d, panel.member_daily("cap").notna())  # doctest: +SKIP
    """
    agg = value_d.where(member_d).sum(axis=1, min_count=20)
    m = agg.groupby(agg.index.to_period("M")).mean()
    m.index = m.index.to_timestamp(how="end").normalize()
    return np.log(m / m.shift(1).rolling(12, min_periods=10).mean())


def lagged_states(panel: MarketPanel) -> pd.DataFrame:
    """月末に確定する状態変数（月 :math:`t+1` のリターンの条件に使う）。

    ``panel`` には日次の ``ret`` / ``close`` / ``value`` が必要。

    Args:
        panel (MarketPanel): 共有パネル。

    Returns:
        pd.DataFrame: 月末 × 状態（``rv21, rv63, avg_corr, breadth_200, hl52, size_cyc, value_shock,
        bear24, drawdown``）。

    Examples:
        >>> st = lagged_states(load_panel(fields=("ret", "close", "value")))  # doctest: +SKIP
    """
    ret_d, close_d, value_d = (panel.daily[k] for k in ("ret", "close", "value"))
    member_m = panel.monthly["cap"].notna()
    last_day = last_trading_days(panel.days)
    idx_d, idx_m = panel.daily_index, panel.monthly_index
    out = pd.DataFrame(index=last_day.index)
    out["rv21"] = realized_vol(idx_d[BM_INDEX], last_day, 21)
    out["rv63"] = realized_vol(idx_d[BM_INDEX], last_day, 63)
    out["avg_corr"] = average_pair_correlation(ret_d, member_m, last_day)
    out["breadth_200"] = breadth_above_ma(close_d, member_m, last_day)
    out["hl52"] = high_low_52w(close_d, member_m)
    spread = np.log1p(idx_m["ew_imi"]) - np.log1p(idx_m["cw_imi"])
    out["size_cyc"] = spread.rolling(12, min_periods=12).sum()
    out["value_shock"] = value_shock(value_d, panel.member_daily("cap").notna())
    lbm = np.log1p(idx_m[BM_INDEX])
    out["bear24"] = (
        (lbm.rolling(24, min_periods=24).sum() < 0)
        .astype(float)
        .where(lbm.rolling(24).count() == 24)
    )
    level = lbm.cumsum()
    out["drawdown"] = np.expm1(level - level.cummax())
    return out


def same_month_states(
    panel: MarketPanel, fx_path: str | Path | None = None
) -> pd.DataFrame:
    """同じ月に観測する市場の状態（記述用）。

    Args:
        panel (MarketPanel): 共有パネル（月次のみ使う）。
        fx_path (str | Path | None): ``data/fx.csv``（1 USD あたり INR、``yyyymm, rate``）。``None`` なら ``fx`` を作らない。

    Returns:
        pd.DataFrame: 月末 × 状態（``mkt, size_spread, win_share, csd, top10_minus_rest, fx``）。

    Examples:
        >>> now = same_month_states(panel, "data/fx.csv")  # doctest: +SKIP
    """
    cap, drtn, size = panel.monthly["cap"], panel.monthly["drtn"], panel.monthly["size"]
    idx_m = panel.monthly_index
    prev_cap = cap.shift(1)
    r = drtn.where(prev_cap.notna())
    out = pd.DataFrame(index=idx_m.index)
    out["mkt"] = idx_m[BM_INDEX]
    out["size_spread"] = idx_m["ew_imi"] - idx_m["cw_imi"]
    out["win_share"] = (
        r.gt(out["mkt"], axis=0).astype(float).where(r.notna()).mean(axis=1)
    )

    def clipped_sd(row: pd.Series) -> float:
        x = row.dropna()
        if len(x) < 20:
            return np.nan
        lo, hi = x.quantile([0.01, 0.99])
        return float(x.clip(lo, hi).std())

    out["csd"] = r.apply(clipped_sd, axis=1)
    bm_cap = prev_cap.where(size.shift(1).isin([1, 2]))
    rank = bm_cap.rank(axis=1, ascending=False)
    top = r.where(rank <= 10)
    rest = r.where((rank > 10) & bm_cap.notna())
    w_top, w_rest = bm_cap.where(top.notna()), bm_cap.where(rest.notna())
    out["top10_minus_rest"] = (top * w_top).sum(axis=1) / w_top.sum(axis=1) - (
        rest * w_rest
    ).sum(axis=1) / w_rest.sum(axis=1)
    if fx_path is not None:
        fx = pd.read_csv(fx_path)
        fx = fx[fx["currency"] == "INR"]
        s = pd.Series(
            fx["rate"].to_numpy(),
            index=pd.to_datetime(fx["yyyymm"].astype(str), format="%Y%m")
            + pd.offsets.MonthEnd(0),
        )
        out["fx"] = s.pct_change().reindex(out.index)
    return out
