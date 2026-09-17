"""bax 出力のパフォーマンス評価。

:class:`alphaeval.bax.reader.BaxOutput` を入力に、

- 月次パフォーマンス表（BM / ファンド / 超過（コスト前後）/ コスト / 回転率 / 推定 TE / 保有数）
- 要約指標（年率リターン、実現 TE、IR、推定 TE との比較、コストドラッグ、回転率、勝率、最大 DD）
- 年別表
- 特性値（アクティブエクスポージャー）の統計と制約への張り付き
- 保有の統計（銘柄数、アクティブシェア、上位集中、上下限への張り付き）
- 銘柄別・グループ別（業種など）の超過リターン寄与
- アルファの実現度（アルファスコアと翌月リターンの順位相関）
- 税控除後評価（:func:`alphaeval.tax.simulate_after_tax` による FIFO ロット台帳）

を計算する。市場固有の情報（税率、業種マップ、外部リターン）はすべて引数で渡す。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from alphaeval.bax.reader import BaxOutput
from alphaeval.metrics import annualized_return, annualized_volatility, max_drawdown
from alphaeval.tax import TaxSchedule, TaxSimulationResult, simulate_after_tax


# ---------------------------------------------------------------------------
# 月次表・要約
# ---------------------------------------------------------------------------
def performance_table(out: BaxOutput) -> pd.DataFrame:
    """月次パフォーマンス表を作る。

    Args:
        out (BaxOutput): bax 出力。

    Returns:
        pd.DataFrame: 月末 ``DatetimeIndex`` × 以下の列（小数）。

            - ``bm``: ベンチマークリターン
            - ``fund``: ファンドリターン（取引コスト控除後）
            - ``excess``: 超過リターン（取引コスト控除後）= ``fund − bm``
            - ``excess_gross``: 取引コスト控除前の超過リターン = ``excess + tr_cost``
            - ``tr_cost``: 取引コスト（NAV 比）
            - ``turnover``: 片道回転率
            - ``omega``: 最適化時点の推定 TE（年率、前月末の値をその月に対応付け）
            - ``alpha_score``: ポートフォリオのアルファスコア（前月末）
            - ``n_hold``: 保有銘柄数（ウェイト > 0.01%、前月末）
            - ``active_share``: アクティブシェア（前月末、``me`` の ``prev_awgt`` から）
    """
    perf = out.performance()
    obj = out.objective()
    awgt, _ = out.monthly_panels()
    n_hold = out.holdings_panel("wgt_o").gt(1e-4).sum(axis=1)
    df = pd.DataFrame(
        {
            "bm": perf["bm_rtn"],
            "fund": perf["fund_rtn"],
            "excess": perf["fund_ex_rtn"],
            "excess_gross": perf["fund_ex_rtn"] + perf["tr_cost"].fillna(0.0),
            "tr_cost": perf["tr_cost"],
            "turnover": perf["rot"],
        }
    )
    # 推定 TE・アルファ・保有数はリバランス日の値。翌月のリターン行に対応付ける
    df["omega"] = obj["omega"].shift(1).reindex(df.index)
    df["alpha_score"] = obj["alpha"].shift(1).reindex(df.index)
    df["n_hold"] = n_hold.shift(1).reindex(df.index)
    df["active_share"] = (awgt.abs().sum(axis=1) / 2).reindex(df.index)
    return df


def summarize(table: pd.DataFrame, periods_per_year: int = 12) -> pd.Series:
    """月次表から要約指標を計算する。

    Args:
        table (pd.DataFrame): :func:`performance_table` の戻り値（期間で絞ってよい）。
        periods_per_year (int): 年率換算の期間数。

    Returns:
        pd.Series: 年率リターン（BM / ファンド）、超過（算術・幾何、コスト前後）、実現 TE、IR、推定 TE 平均、
        コストドラッグ、年率回転率、勝率、最大 DD（超過の累積）、平均保有数、平均アクティブシェア、月数。

    Examples:
        >>> idx = pd.date_range("2020-01-31", periods=3, freq="M")
        >>> t = pd.DataFrame({"bm": [0.01, 0.0, -0.01], "fund": [0.02, 0.0, -0.01], "excess": [0.01, 0.0, 0.0],
        ...                   "excess_gross": [0.011, 0.001, 0.001], "tr_cost": [0.001] * 3, "turnover": [0.1] * 3,
        ...                   "omega": [0.05] * 3, "alpha_score": [1.0] * 3, "n_hold": [80] * 3, "active_share": [0.4] * 3}, index=idx)
        >>> round(summarize(t)["turnover_annual"], 3)
        1.2
    """
    t = table.dropna(subset=["fund", "bm"])
    ex = t["excess"]
    te = annualized_volatility(ex, periods_per_year)
    ex_ann = float(ex.mean()) * periods_per_year
    return pd.Series(
        {
            "ann_return_fund": annualized_return(t["fund"], periods_per_year),
            "ann_return_bm": annualized_return(t["bm"], periods_per_year),
            "active_return": ex_ann,
            "active_return_geo": annualized_return(t["fund"], periods_per_year)
            - annualized_return(t["bm"], periods_per_year),
            "active_return_gross": float(t["excess_gross"].mean()) * periods_per_year,
            "tracking_error": te,
            "information_ratio": ex_ann / te if te and te > 0 else np.nan,
            "omega_mean": float(t["omega"].mean()),
            "te_ratio_realized_to_omega": te / float(t["omega"].mean())
            if t["omega"].notna().any()
            else np.nan,
            "cost_drag": float(t["tr_cost"].mean()) * periods_per_year,
            "turnover_annual": float(t["turnover"].mean()) * periods_per_year,
            "hit_rate": float((ex > 0).mean()),
            "max_drawdown_excess": max_drawdown(ex),
            "ann_volatility_fund": annualized_volatility(t["fund"], periods_per_year),
            "beta": float(t["fund"].cov(t["bm"]) / t["bm"].var(ddof=1))
            if len(t) > 2
            else np.nan,
            "n_hold_mean": float(t["n_hold"].mean()),
            "active_share_mean": float(t["active_share"].mean()),
            "n_periods": float(len(t)),
        }
    )


def yearly_table(table: pd.DataFrame) -> pd.DataFrame:
    """年別の累積リターンと回転率・コストの表を返す。

    Args:
        table (pd.DataFrame): :func:`performance_table` の戻り値。

    Returns:
        pd.DataFrame: 年 × ``bm`` ``fund`` ``excess``（複利）、``excess_arith``、``tr_cost``（合計）、
        ``turnover``（合計）、``te``（年率）、``n_months``。
    """
    t = table.dropna(subset=["fund", "bm"])
    g = t.groupby(t.index.year)
    comp = lambda s: (1 + s).prod() - 1
    return pd.DataFrame(
        {
            "bm": g["bm"].apply(comp),
            "fund": g["fund"].apply(comp),
            "excess": g["fund"].apply(comp) - g["bm"].apply(comp),
            "excess_arith": g["excess"].sum(),
            "tr_cost": g["tr_cost"].sum(),
            "turnover": g["turnover"].sum(),
            "te": g["excess"].std() * np.sqrt(12),
            "n_months": g["excess"].size(),
        }
    )


def rolling_tracking_error(table: pd.DataFrame, window: int = 36) -> pd.DataFrame:
    """実現 TE（ローリング）と推定 TE（``omega``）を並べる。

    Args:
        table (pd.DataFrame): :func:`performance_table` の戻り値。
        window (int): ローリング窓（月）。

    Returns:
        pd.DataFrame: ``realized_te`` ``omega`` ``ratio`` 列。
    """
    te = table["excess"].rolling(
        window, min_periods=max(12, window // 2)
    ).std() * np.sqrt(12)
    return pd.DataFrame(
        {"realized_te": te, "omega": table["omega"], "ratio": te / table["omega"]}
    )


# ---------------------------------------------------------------------------
# 特性値・保有
# ---------------------------------------------------------------------------
def exposure_summary(
    out: BaxOutput, group: str = "risk_index", bounds: tuple[float, float] | None = None
) -> pd.DataFrame:
    """アクティブエクスポージャーの統計を返す。

    Args:
        out (BaxOutput): bax 出力。
        group (str): ``risk_index`` / ``industry`` / ``country`` / ``currency``。
        bounds (tuple[float, float] | None): 制約の下限・上限（例: リスクインデックス ``(-2, 2)``、
            業種 ``(-0.03, 0.03)``）。指定時は上下限に張り付いた月の割合を出す。

    Returns:
        pd.DataFrame: 特性値 × ``mean`` ``std`` ``min`` ``max`` ``abs_mean`` ``at_lower`` ``at_upper``。
    """
    panel = out.exposure_panel("opt_active", group)
    df = pd.DataFrame(
        {
            "mean": panel.mean(),
            "std": panel.std(),
            "min": panel.min(),
            "max": panel.max(),
            "abs_mean": panel.abs().mean(),
        }
    )
    if bounds is not None:
        lo, hi = bounds
        tol = 1e-6
        df["at_lower"] = (panel <= lo + tol).mean()
        df["at_upper"] = (panel >= hi - tol).mean()
    return df.sort_values("abs_mean", ascending=False)


def holdings_summary(out: BaxOutput) -> pd.DataFrame:
    """リバランス日ごとの保有統計を返す。

    Args:
        out (BaxOutput): bax 出力。

    Returns:
        pd.DataFrame: 日付 × ``n_hold``（> 0.01%）、``n_bm_in_file``、``max_weight``、``top10_weight``、
        ``active_share_file``（``O`` に載る銘柄だけで計算した参考値）、``n_at_upper`` ``n_at_lower``
        （個別上下限に張り付いた銘柄数）、``sum_alpha_weighted``（ウェイト加重アルファ）。
    """
    rows = {}
    for d in out.rebalance_dates:
        h = out.holdings(d)
        w = h["wgt_o"]
        held = w > 1e-4
        tol = 1e-6
        rows[d] = {
            "n_hold": int(held.sum()),
            "n_bm_in_file": int((h["wgt_b"] > 0).sum()),
            "max_weight": float(w.max()),
            "top10_weight": float(w.nlargest(10).sum()),
            "active_share_file": float((w - h["wgt_b"]).abs().sum() / 2),
            "n_at_upper": int(((h["cs_wgt_u"] - w).abs() <= tol).sum()),
            "n_at_lower": int(
                (held & ((w - h["cs_wgt_l"]).abs() <= tol) & (h["cs_wgt_l"] > 0)).sum()
            ),
            "alpha_weighted": float((w * h["alpha"]).sum() / w.sum())
            if w.sum() > 0
            else np.nan,
            "alpha_bm_weighted": float(
                (h["wgt_b"] * h["alpha"]).sum() / h["wgt_b"].sum()
            )
            if h["wgt_b"].sum() > 0
            else np.nan,
        }
    return pd.DataFrame(rows).T.sort_index()


# ---------------------------------------------------------------------------
# 寄与分析
# ---------------------------------------------------------------------------
def stock_contribution(
    out: BaxOutput, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None
) -> pd.DataFrame:
    """銘柄別の超過リターン寄与（``Σ_t prev_awgt × rtn``、取引コスト控除前）を返す。

    Args:
        out (BaxOutput): bax 出力。
        start (pd.Timestamp | None): 集計開始（含む）。
        end (pd.Timestamp | None): 集計終了（含む）。

    Returns:
        pd.DataFrame: 銘柄 × ``contribution``（累積寄与）、``months_active``、``mean_active_weight``、
        ``mean_return``。寄与の降順。
    """
    awgt, rtn = out.monthly_panels()
    awgt, rtn = awgt.loc[start:end], rtn.loc[start:end]
    contrib = (awgt * rtn).sum(axis=0, min_count=1)
    return (
        pd.DataFrame(
            {
                "contribution": contrib,
                "months_active": (awgt.abs() > 1e-6).sum(),
                "mean_active_weight": awgt.mean(),
                "mean_return": rtn.mean(),
            }
        )
        .dropna(subset=["contribution"])
        .sort_values("contribution", ascending=False)
    )


def group_contribution(
    out: BaxOutput,
    mapping: pd.Series | pd.DataFrame,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """グループ（業種など）別の超過リターン寄与を返す。

    Args:
        out (BaxOutput): bax 出力。
        mapping (pd.Series | pd.DataFrame): 銘柄 → グループ。``Series``（固定マップ）または
            日付 × 銘柄の ``DataFrame``（時点依存マップ。前月末の値を使う）。
        start (pd.Timestamp | None): 集計開始。
        end (pd.Timestamp | None): 集計終了。

    Returns:
        pd.DataFrame: グループ × ``contribution``（累積、年率換算なし）、``contribution_annual``、
        ``mean_active_weight``（グループのアクティブウェイト合計の平均）、``te_contribution``
        （月次寄与の標準偏差 × √12）。
    """
    awgt, rtn = out.monthly_panels()
    awgt, rtn = awgt.loc[start:end], rtn.loc[start:end]
    prod = awgt * rtn
    if isinstance(mapping, pd.Series):
        grp = pd.DataFrame(
            [mapping.reindex(prod.columns).to_numpy()] * len(prod),
            index=prod.index,
            columns=prod.columns,
        )
    else:
        grp = mapping.shift(1).reindex(index=prod.index, columns=prod.columns)
    monthly = {}
    weights = {}
    for g in pd.unique(grp.stack().dropna()):
        m = grp == g
        monthly[g] = prod.where(m).sum(axis=1)
        weights[g] = awgt.where(m).sum(axis=1)
    monthly = pd.DataFrame(monthly)
    weights = pd.DataFrame(weights)
    n_years = len(monthly) / 12
    return pd.DataFrame(
        {
            "contribution": monthly.sum(),
            "contribution_annual": monthly.sum() / n_years if n_years > 0 else np.nan,
            "mean_active_weight": weights.mean(),
            "te_contribution": monthly.std() * np.sqrt(12),
        }
    ).sort_values("contribution", ascending=False)


def alpha_realization(out: BaxOutput, method: str = "spearman") -> pd.Series:
    """最適化に使ったアルファスコアと翌月リターンの相関（``O`` に載る銘柄のみ）を月次で返す。

    ``O`` ファイルは保有銘柄と BM 保有銘柄しか含まないため、ユニバース全体の IC ではなく
    「最適化が選んだ集合の中で順位が当たっているか」の目安になる。

    Args:
        out (BaxOutput): bax 出力。
        method (str): ``spearman`` または ``pearson``。

    Returns:
        pd.Series: リターン月をインデックスとする相関係数。
    """
    _, rtn = out.monthly_panels()
    alpha = out.holdings_panel("alpha")
    dates = out.rebalance_dates
    vals = {}
    for i, d in enumerate(dates):
        nxt = (
            rtn.index[rtn.index.searchsorted(d, side="right")]
            if rtn.index.searchsorted(d, side="right") < len(rtn.index)
            else None
        )
        if nxt is None:
            continue
        a = alpha.loc[d].dropna()
        r = rtn.loc[nxt].reindex(a.index)
        m = r.notna()
        if m.sum() < 10:
            continue
        x, y = a[m], r[m]
        if method == "spearman":
            x, y = x.rank(), y.rank()
        vals[nxt] = float(x.corr(y))
    return pd.Series(vals, name=f"alpha_ic_{method}")


# ---------------------------------------------------------------------------
# 税
# ---------------------------------------------------------------------------
def after_tax(
    out: BaxOutput,
    schedule: TaxSchedule,
    returns: pd.DataFrame | None = None,
    cost_buy: float = 0.0,
    cost_sell: float = 0.0,
    burn_in: int = 12,
) -> tuple[TaxSimulationResult, pd.DataFrame]:
    """最適化後ウェイト（``wgt_o``）を FIFO ロット台帳で評価し、税控除後の月次表を返す。

    bax の ``fund_rtn`` はすでに取引コスト控除後なので、既定ではシミュレーション側の売買コストを 0 にし、
    税額だけを bax のリターンから差し引く。

    Args:
        out (BaxOutput): bax 出力。
        schedule (TaxSchedule): 税率スケジュール。
        returns (pd.DataFrame | None): 価格指数の元になる銘柄リターン（日付 × 銘柄、小数）。
            ``None`` なら ``me`` ファイルのリターン（bax のニューメレール建て）を使う。
            現地通貨建ての課税を再現するには現地通貨リターンを渡す。
        cost_buy (float): 買付コスト率（bax の取引コストと二重計上しないため既定 0）。
        cost_sell (float): 売却コスト率。
        burn_in (int): 表から除外する先頭月数（初期化バイアス対策）。

    Returns:
        tuple[TaxSimulationResult, pd.DataFrame]: ``(シミュレーション結果, 月次表)``。月次表の列は
        ``fund``（bax、コスト後）、``tax``（NAV 比）、``fund_after_tax = fund − tax``、``bm``、
        ``excess_after_tax``、``turnover_sim``（シミュレーション側の回転率）、``short_ratio`` 用の
        ``gain_short`` ``gain_long``。
    """
    weights = out.holdings_panel("wgt_o").fillna(0.0)
    if returns is None:
        _, returns = out.monthly_panels()
    sim = simulate_after_tax(
        weights, returns, schedule, cost_buy=cost_buy, cost_sell=cost_sell
    )
    perf = out.performance()
    m = sim.monthly
    table = pd.DataFrame(
        {
            "fund": perf["fund_rtn"].reindex(m.index),
            "bm": perf["bm_rtn"].reindex(m.index),
            "tax": m["tax"] / m["value"].shift(1),
            "turnover_sim": m["turnover"],
            "gain_short": m["gain_short"],
            "gain_long": m["gain_long"],
            "r_gross_sim": m["r_gross"],
        }
    )
    table["fund_after_tax"] = table["fund"] - table["tax"]
    table["excess_after_tax"] = table["fund_after_tax"] - table["bm"]
    return sim, table.iloc[burn_in:]


# ---------------------------------------------------------------------------
# 一括評価
# ---------------------------------------------------------------------------
@dataclass
class BaxEvaluation:
    """:func:`evaluate_bax` の結果。

    Attributes:
        table (pd.DataFrame): 月次パフォーマンス表。
        summary (pd.Series): 要約指標。
        yearly (pd.DataFrame): 年別表。
        rolling_te (pd.DataFrame): 実現 TE と推定 TE。
        exposures (dict[str, pd.DataFrame]): グループ別のアクティブエクスポージャー統計。
        holdings (pd.DataFrame): 保有統計。
        contributions (pd.DataFrame): 銘柄別寄与。
        group_contributions (pd.DataFrame | None): グループ別寄与（``mapping`` 指定時）。
        alpha_ic (pd.Series): アルファ実現度。
        tax (TaxSimulationResult | None): 税シミュレーション。
        tax_table (pd.DataFrame | None): 税控除後の月次表。
        params (dict[str, str]): 実行パラメータ。
    """

    table: pd.DataFrame
    summary: pd.Series
    yearly: pd.DataFrame
    rolling_te: pd.DataFrame
    exposures: dict[str, pd.DataFrame]
    holdings: pd.DataFrame
    contributions: pd.DataFrame
    group_contributions: pd.DataFrame | None
    alpha_ic: pd.Series
    tax: TaxSimulationResult | None = None
    tax_table: pd.DataFrame | None = None
    params: dict[str, str] = field(default_factory=dict)


def evaluate_bax(
    out: BaxOutput,
    mapping: pd.Series | pd.DataFrame | None = None,
    tax_schedule: TaxSchedule | None = None,
    tax_returns: pd.DataFrame | None = None,
    exposure_bounds: dict[str, tuple[float, float]] | None = None,
    te_window: int = 36,
    burn_in: int = 12,
) -> BaxEvaluation:
    """bax 出力を一通り評価する。

    Args:
        out (BaxOutput): bax 出力。
        mapping (pd.Series | pd.DataFrame | None): 銘柄 → グループ（業種など）。
        tax_schedule (TaxSchedule | None): 指定時は税控除後評価を行う。
        tax_returns (pd.DataFrame | None): 税評価に使う銘柄リターン（``None`` なら ``me`` のリターン）。
        exposure_bounds (dict[str, tuple[float, float]] | None): グループ → 制約の下限・上限。
            既定は ``bc.txt`` の ``cs_rskidx_default`` / ``cs_industry_default`` / ``cs_country_default`` /
            ``cs_currency_default`` から読む。
        te_window (int): 実現 TE のローリング窓。
        burn_in (int): 税評価のバーンイン月数。

    Returns:
        BaxEvaluation: 評価結果。

    Examples:
        >>> from alphaeval.bax import BaxOutput, evaluate_bax  # doctest: +SKIP
        >>> res = evaluate_bax(BaxOutput("bax/output/v0"))  # doctest: +SKIP
        >>> res.summary  # doctest: +SKIP
    """
    table = performance_table(out)
    bounds = exposure_bounds or _bounds_from_params(out.params)
    exposures = {
        g: exposure_summary(out, g, bounds.get(g))
        for g in ("risk_index", "industry", "country", "currency")
    }
    tax = tax_table = None
    if tax_schedule is not None:
        tax, tax_table = after_tax(out, tax_schedule, tax_returns, burn_in=burn_in)
    return BaxEvaluation(
        table=table,
        summary=summarize(table),
        yearly=yearly_table(table),
        rolling_te=rolling_tracking_error(table, te_window),
        exposures=exposures,
        holdings=holdings_summary(out),
        contributions=stock_contribution(out),
        group_contributions=group_contribution(out, mapping)
        if mapping is not None
        else None,
        alpha_ic=alpha_realization(out),
        tax=tax,
        tax_table=tax_table,
        params=out.params,
    )


def _bounds_from_params(params: dict[str, str]) -> dict[str, tuple[float, float]]:
    """``bc.txt`` の既定制約からグループ別の上下限を読む。"""
    keys = {
        "risk_index": "cs_rskidx_default",
        "industry": "cs_industry_default",
        "country": "cs_country_default",
        "currency": "cs_currency_default",
    }
    out: dict[str, tuple[float, float]] = {}
    for g, k in keys.items():
        v = params.get(k)
        if v and "," in v:
            try:
                lo, hi = (float(x) for x in v.split(","))
                out[g] = (lo, hi)
            except ValueError:
                continue
    return out


# ---------------------------------------------------------------------------
# リスクモデルによる分解（Style / Industry / Country / Currency / Market / Specific）
# ---------------------------------------------------------------------------
#: ``factor_list.csv`` の ``group`` → 表示名
FACTOR_GROUP_LABELS: dict[str, str] = {
    "Risk Indices": "Style",
    "Industries": "Industry",
    "Countries": "Country",
    "Currencies": "Currency",
    "Market": "Market",
}
_ATTR_PREFIX = re.compile(r"^[RICc]\d{2}_")


def _factor_maps(factor_list: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """``factor_list`` から ``symbol → id`` と ``id → グループ表示名`` を作る。"""
    fl = factor_list.copy()
    fl["symbol"] = fl["symbol"].astype(str).str.strip()
    sym_to_id = fl.set_index("symbol")["id"]
    id_to_group = fl.set_index("id")["group"].map(
        lambda g: FACTOR_GROUP_LABELS.get(str(g).strip(), str(g))
    )
    return sym_to_id, id_to_group


def active_factor_exposures(out: BaxOutput, factor_list: pd.DataFrame) -> pd.DataFrame:
    """``A`` ファイルの ``opt_active`` をファクター ID の列に揃えたパネルを返す。

    行名の接頭辞（``R01_`` ``I01_`` ``C39_`` ``c39_``）を除いた記号を ``factor_list`` の ``symbol`` に対応付ける。
    対応しない行（``sum_pos_w`` ``fbeta_*`` 等）は除く。

    Args:
        out (BaxOutput): bax 出力。
        factor_list (pd.DataFrame): ``risk_models/{model}/factor_list.csv``（``id`` ``symbol`` ``group`` 列）。

    Returns:
        pd.DataFrame: リバランス日 × ファクター ID のアクティブエクスポージャー。
    """
    sym_to_id, _ = _factor_maps(factor_list)
    panel = out.exposure_panel("opt_active")
    symbols = panel.columns.str.replace(_ATTR_PREFIX, "", regex=True)
    keep = symbols.isin(sym_to_id.index)
    panel = panel.loc[:, keep]
    panel.columns = [sym_to_id[s] for s in symbols[keep]]
    return panel.sort_index(axis=1)


def factor_attribution(
    out: BaxOutput, factor_returns: pd.DataFrame, factor_list: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """超過リターン（取引コスト控除前）をファクター寄与と固有寄与に分解する。

    リバランス日 ``t`` のアクティブエクスポージャー :math:`x_{k,t}` と翌月のファクターリターン
    :math:`f_{k,t+1}` から、寄与 :math:`x_{k,t} f_{k,t+1}` をグループ（Style / Industry / Country /
    Currency / Market）で合計し、残差を Specific とする。

    Args:
        out (BaxOutput): bax 出力。
        factor_returns (pd.DataFrame): 日付 × ファクター ID のファクターリターン（小数）。
            :meth:`alphaeval.io.InputStore.factor_return` の戻り値。
        factor_list (pd.DataFrame): ``factor_list.csv``。

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: ``(グループ別月次表, ファクター別月次寄与)``。
        グループ別表はリターン月をインデックスとし、各グループ、``factor_total``、``specific``、
        ``excess_gross`` の列を持つ。

    Examples:
        >>> groups, per_factor = factor_attribution(out, store.factor_return("GEMLT"), store.factor_list("GEMLT"))  # doctest: +SKIP
        >>> groups.mean() * 12  # doctest: +SKIP
    """
    _, id_to_group = _factor_maps(factor_list)
    exposures = active_factor_exposures(out, factor_list)
    perf = performance_table(out)
    fr = factor_returns.reindex(columns=exposures.columns)
    rows, per_factor = {}, {}
    for t in exposures.index:
        pos = fr.index.searchsorted(t, side="right")
        if pos >= len(fr.index):
            continue
        nxt = fr.index[pos]
        contrib = exposures.loc[t] * fr.loc[nxt]
        per_factor[nxt] = contrib
        grp = contrib.groupby(id_to_group.reindex(contrib.index).to_numpy()).sum()
        row = grp.to_dict()
        row["factor_total"] = float(contrib.sum())
        row["excess_gross"] = perf["excess_gross"].get(nxt, np.nan)
        row["specific"] = row["excess_gross"] - row["factor_total"]
        rows[nxt] = row
    groups = pd.DataFrame(rows).T.sort_index()
    order = [g for g in FACTOR_GROUP_LABELS.values() if g in groups.columns] + [
        "factor_total",
        "specific",
        "excess_gross",
    ]
    return groups[order].astype(float), pd.DataFrame(per_factor).T.sort_index()


def risk_decomposition(
    out: BaxOutput,
    covariance: Callable[[pd.Timestamp], pd.DataFrame],
    factor_list: pd.DataFrame,
    cov_scale: float = 1e-4,
) -> pd.DataFrame:
    """推定アクティブリスク（``omega``）をファクターグループと固有リスクに分解する。

    :math:`\\sigma^2_{\\text{factor}} = x' F x` をグループ :math:`G` ごとの寄与
    :math:`x_G' (F x)_G`（相互項を含む）に分け、:math:`\\sigma^2_{\\text{specific}} = \\omega^2 - x'Fx` とする。

    Args:
        out (BaxOutput): bax 出力。
        covariance (Callable[[pd.Timestamp], pd.DataFrame]): 日付 → ファクター共分散行列（ID × ID）。
            例: ``lambda d: store.factor_covariance("GEMLT", d)``。
        factor_list (pd.DataFrame): ``factor_list.csv``。
        cov_scale (float): 共分散を小数²（年率）に直す係数。BARRA の %² なら ``1e-4``。

    Returns:
        pd.DataFrame: リバランス日 × 列。分散寄与 ``var_{Style,Industry,...}``、``var_factor``、
        ``var_specific``、``var_total``（= ``omega²``）、標準偏差 ``omega``、``te_factor``、``te_specific``
        （それぞれ分散の平方根）、分散シェア ``share_*``。``var_specific`` が負になる（``omega`` の
        定義と整合しない）場合は 0 に丸め、``share_*`` は総分散に対する比。

    Examples:
        >>> rd = risk_decomposition(out, lambda d: store.factor_covariance("GEMLT", d), store.factor_list("GEMLT"))  # doctest: +SKIP
        >>> rd[["omega", "te_factor", "te_specific"]].mean()  # doctest: +SKIP
    """
    _, id_to_group = _factor_maps(factor_list)
    exposures = active_factor_exposures(out, factor_list)
    omega = out.objective()["omega"]
    rows = {}
    for t in exposures.index:
        x = exposures.loc[t].fillna(0.0)
        F = covariance(t)
        ids = [i for i in x.index if i in F.index]
        x = x.reindex(ids)
        Fm = F.reindex(index=ids, columns=ids).fillna(0.0).to_numpy() * cov_scale
        fx = Fm @ x.to_numpy()
        contrib = pd.Series(x.to_numpy() * fx, index=ids)
        grp = contrib.groupby(id_to_group.reindex(ids).to_numpy()).sum()
        var_factor = float(contrib.sum())
        var_total = float(omega.get(t, np.nan) ** 2)
        var_specific = (
            max(var_total - var_factor, 0.0) if np.isfinite(var_total) else np.nan
        )
        row = {f"var_{g}": v for g, v in grp.items()}
        row.update(
            {
                "var_factor": var_factor,
                "var_specific": var_specific,
                "var_total": var_total,
                "omega": np.sqrt(var_total) if np.isfinite(var_total) else np.nan,
                "te_factor": np.sqrt(var_factor),
                "te_specific": np.sqrt(var_specific)
                if np.isfinite(var_specific)
                else np.nan,
            }
        )
        for g in grp.index:
            row[f"share_{g}"] = grp[g] / var_total if var_total > 0 else np.nan
        row["share_specific"] = var_specific / var_total if var_total > 0 else np.nan
        rows[t] = row
    return pd.DataFrame(rows).T.sort_index().astype(float)
