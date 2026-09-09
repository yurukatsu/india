"""``data/`` 配下の生データを ``input/`` のフォーマットへ整形するスクリプト。

``input/template/`` 配下の README に定義されたフォーマットに従い、
``input/msci_india_imi/`` 配下（``--out-dir`` で変更可）に以下を生成する。

- ``univ/YYYYMM.dat``: ユニバース（MSCI India IMI 想定、浮動株調整時価総額ウェイト）
- ``bm/{msci_india,msci_india_imi}/YYYYMM.dat``: ベンチマーク（時価総額ウェイトのプロキシ）
- ``risk_models/GEMLTL/``: BARRA GEMLTL リスクモデル（exposure / factor_covariance /
  factor_return / return / factor_list.csv / return_list.csv）
- ``alpha/{core,ai,alt,cgo,composite,reprisk}/...``: 各種スコア

すべての銘柄コードは BID（``IND`` + 英数字 3 桁 + 数字 1 桁、計 7 桁）であることを検証し、
それ以外のコード（SEDOL / GID 等）が混入していれば例外で停止する。

Examples:
    すべて生成する::

        uv run python scripts/build_input.py all

    ユニバースとベンチマークのみ::

        uv run python scripts/build_input.py univ bm

    アルファのうち core と alt のみ gzip 圧縮で生成する::

        uv run python scripts/build_input.py alpha --alpha-groups core alt --gzip
"""

from __future__ import annotations

import argparse
import calendar
import gzip
import logging
import re
import shutil
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger("build_input")

#: ``factor/core`` / ``universe`` の欠損センチネル閾値。これ以下の値は NaN として扱う。
SENTINEL_THRESHOLD = -1e8

#: リスクモデル名（``barra_factor_list.csv`` の ``fac`` 接頭辞に対応）
RISK_MODEL_NAME = "GEMLTL"

#: 出力先のデフォルト（ユニバース名のディレクトリ）
DEFAULT_OUT_DIR = Path("input") / "msci_india_imi"

#: BID の形式（例: ``INDAAA1``, ``INDB201``）
BID_PATTERN = re.compile(r"^IND[A-Z0-9]{3}[0-9]$")

#: ``barra/rtn`` の ``rtn`` / ``srtn`` の単位（％表記 → 小数への換算係数）
RETURN_SCALE = 1.0 / 100.0

#: ``risk_models/return`` に出力するリターン列とその説明。
#: ``*_usd`` は ``fx.csv``（1USD あたり INR）で ``(1 + r) * fx[t-1] / fx[t] - 1`` として換算する。
RETURN_LIST: list[dict[str, str]] = [
    {
        "id": "rtn",
        "name": "Total Return (INR)",
        "description": "当月トータルリターン、INR 建て、小数表記（barra/rtn の rtn / 100、lag=0）",
    },
    {
        "id": "srtn",
        "name": "Specific Return (INR)",
        "description": (
            "BARRA モデルの固有（残差）リターン、INR 建て、小数表記"
            "（barra/rtn の srtn / 100、lag=0）"
        ),
    },
    {
        "id": "rtn_usd",
        "name": "Total Return (USD)",
        "description": (
            "当月トータルリターン、USD 建て、小数表記。"
            "(1 + rtn) * fx[t-1] / fx[t] - 1（fx = fx.csv の USD/INR レート）"
        ),
    },
    {
        "id": "srtn_usd",
        "name": "Specific Return (USD)",
        "description": (
            "固有リターンを rtn_usd と同じ式で機械的に USD 換算したもの。"
            "(1 + srtn) * fx[t-1] / fx[t] - 1。"
            "固有リターンは本来ニューメレール不変（為替は通貨ファクターに帰属）のため、"
            "リスク分解用途では srtn をそのまま使うことを推奨"
        ),
    },
]

#: 将来リターンとして出力するラグ（月数）。``barra/rtn`` は 1〜12 ヶ月先まで収録。
FORWARD_LAGS: tuple[int, ...] = tuple(range(1, 13))


def _forward_return_list() -> list[dict[str, str]]:
    """将来リターン列（``fwd_*``）の ``return_list`` エントリを生成する。

    ラグ ``k`` の列は「基準月 ``t`` から ``k`` ヶ月後（``t+k`` 月）の **単月** リターン」。
    累積リターンが必要な場合は利用側で ``(1 + fwd_rtn_1m) * ... * (1 + fwd_rtn_km) - 1`` と複利計算する。

    Returns:
        list[dict[str, str]]: ``id`` / ``name`` / ``description`` の辞書リスト。

    Examples:
        >>> [r["id"] for r in _forward_return_list()][:3]
        ['fwd_rtn_1m', 'fwd_rtn_2m', 'fwd_rtn_3m']
        >>> len(_forward_return_list())
        48
    """
    specs = [
        (
            "fwd_rtn",
            "Forward Total Return (INR)",
            "t+{k} 月の単月トータルリターン、INR 建て、小数表記",
        ),
        (
            "fwd_srtn",
            "Forward Specific Return (INR)",
            "t+{k} 月の単月固有リターン、INR 建て、小数表記",
        ),
        (
            "fwd_rtn_usd",
            "Forward Total Return (USD)",
            "t+{k} 月の単月トータルリターン、USD 建て。(1 + fwd_rtn_{k}m) * fx[t+{k}-1] / fx[t+{k}] - 1",
        ),
        (
            "fwd_srtn_usd",
            "Forward Specific Return (USD)",
            "t+{k} 月の単月固有リターンを機械的に USD 換算（srtn_usd と同様の注意が必要）",
        ),
    ]
    out = []
    for prefix, name, desc in specs:
        for k in FORWARD_LAGS:
            out.append(
                {
                    "id": f"{prefix}_{k}m",
                    "name": f"{name} +{k}M",
                    "description": desc.format(k=k)
                    + "。将来月のデータが無い場合（期間末尾・脱落銘柄）は NaN",
                }
            )
    return out


RETURN_LIST.extend(_forward_return_list())

#: ベンチマーク名 → 採用する ``universe.size`` の集合
BENCHMARKS: dict[str, frozenset[int]] = {
    "msci_india": frozenset({1, 2}),
    "msci_india_imi": frozenset({1, 2, 3}),
}

#: ``factor/core`` のキー列（スコアとして出力しない）
CORE_KEY_COLUMNS = ("dateym", "bid", "sedol")

#: ``cgo`` / ``composite`` / ``reprisk`` のキー列
GENERATED_KEY_COLUMNS = ("yyyymm", "bid")

