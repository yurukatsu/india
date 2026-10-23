"""スタイルリターンの類似局面（DTW）予測から個別株スコアを作り、``input`` の alpha 形式で書き出す。

手順（定式化は ``experiments/style_dtw/README.md``）:

1. ``core`` ファクターを符号調整して各月ユニバース内で Blom 化し、スタイル内で平均して再 Blom 化する
   （スタイルスコア :math:`S_{i,k,t}`）。サイズは :math:`-\\log` ユニバースウェイト（小型ほど高い）。
2. 月末 :math:`t` のスタイルスコアで Q5（上位 20%）と Q1（下位 20%）を決め、翌月の日次リターンを
   等ウェイトで平均して日次スタイルリターン :math:`g_{k,\\tau}` を作る。累積は対数和 :math:`G_{k,\\tau}`。
3. 各月末に、直近 ``L`` 営業日の累積リターンの推移（起点 0）を過去の推移と DTW で比べ、
   互いに ``G_MIN`` 日以上離れた近傍 ``K`` 個の翌 ``H`` 日リターンの平均を予測 :math:`\\hat\\mu_{k,t}` とする。
   候補は翌 ``H`` 日が確定していて、かつクエリの窓と重ならない（終端が ``G_MIN`` 日以上前）ものに限る。
4. :math:`\\alpha_{i,t} = \\sum_k \\tilde\\mu_{k,t} S_{i,k,t}`（:math:`\\tilde\\mu = \\hat\\mu / \\hat\\sigma`）を
   各月ユニバース内で Blom 化して書き出す。比較用に :math:`\\tilde\\mu` を差し替えたベースラインも作る。

出力:

- ``alpha/style_dtw/{name}/YYYYMM.dat`` と ``alpha/style_dtw/README.md``
- ``experiments/style_dtw/output/``: 日次スタイルリターン、スタイル別の予測と実現値、近傍の一覧（CSV）

Examples:
    全スコアを生成::

        uv run python scripts/build_style_dtw.py

    評価開始月を変える::

        uv run python scripts/build_style_dtw.py --eval-start 201201
"""

from __future__ import annotations

import argparse
import logging
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from scipy.stats import norm

from alphaeval.dates import to_int
from alphaeval.io import InputStore, write_score_file

logger = logging.getLogger("build_style_dtw")

DEFAULT_INPUT_ROOT = Path("input") / "msci_india_imi"
DEFAULT_DATA_ROOT = Path("data")
DEFAULT_OUTPUT_DIR = Path("experiments") / "style_dtw" / "output"
GROUP = "style_dtw"

WINDOW = 63  # L: クエリ窓（営業日）
HORIZON = 21  # H: 予測ホライズン（営業日）
N_NEIGHBORS = 3  # K
MIN_GAP = 63  # G_min: 近傍どうし、近傍とクエリの最小間隔（営業日）
BAND = 6  # Sakoe-Chiba 帯の幅（0.1 L）
MIN_HISTORY = 252  # σ̂ とベースラインに必要な最小日数
TOP, BOTTOM = 0.8, 0.2  # Q5 / Q1 のパーセンタイル境界
MIN_STYLES = 5  # 個別株スコアに必要な最小スタイル数
RETURN_CLIP = 0.5  # 日次リターンの絶対値の上限（データ誤り対策）
MAX_FFILL_DAYS = 5  # 価格の前方補完の上限（営業日）

