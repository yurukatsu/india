"""「健全な長期成長企業（Healthy Compounder）」ファクター候補を ``input`` の alpha 形式で生成する。

Notion「インド株：健全な長期成長企業を評価するファクター設計」のうち、手元の ``core`` ファクターと
リスクモデルのエクスポージャーだけで作れる柱（①キャッシュ収益性、②QMJ、③売上成長の安定性、④資本規律）を
月次スコアにして ``alpha/hc/{name}/YYYYMM.dat`` に書き出す。

各スコアの作り方（すべて「高いほど良い」向きにそろえる）:

1. 素材を符号調整し、各月ユニバース内で Blom 正規スコアに変換する。
2. 柱の中で素材の Blom スコアを等ウェイト平均する（``min_components`` 本以上そろう銘柄のみ）。
3. 平均を再び Blom 化する。

時系列の特徴量（12 か月変化、5 年ばらつき）はユニバース外の期間も含む ``core`` の全履歴から計算し、
最後にユニバースで絞る。

Examples:
    全スコアを生成::

        uv run python scripts/build_healthy_compounder.py

    期間を指定::

        uv run python scripts/build_healthy_compounder.py --start 200301 --end 202606
"""

from __future__ import annotations

import argparse
import logging
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from alphaeval.dates import to_int
from alphaeval.io import InputStore, write_score_file

logger = logging.getLogger("build_healthy_compounder")

DEFAULT_INPUT_ROOT = Path("input") / "msci_india_imi"
GROUP = "hc"
RISK_MODEL = "GEMLT"
BETA_FACTOR_ID = 101
FINANCIALS_SECTOR = 40


@dataclass(frozen=True)
class ScoreSpec:
    """生成するスコア 1 本の定義。

    Attributes:
        name (str): スコア名（``alpha/hc/{name}``）。
        pillar (str): Notion 提案の柱。
        components (tuple[str, ...]): 素材名（符号付き。``-`` 始まりは符号反転）。
        description (str): README に書く説明。
        min_components (int): 平均に必要な最小素材数。
    """

    name: str
    pillar: str
    components: tuple[str, ...]
    description: str
    min_components: int = 1


SCORE_SPECS: tuple[ScoreSpec, ...] = (
    ScoreSpec(
        "hc_cfoa",
        "① キャッシュ収益性",
        ("ocf_ta",),
        "営業 CF / 総資産（`ocf_ta`）。Ball et al. (2016) の CbOP の代替。インドの損益計算書は費用を性質別に開示するため"
        "売上総利益 − 販管費（`gpa_act − sga_ta`）が営業利益の代わりにならず、キャッシュ側の指標を使う",
    ),
    ScoreSpec("hc_cfp", "① キャッシュ収益性", ("ocfp",), "営業 CF / 株価（`ocfp`）"),
    ScoreSpec(
        "hc_dso",
        "① キャッシュ収益性",
        ("-dso_chg_ind",),
        "売掛金回転日数（365/`rec_to`）の 12 か月変化を GICS 産業グループ中央値からの乖離にしたもの（負号）。"
        "金融セクターは対象外",
    ),
    ScoreSpec(
        "hc_cash",
        "① キャッシュ収益性",
        ("ocf_ta", "ocfp", "-dso_chg_ind"),
        "① の 3 素材の合成",
        min_components=2,
    ),
    ScoreSpec(
        "hc_qmj_prof",
        "② QMJ",
        ("gpa_act", "roe_act", "roa_act", "ocf_ta", "gm_act", "acc_bs"),
        "QMJ 収益性: GPOA, ROE, ROA, CFOA, GMAR, −ACC。`core` の `acc_bs` は (営業 CF − 純利益)/総資産と"
        "正に相関する「負のアクルーアル」の向きなので符号はそのまま使う",
        min_components=3,
    ),
    ScoreSpec(
        "hc_qmj_growth",
        "② QMJ",
        ("dlt_gpa_5y", "dlt_roe_5y", "dlt_roa_5y", "dlt_ocfa_5y", "dlt_gps_5y"),
        "QMJ 成長: 粗利・純利益・営業利益・営業 CF・粗利率の 5 年変化",
        min_components=3,
    ),
    ScoreSpec(
        "hc_qmj_safety",
        "② QMJ",
        ("-beta", "-vola60", "-td_ta", "-oscore", "zscore", "-roe_vol5y"),
        "QMJ 安全性: −BETA（Barra）, −VOLA60, −負債/総資産, −O-score, Z-score, −ROE の 5 年ばらつき（3 点以上）",
        min_components=3,
    ),
    ScoreSpec(
        "hc_qmj_payout",
        "② QMJ",
        ("eiss", "diss", "npop"),
        "QMJ 還元: −純株式発行（`eiss`）, −純負債発行（`diss`）, 5 年純還元/営業利益（`npop`）。参考",
        min_components=2,
    ),
    ScoreSpec(
        "hc_qmj",
        "② QMJ",
        ("hc_qmj_prof", "hc_qmj_growth", "hc_qmj_safety"),
        "QMJ = 収益性 + 成長 + 安全性",
        min_components=2,
    ),
    ScoreSpec(
        "hc_sales_stab",
        "③ 期待過熱の回避",
        ("-sales_grw_vol5y",),
        "売上成長率（`sales_grw`）の過去 5 年（12 か月おき 5 点、3 点以上）の標準偏差（負号）",
    ),
    ScoreSpec(
        "hc_issuance",
        "④ 資本規律",
        ("-csi", "eiss"),
        "−総合株式発行（`csi`）と −純株式発行（`eiss`）の合成",
    ),
    ScoreSpec(
        "hc_all",
        "統合",
        ("hc_cash", "hc_qmj", "hc_sales_stab", "hc_issuance"),
        "①②③④ の柱スコアの等ウェイト合成",
        min_components=3,
    ),
)