#: 横持ちスコア表として読み込む拡張子
TABLE_SUFFIXES = (".pkl", ".csv")

#: 横持ちスコア表グループ（``data/{name}/YYYYMM.{pkl,csv}`` → ``alpha/{name}/{score}/``）とその説明
GENERATED_GROUPS: dict[str, str] = {
    "cgo": "Capital Gain Overhang（遡及期間別）",
    "composite": "合成スコア v1（スリーブ別 + 等ウェイト合成）",
    "reprisk": "RepRisk Index（RRI、0〜100 の ESG レピュテーションリスク。高いほどリスク大）",
}


#: AI スコアのスコア名（``alpha/ai/{AI_SCORE_NAME}/YYYYMM.dat`` に出力）
AI_SCORE_NAME = "ai_v1"

#: アルファグループ一覧（生成順）
ALPHA_GROUPS = ("core", "ai", "alt", *GENERATED_GROUPS)


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------
def month_end(yyyymm: int) -> int:
    """``YYYYMM`` から月末日 ``YYYYMMDD`` を返す。

    Args:
        yyyymm (int): 年月（例: ``202012``）。

    Returns:
        int: 月末日（例: ``20201231``）。

    Examples:
        >>> month_end(202012)
        20201231
        >>> month_end(202402)
        20240229
    """
    year, month = divmod(yyyymm, 100)
    return yyyymm * 100 + calendar.monthrange(year, month)[1]


def list_month_files(
    directory: Path, suffixes: Sequence[str] = (".pkl",)
) -> list[tuple[int, Path]]:
    """``YYYYMM.{pkl,csv}`` 形式のファイルを年月順に列挙する。

    Args:
        directory (Path): 対象ディレクトリ。
        suffixes (Sequence[str]): 対象とする拡張子（既定は ``.pkl`` のみ）。

    Returns:
        list[tuple[int, Path]]: ``(yyyymm, path)`` のリスト（昇順）。

    Examples:
        >>> list_month_files(Path("data/universe"))[:1]  # doctest: +SKIP
        [(200301, PosixPath('data/universe/200301.pkl'))]
        >>> list_month_files(Path("data/reprisk"), (".csv",))[:1]  # doctest: +SKIP
        [(200701, PosixPath('data/reprisk/200701.csv'))]
    """
    files = []
    for path in sorted(directory.iterdir()):
        stem = path.stem
        if path.suffix in suffixes and len(stem) == 6 and stem.isdigit():
            files.append((int(stem), path))
    return files


def read_table(path: Path) -> pd.DataFrame:
    """拡張子に応じて ``pkl`` / ``csv`` を読み込む。

    Args:
        path (Path): ファイルパス。

    Returns:
        pd.DataFrame: 読み込んだデータフレーム。

    Raises:
        ValueError: 対応していない拡張子の場合。

    Examples:
        >>> read_table(Path("data/reprisk/202607.csv")).columns.tolist()  # doctest: +SKIP
        ['bid', 'repr_current_rri']
    """
    if path.suffix == ".pkl":
        return pd.read_pickle(path)
    if path.suffix == ".csv":
        return pd.read_csv(path, encoding="utf-8-sig")
    raise ValueError(f"{path}: 対応していない拡張子です")


