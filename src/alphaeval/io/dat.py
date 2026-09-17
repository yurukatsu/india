"""``dat`` / ``csv`` 形式（``input/template`` の univ / bm / alpha）の読み書き。

- ウェイトファイル（univ / bm）: ``# format = weight`` / ``# date = YYYYMM`` ヘッダー + ``code weight``
- スコアファイル（alpha）: ``#bid {score_name}`` ヘッダー + ``code value``（ヘッダー省略可）

区切りは ``dat`` が空白、``csv`` がカンマ。gzip 圧縮（``.gz``）に対応する。
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class WeightFile:
    """ウェイトファイルの内容。

    Attributes:
        date (int | None): ヘッダーの ``date``（無ければ ``None``）。
        format (str | None): ヘッダーの ``format``（無ければ ``None``）。
        weights (pd.Series): 銘柄コードをインデックスとするウェイト。
    """

    date: int | None
    format: str | None
    weights: pd.Series


def _read_text(path: Path) -> str:
    """ファイルをテキストとして読む（``.gz`` は展開）。

    Args:
        path (Path): ファイルパス。

    Returns:
        str: ファイル内容。
    """
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read()
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str, use_gzip: bool) -> Path:
    """テキストを書き出す（``use_gzip`` なら ``.gz`` を付与して圧縮）。

    Args:
        path (Path): 出力先。
        text (str): 内容。
        use_gzip (bool): gzip 圧縮するか。

    Returns:
        Path: 実際に書き出したパス。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if use_gzip:
        path = path.with_name(path.name + ".gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _separator(path: Path, sample_line: str) -> str:
    """ファイル名と本文からセパレータを決める。

    Args:
        path (Path): ファイルパス。
        sample_line (str): 本文の 1 行目。

    Returns:
        str: ``","`` または ``r"\\s+"``。
    """
    name = path.name[:-3] if path.suffix == ".gz" else path.name
    if name.endswith(".csv") or "," in sample_line:
        return ","
    return r"\s+"


def _split_header(text: str) -> tuple[list[str], list[str]]:
    """``#`` で始まる行（ヘッダー）と本文行に分ける。

    Args:
        text (str): ファイル内容。

    Returns:
        tuple[list[str], list[str]]: ``(ヘッダー行, 本文行)``。空行は除外。
    """
    headers: list[str] = []
    body: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            headers.append(stripped)
        else:
            body.append(stripped)
    return headers, body


def _parse_body(path: Path, body: list[str], value_name: str) -> pd.Series:
    """本文行を ``code -> value`` の Series にする。

    Args:
        path (Path): ファイルパス（セパレータ判定・エラーメッセージ用）。
        body (list[str]): 本文行。
        value_name (str): Series の名前。

    Returns:
        pd.Series: 銘柄コードをインデックスとする float Series。

    Raises:
        ValueError: 銘柄コードが重複している場合。
    """
    if not body:
        return pd.Series(dtype=float, name=value_name)
    sep = _separator(path, body[0])
    df = pd.read_csv(
        io.StringIO("\n".join(body)),
        sep=sep,
        header=None,
        names=["code", "value"],
        usecols=[0, 1],
        dtype={"code": str},
        engine="python",
        skipinitialspace=True,
    )
    df["code"] = df["code"].str.strip()
    if df["code"].duplicated().any():
        dups = df.loc[df["code"].duplicated(), "code"].unique()[:5].tolist()
        raise ValueError(f"{path}: 銘柄コードが重複しています（例: {dups}）")
    values = pd.to_numeric(df["value"], errors="coerce")
    return pd.Series(
        values.to_numpy(dtype=float), index=df["code"].to_numpy(), name=value_name
    )


def read_weight_file(path: str | Path) -> WeightFile:
    """ウェイトファイル（univ / bm）を読み込む。

    Args:
        path (str | Path): ``YYYYMM.dat`` 等のパス（``.gz`` 可）。

    Returns:
        WeightFile: ヘッダー情報とウェイト。

    Examples:
        >>> import tempfile
        >>> p = Path(tempfile.mkdtemp()) / "202012.dat"
        >>> _ = p.write_text("# format = weight\\n# date = 202012\\nINDAAA1 0.6\\nINDAAB1 0.4\\n")
        >>> wf = read_weight_file(p)
        >>> wf.date, wf.format, wf.weights.tolist()
        (202012, 'weight', [0.6, 0.4])
    """
    path = Path(path)
    headers, body = _split_header(_read_text(path))
    meta: dict[str, str] = {}
    for line in headers:
        content = line.lstrip("#").strip()
        if "=" in content:
            key, _, value = content.partition("=")
            meta[key.strip().lower()] = value.strip()
    date = int(meta["date"]) if meta.get("date", "").isdigit() else None
    weights = _parse_body(path, body, "weight")
    return WeightFile(date=date, format=meta.get("format"), weights=weights)


def read_score_file(path: str | Path, default_name: str = "alpha") -> pd.Series:
    """スコアファイル（alpha）を読み込む。

    ヘッダー ``#bid {score_name}`` があれば Series の名前に用いる。
    csv でヘッダーが省略されている場合は ``default_name`` を使う。

    Args:
        path (str | Path): ``YYYYMM.dat`` / ``YYYYMM.csv`` 等のパス（``.gz`` 可）。
        default_name (str): ヘッダーが無い場合のスコア名。

    Returns:
        pd.Series: 銘柄コードをインデックスとするスコア（名前 = スコア名）。

    Examples:
        >>> import tempfile
        >>> p = Path(tempfile.mkdtemp()) / "202012.dat"
        >>> _ = p.write_text("#bid bp_est\\nINDAAA1 0.26\\nINDAAB1 0.05\\n")
        >>> s = read_score_file(p)
        >>> s.name, s.tolist()
        ('bp_est', [0.26, 0.05])
    """
    path = Path(path)
    headers, body = _split_header(_read_text(path))
    name = default_name
    for line in headers:
        parts = line.lstrip("#").replace(",", " ").split()
        if len(parts) >= 2:
            name = parts[1]
            break
    return _parse_body(path, body, name)


def write_weight_file(
    path: str | Path,
    date: int,
    weights: pd.Series,
    fmt: str = "weight",
    use_gzip: bool = False,
) -> Path:
    """ウェイトファイル（univ / bm）を書き出す。

    Args:
        path (str | Path): 出力先（``YYYYMM.dat``）。
        date (int): ヘッダーに書く基準日。
        weights (pd.Series): 銘柄コードをインデックスとするウェイト。NaN は除外。
        fmt (str): ヘッダーの ``format``。
        use_gzip (bool): gzip 圧縮するか。

    Returns:
        Path: 実際に書き出したパス。

    Examples:
        >>> import tempfile
        >>> p = Path(tempfile.mkdtemp()) / "202012.dat"
        >>> out = write_weight_file(p, 202012, pd.Series({"INDAAA1": 0.6, "INDAAB1": 0.4}))
        >>> read_weight_file(out).weights.sum()
        1.0
    """
    weights = weights.dropna()
    lines = [f"# format = {fmt}", f"# date = {date}"]
    lines.extend(f"{code} {value:.15E}" for code, value in weights.items())
    return _write_text(Path(path), "\n".join(lines) + "\n", use_gzip)


def write_score_file(
    path: str | Path, name: str, scores: pd.Series, use_gzip: bool = False
) -> Path:
    """スコアファイル（alpha）を書き出す。NaN は除外する。

    Args:
        path (str | Path): 出力先（``YYYYMM.dat`` または ``YYYYMM.csv``）。
        name (str): スコア名（ヘッダー ``#bid {name}``）。
        scores (pd.Series): 銘柄コードをインデックスとするスコア。
        use_gzip (bool): gzip 圧縮するか。

    Returns:
        Path: 実際に書き出したパス。

    Examples:
        >>> import tempfile
        >>> p = Path(tempfile.mkdtemp()) / "202012.dat"
        >>> out = write_score_file(p, "s", pd.Series({"INDAAA1": 1.0, "INDAAB1": float("nan")}))
        >>> read_score_file(out).tolist()
        [1.0]
    """
    path = Path(path)
    scores = scores.dropna()
    sep = "," if path.name.endswith((".csv", ".csv.gz")) else " "
    lines = [f"#bid{sep}{name}"]
    lines.extend(f"{code}{sep}{value!r}" for code, value in scores.items())
    return _write_text(path, "\n".join(lines) + "\n", use_gzip)
