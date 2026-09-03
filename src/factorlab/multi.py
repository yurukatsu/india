"""複数ファクターの一括比較.

`data/factor/core.yaml` のスタイル定義に沿って、多数のコアファクターを
同時にスコア化し、パフォーマンスと相互相関を行列として並べる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

from . import ic as ic_mod
from . import metrics, preprocess, quantile
from .config import DATA_DIR, PERIODS_PER_YEAR


# --- スタイル定義 --------------------------------------------------------------


def load_style_map(
    path=None,
    exclude: tuple[str, ...] = ("Seasonality",),
    lower: bool = True,
) -> dict[str, list[str]]:
    """``data/factor/core.yaml`` を読み、``{style: [factor, ...]}`` を返す.

    ``exclude`` のスタイルは除外する（既定で ``Seasonality`` — `factor/core` に実データが無い）.
    ``lower=True`` で `factor/core` の実カラム名（小文字）に合わせる.
    """
    path = path or (DATA_DIR / "factor" / "core.yaml")
    with open(path) as f:
        y = yaml.safe_load(f)
    out = {}
    for style, facs in y.items():
        if style in exclude:
            continue
        out[style] = [f.lower() if lower else f for f in facs]
    return out


def flatten_styles(style_map: dict[str, list[str]]) -> tuple[list[str], pd.Series]:
    """``(factors, factor -> style の Series)`` を返す（core.yaml の並び順を保持）."""
    factors = [f for facs in style_map.values() for f in facs]
    groups = pd.Series({f: s for s, facs in style_map.items() for f in facs}, name="style")
    return factors, groups.reindex(factors)


# --- 行列: クロスセクション相関 / 分位の重複 ---------------------------------------


def _month_blocks(panel: pd.DataFrame, cols: list[str], time_level: str = "yyyymm"):
    """月ごとの ``(yyyymm, ndarray(n, K))`` を返すジェネレータ."""
    arr = panel[cols].to_numpy(dtype=float)
    ym = panel.index.get_level_values(time_level).to_numpy()
    edges = np.flatnonzero(np.r_[True, ym[1:] != ym[:-1], True])
    for a, b in zip(edges[:-1], edges[1:]):
        yield ym[a], arr[a:b]


def _standardize_cols(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """列ごとに（有効値のみで）平均0・分散1に標準化し、欠損は0で埋めた行列とマスクを返す."""
    m = np.isfinite(x)
    z = np.where(m, x, np.nan)
    mu = np.nanmean(z, axis=0)
    sd = np.nanstd(z, axis=0, ddof=1)
    sd = np.where((sd > 0) & np.isfinite(sd), sd, np.nan)
    z = (z - mu) / sd
    return np.nan_to_num(z, nan=0.0), m & np.isfinite(z)


def _rank_cols(x: np.ndarray) -> np.ndarray:
    """列ごとの平均順位（欠損は NaN のまま）."""
    out = np.full_like(x, np.nan, dtype=float)
    for j in range(x.shape[1]):
        col = x[:, j]
        ok = np.isfinite(col)
        if ok.sum() == 0:
            continue
        out[ok, j] = pd.Series(col[ok]).rank(method="average").to_numpy()
    return out


def cross_sectional_corr(
    panel: pd.DataFrame,
    cols: list[str],
    method: str = "spearman",
    time_level: str = "yyyymm",
    min_obs: int = 20,
    labels: list[str] | None = None,
) -> pd.DataFrame:
    """月次クロスセクション相関行列の**期間平均**.

    ``method="spearman"``（順位相関）が既定。欠損はペアワイズで扱う
    （列ごとの標準化は有効値のみ、内積は共通有効ペアのみで割る）。
    """
    K = len(cols)
    num = np.zeros((K, K))
    cnt = np.zeros((K, K))
    for _, x in _month_blocks(panel, cols, time_level):
        if x.shape[0] < min_obs:
            continue
        v = _rank_cols(x) if method == "spearman" else x
        z, m = _standardize_cols(v)
        n_pair = m.astype(float).T @ m.astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            c = (z.T @ z) / np.maximum(n_pair - 1.0, 1.0)
        ok = n_pair >= min_obs
        num[ok] += np.clip(c[ok], -1.0, 1.0)
        cnt[ok] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(cnt > 0, num / cnt, np.nan)
    labels = labels or cols
    return pd.DataFrame(out, index=labels, columns=labels)


def quantile_overlap(
    panel: pd.DataFrame,
    cols: list[str],
    q: int = 5,
    which: str = "top",
    time_level: str = "yyyymm",
    min_obs: int = 20,
    labels: list[str] | None = None,
) -> pd.DataFrame:
    r"""上位（下位）分位メンバーの**重複率**の期間平均.

    .. math:: \text{overlap}_{ab} = \frac{|\,Q_a \cap Q_b\,|}{\sqrt{|Q_a|\,|Q_b|}}

    分位サイズがほぼ等しいので「同じ銘柄をどれだけ拾っているか」の割合になる。
    ロングオンリーで複数ファクターを併用するときの**銘柄の二重計上**を見るための指標。

    無相関なら :math:`1/q`（5分位なら 0.2）が基準値。
    """
    K = len(cols)
    num = np.zeros((K, K))
    cnt = 0
    for _, x in _month_blocks(panel, cols, time_level):
        n = x.shape[0]
        if n < min_obs:
            continue
        r = _rank_cols(x)
        valid = np.isfinite(r)
        nv = valid.sum(axis=0).astype(float)
        u = np.where(valid, (r - 0.5) / np.where(nv > 0, nv, np.nan), np.nan)
        thr = (q - 1) / q if which == "top" else 1.0 / q
        b = (u >= thr) if which == "top" else (u < thr)
        b = np.nan_to_num(b.astype(float))
        inter = b.T @ b
        sizes = b.sum(axis=0)
        denom = np.sqrt(np.outer(sizes, sizes))
        with np.errstate(invalid="ignore", divide="ignore"):
            num += np.where(denom > 0, inter / denom, np.nan)
        cnt += 1
    labels = labels or cols
    return pd.DataFrame(num / cnt if cnt else num, index=labels, columns=labels)


# --- スコアボード（ファクター別のパフォーマンス） -----------------------------------


def build_scores(
    panel: pd.DataFrame,
    factors: list[str],
    method: str = "blom",
    group_col: str | None = None,
    control_cols: list[str] | None = None,
    suffix: str | None = None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """全ファクターを一括で標準化（＋任意で中立化）し、``{factor: 列名}`` を返す.

    ``suffix`` の既定は ``f"_{method}"``。同じパネルに素の版と中立化版を共存させる
    場合は別の suffix（例 ``"_blom_ss"``）を指定する。
    """
    suffix = f"_{method}" if suffix is None else suffix
    out = preprocess.standardize_panel(
        panel, factors, method=method, group_col=group_col, control_cols=control_cols, suffix=suffix
    )
    return out, {f: f"{f}{suffix}" for f in factors}


def factor_scoreboard(
    panel: pd.DataFrame,
    score_cols: dict[str, str],
    groups: pd.Series | None = None,
    q: int = 5,
    weighting: str = "ew",
    periods: int = PERIODS_PER_YEAR,
    with_ic: bool = True,
    min_universe: int = 0,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """全ファクターについて分位分析と IC を回し、1行1ファクターの表にまとめる.

    Returns
    -------
    (scoreboard, results):
      scoreboard : 1行1ファクター（`Q5-Q1` と `Q5` の年率超過・IR・TE・DD・回転率・IC）
      results    : ``{factor: quantile_returns の戻り値}``（後段の相関計算に使う）
    """
    top, bot = f"Q{q}", "Q1"
    spread = f"{top}-{bot}"
    rows, results = {}, {}
    for f, col in score_cols.items():
        res = quantile.quantile_returns(panel, col, q=q, ascending=True, min_universe=min_universe)
        s = quantile.quantile_summary(res, weighting=weighting, periods=periods)
        results[f] = res
        row = {
            "ls_ann_excess": s.loc[spread, "ann_excess"],
            "ls_ann_excess_arith": s.loc[spread, "ann_excess_arith"],
            "ls_te": s.loc[spread, "te"],
            "ls_IR": s.loc[spread, "IR"],
            "ls_t_NW": s.loc[spread, "t_stat_NW"],
            "ls_max_dd": s.loc[spread, "max_dd_excess"],
            "ls_turnover_ann": s.loc[spread, "turnover_ann"],
            "top_ann_excess": s.loc[top, "ann_excess"],
            "top_ann_excess_arith": s.loc[top, "ann_excess_arith"],
            "top_te": s.loc[top, "te"],
            "top_IR": s.loc[top, "IR"],
            "top_t_NW": s.loc[top, "t_stat_NW"],
            "top_max_dd": s.loc[top, "max_dd_excess"],
            "top_turnover_ann": s.loc[top, "turnover_ann"],
            "n_stocks_mean": s.loc[top, "n_stocks_mean"],
            # 生列があればそのカバレッジ、なければスコア列（スタイル合成はこちら）
            "coverage": float(panel[f if f in panel.columns else col].notna().mean()),
        }
        if with_ic:
            icx = ic_mod.information_coefficient(panel, col, "fwd_rtn", method="spearman")
            summ = ic_mod.ic_summary(icx, periods)
            row |= {"mean_IC": summ["mean_IC"], "ICIR_ann": summ["ICIR_ann"], "IC_t_NW": summ["t_stat_NW"]}
        rows[f] = row

    sb = pd.DataFrame(rows).T
    if groups is not None:
        sb.insert(0, "style", groups.reindex(sb.index))
    return sb, results


def return_matrix(
    results: dict[str, pd.DataFrame],
    key: str,
    weighting: str = "ew",
    excess: bool = True,
) -> pd.DataFrame:
    """``{factor: quantile_returns}`` から月次リターン系列を集めて DataFrame にする.

    ``key`` は ``"Q5-Q1"``（ロングショート）や ``"Q5"``（上位分位）等。
    ``excess=True`` ならベンチマーク差引後（ロングショートは元々ベンチ中立なので同値）。
    """
    src = f"{weighting}_excess" if excess else weighting
    return pd.DataFrame({f: res[src][key] for f, res in results.items()})


def style_composite(
    panel: pd.DataFrame,
    style_map: dict[str, list[str]],
    score_cols: dict[str, str],
    method: str = "blom",
    time_level: str = "yyyymm",
) -> tuple[pd.DataFrame, dict[str, str]]:
    """スタイル合成スコア（構成ファクターの標準化スコアの平均 → 再標準化）を作る.

    `core.yaml` の「core factor をまとめると style になる」を素直に実装したもの。
    欠損は利用可能なファクターのみで平均する（`docs/tutorial` 2.6 の再ウェイト）。
    """
    out = panel.copy()
    cols = {}
    for style, facs in style_map.items():
        use = [score_cols[f] for f in facs if score_cols.get(f) in out.columns]
        if not use:
            continue
        tmp = f"__tmp_{style}"
        out[tmp] = out[use].mean(axis=1, skipna=True)
        out = preprocess.standardize_panel(out, tmp, method=method, suffix="_std", time_level=time_level)
        name = "style_" + style.lower().replace(" ", "_").replace("/", "_")
        out[name] = out[f"{tmp}_std"]
        out = out.drop(columns=[tmp, f"{tmp}_std"])
        cols[style] = name
    return out, cols