def write_text(path: Path, text: str, use_gzip: bool) -> Path:
    """テキストを書き出す。``use_gzip`` が真なら ``.gz`` を付けて gzip 圧縮する。

    Args:
        path (Path): 出力先パス（拡張子は ``.dat`` 等。gzip 時は ``.gz`` を末尾に付与）。
        text (str): 書き出す内容。
        use_gzip (bool): gzip 圧縮するかどうか。

    Returns:
        Path: 実際に書き出したパス。

    Examples:
        >>> write_text(Path("/tmp/x.dat"), "a 1\\n", use_gzip=False)  # doctest: +SKIP
        PosixPath('/tmp/x.dat')
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if use_gzip:
        path = path.with_name(path.name + ".gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def format_weight_file(
    date: int, codes: Iterable[str], weights: Iterable[float]
) -> str:
    """univ / bm 用の ``weight`` フォーマット文字列を組み立てる。

    Args:
        date (int): 基準日（``YYYYMM`` または ``YYYYMMDD``）。
        codes (Iterable[str]): 銘柄コード（``bid``）。
        weights (Iterable[float]): 保有割合。

    Returns:
        str: ``# format = weight`` ヘッダー付きのテキスト。

    Examples:
        >>> print(format_weight_file(202012, ["INDAAA1"], [1.0]))
        # format = weight
        # date = 202012
        INDAAA1 1.000000000000000E+00
        <BLANKLINE>
    """
    lines = ["# format = weight", f"# date = {date}"]
    lines.extend(f"{code} {weight:.15E}" for code, weight in zip(codes, weights))
    return "\n".join(lines) + "\n"


def format_alpha_file(
    score_name: str, codes: Iterable[str], values: Iterable[float]
) -> str:
    """alpha 用の ``dat`` フォーマット文字列を組み立てる。

    Args:
        score_name (str): スコア名（ヘッダー ``#bid {score_name}`` に使用）。
        codes (Iterable[str]): 銘柄コード（``bid``）。
        values (Iterable[float]): スコア値。

    Returns:
        str: ``#bid {score_name}`` ヘッダー付きのテキスト。

    Examples:
        >>> print(format_alpha_file("alpha", ["INDAAA1", "INDAAB1"], [0.5, -1.0]))
        #bid alpha
        INDAAA1 0.5
        INDAAB1 -1.0
        <BLANKLINE>
    """
    lines = [f"#bid {score_name}"]
    lines.extend(f"{code} {value!r}" for code, value in zip(codes, values))
    return "\n".join(lines) + "\n"


def validate_bids(codes: pd.Series, context: str) -> pd.Series:
    """銘柄コードが BID 形式であることを検証し、文字列化して返す。

    前後の空白は除去する。BID 形式（:data:`BID_PATTERN`）に合致しないコード
    （SEDOL / CUSIP / ISIN / GID 等）が含まれる場合は例外を送出する。

    Args:
        codes (pd.Series): 銘柄コード列。
        context (str): エラーメッセージ用のコンテキスト（ファイル名など）。

    Returns:
        pd.Series: 空白除去済みの文字列 Series（インデックスは入力と同じ）。

    Raises:
        ValueError: BID 形式でないコードが含まれる場合。

    Examples:
        >>> validate_bids(pd.Series(["INDAAA1", " INDB201 "]), "x").tolist()
        ['INDAAA1', 'INDB201']
        >>> validate_bids(pd.Series(["6099626"]), "x")
        Traceback (most recent call last):
        ...
        ValueError: x: BID 形式でないコードが 1 件あります（例: ['6099626']）
    """
    codes = codes.astype(str).str.strip()
    bad = codes[~codes.str.match(BID_PATTERN)]
    if len(bad):
        raise ValueError(
            f"{context}: BID 形式でないコードが {len(bad)} 件あります"
            f"（例: {bad.unique()[:5].tolist()}）"
        )
    return codes


def replace_sentinel(df: pd.DataFrame) -> pd.DataFrame:
    """数値列の欠損センチネル（``-1e9`` 等）を NaN に置換する。

    Args:
        df (pd.DataFrame): 対象データフレーム。

    Returns:
        pd.DataFrame: センチネルを NaN に置換した新しいデータフレーム。

    Examples:
        >>> df = pd.DataFrame({"a": [1.0, -1e9], "b": ["x", "y"]})
        >>> replace_sentinel(df)["a"].isna().tolist()
        [False, True]
    """
    df = df.copy()
    num_cols = df.select_dtypes(include="number").columns
    values = df[num_cols].astype(float)
    df[num_cols] = values.mask(values <= SENTINEL_THRESHOLD, np.nan)
    return df


def write_score_columns(
    df: pd.DataFrame,
    date: int,
    columns: Sequence[str],
    out_dir: Path,
    use_gzip: bool,
) -> int:
    """横持ちスコア表の各列を ``out_dir/{column}/{date}.dat`` へ書き出す。

    NaN の行は除外する（alpha フォーマットは欠損値なしが前提）。全行が NaN の列は
    ファイルを出力しない。

    Args:
        df (pd.DataFrame): ``bid`` 列を持つ横持ちスコア表。
        date (int): 基準日（ファイル名 ``{date}.dat`` に使用）。
        columns (Sequence[str]): 出力するスコア列名。
        out_dir (Path): 出力ルート（この直下にスコア名ディレクトリを作る）。
        use_gzip (bool): gzip 圧縮するかどうか。

    Returns:
        int: 書き出したファイル数。

    Examples:
        >>> df = pd.DataFrame({"bid": ["A", "B"], "s": [1.0, np.nan]})
        >>> write_score_columns(df, 202012, ["s"], Path("/tmp/alpha"), False)  # doctest: +SKIP
        1
    """
    n_written = 0
    bids = validate_bids(df["bid"], f"{out_dir.name} {date}").to_numpy()
    for col in columns:
        values = df[col].to_numpy(dtype=float)
        mask = ~np.isnan(values)
        if not mask.any():
            continue
        text = format_alpha_file(col, bids[mask], values[mask])
        write_text(out_dir / col / f"{date}.dat", text, use_gzip)
        n_written += 1
    return n_written


# ---------------------------------------------------------------------------
# univ / bm
# ---------------------------------------------------------------------------
def load_universe(path: Path) -> pd.DataFrame:
    """``universe/YYYYMM.pkl`` を読み込み、ウェイト計算に必要な列を検証して返す。

    ``cap`` が欠損または非正の銘柄は除外する（ウェイトを定義できないため）。

    Args:
        path (Path): ユニバースファイルのパス。

    Returns:
        pd.DataFrame: ``bid`` / ``size`` / ``cap`` 列を持つデータフレーム。

    Examples:
        >>> load_universe(Path("data/universe/202012.pkl")).columns.tolist()  # doctest: +SKIP
        ['bid', 'size', 'cap']
    """
    df = pd.read_pickle(path)[["bid", "size", "cap"]].copy()
    df["cap"] = df["cap"].astype(float).mask(df["cap"] <= SENTINEL_THRESHOLD, np.nan)
    bad = df["cap"].isna() | (df["cap"] <= 0)
    if bad.any():
        logger.warning("%s: cap が欠損/非正の %d 銘柄を除外", path.name, int(bad.sum()))
        df = df.loc[~bad]
    df["bid"] = validate_bids(df["bid"], str(path))
    if df["bid"].duplicated().any():
        raise ValueError(f"{path}: bid が重複しています")
    return df.reset_index(drop=True)


def build_weight_file(
    df: pd.DataFrame, date: int, sizes: frozenset[int] | None
) -> str | None:
    """ユニバース表から時価総額ウェイトの ``weight`` フォーマット文字列を作る。

    Args:
        df (pd.DataFrame): :func:`load_universe` の戻り値。
        date (int): 基準日（``YYYYMM``）。
        sizes (frozenset[int] | None): 採用する ``size`` 区分。``None`` なら全銘柄。

    Returns:
        str | None: フォーマット済みテキスト。該当銘柄がなければ ``None``。

    Examples:
        >>> df = pd.DataFrame({"bid": ["A", "B"], "size": [1, 3], "cap": [3.0, 1.0]})
        >>> print(build_weight_file(df, 202012, frozenset({1})))
        # format = weight
        # date = 202012
        A 1.000000000000000E+00
        <BLANKLINE>
    """
    if sizes is not None:
        df = df.loc[df["size"].isin(sizes)]
    if df.empty:
        return None
    weights = df["cap"] / df["cap"].sum()
    return format_weight_file(date, df["bid"], weights)


def build_univ(data_dir: Path, out_dir: Path, use_gzip: bool) -> None:
    """``{out_dir}/univ/YYYYMM.dat`` を生成する。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        use_gzip (bool): gzip 圧縮するかどうか。

    Returns:
        None
    """
    dest = out_dir / "univ"
    n = 0
    for date, path in list_month_files(data_dir / "universe"):
        text = build_weight_file(load_universe(path), date, None)
        if text is None:
            logger.warning("univ %d: 銘柄なしのためスキップ", date)
            continue
        write_text(dest / f"{date}.dat", text, use_gzip)
        n += 1
    logger.info("univ: %d ファイルを %s に出力", n, dest)


def build_bm(data_dir: Path, out_dir: Path, use_gzip: bool) -> None:
    """``{out_dir}/bm/{benchmark}/YYYYMM.dat`` を生成する。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        use_gzip (bool): gzip 圧縮するかどうか。

    Returns:
        None
    """
    dest = out_dir / "bm"
    counts = dict.fromkeys(BENCHMARKS, 0)
    for date, path in list_month_files(data_dir / "universe"):
        univ = load_universe(path)
        for name, sizes in BENCHMARKS.items():
            text = build_weight_file(univ, date, sizes)
            if text is None:
                logger.warning("bm %s %d: 銘柄なしのためスキップ", name, date)
                continue
            write_text(dest / name / f"{date}.dat", text, use_gzip)
            counts[name] += 1
    for name, n in counts.items():
        logger.info("bm/%s: %d ファイルを出力", name, n)
    write_bm_list(dest)


def write_bm_list(dest: Path) -> None:
    """``{out_dir}/bm/LIST.md`` を書き出す。

    Args:
        dest (Path): ``{out_dir}/bm`` ディレクトリ。

    Returns:
        None
    """
    lines = [
        "# Benchmark List",
        "",
        "ベンチマーク一覧。いずれも `data/universe` の浮動株調整後時価総額 `cap` を",
        "正規化した **時価総額ウェイトのプロキシ** であり、MSCI 公式のインデックスウェイトではない。",
        "",
        "| ディレクトリ | 想定インデックス | 構成 | 期間 |",
        "| --- | --- | --- | --- |",
        "| `msci_india` | MSCI India (Standard) | `size ∈ {1, 2}`（大型 + 中型） | 200301〜202607 |",
        "| `msci_india_imi` | MSCI India IMI | `size ∈ {1, 2, 3}`（大型 + 中型 + 小型） | 200301〜202607 |",
        "",
        "生成: `uv run python scripts/build_input.py bm`",
        "",
    ]
    (dest / "LIST.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# risk_models
# ---------------------------------------------------------------------------
def build_factor_list(barra_factor_list: Path) -> pd.DataFrame:
    """``barra_factor_list.csv`` を ``risk_models/factor_list.csv`` の形式に変換する。

    Args:
        barra_factor_list (Path): ``data/barra/barra_factor_list.csv`` のパス。

    Returns:
        pd.DataFrame: ``id`` / ``symbol`` / ``name`` / ``description`` / ``group`` /
        ``group_alt_1`` / ``group_alt_2`` 列を持つデータフレーム。

    Examples:
        >>> fl = build_factor_list(Path("data/barra/barra_factor_list.csv"))  # doctest: +SKIP
        >>> fl.iloc[1].to_dict()  # doctest: +SKIP
        {'id': 102, 'symbol': 'BTOP', 'name': 'Book-to-Price', ...}
    """
    src = pd.read_csv(barra_factor_list)
    prefix = f"{RISK_MODEL_NAME}_"
    out = pd.DataFrame(
        {
            "id": src["fcd"].astype(int),
            "symbol": src["fac"].str.strip().str.removeprefix(prefix),
            "name": src["fdname"]
            .str.strip()
            .str.removeprefix("GEM_")
            .str.replace("_", " "),
            "description": src["fac"].str.strip()
            + " (fnum="
            + src["fnum"].astype(str)
            + ")",
            "group": (
                src["fgroup"]
                .str.strip()
                .str.replace(r"^\d+-", "", regex=True)
                .str.replace("_", " ")
            ),
            "group_alt_1": None,
            "group_alt_2": None,
        }
    )
    return out.sort_values("id").reset_index(drop=True)


def build_risk_models(data_dir: Path, out_dir: Path) -> None:
    """``{out_dir}/risk_models/GEMLTL/`` を生成する。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。

    Returns:
        None
    """
    dest = out_dir / "risk_models" / RISK_MODEL_NAME
    barra = data_dir / "barra"

    factor_list = build_factor_list(barra / "barra_factor_list.csv")
    dest.mkdir(parents=True, exist_ok=True)
    factor_list.to_csv(dest / "factor_list.csv", index=False)
    pd.DataFrame(RETURN_LIST).to_csv(dest / "return_list.csv", index=False)
    valid_ids = set(factor_list["id"])

    # exposure
    n = 0
    for date, path in list_month_files(barra / "exp"):
        df = pd.read_pickle(path)
        out = pd.DataFrame(
            {
                "date": date,
                "bid": validate_bids(df["bid"], str(path)),
                "factor_id": df["fcd"].astype(int),
                "value": df["exp"].astype(float),
            }
        )
        unknown = set(out["factor_id"]) - valid_ids
        if unknown:
            logger.warning(
                "exposure %d: factor_list に無い factor_id %s", date, sorted(unknown)
            )
        _write_pickle(out, dest / "exposure" / f"{date}.pkl")
        n += 1
    logger.info("risk_models/exposure: %d ファイル", n)

    # factor_covariance
    n = 0
    for date, path in list_month_files(barra / "fctcov"):
        df = pd.read_pickle(path)
        out = pd.DataFrame(
            {
                "date": date,
                "factor_id_1": df["fcd1"].astype(int),
                "factor_id_2": df["fcd2"].astype(int),
                "value": df["cov"].astype(float),
            }
        )
        _write_pickle(out, dest / "factor_covariance" / f"{date}.pkl")
        n += 1
    logger.info("risk_models/factor_covariance: %d ファイル", n)

    # factor_return
    n = 0
    for date, path in list_month_files(barra / "fctrtn"):
        df = pd.read_pickle(path)
        out = pd.DataFrame(
            {
                "date": date,
                "factor_id": df["fcd"].astype(int),
                "value": df["fctrtn"].astype(float),
            }
        )
        _write_pickle(out, dest / "factor_return" / f"{date}.pkl")
        n += 1
    logger.info("risk_models/factor_return: %d ファイル", n)

    # return（lag 0 = 当月、lag 1〜12 = 将来単月。小数表記、INR / USD）
    n = 0
    fx = load_fx(data_dir / "fx.csv")
    for date, path in list_month_files(barra / "rtn"):
        df = pd.read_pickle(path)
        out = build_return_table(df, date, path, fx)
        _write_pickle(out, dest / "return" / f"{date}.pkl")
        n += 1
    logger.info("risk_models/return: %d ファイル", n)


def load_fx(path: Path) -> pd.Series:
    """``fx.csv`` を読み込み、``yyyymm`` をインデックスとする USD/INR レート Series を返す。

    Args:
        path (Path): ``data/fx.csv`` のパス。

    Returns:
        pd.Series: インデックス ``yyyymm``（int）、値 ``rate``（1USD あたり INR）。

    Examples:
        >>> load_fx(Path("data/fx.csv")).loc[202012]  # doctest: +SKIP
        73.067
    """
    fx = pd.read_csv(path)
    fx = fx.loc[fx["currency"].str.strip() == "INR"]
    fx = fx.set_index(fx["yyyymm"].astype(int))["rate"].astype(float).sort_index()
    if fx.index.duplicated().any():
        raise ValueError(f"{path}: yyyymm が重複しています")
    return fx


def previous_month(yyyymm: int) -> int:
    """前月の ``YYYYMM`` を返す。

    Args:
        yyyymm (int): 年月。

    Returns:
        int: 前月の年月。

    Examples:
        >>> previous_month(202101)
        202012
        >>> previous_month(202006)
        202005
    """
    year, month = divmod(yyyymm, 100)
    return (year - 1) * 100 + 12 if month == 1 else yyyymm - 1


def add_months(yyyymm: int, k: int) -> int:
    """``YYYYMM`` に ``k`` ヶ月を加えた年月を返す。

    Args:
        yyyymm (int): 年月。
        k (int): 加える月数（負も可）。

    Returns:
        int: 加算後の年月。

    Examples:
        >>> add_months(202012, 1)
        202101
        >>> add_months(202012, 12)
        202112
        >>> add_months(202101, -1)
        202012
    """
    year, month = divmod(yyyymm, 100)
    idx = year * 12 + (month - 1) + k
    return (idx // 12) * 100 + idx % 12 + 1


def to_usd_return(
    local_return: pd.Series, date: int, fx: pd.Series, strict: bool = True
) -> pd.Series:
    """現地通貨建てリターン（小数）を USD 建て（小数）へ換算する。

    ``r_usd = (1 + r_local) * fx[t-1] / fx[t] - 1``（``fx`` は 1USD あたりの現地通貨）。

    Args:
        local_return (pd.Series): 現地通貨建てリターン（小数）。
        date (int): リターンが実現した月 ``YYYYMM``。
        fx (pd.Series): :func:`load_fx` の戻り値。
        strict (bool): 為替レートが無い場合に例外を送出するか。``False`` なら全て NaN を返す。

    Returns:
        pd.Series: USD 建てリターン（小数）。

    Raises:
        KeyError: ``strict=True`` で当月または前月の為替レートが無い場合。

    Examples:
        >>> fx = pd.Series({202011: 74.056, 202012: 73.067})
        >>> round(to_usd_return(pd.Series([0.02875946]), 202012, fx).iloc[0], 6)
        0.042684
        >>> to_usd_return(pd.Series([0.01]), 202101, fx, strict=False).isna().all()
        True
    """
    prev = previous_month(date)
    if date not in fx.index or prev not in fx.index:
        if strict:
            raise KeyError(f"fx.csv に {prev} または {date} のレートがありません")
        return pd.Series(np.nan, index=local_return.index, dtype=float)
    return (1.0 + local_return) * (fx.loc[prev] / fx.loc[date]) - 1.0


def build_return_table(
    df: pd.DataFrame, date: int, path: Path, fx: pd.Series
) -> pd.DataFrame:
    """``barra/rtn`` の 1 年月ファイルから ``risk_models/return`` のテーブルを作る。

    ``lag == 0`` を当月リターン（``rtn`` / ``srtn`` / ``*_usd``）、``lag == k``（1〜12）を
    将来単月リターン（``fwd_*_{k}m``）として横持ちにする。行は ``lag == 0`` を持つ銘柄。
    将来月の行が無い銘柄・期間は NaN。

    Args:
        df (pd.DataFrame): ``barra/rtn/YYYYMM.pkl`` の全行（``bid`` / ``lag`` / ``yyyymm`` /
            ``rtn`` / ``srtn`` 列）。
        date (int): 基準月 ``YYYYMM``（ファイル名）。
        path (Path): 元ファイルのパス（エラーメッセージ用）。
        fx (pd.Series): :func:`load_fx` の戻り値。

    Returns:
        pd.DataFrame: ``date`` / ``bid`` に続き :data:`RETURN_LIST` の ``id`` 順の列。

    Raises:
        ValueError: ``lag`` と ``yyyymm`` の整合が取れない、または ``(bid, lag)`` が重複する場合。

    Examples:
        >>> df = pd.DataFrame(
        ...     {
        ...         "bid": ["INDAAA1", "INDAAA1"],
        ...         "lag": [0, 1],
        ...         "yyyymm": [202012, 202101],
        ...         "rtn": [2.875946, 1.0],
        ...         "srtn": [1.0, 0.5],
        ...     }
        ... )
        >>> fx = pd.Series({202011: 74.056, 202012: 73.067, 202101: 73.0})
        >>> t = build_return_table(df, 202012, Path("x"), fx)
        >>> t.columns.tolist()[:6]
        ['date', 'bid', 'rtn', 'srtn', 'rtn_usd', 'srtn_usd']
        >>> round(t.loc[0, "rtn_usd"], 6), t.loc[0, "fwd_rtn_1m"], t.loc[0, "fwd_srtn_1m"]
        (0.042684, 0.01, 0.005)
        >>> bool(np.isnan(t.loc[0, "fwd_rtn_2m"]))
        True
    """
    df = df.copy()
    df["bid"] = validate_bids(df["bid"], str(path))
    if df.duplicated(["bid", "lag"]).any():
        raise ValueError(f"{path}: (bid, lag) が重複しています")
    lag = df["lag"].astype(int)
    expected_ym = lag.map(lambda k: add_months(date, k))
    if (df["yyyymm"].astype(int) != expected_ym).any():
        raise ValueError(f"{path}: lag と yyyymm が整合しません")

    rtn_w = (df.pivot(index="bid", columns="lag", values="rtn") * RETURN_SCALE).astype(
        float
    )
    srtn_w = (
        df.pivot(index="bid", columns="lag", values="srtn") * RETURN_SCALE
    ).astype(float)
    bids = rtn_w.index[rtn_w[0].notna()] if 0 in rtn_w.columns else rtn_w.index[:0]
    rtn_w = rtn_w.reindex(index=bids, columns=[0, *FORWARD_LAGS])
    srtn_w = srtn_w.reindex(index=bids, columns=[0, *FORWARD_LAGS])

    cols: dict[str, np.ndarray] = {
        "rtn": rtn_w[0].to_numpy(),
        "srtn": srtn_w[0].to_numpy(),
        "rtn_usd": to_usd_return(rtn_w[0], date, fx).to_numpy(),
        "srtn_usd": to_usd_return(srtn_w[0], date, fx).to_numpy(),
    }
    for k in FORWARD_LAGS:
        cols[f"fwd_rtn_{k}m"] = rtn_w[k].to_numpy()
    for k in FORWARD_LAGS:
        cols[f"fwd_srtn_{k}m"] = srtn_w[k].to_numpy()
    for k in FORWARD_LAGS:
        cols[f"fwd_rtn_usd_{k}m"] = to_usd_return(
            rtn_w[k], add_months(date, k), fx, strict=False
        ).to_numpy()
    for k in FORWARD_LAGS:
        cols[f"fwd_srtn_usd_{k}m"] = to_usd_return(
            srtn_w[k], add_months(date, k), fx, strict=False
        ).to_numpy()

    out = pd.DataFrame({"date": date, "bid": bids.to_numpy(), **cols})
    expected = [r["id"] for r in RETURN_LIST]
    if [c for c in out.columns if c not in ("date", "bid")] != expected:
        raise RuntimeError("RETURN_LIST と出力列が一致しません")
    return out


def _write_pickle(df: pd.DataFrame, path: Path) -> None:
    """データフレームを pickle で書き出す（親ディレクトリを自動作成）。

    Args:
        df (pd.DataFrame): 書き出すデータフレーム。
        path (Path): 出力先パス。

    Returns:
        None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index(drop=True).to_pickle(path)


