"""``input/template`` 構造のディレクトリから日付 × 銘柄のワイドパネルを読み込む。

想定するディレクトリ構造（``input/template/README.md``）::

    {root}/
    ├── univ/YYYYMM.dat
    ├── bm/{benchmark}/YYYYMM.dat
    ├── alpha/{group}/{score}/YYYYMM.dat
    └── risk_models/{model}/
        ├── exposure/YYYYMM.pkl
        ├── factor_covariance/YYYYMM.pkl
        ├── factor_return/YYYYMM.pkl
        ├── return/YYYYMM.pkl
        ├── factor_list.csv
        └── return_list.csv

返すパネルはすべて ``index = DatetimeIndex``（月末）、``columns = 銘柄コード`` の ``DataFrame``。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from alphaeval.dates import to_timestamp
from alphaeval.io.dat import read_score_file, read_weight_file

_DATE_STEM = re.compile(r"^(\d{6}|\d{8})$")
_DAT_SUFFIXES = (".dat", ".dat.gz", ".csv", ".csv.gz")
_TABLE_SUFFIXES = (".pkl", ".csv", ".csv.gz", ".gz")


def _strip_suffix(name: str, suffixes: Sequence[str]) -> str | None:
    """既知の拡張子を取り除いた語幹を返す（該当しなければ ``None``）。

    Args:
        name (str): ファイル名。
        suffixes (Sequence[str]): 候補拡張子（長いものから照合する）。

    Returns:
        str | None: 語幹。
    """
    for suffix in sorted(suffixes, key=len, reverse=True):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def list_dated_files(
    directory: str | Path,
    suffixes: Sequence[str] = _DAT_SUFFIXES,
    start: int | pd.Timestamp | None = None,
    end: int | pd.Timestamp | None = None,
) -> list[tuple[pd.Timestamp, Path]]:
    """``YYYYMM.*`` / ``YYYYMMDD.*`` 形式のファイルを日付順に列挙する。

    Args:
        directory (str | Path): 対象ディレクトリ。
        suffixes (Sequence[str]): 対象拡張子。
        start (int | pd.Timestamp | None): この日付以降のみ（``YYYYMM`` 等）。
        end (int | pd.Timestamp | None): この日付以前のみ。

    Returns:
        list[tuple[pd.Timestamp, Path]]: ``(日付, パス)`` の昇順リスト。

    Examples:
        >>> list_dated_files("input/msci_india_imi/univ", start=202012, end=202012)  # doctest: +SKIP
        [(Timestamp('2020-12-31 00:00:00'), PosixPath('input/msci_india_imi/univ/202012.dat'))]
    """
    directory = Path(directory)
    lo = to_timestamp(start) if start is not None else None
    hi = to_timestamp(end) if end is not None else None
    out: list[tuple[pd.Timestamp, Path]] = []
    if not directory.is_dir():
        return out
    for path in directory.iterdir():
        stem = _strip_suffix(path.name, suffixes)
        if stem is None or not _DATE_STEM.match(stem):
            continue
        date = to_timestamp(int(stem))
        if (lo is not None and date < lo) or (hi is not None and date > hi):
            continue
        out.append((date, path))
    return sorted(out)


def _to_panel(series_by_date: dict[pd.Timestamp, pd.Series]) -> pd.DataFrame:
    """``{日付: Series(code -> value)}`` をワイドパネルにまとめる。

    Args:
        series_by_date (dict[pd.Timestamp, pd.Series]): 日付ごとの Series。

    Returns:
        pd.DataFrame: 日付 × 銘柄（欠損は NaN）。
    """
    if not series_by_date:
        return pd.DataFrame(index=pd.DatetimeIndex([]), dtype=float)
    panel = pd.DataFrame.from_dict(series_by_date, orient="index")
    panel.index = pd.DatetimeIndex(panel.index)
    return panel.sort_index().sort_index(axis=1).astype(float)


def load_weight_panel(
    directory: str | Path,
    start: int | pd.Timestamp | None = None,
    end: int | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """ウェイトファイル群（univ / bm）をワイドパネルに読み込む。

    Args:
        directory (str | Path): ``YYYYMM.dat`` を含むディレクトリ。
        start (int | pd.Timestamp | None): 開始日（含む）。
        end (int | pd.Timestamp | None): 終了日（含む）。

    Returns:
        pd.DataFrame: 日付 × 銘柄のウェイト。非構成銘柄は NaN。

    Examples:
        >>> load_weight_panel("input/msci_india_imi/univ", 202001, 202012).shape  # doctest: +SKIP
        (12, 372)
    """
    data = {
        d: read_weight_file(p).weights
        for d, p in list_dated_files(directory, _DAT_SUFFIXES, start, end)
    }
    return _to_panel(data)


def load_score_panel(
    directory: str | Path,
    start: int | pd.Timestamp | None = None,
    end: int | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """スコアファイル群（alpha）をワイドパネルに読み込む。

    Args:
        directory (str | Path): ``YYYYMM.dat`` を含むディレクトリ。
        start (int | pd.Timestamp | None): 開始日（含む）。
        end (int | pd.Timestamp | None): 終了日（含む）。

    Returns:
        pd.DataFrame: 日付 × 銘柄のスコア。欠損は NaN。

    Examples:
        >>> load_score_panel("input/msci_india_imi/alpha/core/bp_est", 202001, 202012).shape  # doctest: +SKIP
        (12, 372)
    """
    data = {
        d: read_score_file(p)
        for d, p in list_dated_files(directory, _DAT_SUFFIXES, start, end)
    }
    return _to_panel(data)


def _read_table(path: Path) -> pd.DataFrame:
    """``pkl`` / ``csv`` / ``csv.gz`` を読む。

    Args:
        path (Path): ファイルパス。

    Returns:
        pd.DataFrame: 読み込んだ表。
    """
    if path.name.endswith(".pkl"):
        return pd.read_pickle(path)
    return pd.read_csv(path)


class InputStore:
    """``input/template`` 構造のルートディレクトリへのアクセサ。

    Args:
        root (str | Path): ``univ`` / ``bm`` / ``alpha`` / ``risk_models`` を含むディレクトリ。

    Examples:
        >>> store = InputStore("input/msci_india_imi")  # doctest: +SKIP
        >>> univ = store.universe(201001, 202012)  # doctest: +SKIP
        >>> score = store.alpha("core/bp_est", 201001, 202012)  # doctest: +SKIP
        >>> rtn = store.returns("GEMLTL", "rtn", 201001, 202101)  # doctest: +SKIP
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ univ / bm / alpha
    def universe(
        self,
        start: int | pd.Timestamp | None = None,
        end: int | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """``univ/`` をワイドパネルで返す。

        Args:
            start (int | pd.Timestamp | None): 開始日（含む）。
            end (int | pd.Timestamp | None): 終了日（含む）。

        Returns:
            pd.DataFrame: 日付 × 銘柄のウェイト（非構成銘柄は NaN）。
        """
        return load_weight_panel(self.root / "univ", start, end)

    def benchmark(
        self,
        name: str,
        start: int | pd.Timestamp | None = None,
        end: int | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """``bm/{name}/`` をワイドパネルで返す。

        Args:
            name (str): ベンチマーク名（``bm/`` からの相対パス。階層可）。
            start (int | pd.Timestamp | None): 開始日（含む）。
            end (int | pd.Timestamp | None): 終了日（含む）。

        Returns:
            pd.DataFrame: 日付 × 銘柄のウェイト。
        """
        return load_weight_panel(self.root / "bm" / name, start, end)

    def alpha(
        self,
        name: str,
        start: int | pd.Timestamp | None = None,
        end: int | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """``alpha/{name}/`` をワイドパネルで返す。

        Args:
            name (str): スコア名（``alpha/`` からの相対パス。例 ``core/bp_est``）。
            start (int | pd.Timestamp | None): 開始日（含む）。
            end (int | pd.Timestamp | None): 終了日（含む）。

        Returns:
            pd.DataFrame: 日付 × 銘柄のスコア。
        """
        return load_score_panel(self.root / "alpha" / name, start, end)

    def _leaf_dirs(self, base: Path) -> list[str]:
        """``base`` 以下で日付ファイルを直接含むディレクトリを相対パスで列挙する。

        Args:
            base (Path): 起点ディレクトリ。

        Returns:
            list[str]: 相対パス（``/`` 区切り）の昇順リスト。
        """
        if not base.is_dir():
            return []
        out = []
        for d in sorted(p for p in base.rglob("*") if p.is_dir()):
            if list_dated_files(d, _DAT_SUFFIXES):
                out.append(d.relative_to(base).as_posix())
        if list_dated_files(base, _DAT_SUFFIXES):
            out.insert(0, ".")
        return out

    def list_alphas(self) -> list[str]:
        """``alpha/`` 配下のスコア名（``alpha()`` に渡せる相対パス）を列挙する。

        Returns:
            list[str]: スコア名のリスト。
        """
        return self._leaf_dirs(self.root / "alpha")

    def list_benchmarks(self) -> list[str]:
        """``bm/`` 配下のベンチマーク名を列挙する。

        Returns:
            list[str]: ベンチマーク名のリスト。
        """
        return self._leaf_dirs(self.root / "bm")

    # ------------------------------------------------------------------ risk models
    def list_risk_models(self) -> list[str]:
        """``risk_models/`` 配下のモデル名を列挙する。

        Returns:
            list[str]: モデル名。
        """
        base = self.root / "risk_models"
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    def return_list(self, model: str) -> pd.DataFrame:
        """``risk_models/{model}/return_list.csv`` を返す。

        Args:
            model (str): リスクモデル名。

        Returns:
            pd.DataFrame: ``id`` / ``name`` / ``description`` 列。
        """
        return pd.read_csv(self.root / "risk_models" / model / "return_list.csv")

    def factor_list(self, model: str) -> pd.DataFrame:
        """``risk_models/{model}/factor_list.csv`` を返す。

        Args:
            model (str): リスクモデル名。

        Returns:
            pd.DataFrame: ``id`` / ``symbol`` / ``name`` / ``description`` / ``group`` 列など。
        """
        return pd.read_csv(self.root / "risk_models" / model / "factor_list.csv")

    def returns(
        self,
        model: str,
        column: str,
        start: int | pd.Timestamp | None = None,
        end: int | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """``risk_models/{model}/return/`` の 1 列をワイドパネルで返す。

        ファイルの ``date`` 列（``YYYYMM`` / ``YYYYMMDD``）を日付とし、値はファイルの単位のまま。

        Args:
            model (str): リスクモデル名。
            column (str): ``return_list.csv`` の ``id``（例 ``rtn``）。
            start (int | pd.Timestamp | None): 開始日（含む）。
            end (int | pd.Timestamp | None): 終了日（含む）。

        Returns:
            pd.DataFrame: 日付 × 銘柄のリターン。

        Raises:
            KeyError: ``column`` がファイルに無い場合。
        """
        data: dict[pd.Timestamp, pd.Series] = {}
        directory = self.root / "risk_models" / model / "return"
        for _, path in list_dated_files(directory, _TABLE_SUFFIXES, start, end):
            df = _read_table(path)
            if column not in df.columns:
                raise KeyError(
                    f"{path}: 列 {column!r} がありません（{list(df.columns)}）"
                )
            for date_int, part in df.groupby("date"):
                date = to_timestamp(int(date_int))
                s = pd.Series(
                    part[column].to_numpy(dtype=float), index=part["bid"].astype(str)
                )
                data[date] = s if date not in data else pd.concat([data[date], s])
        return _to_panel(data)

    def exposure(self, model: str, date: int | pd.Timestamp) -> pd.DataFrame:
        """``risk_models/{model}/exposure/`` の 1 日付分を 銘柄 × ファクター で返す。

        Args:
            model (str): リスクモデル名。
            date (int | pd.Timestamp): 日付（``YYYYMM`` 等）。

        Returns:
            pd.DataFrame: 銘柄 × ファクター ID のエクスポージャー（欠損は 0）。
        """
        path = self._dated_path(self.root / "risk_models" / model / "exposure", date)
        df = _read_table(path)
        return df.pivot(index="bid", columns="factor_id", values="value").fillna(0.0)

    def factor_covariance(self, model: str, date: int | pd.Timestamp) -> pd.DataFrame:
        """``risk_models/{model}/factor_covariance/`` の 1 日付分を対称行列で返す。

        上三角のみ格納されている場合は転置を補完する。

        Args:
            model (str): リスクモデル名。
            date (int | pd.Timestamp): 日付。

        Returns:
            pd.DataFrame: ファクター ID × ファクター ID の共分散行列。
        """
        path = self._dated_path(
            self.root / "risk_models" / model / "factor_covariance", date
        )
        df = _read_table(path)
        mat = df.pivot(index="factor_id_1", columns="factor_id_2", values="value")
        ids = sorted(set(mat.index) | set(mat.columns))
        mat = mat.reindex(index=ids, columns=ids)
        return mat.where(mat.notna(), mat.T)

    def factor_return(
        self,
        model: str,
        start: int | pd.Timestamp | None = None,
        end: int | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """``risk_models/{model}/factor_return/`` を 日付 × ファクター で返す。

        Args:
            model (str): リスクモデル名。
            start (int | pd.Timestamp | None): 開始日（含む）。
            end (int | pd.Timestamp | None): 終了日（含む）。

        Returns:
            pd.DataFrame: 日付 × ファクター ID のファクターリターン。
        """
        data: dict[pd.Timestamp, pd.Series] = {}
        directory = self.root / "risk_models" / model / "factor_return"
        for _, path in list_dated_files(directory, _TABLE_SUFFIXES, start, end):
            df = _read_table(path)
            for date_int, part in df.groupby("date"):
                data[to_timestamp(int(date_int))] = pd.Series(
                    part["value"].to_numpy(dtype=float),
                    index=part["factor_id"].to_numpy(),
                )
        return _to_panel(data)

    @staticmethod
    def _dated_path(directory: Path, date: int | pd.Timestamp) -> Path:
        """``directory`` 内で ``date`` に対応するファイルを探す。

        Args:
            directory (Path): 対象ディレクトリ。
            date (int | pd.Timestamp): 日付。

        Returns:
            Path: 見つかったパス。

        Raises:
            FileNotFoundError: 対応するファイルが無い場合。
        """
        ts = to_timestamp(date)
        for d, p in list_dated_files(directory, _TABLE_SUFFIXES, ts, ts):
            if d == ts:
                return p
        raise FileNotFoundError(f"{directory}: {ts.date()} のファイルがありません")