#: スタイル → 符号付き構成ファクター（``-`` 始まりは符号反転）。各スタイル内で向きをそろえてある。
#: Volatility は低ボラほど高い向き、Financial Quality は財務の健全性が高いほど高い向き。
#: ``eiss`` / ``diss`` はデータ上すでに「発行が少ないほど高い」向き（``build_healthy_compounder.py`` と同じ扱い）。
STYLE_MEMBERS: dict[str, tuple[str, ...]] = {
    "Stock Value": ("bp_est", "bp_act"),
    "Flow Value": (
        "ep_est", "ep_act", "dp_est", "dp_act", "sp_est", "sp_act", "ebitdaev_est", "ebitdaev_act",
        "cfp_est", "cfp_act", "ocfp", "fcfp", "rdp", "maratio", "dlt_ep3", "dlt_ep6", "peg_est", "peg_act",
    ),
    "Profitability": (
        "roe_est", "roe_act", "roa_est", "roa_act", "roic_est", "roic_act", "gpa_act", "gm_act",
        "nis_est", "nis_act", "ois_est", "ois_act", "ocfs", "prof", "re_ta", "ebit_ta", "sales_ta",
        "dlt_gpa_5y", "dlt_roe_5y", "dlt_roa_5y", "dlt_ocfa_5y", "dlt_gps_5y", "ni_ta", "pti_tl", "chin",
    ),
    "Growth": (
        "ltg", "sales_grw", "oi_grw", "eps_grw", "ni_grw", "ppe_grw", "ta_grw", "invent_grw",
        "eq_grw", "capex_grw", "ia",
    ),
    "Shareholder Return": ("-csi", "doe", "eiss", "diss", "npop"),
    "Momentum": ("rev1p", "rev1r", "rev3p", "rev3r", "mom1", "mom12", "mom12_1", "mom60", "tover"),
    "Volatility": ("-vola60", "-skew60"),
    "Financial Quality": (
        "acc_bs", "acc_cf", "-xfin_bs", "-xfin_cf", "-xfin_sp", "curr", "ta_to", "rec_to",
        "invent_to", "acpy_to", "-ccc", "-sales_bep", "eqr", "mve_tl", "eq_tl", "-tdint", "ocftd",
        "-dsr", "-gmi", "-aqi", "-depi", "-sgai", "-levi", "-td_ta", "-tl_ta", "-oscore", "zscore",
        "-mscore",
    ),
}  # fmt: skip
SIZE_STYLE = "Size"


@dataclass(frozen=True)
class ScoreSpec:
    """生成する個別株スコア 1 本の定義。

    Attributes:
        name (str): スコア名（``alpha/style_dtw/{name}``）。
        forecast (str): スタイル予測の種類（``dtw`` / ``dtw_z`` / ``euclid`` / ``ew`` / ``hist`` /
            ``mom12`` / ``mom3``）。
        transform (str): 予測からスタイルウェイトへの変換（``scaled`` = :math:`\\hat\\mu/\\hat\\sigma`、
            ``sign``、``positive`` = :math:`\\max(\\hat\\mu, 0)/\\hat\\sigma`、``one`` = 1）。
        description (str): README に書く説明。
    """

    name: str
    forecast: str
    transform: str
    description: str


SCORE_SPECS: tuple[ScoreSpec, ...] = (
    ScoreSpec(
        "sdtw", "dtw", "scaled", "DTW 近傍 3 個の翌 21 日リターン平均 / σ̂（本命）"
    ),
    ScoreSpec("sdtw_z", "dtw_z", "scaled", "窓ごとに標準化した形で DTW 照合 / σ̂"),
    ScoreSpec("sdtw_sign", "dtw", "sign", "DTW 予測の符号のみ"),
    ScoreSpec("sdtw_pos", "dtw", "positive", "DTW 予測の正の部分のみ / σ̂"),
    ScoreSpec(
        "sknn_euc", "euclid", "scaled", "ユークリッド距離の kNN（DTW との比較用）/ σ̂"
    ),
    ScoreSpec("base_ew", "ew", "one", "全スタイル等ウェイト（静的）"),
    ScoreSpec("base_hist", "hist", "scaled", "過去の平均リターン（拡大窓）/ σ̂"),
    ScoreSpec(
        "base_mom12", "mom12", "scaled", "スタイルの 12 か月（252 日）モメンタム / σ̂"
    ),
    ScoreSpec(
        "base_mom3", "mom3", "scaled", "スタイルの 3 か月（63 日）モメンタム / σ̂"
    ),
)