# ---------------------------------------------------------------------------
# alpha
# ---------------------------------------------------------------------------
def _alpha_core_one(args: tuple[int, Path, Path, bool]) -> int:
    """``factor/core`` の 1 年月分をスコアごとの ``dat`` に展開する（ワーカー関数）。

    Args:
        args (tuple[int, Path, Path, bool]): ``(date, src_path, dest_dir, use_gzip)``。

    Returns:
        int: 書き出したファイル数。
    """
    date, path, dest, use_gzip = args
    df = replace_sentinel(pd.read_pickle(path))
    if (df["dateym"] != date).any():
        raise ValueError(f"{path}: dateym がファイル名と一致しません")
    if df["bid"].duplicated().any():
        raise ValueError(f"{path}: bid が重複しています")
    cols = [c for c in df.columns if c not in CORE_KEY_COLUMNS]
    return write_score_columns(df, date, cols, dest, use_gzip)


def _alpha_generated_one(args: tuple[int, Path, Path, bool]) -> int:
    """横持ちスコア表（``cgo`` / ``composite`` / ``reprisk``）の 1 年月分を展開する（ワーカー関数）。

    ``yyyymm`` 列があればファイル名との整合を検証する（無ければファイル名を基準日とする）。
    欠損センチネル（``-1e9`` 等）は NaN として除外する。

    Args:
        args (tuple[int, Path, Path, bool]): ``(date, src_path, dest_dir, use_gzip)``。

    Returns:
        int: 書き出したファイル数。
    """
    date, path, dest, use_gzip = args
    df = read_table(path)
    if "yyyymm" in df.columns and (df["yyyymm"].astype(int) != date).any():
        raise ValueError(f"{path}: yyyymm がファイル名と一致しません")
    if df["bid"].duplicated().any():
        raise ValueError(f"{path}: bid が重複しています")
    cols = [c for c in df.columns if c not in GENERATED_KEY_COLUMNS]
    return write_score_columns(replace_sentinel(df), date, cols, dest, use_gzip)


