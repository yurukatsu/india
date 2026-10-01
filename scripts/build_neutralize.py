"""アルファスコアをサイズ・ボラティリティ・セクター等に対して中立化した版を ``input`` の alpha 形式で生成する。

各月、ユニバース内でスコアが存在する銘柄について

1. スコアと連続型の説明変数（log 時価総額、``vola60`` など）を Blom 正規スコアに変換
2. ``score ~ 定数 + 連続変数 + セクターダミー`` を OLS で推定し、残差を取る
3. 残差を再び Blom 正規スコアに変換

して書き出す。Composite v1（``docs`` の合成仕様 §6.4）の「GICS セクターダミー + log 時価総額への回帰残差」と同じ手順で、
連続変数を追加できるようにしたもの。

Examples:
    サイズ・``vola60``・セクターで中立化::

        uv run python scripts/build_neutralize.py --score my_ai/ens_lgbm_dnn \\
            --size --sector --controls core/vola60 --name ens_lgbm_dnn_neu --group my_ai_neu

    サイズ・セクターのみ::

        uv run python scripts/build_neutralize.py --score my_ai/ens_lgbm_dnn --size --sector \\
            --name ens_lgbm_dnn_neu_sz --group my_ai_neu
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

logger = logging.getLogger("build_neutralize")


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


def neutralize(
    score: pd.DataFrame,
    controls: dict[str, pd.DataFrame],
    groups: pd.DataFrame | None,
    universe: pd.DataFrame | None = None,
    min_obs: int = 30,
) -> pd.DataFrame:
    """スコアを連続変数とグループダミーに月次回帰し、残差を再 Blom 化して返す。

    Args:
        score (pd.DataFrame): 日付 × 銘柄のスコア。
        controls (dict[str, pd.DataFrame]): 名前 → 日付 × 銘柄の連続変数（Blom 化して使う）。
        groups (pd.DataFrame | None): 日付 × 銘柄のグループ（セクター等、ダミー化して使う）。
        universe (pd.DataFrame | None): ユニバース（非構成銘柄は NaN）。指定時はユニバース内に限定。
        min_obs (int): 回帰に必要な最小銘柄数。

    Returns:
        pd.DataFrame: 日付 × 銘柄の中立化スコア（回帰に使えなかった銘柄は NaN）。

    Examples:
        >>> idx = [202001]
        >>> s = pd.DataFrame([[4.0, 1.0, 3.0, 2.0, 5.0, 6.0]], index=idx, columns=list("ABCDEF"))
        >>> size = pd.DataFrame([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]], index=idx, columns=list("ABCDEF"))
        >>> out = neutralize(s, {"size": size}, None, min_obs=5)
        >>> out.shape == s.shape and bool(out.notna().all().all())
        True
    """
    cols = score.columns
    dates = score.index
    s = score.copy()
    if universe is not None:
        s = s.where(universe.reindex(index=dates, columns=cols).notna())
    zs = blom_score(s)
    zc = {
        k: blom_score(v.reindex(index=dates, columns=cols)) for k, v in controls.items()
    }
    out = pd.DataFrame(np.nan, index=dates, columns=cols)
    for d in dates:
        y = zs.loc[d]
        X = pd.DataFrame({k: v.loc[d] for k, v in zc.items()}, index=cols)
        if groups is not None:
            g = groups.reindex(index=[d], columns=cols).loc[d]
            dummies = pd.get_dummies(g.dropna().astype(int), prefix="g", dtype=float)
            X = X.join(dummies, how="left")
            X.loc[g.isna(), dummies.columns] = np.nan
        m = y.notna() & X.notna().all(axis=1)
        if m.sum() < min_obs:
            continue
        Xm = np.column_stack([np.ones(int(m.sum())), X[m].to_numpy(dtype=float)])
        ym = y[m].to_numpy(dtype=float)
        beta, *_ = np.linalg.lstsq(Xm, ym, rcond=None)
        out.loc[d, m[m].index] = ym - Xm @ beta
    return blom_score(out)


def build(
    input_root: Path,
    score: str,
    name: str,
    group: str,
    use_size: bool,
    use_sector: bool,
    controls: Sequence[str],
    start: int | None,
    end: int | None,
) -> None:
    """中立化スコアを生成して ``{input_root}/alpha/{group}/{name}/YYYYMM.dat`` に書き出す。

    Args:
        input_root (Path): ``input/msci_india_imi`` などのルート。
        score (str): 中立化するスコア（``alpha/`` からの相対パス）。
        name (str): 出力スコア名。
        group (str): 出力グループ名。
        use_size (bool): log ユニバースウェイト（時価総額に比例）を説明変数に加える。
        use_sector (bool): GICS セクター（``attributes/gics`` の先頭 2 桁）ダミーを加える。
        controls (Sequence[str]): 追加の連続変数（``alpha/`` からの相対パス。例 ``core/vola60``）。
        start (int | None): 開始年月。
        end (int | None): 終了年月。

    Returns:
        None
    """
    store = InputStore(input_root)
    univ = store.universe(start, end)
    s = store.alpha(score, start, end).reindex(columns=univ.columns)
    dates = s.index.intersection(univ.index)
    s = s.loc[dates]
    ctrl: dict[str, pd.DataFrame] = {}
    if use_size:
        ctrl["log_size"] = np.log(univ.where(univ > 0)).loc[dates]
    for c in controls:
        ctrl[c] = store.alpha(c, start, end).reindex(index=dates, columns=univ.columns)
    groups = None
    if use_sector:
        gics = store.alpha("attributes/gics", start, end).reindex(
            index=dates, columns=univ.columns
        )
        groups = (gics // 1_000_000).where(univ.loc[dates].notna())
    out = neutralize(s, ctrl, groups, univ.loc[dates])
    dest = input_root / "alpha" / group / name
    n = 0
    for date, row in out.iterrows():
        v = row.dropna()
        if v.empty:
            continue
        write_score_file(dest / f"{to_int(date)}.dat", name, v)
        n += 1
    spec = ", ".join(
        [
            *(["log 時価総額"] if use_size else []),
            *controls,
            *(["GICS セクターダミー"] if use_sector else []),
        ]
    )
    logger.info(
        "%s: %d ファイル（%d〜%d）、説明変数: %s",
        name,
        n,
        to_int(out.index.min()),
        to_int(out.index.max()),
        spec,
    )
    readme = input_root / "alpha" / group / "README.md"
    line = f"| `{name}` | `{score}` | {spec} | {to_int(out.index.min())}〜{to_int(out.index.max())} |"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        if line not in text:
            readme.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
    else:
        readme.write_text(
            "# 中立化スコア\n\n各月ユニバース内でスコアを Blom 正規化し、説明変数（Blom 化した連続変数 + セクターダミー）に OLS 回帰した残差を再 Blom 化したもの。"
            "`scripts/build_neutralize.py` で生成。\n\n| スコア | 元スコア | 説明変数 | 期間 |\n| --- | --- | --- | --- |\n"
            + line
            + "\n",
            encoding="utf-8",
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。

    Args:
        argv (Sequence[str] | None): 引数リスト。

    Returns:
        argparse.Namespace: 解析結果。

    Examples:
        >>> parse_args(["--score", "x", "--name", "y", "--size"]).size
        True
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input-root", type=Path, default=Path("input") / "msci_india_imi"
    )
    parser.add_argument(
        "--score", required=True, help="中立化するスコア（alpha/ からの相対パス）"
    )
    parser.add_argument("--name", required=True, help="出力スコア名")
    parser.add_argument(
        "--group", default="my_ai_neu", help="出力グループ（alpha/{group}/）"
    )
    parser.add_argument(
        "--size", action="store_true", help="log 時価総額（ユニバースウェイト）で中立化"
    )
    parser.add_argument(
        "--sector", action="store_true", help="GICS セクターダミーで中立化"
    )
    parser.add_argument(
        "--controls", nargs="*", default=[], help="追加の連続変数（例 core/vola60）"
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
    a = parse_args(argv)
    build(
        a.input_root,
        a.score,
        a.name,
        a.group,
        a.size,
        a.sector,
        a.controls,
        a.start,
        a.end,
    )


if __name__ == "__main__":
    main()