CORE_INPUTS: tuple[str, ...] = (
    "gpa_act",
    "acc_bs",
    "ocfp",
    "rec_to",
    "roe_act",
    "roa_act",
    "ocf_ta",
    "gm_act",
    "dlt_gpa_5y",
    "dlt_roe_5y",
    "dlt_roa_5y",
    "dlt_ocfa_5y",
    "dlt_gps_5y",
    "vola60",
    "td_ta",
    "oscore",
    "zscore",
    "eiss",
    "diss",
    "npop",
    "sales_grw",
    "csi",
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


def annual_std(panel: pd.DataFrame, n_years: int = 5, min_obs: int = 4) -> pd.DataFrame:
    """12 か月おきの過去 ``n_years`` 点の標本標準偏差を返す。

    月次パネルの値が年次決算の更新でしか変わらないことを前提に、``t, t−12, …, t−12(n−1)`` を 1 年 1 点として扱う。

    Args:
        panel (pd.DataFrame): 連続した月末インデックスの日付 × 銘柄パネル。
        n_years (int): 使う年数（点数）。
        min_obs (int): 必要な最小点数。

    Returns:
        pd.DataFrame: 同じ形の標準偏差（点数不足は NaN）。

    Examples:
        >>> idx = pd.date_range("2000-01-31", periods=49, freq="M")
        >>> p = pd.DataFrame({"A": np.r_[np.full(12, 1.0), np.full(12, 2.0), np.full(12, 3.0),
        ...                              np.full(12, 4.0), [5.0]]}, index=idx)
        >>> round(float(annual_std(p).iloc[-1, 0]), 3)
        1.581
    """
    stack = np.stack(
        [panel.shift(12 * k).to_numpy(dtype=float) for k in range(n_years)]
    )
    count = np.isfinite(stack).sum(axis=0)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        std = np.nanstd(stack, axis=0, ddof=1)
    std[count < min_obs] = np.nan
    return pd.DataFrame(std, index=panel.index, columns=panel.columns)


def group_demean(panel: pd.DataFrame, groups: pd.DataFrame) -> pd.DataFrame:
    """各日付・各グループ内の中央値を引く。

    Args:
        panel (pd.DataFrame): 日付 × 銘柄の値。
        groups (pd.DataFrame): 同じ形のグループラベル（NaN は対象外）。

    Returns:
        pd.DataFrame: グループ中央値からの乖離。

    Examples:
        >>> p = pd.DataFrame({"A": [1.0], "B": [3.0], "C": [10.0]}, index=[0])
        >>> g = pd.DataFrame({"A": [1], "B": [1], "C": [2]}, index=[0])
        >>> group_demean(p, g).iloc[0].tolist()
        [-1.0, 1.0, 0.0]
    """
    long = pd.DataFrame({"v": panel.stack(), "g": groups.stack()}).dropna()
    med = long.groupby([long.index.get_level_values(0), "g"])["v"].transform("median")
    return (long["v"] - med).unstack().reindex(index=panel.index, columns=panel.columns)


def average_components(
    blom: Mapping[str, pd.DataFrame], components: Sequence[str], min_components: int
) -> pd.DataFrame:
    """符号付き素材の Blom スコアを平均する。

    Args:
        blom (Mapping[str, pd.DataFrame]): 素材名 → Blom スコア（符号調整前）。
        components (Sequence[str]): 符号付き素材名（``-`` 始まりは反転）。
        min_components (int): 必要な最小素材数。

    Returns:
        pd.DataFrame: 平均（素材数不足は NaN）。

    Examples:
        >>> a = pd.DataFrame({"X": [1.0], "Y": [-1.0]}, index=[0])
        >>> b = pd.DataFrame({"X": [0.5], "Y": [np.nan]}, index=[0])
        >>> average_components({"a": a, "b": b}, ["a", "-b"], 1).iloc[0].tolist()
        [0.25, -1.0]
    """
    parts = []
    for comp in components:
        sign, key = (-1.0, comp[1:]) if comp.startswith("-") else (1.0, comp)
        parts.append(sign * blom[key])
    total = sum(p.fillna(0.0) for p in parts)
    count = sum(p.notna().astype(int) for p in parts)
    return (total / count).where(count >= min_components)


def load_raw_inputs(
    store: InputStore,
    start: int,
    end: int,
    columns: pd.Index,
    load_beta: Callable[[pd.DatetimeIndex], pd.DataFrame] | None = None,
) -> dict[str, pd.DataFrame]:
    """素材の生値パネル（ユニバースで絞る前）を作る。

    Args:
        store (InputStore): 入力ストア。
        start (int): 開始年月。
        end (int): 終了年月。
        columns (pd.Index): 銘柄（列）。
        load_beta (Callable[[pd.DatetimeIndex], pd.DataFrame] | None): BETA パネルの読み込み関数
            （テスト用の差し替え口。``None`` ならリスクモデルから読む）。

    Returns:
        dict[str, pd.DataFrame]: 素材名 → 日付 × 銘柄の生値。

    Examples:
        >>> raw = load_raw_inputs(InputStore("input/msci_india_imi"), 200301, 202606, cols)  # doctest: +SKIP
        >>> sorted(raw)[:3]  # doctest: +SKIP
        ['acc_bs', 'beta', 'csi']
    """
    idx = pd.date_range(
        pd.Timestamp(f"{start // 100}-{start % 100:02d}-01"),
        pd.Timestamp(f"{end // 100}-{end % 100:02d}-01") + pd.offsets.MonthEnd(0),
        freq="M",
    )
    raw = {
        c: store.alpha(f"core/{c}", start, end).reindex(index=idx, columns=columns)
        for c in CORE_INPUTS
    }
    rec_to = raw["rec_to"].where(raw["rec_to"] > 0)
    dso = 365.0 / rec_to
    gics = store.alpha("attributes/gics", start, end).reindex(
        index=idx, columns=columns
    )
    sector = gics // 1_000_000
    industry_group = (gics // 10_000).where(sector != FINANCIALS_SECTOR)
    raw["dso_chg_ind"] = group_demean(dso - dso.shift(12), industry_group)
    raw["roe_vol5y"] = annual_std(raw["roe_act"], min_obs=3)
    raw["sales_grw_vol5y"] = annual_std(raw["sales_grw"], min_obs=3)
    raw["beta"] = (load_beta or (lambda i: load_beta_panel(store, i)))(idx).reindex(
        columns=columns
    )
    return raw


def load_beta_panel(store: InputStore, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """リスクモデルの BETA エクスポージャーを日付 × 銘柄で返す。

    Args:
        store (InputStore): 入力ストア。
        dates (pd.DatetimeIndex): 読み込む月末日付。

    Returns:
        pd.DataFrame: 日付 × 銘柄の BETA（ファイルが無い月は欠損）。

    Examples:
        >>> beta = load_beta_panel(InputStore("input/msci_india_imi"), idx)  # doctest: +SKIP
    """
    rows = {}
    for d in dates:
        try:
            exp = store.exposure(RISK_MODEL, d)
        except FileNotFoundError:
            continue
        if BETA_FACTOR_ID in exp.columns:
            rows[d] = exp[BETA_FACTOR_ID]
    return pd.DataFrame(rows).T.reindex(dates)


def build_scores(
    raw: Mapping[str, pd.DataFrame],
    universe: pd.DataFrame,
    specs: Sequence[ScoreSpec] = SCORE_SPECS,
) -> dict[str, pd.DataFrame]:
    """素材の生値からスコアを作る（定義順に評価し、後のスコアは前のスコアを素材に使える）。

    Args:
        raw (Mapping[str, pd.DataFrame]): 素材名 → 生値パネル。
        universe (pd.DataFrame): ユニバース（非構成銘柄は NaN）。
        specs (Sequence[ScoreSpec]): スコア定義。

    Returns:
        dict[str, pd.DataFrame]: スコア名 → ユニバース内の Blom スコア。

    Examples:
        >>> scores = build_scores(raw, univ)  # doctest: +SKIP
        >>> scores["hc_all"].notna().sum(axis=1).tail()  # doctest: +SKIP
    """
    mask = universe.reindex(
        index=next(iter(raw.values())).index, columns=next(iter(raw.values())).columns
    ).notna()
    blom: dict[str, pd.DataFrame] = {}
    for key, panel in raw.items():
        blom[key] = blom_score(panel.where(mask))
    scores: dict[str, pd.DataFrame] = {}
    for spec in specs:
        avg = average_components(blom, spec.components, spec.min_components)
        scores[spec.name] = blom_score(avg.where(mask))
        blom[spec.name] = scores[spec.name]
    return scores


def write_scores(
    scores: Mapping[str, pd.DataFrame],
    dest_root: Path,
    specs: Sequence[ScoreSpec] = SCORE_SPECS,
) -> None:
    """スコアを ``{dest_root}/{name}/YYYYMM.dat`` に書き出し、README を作る。

    Args:
        scores (Mapping[str, pd.DataFrame]): スコア名 → 日付 × 銘柄。
        dest_root (Path): ``alpha/hc`` ディレクトリ。
        specs (Sequence[ScoreSpec]): README に載せるスコア定義。

    Returns:
        None

    Examples:
        >>> write_scores(scores, Path("input/msci_india_imi/alpha/hc"))  # doctest: +SKIP
    """
    lines = []
    for spec in specs:
        panel = scores[spec.name]
        n = 0
        first = last = None
        for date, row in panel.iterrows():
            v = row.dropna()
            if v.empty:
                continue
            write_score_file(
                dest_root / spec.name / f"{to_int(date)}.dat", spec.name, v
            )
            n += 1
            first = first or to_int(date)
            last = to_int(date)
        logger.info("%s: %d ファイル（%s〜%s）", spec.name, n, first, last)
        comps = ", ".join(f"`{c}`" for c in spec.components)
        lines.append(
            f"| `{spec.name}` | {spec.pillar} | {spec.description} | {comps} | {first}〜{last} |"
        )
    (dest_root / "README.md").write_text(
        "# Healthy Compounder ファクター候補\n\n"
        "`scripts/build_healthy_compounder.py` で `core` とリスクモデル（BETA）から生成。"
        "素材を符号調整して各月ユニバース内で Blom 化し、柱内で等ウェイト平均して再 Blom 化。"
        "すべて高いほど良い向き。\n\n"
        "| スコア | 柱 | 定義 | 素材（`-` は符号反転） | 期間 |\n| --- | --- | --- | --- | --- |\n"
        + "\n".join(lines)
        + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解釈する。

    Args:
        argv (Sequence[str] | None): 引数（``None`` なら ``sys.argv``）。

    Returns:
        argparse.Namespace: 解釈結果。

    Examples:
        >>> parse_args(["--start", "200501"]).start
        200501
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    p.add_argument("--start", type=int, default=200301)
    p.add_argument("--end", type=int, default=202606)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """スコアを生成して書き出す。

    Args:
        argv (Sequence[str] | None): 引数。

    Returns:
        None

    Examples:
        >>> main(["--start", "200301", "--end", "202606"])  # doctest: +SKIP
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    store = InputStore(args.input_root)
    univ = store.universe(args.start, args.end)
    raw = load_raw_inputs(store, args.start, args.end, univ.columns)
    univ = univ.reindex(index=next(iter(raw.values())).index)
    scores = build_scores(raw, univ)
    write_scores(scores, args.input_root / "alpha" / GROUP)


if __name__ == "__main__":
    main()