def _run_parallel(func, jobs: list[tuple], workers: int) -> int:
    """ワーカー関数をジョブリストに並列適用し、戻り値の合計を返す。

    Args:
        func: ワーカー関数（引数タプル 1 つを受け取り int を返す）。
        jobs (list[tuple]): 引数タプルのリスト。
        workers (int): 並列数。1 以下なら逐次実行。

    Returns:
        int: 各ジョブの戻り値の合計。
    """
    if workers <= 1:
        return sum(func(job) for job in jobs)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return sum(ex.map(func, jobs, chunksize=4))


def build_alpha_core(
    data_dir: Path, out_dir: Path, use_gzip: bool, workers: int
) -> None:
    """``{out_dir}/alpha/core/{factor}/YYYYMM.dat`` を生成する。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        use_gzip (bool): gzip 圧縮するかどうか。
        workers (int): 並列数。

    Returns:
        None
    """
    dest = out_dir / "alpha" / "core"
    jobs = [
        (d, p, dest, use_gzip)
        for d, p in list_month_files(data_dir / "factor" / "core")
    ]
    n = _run_parallel(_alpha_core_one, jobs, workers)
    logger.info("alpha/core: %d ファイル（%d 年月）", n, len(jobs))


def build_alpha_generated(
    name: str, data_dir: Path, out_dir: Path, use_gzip: bool, workers: int
) -> None:
    """``{out_dir}/alpha/{name}/{score}/YYYYMM.dat`` を ``data/{name}`` から生成する。

    ``cgo`` / ``composite`` / ``reprisk`` のような「(``yyyymm``) + ``bid`` + スコア列」の
    横持ちデータ向け。``YYYYMM.pkl`` / ``YYYYMM.csv`` の両方に対応。

    Args:
        name (str): データ名（``cgo`` / ``composite`` / ``reprisk``）。
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        use_gzip (bool): gzip 圧縮するかどうか。
        workers (int): 並列数。

    Returns:
        None
    """
    src = data_dir / name
    if not src.is_dir():
        logger.warning("alpha/%s: %s が無いためスキップ", name, src)
        return
    dest = out_dir / "alpha" / name
    jobs = [(d, p, dest, use_gzip) for d, p in list_month_files(src, TABLE_SUFFIXES)]
    n = _run_parallel(_alpha_generated_one, jobs, workers)
    logger.info("alpha/%s: %d ファイル（%d 年月）", name, n, len(jobs))


