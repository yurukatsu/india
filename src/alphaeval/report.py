"""アルファ横断の定型レポート（系列・累積図・年率棒グラフ・指標表）。

1 本のスコアについて、リターン種別（例: ``drtn`` = 配当込みトータルリターン、``srtn`` = 固有リターン）ごとに

- 最上位分位（既定 ``Q5``）の等ウェイト超過（対ベンチマーク）
- 最上位 − 最下位（``Q5 − Q1``）
- 最上位分位の時価総額ウェイト超過（対ベンチマーク）
- 税控除後の最上位分位の等ウェイト超過（税・コストは :func:`alphaeval.tax.simulate_after_tax`）
- IC（1 期先）

の月次系列を計算し、累積図（``drtn`` は複利累積、``srtn`` は累和）、アルファ横断の年率棒グラフ、指標表を作る。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from alphaeval.ic import ic_summary, information_coefficient
from alphaeval.metrics import annualized_volatility
from alphaeval.panel import forward_returns
from alphaeval.quantile import portfolio_returns, quantile_analysis
from alphaeval.tax import TaxSchedule, TaxSimulationResult, simulate_after_tax

#: 指標キー → 表示名（``{top}`` ``{bottom}`` は分位ラベルで置換）
METRIC_LABELS: dict[str, str] = {
    "ew_excess": "{top} EW 超過 (vs BM)",
    "spread": "{top} − {bottom}",
    "cw_excess": "{top} CW 超過 (vs BM)",
    "after_tax_excess": "税控除後 {top} EW 超過 (vs BM)",
    "ic": "IC",
}

#: 累積の取り方。``compound`` = Π(1+r) − 1、``sum`` = 累和
DEFAULT_CUMULATION: dict[str, str] = {"drtn": "compound", "srtn": "sum"}


@dataclass
class AlphaReport:
    """1 本のスコアのレポート用系列。

    Attributes:
        name (str): スコア名。
        series (dict[str, dict[str, pd.Series]]): 指標キー → リターン種別 → 月次系列（バーンイン後）。
        quantile_returns (dict[str, pd.DataFrame]): リターン種別 → 分位別リターン（等ウェイト、``spread`` 列含む）。
        turnover (pd.Series): 最上位分位（等ウェイト）の片道回転率。
        turnover_cw (pd.Series): 最上位分位（時価総額ウェイト）の片道回転率。
        tax (TaxSimulationResult | None): 税シミュレーション（``tax_kind`` のリターンで実行）。
        top (str): 最上位分位のラベル。
        bottom (str): 最下位分位のラベル。
        tax_kind (str): 税シミュレーションに使ったリターン種別。
        cumulation (dict[str, str]): リターン種別 → 累積の取り方。
    """

    name: str
    series: dict[str, dict[str, pd.Series]]
    quantile_returns: dict[str, pd.DataFrame]
    turnover: pd.Series
    turnover_cw: pd.Series
    tax: TaxSimulationResult | None
    top: str
    bottom: str
    tax_kind: str
    cumulation: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CUMULATION))

    @property
    def kinds(self) -> list[str]:
        """リターン種別のリスト。"""
        return list(self.quantile_returns)

    def label(self, metric: str) -> str:
        """指標の表示名を返す。"""
        return METRIC_LABELS[metric].format(top=self.top, bottom=self.bottom)


def cumulate(series: pd.Series, how: str) -> pd.Series:
    """月次系列を累積する。

    Args:
        series (pd.Series): 月次系列。
        how (str): ``"compound"``（Π(1+r) − 1）または ``"sum"``（累和）。

    Returns:
        pd.Series: 累積系列（NaN は 0 として扱う）。

    Examples:
        >>> s = pd.Series([0.1, 0.1])
        >>> cumulate(s, "compound").round(3).tolist(), cumulate(s, "sum").round(3).tolist()
        ([0.1, 0.21], [0.1, 0.2])
    """
    s = series.fillna(0.0)
    if how == "compound":
        return (1.0 + s).cumprod() - 1.0
    if how == "sum":
        return s.cumsum()
    raise ValueError("how は 'compound' または 'sum' を指定してください")


def compute_alpha_report(
    name: str,
    score: pd.DataFrame,
    universe: pd.DataFrame,
    benchmark: pd.DataFrame,
    returns: Mapping[str, pd.DataFrame],
    n_quantiles: int = 5,
    buffer: float = 0.0,
    cw_max_weight: float | None = 0.05,
    tax_schedule: TaxSchedule | None = None,
    cost_buy: float = 0.0,
    cost_sell: float = 0.0,
    burn_in: int = 12,
    higher_is_better: bool = True,
    tax_kind: str | None = None,
    cumulation: Mapping[str, str] | None = None,
    q1_is_top: bool = False,
) -> AlphaReport:
    """1 本のスコアのレポート用系列を計算する。

    Args:
        name (str): スコア名。
        score (pd.DataFrame): 日付 × 銘柄のスコア。
        universe (pd.DataFrame): ユニバースウェイト（時価総額ウェイトの基礎にも使う）。
        benchmark (pd.DataFrame): ベンチマークウェイト。
        returns (Mapping[str, pd.DataFrame]): リターン種別 → 日付 × 銘柄のリターン（例: ``{"drtn": rtn, "srtn": srtn}``）。
        n_quantiles (int): 分位数。
        buffer (float): バッファ幅（等ウェイト分位に適用）。
        cw_max_weight (float | None): 時価総額ウェイト版の 1 銘柄上限。
        tax_schedule (TaxSchedule | None): 指定時は税控除後系列を計算する（``None`` ならグロスと同じ）。
        cost_buy (float): 買付コスト率。
        cost_sell (float): 売却コスト率。
        burn_in (int): 先頭から除外する月数。
        higher_is_better (bool): スコアが高いほど良いか。
        tax_kind (str | None): 税シミュレーションに使うリターン種別。``None`` なら ``returns`` の最初のキー。
        cumulation (Mapping[str, str] | None): リターン種別 → 累積の取り方。既定は ``drtn`` 複利、``srtn`` 累和、
            その他は複利。
        q1_is_top (bool): ``True`` なら ``Q1`` を最上位にする。

    Returns:
        AlphaReport: レポート用系列。

    Examples:
        >>> rep = compute_alpha_report("x", score, univ, bm, {"drtn": rtn, "srtn": srtn},
        ...                            tax_schedule=india_tax_schedule())  # doctest: +SKIP
        >>> rep.series["ew_excess"]["drtn"].mean() * 12  # doctest: +SKIP
    """
    kinds = list(returns)
    tax_kind = tax_kind or kinds[0]
    cum = {
        k: (cumulation or {}).get(k, DEFAULT_CUMULATION.get(k, "compound"))
        for k in kinds
    }
    series: dict[str, dict[str, pd.Series]] = {m: {} for m in METRIC_LABELS}
    q_returns: dict[str, pd.DataFrame] = {}
    top = bottom = ""
    turnover = turnover_cw = pd.Series(dtype=float)
    ew_weights = None
    for kind in kinds:
        r = returns[kind]
        q_ew = quantile_analysis(
            score,
            r,
            universe,
            n_quantiles,
            buffer,
            "equal",
            higher_is_better=higher_is_better,
            q1_is_top=q1_is_top,
        )
        q_cw = quantile_analysis(
            score,
            r,
            universe,
            n_quantiles,
            buffer,
            "size",
            size=universe,
            max_weight=cw_max_weight,
            higher_is_better=higher_is_better,
            q1_is_top=q1_is_top,
        )
        top, bottom = q_ew.top, q_ew.bottom
        bm_ret = portfolio_returns(benchmark, r)
        ret = q_ew.returns
        series["ew_excess"][kind] = (ret[top] - bm_ret.reindex(ret.index)).iloc[
            burn_in:
        ]
        series["spread"][kind] = ret["spread"].iloc[burn_in:]
        series["cw_excess"][kind] = (
            q_cw.returns[top] - bm_ret.reindex(q_cw.returns.index)
        ).iloc[burn_in:]
        fwd = forward_returns(r, 1)
        series["ic"][kind] = information_coefficient(
            score if higher_is_better else -score, fwd, universe
        ).iloc[burn_in:]
        q_returns[kind] = ret.iloc[burn_in:]
        if kind == tax_kind:
            turnover = q_ew.turnover[top].iloc[burn_in:]
            turnover_cw = q_cw.turnover[top].iloc[burn_in:]
            ew_weights = q_ew.weights[top]
    tax = None
    if tax_schedule is not None and ew_weights is not None:
        tax = simulate_after_tax(
            ew_weights,
            returns[tax_kind],
            tax_schedule,
            cost_buy=cost_buy,
            cost_sell=cost_sell,
        )
        drag = (tax.monthly["r_gross"] - tax.monthly["r_net"]).iloc[1:]
        for kind in kinds:
            gross = series["ew_excess"][kind]
            series["after_tax_excess"][kind] = gross - drag.reindex(gross.index).fillna(
                0.0
            )
    else:
        for kind in kinds:
            series["after_tax_excess"][kind] = series["ew_excess"][kind].copy()
    return AlphaReport(
        name=name,
        series=series,
        quantile_returns=q_returns,
        turnover=turnover,
        turnover_cw=turnover_cw,
        tax=tax,
        top=top,
        bottom=bottom,
        tax_kind=tax_kind,
        cumulation=cum,
    )


def _annualize(metric: str, s: pd.Series) -> float:
    """指標の年率値（IC は平均）。"""
    s = s.dropna()
    if s.empty:
        return np.nan
    return float(s.mean()) if metric == "ic" else float(s.mean()) * 12


def summary_table(
    reports: Mapping[str, AlphaReport], periods_per_year: int = 12
) -> pd.DataFrame:
    """アルファ横断の指標表を作る。

    Args:
        reports (Mapping[str, AlphaReport]): スコア名 → レポート。
        periods_per_year (int): 年率換算の期間数。

    Returns:
        pd.DataFrame: 行 = スコア、列 = ``(リターン種別, 指標)`` の MultiIndex。指標は
        ``{top} EW 超過`` / ``IR`` / ``{top}−{bottom}`` / ``R/R`` / ``{top} CW 超過`` / ``CW IR`` /
        ``税後 EW 超過`` / ``税後 IR`` / ``IC`` / ``ICIR`` / ``IC t`` と、種別によらない
        ``回転率 EW`` / ``回転率 CW`` / ``コストドラッグ`` / ``税ドラッグ`` / ``短期比率``。

    Examples:
        >>> summary_table({"a": rep_a, "b": rep_b}).round(3)  # doctest: +SKIP
    """
    rows: dict[str, dict[tuple[str, str], float]] = {}
    for name, rep in reports.items():
        row: dict[tuple[str, str], float] = {}
        for kind in rep.kinds:
            for metric in ("ew_excess", "spread", "cw_excess", "after_tax_excess"):
                s = rep.series[metric][kind]
                ann = _annualize(metric, s)
                vol = annualized_volatility(s, periods_per_year)
                row[(kind, rep.label(metric))] = ann
                row[(kind, rep.label(metric) + " IR")] = (
                    ann / vol if vol and vol > 0 else np.nan
                )
            ic = ic_summary(rep.series["ic"][kind], periods_per_year)
            row[(kind, "IC")] = ic["mean"]
            row[(kind, "ICIR")] = ic["icir"]
            row[(kind, "IC t")] = ic["t_stat"]
        row[("共通", "回転率 EW")] = float(rep.turnover.mean()) * periods_per_year
        row[("共通", "回転率 CW")] = float(rep.turnover_cw.mean()) * periods_per_year
        if rep.tax is not None:
            m = rep.tax.monthly.iloc[1:]
            row[("共通", "コストドラッグ")] = (
                float((m["r_gross"] - m["r_net_tc"]).mean()) * periods_per_year
            )
            row[("共通", "税ドラッグ")] = (
                float((m["r_net_tc"] - m["r_net"]).mean()) * periods_per_year
            )
            pos_s, pos_l = (
                m["gain_short"].clip(lower=0).sum(),
                m["gain_long"].clip(lower=0).sum(),
            )
            row[("共通", "短期比率")] = (
                pos_s / (pos_s + pos_l) if pos_s + pos_l > 0 else np.nan
            )
        rows[name] = row
    df = pd.DataFrame(rows).T
    df.columns = pd.MultiIndex.from_tuples(df.columns, names=["リターン", "指標"])
    return df


def plot_cumulative(
    reports: Mapping[str, AlphaReport],
    metrics: Sequence[str] = (
        "ew_excess",
        "spread",
        "cw_excess",
        "after_tax_excess",
        "ic",
    ),
    ic_window: int = 36,
    figsize_per_row: float = 3.2,
    highlight: Sequence[str] = (),
) -> plt.Figure:
    """指標 × リターン種別の累積図を描く（IC は累和と移動平均の 2 段）。

    Args:
        reports (Mapping[str, AlphaReport]): スコア名 → レポート（同じリターン種別を持つこと）。
        metrics (Sequence[str]): 描く指標キー。
        ic_window (int): IC 移動平均の窓（月）。
        figsize_per_row (float): 1 段あたりの高さ（インチ）。
        highlight (Sequence[str]): 太線にするスコア名。

    Returns:
        plt.Figure: 図。

    Examples:
        >>> fig = plot_cumulative({"a": rep_a, "b": rep_b})  # doctest: +SKIP
    """
    first = next(iter(reports.values()))
    kinds = first.kinds
    rows = [(m, "cum") for m in metrics if m != "ic"] + (
        [("ic", "cum"), ("ic", "ma")] if "ic" in metrics else []
    )
    fig, axes = plt.subplots(
        len(rows),
        len(kinds),
        figsize=(6.5 * len(kinds), figsize_per_row * len(rows)),
        squeeze=False,
    )
    for i, (metric, mode) in enumerate(rows):
        for j, kind in enumerate(kinds):
            ax = axes[i, j]
            for name, rep in reports.items():
                s = rep.series[metric][kind]
                if metric == "ic":
                    y = (
                        s.cumsum()
                        if mode == "cum"
                        else s.rolling(
                            ic_window, min_periods=max(6, ic_window // 2)
                        ).mean()
                    )
                else:
                    y = cumulate(s, rep.cumulation[kind])
                ax.plot(
                    y.index, y, label=name, linewidth=2.2 if name in highlight else 1.0
                )
            ax.axhline(0, color="k", linewidth=0.8)
            how = "累積" if first.cumulation.get(kind) == "compound" else "累和"
            if metric == "ic":
                title = f"IC {'累和' if mode == 'cum' else f'{ic_window} か月移動平均'}（{kind}）"
            else:
                title = f"{first.label(metric)}（{kind}、{how}）"
            ax.set_title(title, fontsize=10)
            ax.grid(alpha=0.3)
    axes[0, 0].legend(fontsize=7, ncol=2)
    fig.tight_layout()
    return fig


def plot_cross_alpha_bars(
    reports: Mapping[str, AlphaReport],
    metrics: Sequence[str] = (
        "ew_excess",
        "spread",
        "cw_excess",
        "after_tax_excess",
        "ic",
    ),
    figsize_per_row: float = 3.0,
) -> plt.Figure:
    """アルファ横断の棒グラフ（年率、IC は平均）を指標 × リターン種別で描く。

    Args:
        reports (Mapping[str, AlphaReport]): スコア名 → レポート。
        metrics (Sequence[str]): 描く指標キー。
        figsize_per_row (float): 1 段あたりの高さ（インチ）。

    Returns:
        plt.Figure: 図。

    Examples:
        >>> fig = plot_cross_alpha_bars({"a": rep_a, "b": rep_b})  # doctest: +SKIP
    """
    first = next(iter(reports.values()))
    kinds = first.kinds
    names = list(reports)
    fig, axes = plt.subplots(
        len(metrics),
        len(kinds),
        figsize=(
            max(6.5, 0.6 * len(names) + 3) * len(kinds),
            figsize_per_row * len(metrics),
        ),
        squeeze=False,
    )
    x = np.arange(len(names))
    for i, metric in enumerate(metrics):
        for j, kind in enumerate(kinds):
            ax = axes[i, j]
            vals = [_annualize(metric, reports[n].series[metric][kind]) for n in names]
            colors = ["C0" if v >= 0 else "C3" for v in vals]
            ax.bar(x, vals, color=colors)
            ax.axhline(0, color="k", linewidth=0.8)
            ax.set_xticks(x, names, rotation=30, ha="right", fontsize=8)
            ax.set_title(
                f"{first.label(metric)}（{kind}、{'平均' if metric == 'ic' else '年率'}）",
                fontsize=10,
            )
            ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    return fig
