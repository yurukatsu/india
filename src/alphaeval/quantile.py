"""分位ポートフォリオ分析（通常分位・バッファ付き分位・ウェイト・キャップ・回転率）。

記号は ``docs/india_tcg.md`` §3 に従う。

- :math:`p_{i,t}`: ユニバース内の順位パーセンタイル。本実装では中央順位
  :math:`(\\mathrm{rank} - 0.5) / N \\in (0, 1)` を用いる（``rank`` は昇順、最上位が
  :math:`p \\to 1`）。これにより「上位 20%」が :math:`p \\ge 0.8` と厳密に対応する。
- 通常分位: :math:`I_{i,t} = 1[p_{i,t} \\ge \\theta]`。
- バッファ付き分位: :math:`p \\ge \\theta_{in}` で組入、:math:`\\theta_{out} \\le p < \\theta_{in}`
  では前期の状態を維持、:math:`p < \\theta_{out}` で除外。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from alphaeval.dates import ensure_datetime_index
from alphaeval.panel import mask_to_universe, next_dates, period_returns

WEIGHT_SCHEMES = ("equal", "size", "sqrt_size")


def rank_percentile(
    score: pd.DataFrame,
    universe: pd.DataFrame | None = None,
    higher_is_better: bool = True,
) -> pd.DataFrame:
    """スコアの中央順位パーセンタイル :math:`(\\mathrm{rank} - 0.5)/N` を返す。

    Args:
        score (pd.DataFrame): 日付 × 銘柄のスコア。
        universe (pd.DataFrame | None): ユニバース。指定時はユニバース内で順位付けする。
        higher_is_better (bool): ``True`` ならスコアが高いほど :math:`p` が大きい。

    Returns:
        pd.DataFrame: 日付 × 銘柄のパーセンタイル（(0, 1)、欠損は NaN）。

    Examples:
        >>> s = pd.DataFrame({"A": [3.0], "B": [1.0], "C": [2.0]}, index=[202001])
        >>> rank_percentile(s).round(3).iloc[0].to_dict()
        {'A': 0.833, 'B': 0.167, 'C': 0.5}
    """
    score = mask_to_universe(ensure_datetime_index(score), universe)
    ranks = score.rank(axis=1, ascending=higher_is_better, method="average")
    n = score.notna().sum(axis=1)
    return (ranks.sub(0.5)).div(n.where(n > 0), axis=0)


def quantile_bounds(n_quantiles: int, q: int) -> tuple[float, float]:
    """分位 ``q``（1 = 最上位）のパーセンタイル帯 ``[lower, upper)`` を返す。

    Args:
        n_quantiles (int): 分位数。
        q (int): 分位番号（1 が最上位、``n_quantiles`` が最下位）。

    Returns:
        tuple[float, float]: ``(lower, upper)``。最上位は ``upper = inf``、最下位は ``lower = -inf``。

    Examples:
        >>> quantile_bounds(5, 1)
        (0.8, inf)
        >>> quantile_bounds(5, 3)
        (0.4, 0.6)
    """
    if not 1 <= q <= n_quantiles:
        raise ValueError("q は 1 以上 n_quantiles 以下で指定してください")
    lower = 1.0 - q / n_quantiles
    upper = 1.0 - (q - 1) / n_quantiles
    if q == 1:
        upper = np.inf
    if q == n_quantiles:
        lower = -np.inf
    return lower, upper


def assign_quantiles(percentile: pd.DataFrame, n_quantiles: int) -> pd.DataFrame:
    """パーセンタイルから分位番号（1 = 最上位）を割り当てる。

    Args:
        percentile (pd.DataFrame): :func:`rank_percentile` の戻り値。
        n_quantiles (int): 分位数。

    Returns:
        pd.DataFrame: 日付 × 銘柄の分位番号（float、欠損は NaN）。

    Examples:
        >>> p = pd.DataFrame({"A": [0.9], "B": [0.1], "C": [0.5]}, index=[202001])
        >>> assign_quantiles(p, 5).iloc[0].to_dict()
        {'A': 1.0, 'B': 5.0, 'C': 3.0}
    """
    q = np.ceil((1.0 - percentile) * n_quantiles)
    q = q.clip(lower=1, upper=n_quantiles)
    return q.where(percentile.notna())


def membership(percentile: pd.DataFrame, lower: float, upper: float) -> pd.DataFrame:
    """通常分位の組入判定 ``lower <= p < upper`` を返す。

    Args:
        percentile (pd.DataFrame): パーセンタイル。
        lower (float): 下限（含む）。
        upper (float): 上限（含まない）。

    Returns:
        pd.DataFrame: 日付 × 銘柄の bool。

    Examples:
        >>> p = pd.DataFrame({"A": [0.9], "B": [0.1]}, index=[202001])
        >>> membership(p, 0.8, np.inf).iloc[0].to_dict()
        {'A': True, 'B': False}
    """
    return (percentile >= lower) & (percentile < upper)


def buffered_membership(
    percentile: pd.DataFrame,
    lower_in: float,
    upper_in: float = np.inf,
    buffer: float = 0.0,
    lower_out: float | None = None,
    upper_out: float | None = None,
    initial: pd.Series | None = None,
) -> pd.DataFrame:
    """バッファ付きの組入判定（前期の保有状態に依存）を逐次計算する。

    - 組入帯 ``[lower_in, upper_in)`` に入れば組入。
    - 維持帯 ``[lower_out, upper_out)`` に入れば前期の状態を維持。
    - それ以外（またはパーセンタイルが NaN）なら除外。

    ``lower_out`` / ``upper_out`` を省略すると ``lower_in - buffer`` / ``upper_in + buffer``。
    最上位分位（``docs/india_tcg.md`` §3.3）は ``lower_in = θ_in``, ``lower_out = θ_out``,
    ``upper_in = inf`` に対応する。

    Args:
        percentile (pd.DataFrame): 日付 × 銘柄のパーセンタイル（昇順に並んでいること）。
        lower_in (float): 組入帯の下限 :math:`\\theta_{in}`。
        upper_in (float): 組入帯の上限（含まない）。最上位分位なら ``inf``。
        buffer (float): 維持帯を組入帯から両側に広げる幅。
        lower_out (float | None): 維持帯の下限 :math:`\\theta_{out}`。
        upper_out (float | None): 維持帯の上限。
        initial (pd.Series | None): 初期（最初の日付の前）の保有状態。既定は全て非保有。

    Returns:
        pd.DataFrame: 日付 × 銘柄の bool。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29"])
        >>> p = pd.DataFrame({"A": [0.95, 0.90], "B": [0.85, 0.75], "C": [0.70, 0.85], "D": [0.85, 0.50]}, index=idx)
        >>> buffered_membership(p, 0.8, lower_out=0.6).iloc[1].to_dict()
        {'A': True, 'B': True, 'C': True, 'D': False}
        >>> membership(p, 0.8, np.inf).iloc[1].to_dict()
        {'A': True, 'B': False, 'C': True, 'D': False}
    """
    percentile = ensure_datetime_index(percentile)
    lo_out = lower_in - buffer if lower_out is None else lower_out
    hi_out = upper_in + buffer if upper_out is None else upper_out
    if lo_out > lower_in or hi_out < upper_in:
        raise ValueError("維持帯は組入帯を含む必要があります")
    p = percentile.to_numpy(dtype=float)
    enter = (p >= lower_in) & (p < upper_in)
    stay = (p >= lo_out) & (p < hi_out)
    held = np.zeros(p.shape[1], dtype=bool)
    if initial is not None:
        held = initial.reindex(percentile.columns).fillna(False).to_numpy(dtype=bool)
    out = np.zeros_like(enter)
    for i in range(p.shape[0]):
        held = enter[i] | (stay[i] & held)
        out[i] = held
    return pd.DataFrame(out, index=percentile.index, columns=percentile.columns)


def quantile_membership(
    percentile: pd.DataFrame, n_quantiles: int, q: int, buffer: float = 0.0
) -> pd.DataFrame:
    """分位 ``q`` の組入判定（``buffer > 0`` ならバッファ付き）を返す。

    Args:
        percentile (pd.DataFrame): パーセンタイル。
        n_quantiles (int): 分位数。
        q (int): 分位番号（1 = 最上位）。
        buffer (float): バッファ幅 :math:`\\theta_{in} - \\theta_{out}`。0 なら通常分位。

    Returns:
        pd.DataFrame: 日付 × 銘柄の bool。

    Examples:
        >>> p = pd.DataFrame({"A": [0.9, 0.7], "B": [0.1, 0.1]}, index=pd.to_datetime(["2020-01-31", "2020-02-29"]))
        >>> quantile_membership(p, 5, 1)["A"].tolist()
        [True, False]
        >>> quantile_membership(p, 5, 1, buffer=0.2)["A"].tolist()
        [True, True]
    """
    lower, upper = quantile_bounds(n_quantiles, q)
    if buffer <= 0:
        return membership(percentile, lower, upper)
    return buffered_membership(percentile, lower, upper, buffer=buffer)


def cap_weights(weights: pd.Series, cap: float, tol: float = 1e-12) -> pd.Series:
    """1 銘柄あたり上限 ``cap`` を課し、超過分を未達銘柄へ比例配分する（§3.5 の反復）。

    Args:
        weights (pd.Series): 合計 1 の基礎ウェイト（非負）。
        cap (float): 上限。``cap < 1/n`` の場合は ``1/n`` に緩める（等ウェイトになる）。
        tol (float): 収束判定の許容誤差。

    Returns:
        pd.Series: キャップ適用後のウェイト（合計 1）。

    Examples:
        >>> w = pd.Series({"A": 0.6, "B": 0.3, "C": 0.1})
        >>> cap_weights(w, 0.4).round(4).to_dict()
        {'A': 0.4, 'B': 0.4, 'C': 0.2}
        >>> cap_weights(w, 0.1).round(4).to_dict()
        {'A': 0.3333, 'B': 0.3333, 'C': 0.3333}
    """
    w = weights.dropna()
    w = w[w > 0]
    n = len(w)
    if n == 0:
        return weights * np.nan
    cap = max(cap, 1.0 / n)  # cap < 1/n は実現不能なので 1/n（等ウェイト）に緩める
    w = w / w.sum()
    capped = pd.Series(False, index=w.index)
    for _ in range(n + 1):
        over = (w > cap + tol) & ~capped
        if not over.any():
            break
        capped |= over
        w[capped] = cap
        residual = 1.0 - cap * capped.sum()
        free = ~capped
        base = weights.reindex(w.index)[free]
        w[free] = base / base.sum() * residual if base.sum() > 0 else 0.0
    return w.reindex(weights.index)


def weight_portfolio(
    member: pd.DataFrame,
    scheme: str = "equal",
    size: pd.DataFrame | None = None,
    max_weight: float | None = None,
) -> pd.DataFrame:
    """組入判定からウェイトパネルを作る（§3.4, §3.5）。

    Args:
        member (pd.DataFrame): 日付 × 銘柄の bool（組入判定）。
        scheme (str): ``"equal"`` / ``"size"`` / ``"sqrt_size"``。
        size (pd.DataFrame | None): 日付 × 銘柄の時価総額（``size`` / ``sqrt_size`` で必須）。
            ユニバースのウェイトなど、時価総額に比例する値で良い。
        max_weight (float | None): 分位内の 1 銘柄上限。``None`` なら制約なし。

    Returns:
        pd.DataFrame: 日付 × 銘柄のウェイト（非組入は NaN、各行の合計は 1）。

    Examples:
        >>> m = pd.DataFrame({"A": [True], "B": [True], "C": [False]}, index=[202001])
        >>> weight_portfolio(m).iloc[0].to_dict()
        {'A': 0.5, 'B': 0.5, 'C': nan}
    """
    if scheme not in WEIGHT_SCHEMES:
        raise ValueError(f"scheme は {WEIGHT_SCHEMES} のいずれかを指定してください")
    # NaN（列の不一致などで生じる）は非組入として扱う。astype(bool) は NaN を True にするので先に埋める
    member = ensure_datetime_index(member).fillna(False).astype(bool)
    if scheme == "equal":
        base = member.astype(float)
    else:
        if size is None:
            raise ValueError(f"scheme={scheme!r} には size が必要です")
        s = ensure_datetime_index(size).reindex(
            index=member.index, columns=member.columns
        )
        s = s.where(s > 0)
        base = (np.sqrt(s) if scheme == "sqrt_size" else s).where(member)
        missing = member & base.isna()
        if missing.to_numpy().any():
            # 時価総額が無い組入銘柄は、その日の組入銘柄の中央値で代替する
            med = base.median(axis=1)
            base = base.mask(missing, med, axis=0)
    base = base.where(member)
    weights = base.div(base.sum(axis=1).where(lambda x: x > 0), axis=0)
    if max_weight is not None:
        rows = {}
        for date, row in weights.iterrows():
            if row.notna().any():
                rows[date] = cap_weights(row, max_weight)
            else:
                rows[date] = row
        weights = pd.DataFrame(rows).T.reindex(
            index=weights.index, columns=weights.columns
        )
    return weights


def portfolio_returns(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    missing_return: float | None = 0.0,
    include_trailing: bool = True,
) -> pd.Series:
    """ウェイトパネルとリターンパネルからポートフォリオリターン系列を計算する。

    行 ``t_k`` の値は :math:`\\sum_i w_{i,t_{k-1}}\\, R_{i,(t_{k-1}, t_k]}`。
    ``t_k`` はウェイトの日付列（``include_trailing=True`` なら最後のウェイト日の
    次のリターン日を末尾に加える）。

    Args:
        weights (pd.DataFrame): 日付 × 銘柄のウェイト（非保有は NaN または 0）。
        returns (pd.DataFrame): 日付 × 銘柄の期間リターン（小数）。
        missing_return (float | None): 保有銘柄のリターンが欠損している場合の扱い。
            数値ならその値で補完（既定 0 = 価格据え置き）、``None`` なら残りの銘柄で再正規化。
        include_trailing (bool): 最後のウェイト日以降の 1 期間を含めるか。

    Returns:
        pd.Series: 日付をインデックスとするポートフォリオリターン（最初のウェイト日は含まない）。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29"])
        >>> w = pd.DataFrame({"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=idx)
        >>> r = pd.DataFrame({"A": [0.0, 0.1], "B": [0.0, -0.1]}, index=idx)
        >>> portfolio_returns(w, r).round(6).tolist()
        [0.0]
    """
    weights = ensure_datetime_index(weights)
    returns = ensure_datetime_index(returns)
    dates = weights.index
    if include_trailing:
        nxt = next_dates(dates[-1:], returns.index)
        if not pd.isna(nxt[0]) and nxt[0] not in dates:
            dates = dates.append(nxt)
    if len(dates) < 2:
        return pd.Series(dtype=float, name="portfolio")
    cols = sorted(set(weights.columns) | set(returns.columns))
    w = weights.reindex(index=dates[:-1], columns=cols).fillna(0.0)
    r = period_returns(returns.reindex(columns=cols), dates)
    held = w.to_numpy() != 0
    r_arr = r.to_numpy(dtype=float)
    w_arr = w.to_numpy(dtype=float)
    if missing_return is None:
        avail = held & ~np.isnan(r_arr)
        w_arr = np.where(avail, w_arr, 0.0)
        tot = w_arr.sum(axis=1, keepdims=True)
        w_arr = np.divide(w_arr, tot, out=np.zeros_like(w_arr), where=tot > 0)
        r_arr = np.nan_to_num(r_arr)
    else:
        r_arr = np.where(np.isnan(r_arr), missing_return, r_arr)
    port = (w_arr * r_arr).sum(axis=1)
    return pd.Series(port, index=dates[1:], name="portfolio")


def turnover(
    weights: pd.DataFrame,
    returns: pd.DataFrame | None = None,
    missing_return: float = 0.0,
) -> pd.Series:
    """片道回転率 :math:`\\mathrm{TO}_t = \\tfrac12 \\sum_i |w_{i,t} - w^{-}_{i,t}|` を返す。

    :math:`w^{-}_{i,t} = w_{i,t-1}(1 + r_{i,t}) / (1 + r^{p}_t)` はリターンでドリフトさせた
    リバランス前ウェイト。``returns=None`` ならドリフトなし（:math:`w^{-} = w_{t-1}`）。

    Args:
        weights (pd.DataFrame): 日付 × 銘柄のウェイト（非保有は NaN または 0）。
        returns (pd.DataFrame | None): 日付 × 銘柄の期間リターン。
        missing_return (float): 保有銘柄のリターン欠損時の補完値。

    Returns:
        pd.Series: 日付（2 番目以降）をインデックスとする回転率。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29"])
        >>> w = pd.DataFrame({"A": [1.0, 0.0], "B": [0.0, 1.0]}, index=idx)
        >>> turnover(w).tolist()
        [1.0]
    """
    weights = ensure_datetime_index(weights).fillna(0.0)
    dates = weights.index
    prev = weights.iloc[:-1].to_numpy(dtype=float)
    cur = weights.iloc[1:].to_numpy(dtype=float)
    if returns is not None:
        r = period_returns(
            ensure_datetime_index(returns).reindex(columns=weights.columns), dates
        )
        r_arr = np.where(
            np.isnan(r.to_numpy(dtype=float)), missing_return, r.to_numpy(dtype=float)
        )
        grown = prev * (1.0 + r_arr)
        tot = grown.sum(axis=1, keepdims=True)
        prev = np.divide(grown, tot, out=np.zeros_like(grown), where=tot > 0)
    to = 0.5 * np.abs(cur - prev).sum(axis=1)
    return pd.Series(to, index=dates[1:], name="turnover")


@dataclass
class QuantileResult:
    """分位分析の結果。

    Attributes:
        weights (dict[int, pd.DataFrame]): 分位番号 → ウェイトパネル。
        returns (pd.DataFrame): 日付 × 分位（列名 ``Q1``…）のリターン。
            ``spread`` 列（最上位 − 最下位）を含む。
        turnover (pd.DataFrame): 日付 × 分位の片道回転率。
        n_holdings (pd.DataFrame): 日付 × 分位の保有銘柄数。
        buffer (float): 使用したバッファ幅。
        n_quantiles (int): 分位数。
        scheme (str): ウェイト方式。
    """

    weights: dict[int, pd.DataFrame]
    returns: pd.DataFrame
    turnover: pd.DataFrame
    n_holdings: pd.DataFrame
    buffer: float = 0.0
    n_quantiles: int = 5
    scheme: str = "equal"
    labels: list[str] = field(default_factory=list)


def quantile_analysis(
    score: pd.DataFrame,
    returns: pd.DataFrame,
    universe: pd.DataFrame | None = None,
    n_quantiles: int = 5,
    buffer: float = 0.0,
    scheme: str = "equal",
    size: pd.DataFrame | None = None,
    max_weight: float | None = None,
    higher_is_better: bool = True,
    missing_return: float | None = 0.0,
) -> QuantileResult:
    """分位ポートフォリオを構築し、分位別リターン・回転率を計算する。

    Args:
        score (pd.DataFrame): 日付 × 銘柄のスコア。
        returns (pd.DataFrame): 日付 × 銘柄の期間リターン（小数）。
        universe (pd.DataFrame | None): ユニバース（非構成銘柄は NaN）。
        n_quantiles (int): 分位数。
        buffer (float): バッファ幅 :math:`\\theta_{in} - \\theta_{out}`（0 で通常分位）。
        scheme (str): ``"equal"`` / ``"size"`` / ``"sqrt_size"``。
        size (pd.DataFrame | None): 時価総額パネル。``None`` かつ size 系なら ``universe`` を使う。
        max_weight (float | None): 分位内 1 銘柄上限。
        higher_is_better (bool): スコアが高いほど良いか。
        missing_return (float | None): 保有銘柄のリターン欠損時の扱い（:func:`portfolio_returns`）。

    Returns:
        QuantileResult: 分位ごとのウェイト・リターン・回転率。

    Examples:
        >>> res = quantile_analysis(score, rtn, univ, n_quantiles=5, buffer=0.1)  # doctest: +SKIP
        >>> res.returns[["Q1", "Q5", "spread"]].mean() * 12  # doctest: +SKIP
    """
    if scheme != "equal" and size is None:
        if universe is None:
            raise ValueError("size 系のウェイトには size または universe が必要です")
        size = universe
    pct = rank_percentile(score, universe, higher_is_better)
    weights: dict[int, pd.DataFrame] = {}
    rets: dict[str, pd.Series] = {}
    tos: dict[str, pd.Series] = {}
    counts: dict[str, pd.Series] = {}
    labels = [f"Q{q}" for q in range(1, n_quantiles + 1)]
    for q, label in zip(range(1, n_quantiles + 1), labels):
        member = quantile_membership(pct, n_quantiles, q, buffer)
        w = weight_portfolio(member, scheme, size, max_weight)
        weights[q] = w
        rets[label] = portfolio_returns(w, returns, missing_return)
        tos[label] = turnover(w, returns)
        counts[label] = member.sum(axis=1)
    ret_df = pd.DataFrame(rets)
    ret_df["spread"] = ret_df[labels[0]] - ret_df[labels[-1]]
    return QuantileResult(
        weights=weights,
        returns=ret_df,
        turnover=pd.DataFrame(tos),
        n_holdings=pd.DataFrame(counts),
        buffer=buffer,
        n_quantiles=n_quantiles,
        scheme=scheme,
        labels=labels,
    )