def build_alpha_ai(data_dir: Path, out_dir: Path, use_gzip: bool) -> None:
    """``{out_dir}/alpha/ai/{AI_SCORE_NAME}/YYYYMM.dat`` を日次 AI スコアの月末スナップショットから生成する。

    各月ファイル内の最終営業日（``yyyymmdd`` 最大）の値を採用する。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        use_gzip (bool): gzip 圧縮するかどうか。

    Returns:
        None
    """
    dest = out_dir / "alpha" / "ai" / AI_SCORE_NAME
    n = 0
    for date, path in list_month_files(data_dir / "factor" / "ai"):
        df = pd.read_pickle(path)
        df = df.loc[df["yyyymmdd"] <= month_end(date)]
        if df.empty:
            logger.warning("alpha/ai %d: 月内データなしのためスキップ", date)
            continue
        last = df.loc[df["yyyymmdd"] == df["yyyymmdd"].max()]
        last = last.dropna(subset=["ai"]).drop_duplicates("bid", keep="last")
        bids = validate_bids(last["bid"], str(path)).to_numpy()
        text = format_alpha_file(AI_SCORE_NAME, bids, last["ai"].to_numpy(dtype=float))
        write_text(dest / f"{date}.dat", text, use_gzip)
        n += 1
    logger.info("alpha/ai/%s: %d ファイル", AI_SCORE_NAME, n)


