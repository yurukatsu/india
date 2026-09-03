r"""Capital Gain Overhang（Grinblatt-Han 2005）.

日次の価格・売買回転率（``data/turnover/``）を週次化し、

.. math::

    CGO_t = \frac{P_{t-1} - RP_t}{P_{t-1}}, \qquad
    RP_t = \frac{1}{k} \sum_{n=1}^{N}
        \Big[ V_{t-n} \prod_{\tau=1}^{n-1} (1 - V_{t-n+\tau}) \Big] P_{t-n}

- :math:`V_t`: 週次売買回転率（日次 turnover の週内和、[0, V_CAP] にクリップ）
- 重み :math:`V_{t-n}\prod(1-V_{t-n+\tau})`: 「n 週前に取得され、その後未売買」の確率
- :math:`k`: 重みの和（正規化）、:math:`N`: 遡及週数（原論文 260 週）
- :math:`P_{t-1}`: 前週末株価（当週価格を使うと直近リターンと機械的相関が出るため 1 週ラグ）

タイミング: 週 t の CGO は週 t−1 までの情報のみ。月末スナップショットは
「月末以前に終了した最後の週」の値を使うため、常に PIT。
価格は最新ヴィンテージで一貫調整済みの adj_close（CGO は価格比率なのでスケール不変）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from .config import DATA_DIR

TURNOVER_DIR = DATA_DIR / "turnover"
CGO_DIR = DATA_DIR / "cgo"

N_WEEKS = 260      # 遡及期間（週）
MIN_WEEKS = 104    # 出力に要求する最低履歴（週）
V_CAP = 0.9999     # 週次回転率の上限クリップ（生存確率を正に保つ）


def load_daily(start: int | None = None, end: int | None = None) -> pd.DataFrame:
    """``data/turnover/{yyyymm}.pkl`` を連結して返す（yyyymmdd, bid, price, turnover）."""
    frames = []
    for p in sorted(TURNOVER_DIR.glob("*.pkl")):
        ym = int(p.stem)
        if (start is not None and ym < start) or (end is not None and ym > end):
            continue
        frames.append(pd.read_pickle(p))
    return pd.concat(frames, ignore_index=True)


def daily_panel(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """日次パネル (P, V) を返す（index = 営業日 Timestamp、columns = bid）.

    週次版と同じ規約: V は [0, V_CAP] にクリップ。ギャップ日の扱いは compute_cgo 側
    （P 前値埋め・V=0）。日次版の 1 期ラグは「前営業日」になる。
    """
    d = daily.copy()
    d["date"] = pd.to_datetime(d["yyyymmdd"].astype(str), format="%Y%m%d")
    P = d.pivot(index="date", columns="bid", values="price").sort_index()
    V = d.pivot(index="date", columns="bid", values="turnover").reindex(P.index)
    V = V.clip(lower=0.0, upper=V_CAP)
    return P, V


def weekly_panel(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """週次パネル (P, V) を返す（index = 週の金曜 Timestamp、columns = bid）.

    - P: 週内最終営業日の価格
    - V: 週内 turnover の合計（欠損日は 0 扱い、[0, V_CAP] にクリップ）
    """
    d = daily.copy()
    d["date"] = pd.to_datetime(d["yyyymmdd"].astype(str), format="%Y%m%d")
    d["week"] = d["date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()

    d = d.sort_values(["bid", "date"])
    last = d.drop_duplicates(["bid", "week"], keep="last")
    P = last.pivot(index="week", columns="bid", values="price").sort_index()
    V = (
        d.groupby(["week", "bid"])["turnover"].sum(min_count=1)
        .unstack("bid").reindex(P.index)
    )
    V = V.clip(lower=0.0, upper=V_CAP)
    return P, V


@njit(cache=True)
def _cgo_kernel(P: np.ndarray, V: np.ndarray, n_weeks: int, min_weeks: int) -> np.ndarray:
    """1銘柄の週次系列から CGO を計算する（P はギャップ ffill 済み、V は欠損 0 埋め済み）."""
    T = P.shape[0]
    out = np.full(T, np.nan)
    for t in range(min_weeks, T):
        p_lag = P[t - 1]
        if np.isnan(p_lag) or p_lag <= 0.0:
            continue
        n_max = min(n_weeks, t)
        s = 0.0
        k = 0.0
        surv = 1.0
        for n in range(1, n_max + 1):
            v = V[t - n]
            p = P[t - n]
            if v > 0.0 and not np.isnan(p):
                w = v * surv
                s += w * p
                k += w
            surv *= 1.0 - v
            if surv < 1e-12:
                break
        if k > 0.0:
            rp = s / k
            out[t] = (p_lag - rp) / p_lag
    return out


def compute_cgo(
    P: pd.DataFrame,
    V: pd.DataFrame,
    n_weeks: int = N_WEEKS,
    min_weeks: int = MIN_WEEKS,
) -> pd.DataFrame:
    """週次 CGO（index = 週、columns = bid）.

    各銘柄とも「最初の観測週から min_weeks 経過後」から出力。
    上場期間内のギャップ週は P を前値埋め・V を 0（取引なし）として扱う。
    """
    out = pd.DataFrame(np.nan, index=P.index, columns=P.columns)
    for bid in P.columns:
        p_raw = P[bid]
        valid = p_raw.notna()
        if valid.sum() < min_weeks:
            continue
        i0, i1 = valid.idxmax(), valid[::-1].idxmax()
        p = p_raw.loc[i0:i1].ffill().to_numpy(dtype=float)
        v = V[bid].loc[i0:i1].fillna(0.0).to_numpy(dtype=float)
        out.loc[i0:i1, bid] = _cgo_kernel(p, v, n_weeks, min_weeks)
    return out


def monthly_snapshot(
    weekly: pd.DataFrame,
    start: int | None = None,
    end: int | None = None,
    name: str = "cgo",
) -> pd.Series:
    """週次パネル（index=週末日, columns=bid）を月次スナップショットにする.

    各月について「月末以前に終了した最後の週」= その月に属する最後の週の値を採用（PIT）。
    """
    week_end = weekly.index
    month_of_week = pd.PeriodIndex(week_end, freq="M")
    pieces = {}
    for m, idx in pd.Series(range(len(week_end)), index=month_of_week).groupby(level=0):
        w = week_end[idx.iloc[-1]]
        ym = int(m.strftime("%Y%m"))
        if (start is not None and ym < start) or (end is not None and ym > end):
            continue
        pieces[ym] = weekly.loc[w].dropna()
    s = pd.concat(pieces, names=["yyyymm", "bid"]).sort_index()
    s.name = name
    return s


def monthly_cgo(
    start: int | None = None,
    end: int | None = None,
    n_weeks: int = N_WEEKS,
    min_weeks: int = MIN_WEEKS,
) -> pd.Series:
    """月次 CGO スナップショット（MultiIndex: yyyymm × bid）.

    daily の読み込みは start より前（遡及分）も含めて全期間行う。
    """
    daily = load_daily()
    P, V = weekly_panel(daily)
    cgo = compute_cgo(P, V, n_weeks=n_weeks, min_weeks=min_weeks)
    return monthly_snapshot(cgo, start, end)


def monthly_cgo_multi(
    horizons: dict[str, int],
    start: int | None = None,
    end: int | None = None,
    min_periods: int | None = None,
    frequency: str = "weekly",
) -> pd.DataFrame:
    """複数の遡及期間で CGO を一括計算する（列 = ホライズン名）.

    Parameters
    ----------
    horizons : ``{列名: 遡及期間}``。単位は ``frequency`` に依存
        （weekly なら週数、daily なら**営業日数**。例 daily 5年 = 1260）
    min_periods : None（既定）なら**各ホライズンの遡及期間そのもの**
        （= フル・ルックバックが確保できる時点から出力。ホライズン間で定義が一貫する）
    frequency : ``"weekly"``（週次化、1期ラグ = 前週末）| ``"daily"``（1期ラグ = 前営業日）
    """
    daily = load_daily()
    P, V = weekly_panel(daily) if frequency == "weekly" else daily_panel(daily)
    out = {}
    for name, n in horizons.items():
        mp = n if min_periods is None else min_periods
        c = compute_cgo(P, V, n_weeks=n, min_weeks=mp)
        out[name] = monthly_snapshot(c, start, end, name=name)
    return pd.DataFrame(out)


def load_cgo_monthly(
    columns: list[str] | None = None,
    start: int | None = None,
    end: int | None = None,
) -> pd.DataFrame:
    """事前計算済みの CGO（``data/cgo/{yyyymm}.pkl``、scripts/build_cgo.py で生成）を読む.

    Returns
    -------
    MultiIndex (yyyymm, bid) の DataFrame。列 = ``cgo_1m`` ... ``cgo_60m``（columns で絞り込み可）。
    パネルへの結合は ``panel[c] = load_cgo_monthly([c])[c].reindex(panel.index)``。
    """
    frames = []
    for p in sorted(CGO_DIR.glob("*.pkl")):
        ym = int(p.stem)
        if (start is not None and ym < start) or (end is not None and ym > end):
            continue
        df = pd.read_pickle(p)
        if columns is not None:
            df = df[["yyyymm", "bid", *columns]]
        frames.append(df)
    out = pd.concat(frames, ignore_index=True).set_index(["yyyymm", "bid"]).sort_index()
    return out


# --- 税制調整 CGO（3成分分解） --------------------------------------------------


@njit(cache=True)
def _tax_cgo_kernel(P, V, n_weeks, min_weeks, boundary, delta, h_lock, h_rel):
    """LIO / RO / TLO の3成分を返す（T x 3）. 重みの正規化 k は全 N 窓の合計."""
    T = P.shape[0]
    out = np.full((T, 3), np.nan)
    for t in range(min_weeks, T):
        p1 = P[t - 1]
        if np.isnan(p1) or p1 <= 0.0:
            continue
        n_max = min(n_weeks, t)
        surv = 1.0
        k = 0.0
        lio = 0.0
        ro = 0.0
        tlo = 0.0
        for n in range(1, n_max + 1):
            v = V[t - n]
            p = P[t - n]
            if v > 0.0 and not np.isnan(p):
                w = v * surv
                k += w
                g = (p1 - p) / p1
                if n < boundary:
                    kap = np.exp(-(boundary - n) / h_lock)
                    if g > 0.0:
                        lio += w * g * kap
                    else:
                        tlo += w * (-g) * kap
                elif n < boundary + delta:
                    if g > 0.0:
                        ro += w * g * np.exp(-(n - boundary) / h_rel)
            surv *= 1.0 - v
            if surv < 1e-12:
                break
        if k > 0.0:
            out[t, 0] = lio / k
            out[t, 1] = ro / k
            out[t, 2] = tlo / k
    return out


def monthly_tax_cgo(
    start: int | None = None,
    end: int | None = None,
    n_periods: int = 1260,
    boundary: int = 252,
    delta: int = 63,
    h_lock: float = 42.0,
    h_rel: float = 42.0,
    frequency: str = "daily",
) -> pd.DataFrame:
    r"""税制調整 CGO の3成分（月次、MultiIndex: yyyymm × bid）.

    コホート n の含み益率 g_n = (P_{t-1} − P_{t-n})/P_{t-1}、
    重み ω_n = V_{t-n} Π(1−V)/k（k は全 N 窓の重み合計）に対し

    - ``lio``: Σ_{n<boundary} ω_n max(g_n,0) exp(−(boundary−n)/h_lock)  … ロックイン（正の予測）
    - ``ro`` : Σ_{boundary≤n<boundary+delta} ω_n max(g_n,0) exp(−(n−boundary)/h_rel) … リリース（負の予測）
    - ``tlo``: Σ_{n<boundary} ω_n max(−g_n,0) exp(−(boundary−n)/h_lock) … 税損売り（負の予測）

    既定は日次: boundary=252営業日（LTCG 12ヶ月境界）、N=1260、Δ=63、h=42。
    PIT は CGO と同じ（時点 t は t−1 までの情報のみ、月次値は月内最終営業日）。
    """
    daily = load_daily()
    P, V = daily_panel(daily) if frequency == "daily" else weekly_panel(daily)
    cols = ["lio", "ro", "tlo"]
    mats = {c: pd.DataFrame(np.nan, index=P.index, columns=P.columns) for c in cols}
    for bid in P.columns:
        p_raw = P[bid]
        valid = p_raw.notna()
        if valid.sum() < n_periods:
            continue
        i0, i1 = valid.idxmax(), valid[::-1].idxmax()
        p = p_raw.loc[i0:i1].ffill().to_numpy(dtype=float)
        v = V[bid].loc[i0:i1].fillna(0.0).to_numpy(dtype=float)
        arr = _tax_cgo_kernel(p, v, n_periods, n_periods, boundary, delta,
                              float(h_lock), float(h_rel))
        for j, c in enumerate(cols):
            mats[c].loc[i0:i1, bid] = arr[:, j]
    return pd.DataFrame({c: monthly_snapshot(mats[c], start, end, name=c) for c in cols})
