"""可視化ユーティリティ（図中のテキストは英語）."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import PERIODS_PER_YEAR

# --- 共通スタイル -------------------------------------------------------------

QUANTILE_CMAP = "RdYlBu_r"
SPREAD_COLOR = "#111111"
NA_COLOR = "#9E9E9E"
NA_LABEL = "No score"
POS_COLOR = "#4C72B0"
NEG_COLOR = "#C44E52"

WEIGHT_LABEL = {"ew": "Equal-weighted", "cw": "Cap-weighted"}

TSTAT_NOTE = ("t = Newey-West t-stat on the monthly excess return (arithmetic mean); "
              "its sign can differ from a geometric bar when volatility differs across quintiles")

VALUE_LABEL = {
    "ann_excess": "Annualized excess return (geometric)",
    "ann_excess_arith": "Annualized excess return (arithmetic)",
    "ann_return": "Annualized return (geometric)",
}


def set_style() -> None:
    plt.rcParams.update({
        "figure.dpi": 110,
        "savefig.dpi": 150,
        "savefig.bbox": "tight",
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.labelsize": 9.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linewidth": 0.6,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
    })


def to_datetime_index(idx: pd.Index) -> pd.DatetimeIndex:
    """yyyymm(int) の Index を月末 DatetimeIndex に変換."""
    return pd.PeriodIndex(idx.astype(int).astype(str), freq="M").to_timestamp(how="end")


def quantile_colors(q: int) -> list:
    return [plt.get_cmap(QUANTILE_CMAP)(x) for x in np.linspace(0.08, 0.92, q)]


def _pct(ax, axis: str = "y", decimals: int = 0) -> None:
    fmt = plt.FuncFormatter(lambda v, _: f"{v * 100:.{decimals}f}%")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def _bar_labels(ax, xs, vals, tstats=None, value_fmt: str = "{:+.2f}%", scale: float = 100.0,
                fontsize: float = 9) -> None:
    """バー端の外側に値ラベル、その外側に t 値を注記（オフセットは points 指定で重なりを回避）."""
    for i, v in zip(xs, vals):
        up = v >= 0
        ax.annotate(value_fmt.format(v * scale), xy=(i, v), xytext=(0, 5 if up else -5),
                    textcoords="offset points", ha="center", va="bottom" if up else "top",
                    fontsize=fontsize, fontweight="bold")
        if tstats is not None:
            ax.annotate(f"t={tstats[i]:.2f}", xy=(i, v), xytext=(0, 19 if up else -19),
                        textcoords="offset points", ha="center", va="bottom" if up else "top",
                        fontsize=8, color="#777777")


def _quantile_ticks(ax, bars: list[str], counts: pd.DataFrame | None) -> None:
    """x 軸ラベルを 2 行にして銘柄数を併記（Q1 / n=61）."""
    labels = []
    for b in bars:
        if counts is not None and b in counts.columns:
            labels.append(f"{b}\nn={counts[b].mean():.0f}")
        else:
            labels.append(f"{b}\n ")
    ax.set_xticks(range(len(bars)), labels)


# --- 前処理の診断 --------------------------------------------------------------


def plot_transform_comparison(
    panel: pd.DataFrame,
    factor: str,
    variants: dict[str, str] | None = None,
    bins: int = 60,
    title: str | None = None,
):
    """raw / z-score / Blom の分布とQQプロットを並べて裾の厚さを確認する."""
    from scipy import stats

    variants = variants or {"Raw": factor, "z-score": f"{factor}_zscore", "Blom": f"{factor}_blom"}
    n = len(variants)
    fig, axes = plt.subplots(2, n, figsize=(4.0 * n, 6.4))
    for j, (name, col) in enumerate(variants.items()):
        x = panel[col].dropna().to_numpy()
        ax = axes[0, j]
        ax.hist(x, bins=bins, color=POS_COLOR, alpha=0.85, edgecolor="white", linewidth=0.3)
        ax.set_title(f"{name}\nskew={stats.skew(x):.2f}, excess kurt={stats.kurtosis(x):.2f}")
        ax.set_ylabel("Frequency" if j == 0 else "")
        ax2 = axes[1, j]
        stats.probplot(x, dist="norm", plot=ax2)
        ax2.set_title("")
        ax2.get_lines()[0].set(markersize=2, alpha=0.35, color=POS_COLOR)
        ax2.get_lines()[1].set(color=NEG_COLOR, linewidth=1.2)
        ax2.set_xlabel("Theoretical quantiles (normal)")
        ax2.set_ylabel("Sample quantiles" if j == 0 else "")
    fig.suptitle(title or f"{factor}: distribution before / after transform (pooled)",
                 y=0.99, fontweight="bold")
    fig.tight_layout()
    return fig


def plot_coverage(cov: pd.DataFrame, factor_cols: list[str] | None = None):
    """ユニバース銘柄数とファクターカバレッジの時系列."""
    factor_cols = factor_cols or [c for c in cov.columns if c.startswith("cov_")]
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.6), sharex=True)
    x = to_datetime_index(cov.index)
    axes[0].plot(x, cov["n_universe"], label="Universe — MSCI India IMI (size 1-3)", color=POS_COLOR)
    axes[0].plot(x, cov["n_bench"], label="Benchmark — MSCI India (size 1-2)", color=NEG_COLOR)
    axes[0].set_ylabel("Number of stocks")
    axes[0].legend(ncol=2)
    axes[0].set_title("Universe size")
    for c in factor_cols:
        axes[1].plot(x, cov[c], label=c.replace("cov_", ""), linewidth=1.2)
    axes[1].set_ylim(0, 1.02)
    axes[1].set_ylabel("Non-missing ratio")
    axes[1].set_title("Factor coverage")
    axes[1].legend(ncol=4)
    _pct(axes[1])
    fig.tight_layout()
    return fig


# --- 分位分析 -----------------------------------------------------------------


def plot_quantile_timeseries(
    res: dict[str, pd.DataFrame],
    weighting: str = "ew",
    q: int = 5,
    factor: str = "",
    log_scale: bool = False,
    show_na: bool = False,
):
    """分位別 累積超過リターン（対ベンチマーク）とロングショートスプレッドの時系列.

    ``show_na=True`` でスコア欠損バケット（"NA" 列）を灰色破線で重ねる。
    """
    labels = [f"Q{i}" for i in range(1, q + 1)]
    spread = f"{labels[-1]}-{labels[0]}"
    ex = res[f"{weighting}_excess"]
    x = to_datetime_index(ex.index)
    colors = quantile_colors(q)
    wname = WEIGHT_LABEL[weighting]

    fig, axes = plt.subplots(2, 1, figsize=(11, 7.2), sharex=True, height_ratios=[2, 1])
    for lab, c in zip(labels, colors):
        axes[0].plot(x, (1 + ex[lab]).cumprod() - 1, label=lab, color=c, linewidth=1.4)
    if show_na and "NA" in ex.columns:
        axes[0].plot(x, (1 + ex["NA"].fillna(0)).cumprod() - 1, label=NA_LABEL,
                     color=NA_COLOR, linewidth=1.6, linestyle="--")
    axes[0].axhline(0, color="grey", linewidth=0.8)
    axes[0].set_ylabel("Cumulative excess return")
    axes[0].set_title(f"{factor}: cumulative excess return by quintile "
                      f"({wname} portfolio vs {wname.lower()} benchmark)")
    axes[0].legend(ncol=q + (1 if show_na and "NA" in ex.columns else 0), loc="upper left")
    _pct(axes[0])
    if log_scale:
        axes[0].set_yscale("symlog", linthresh=0.1)

    ls = (1 + ex[spread]).cumprod() - 1
    axes[1].plot(x, ls, color=SPREAD_COLOR, linewidth=1.5)
    axes[1].fill_between(x, 0, ls, color=SPREAD_COLOR, alpha=0.10)
    axes[1].axhline(0, color="grey", linewidth=0.8)
    axes[1].set_ylabel("Cumulative return")
    axes[1].set_title(f"Long-short {spread} ({wname})")
    _pct(axes[1])
    fig.tight_layout()
    return fig


def plot_quantile_bar(
    summary: pd.DataFrame,
    counts: pd.DataFrame | None = None,
    q: int = 5,
    factor: str = "",
    weighting: str = "ew",
    value_col: str = "ann_excess",
    show_tstat: bool = True,
    include_na: bool = False,
):
    """分位別 年率超過リターンの棒グラフ（x軸ラベルに銘柄数、バー外側に値と t 値）.

    ``include_na=True`` でスコア欠損バケット（"NA" 行）の灰色バーを右端に追加。
    """
    labels = [f"Q{i}" for i in range(1, q + 1)]
    spread = f"{labels[-1]}-{labels[0]}"
    bars = labels + [spread]
    colors = quantile_colors(q) + [SPREAD_COLOR]
    if include_na and "NA" in summary.index:
        bars = bars + ["NA"]
        colors = colors + [NA_COLOR]
    vals = summary.loc[bars, value_col].astype(float)
    wname = WEIGHT_LABEL[weighting]
    vlabel = VALUE_LABEL.get(value_col, value_col)

    fig, ax = plt.subplots(figsize=(8.6, 5.0))
    ax.bar(range(len(bars)), vals.to_numpy(), color=colors, edgecolor="white", linewidth=0.8, width=0.68)
    ax.axhline(0, color="#333333", linewidth=0.9)
    _quantile_ticks(ax, bars, counts)
    ax.set_ylabel(vlabel)
    ax.set_title(f"{factor}: {vlabel.lower()} by quintile ({wname})")
    _pct(ax)

    tstats = summary.loc[bars, "t_stat_NW"].to_numpy() if (show_tstat and "t_stat_NW" in summary) else None
    _bar_labels(ax, range(len(bars)), vals.to_numpy(), tstats)
    ax.margins(y=0.22)
    fig.tight_layout()
    if tstats is not None:
        fig.text(0.5, -0.02, TSTAT_NOTE, ha="center", fontsize=7.5, color="#888888")
    return fig


def plot_quantile_panel(
    res: dict[str, pd.DataFrame],
    summaries: dict[str, pd.DataFrame],
    q: int = 5,
    factor: str = "",
    value_col: str = "ann_excess",
    show_tstat: bool = True,
):
    """EW / CW の年率超過リターンを横並びで比較."""
    labels = [f"Q{i}" for i in range(1, q + 1)]
    spread = f"{labels[-1]}-{labels[0]}"
    bars = labels + [spread]
    colors = quantile_colors(q) + [SPREAD_COLOR]
    vlabel = VALUE_LABEL.get(value_col, value_col)

    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.0), sharey=True)
    for ax, w in zip(axes, ("ew", "cw")):
        vals = summaries[w].loc[bars, value_col].astype(float)
        ax.bar(range(len(bars)), vals.to_numpy(), color=colors, edgecolor="white", linewidth=0.8, width=0.68)
        ax.axhline(0, color="#333333", linewidth=0.9)
        _quantile_ticks(ax, bars, res["counts"])
        ax.set_title(WEIGHT_LABEL[w])
        tstats = summaries[w].loc[bars, "t_stat_NW"].to_numpy() if (show_tstat and "t_stat_NW" in summaries[w]) else None
        _bar_labels(ax, range(len(bars)), vals.to_numpy(), tstats, value_fmt="{:+.1f}%")
        ax.margins(y=0.22)
    axes[0].set_ylabel(vlabel)
    _pct(axes[0])
    fig.suptitle(f"{factor}: {vlabel.lower()} by quintile", fontweight="bold")
    fig.tight_layout()
    if show_tstat:
        fig.text(0.5, -0.02, TSTAT_NOTE, ha="center", fontsize=7.5, color="#888888")
    return fig


# --- IC -----------------------------------------------------------------------


def plot_ic(ic: pd.Series, ma_window: int = 12, factor: str = "", method: str = "Spearman"):
    """IC の月次時系列（移動平均つき）と累積IC."""
    x = to_datetime_index(ic.index)
    mean = ic.mean()
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.6), sharex=True, height_ratios=[1.3, 1])

    colors = np.where(ic.to_numpy() >= 0, POS_COLOR, NEG_COLOR)
    axes[0].bar(x, ic.to_numpy(), width=22, color=colors, alpha=0.45, linewidth=0)
    axes[0].plot(x, ic.rolling(ma_window, min_periods=ma_window // 2).mean(),
                 color="#111111", linewidth=1.6, label=f"{ma_window}M moving average")
    axes[0].axhline(mean, color=NEG_COLOR, linestyle="--", linewidth=1.1,
                    label=f"Full-period mean = {mean:.3f}")
    axes[0].axhline(0, color="grey", linewidth=0.8)
    axes[0].set_ylabel(f"IC ({method})")
    axes[0].set_title(f"{factor}: monthly information coefficient")
    axes[0].legend(ncol=2, loc="upper left")

    axes[1].plot(x, ic.fillna(0).cumsum(), color=POS_COLOR, linewidth=1.6)
    axes[1].fill_between(x, 0, ic.fillna(0).cumsum(), color=POS_COLOR, alpha=0.12)
    axes[1].axhline(0, color="grey", linewidth=0.8)
    axes[1].set_ylabel("Cumulative IC")
    axes[1].set_title("Cumulative IC (a steady slope means a stable sign)")
    fig.tight_layout()
    return fig


def plot_ic_decay(summary: pd.DataFrame, factor: str = "", tstat_line: float = 2.0,
                  tstat_col: str = "t_stat_NW"):
    """ホライズン別の平均IC（減衰）と t 値."""
    h = summary.index.to_numpy()
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 4.4))
    cmap = plt.get_cmap("Blues_r")
    cols = [cmap(0.15 + 0.55 * i / max(len(h) - 1, 1)) for i in range(len(h))]

    axes[0].bar(h, summary["mean_IC"], color=cols, edgecolor="white", linewidth=0.7)
    axes[0].axhline(0, color="#333333", linewidth=0.9)
    axes[0].set_xlabel("Horizon h (single-month return, h months ahead)")
    axes[0].set_ylabel("Mean IC")
    axes[0].set_title("IC decay")
    axes[0].set_xticks(h)
    _bar_labels(axes[0], h, summary["mean_IC"].to_numpy(), value_fmt="{:.3f}", scale=1.0, fontsize=7.5)
    axes[0].margins(y=0.20)

    axes[1].bar(h, summary[tstat_col], color=cols, edgecolor="white", linewidth=0.7)
    for y in (tstat_line, -tstat_line):
        axes[1].axhline(y, color=NEG_COLOR, linestyle="--", linewidth=1.0)
    axes[1].axhline(0, color="#333333", linewidth=0.9)
    axes[1].set_xlabel("Horizon h")
    axes[1].set_ylabel("t-stat (Newey-West)" if tstat_col == "t_stat_NW" else "t-stat")
    axes[1].set_title(f"Significance (dashed = ±{tstat_line:g})")
    axes[1].set_xticks(h)
    fig.suptitle(f"{factor}: IC decay", fontweight="bold")
    fig.tight_layout()
    return fig


# --- ファクターリターン（クロスセクション回帰） ---------------------------------


def plot_factor_return(fr: pd.DataFrame, factor: str = "", ma_window: int = 12,
                       show_compound: bool = True):
    """月次ファクターリターンと累積ファクターリターン.

    累積は単純和（実線）を主とし、複利（破線）を参考に重ねる。
    f_t はゼロ投資のロングショート・ポートフォリオのリターンなので、
    単純和が素直な「情報の蓄積」の読み方になる。
    """
    x = to_datetime_index(fr.index)
    f = fr["f"]

    fig, axes = plt.subplots(2, 1, figsize=(11, 6.8), sharex=True, height_ratios=[1.3, 1])
    axes[0].plot(x, fr["cum_f"], color=POS_COLOR, linewidth=1.8, label="Sum of monthly f")
    axes[0].fill_between(x, 0, fr["cum_f"], color=POS_COLOR, alpha=0.12)
    if show_compound:
        axes[0].plot(x, fr["cum_f_compound"], color=POS_COLOR, linewidth=1.2,
                     linestyle="--", alpha=0.7, label="Compounded")
        axes[0].legend(loc="upper left")
    axes[0].axhline(0, color="grey", linewidth=0.8)
    axes[0].set_ylabel("Cumulative factor return")
    axes[0].set_title(f"{factor}: cumulative factor return (per 1 SD of exposure)")
    _pct(axes[0])

    colors = np.where(f.to_numpy() >= 0, POS_COLOR, NEG_COLOR)
    axes[1].bar(x, f.to_numpy(), width=22, color=colors, alpha=0.45, linewidth=0)
    axes[1].plot(x, f.rolling(ma_window, min_periods=ma_window // 2).mean(),
                 color="#111111", linewidth=1.5, label=f"{ma_window}M moving average")
    axes[1].axhline(f.mean(), color=NEG_COLOR, linestyle="--", linewidth=1.1,
                    label=f"Mean = {f.mean() * 100:.2f}% / month")
    axes[1].axhline(0, color="grey", linewidth=0.8)
    axes[1].set_ylabel("Monthly factor return")
    axes[1].legend(ncol=2, loc="upper left")
    _pct(axes[1], decimals=1)
    fig.tight_layout()
    return fig


def savefig(fig, name: str, outdir=None):
    """図を output/figures に保存してパスを返す."""
    from .config import OUTPUT_DIR

    outdir = outdir or (OUTPUT_DIR / "figures")
    path = outdir / f"{name}.png"          # name は "subdir/name" 形式も可
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    return path


# --- 中立化バリアントの比較 -----------------------------------------------------

VARIANT_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860"]


def plot_variant_quantile_bar(
    summaries: dict[str, pd.DataFrame],
    q: int = 5,
    factor: str = "",
    weighting: str = "ew",
    value_col: str = "ann_excess",
    labels: dict[str, str] | None = None,
):
    """中立化バリアント別の分位年率超過リターンをグループ棒グラフで比較."""
    qlabels = [f"Q{i}" for i in range(1, q + 1)]
    spread = f"{qlabels[-1]}-{qlabels[0]}"
    bars = qlabels + [spread]
    variants = list(summaries)
    labels = labels or {v: v for v in variants}
    vlabel = VALUE_LABEL.get(value_col, value_col)

    n = len(variants)
    width = 0.8 / n
    x = np.arange(len(bars))

    fig, ax = plt.subplots(figsize=(11.5, 5.2))
    for j, v in enumerate(variants):
        vals = summaries[v].loc[bars, value_col].astype(float).to_numpy()
        off = (j - (n - 1) / 2) * width
        ax.bar(x + off, vals, width=width * 0.92, label=labels[v],
               color=VARIANT_COLORS[j % len(VARIANT_COLORS)], edgecolor="white", linewidth=0.5)
    ax.axhline(0, color="#333333", linewidth=0.9)
    ax.axvline(len(qlabels) - 0.5, color="#BBBBBB", linewidth=0.9, linestyle=":")
    ax.set_xticks(x, bars)
    ax.set_ylabel(vlabel)
    ax.set_title(f"{factor}: {vlabel.lower()} by quintile — {WEIGHT_LABEL[weighting]}")
    ax.legend(ncol=n, loc="upper left")
    _pct(ax)
    ax.margins(y=0.16)
    fig.tight_layout()
    return fig


def plot_ic_decay_compare(
    decays: dict[str, pd.DataFrame],
    factor: str = "",
    labels: dict[str, str] | None = None,
    tstat_line: float = 2.0,
    tstat_col: str = "t_stat_NW",
):
    """中立化バリアント別の IC 減衰と t 値を折れ線で比較."""
    variants = list(decays)
    labels = labels or {v: v for v in variants}
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 4.4))
    for j, v in enumerate(variants):
        d = decays[v]
        c = VARIANT_COLORS[j % len(VARIANT_COLORS)]
        axes[0].plot(d.index, d["mean_IC"], marker="o", markersize=4.5, linewidth=1.6, color=c, label=labels[v])
        axes[1].plot(d.index, d[tstat_col], marker="o", markersize=4.5, linewidth=1.6, color=c, label=labels[v])
    for ax in axes:
        ax.axhline(0, color="#333333", linewidth=0.9)
        ax.set_xlabel("Horizon h (single-month return, h months ahead)")
        ax.set_xticks(list(decays[variants[0]].index))
    axes[0].set_ylabel("Mean IC")
    axes[0].set_title("IC decay")
    axes[0].legend()
    for y in (tstat_line, -tstat_line):
        axes[1].axhline(y, color=NEG_COLOR, linestyle="--", linewidth=1.0)
    axes[1].set_ylabel("t-stat (Newey-West)" if tstat_col == "t_stat_NW" else "t-stat")
    axes[1].set_title(f"Significance (dashed = ±{tstat_line:g})")
    fig.suptitle(f"{factor}: IC decay by neutralization", fontweight="bold")
    fig.tight_layout()
    return fig


def plot_cumulative_ic_compare(
    ics: dict[str, pd.Series],
    factor: str = "",
    labels: dict[str, str] | None = None,
    ma_window: int = 12,
):
    """中立化バリアント別の累積 IC と IC 移動平均を比較."""
    variants = list(ics)
    labels = labels or {v: v for v in variants}
    fig, axes = plt.subplots(2, 1, figsize=(11, 7.0), sharex=True, height_ratios=[1.3, 1])
    for j, v in enumerate(variants):
        ic = ics[v]
        x = to_datetime_index(ic.index)
        c = VARIANT_COLORS[j % len(VARIANT_COLORS)]
        axes[0].plot(x, ic.fillna(0).cumsum(), color=c, linewidth=1.6,
                     label=f"{labels[v]} (mean={ic.mean():.3f})")
        axes[1].plot(x, ic.rolling(ma_window, min_periods=ma_window // 2).mean(), color=c, linewidth=1.4)
    for ax in axes:
        ax.axhline(0, color="grey", linewidth=0.8)
    axes[0].set_ylabel("Cumulative IC")
    axes[0].set_title(f"{factor}: cumulative IC by neutralization")
    axes[0].legend(loc="upper left")
    axes[1].set_ylabel(f"IC — {ma_window}M moving average")
    axes[1].set_title(f"{ma_window}-month moving average of monthly IC")
    fig.tight_layout()
    return fig


def plot_factor_return_compare(
    frs: dict[str, pd.DataFrame],
    factor: str = "",
    labels: dict[str, str] | None = None,
    ma_window: int = 12,
    title_suffix: str = "",
):
    """複数系列（中立化バリアント / 回帰ウェイト）の累積ファクターリターンを比較."""
    keys = list(frs)
    labels = labels or {k: k for k in keys}
    fig, axes = plt.subplots(2, 1, figsize=(11, 7.0), sharex=True, height_ratios=[1.4, 1])
    for j, k in enumerate(keys):
        fr = frs[k]
        x = to_datetime_index(fr.index)
        c = VARIANT_COLORS[j % len(VARIANT_COLORS)]
        m = fr["f"].mean()
        axes[0].plot(x, fr["cum_f"], color=c, linewidth=1.7,
                     label=f"{labels[k]} ({m * 100:.2f}%/m)")
        axes[1].plot(x, fr["f"].rolling(ma_window, min_periods=ma_window // 2).mean(),
                     color=c, linewidth=1.4)
    for ax in axes:
        ax.axhline(0, color="grey", linewidth=0.8)
    axes[0].set_ylabel("Cumulative factor return (sum)")
    axes[0].set_title(f"{factor}: cumulative factor return{title_suffix}")
    axes[0].legend(loc="upper left")
    _pct(axes[0])
    axes[1].set_ylabel(f"Monthly f — {ma_window}M MA")
    axes[1].set_title(f"{ma_window}-month moving average of the monthly factor return")
    _pct(axes[1], decimals=1)
    fig.tight_layout()
    return fig


def plot_factor_return_diagnostics(fr: pd.DataFrame, factor: str = ""):
    """回帰の診断: 月次 t 値と決定係数の推移."""
    x = to_datetime_index(fr.index)
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.8), sharex=True)
    colors = np.where(fr["t_stat"].to_numpy() >= 0, POS_COLOR, NEG_COLOR)
    axes[0].bar(x, fr["t_stat"].to_numpy(), width=22, color=colors, alpha=0.5, linewidth=0)
    for y in (2, -2):
        axes[0].axhline(y, color=NEG_COLOR, linestyle="--", linewidth=1.0)
    axes[0].axhline(0, color="grey", linewidth=0.8)
    axes[0].set_ylabel("t-stat of $f_t$")
    axes[0].set_title(f"{factor}: monthly cross-sectional regression diagnostics "
                      f"(|t|>2 in {fr['t_stat'].abs().gt(2).mean() * 100:.0f}% of months)")

    axes[1].plot(x, fr["r2"], color=POS_COLOR, linewidth=1.3)
    axes[1].axhline(fr["r2"].mean(), color=NEG_COLOR, linestyle="--", linewidth=1.1,
                    label=f"Mean $R^2$ = {fr['r2'].mean():.3f}")
    axes[1].set_ylabel("$R^2$")
    axes[1].set_title("Cross-sectional $R^2$ (a single factor explains only a small share)")
    axes[1].legend(loc="upper left")
    fig.tight_layout()
    return fig


# --- 分位ポートフォリオのリスク・回転率ダッシュボード -----------------------------

DASHBOARD_PANELS = [
    ("ann_excess", "Annualized excess return", True, "{:+.1f}%"),
    ("te", "Tracking error", True, "{:.1f}%"),
    ("IR", "Information ratio", False, "{:.2f}"),
    ("max_dd_excess", "Max drawdown of cumulative excess", True, "{:.0f}%"),
    ("ann_vol", "Annualized volatility", True, "{:.0f}%"),
    ("turnover_ann", "Annualized one-way turnover", True, "{:.0f}%"),
]


def plot_quantile_dashboard(
    summary: pd.DataFrame,
    q: int = 5,
    factor: str = "",
    weighting: str = "ew",
    panels: list | None = None,
    bench_ref: bool = True,
    include_na: bool = False,
):
    """分位別のリターン・TE・IR・最大DD・ボラ・回転率を一覧する."""
    panels = panels or DASHBOARD_PANELS
    qlabels = [f"Q{i}" for i in range(1, q + 1)]
    spread = f"{qlabels[-1]}-{qlabels[0]}"
    bars = qlabels + [spread]
    colors = quantile_colors(q) + [SPREAD_COLOR]
    if include_na and "NA" in summary.index:
        bars = bars + ["NA"]
        colors = colors + [NA_COLOR]

    ncol = 3
    nrow = int(np.ceil(len(panels) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.3 * ncol, 3.3 * nrow))
    axes = np.atleast_1d(axes).ravel()

    for ax, (col, title, is_pct, fmt) in zip(axes, panels):
        vals = summary.reindex(bars)[col].astype(float)
        ax.bar(range(len(bars)), vals.to_numpy(), color=colors, edgecolor="white", linewidth=0.7, width=0.7)
        ax.axhline(0, color="#333333", linewidth=0.9)
        if bench_ref and "bench" in summary.index and col in summary.columns:
            bv = summary.loc["bench", col]
            if pd.notna(bv):
                ax.axhline(float(bv), color=NEG_COLOR, linestyle="--", linewidth=1.0,
                           label=f"Benchmark = {fmt.format(float(bv) * (100 if is_pct else 1))}")
                ax.legend(fontsize=7.5, loc="best")
        ax.set_xticks(range(len(bars)), bars, fontsize=8)
        ax.set_title(title, fontsize=10)
        if is_pct:
            _pct(ax)
        _bar_labels(ax, range(len(bars)), vals.to_numpy(), value_fmt=fmt,
                    scale=100.0 if is_pct else 1.0, fontsize=7.5)
        ax.margins(y=0.22)

    for ax in axes[len(panels):]:
        ax.set_visible(False)
    fig.suptitle(f"{factor}: quintile portfolio risk & turnover — {WEIGHT_LABEL[weighting]}",
                 fontweight="bold")
    fig.tight_layout()
    return fig


# --- 多ファクター比較のヒートマップ ---------------------------------------------

GROUP_CMAP = "tab10"


def _group_edges(groups: pd.Series) -> tuple[list[int], list[str], list[float]]:
    """連続するグループの境界位置・ラベル・中心位置を返す."""
    g = groups.to_numpy()
    edges = [0] + [i for i in range(1, len(g)) if g[i] != g[i - 1]] + [len(g)]
    labels = [g[edges[i]] for i in range(len(edges) - 1)]
    centers = [(edges[i] + edges[i + 1]) / 2 - 0.5 for i in range(len(edges) - 1)]
    return edges, labels, centers


def plot_factor_matrix(
    off_diagonal: pd.DataFrame,
    diagonal: pd.Series | None = None,
    groups: pd.Series | None = None,
    title: str = "",
    off_label: str = "Rank correlation",
    diag_label: str = "Annualized excess return",
    off_cmap: str = "RdBu_r",
    diag_cmap: str = "PiYG",
    off_vmin: float | None = None,
    off_vmax: float | None = None,
    diag_vmax: float | None = None,
    annotate: bool | None = None,
    off_fmt: str = "{:.2f}",
    diag_fmt: str = "{:+.1%}",
    figsize: tuple[float, float] | None = None,
    band: float = 0.35,
):
    """非対角＝相関/重複、対角＝パフォーマンス の二重スケール・ヒートマップ.

    ``groups`` を渡すと core.yaml のスタイル階層が色帯と区切り線で表示される。
    """
    labels = list(off_diagonal.index)
    K = len(labels)
    M = off_diagonal.to_numpy(dtype=float).copy()
    D = None
    if diagonal is not None:
        D = diagonal.reindex(labels).to_numpy(dtype=float)
        np.fill_diagonal(M, np.nan)

    if annotate is None:
        annotate = K <= 20
    if figsize is None:
        side = max(5.5, min(0.42 * K + 3.0, 26))
        figsize = (side + 2.0, side)

    fig, ax = plt.subplots(figsize=figsize)
    if off_vmin is None or off_vmax is None:
        lim = float(np.nanmax(np.abs(M))) if np.isfinite(M).any() else 1.0
        off_vmin = -lim if off_vmin is None else off_vmin
        off_vmax = lim if off_vmax is None else off_vmax
    im_off = ax.imshow(M, cmap=off_cmap, vmin=off_vmin, vmax=off_vmax)

    im_diag = None
    if D is not None:
        Dm = np.full((K, K), np.nan)
        np.fill_diagonal(Dm, D)
        dv = diag_vmax if diag_vmax is not None else (
            float(np.nanmax(np.abs(D))) if np.isfinite(D).any() else 1.0)
        im_diag = ax.imshow(Dm, cmap=diag_cmap, vmin=-dv, vmax=dv)

    ax.set_xticks(range(K), labels, rotation=90, fontsize=max(4.0, min(9, 260 / K)))
    ax.set_yticks(range(K), labels, fontsize=max(4.0, min(9, 260 / K)))
    ax.set_xticks(np.arange(K + 1) - 0.5, minor=True)
    ax.set_yticks(np.arange(K + 1) - 0.5, minor=True)
    ax.grid(which="minor", color="white", linewidth=0.4)
    ax.grid(which="major", visible=False)
    ax.tick_params(which="minor", length=0)

    if annotate:
        for i in range(K):
            for j in range(K):
                if i == j and D is not None:
                    v, fmt = D[i], diag_fmt
                else:
                    v, fmt = M[i, j], off_fmt
                if np.isfinite(v):
                    ax.text(j, i, fmt.format(v), ha="center", va="center",
                            fontsize=max(5.0, min(8, 150 / K)),
                            fontweight="bold" if i == j else "normal")

    if groups is not None:
        g = groups.reindex(labels)
        edges, glabels, centers = _group_edges(g)
        cmap = plt.get_cmap(GROUP_CMAP)
        uniq = list(dict.fromkeys(glabels))
        cmap_of = {s: cmap(i % 10) for i, s in enumerate(uniq)}
        for e in edges[1:-1]:
            ax.axhline(e - 0.5, color="#222222", linewidth=1.4)
            ax.axvline(e - 0.5, color="#222222", linewidth=1.4)
        for i in range(len(glabels)):
            a, b = edges[i] - 0.5, edges[i + 1] - 0.5
            c = cmap_of[glabels[i]]
            ax.add_patch(plt.Rectangle((-band - 0.5, a), band, b - a, color=c,
                                       clip_on=False, transform=ax.transData))
            ax.add_patch(plt.Rectangle((a, -band - 0.5), b - a, band, color=c,
                                       clip_on=False, transform=ax.transData))
            ax.text(-band - 0.9, centers[i], glabels[i], ha="right", va="center",
                    fontsize=8.5, fontweight="bold", color=c)

    ax.set_title(title, fontweight="bold", pad=14)
    cb1 = fig.colorbar(im_off, ax=ax, fraction=0.032, pad=0.02)
    cb1.set_label(off_label, fontsize=9)
    if im_diag is not None:
        cb2 = fig.colorbar(im_diag, ax=ax, fraction=0.032, pad=0.03)
        cb2.set_label(f"{diag_label} (diagonal)", fontsize=9)
    fig.tight_layout()
    return fig


def plot_scoreboard_bars(
    scoreboard: pd.DataFrame,
    value_col: str = "ls_ann_excess",
    tstat_col: str | None = "ls_t_NW",
    group_col: str = "style",
    title: str = "",
    ylabel: str = "Annualized excess return",
    is_pct: bool = True,
    sort_within_group: bool = True,
    figsize: tuple[float, float] | None = None,
):
    """スタイルでグループ化した縦棒チャート（x = ファクター、y = リターン）."""
    sb = scoreboard.copy()
    if sort_within_group and group_col in sb.columns:
        sb = sb.sort_values([group_col, value_col], ascending=[True, False], kind="stable")
        sb = pd.concat([sb[sb[group_col] == g] for g in scoreboard[group_col].unique()])
    labels = list(sb.index)
    K = len(labels)
    vals = sb[value_col].astype(float).to_numpy()
    figsize = figsize or (max(9.5, 0.13 * K + 2.2), 5.4)

    cmap = plt.get_cmap(GROUP_CMAP)
    uniq = list(dict.fromkeys(sb[group_col])) if group_col in sb.columns else ["all"]
    cmap_of = {s: cmap(i % 10) for i, s in enumerate(uniq)}
    colors = [cmap_of[s] for s in sb[group_col]] if group_col in sb.columns else [POS_COLOR] * K

    fig, ax = plt.subplots(figsize=figsize)
    ax.bar(range(K), vals, color=colors, edgecolor="white", linewidth=0.4, width=0.8)
    ax.axhline(0, color="#333333", linewidth=0.9)
    ax.set_xticks(range(K), labels, rotation=90, fontsize=max(5.0, min(9, 340 / K)))
    ax.set_xlim(-0.8, K - 0.2)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontweight="bold")
    if is_pct:
        _pct(ax)

    if group_col in sb.columns:
        edges, _, _ = _group_edges(sb[group_col])
        for e in edges[1:-1]:
            ax.axvline(e - 0.5, color="#BBBBBB", linewidth=0.9, linestyle=":")
        handles = [plt.Rectangle((0, 0), 1, 1, color=cmap_of[s]) for s in uniq]
        ax.legend(handles, uniq, fontsize=7.5, ncol=1, loc="center left",
                  bbox_to_anchor=(1.005, 0.5), borderaxespad=0)

    if tstat_col and tstat_col in sb.columns:
        for i, (v, t) in enumerate(zip(vals, sb[tstat_col].astype(float))):
            if np.isfinite(t) and abs(t) >= 2:
                ax.annotate("*", xy=(i, v), xytext=(0, 1 if v >= 0 else -1),
                            textcoords="offset points", ha="center",
                            va="bottom" if v >= 0 else "top",
                            fontsize=10, fontweight="bold", color="#333333")
        ax.text(0.005, 0.97, "* = NW |t| ≥ 2", transform=ax.transAxes,
                ha="left", va="top", fontsize=7.5, color="#777777")
    ax.margins(y=0.12)
    fig.tight_layout()
    return fig