def load_alt_factor_list(data_dir: Path) -> pd.DataFrame:
    """``factor/alt/factor_list.csv`` を読み込み、実データが存在する factor_id に絞って返す。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。

    Returns:
        pd.DataFrame: ``factor_id`` / ``factor_name`` / ``dir_name`` などを持つデータフレーム。
        ``dir_name`` は ``{factor_id}_{factor_name}`` 形式（名前の重複を避けるため）。

    Examples:
        >>> load_alt_factor_list(Path("data")).dir_name.head(2).tolist()  # doctest: +SKIP
        ['1_overall_rating', '2_culture_and_values']
    """
    alt_dir = data_dir / "factor" / "alt"
    ids = sorted(
        int(p.name) for p in alt_dir.iterdir() if p.is_dir() and p.name.isdigit()
    )
    fl = pd.read_csv(alt_dir / "factor_list.csv", encoding="utf-8-sig")
    fl["factor_name"] = fl["factor_name"].astype(str).str.strip()
    fl = fl.loc[fl["factor_id"].isin(ids)].copy()
    missing = set(ids) - set(fl["factor_id"])
    if missing:
        logger.warning(
            "alt: factor_list.csv に定義の無い factor_id %s", sorted(missing)
        )
        extra = pd.DataFrame({"factor_id": sorted(missing), "factor_name": "unknown"})
        fl = pd.concat([fl, extra], ignore_index=True)
    fl["dir_name"] = fl["factor_id"].astype(str) + "_" + fl["factor_name"]
    return fl.sort_values("factor_id").reset_index(drop=True)


def _alpha_alt_one(args: tuple[int, str, Path, Path, bool]) -> tuple[int, int]:
    """alt ファクター 1 本分の全年月を ``dat`` に展開する（ワーカー関数）。

    出力月 ``M`` のファイルには、``M`` のソースファイルのうち
    ``effective_yyyymmdd <= 月末(M)`` の行だけを採用する（ルックアヘッド防止）。
    同一 ``bid`` が複数行ある場合は発効日が最新の行を採用する。

    Args:
        args (tuple[int, str, Path, Path, bool]):
            ``(factor_id, dir_name, alt_dir, dest_root, use_gzip)``。

    Returns:
        tuple[int, int]: ``(書き出したファイル数, ルックアヘッドで除外した行数)``。
    """
    factor_id, dir_name, alt_dir, dest_root, use_gzip = args
    dest = dest_root / dir_name
    n_files = 0
    n_dropped = 0
    for date, path in list_month_files(alt_dir / str(factor_id)):
        df = pd.read_pickle(path)
        ok = df["effective_yyyymmdd"] <= month_end(date)
        n_dropped += int((~ok).sum())
        df = df.loc[ok].dropna(subset=["value"])
        df = df.sort_values("effective_yyyymmdd").drop_duplicates("bid", keep="last")
        if df.empty:
            continue
        bids = validate_bids(df["bid"], str(path)).to_numpy()
        text = format_alpha_file(dir_name, bids, df["value"].to_numpy(dtype=float))
        write_text(dest / f"{date}.dat", text, use_gzip)
        n_files += 1
    return n_files, n_dropped


def build_alpha_alt(
    data_dir: Path, out_dir: Path, use_gzip: bool, workers: int
) -> None:
    """``{out_dir}/alpha/alt/{factor_id}_{factor_name}/YYYYMM.dat`` を生成する。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        use_gzip (bool): gzip 圧縮するかどうか。
        workers (int): 並列数。

    Returns:
        None
    """
    alt_dir = data_dir / "factor" / "alt"
    dest = out_dir / "alpha" / "alt"
    fl = load_alt_factor_list(data_dir)
    jobs = [
        (int(r.factor_id), str(r.dir_name), alt_dir, dest, use_gzip)
        for r in fl.itertuples()
    ]
    if workers <= 1:
        results = [_alpha_alt_one(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_alpha_alt_one, jobs))
    n_files = sum(r[0] for r in results)
    n_dropped = sum(r[1] for r in results)
    logger.info("alpha/alt: %d ファイル（%d ファクター）", n_files, len(jobs))
    if n_dropped:
        logger.warning("alpha/alt: 発効日が月末より後の %d 行を除外", n_dropped)


