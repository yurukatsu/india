"""最適化システム（bax / gbax）の出力ディレクトリを読むリーダー。

対象ファイル（``{dir}/`` 直下、日付は ``YYYYMM`` または ``YYYYMMDD``）:

| ファイル | 内容 | 主な列 |
| --- | --- | --- |
| ``A{date}.dat`` | 特性値（リスクインデックス・業種・国・通貨のエクスポージャー等） | ``opt`` ``bm`` ``init`` ``opt_active`` ``opt_long`` ``opt_short`` |
| ``O{date}.dat`` | 最適化前後の保有（非保有は載らない）・BM ウェイト・アルファ・銘柄別上下限（%） | ``wgt_o`` ``wgt_b`` ``wgt_i`` ``alpha`` ``cs_wgt_l`` ``cs_wgt_u`` |
| ``S{date}.dat`` | 目的関数・ポートフォリオアルファ・推定 TE（``omega``、%）・取引コスト | ``opt_obj`` ``alpha`` ``omega`` ``tr_cost`` ``h_tr_cost`` ``br_cost`` |
| ``me_{date}.dat`` | 前月末のアクティブウェイト（%）と当月の配当込みリターン（%） | ``prev_awgt`` ``rtn`` |
| ``op_{date}.dat`` | 最適化後の株数と株価（ヘッダーに ``nav``） | ``shares`` ``price`` |
| ``st_prfm.dat`` | ポートフォリオ・BM・超過リターン、取引コスト、回転率の時系列（%） | ``bm_rtn`` ``fund_rtn`` ``fund_ex_rtn`` ``tr_cost`` ``rot`` ``w_cash`` |
| ``st_objv.dat`` | ``S`` の時系列 | 同上 |
| ``st_trad.dat`` | 回転率と取引コストの時系列 | ``rot`` ``tr_cost`` |
| ``bc.txt`` | 実行パラメータ（``key = value``） | |

単位の規約: このモジュールが返す値は、**ウェイト・リターン・回転率・コストをすべて小数**に変換する
（ファイルは %）。``omega``（推定 TE、%）も小数に変換する。アルファスコアや目的関数値はそのまま。

確認済みの関係（``bax/output/v0``）:

- ``me_{t}.prev_awgt`` は ``O{t-1}`` の ``wgt_o - wgt_b`` に一致し、BM のみ保有（非保有）の銘柄も含む
- ``st_prfm.fund_ex_rtn(t) = Σ_i prev_awgt_i × rtn_i − tr_cost(t)``（取引コスト控除後の超過リターン）
- ``st_prfm.fund_rtn = bm_rtn + fund_ex_rtn``
- ``op`` の ``shares × price / nav`` は ``O`` の ``wgt_o`` に一致（微小株数の残差は合計 0.01% 程度）
"""

from __future__ import annotations

import io
import re
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from alphaeval.bax.params import describe_params, optimization_settings
from alphaeval.dates import to_timestamp

#: 現金を表すコード（``USACURR`` など通貨コード、または ``00000``）
CASH_PATTERN = re.compile(r"(?:CURR$|^0+$)")

#: ``A`` ファイルの行名の接頭辞 → グループ名
ATTRIBUTE_GROUPS = {"R": "risk_index", "I": "industry", "C": "country", "c": "currency"}

_DATE_RE = re.compile(r"^(?P<prefix>[A-Za-z]+_?)(?P<date>\d{6}|\d{8})\.dat$")


def read_table(path: str | Path, percent_columns: tuple[str, ...] = ()) -> pd.DataFrame:
    """``#`` 始まりのヘッダー行を持つ空白区切り表を読む。

    先頭の ``# key = value`` 行（メタ情報）は読み飛ばし、``#col1 col2 ...`` 行を列名にする。
    ``NA`` は欠損。

    Args:
        path (str | Path): ファイルパス。
        percent_columns (tuple[str, ...]): 100 で割って小数に変換する列名。

    Returns:
        pd.DataFrame: 読み込んだ表。

    Examples:
        >>> import tempfile
        >>> p = Path(tempfile.mkdtemp()) / "x.dat"
        >>> _ = p.write_text("# nav = 1\\n#code  wgt\\nAAA  50\\nBBB  NA\\n")
        >>> read_table(p, percent_columns=("wgt",))
          code  wgt
        0  AAA  0.5
        1  BBB  NaN
    """
    header: list[str] | None = None
    body: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                content = stripped.lstrip("#").strip()
                if "=" in content and header is None:
                    continue
                if header is None:
                    header = content.split()
                continue
            body.append(stripped)
    if header is None:
        raise ValueError(f"{path}: ヘッダー行（#col ...）がありません")
    if not body:
        return pd.DataFrame(columns=header)
    df = pd.read_csv(
        io.StringIO("\n".join(body)),
        sep=r"\s+",
        header=None,
        names=header,
        na_values=["NA", "nan"],
        engine="python",
    )
    for col in percent_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0
    return df


