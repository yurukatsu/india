"""ファクターの業種間・業種内分解（Vyas & van Baren "Should Equity Factors Be Betting on Industries?"）。

記号（日付 :math:`t`、銘柄 :math:`i`、銘柄の属する業種 :math:`g(i)`）:

- 業種間成分: :math:`F^{A}_{i,t} = \\mathrm{med}_{j \\in g(i)} F_{j,t}`（業種の中央値。同じ業種の銘柄は同じ値）
- 業種内成分: :math:`F^{W}_{i,t} = (F_{i,t} - F^{A}_{i,t}) / (k \\cdot \\mathrm{MAD}_{g(i),t})`、:math:`k = 1.4826`
  （正規分布で MAD を標準偏差に換算する定数）
- 混合: :math:`F^{(w)} = w \\cdot z(F^{A}) + (1 - w) \\cdot z(F^{W})`。:math:`w = 1` で業種間、:math:`w = 0` で業種内。
  :math:`z` はユニバース内の Blom 正規スコア。

MAD が 0 の業種（同じ値が多い指標）は業種内の標準偏差で割り、それも 0 なら業種内成分を 0 とする。
銘柄数の少ない業種は、上位の分類（例: 産業グループ → セクター）にまとめる（:func:`industry_labels`）。
"""

from __future__ import annotations

import pandas as pd
from scipy.stats import norm

MAD_SCALE = 1.4826

#: GICS コード（8 桁）を各階層に切り詰める除数。
GICS_LEVELS: dict[str, int] = {
    "sector": 1_000_000,
    "industry_group": 10_000,
    "industry": 100,
}


def blom(values: pd.DataFrame) -> pd.DataFrame:
    """各行（日付）内で Blom 正規スコア :math:`\\Phi^{-1}((r - 3/8)/(n + 1/4))` に変換する（同順位は平均順位）。

    Args:
        values (pd.DataFrame): 日付 × 銘柄の値（欠損は NaN のまま）。

    Returns:
        pd.DataFrame: 同じ形の正規スコア。

    Examples:
        >>> df = pd.DataFrame({"A": [1.0], "B": [2.0], "C": [3.0]}, index=[202001])
        >>> blom(df).round(3).iloc[0].tolist()
        [-0.869, 0.0, 0.869]
    """
    ranks = values.rank(axis=1, method="average")
    n = values.notna().sum(axis=1)
    u = ranks.sub(3.0 / 8.0).div(n.add(1.0 / 4.0), axis=0)
    return pd.DataFrame(
        norm.ppf(u.to_numpy(dtype=float)), index=values.index, columns=values.columns
    )


