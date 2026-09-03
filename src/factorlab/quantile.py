"""分位（クインタイル）分析.

規約:
  - 分位はユニバース（MSCI India IMI, size 1-3）のスコアで毎月分割
  - **Q5 = スコア最上位、Q1 = 最下位**（``ascending=True`` のとき）
  - ロングショートは ``Q5-Q1``
  - 超過リターンの対象は用途別に2通り
      EW: 分位内 等ウェイト  vs  ベンチマーク（MSCI India, size 1-2）等ウェイト
      CW: 分位内 時価総額ウェイト vs ベンチマーク時価総額ウェイト
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics
from .config import PERIODS_PER_YEAR


def assign_quantiles(
    panel: pd.DataFrame,
    score_col: str,
    q: int = 5,
    group_col: str | None = None,
    time_level: str = "yyyymm",
    ascending: bool = True,
    out_col: str = "quantile",
) -> pd.Series:
    """毎月クロスセクションで分位ラベル（1..q）を付与する.

    Parameters
    ----------
    group_col : 指定すると「グループ内分位（sort within group）」になる
                （docs/tutorial/07 の方式A: 業種・国中立な分位分割）
    ascending : True（既定）なら **Qq = スコア最大 / Q1 = 最小**.
                False なら逆順（Q1 = スコア最大）.
    """
    keys = [panel.index.get_level_values(time_level)]
    if group_col is not None:
        keys.append(panel[group_col].astype(str))

    s = panel[score_col].astype(float)
    if not ascending:
        s = -s  # 符号反転して Q1 = スコア最大 にする

    def _cut(x: pd.Series) -> pd.Series:
        v = x.dropna()
        n = len(v)
        if n < q:
            return pd.Series(np.nan, index=x.index, dtype=float)
        # 一様順位 u=(r-0.5)/n を [0,1] の等分割で切る.
        # u は順序反転で 1-u に写るため、昇順・降順で分位が厳密に鏡像になる.
        u = (v.rank(method="first") - 0.5) / n
        lab = np.floor(u * q).clip(0, q - 1) + 1
        return lab.reindex(x.index)

    return s.groupby(keys, sort=False).transform(_cut).rename(out_col)


def _wavg(values: pd.Series, weights: pd.Series | None) -> float:
    ok = values.notna()
    if weights is not None:
        ok &= weights.notna() & (weights > 0)
    if not ok.any():
        return np.nan
    v = values[ok]
    if weights is None:
        return float(v.mean())
    w = weights[ok]
    return float((v * w).sum() / w.sum())


def _portfolio_weights(sub: pd.DataFrame, ret_col: str, cap_col: str) -> tuple[pd.Series, pd.Series]:
    """分位内の等ウェイト / 時価総額ウェイト（bid インデックス、合計1）を返す."""
    ok = sub[ret_col].notna()
    idx = sub.index.get_level_values("bid")[ok]
    n = int(ok.sum())
    if n == 0:
        empty = pd.Series(dtype=float)
        return empty, empty
    w_ew = pd.Series(1.0 / n, index=idx)
    cap = pd.Series(sub[cap_col].to_numpy()[ok.to_numpy()], index=idx).where(lambda s: s > 0)
    w_cw = (cap / cap.sum()) if cap.sum() > 0 else w_ew.copy()
    return w_ew, w_cw.fillna(0.0)


def _drift(w: pd.Series, r: pd.Series) -> pd.Series:
    """期中の価格変動でドリフトさせた期末ウェイト（docs/tutorial 1.4）."""
    g = 1.0 + r.reindex(w.index).fillna(0.0)
    wd = w * g
    tot = wd.sum()
    return wd / tot if tot > 0 else w


def _turnover_frame(weights, rets, labels, weighting, spread, top=None, bot=None) -> pd.DataFrame:
    r"""片道回転率の月次系列.

    .. math:: \text{turnover}_t = \tfrac{1}{2} \sum_i \big| w_{i,t} - \tilde w_{i,t^-} \big|

    :math:`\tilde w_{i,t^-}` は前月のウェイトを期中リターンでドリフトさせたもの。
    ドリフトを無視すると等ウェイトの回転率が過大に出る。
    ロングショートは両レッグの合計。
    """
    yms = sorted(weights)
    rows = {}
    for prev, cur in zip(yms[:-1], yms[1:]):
        if (pd.Period(str(cur), "M") - pd.Period(str(prev), "M")).n != 1:
            continue
        row = {}
        for lab in labels:
            w_prev = weights[prev][weighting][lab]
            w_cur = weights[cur][weighting][lab]
            if len(w_prev) == 0 or len(w_cur) == 0:
                row[lab] = np.nan
                continue
            wd = _drift(w_prev, rets[prev])
            idx = wd.index.union(w_cur.index)
            row[lab] = float(0.5 * (w_cur.reindex(idx).fillna(0.0) - wd.reindex(idx).fillna(0.0)).abs().sum())
        _top = top if top is not None else labels[-1]
        _bot = bot if bot is not None else labels[0]
        row[spread] = row.get(_top, np.nan) + row.get(_bot, np.nan)
        rows[cur] = row
    return pd.DataFrame(rows).T.sort_index()


def _name_turnover_frame(weights, labels, spread) -> pd.DataFrame:
    """分位メンバーの入れ替わり率（新規銘柄数 / 当月銘柄数）."""
    yms = sorted(weights)
    rows = {}
    for prev, cur in zip(yms[:-1], yms[1:]):
        if (pd.Period(str(cur), "M") - pd.Period(str(prev), "M")).n != 1:
            continue
        row = {}
        for lab in labels:
            a = set(weights[prev]["ew"][lab].index)
            b = set(weights[cur]["ew"][lab].index)
            row[lab] = len(b - a) / len(b) if b else np.nan
        row[spread] = np.nan
        rows[cur] = row
    return pd.DataFrame(rows).T.sort_index()


def quantile_returns(
    panel: pd.DataFrame,
    score_col: str,
    q: int = 5,
    ret_col: str = "fwd_rtn",
    cap_col: str = "cap",
    bench_col: str = "in_bench",
    group_col: str | None = None,
    time_level: str = "yyyymm",
    ascending: bool = True,
    min_universe: int = 0,
    include_na: bool = True,
) -> dict[str, pd.DataFrame]:
    """分位ポートフォリオの月次リターン・超過リターン・銘柄数を計算する.

    Returns
    -------
    dict:
      ``"ew"``     : 分位別 等ウェイトリターン（列 Q1..Qq, "Q5-Q1", "bench"）
      ``"cw"``     : 分位別 時価総額ウェイトリターン（同上）
      ``"ew_excess"`` / ``"cw_excess"`` : ベンチマーク差引後
      ``"counts"`` : 分位別の月次銘柄数
      ``"ew_turnover"`` / ``"cw_turnover"`` : 片道回転率（月次）
      ``"name_turnover"`` : 分位メンバーの入れ替わり率（銘柄数ベース）

    ``include_na=True``（既定）なら、**スコア欠損銘柄のバケット（列名 "NA"）**も
    各フレームに追加する（分位が1つも組めない月は NaN）。
    欠損が情報を持つか（docs/tutorial 2.2）の診断に使う。

    ``min_universe`` を指定すると、スコア保有銘柄数がその数に満たない月を除外する
    （分位あたりの銘柄数が少ないとスペシフィックリスク支配になるため / docs/tutorial 07.1）.
    """
    df = panel.copy()
    # 評価リターンが1本も無い月は落とす（データ末尾は lag=1 の先行リターンが存在しない）
    n_ret = df.groupby(level=time_level)[ret_col].count()
    df = df[df.index.get_level_values(time_level).isin(n_ret[n_ret > 0].index)]
    if min_universe > 0:
        n_by_t = df.groupby(level=time_level)[score_col].count()
        keep = n_by_t[n_by_t >= min_universe].index
        df = df[df.index.get_level_values(time_level).isin(keep)]
    df["quantile"] = assign_quantiles(
        df, score_col, q=q, group_col=group_col, time_level=time_level, ascending=ascending
    )

    labels = [f"Q{i}" for i in range(1, q + 1)]
    ew_rows, cw_rows, cnt_rows, bench_rows = {}, {}, {}, {}
    weights: dict[int, dict[str, dict[str, pd.Series]]] = {}
    rets: dict[int, pd.Series] = {}

    for ym, blk in df.groupby(level=time_level, sort=True):
        b = blk.loc[blk[bench_col].fillna(False).astype(bool)]
        bench_rows[ym] = {
            "bench_ew": _wavg(b[ret_col], None),
            "bench_cw": _wavg(b[ret_col], b[cap_col]),
        }
        ew, cw, cnt = {}, {}, {}
        w_ew, w_cw = {}, {}
        for i, lab in enumerate(labels, start=1):
            sub = blk.loc[blk["quantile"] == i]
            ew[lab] = _wavg(sub[ret_col], None)
            cw[lab] = _wavg(sub[ret_col], sub[cap_col])
            cnt[lab] = int(sub[ret_col].notna().sum())
            w_ew[lab], w_cw[lab] = _portfolio_weights(sub, ret_col, cap_col)
        if include_na:
            # スコア欠損バケット。分位が1つも組めない月（全銘柄欠損等）は NaN のまま
            if blk["quantile"].notna().any():
                sub = blk.loc[blk["quantile"].isna()]
                ew["NA"] = _wavg(sub[ret_col], None)
                cw["NA"] = _wavg(sub[ret_col], sub[cap_col])
                cnt["NA"] = int(sub[ret_col].notna().sum())
                w_ew["NA"], w_cw["NA"] = _portfolio_weights(sub, ret_col, cap_col)
            else:
                ew["NA"], cw["NA"], cnt["NA"] = np.nan, np.nan, 0
                w_ew["NA"] = w_cw["NA"] = pd.Series(dtype=float)
        ew_rows[ym], cw_rows[ym], cnt_rows[ym] = ew, cw, cnt
        weights[ym] = {"ew": w_ew, "cw": w_cw}
        rets[ym] = blk[ret_col].droplevel(time_level)

    ew = pd.DataFrame(ew_rows).T.sort_index()
    cw = pd.DataFrame(cw_rows).T.sort_index()
    counts = pd.DataFrame(cnt_rows).T.sort_index()
    bench = pd.DataFrame(bench_rows).T.sort_index()

    spread = f"{labels[-1]}-{labels[0]}"
    ew[spread] = ew[labels[-1]] - ew[labels[0]]
    cw[spread] = cw[labels[-1]] - cw[labels[0]]
    ew["bench"] = bench["bench_ew"]
    cw["bench"] = bench["bench_cw"]

    ew_x = ew[labels].sub(bench["bench_ew"], axis=0)
    cw_x = cw[labels].sub(bench["bench_cw"], axis=0)
    ew_x[spread] = ew[spread]  # ロングショートは元々ベンチ中立
    cw_x[spread] = cw[spread]
    if include_na:
        ew_x["NA"] = ew["NA"] - bench["bench_ew"]
        cw_x["NA"] = cw["NA"] - bench["bench_cw"]

    labels_t = labels + (["NA"] if include_na else [])
    turn = {w: _turnover_frame(weights, rets, labels_t, w, spread,
                               top=labels[-1], bot=labels[0]) for w in ("ew", "cw")}
    names = _name_turnover_frame(weights, labels_t, spread)

    for f in (ew, cw, ew_x, cw_x, counts, bench, names, *turn.values()):
        f.index.name = time_level

    return {
        "ew": ew, "cw": cw, "ew_excess": ew_x, "cw_excess": cw_x,
        "counts": counts, "bench": bench,
        "ew_turnover": turn["ew"], "cw_turnover": turn["cw"], "name_turnover": names,
    }


def quantile_summary(
    res: dict[str, pd.DataFrame],
    weighting: str = "ew",
    periods: int = PERIODS_PER_YEAR,
) -> pd.DataFrame:
    """分位別のリターン・リスク・回転率の表.

    列の意味:

    - ``ann_return`` / ``ann_vol``  : 分位ポートフォリオ自身の年率リターン（幾何）・年率ボラ
    - ``ann_excess``               : ``ann_return`` − ベンチマークの年率リターン（幾何差）
    - ``ann_excess_arith``         : 月次超過リターンの平均 × 12（算術）
    - ``te``                       : トラッキングエラー = 月次超過リターンの年率標準偏差
    - ``IR``                       : ``ann_excess_arith`` / ``te``
    - ``max_dd``                   : 分位ポートフォリオ自身の最大ドローダウン（絶対）
    - ``max_dd_excess``            : 累積超過リターン系列の最大ドローダウン（対ベンチの劣後幅）
    - ``turnover_1way``            : 片道回転率の月次平均（ドリフト調整済み）
    - ``turnover_ann``             : 同 × 12（年率、片道）
    - ``name_turnover``            : 分位メンバーの入れ替わり率（月次平均）
    """
    gross = res[weighting]
    excess = res[f"{weighting}_excess"]
    counts = res["counts"]
    turn = res.get(f"{weighting}_turnover")
    names = res.get("name_turnover")
    bench_ann = metrics.ann_return(gross["bench"], periods)

    rows = {}
    for c in excess.columns:
        s = metrics.summarize(excess[c], periods)
        ann_port = metrics.ann_return(gross[c], periods)
        t_1way = turn[c].mean() if (turn is not None and c in turn.columns) else np.nan
        rows[c] = {
            "ann_return": ann_port,
            "ann_excess": ann_port - bench_ann if c in counts.columns else ann_port,
            "ann_excess_arith": s["mean_monthly"] * periods,
            "ann_vol": metrics.ann_vol(gross[c], periods),
            "te": s["ann_vol"],
            "IR": (s["mean_monthly"] * periods) / s["ann_vol"] if s["ann_vol"] > 0 else np.nan,
            "t_stat_NW": s["t_stat_NW"],
            "hit_ratio": s["hit_ratio"],
            "max_dd": metrics.max_drawdown(gross[c]),
            "max_dd_excess": metrics.max_drawdown(excess[c]),
            "turnover_1way": t_1way,
            "turnover_ann": t_1way * periods,
            "name_turnover": (names[c].mean() if (names is not None and c in names.columns) else np.nan),
            "n_stocks_mean": counts[c].mean() if c in counts.columns else np.nan,
            "n_stocks_min": counts[c].min() if c in counts.columns else np.nan,
        }
    out = pd.DataFrame(rows).T
    out.loc["bench", ["ann_return", "ann_vol", "max_dd"]] = [
        bench_ann, metrics.ann_vol(gross["bench"], periods), metrics.max_drawdown(gross["bench"]),
    ]
    return out


def monotonicity(summary: pd.DataFrame, q: int = 5, value_col: str = "ann_excess_arith") -> float:
    """分位の単調性: 年率超過リターンと分位番号の順位相関.

    Q5 = スコア最上位の規約では **+1 が完全単調増加（Q1 -> Q5 で右肩上がり）= 良好**.
    """
    labels = [f"Q{i}" for i in range(1, q + 1)]
    s = summary.loc[labels, value_col]
    return float(pd.Series(range(1, q + 1)).corr(pd.Series(s.to_numpy()), method="spearman"))
