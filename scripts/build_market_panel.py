"""市場の基礎分析（``experiments/market_basics``）で共有する日次・月次パネルを作り、読み出す。

生データ（``data/``）から次を作って ``data/market_panel/`` に pickle で保存する（git 管理外）。

- 日次（営業日 × 銘柄）: 終値・始値・高値・安値（分割調整済み、配当は含まない）、売買代金（INR）、回転率
  （売買株数 / 発行済株数）、日次リターン（終値ベース）、オーバーナイト / 日中リターン
- 月次（月末 × 銘柄）: ユニバースウェイト、配当込みリターン ``drtn``、MSCI のサイズ区分、GICS セクター
- 日次・月次の指数: 時価総額加重（IMI / MSCI India プロキシ / サイズ区分 / セクター）と等ウェイト

規約:

- 月末 :math:`t` のユニバース（``data/universe/t``）に入っている銘柄を、翌月 :math:`t+1` の各営業日の構成銘柄とする。
  時価総額加重は月末 :math:`t` の時価総額で始め、月中は日次リターンでドリフトさせる（買い持ち）。
- 日次リターンは終値の前日比。5 営業日までの欠損は前方補完し、絶対値は 0.5 で抑える（データ誤り対策、
  ``build_style_dtw.py`` と同じ）。
- オーバーナイト = 始値 / 前日終値 − 1、日中 = 終値 / 始値 − 1（``turnover_org`` の調整済み価格）。
- 月次の指数は ``drtn``（配当込み）で、日次の指数は価格リターン（配当なし）。``drtn`` の −1e9（%）、``pbr`` / ``per`` / ``dy`` の絶対値 1e4 以上（−999999998、±10000）は欠損とする。

Examples:
    パネルを作る（初回のみ、数分）::

        uv run python scripts/build_market_panel.py

    ノートブックから読む::

        from build_market_panel import load_panel
        panel = load_panel()
        panel.daily_index["cw_imi"]
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("build_market_panel")

DEFAULT_DATA_ROOT = Path("data")
DEFAULT_PANEL_DIR = DEFAULT_DATA_ROOT / "market_panel"

RETURN_CLIP = 0.5  # 日次リターンの絶対値の上限
MAX_FFILL_DAYS = 5  # 終値の前方補完の上限（営業日）
MIN_STOCKS_PER_DAY = 20  # 営業日とみなす最小の値付き銘柄数

SECTOR_NAMES: dict[int, str] = {
    10: "Energy",
    15: "Materials",
    20: "Industrials",
    25: "ConsDisc",
    30: "ConsStap",
    35: "HealthCare",
    40: "Financials",
    45: "IT",
    50: "CommSvc",
    55: "Utilities",
    60: "RealEstate",
}
SIZE_NAMES: dict[int, str] = {1: "large", 2: "mid", 3: "small"}
VALUATION_FIELDS: tuple[str, ...] = ("pbr", "per", "dy")
VALUATION_LIMIT = 1e4  # |値| ≥ 1e4（-999999998、±10000 の張り付き）は欠損

DAILY_FIELDS: tuple[str, ...] = (
    "close",
    "open",
    "high",
    "low",
    "value",
    "turnover",
    "ret",
    "ret_on",
    "ret_id",
)
MONTHLY_FIELDS: tuple[str, ...] = (
    "cap",
    "drtn",
    "drtn_usd",
    "size",
    "sector",
    "pbr",
    "per",
    "dy",
)


# ---------------------------------------------------------------------------
# 構築
# ---------------------------------------------------------------------------


def load_universe_long(data_root: Path) -> pd.DataFrame:
    """``data/universe/*.pkl`` を縦に積んで読む。

    Args:
        data_root (Path): ``data`` ディレクトリ。

    Returns:
        pd.DataFrame: ``yyyymm, bid, sedol, gics, size, cap, drtn, drtn_usd, pbr, per, dividend_yield``。

    Examples:
        >>> u = load_universe_long(Path("data"))  # doctest: +SKIP
        >>> u["yyyymm"].min()  # doctest: +SKIP
        200301
    """
    cols = [
        "yyyymm",
        "bid",
        "sedol",
        "gics",
        "size",
        "cap",
        "drtn",
        "drtn_usd",
        "pbr",
        "per",
        "dividend_yield",
    ]
    frames = [
        pd.read_pickle(p)[cols] for p in sorted((data_root / "universe").glob("*.pkl"))
    ]
    return pd.concat(frames, ignore_index=True)


def month_end(yyyymm: pd.Series | pd.Index) -> pd.DatetimeIndex:
    """``YYYYMM`` の整数を月末の ``Timestamp`` に変換する。

    Args:
        yyyymm (pd.Series | pd.Index): ``YYYYMM`` の整数。

    Returns:
        pd.DatetimeIndex: 月末日付。

    Examples:
        >>> month_end(pd.Index([202002])).strftime("%Y-%m-%d").tolist()
        ['2020-02-29']
    """
    return pd.DatetimeIndex(
        pd.to_datetime(pd.Index(yyyymm).astype(str), format="%Y%m")
    ) + pd.offsets.MonthEnd(0)


def monthly_wide(universe: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """縦持ちのユニバースを月末 × 銘柄のワイドパネルにする。

    Args:
        universe (pd.DataFrame): :func:`load_universe_long` の出力。

    Returns:
        dict[str, pd.DataFrame]: ``MONTHLY_FIELDS`` の各列。``drtn`` は小数、``sector`` は GICS 上 2 桁。

    Examples:
        >>> u = pd.DataFrame({"yyyymm": [202001], "bid": ["A"], "gics": ["40101010"], "size": [1],
        ...                   "cap": [10.0], "drtn": [5.0], "drtn_usd": [4.0], "pbr": [1.0],
        ...                   "per": [10.0], "dividend_yield": [1.0]})
        >>> int(monthly_wide(u)["sector"].iloc[0, 0])
        40
    """
    u = universe.copy()
    u["date"] = month_end(u["yyyymm"])
    u["sector"] = pd.to_numeric(u["gics"].astype(str).str[:2], errors="coerce")
    for c in ("drtn", "drtn_usd"):
        r = u[c] / 100.0
        u[c] = r.where(r > -1.0)  # -1e9（%）のセンチネルを欠損にする
    u["dy"] = u["dividend_yield"]
    for c in VALUATION_FIELDS:
        u[c] = u[c].where(u[c].abs() < VALUATION_LIMIT)
    u = u.drop_duplicates(["date", "bid"], keep="last")
    out = {}
    for f in MONTHLY_FIELDS:
        out[f] = u.pivot(index="date", columns="bid", values=f).sort_index()
    out["cap"] = out["cap"].where(out["cap"] > 0)
    return out


def load_daily_close_turnover(
    data_root: Path, bids: pd.Index
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``data/turnover/*.pkl`` から終値（分割調整済み）と回転率を読む。

    Args:
        data_root (Path): ``data`` ディレクトリ。
        bids (pd.Index): 読み込む銘柄（BID）。

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: 営業日 × 銘柄の終値と回転率。

    Examples:
        >>> close, to = load_daily_close_turnover(Path("data"), pd.Index(["INDAAA1"]))  # doctest: +SKIP
    """
    frames = []
    for path in sorted((data_root / "turnover").glob("*.pkl")):
        df = pd.read_pickle(path)
        if len(df):
            frames.append(df.loc[df["bid"].isin(bids)])
    long = pd.concat(frames, ignore_index=True)
    long["date"] = pd.to_datetime(long["yyyymmdd"].astype(str), format="%Y%m%d")
    long = long.drop_duplicates(["date", "bid"], keep="last")
    close = long.pivot(index="date", columns="bid", values="price").sort_index()
    turnover = long.pivot(index="date", columns="bid", values="turnover").sort_index()
    return close, turnover


def sedol_map(universe: pd.DataFrame) -> pd.DataFrame:
    """SEDOL → BID の対応表（使われた月の範囲つき）を作る。

    1 つの SEDOL が複数の BID に対応する場合（ごく少数）は、月の範囲で使い分ける。

    Args:
        universe (pd.DataFrame): :func:`load_universe_long` の出力。

    Returns:
        pd.DataFrame: ``sedol, bid, first, last``（``first`` / ``last`` は YYYYMM）。

    Examples:
        >>> u = pd.DataFrame({"yyyymm": [202001, 202002], "bid": ["A", "A"], "sedol": ["S", "S"]})
        >>> sedol_map(u).to_dict("records")
        [{'sedol': 'S', 'bid': 'A', 'first': 202001, 'last': 202002}]
    """
    g = universe.groupby(["sedol", "bid"])["yyyymm"]
    return g.agg(first="min", last="max").reset_index()


def _resolve_sedols(mapping: pd.DataFrame, yyyymm: int) -> pd.DataFrame:
    """月 ``yyyymm`` で使う SEDOL → BID の 1 対 1 対応を選ぶ（範囲に最も近い BID）。"""
    m = mapping.copy()
    m["dist"] = np.maximum(m["first"] - yyyymm, 0) + np.maximum(yyyymm - m["last"], 0)
    m = m.sort_values(["sedol", "dist"]).drop_duplicates("sedol")
    return m[["sedol", "bid"]]


def load_daily_ohlc(data_root: Path, mapping: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """``data/turnover_org/*.csv`` から始値・高値・安値・終値と売買代金（INR）を読む。

    1 つの BID に同日 2 つの SEDOL がある場合は出来高の多い方を採る。

    Args:
        data_root (Path): ``data`` ディレクトリ。
        mapping (pd.DataFrame): :func:`sedol_map` の出力。

    Returns:
        dict[str, pd.DataFrame]: ``open, high, low, close_org, value``（営業日 × 銘柄）。

    Examples:
        >>> ohlc = load_daily_ohlc(Path("data"), sedol_map(load_universe_long(Path("data"))))  # doctest: +SKIP
    """
    usecols = [
        "sedol",
        "market_date",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "unadj_close",
        "unadj_volume",
    ]
    sedols = set(mapping["sedol"])
    frames = []
    for path in sorted((data_root / "turnover_org").glob("*.csv")):
        ym = int(path.stem)
        df = pd.read_csv(path, usecols=usecols, dtype={"sedol": str})
        df = df.loc[df["sedol"].isin(sedols)]
        if df.empty:
            continue
        df = df.merge(_resolve_sedols(mapping, ym), on="sedol")
        df["value"] = df["unadj_close"] * df["unadj_volume"]
        frames.append(df)
        logger.debug("turnover_org %s: %d rows", ym, len(df))
    long = pd.concat(frames, ignore_index=True)
    long["date"] = pd.to_datetime(long["market_date"])
    long = long.sort_values("unadj_volume").drop_duplicates(
        ["date", "bid"], keep="last"
    )
    out = {}
    for src, dst in [
        ("adj_open", "open"),
        ("adj_high", "high"),
        ("adj_low", "low"),
        ("adj_close", "close_org"),
        ("value", "value"),
    ]:
        out[dst] = long.pivot(index="date", columns="bid", values=src).sort_index()
    return out


def daily_returns(close: pd.DataFrame) -> pd.DataFrame:
    """終値から日次リターンを作る（短い欠損は前方補完、絶対値は ``RETURN_CLIP`` で抑える）。

    Args:
        close (pd.DataFrame): 営業日 × 銘柄の終値。

    Returns:
        pd.DataFrame: 同じ形の日次リターン。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"])
        >>> daily_returns(pd.DataFrame({"A": [100.0, 110.0, 99.0]}, index=idx))["A"].round(3).tolist()
        [nan, 0.1, -0.1]
    """
    filled = close.where(close > 0).ffill(limit=MAX_FFILL_DAYS)
    return filled.pct_change(fill_method=None).clip(-RETURN_CLIP, RETURN_CLIP)


def overnight_intraday(
    open_: pd.DataFrame, close: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """オーバーナイト（前日終値 → 始値）と日中（始値 → 終値）のリターンに分ける。

    前日に値が無い場合や始値が無い場合は NaN。どちらも ``RETURN_CLIP`` で抑える。

    Args:
        open_ (pd.DataFrame): 始値。
        close (pd.DataFrame): 終値（``open_`` と同じ出所・同じ調整）。

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: オーバーナイトと日中のリターン。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-01", "2020-01-02"])
        >>> o = pd.DataFrame({"A": [100.0, 102.0]}, index=idx)
        >>> c = pd.DataFrame({"A": [100.0, 99.96]}, index=idx)
        >>> on, intra = overnight_intraday(o, c)
        >>> round(on.iloc[1, 0], 3), round(intra.iloc[1, 0], 3)
        (0.02, -0.02)
    """
    o = open_.where(open_ > 0)
    c = close.where(close > 0)
    on = (o / c.shift(1) - 1.0).clip(-RETURN_CLIP, RETURN_CLIP)
    intra = (c / o - 1.0).clip(-RETURN_CLIP, RETURN_CLIP)
    return on, intra


def daily_membership(weights_m: pd.DataFrame, days: pd.DatetimeIndex) -> pd.DataFrame:
    """月末ウェイトを翌月の各営業日に割り当てる（月末 :math:`t` の構成 → 月 :math:`t+1`）。

    Args:
        weights_m (pd.DataFrame): 月末 × 銘柄のウェイト（または時価総額、非構成は NaN）。
        days (pd.DatetimeIndex): 営業日。

    Returns:
        pd.DataFrame: 営業日 × 銘柄の、前月末の値（月初の値。ドリフトは含まない）。

    Examples:
        >>> w = pd.DataFrame({"A": [1.0, np.nan]}, index=pd.to_datetime(["2020-01-31", "2020-02-29"]))
        >>> days = pd.to_datetime(["2020-02-03", "2020-03-02"])
        >>> daily_membership(w, days)["A"].tolist()
        [1.0, nan]
    """
    prev_me = (days.to_period("M") - 1).to_timestamp(how="end").normalize()
    out = weights_m.reindex(prev_me)
    out.index = days
    return out


def buy_and_hold_index(
    ret: pd.DataFrame, base: pd.DataFrame, mask: pd.DataFrame | None = None
) -> pd.Series:
    """月初ウェイト ``base`` から月中は買い持ちでドリフトさせた指数の日次リターン。

    Args:
        ret (pd.DataFrame): 営業日 × 銘柄の日次リターン（欠損は 0 として扱う = 値が付かなかった日）。
        base (pd.DataFrame): 営業日 × 銘柄の月初ウェイト（:func:`daily_membership` の出力。非構成は NaN）。
            等ウェイトなら構成銘柄に 1 を入れる。
        mask (pd.DataFrame | None): 構成を絞る真偽値（営業日 × 銘柄）。

    Returns:
        pd.Series: 指数の日次リターン（構成銘柄が無い日は NaN）。

    Examples:
        >>> idx = pd.to_datetime(["2020-02-03", "2020-02-04"])
        >>> r = pd.DataFrame({"A": [0.1, 0.0], "B": [0.0, 0.1]}, index=idx)
        >>> w = pd.DataFrame({"A": [1.0, 1.0], "B": [1.0, 1.0]}, index=idx)
        >>> buy_and_hold_index(r, w).round(4).tolist()
        [0.05, 0.0476]
    """
    w0 = base if mask is None else base.where(mask)
    cols = w0.columns[w0.notna().any()]
    w0 = w0[cols]
    r = ret.reindex(index=w0.index, columns=cols).fillna(0.0)
    month = w0.index.to_period("M")
    out = pd.Series(np.nan, index=w0.index)
    for _, idx in pd.Series(np.arange(len(month)), index=w0.index).groupby(month):
        rows = idx.to_numpy()
        w = w0.iloc[rows[0]].to_numpy(dtype=float)
        valid = np.isfinite(w) & (w > 0)
        if not valid.any():
            continue
        rr = r.iloc[rows].to_numpy()[:, valid]
        growth = np.cumprod(1.0 + rr, axis=0)
        prev = np.vstack([np.ones((1, rr.shape[1])), growth[:-1]])
        wd = w[valid] * prev
        out.iloc[rows] = (wd * rr).sum(axis=1) / wd.sum(axis=1)
    return out


def monthly_index(
    drtn: pd.DataFrame, weights: pd.DataFrame, mask: pd.DataFrame | None = None
) -> pd.Series:
    """前月末ウェイトで当月の ``drtn`` を加重平均した月次指数リターン。

    Args:
        drtn (pd.DataFrame): 月末 × 銘柄の月次リターン（行 :math:`t` は月 :math:`t` のリターン）。
        weights (pd.DataFrame): 月末 × 銘柄のウェイト（行 :math:`t` は月 :math:`t+1` に適用）。
        mask (pd.DataFrame | None): 構成を絞る真偽値（月末 × 銘柄、ウェイトと同じ時点）。

    Returns:
        pd.Series: 月次指数リターン。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29"])
        >>> w = pd.DataFrame({"A": [1.0, 1.0], "B": [3.0, 1.0]}, index=idx)
        >>> r = pd.DataFrame({"A": [0.0, 0.1], "B": [0.0, 0.2]}, index=idx)
        >>> monthly_index(r, w).round(3).tolist()
        [nan, 0.175]
    """
    w = weights if mask is None else weights.where(mask)
    w = w.shift(1)
    r = drtn.reindex_like(w)
    valid = w.notna() & r.notna() & (w > 0)
    num = (w * r).where(valid).sum(axis=1)
    den = w.where(valid).sum(axis=1)
    return (num / den).where(den > 0)


def build_indices(
    ret: pd.DataFrame,
    cap_d: pd.DataFrame,
    size_d: pd.DataFrame,
    sector_d: pd.DataFrame,
    drtn: pd.DataFrame,
    cap_m: pd.DataFrame,
    size_m: pd.DataFrame,
    sector_m: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """日次・月次の指数群を作る。

    列: ``cw_imi``（全銘柄の時価総額加重）、``cw_msci``（大型 + 中型 = MSCI India プロキシ）、
    ``ew_imi``（等ウェイト）、``cw_{large,mid,small}`` / ``ew_{large,mid,small}``、``sec_{name}``（セクターの時価総額加重）。

    Args:
        ret (pd.DataFrame): 日次リターン。
        cap_d (pd.DataFrame): 営業日 × 銘柄の前月末時価総額。
        size_d (pd.DataFrame): 営業日 × 銘柄の前月末サイズ区分。
        sector_d (pd.DataFrame): 営業日 × 銘柄の前月末セクター。
        drtn (pd.DataFrame): 月次リターン（配当込み）。
        cap_m (pd.DataFrame): 月末時価総額。
        size_m (pd.DataFrame): 月末サイズ区分。
        sector_m (pd.DataFrame): 月末セクター。

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: 日次指数リターンと月次指数リターン。

    Examples:
        >>> d, m = build_indices(ret, cap_d, size_d, sector_d, drtn, cap_m, size_m, sector_m)  # doctest: +SKIP
    """
    ones_d = cap_d.notna().astype(float).where(cap_d.notna())
    ones_m = cap_m.notna().astype(float).where(cap_m.notna())
    specs: list[tuple[str, str, object]] = [
        ("cw_imi", "cw", None),
        ("cw_msci", "cw", ("size", (1, 2))),
        ("ew_imi", "ew", None),
    ]
    for code, name in SIZE_NAMES.items():
        specs.append((f"cw_{name}", "cw", ("size", (code,))))
        specs.append((f"ew_{name}", "ew", ("size", (code,))))
    for code, name in SECTOR_NAMES.items():
        specs.append((f"sec_{name}", "cw", ("sector", (code,))))
    daily, monthly = {}, {}
    for name, kind, cond in specs:
        if cond is None:
            mask_d, mask_m = None, None
        else:
            key, vals = cond
            src_d, src_m = (size_d, size_m) if key == "size" else (sector_d, sector_m)
            mask_d, mask_m = src_d.isin(vals), src_m.isin(vals)
        base_d = cap_d if kind == "cw" else ones_d
        base_m = cap_m if kind == "cw" else ones_m
        daily[name] = buy_and_hold_index(ret, base_d, mask_d)
        monthly[name] = monthly_index(drtn, base_m, mask_m)
        logger.info("index %s", name)
    return pd.DataFrame(daily), pd.DataFrame(monthly)


def build(data_root: Path, panel_dir: Path) -> None:
    """パネルを作って ``panel_dir`` に保存する。

    Args:
        data_root (Path): ``data`` ディレクトリ。
        panel_dir (Path): 出力先。

    Examples:
        >>> build(Path("data"), Path("data/market_panel"))  # doctest: +SKIP
    """
    panel_dir.mkdir(parents=True, exist_ok=True)
    universe = load_universe_long(data_root)
    monthly = monthly_wide(universe)
    bids = monthly["cap"].columns
    logger.info("universe: %d months, %d bids", len(monthly["cap"]), len(bids))

    close, turnover = load_daily_close_turnover(data_root, bids)
    n_priced = close.notna().sum(axis=1)
    days = close.index[n_priced >= MIN_STOCKS_PER_DAY]
    close, turnover = close.loc[days], turnover.reindex(days)
    logger.info("daily close: %d days x %d bids", *close.shape)

    ohlc = load_daily_ohlc(data_root, sedol_map(universe))
    ohlc = {k: v.reindex(index=days, columns=close.columns) for k, v in ohlc.items()}
    ret = daily_returns(close)
    ret_on, ret_id = overnight_intraday(ohlc["open"], ohlc["close_org"])

    daily = {
        "close": close,
        "open": ohlc["open"],
        "high": ohlc["high"],
        "low": ohlc["low"],
        "value": ohlc["value"],
        "turnover": turnover,
        "ret": ret,
        "ret_on": ret_on,
        "ret_id": ret_id,
    }
    for k, v in daily.items():
        v.astype("float32").to_pickle(panel_dir / f"daily_{k}.pkl")
    for k, v in monthly.items():
        v.to_pickle(panel_dir / f"monthly_{k}.pkl")

    cap_d = daily_membership(monthly["cap"], days)
    size_d = daily_membership(monthly["size"], days)
    sector_d = daily_membership(monthly["sector"], days)
    idx_d, idx_m = build_indices(
        ret,
        cap_d,
        size_d,
        sector_d,
        monthly["drtn"],
        monthly["cap"],
        monthly["size"],
        monthly["sector"],
    )
    idx_d.to_pickle(panel_dir / "daily_index.pkl")
    idx_m.to_pickle(panel_dir / "monthly_index.pkl")
    logger.info("saved to %s", panel_dir)


# ---------------------------------------------------------------------------
# 読み出し
# ---------------------------------------------------------------------------


@dataclass
class MarketPanel:
    """共有パネル。

    Attributes:
        daily (dict[str, pd.DataFrame]): 営業日 × 銘柄（``DAILY_FIELDS``）。列は月次と同じ全銘柄。
        monthly (dict[str, pd.DataFrame]): 月末 × 銘柄（``MONTHLY_FIELDS``）。
        daily_index (pd.DataFrame): 日次指数リターン（価格）。
        monthly_index (pd.DataFrame): 月次指数リターン（配当込み）。
    """

    daily: dict[str, pd.DataFrame]
    monthly: dict[str, pd.DataFrame]
    daily_index: pd.DataFrame
    monthly_index: pd.DataFrame

    @property
    def days(self) -> pd.DatetimeIndex:
        """営業日（日次指数の行と同じ。どの日次フィールドを読んだかに依らない）。"""
        return self.daily_index.index

    def member_daily(self, field: str = "cap") -> pd.DataFrame:
        """月次の値を翌月の各営業日に割り当てたもの（:func:`daily_membership`）。

        Args:
            field (str): ``monthly`` のキー（``cap`` / ``size`` / ``sector`` など）。

        Returns:
            pd.DataFrame: 営業日 × 銘柄（非構成は NaN）。
        """
        return daily_membership(self.monthly[field], self.days)


def load_panel(
    panel_dir: str | Path = DEFAULT_PANEL_DIR,
    fields: Sequence[str] = DAILY_FIELDS,
) -> MarketPanel:
    """保存済みのパネルを読む。

    Args:
        panel_dir (str | Path): :func:`build` の出力先。ノートブックからは ``../../data/market_panel``。
        fields (Sequence[str]): 読む日次フィールド（メモリ節約用）。

    Returns:
        MarketPanel: パネル。

    Examples:
        >>> panel = load_panel("../../data/market_panel", fields=("ret",))  # doctest: +SKIP
    """
    d = Path(panel_dir)
    monthly = {f: pd.read_pickle(d / f"monthly_{f}.pkl") for f in MONTHLY_FIELDS}
    bids = monthly["cap"].columns
    daily = {
        f: pd.read_pickle(d / f"daily_{f}.pkl").astype("float64").reindex(columns=bids)
        for f in fields
    }
    return MarketPanel(
        daily=daily,
        monthly=monthly,
        daily_index=pd.read_pickle(d / "daily_index.pkl"),
        monthly_index=pd.read_pickle(d / "monthly_index.pkl"),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を読む。

    Args:
        argv (Sequence[str] | None): 引数（``None`` なら ``sys.argv``）。

    Returns:
        argparse.Namespace: ``data_root``, ``panel_dir``。

    Examples:
        >>> parse_args(["--panel-dir", "x"]).panel_dir
        PosixPath('x')
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    p.add_argument("--panel-dir", type=Path, default=DEFAULT_PANEL_DIR)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """エントリポイント。

    Args:
        argv (Sequence[str] | None): 引数。

    Examples:
        >>> main([])  # doctest: +SKIP
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = parse_args(argv)
    build(args.data_root, args.panel_dir)


if __name__ == "__main__":
    main()