def write_alpha_list(data_dir: Path, out_dir: Path, groups: Sequence[str]) -> None:
    """``{out_dir}/alpha/LIST.md`` を生成する。

    今回生成したグループに限らず、``{out_dir}/alpha/`` に出力が存在するグループを
    すべて記載する（部分生成で一覧が欠けないようにするため）。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        groups (Sequence[str]): 今回生成したアルファグループ名。

    Returns:
        None
    """
    dest = out_dir / "alpha"
    dest.mkdir(parents=True, exist_ok=True)
    groups = [g for g in ALPHA_GROUPS if g in groups or (dest / g).is_dir()]
    lines = [
        "# Alpha List",
        "",
        "`scripts/build_input.py alpha` で `data/` から生成したスコア一覧。",
        "各スコアは `alpha/{group}/{score}/YYYYMM.dat` に格納。",
        "欠損値の行は出力していない（`factor/core` の `-1e9` センチネルは欠損扱い）。",
        "ユニバースによる絞り込みは行っていない。銘柄コードはすべて BID（7 桁）であることを検証済み。",
        "",
        "| グループ | 元データ | 内容 | 基準日の定義 |",
        "| --- | --- | --- | --- |",
        "| `core` | `data/factor/core` | コアファクター 123 本（列ごとに 1 スコア） | ファイル年月 `dateym` |",
        "| `ai` | `data/factor/ai` | AI スコア（月内最終営業日のスナップショット） | ファイル年月 |",
        (
            "| `alt` | `data/factor/alt/{id}` | オルタナティブファクター（`{id}_{name}`） "
            "| ファイル年月（`effective_yyyymmdd` ≤ 月末 のみ採用） |"
        ),
    ]
    lines += [
        f"| `{name}` | `data/{name}` | {desc} | ファイル年月 |"
        for name, desc in GENERATED_GROUPS.items()
    ]
    lines.append("")

    if "core" in groups:
        core_dir = data_dir / "factor" / "core"
        files = list_month_files(core_dir)
        sample = pd.read_pickle(files[-1][1])
        cols = [c for c in sample.columns if c not in CORE_KEY_COLUMNS]
        with open(data_dir / "factor" / "core.yaml", encoding="utf-8") as f:
            styles: dict[str, list[str]] = yaml.safe_load(f)
        col_to_style: dict[str, str] = {}
        for style, members in styles.items():
            for m in members:
                col_to_style[m.lower()] = style
        lines += [
            "## core",
            "",
            (
                f"期間 {files[0][0]}〜{files[-1][0]}。スタイルは `data/factor/core.yaml` に基づく"
                "（`-` はどのスタイルにも属さない補助列）。"
            ),
            "",
            "| スコア | スタイル |",
            "| --- | --- |",
        ]
        lines += [f"| `{c}` | {col_to_style.get(c, '-')} |" for c in cols]
        seasonal = [m for m in styles.get("Seasonality", []) if m.lower() not in cols]
        if seasonal:
            lines += [
                "",
                f"`Seasonality`（{', '.join(seasonal)}）は実データが無いため生成していない。",
            ]
        lines.append("")

    if "ai" in groups:
        files = list_month_files(data_dir / "factor" / "ai")
        lines += [
            "## ai",
            "",
            f"期間 {files[0][0]}〜{files[-1][0]}。スコア名は `{AI_SCORE_NAME}`（`data/factor/ai` の `ai` 列）。",
            "",
        ]

    if "alt" in groups:
        fl = load_alt_factor_list(data_dir)
        lines += [
            "## alt",
            "",
            "| ディレクトリ | factor_id | ファクター名 | 型 | 頻度 | 期間 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in fl.itertuples():
            files = list_month_files(data_dir / "factor" / "alt" / str(r.factor_id))
            period = f"{files[0][0]}〜{files[-1][0]}" if files else "-"
            dtype = getattr(r, "data_type", "-")
            freq = getattr(r, "frequency", "-")
            lines.append(
                f"| `{r.dir_name}` | {r.factor_id} | {r.factor_name} | {dtype} | {freq} | {period} |"
            )
        lines.append("")

    for name in GENERATED_GROUPS:
        if name in groups and (data_dir / name).is_dir():
            files = list_month_files(data_dir / name, TABLE_SUFFIXES)
            sample = read_table(files[-1][1])
            cols = [c for c in sample.columns if c not in GENERATED_KEY_COLUMNS]
            lines += [
                f"## {name}",
                "",
                f"期間 {files[0][0]}〜{files[-1][0]}。スコア: "
                + ", ".join(f"`{c}`" for c in cols),
                "",
            ]

    (dest / "LIST.md").write_text("\n".join(lines), encoding="utf-8")


def build_alpha(
    data_dir: Path, out_dir: Path, use_gzip: bool, workers: int, groups: Sequence[str]
) -> None:
    """``{out_dir}/alpha/`` 配下を生成する。

    Args:
        data_dir (Path): ``data/`` ディレクトリ。
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        use_gzip (bool): gzip 圧縮するかどうか。
        workers (int): 並列数。
        groups (Sequence[str]): 生成するグループ（:data:`ALPHA_GROUPS` の部分集合）。

    Returns:
        None
    """
    if "core" in groups:
        build_alpha_core(data_dir, out_dir, use_gzip, workers)
    if "ai" in groups:
        build_alpha_ai(data_dir, out_dir, use_gzip)
    if "alt" in groups:
        build_alpha_alt(data_dir, out_dir, use_gzip, workers)
    for name in GENERATED_GROUPS:
        if name in groups:
            build_alpha_generated(name, data_dir, out_dir, use_gzip, workers)
    write_alpha_list(data_dir, out_dir, groups)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
TARGETS = ("univ", "bm", "risk", "alpha")


def clean_targets(out_dir: Path, targets: Sequence[str], groups: Sequence[str]) -> None:
    """再生成前に対象ディレクトリを削除する。

    Args:
        out_dir (Path): 出力ルート（既定 ``input/msci_india_imi/``）。
        targets (Sequence[str]): 生成対象（:data:`TARGETS` の部分集合）。
        groups (Sequence[str]): アルファグループ。

    Returns:
        None
    """
    paths: list[Path] = []
    if "univ" in targets:
        paths.append(out_dir / "univ")
    if "bm" in targets:
        paths.extend(out_dir / "bm" / name for name in BENCHMARKS)
    if "risk" in targets:
        paths.append(out_dir / "risk_models" / RISK_MODEL_NAME)
    if "alpha" in targets:
        paths.extend(out_dir / "alpha" / g for g in groups)
    for p in paths:
        if p.exists():
            logger.info("削除: %s", p)
            shutil.rmtree(p)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。

    Args:
        argv (Sequence[str] | None): 引数リスト。``None`` なら ``sys.argv`` を使う。

    Returns:
        argparse.Namespace: 解析結果。

    Examples:
        >>> parse_args(["all", "--gzip"]).gzip
        True
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "targets",
        nargs="+",
        choices=(*TARGETS, "all"),
        help="生成対象。all は univ / bm / risk / alpha のすべて",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data"), help="入力 data ディレクトリ"
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="出力ディレクトリ（この直下に univ / bm / risk_models / alpha を作る）",
    )
    parser.add_argument(
        "--gzip", action="store_true", help="dat ファイルを gzip 圧縮する"
    )
    parser.add_argument("--workers", type=int, default=4, help="alpha 生成の並列数")
    parser.add_argument(
        "--alpha-groups",
        nargs="+",
        choices=ALPHA_GROUPS,
        default=list(ALPHA_GROUPS),
        help="生成するアルファグループ",
    )
    parser.add_argument(
        "--no-clean", action="store_true", help="既存の出力を削除せずに上書きする"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """エントリポイント。

    Args:
        argv (Sequence[str] | None): 引数リスト。``None`` なら ``sys.argv`` を使う。

    Returns:
        None
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = parse_args(argv)
    targets = (
        list(TARGETS) if "all" in args.targets else list(dict.fromkeys(args.targets))
    )
    data_dir: Path = args.data_dir
    out_dir: Path = args.out_dir

    if not args.no_clean:
        clean_targets(out_dir, targets, args.alpha_groups)
    if "univ" in targets:
        build_univ(data_dir, out_dir, args.gzip)
    if "bm" in targets:
        build_bm(data_dir, out_dir, args.gzip)
    if "risk" in targets:
        build_risk_models(data_dir, out_dir)
    if "alpha" in targets:
        build_alpha(data_dir, out_dir, args.gzip, args.workers, args.alpha_groups)
    logger.info("完了")


if __name__ == "__main__":
    main()
