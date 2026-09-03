"""前処理: ウィンザライズ・標準化（z-score / Blom 正規スコア）.

すべて **時点 t のクロスセクションのみ** で完結する（docs/tutorial/02 のPIT規律）.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm


# --- 素の変換関数（1本の Series = 1時点のクロスセクションを想定） -----------------


def winsorize(s: pd.Series, p: float = 0.01) -> pd.Series:
    """分位点クリップ. p=0.01 なら下位1%・上位1%で打ち切り."""
    if s.notna().sum() == 0:
        return s
    lo, hi = s.quantile(p), s.quantile(1 - p)
    return s.clip(lo, hi)


def winsorize_mad(s: pd.Series, c: float = 5.0) -> pd.Series:
    """中央値絶対偏差（MAD）ベースのクリップ. med ± c * 1.4826 * MAD."""
    med = s.median()
    mad = (s - med).abs().median() * 1.4826
    if not np.isfinite(mad) or mad == 0:
        return s
    return s.clip(med - c * mad, med + c * mad)


def zscore(s: pd.Series) -> pd.Series:
    """クロスセクション z-score."""
    sd = s.std(ddof=1)
    if not np.isfinite(sd) or sd == 0:
        return s * 0.0
    return (s - s.mean()) / sd


def blom(s: pd.Series) -> pd.Series:
    r"""Blom の正規スコア変換.

    順位 :math:`r_i \in \{1, \dots, n\}`（欠損を除いた有効数 :math:`n`）に対し

    .. math:: z_i = \Phi^{-1}\!\left(\frac{r_i - 3/8}{n + 1/4}\right)

    裾が厚い分布・外れ値に頑健で、分布形を標準正規に固定する.
    同順位は平均順位（``method="average"``）で処理する.
    """
    valid = s.notna()
    n = int(valid.sum())
    out = pd.Series(np.nan, index=s.index, dtype=float)
    if n == 0:
        return out
    if n == 1:
        out[valid] = 0.0
        return out
    r = s[valid].rank(method="average")
    out[valid] = norm.ppf((r - 0.375) / (n + 0.25))
    return out


def rank_uniform(s: pd.Series) -> pd.Series:
    """[0, 1) 一様スコア（分位分割の下地・可視化用）."""
    valid = s.notna()
    n = int(valid.sum())
    out = pd.Series(np.nan, index=s.index, dtype=float)
    if n:
        out[valid] = (s[valid].rank(method="average") - 0.5) / n
    return out


TRANSFORMS = {"raw": lambda s: s, "zscore": zscore, "blom": blom, "rank": rank_uniform}


# --- 中立化 -------------------------------------------------------------------


def neutralize(s: pd.Series, groups: pd.Series | None = None, controls: pd.DataFrame | None = None) -> pd.Series:
    """グループダミー（業種等）・連続変数（サイズ等）に対する回帰残差を返す.

    残差は標準偏差1に再スケールする.
    """
    parts = []
    if groups is not None:
        parts.append(pd.get_dummies(groups.astype(str), dtype=float))
    if controls is not None:
        parts.append(controls.astype(float))
    if not parts:
        return s
    X = pd.concat(parts, axis=1)
    X = X.assign(_const=1.0)
    ok = s.notna() & X.notna().all(axis=1)
    out = pd.Series(np.nan, index=s.index, dtype=float)
    if ok.sum() <= X.shape[1]:
        return out
    beta, *_ = np.linalg.lstsq(X.loc[ok].to_numpy(), s.loc[ok].to_numpy(), rcond=None)
    resid = s.loc[ok].to_numpy() - X.loc[ok].to_numpy() @ beta
    sd = resid.std(ddof=1)
    out.loc[ok] = resid / sd if sd > 0 else resid
    return out


# --- パネル適用 ---------------------------------------------------------------


def standardize_panel(
    panel: pd.DataFrame,
    factors: str | list[str],
    method: str = "blom",
    winsor: float | None = None,
    winsor_mad: float | None = None,
    group_col: str | None = None,
    control_cols: list[str] | None = None,
    time_level: str = "yyyymm",
    suffix: str | None = None,
) -> pd.DataFrame:
    """パネルの各ファクターを毎月クロスセクションで標準化した列を追加する.

    パイプライン: winsorize -> transform(method) -> neutralize(任意)

    Parameters
    ----------
    method : ``"raw" | "zscore" | "blom" | "rank"``
    winsor : 分位点ウィンザライズの片側割合（None ならスキップ）
    winsor_mad : MAD クリップの係数 c（None ならスキップ）
    group_col : 中立化に使うグループ列（例 ``"sector"``）
    control_cols : 中立化に使う連続変数列（例 ``["log_cap"]``）
    suffix : 出力列名の接尾辞（既定は ``f"_{method}"``）

    Returns
    -------
    元のパネルに ``{factor}{suffix}`` 列を足した DataFrame（コピー）
    """
    if isinstance(factors, str):
        factors = [factors]
    if method not in TRANSFORMS:
        raise ValueError(f"unknown method: {method!r} (choose from {list(TRANSFORMS)})")
    fn = TRANSFORMS[method]
    suffix = f"_{method}" if suffix is None else suffix

    out = panel.copy()
    new_cols = {}
    for f in factors:
        pieces = []
        for _, blk in out.groupby(level=time_level, sort=True):
            s = blk[f].astype(float)
            if winsor is not None:
                s = winsorize(s, winsor)
            if winsor_mad is not None:
                s = winsorize_mad(s, winsor_mad)
            s = fn(s)
            if group_col is not None or control_cols:
                s = neutralize(
                    s,
                    groups=blk[group_col] if group_col else None,
                    controls=blk[control_cols] if control_cols else None,
                )
            pieces.append(s)
        new_cols[f"{f}{suffix}"] = pd.concat(pieces)
    # 列を1本ずつ挿入すると DataFrame が断片化するため一括で結合する
    return pd.concat([out, pd.DataFrame(new_cols).reindex(out.index)], axis=1)


# 中立化バリアント: 名前 -> (group_col, control_cols)
NEUTRAL_SPECS: dict[str, tuple[str | None, list[str] | None]] = {
    "none": (None, None),
    "size": (None, ["log_cap"]),
    "sector": ("sector", None),
    "size_sector": ("sector", ["log_cap"]),
}

VARIANT_LABEL = {
    "none": "No neutralization",
    "size": "Size-neutral",
    "sector": "Sector-neutral",
    "size_sector": "Size + sector neutral",
}


def make_neutral_variants(
    panel: pd.DataFrame,
    factor: str,
    method: str = "blom",
    variants: list[str] | None = None,
    winsor: float | None = None,
    specs: dict[str, tuple[str | None, list[str] | None]] | None = None,
    time_level: str = "yyyymm",
) -> tuple[pd.DataFrame, dict[str, str]]:
    r"""中立化バリアントのスコア列をまとめて作る.

    各バリアントは「標準化（``method``）→ 時点 t のクロスセクション回帰の残差」で作る
    （docs/tutorial/02 2.5 の方式）:

    .. math::
        z_{i,t} = \gamma_0 + \sum_g \gamma_g \mathbb{1}[i \in g]
                  + \gamma_s \log \text{Cap}_{i,t} + \varepsilon_{i,t},
        \qquad x_{i,t} = \varepsilon_{i,t} / \sigma(\varepsilon_{\cdot,t})

    - ``size``        : ``log_cap`` のみコントロール
    - ``sector``      : GICS セクターダミーのみ
    - ``size_sector`` : 両方を同時に落とす（逐次ではなく1本の回帰）

    Returns
    -------
    (panel, score_cols): ``score_cols[variant]`` が対応するスコア列名
    """
    specs = specs or NEUTRAL_SPECS
    variants = variants or list(specs)
    out = panel.copy()
    score_cols: dict[str, str] = {}
    for v in variants:
        group_col, control_cols = specs[v]
        suffix = f"_{method}" if v == "none" else f"_{method}_{v}"
        out = standardize_panel(
            out, factor, method=method, winsor=winsor,
            group_col=group_col, control_cols=control_cols,
            time_level=time_level, suffix=suffix,
        )
        score_cols[v] = f"{factor}{suffix}"
    return out, score_cols


def neutralization_check(
    panel: pd.DataFrame,
    score_cols: dict[str, str],
    time_level: str = "yyyymm",
) -> pd.DataFrame:
    """中立化が効いているかの診断.

    - ``corr_log_cap``  : スコアと ``log_cap`` の月次クロスセクション相関の平均（サイズ中立なら ≈ 0）
    - ``sector_disp``   : セクター平均スコアの月次標準偏差の平均（セクター中立なら ≈ 0）
    - ``corr_with_none``: 中立化なし版との相関（どれだけ情報が残ったか）
    """
    base = score_cols.get("none")
    rows = {}
    for v, col in score_cols.items():
        g = panel.groupby(level=time_level)
        corr_cap = g.apply(lambda b: b[col].corr(b["log_cap"])).mean()
        sec_disp = g.apply(lambda b: b.groupby("sector")[col].mean().std()).mean()
        corr_base = (g.apply(lambda b: b[col].corr(b[base])).mean() if base else np.nan)
        rows[v] = {"corr_log_cap": corr_cap, "sector_disp": sec_disp, "corr_with_none": corr_base}
    return pd.DataFrame(rows).T


def tail_stats(panel: pd.DataFrame, factors: str | list[str], time_level: str = "yyyymm") -> pd.DataFrame:
    """裾の厚さの診断: 月次クロスセクションの歪度・尖度・|z|>3 比率の平均."""
    if isinstance(factors, str):
        factors = [factors]
    rows = {}
    for f in factors:
        g = panel.groupby(level=time_level)[f]
        z_out = panel.groupby(level=time_level)[f].transform(zscore).abs()
        rows[f] = {
            "skew": g.skew().mean(),
            "excess_kurt": g.apply(lambda s: s.kurt()).mean(),
            "frac_|z|>3": z_out.gt(3).groupby(level=time_level).mean().mean(),
            "frac_|z|>5": z_out.gt(5).groupby(level=time_level).mean().mean(),
            "coverage": g.count().sum() / len(panel),
        }
    return pd.DataFrame(rows).T