def read_meta(path: str | Path) -> dict[str, str]:
    """ファイル先頭の ``# key = value`` 行を辞書で返す。

    Args:
        path (str | Path): ファイルパス。

    Returns:
        dict[str, str]: メタ情報（例: ``{"format": "shares", "nav": "1.00185e+08"}``）。

    Examples:
        >>> import tempfile
        >>> p = Path(tempfile.mkdtemp()) / "x.dat"
        >>> _ = p.write_text("# format = shares\\n# nav = 1e8\\n#code shares\\nAAA 1\\n")
        >>> read_meta(p)["nav"]
        '1e8'
    """
    meta: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped.startswith("#"):
                break
            content = stripped.lstrip("#").strip()
            if "=" in content:
                key, _, value = content.partition("=")
                meta[key.strip()] = value.strip()
    return meta


def read_params(path: str | Path) -> dict[str, str]:
    """``bc.txt`` 形式（``key = value``、``#`` はコメント）を辞書で返す。

    Args:
        path (str | Path): ファイルパス。

    Returns:
        dict[str, str]: パラメータ。

    Examples:
        >>> import tempfile
        >>> p = Path(tempfile.mkdtemp()) / "bc.txt"
        >>> _ = p.write_text("# gbax\\ntarget_omega = 5\\ncs_rot = 0.15\\n")
        >>> read_params(p)
        {'target_omega': '5', 'cs_rot': '0.15'}
    """
    params: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            params[key.strip()] = value.strip()
    return params


def is_cash(codes: pd.Series | pd.Index) -> np.ndarray:
    """現金コードかどうかの bool 配列を返す。

    Args:
        codes (pd.Series | pd.Index): 銘柄コード。

    Returns:
        np.ndarray: 現金なら True。

    Examples:
        >>> is_cash(pd.Series(["USACURR", "INDAAA1", "00000"])).tolist()
        [True, False, True]
    """
    return pd.Series(codes).astype(str).str.contains(CASH_PATTERN).to_numpy()


