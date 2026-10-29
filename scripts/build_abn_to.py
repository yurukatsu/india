"""異常回転率（abnormal turnover）のスコアを ``input`` の alpha 形式で生成する。

``experiments/market_basics/04_stock_timeseries`` で、当月の回転率を過去 12 か月の平均で割った比（の対数）が
既存特性（mom1・mom12_1・tover・vola60・skew60・log 時価総額）を制御しても翌月リターンを予測した（FM t 3.3）。
これを月次スコアにして ``alpha/abn_to/{name}/YYYYMM.dat`` に書き出し、既存の規約で評価できるようにする。

回転率 :math:`v_{i,\\tau}` は ``data/turnover`` の日次回転率（売買株数 / 発行済株数）。値の無い日（売買停止）は除く。

| スコア | 定義 |
| --- | --- |
| ``abn_to`` | :math:`\\log\\big(\\bar v_{i,t} / \\tfrac{1}{12}\\sum_{k=1}^{12} \\bar v_{i,t-k}\\big)`、:math:`\\bar v_{i,t}` は月 :math:`t` の日次回転率の平均（本命、04 と同じ） |
| ``abn_to_med`` | 日次の中央値版: 月 :math:`t` の中央値 / 前月末までの 252 営業日の中央値。大口取引（ブロック取引）の 1 日の急増に頑健 |
| ``abn_to_q`` | 3 か月版: :math:`t-2..t` の平均 / :math:`t-14..t-3` の平均。シグナルが遅く回転率が低い |

いずれも月末 :math:`t` までのデータだけで作る（翌月 :math:`t+1` のリターンで評価）。当月の値付き日が
``MIN_DAYS`` 日未満、または過去の月が ``MIN_MONTHS`` 未満の銘柄は欠損。各月ユニバースの構成銘柄に絞って書き出す。

Examples:
    全スコアを生成::

        uv run python scripts/build_abn_to.py

    期間を指定::

        uv run python scripts/build_abn_to.py --start 200801 --end 202607
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
from build_market_panel import load_daily_close_turnover

from alphaeval.dates import to_int
from alphaeval.io import InputStore, write_score_file

logger = logging.getLogger("build_abn_to")

DEFAULT_INPUT_ROOT = Path("input") / "msci_india_imi"
DEFAULT_DATA_ROOT = Path("data")
GROUP = "abn_to"

MIN_DAYS = 10  # 当月に必要な値付き日数
MIN_MONTHS = 6  # 基準期間（12 か月）に必要な月数
MEDIAN_WINDOW = 252  # abn_to_med の基準期間（営業日）
MEDIAN_MIN_OBS = 120

DESCRIPTIONS: dict[str, str] = {
    "abn_to": "log(当月の日次回転率の平均 / 過去 12 か月の月平均の平均)。本命",
    "abn_to_med": "log(当月の日次回転率の中央値 / 前月末までの 252 営業日の中央値)。ブロック取引に頑健",
    "abn_to_q": "log(直近 3 か月の平均 / その前の 12 か月の平均)。遅い版",
}


def monthly_turnover(
    turnover: pd.DataFrame, min_days: int = MIN_DAYS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """日次回転率を月次の平均と中央値に集計する。

    Args:
        turnover (pd.DataFrame): 営業日 × 銘柄の日次回転率（値の無い日は NaN）。
        min_days (int): 月に必要な値付き日数（未満は NaN）。

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: 月末 × 銘柄の平均と中央値。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
        >>> to = pd.DataFrame({"A": [1.0, 2.0, 6.0]}, index=idx)
        >>> mean, med = monthly_turnover(to, min_days=2)
        >>> float(mean.iloc[0, 0]), float(med.iloc[0, 0])
        (3.0, 2.0)
    """
    v = turnover.where(turnover > 0)
    g = v.resample("M")
    n = g.count()
    return g.mean().where(n >= min_days), g.median().where(n >= min_days)


def abnormal_ratio(
    num: pd.DataFrame, base: pd.DataFrame, lag: int, window: int, min_periods: int
) -> pd.DataFrame:
    """``num`` を ``base`` の過去平均（``lag`` か月前から ``window`` か月）で割った比の対数。

    Args:
        num (pd.DataFrame): 月末 × 銘柄の分子。
        base (pd.DataFrame): 月末 × 銘柄の基準（月次の値）。
        lag (int): 基準期間の終端が何か月前か（1 なら前月まで）。
        window (int): 基準期間の長さ（月）。
        min_periods (int): 基準期間に必要な月数。

    Returns:
        pd.DataFrame: :math:`\\log(\\mathrm{num}_t / \\mathrm{mean}(\\mathrm{base}_{t-lag-window+1..t-lag}))`。

    Examples:
        >>> idx = pd.date_range("2020-01-31", periods=4, freq="M")
        >>> b = pd.DataFrame({"A": [1.0, 1.0, 1.0, np.e]}, index=idx)
        >>> abnormal_ratio(b, b, lag=1, window=3, min_periods=3)["A"].round(3).tolist()
        [nan, nan, nan, 1.0]
    """
    ref = base.shift(lag).rolling(window, min_periods=min_periods).mean()
    ratio = num / ref
    return np.log(ratio.where(ratio > 0))


def median_ratio(turnover: pd.DataFrame, med_m: pd.DataFrame) -> pd.DataFrame:
    """当月の中央値を、前月末までの ``MEDIAN_WINDOW`` 営業日の日次中央値で割った比の対数。

    Args:
        turnover (pd.DataFrame): 営業日 × 銘柄の日次回転率。
        med_m (pd.DataFrame): 月末 × 銘柄の当月中央値（:func:`monthly_turnover`）。

    Returns:
        pd.DataFrame: 月末 × 銘柄の対数比。

    Examples:
        >>> r = median_ratio(to_daily, med_m)  # doctest: +SKIP
    """
    v = turnover.where(turnover > 0)
    roll = v.rolling(MEDIAN_WINDOW, min_periods=MEDIAN_MIN_OBS).median()
    ref = roll.resample("M").last().shift(1)  # 前月の最終営業日の値
    ratio = med_m / ref.reindex(med_m.index)
    return np.log(ratio.where(ratio > 0))


def build_scores(turnover: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """3 本のスコアを作る（ユニバースで絞る前）。

    Args:
        turnover (pd.DataFrame): 営業日 × 銘柄の日次回転率。

    Returns:
        dict[str, pd.DataFrame]: スコア名 → 月末 × 銘柄。

    Examples:
        >>> scores = build_scores(turnover)  # doctest: +SKIP
        >>> sorted(scores)  # doctest: +SKIP
        ['abn_to', 'abn_to_med', 'abn_to_q']
    """
    mean_m, med_m = monthly_turnover(turnover)
    q_num = mean_m.rolling(3, min_periods=2).mean()
    return {
        "abn_to": abnormal_ratio(
            mean_m, mean_m, lag=1, window=12, min_periods=MIN_MONTHS
        ),
        "abn_to_med": median_ratio(turnover, med_m),
        "abn_to_q": abnormal_ratio(
            q_num, mean_m, lag=3, window=12, min_periods=MIN_MONTHS
        ),
    }


def write_scores(
    scores: Mapping[str, pd.DataFrame], universe: pd.DataFrame, dest_root: Path
) -> None:
    """ユニバースの構成銘柄に絞って ``{dest_root}/{name}/YYYYMM.dat`` に書き出し、README を作る。

    Args:
        scores (Mapping[str, pd.DataFrame]): スコア名 → 月末 × 銘柄。
        universe (pd.DataFrame): 月末 × 銘柄のユニバースウェイト（非構成は NaN）。
        dest_root (Path): ``alpha/abn_to`` ディレクトリ。

    Returns:
        None

    Examples:
        >>> write_scores(scores, univ, Path("input/msci_india_imi/alpha/abn_to"))  # doctest: +SKIP
    """
    lines = []
    for name, panel in scores.items():
        p = panel.reindex(index=universe.index, columns=universe.columns).where(
            universe.notna()
        )
        p = p.replace([np.inf, -np.inf], np.nan)
        n, first, last, cov = 0, None, None, []
        for date, row in p.iterrows():
            v = row.dropna()
            if v.empty:
                continue
            write_score_file(dest_root / name / f"{to_int(date)}.dat", name, v)
            n += 1
            first = first or to_int(date)
            last = to_int(date)
            cov.append(len(v) / universe.loc[date].notna().sum())
        logger.info(
            "%s: %d ファイル（%s〜%s）、カバレッジ平均 %.3f",
            name,
            n,
            first,
            last,
            np.mean(cov),
        )
        lines.append(
            f"| `{name}` | {DESCRIPTIONS[name]} | {first}〜{last} | {np.mean(cov):.3f} |"
        )
    (dest_root / "README.md").write_text(
        "# 異常回転率（abnormal turnover）\n\n"
        "`scripts/build_abn_to.py` で `data/turnover` の日次回転率（売買株数 / 発行済株数）から生成。"
        "月末 t までのデータだけを使い、各月ユニバースの構成銘柄に絞った素の値（高いほど出来高が平常より多い）。"
        "検証は `experiments/abn_to/`。\n\n"
        "| スコア | 定義 | 期間 | カバレッジ |\n| --- | --- | --- | --- |\n"
        + "\n".join(lines)
        + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を読む。

    Args:
        argv (Sequence[str] | None): 引数（``None`` なら ``sys.argv``）。

    Returns:
        argparse.Namespace: ``input_root``, ``data_root``, ``start``, ``end``。

    Examples:
        >>> parse_args(["--start", "200801"]).start
        200801
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    p.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    p.add_argument("--start", type=int, default=200301)
    p.add_argument("--end", type=int, default=202607)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """エントリポイント。

    Args:
        argv (Sequence[str] | None): 引数。

    Examples:
        >>> main([])  # doctest: +SKIP
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = parse_args(argv)
    store = InputStore(args.input_root)
    universe = store.universe(args.start, args.end)
    _, turnover = load_daily_close_turnover(args.data_root, universe.columns)
    scores = build_scores(turnover)
    write_scores(scores, universe, args.input_root / "alpha" / GROUP)


if __name__ == "__main__":
    main()
