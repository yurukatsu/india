"""カバレッジ分析: スコアがユニバースをどれだけ覆っているか。"""

from __future__ import annotations

import pandas as pd

from alphaeval.dates import ensure_datetime_index


def coverage(score: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """日付ごとのカバレッジを計算する。

    Args:
        score (pd.DataFrame): 日付 × 銘柄のスコア（欠損は NaN）。
        universe (pd.DataFrame): 日付 × 銘柄のユニバースウェイト（非構成銘柄は NaN）。

    Returns:
        pd.DataFrame: ``universe`` の日付をインデックスとする表。

            - ``n_universe``: ユニバース銘柄数
            - ``n_covered``: スコアが存在するユニバース銘柄数
            - ``coverage``: ``n_covered / n_universe``
            - ``weight_coverage``: スコアが存在する銘柄のユニバースウェイト合計 ÷ 全ウェイト合計
            - ``n_score_total``: スコアが存在する銘柄数（ユニバース外を含む）
            - ``n_score_outside``: ユニバース外でスコアが存在する銘柄数

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31"])
        >>> u = pd.DataFrame({"A": [0.5], "B": [0.3], "C": [0.2]}, index=idx)
        >>> s = pd.DataFrame({"A": [1.0], "B": [float("nan")], "D": [2.0]}, index=idx)
        >>> coverage(s, u).iloc[0].to_dict()
        {'n_universe': 3.0, 'n_covered': 1.0, 'coverage': 0.3333333333333333, 'weight_coverage': 0.5, 'n_score_total': 2.0, 'n_score_outside': 1.0}
    """
    universe = ensure_datetime_index(universe)
    score = ensure_datetime_index(score)
    cols = sorted(set(universe.columns) | set(score.columns))
    u = universe.reindex(index=universe.index, columns=cols)
    s = score.reindex(index=universe.index, columns=cols)
    in_univ = u.notna()
    has_score = s.notna()
    n_univ = in_univ.sum(axis=1)
    n_cov = (in_univ & has_score).sum(axis=1)
    w_total = u.sum(axis=1)
    w_cov = u.where(has_score).sum(axis=1)
    n_total = has_score.sum(axis=1)
    return pd.DataFrame(
        {
            "n_universe": n_univ,
            "n_covered": n_cov,
            "coverage": n_cov / n_univ.where(n_univ > 0),
            "weight_coverage": w_cov / w_total.where(w_total > 0),
            "n_score_total": n_total,
            "n_score_outside": n_total - n_cov,
        },
        index=universe.index,
    ).astype(float)


def coverage_summary(cov: pd.DataFrame) -> pd.Series:
    """:func:`coverage` の出力を要約する。

    Args:
        cov (pd.DataFrame): :func:`coverage` の戻り値。

    Returns:
        pd.Series: 平均 / 最小 / 最大カバレッジ、平均ウェイトカバレッジ、期間、日付数。

    Examples:
        >>> idx = pd.to_datetime(["2020-01-31", "2020-02-29"])
        >>> c = pd.DataFrame({"n_universe": [10, 10], "n_covered": [8, 9], "coverage": [0.8, 0.9],
        ...                   "weight_coverage": [0.9, 0.95], "n_score_total": [8, 9],
        ...                   "n_score_outside": [0, 0]}, index=idx)
        >>> round(coverage_summary(c)["coverage_mean"], 6)
        0.85
    """
    return pd.Series(
        {
            "n_dates": float(len(cov)),
            "start": cov.index.min(),
            "end": cov.index.max(),
            "n_universe_mean": cov["n_universe"].mean(),
            "n_covered_mean": cov["n_covered"].mean(),
            "coverage_mean": cov["coverage"].mean(),
            "coverage_min": cov["coverage"].min(),
            "coverage_max": cov["coverage"].max(),
            "weight_coverage_mean": cov["weight_coverage"].mean(),
            "n_score_outside_mean": cov["n_score_outside"].mean(),
        }
    )