def blom_score(values: pd.DataFrame) -> pd.DataFrame:
    """各行（日付）内で Blom 正規スコアに変換する。

    Args:
        values (pd.DataFrame): 日付 × 銘柄の値（欠損は NaN のまま）。

    Returns:
        pd.DataFrame: 同じ形の正規スコア。

    Examples:
        >>> df = pd.DataFrame({"A": [1.0], "B": [2.0], "C": [3.0]}, index=[202001])
        >>> blom_score(df).round(3).iloc[0].tolist()
        [-0.869, 0.0, 0.869]
    """
    ranks = values.rank(axis=1, method="average")
    n = values.notna().sum(axis=1)
    u = ranks.sub(3.0 / 8.0).div(n.add(1.0 / 4.0), axis=0)
    return pd.DataFrame(
        norm.ppf(u.to_numpy(dtype=float)), index=values.index, columns=values.columns
    )


def average_signed(
    blom: Mapping[str, pd.DataFrame], members: Sequence[str]
) -> pd.DataFrame:
    """符号付き構成ファクターの Blom スコアを、値のあるものだけで平均する。

    Args:
        blom (Mapping[str, pd.DataFrame]): ファクター名 → Blom スコア（符号調整前）。
        members (Sequence[str]): 符号付きファクター名（``-`` 始まりは反転）。

    Returns:
        pd.DataFrame: 平均（全ファクター欠損の銘柄は NaN）。

    Examples:
        >>> a = pd.DataFrame({"X": [1.0], "Y": [-1.0]}, index=[0])
        >>> b = pd.DataFrame({"X": [0.5], "Y": [np.nan]}, index=[0])
        >>> average_signed({"a": a, "b": b}, ["a", "-b"]).iloc[0].tolist()
        [0.25, -1.0]
    """
    total: pd.DataFrame | None = None
    count: pd.DataFrame | None = None
    for m in members:
        sign, key = (-1.0, m[1:]) if m.startswith("-") else (1.0, m)
        part = sign * blom[key]
        total = part.fillna(0.0) if total is None else total + part.fillna(0.0)
        count = (
            part.notna().astype(int)
            if count is None
            else count + part.notna().astype(int)
        )
    assert total is not None and count is not None
    return (total / count).where(count > 0)


