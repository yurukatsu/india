"""アルファスコアを過去 ``L`` か月で平均した「長期保有版」を ``input`` の alpha 形式で生成する。

Bartram–Grinblatt (2018) の 1 年保有戦略（ラグ 0〜``L−1`` か月のシグナルで作った ``L`` 本の重なり合う
ポートフォリオの平均、Jegadeesh–Titman 型）を、スコア側で近似する。各月、銘柄ごとに

1. 元スコアを各月 Blom 正規スコアに変換し、
2. 当月を含む過去 ``L`` か月の平均を取り（``min_periods`` か月以上そろう銘柄のみ。当月のスコアが無い銘柄は除外）、
3. 平均を当月の銘柄内で再び Blom 化する

して書き出す。シグナルの順位が月ごとに少しずつしか変わらなくなるので、分位ポートフォリオの回転率が下がる。

Examples:
    12 か月平均::

        uv run python scripts/build_smooth.py --score agnostic_neu/afa_rf_neu --window 12 \\
            --name afa_rf_neu_avg12 --group agnostic_smooth
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from scipy.stats import norm

from alphaeval.dates import to_int
from alphaeval.io import InputStore, write_score_file

logger = logging.getLogger("build_smooth")

DEFAULT_INPUT_ROOT = Path("input") / "msci_india_imi"


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


def smooth_score(score: pd.DataFrame, window: int, min_periods: int) -> pd.DataFrame:
    """スコアの過去 ``window`` か月平均（Blom 化してから平均し、再 Blom 化）を返す。

    Args:
        score (pd.DataFrame): 月次の日付 × 銘柄のスコア（日付は昇順・欠月なし）。
        window (int): 平均する月数（当月を含む）。
        min_periods (int): 平均に必要な最小月数。

    Returns:
        pd.DataFrame: 同じ形の平滑化スコア（当月のスコアが無い銘柄、月数不足の銘柄は NaN）。

    Examples:
        >>> s = pd.DataFrame({"A": [1.0, 1.0, 3.0], "B": [2.0, 2.0, 2.0], "C": [3.0, 3.0, 1.0]})
        >>> out = smooth_score(s, window=3, min_periods=2)
        >>> bool(out.iloc[0].isna().all())  # 1 か月しか無い
        True
        >>> out.iloc[2].rank().tolist()  # 3 か月平均では C > B > A（当月だけなら A が最上位）
        [1.0, 2.0, 3.0]
    """
    z = blom_score(score)
    avg = z.rolling(window, min_periods=min_periods).mean()
    return blom_score(avg.where(score.notna()))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解釈する。

    Args:
        argv (Sequence[str] | None): 引数（``None`` なら ``sys.argv``）。

    Returns:
        argparse.Namespace: 解釈結果。

    Examples:
        >>> parse_args(["--score", "a/b", "--name", "b12", "--group", "g"]).window
        12
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    p.add_argument("--score", required=True, help="元スコア（alpha/ からの相対パス）")
    p.add_argument("--window", type=int, default=12, help="平均する月数（当月を含む）")
    p.add_argument("--min-periods", type=int, default=6, help="平均に必要な最小月数")
    p.add_argument("--name", required=True, help="出力スコア名")
    p.add_argument(
        "--group", required=True, help="出力グループ（alpha/{group}/{name}）"
    )
    p.add_argument("--start", type=int, default=None)
    p.add_argument("--end", type=int, default=None)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """平滑化スコアを生成して書き出し、グループの README に追記する。

    Args:
        argv (Sequence[str] | None): 引数。

    Returns:
        None

    Examples:
        >>> main(["--score", "agnostic_neu/afa_rf_neu", "--name", "afa_rf_neu_avg12",
        ...       "--group", "agnostic_smooth"])  # doctest: +SKIP
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    store = InputStore(args.input_root)
    raw = store.alpha(args.score).sort_index()
    full = pd.date_range(raw.index.min(), raw.index.max(), freq="M")
    raw = raw.reindex(full)
    smoothed = smooth_score(raw, args.window, args.min_periods)
    dest = args.input_root / "alpha" / args.group
    written = []
    for date, row in smoothed.iterrows():
        ym = to_int(date)
        if (args.start and ym < args.start) or (args.end and ym > args.end):
            continue
        v = row.dropna()
        if v.empty:
            continue
        write_score_file(dest / args.name / f"{ym}.dat", args.name, v)
        written.append(ym)
    period = f"{written[0]}〜{written[-1]}" if written else "-"
    logger.info("%s: %d ファイル（%s）", args.name, len(written), period)
    line = (
        f"| `{args.name}` | `{args.score}` | {args.window} か月"
        f"（最小 {args.min_periods} か月） | {period} |"
    )
    readme = dest / "README.md"
    header = (
        "# 平滑化スコア（長期保有版）\n\n"
        "元スコアを各月 Blom 化して過去 L か月（当月を含む）で平均し、当月の銘柄内で再 Blom 化したもの。"
        "`scripts/build_smooth.py` で生成。Bartram–Grinblatt (2018) の 1 年保有戦略"
        "（ラグ 0〜L−1 のシグナルによる重なり合うポートフォリオの平均）をスコア側で近似する。\n\n"
        "| スコア | 元スコア | 窓 | 期間 |\n| --- | --- | --- | --- |\n"
    )
    lines = []
    if readme.exists():
        lines = [
            ln
            for ln in readme.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| `") and not ln.startswith(f"| `{args.name}` ")
        ]
    readme.write_text(header + "\n".join(lines + [line]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