class BaxOutput:
    """bax 出力ディレクトリへのアクセサ。

    Args:
        directory (str | Path): ``A*.dat`` ``O*.dat`` ``st_prfm.dat`` 等を含むディレクトリ。

    Examples:
        >>> out = BaxOutput("bax/output/v0")  # doctest: +SKIP
        >>> out.rebalance_dates[:2]  # doctest: +SKIP
        DatetimeIndex(['2011-01-31', '2011-02-28'], ...)
        >>> out.performance()[["bm_rtn", "fund_rtn", "fund_ex_rtn"]].head()  # doctest: +SKIP
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(f"{self.directory} がありません")

    # ------------------------------------------------------------------ ファイル列挙
    def _dated_files(self, prefix: str) -> dict[pd.Timestamp, Path]:
        """``{prefix}{date}.dat`` を日付 → パスで返す。"""
        out: dict[pd.Timestamp, Path] = {}
        for path in self.directory.iterdir():
            m = _DATE_RE.match(path.name)
            if m and m.group("prefix") == prefix:
                out[to_timestamp(int(m.group("date")))] = path
        return dict(sorted(out.items()))

    @cached_property
    def rebalance_dates(self) -> pd.DatetimeIndex:
        """リバランス日（``O`` ファイルの日付）。"""
        return pd.DatetimeIndex(list(self._dated_files("O")))

    @cached_property
    def params(self) -> dict[str, str]:
        """``bc.txt`` のパラメータ（無ければ空）。"""
        path = self.directory / "bc.txt"
        return read_params(path) if path.exists() else {}

    def initial_nav(self) -> float | None:
        """最初の ``op`` ファイルのヘッダーから NAV を返す（無ければ ``None``）。

        ``f_same_nav = 1`` の場合、これは毎回のリバランス時にリセットされる基準 NAV に
        当月のパフォーマンスを反映した値になる（前回リバランス時点の NAV は ``fn_init_fund`` の指定値）。

        Returns:
            float | None: NAV。
        """
        files = self._dated_files("op_")
        if not files:
            return None
        nav = read_meta(next(iter(files.values()))).get("nav")
        try:
            return float(nav) if nav is not None else None
        except ValueError:
            return None

    def settings(self) -> pd.DataFrame:
        """``bc.txt`` から最適化条件（NAV と制約条件）を人が読める表で返す。

        Returns:
            pd.DataFrame: :func:`alphaeval.bax.params.optimization_settings` の戻り値。
        """
        return optimization_settings(self.params)

    def describe_params(self) -> pd.DataFrame:
        """``bc.txt`` の全パラメータを説明・既定値付きで返す。

        Returns:
            pd.DataFrame: :func:`alphaeval.bax.params.describe_params` の戻り値。
        """
        return describe_params(self.params)

    # ------------------------------------------------------------------ 時系列
    def performance(self) -> pd.DataFrame:
        """``st_prfm.dat`` を小数単位で返す。

        Returns:
            pd.DataFrame: 月末 ``DatetimeIndex`` × 列。リターン（``bm_rtn`` ``fund_rtn`` ``fund_ex_rtn``
            ``altbm_rtn`` ``altbm2_rtn``）、コスト（``tr_cost`` ``fixed_fee`` ``tr_fee`` ``br_cost``）、
            回転率 ``rot``（片道）、``w_cash`` はすべて小数。
        """
        df = read_table(
            self.directory / "st_prfm.dat",
            percent_columns=(
                "bm_rtn",
                "fund_rtn",
                "fund_ex_rtn",
                "tr_cost",
                "fixed_fee",
                "tr_fee",
                "br_cost",
                "rot",
                "w_cash",
                "altbm_rtn",
                "altbm2_rtn",
            ),
        )
        df.index = pd.DatetimeIndex([to_timestamp(int(d)) for d in df["month"]])
        return df.drop(columns=["month"]).sort_index()

    def objective(self) -> pd.DataFrame:
        """``st_objv.dat`` を返す（``omega`` ``tr_cost`` ``h_tr_cost`` ``br_cost`` ``altrisk`` ``alt2risk`` は小数）。

        Returns:
            pd.DataFrame: 月末 ``DatetimeIndex`` × 列。``opt_obj``（効用）と ``alpha``（ポートフォリオの
            アルファスコア）はファイルの値のまま。
        """
        df = read_table(
            self.directory / "st_objv.dat",
            percent_columns=(
                "omega",
                "tr_cost",
                "h_tr_cost",
                "br_cost",
                "altrisk",
                "alt2risk",
            ),
        )
        df.index = pd.DatetimeIndex([to_timestamp(int(d)) for d in df["day"]])
        return df.drop(columns=["day"]).sort_index()

    def trades(self) -> pd.DataFrame:
        """``st_trad.dat`` を返す（``rot`` ``tr_cost`` は小数）。

        Returns:
            pd.DataFrame: 月末 ``DatetimeIndex`` × ``rot`` ``tr_cost``。
        """
        df = read_table(
            self.directory / "st_trad.dat", percent_columns=("rot", "tr_cost")
        )
        df.index = pd.DatetimeIndex([to_timestamp(int(d)) for d in df["day"]])
        return df.drop(columns=["day"]).sort_index()

    # ------------------------------------------------------------------ 保有
    def holdings(
        self, date: int | pd.Timestamp, include_cash: bool = False
    ) -> pd.DataFrame:
        """``O{date}.dat`` を返す（ウェイト・上下限は小数）。

        Args:
            date (int | pd.Timestamp): リバランス日。
            include_cash (bool): 現金行を含めるか。

        Returns:
            pd.DataFrame: ``code`` をインデックスとし ``wgt_o`` ``wgt_b`` ``wgt_i`` ``alpha`` ``cs_wgt_l``
            ``cs_wgt_u`` を持つ表。非保有の BM 銘柄は含まれない。
        """
        path = self._dated_files("O")[to_timestamp(date)]
        df = read_table(
            path, percent_columns=("wgt_o", "wgt_b", "wgt_i", "cs_wgt_l", "cs_wgt_u")
        )
        df["code"] = df["code"].astype(str).str.strip()
        if not include_cash:
            df = df.loc[~is_cash(df["code"])]
        return df.set_index("code")

    def holdings_panel(self, column: str = "wgt_o") -> pd.DataFrame:
        """``O`` ファイルの 1 列を 日付 × 銘柄 のワイドパネルで返す（現金除く、非保有は NaN）。

        Args:
            column (str): ``wgt_o`` / ``wgt_b`` / ``wgt_i`` / ``alpha`` / ``cs_wgt_l`` / ``cs_wgt_u``。

        Returns:
            pd.DataFrame: 日付 × 銘柄。
        """
        data = {d: self.holdings(d)[column] for d in self._dated_files("O")}
        panel = pd.DataFrame.from_dict(data, orient="index")
        panel.index = pd.DatetimeIndex(panel.index)
        return panel.sort_index().sort_index(axis=1).astype(float)

    # ------------------------------------------------------------------ 月次（me）
    def monthly(self, date: int | pd.Timestamp) -> pd.DataFrame:
        """``me_{date}.dat`` を返す（``prev_awgt`` ``rtn`` は小数）。

        ``prev_awgt`` は前リバランス日のアクティブウェイト、``rtn`` は ``date`` の月のリターン。

        Args:
            date (int | pd.Timestamp): リターンの月。

        Returns:
            pd.DataFrame: ``code`` をインデックスとし ``prev_awgt`` ``rtn`` を持つ表。
        """
        path = self._dated_files("me_")[to_timestamp(date)]
        df = read_table(path, percent_columns=("prev_awgt", "rtn"))
        df["code"] = df["code"].astype(str).str.strip()
        df = df.loc[~is_cash(df["code"])]
        return df.set_index("code")

    def monthly_panels(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """``me`` ファイル群から ``(アクティブウェイト, リターン)`` のワイドパネルを返す。

        両方とも **リターンの月** をインデックスとする（アクティブウェイトは前月末の値）。
        ``Σ_i awgt_{i,t} × rtn_{i,t}`` が取引コスト控除前の超過リターンになる。

        Returns:
            tuple[pd.DataFrame, pd.DataFrame]: ``(prev_awgt パネル, rtn パネル)``。
        """
        aw, rt = {}, {}
        for d in self._dated_files("me_"):
            m = self.monthly(d)
            aw[d], rt[d] = m["prev_awgt"], m["rtn"]
        a = pd.DataFrame.from_dict(aw, orient="index")
        r = pd.DataFrame.from_dict(rt, orient="index")
        a.index, r.index = pd.DatetimeIndex(a.index), pd.DatetimeIndex(r.index)
        cols = sorted(set(a.columns) | set(r.columns))
        return a.sort_index().reindex(columns=cols).astype(
            float
        ), r.sort_index().reindex(columns=cols).astype(float)

    # ------------------------------------------------------------------ 特性値（A）
    def exposures(self, date: int | pd.Timestamp) -> pd.DataFrame:
        """``A{date}.dat`` を返す。

        Args:
            date (int | pd.Timestamp): リバランス日。

        Returns:
            pd.DataFrame: ``attribute`` をインデックスとし ``opt`` ``bm`` ``init`` ``opt_active``
            ``opt_long`` ``opt_short`` を持つ表。``group`` 列（``risk_index`` / ``industry`` / ``country`` /
            ``currency`` / ``other``）を付加する。値はファイルのまま（業種等はウェイト合計 = 小数、
            ``sum_pos_w`` 等は %）。
        """
        path = self._dated_files("A")[to_timestamp(date)]
        df = read_table(path)
        df["attribute"] = df["attribute"].astype(str).str.strip()
        df["group"] = [self._attribute_group(a) for a in df["attribute"]]
        return df.set_index("attribute")

    @staticmethod
    def _attribute_group(name: str) -> str:
        """行名からグループ名を判定する。"""
        m = re.match(r"^([RICc])\d{2}_", name)
        if m:
            return ATTRIBUTE_GROUPS.get(m.group(1), "other")
        return "other"

    def exposure_panel(
        self, column: str = "opt_active", group: str | None = None
    ) -> pd.DataFrame:
        """``A`` ファイルの 1 列を 日付 × 特性値 のワイドパネルで返す。

        Args:
            column (str): ``opt`` / ``bm`` / ``init`` / ``opt_active`` / ``opt_long`` / ``opt_short``。
            group (str | None): ``risk_index`` / ``industry`` / ``country`` / ``currency`` / ``other`` で絞る。

        Returns:
            pd.DataFrame: 日付 × 特性値。
        """
        data = {}
        for d in self._dated_files("A"):
            df = self.exposures(d)
            if group is not None:
                df = df.loc[df["group"] == group]
            data[d] = df[column]
        panel = pd.DataFrame.from_dict(data, orient="index")
        panel.index = pd.DatetimeIndex(panel.index)
        return panel.sort_index().astype(float)

    # ------------------------------------------------------------------ 株数（op）
    def positions(self, date: int | pd.Timestamp) -> tuple[pd.DataFrame, float]:
        """``op_{date}.dat`` の株数・株価・ウェイトと NAV を返す。

        Args:
            date (int | pd.Timestamp): 日付。

        Returns:
            tuple[pd.DataFrame, float]: ``(code をインデックスとし shares / price / value / weight を持つ表, nav)``。
            ``weight = shares × price / nav``（小数）。現金行は ``is_cash`` で判別できるよう残す。
        """
        path = self._dated_files("op_")[to_timestamp(date)]
        nav = float(read_meta(path).get("nav", "nan"))
        df = read_table(path)
        df["code"] = df["code"].astype(str).str.strip()
        df["value"] = df["shares"] * df["price"]
        df["weight"] = df["value"] / nav
        return df.set_index("code"), nav