def industry_labels(
    gics: pd.DataFrame,
    level: str = "industry_group",
    universe: pd.DataFrame | None = None,
    min_size: int = 5,
    fallback: str = "sector",
) -> pd.DataFrame:
    """GICS コードから業種ラベルを作る。銘柄数が ``min_size`` 未満の業種は ``fallback`` の階層にまとめる。

    まとめた業種のラベルは ``fallback`` の階層のコードに負号を付けて、元の階層のコードと区別する。

    Args:
        gics (pd.DataFrame): 日付 × 銘柄の GICS コード（8 桁）。
        level (str): ``sector`` / ``industry_group`` / ``industry``。
        universe (pd.DataFrame | None): ユニバース（指定時は構成銘柄だけにラベルを付け、銘柄数もその中で数える）。
        min_size (int): 業種として扱う最小銘柄数。
        fallback (str): 小さい業種をまとめる階層。

    Returns:
        pd.DataFrame: 日付 × 銘柄の業種ラベル（float、欠損は NaN）。

    Examples:
        >>> g = pd.DataFrame([[45102010, 45103010, 45202030, 40101010]], columns=list("ABCD"), index=[0])
        >>> industry_labels(g, "industry_group", min_size=2).iloc[0].tolist()
        [4510.0, 4510.0, -45.0, -40.0]
    """
    if level not in GICS_LEVELS or fallback not in GICS_LEVELS:
        raise ValueError(f"level / fallback は {list(GICS_LEVELS)} のいずれか")
    codes = gics.where(universe.notna()) if universe is not None else gics
    lab = (codes // GICS_LEVELS[level]).astype(float)
    if min_size <= 1:
        return lab
    st = lab.stack()
    size = st.groupby([st.index.get_level_values(0), st]).transform("size").unstack()
    size = size.reindex(index=lab.index, columns=lab.columns)
    coarse = -(codes // GICS_LEVELS[fallback]).astype(float)
    return lab.where(size >= min_size, coarse).where(lab.notna())


def _long(values: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """値とラベルを（日付, 銘柄）の縦長にそろえ、両方そろう行だけ残す。"""
    v = values.stack()
    g = labels.reindex(index=values.index, columns=values.columns).stack()
    df = pd.DataFrame({"v": v, "g": g}).dropna()
    df["d"] = df.index.get_level_values(0)
    return df


def industry_decompose(
    values: pd.DataFrame, labels: pd.DataFrame, k: float = MAD_SCALE
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """業種間成分（業種の中央値）と業種内成分（業種中央値からの乖離 / (k · MAD)）に分ける。

    Args:
        values (pd.DataFrame): 日付 × 銘柄のファクター値（高いほど良い向き）。
        labels (pd.DataFrame): 日付 × 銘柄の業種ラベル（:func:`industry_labels`）。
        k (float): MAD を標準偏差に換算する定数。

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: ``(across, within)``。ラベルか値が欠損の銘柄は NaN。

    Examples:
        >>> v = pd.DataFrame([[1.0, 3.0, 5.0, 10.0, 20.0]], columns=list("ABCDE"), index=[0])
        >>> g = pd.DataFrame([[1, 1, 1, 2, 2]], columns=list("ABCDE"), index=[0])
        >>> a, w = industry_decompose(v, g)
        >>> a.iloc[0].tolist()
        [3.0, 3.0, 3.0, 15.0, 15.0]
        >>> w.round(3).iloc[0].tolist()
        [-0.674, 0.0, 0.674, -0.674, 0.674]
    """
    df = _long(values, labels)
    keys = [df["d"], df["g"]]
    med = df["v"].groupby(keys).transform("median")
    dev = df["v"] - med
    scale = dev.abs().groupby(keys).transform("median") * k
    sd = df["v"].groupby(keys).transform("std")
    scale = scale.where(scale > 0, sd)
    within = (dev / scale.where(scale > 0)).fillna(0.0)
    across = med.unstack().reindex(index=values.index, columns=values.columns)
    within = within.unstack().reindex(index=values.index, columns=values.columns)
    return across, within


def blend_across_within(
    across: pd.DataFrame, within: pd.DataFrame, w: float
) -> pd.DataFrame:
    """:math:`w \\cdot z(F^{A}) + (1 - w) \\cdot z(F^{W})` を Blom 化して返す（:math:`z` は Blom）。

    Args:
        across (pd.DataFrame): 業種間成分。
        within (pd.DataFrame): 業種内成分。
        w (float): 業種間の比率（0〜1）。

    Returns:
        pd.DataFrame: 日付 × 銘柄の Blom スコア。

    Examples:
        >>> a = pd.DataFrame([[1.0, 1.0, 2.0]], columns=list("ABC"), index=[0])
        >>> b = pd.DataFrame([[-1.0, 1.0, 0.0]], columns=list("ABC"), index=[0])
        >>> blend_across_within(a, b, 0.0).round(3).iloc[0].tolist()
        [-0.869, 0.869, 0.0]
    """
    if not 0.0 <= w <= 1.0:
        raise ValueError("w は 0 以上 1 以下")
    if w == 0.0:
        return blom(within)
    if w == 1.0:
        return blom(across)
    za, zw = blom(across), blom(within)
    both = za.notna() & zw.notna()
    return blom((w * za + (1.0 - w) * zw).where(both))


def industry_variance_share(values: pd.DataFrame, labels: pd.DataFrame) -> pd.Series:
    """順位の分散のうち業種で説明できる割合（業種ダミーへの回帰の :math:`R^2`）を日付ごとに返す。

    Args:
        values (pd.DataFrame): 日付 × 銘柄のファクター値。
        labels (pd.DataFrame): 日付 × 銘柄の業種ラベル。

    Returns:
        pd.Series: 日付 → :math:`R^2`（銘柄が 2 未満の日付は NaN）。

    Examples:
        >>> v = pd.DataFrame([[1.0, 2.0, 3.0, 4.0]], columns=list("ABCD"), index=[0])
        >>> g = pd.DataFrame([[1, 1, 2, 2]], columns=list("ABCD"), index=[0])
        >>> round(float(industry_variance_share(v, g).iloc[0]), 3)
        0.8
    """
    df = _long(values, labels)
    df["r"] = df["v"].groupby(df["d"]).rank()
    gm = df["r"].groupby([df["d"], df["g"]]).transform("mean")
    tm = df["r"].groupby(df["d"]).transform("mean")
    ss_res = ((df["r"] - gm) ** 2).groupby(df["d"]).sum()
    ss_tot = ((df["r"] - tm) ** 2).groupby(df["d"]).sum()
    out = 1.0 - ss_res / ss_tot.where(ss_tot > 0)
    return out


def quantile_excess(
    score: pd.DataFrame,
    fwd: pd.DataFrame,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """等ウェイト分位ポートフォリオの、等ウェイトユニバース平均に対する超過リターン（月次）。

    分位はパーセンタイル :math:`p = (\\mathrm{rank} - 0.5)/N` で決め、``Q{n}`` が最上位。
    行 :math:`t` は「:math:`t` のスコアで作り、:math:`t+1` の期間に保有した」リターン（``fwd`` の行 :math:`t`）。

    Args:
        score (pd.DataFrame): 日付 × 銘柄のスコア（ユニバース外は NaN にしておく）。
        fwd (pd.DataFrame): 日付 × 銘柄の翌期リターン（行 :math:`t` は :math:`t+1` の期間）。
        n_quantiles (int): 分位数。

    Returns:
        pd.DataFrame: 日付 × ``Q1..Q{n}`` と ``spread``（最上位 − 最下位）。

    Examples:
        >>> s = pd.DataFrame([[1.0, 2.0, 3.0, 4.0, 5.0]], columns=list("ABCDE"), index=[0])
        >>> f = pd.DataFrame([[0.0, 0.1, 0.2, 0.3, 0.4]], columns=list("ABCDE"), index=[0])
        >>> quantile_excess(s, f).round(2).iloc[0].tolist()
        [-0.2, -0.1, 0.0, 0.1, 0.2, 0.4]
    """
    fwd = fwd.reindex(index=score.index, columns=score.columns)
    s = score.where(fwd.notna())
    p = (s.rank(axis=1, method="average") - 0.5).div(s.notna().sum(axis=1), axis=0)
    base = fwd.where(s.notna()).mean(axis=1)
    out = {}
    for q in range(1, n_quantiles + 1):
        lo = (q - 1) / n_quantiles
        hi = q / n_quantiles
        m = (p >= lo) & ((p < hi) if q < n_quantiles else p.notna())
        out[f"Q{q}"] = fwd.where(m).mean(axis=1) - base
    df = pd.DataFrame(out)
    df["spread"] = df[f"Q{n_quantiles}"] - df["Q1"]
    return df