def build_style_scores(
    store: InputStore, universe: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """スタイルスコア :math:`S_{i,k,t}`（各月ユニバース内の Blom）を作る。

    Args:
        store (InputStore): 入力ストア。
        universe (pd.DataFrame): 月末 × 銘柄のユニバースウェイト（非構成銘柄は NaN）。

    Returns:
        dict[str, pd.DataFrame]: スタイル名 → 月末 × 銘柄のスコア（サイズを含む）。

    Examples:
        >>> s = build_style_scores(InputStore("input/msci_india_imi"), univ)  # doctest: +SKIP
        >>> sorted(s)[:2]  # doctest: +SKIP
        ['Financial Quality', 'Flow Value']
    """
    start, end = to_int(universe.index[0]), to_int(universe.index[-1])
    mask = universe.notna()
    keys = sorted(
        {m.lstrip("-") for members in STYLE_MEMBERS.values() for m in members}
    )
    blom = {}
    for k in keys:
        raw = store.alpha(f"core/{k}", start, end).reindex(
            index=universe.index, columns=universe.columns
        )
        blom[k] = blom_score(raw.where(mask))
    scores = {
        s: blom_score(average_signed(blom, members).where(mask))
        for s, members in STYLE_MEMBERS.items()
    }
    scores[SIZE_STYLE] = blom_score(-np.log(universe.where(universe > 0)))
    return scores


def load_daily_prices(
    data_root: Path, start: int, end: int, codes: pd.Index
) -> pd.DataFrame:
    """``data/turnover/YYYYMM.pkl`` から日次株価（分割調整済み、配当は含まない）を読む。

    Args:
        data_root (Path): ``data`` ディレクトリ。
        start (int): 開始年月（YYYYMM）。
        end (int): 終了年月（YYYYMM）。
        codes (pd.Index): 読み込む銘柄コード（BID）。

    Returns:
        pd.DataFrame: 営業日 × 銘柄の株価。

    Examples:
        >>> px = load_daily_prices(Path("data"), 200801, 200802, univ.columns)  # doctest: +SKIP
    """
    frames = []
    for path in sorted((data_root / "turnover").glob("*.pkl")):
        ym = int(path.stem)
        if start <= ym <= end:
            df = pd.read_pickle(path)
            frames.append(df.loc[df["bid"].isin(codes), ["yyyymmdd", "bid", "price"]])
    long = pd.concat(frames, ignore_index=True)
    px = long.pivot_table(
        index="yyyymmdd", columns="bid", values="price", aggfunc="last"
    )
    px.index = pd.to_datetime(px.index.astype(str), format="%Y%m%d")
    return px.sort_index()


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """日次リターンを作る（短い欠損は前方補完し、絶対値を ``RETURN_CLIP`` で抑える）。

    Args:
        prices (pd.DataFrame): 営業日 × 銘柄の株価。

    Returns:
        pd.DataFrame: 同じ形の日次リターン（初日と長い欠損の後は NaN）。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"])
        >>> daily_returns(pd.DataFrame({"A": [100.0, 110.0, 99.0]}, index=idx))["A"].round(3).tolist()
        [nan, 0.1, -0.1]
    """
    filled = prices.where(prices > 0).ffill(limit=MAX_FFILL_DAYS)
    ret = filled.pct_change(fill_method=None)
    return ret.clip(lower=-RETURN_CLIP, upper=RETURN_CLIP)


def month_end_days(days: pd.DatetimeIndex, months: pd.DatetimeIndex) -> pd.Series:
    """各月末に対応する、その月の最終営業日を返す。

    Args:
        days (pd.DatetimeIndex): 営業日。
        months (pd.DatetimeIndex): 月末日付。

    Returns:
        pd.Series: 月末 → 最終営業日（その月に営業日が無ければ NaT）。

    Examples:
        >>> d = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03"])
        >>> month_end_days(d, pd.to_datetime(["2020-01-31"])).iloc[0].date()
        datetime.date(2020, 1, 31)
    """
    s = pd.Series(days, index=days)
    last = s.groupby(days.to_period("M")).max()
    return pd.Series([last.get(m.to_period("M"), pd.NaT) for m in months], index=months)


def style_daily_returns(
    scores: Mapping[str, pd.DataFrame], returns: pd.DataFrame
) -> pd.DataFrame:
    """月末のスタイルスコアで Q5 / Q1 を固定し、翌月の日次 Q5−Q1（等ウェイト）を作る。

    Args:
        scores (Mapping[str, pd.DataFrame]): スタイル → 月末 × 銘柄のスコア。
        returns (pd.DataFrame): 営業日 × 銘柄の日次リターン。

    Returns:
        pd.DataFrame: 営業日 × スタイルの日次リターン :math:`g_{k,\\tau}`（最初の形成月の翌月から）。

    Examples:
        >>> g = style_daily_returns(scores, rets)  # doctest: +SKIP
    """
    months = next(iter(scores.values())).index
    day_period = returns.index.to_period("M")
    out = {}
    for style, sc in scores.items():
        parts = []
        for m in months:
            nxt = m.to_period("M") + 1
            sel = day_period == nxt
            if not sel.any():
                continue
            row = sc.loc[m].dropna()
            if len(row) < 20:
                continue
            p = (row.rank(method="average") - 0.5) / len(row)
            top = p.index[p >= TOP].intersection(returns.columns)
            bot = p.index[p < BOTTOM].intersection(returns.columns)
            r = returns.loc[sel]
            parts.append(r[top].mean(axis=1) - r[bot].mean(axis=1))
        out[style] = pd.concat(parts)
    return pd.DataFrame(out)


def dtw_distances(query: np.ndarray, candidates: np.ndarray, band: int) -> np.ndarray:
    """1 本のクエリと多数の候補の DTW 距離（Sakoe-Chiba 帯つき）を一括で計算する。

    漸化式 :math:`\\gamma(a,b) = (q_a - c_b)^2 + \\min\\{\\gamma(a-1,b), \\gamma(a,b-1), \\gamma(a-1,b-1)\\}`、
    距離は :math:`\\sqrt{\\gamma(L,L)}`。

    Args:
        query (np.ndarray): 長さ ``L`` のクエリ。
        candidates (np.ndarray): ``(N, L)`` の候補。
        band (int): 帯の幅（:math:`|a-b| \\le` ``band``）。

    Returns:
        np.ndarray: 長さ ``N`` の距離。

    Examples:
        >>> q = np.array([0.0, 1.0, 2.0, 2.0])
        >>> c = np.array([[0.0, 1.0, 2.0, 2.0], [0.0, 0.0, 1.0, 2.0], [2.0, 2.0, 2.0, 2.0]])
        >>> dtw_distances(q, c, band=1).round(3).tolist()
        [0.0, 0.0, 2.236]
    """
    n, length = candidates.shape
    prev = np.full((n, length + 1), np.inf)
    prev[:, 0] = 0.0
    for a in range(1, length + 1):
        cur = np.full((n, length + 1), np.inf)
        lo, hi = max(1, a - band), min(length, a + band)
        cost = (query[a - 1] - candidates[:, lo - 1 : hi]) ** 2
        for b in range(lo, hi + 1):
            best = np.minimum(np.minimum(prev[:, b], cur[:, b - 1]), prev[:, b - 1])
            cur[:, b] = cost[:, b - lo] + best
        prev = cur
    return np.sqrt(prev[:, length])


def euclid_distances(query: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """1 本のクエリと多数の候補のユークリッド距離。

    Args:
        query (np.ndarray): 長さ ``L`` のクエリ。
        candidates (np.ndarray): ``(N, L)`` の候補。

    Returns:
        np.ndarray: 長さ ``N`` の距離。

    Examples:
        >>> euclid_distances(np.array([0.0, 0.0]), np.array([[3.0, 4.0]])).tolist()
        [5.0]
    """
    return np.sqrt(((candidates - query) ** 2).sum(axis=1))


def select_neighbors(
    distances: np.ndarray, ends: np.ndarray, k: int, min_gap: int
) -> list[int]:
    """距離の小さい順に、互いに ``min_gap`` 以上離れた候補を ``k`` 個選ぶ。

    Args:
        distances (np.ndarray): 候補の距離。
        ends (np.ndarray): 候補の窓の終端位置（営業日の通し番号）。
        k (int): 選ぶ個数。
        min_gap (int): 終端どうしの最小間隔。

    Returns:
        list[int]: 選んだ候補の添字（距離の小さい順）。

    Examples:
        >>> select_neighbors(np.array([0.1, 0.2, 0.3, 0.4]), np.array([100, 101, 200, 300]), 3, 50)
        [0, 2, 3]
    """
    chosen: list[int] = []
    for idx in np.argsort(distances, kind="stable"):
        if not np.isfinite(distances[idx]):
            break
        if all(abs(int(ends[idx]) - int(ends[j])) >= min_gap for j in chosen):
            chosen.append(int(idx))
            if len(chosen) == k:
                break
    return chosen


def windows(cum: np.ndarray, length: int, znorm: bool) -> np.ndarray:
    """終端 ``u`` ごとの窓 :math:`(G_{u-L+j} - G_{u-L})_{j=1..L}` を作る（行 ``r`` は終端 ``u = r + L``）。

    Args:
        cum (np.ndarray): 累積リターン :math:`G`。
        length (int): 窓の長さ ``L``。
        znorm (bool): ``True`` なら窓ごとに平均 0・標準偏差 1 に標準化する。

    Returns:
        np.ndarray: ``(len(cum) - L, L)`` の窓。

    Examples:
        >>> windows(np.array([0.0, 1.0, 3.0, 6.0]), 2, False).tolist()
        [[1.0, 3.0], [2.0, 5.0]]
    """
    w = sliding_window_view(cum, length + 1)
    out = w[:, 1:] - w[:, [0]]
    if znorm:
        sd = out.std(axis=1, keepdims=True)
        out = (out - out.mean(axis=1, keepdims=True)) / np.where(sd > 0, sd, np.nan)
    return out


def forecast_style(
    g: pd.Series, query_days: pd.Series, method: str
) -> tuple[pd.Series, list[dict[str, object]]]:
    """1 スタイルについて、各月末の翌 ``HORIZON`` 日リターンの類似局面予測を作る。

    Args:
        g (pd.Series): 日次スタイルリターン（営業日インデックス、欠損なし）。
        query_days (pd.Series): 月末 → その月の最終営業日。
        method (str): ``dtw`` / ``dtw_z`` / ``euclid``。

    Returns:
        tuple[pd.Series, list[dict[str, object]]]: 月末 → 予測 :math:`\\hat\\mu`、近傍の記録。

    Examples:
        >>> mu, nb = forecast_style(g["Size"], qdays, "dtw")  # doctest: +SKIP
    """
    cum = np.concatenate([[0.0], np.log1p(g.to_numpy(dtype=float)).cumsum()])
    # cum[p + 1] = 日 p（g の位置）終了時点の G。位置 p を終端とする窓は windows の行 p + 1 - L。
    wins = windows(cum, WINDOW, znorm=(method == "dtw_z"))
    pos = pd.Series(np.arange(len(g)), index=g.index)
    mu, records = {}, []
    for month, day in query_days.items():
        if pd.isna(day) or day not in pos.index:
            continue
        p = int(pos[day])
        q_row = p + 1 - WINDOW
        last_cand = p - max(HORIZON, MIN_GAP)  # 翌 H 日が確定し、クエリと重ならない終端
        if q_row < 0 or last_cand - WINDOW + 1 < 0:
            continue
        ends = np.arange(WINDOW - 1, last_cand + 1)
        cand = wins[ends + 1 - WINDOW]
        query = wins[q_row]
        if not np.all(np.isfinite(query)):
            continue
        valid = np.all(np.isfinite(cand), axis=1)
        dist = np.full(len(ends), np.inf)
        if method == "euclid":
            dist[valid] = euclid_distances(query, cand[valid])
        else:
            dist[valid] = dtw_distances(query, cand[valid], BAND)
        chosen = select_neighbors(dist, ends, N_NEIGHBORS, MIN_GAP)
        if len(chosen) < N_NEIGHBORS:
            continue
        fwd = [cum[ends[j] + 1 + HORIZON] - cum[ends[j] + 1] for j in chosen]
        mu[month] = float(np.mean(fwd))
        for rank, (j, f) in enumerate(zip(chosen, fwd), start=1):
            records.append(
                {
                    "month": month,
                    "method": method,
                    "rank": rank,
                    "neighbor_end": g.index[ends[j]],
                    "distance": float(dist[j]),
                    "forward": float(f),
                }
            )
    return pd.Series(mu, dtype=float), records


def simple_forecasts(g: pd.Series, query_days: pd.Series) -> pd.DataFrame:
    """ベースライン用の予測（過去平均・モメンタム）と σ̂・翌月実現値を作る。

    Args:
        g (pd.Series): 日次スタイルリターン。
        query_days (pd.Series): 月末 → その月の最終営業日。

    Returns:
        pd.DataFrame: 月末 × 列 ``hist`` / ``mom12`` / ``mom3`` / ``sigma`` / ``realized``。
        ``sigma`` は日次標準偏差（拡大窓）× :math:`\\sqrt{H}`、``realized`` は翌月の累積対数リターン。

    Examples:
        >>> sf = simple_forecasts(g["Size"], qdays)  # doctest: +SKIP
    """
    lg = np.log1p(g)
    cum = lg.cumsum()
    pos = pd.Series(np.arange(len(g)), index=g.index)
    rows = {}
    days = [d for d in query_days if pd.notna(d) and d in pos.index]
    for i, (month, day) in enumerate(query_days.items()):
        if pd.isna(day) or day not in pos.index:
            continue
        p = int(pos[day])
        if p < MIN_HISTORY:
            continue
        hist = lg.iloc[: p + 1]
        nxt = days.index(day) + 1
        realized = (
            float(cum.iloc[int(pos[days[nxt]])] - cum.iloc[p])
            if nxt < len(days)
            else np.nan
        )
        rows[month] = {
            "hist": float(hist.mean()) * HORIZON,
            "mom12": float(cum.iloc[p] - cum.iloc[p - 252]),
            "mom3": float(cum.iloc[p] - cum.iloc[p - 63]),
            "sigma": float(hist.std()) * np.sqrt(HORIZON),
            "realized": realized,
        }
    return pd.DataFrame(rows).T


def style_weights(forecasts: pd.DataFrame, spec: ScoreSpec) -> pd.DataFrame:
    """スタイル予測（長い形式）からスコア 1 本分のスタイルウェイト :math:`\\tilde\\mu_{k,t}` を作る。

    Args:
        forecasts (pd.DataFrame): 列 ``month`` / ``style`` / 予測の各列 / ``sigma`` を持つ表。
        spec (ScoreSpec): スコア定義。

    Returns:
        pd.DataFrame: 月末 × スタイルのウェイト。

    Examples:
        >>> f = pd.DataFrame({"month": [1, 1], "style": ["a", "b"], "dtw": [0.02, -0.01], "sigma": [0.04, 0.02]})
        >>> style_weights(f, ScoreSpec("x", "dtw", "scaled", "")).loc[1].tolist()
        [0.5, -0.5]
    """
    mu = (
        forecasts.pivot(index="month", columns="style", values=spec.forecast)
        if spec.forecast != "ew"
        else None
    )
    sigma = forecasts.pivot(index="month", columns="style", values="sigma")
    if spec.transform == "one":
        return sigma.notna().astype(float).where(sigma.notna())
    assert mu is not None
    if spec.transform == "scaled":
        return mu / sigma
    if spec.transform == "sign":
        return np.sign(mu)
    if spec.transform == "positive":
        return mu.clip(lower=0.0) / sigma
    raise ValueError(f"未知の transform: {spec.transform}")


def combine_scores(
    style_scores: Mapping[str, pd.DataFrame],
    weights: pd.DataFrame,
    universe: pd.DataFrame,
) -> pd.DataFrame:
    """:math:`\\alpha_{i,t} = \\sum_k w_{k,t} S_{i,k,t}` を作り、各月ユニバース内で Blom 化する。

    スタイルスコアが欠損の銘柄はそのスタイルを 0（中立）とし、``MIN_STYLES`` 個未満しか無い銘柄は欠損にする。
    ウェイトがすべて 0 の月（例: ``positive`` で全スタイルが負の予測）は欠損にする。

    Args:
        style_scores (Mapping[str, pd.DataFrame]): スタイル → 月末 × 銘柄のスコア。
        weights (pd.DataFrame): 月末 × スタイルのウェイト。
        universe (pd.DataFrame): ユニバース。

    Returns:
        pd.DataFrame: 月末 × 銘柄の Blom スコア（ウェイトが無い月は行ごと欠損）。

    Examples:
        >>> alpha = combine_scores(style_scores, w, univ)  # doctest: +SKIP
    """
    idx = universe.index.intersection(weights.dropna(how="all").index)
    total = pd.DataFrame(0.0, index=idx, columns=universe.columns)
    count = pd.DataFrame(0, index=idx, columns=universe.columns)
    for style, sc in style_scores.items():
        s = sc.reindex(index=idx, columns=universe.columns)
        w = weights[style].reindex(idx).fillna(0.0)
        total += s.fillna(0.0).mul(w, axis=0)
        count += s.notna().astype(int)
    active = weights.reindex(idx).abs().sum(axis=1) > 0
    alpha = total.where((count >= MIN_STYLES) & universe.reindex(idx).notna())
    alpha = alpha.loc[active]
    return blom_score(alpha).reindex(universe.index)


def write_scores(
    scores: Mapping[str, pd.DataFrame], dest_root: Path, eval_start: int
) -> None:
    """スコアを ``{dest_root}/{name}/YYYYMM.dat`` に書き出し、README を作る。

    Args:
        scores (Mapping[str, pd.DataFrame]): スコア名 → 月末 × 銘柄。
        dest_root (Path): ``alpha/style_dtw`` ディレクトリ。
        eval_start (int): 書き出す最初の年月。

    Returns:
        None

    Examples:
        >>> write_scores(scores, Path("input/msci_india_imi/alpha/style_dtw"), 201101)  # doctest: +SKIP
    """
    lines = []
    for spec in SCORE_SPECS:
        panel = scores[spec.name]
        n, first, last = 0, None, None
        for date, row in panel.iterrows():
            ym = to_int(date)
            v = row.dropna()
            if ym < eval_start or v.empty:
                continue
            write_score_file(dest_root / spec.name / f"{ym}.dat", spec.name, v)
            n += 1
            first = first or ym
            last = ym
        logger.info("%s: %d ファイル（%s〜%s）", spec.name, n, first, last)
        lines.append(f"| `{spec.name}` | {spec.description} | {first}〜{last} |")
    (dest_root / "README.md").write_text(
        "# スタイル類似局面（DTW）スコア\n\n"
        "`scripts/build_style_dtw.py` で生成。日次スタイルリターン（スタイルスコアの Q5−Q1、等ウェイト）の"
        f"直近 {WINDOW} 営業日の推移を過去と DTW で照合し、近傍 {N_NEIGHBORS} 個の翌 {HORIZON} 日リターン平均で"
        "スタイルリターンを予測。予測 × スタイルスコアの和を各月ユニバース内で Blom 化。"
        "定式化と符号表は `experiments/style_dtw/README.md`。\n\n"
        "| スコア | 定義 | 期間 |\n| --- | --- | --- |\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解釈する。

    Args:
        argv (Sequence[str] | None): 引数（``None`` なら ``sys.argv``）。

    Returns:
        argparse.Namespace: 解釈結果。

    Examples:
        >>> parse_args(["--eval-start", "201201"]).eval_start
        201201
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    p.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument(
        "--start", type=int, default=200801, help="スタイルスコアを作る最初の月"
    )
    p.add_argument("--end", type=int, default=202606)
    p.add_argument(
        "--eval-start", type=int, default=201101, help="スコアを書き出す最初の月"
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """スタイル予測と個別株スコアを生成して書き出す。

    Args:
        argv (Sequence[str] | None): 引数。

    Returns:
        None

    Examples:
        >>> main(["--eval-start", "201101"])  # doctest: +SKIP
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    warnings.simplefilter("ignore", RuntimeWarning)
    args = parse_args(argv)
    store = InputStore(args.input_root)
    univ = store.universe(args.start, args.end)

    logger.info("スタイルスコアを作成")
    sscores = build_style_scores(store, univ)

    logger.info("日次株価を読み込み")
    end_next = to_int(univ.index[-1] + pd.offsets.MonthEnd(1))
    prices = load_daily_prices(args.data_root, args.start, end_next, univ.columns)
    rets = daily_returns(prices)

    logger.info("日次スタイルリターンを作成")
    g = style_daily_returns(sscores, rets).dropna(how="any")
    qdays = month_end_days(g.index, univ.index)

    logger.info("スタイル予測（DTW / ユークリッド / ベースライン）")
    frames, neighbor_rows = [], []
    for style in g.columns:
        sf = simple_forecasts(g[style], qdays)
        for method in ("dtw", "dtw_z", "euclid"):
            mu, recs = forecast_style(g[style], qdays, method)
            sf[method] = mu
            neighbor_rows += [{**r, "style": style} for r in recs]
        sf["ew"] = 1.0
        sf.index.name = "month"
        frames.append(sf.reset_index().assign(style=style))
        logger.info("  %s 完了", style)
    forecasts = pd.concat(frames, ignore_index=True)

    logger.info("個別株スコアを作成")
    out_scores = {}
    for spec in SCORE_SPECS:
        w = style_weights(forecasts, spec)
        out_scores[spec.name] = combine_scores(sscores, w, univ)
    write_scores(out_scores, args.input_root / "alpha" / GROUP, args.eval_start)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    g.to_csv(args.output_dir / "style_daily_returns.csv", index_label="date")
    forecasts.to_csv(args.output_dir / "style_forecasts.csv", index=False)
    pd.DataFrame(neighbor_rows).to_csv(args.output_dir / "neighbors.csv", index=False)
    logger.info("診断用 CSV を %s に出力", args.output_dir)


if __name__ == "__main__":
    main()
