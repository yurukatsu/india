"""2 つのアルファスコアを加重平均した複合スコアを ``input`` の alpha 形式で生成する。

各月、ユニバース内でスコアが存在する銘柄について

1. 各スコアを Blom 正規スコア :math:`z_i = \\Phi^{-1}((\\mathrm{rank}_i - 3/8)/(n + 1/4))` に変換（同順位は平均順位）
2. :math:`w \\cdot z^{A} + (1 - w) \\cdot z^{B}` を計算（片方しか無い銘柄は存在する方のみ、重みを再正規化）
3. 再び Blom 正規スコアに変換

して書き出す。Composite v1（``docs`` の合成仕様 §6.4）のスリーブ合成と同じ手順。

Examples:
    ``comp_ew`` と ``ens_lgbm_dnn`` を 50:50〜90:10 で合成する::

        uv run python scripts/build_blend.py \\
            --score-a composite/comp_ew --score-b my_ai/ens_lgbm_dnn \\
            --weights 50 60 70 80 90 --name "comp_ew_{a}_ai_{b}" --group blend
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from alphaeval.dates import to_int
from alphaeval.io import InputStore, write_score_file

logger = logging.getLogger("build_blend")


def blom_score(values: pd.DataFrame) -> pd.DataFrame:
    """各行（日付）内で Blom 正規スコアに変換する。

    Args:
        values (pd.DataFrame): 日付 × 銘柄のスコア（欠損は NaN のまま）。

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


def blend_scores(
    score_a: pd.DataFrame,
    score_b: pd.DataFrame,
    weight_a: float,
    universe: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """2 つのスコアを Blom 化して加重平均し、再 Blom 化した複合スコアを返す。

    Args:
        score_a (pd.DataFrame): 日付 × 銘柄のスコア A。
        score_b (pd.DataFrame): 日付 × 銘柄のスコア B。
        weight_a (float): A の重み（0〜1）。B の重みは ``1 - weight_a``。
        universe (pd.DataFrame | None): ユニバース（非構成銘柄は NaN）。指定時はユニバース内に限定。

    Returns:
        pd.DataFrame: 両スコアに共通する日付 × 和集合の銘柄の複合スコア（どちらも無い銘柄は NaN）。

    Examples:
        >>> a = pd.DataFrame({"A": [3.0], "B": [2.0], "C": [1.0]}, index=[202001])
        >>> b = pd.DataFrame({"A": [1.0], "B": [2.0], "C": [3.0]}, index=[202001])
        >>> blend_scores(a, b, 0.5).round(3).iloc[0].tolist()   # 相殺して全員同順位 → 0
        [0.0, 0.0, 0.0]
        >>> blend_scores(a, b, 1.0).round(3).iloc[0].tolist()
        [0.869, 0.0, -0.869]
    """
    dates = score_a.index.intersection(score_b.index)
    cols = sorted(set(score_a.columns) | set(score_b.columns))
    a = score_a.reindex(index=dates, columns=cols)
    b = score_b.reindex(index=dates, columns=cols)
    if universe is not None:
        mask = universe.reindex(index=dates, columns=cols).notna()
        a, b = a.where(mask), b.where(mask)
    za, zb = blom_score(a), blom_score(b)
    wa = np.where(za.notna(), weight_a, 0.0)
    wb = np.where(zb.notna(), 1.0 - weight_a, 0.0)
    total = wa + wb
    num = np.nan_to_num(za.to_numpy()) * wa + np.nan_to_num(zb.to_numpy()) * wb
    mixed = pd.DataFrame(
        np.where(total > 0, num / np.where(total > 0, total, 1.0), np.nan),
        index=dates,
        columns=cols,
    )
    return blom_score(mixed)


def build(
    input_root: Path,
    score_a: str,
    score_b: str,
    weights: Sequence[int],
    name_template: str,
    group: str,
    start: int | None,
    end: int | None,
) -> None:
    """複合スコアを生成して ``{input_root}/alpha/{group}/{name}/YYYYMM.dat`` に書き出す。

    Args:
        input_root (Path): ``input/msci_india_imi`` などのルート。
        score_a (str): スコア A の名前（``alpha/`` からの相対パス）。
        score_b (str): スコア B の名前。
        weights (Sequence[int]): A の重み（%）のリスト。
        name_template (str): スコア名のテンプレート（``{a}`` ``{b}`` に重み % が入る）。
        group (str): 出力グループ名。
        start (int | None): 開始年月。
        end (int | None): 終了年月。

    Returns:
        None
    """
    store = InputStore(input_root)
    univ = store.universe(start, end)
    a = store.alpha(score_a, start, end)
    b = store.alpha(score_b, start, end)
    logger.info(
        "A=%s %s, B=%s %s, universe %s", score_a, a.shape, score_b, b.shape, univ.shape
    )
    lines = [
        f"# {group}",
        "",
        f"`{score_a}`（A）と `{score_b}`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、",
        "`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。",
        "",
        "| スコア | A の重み | B の重み | 期間 |",
        "| --- | --- | --- | --- |",
    ]
    for w in weights:
        name = name_template.format(a=w, b=100 - w)
        blended = blend_scores(a, b, w / 100.0, univ)
        dest = input_root / "alpha" / group / name
        n = 0
        for date, row in blended.iterrows():
            s = row.dropna()
            if s.empty:
                continue
            write_score_file(dest / f"{to_int(date)}.dat", name, s)
            n += 1
        logger.info(
            "%s: %d ファイル（%d〜%d）",
            name,
            n,
            to_int(blended.index.min()),
            to_int(blended.index.max()),
        )
        lines.append(
            f"| `{name}` | {w}% | {100 - w}% | {to_int(blended.index.min())}〜{to_int(blended.index.max())} |"
        )
    (input_root / "alpha" / group / "README.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。

    Args:
        argv (Sequence[str] | None): 引数リスト。

    Returns:
        argparse.Namespace: 解析結果。

    Examples:
        >>> parse_args(["--score-a", "x", "--score-b", "y", "--weights", "50"]).weights
        [50]
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input-root", type=Path, default=Path("input") / "msci_india_imi"
    )
    parser.add_argument(
        "--score-a", required=True, help="スコア A（alpha/ からの相対パス）"
    )
    parser.add_argument("--score-b", required=True, help="スコア B")
    parser.add_argument(
        "--weights", type=int, nargs="+", required=True, help="A の重み（%%）"
    )
    parser.add_argument(
        "--name",
        default="blend_{a}_{b}",
        help="スコア名テンプレート（{a} {b} に重み %% が入る）",
    )
    parser.add_argument(
        "--group", default="blend", help="出力グループ（alpha/{group}/）"
    )
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """エントリポイント。

    Args:
        argv (Sequence[str] | None): 引数リスト。

    Returns:
        None
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = parse_args(argv)
    build(
        args.input_root,
        args.score_a,
        args.score_b,
        args.weights,
        args.name,
        args.group,
        args.start,
        args.end,
    )


if __name__ == "__main__":
    main()
