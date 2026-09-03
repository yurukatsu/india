"""データ読み込みとパネル構築.

タイミング規約（docs/tutorial/00 準拠）:
  - スコア（ファクター値）は時点 t のクロスセクション
  - 評価リターンは区間 (t, t+1] = ``barra/rtn/{t}.pkl`` の ``lag == 1``
"""

from __future__ import annotations

import re
from functools import lru_cache

import numpy as np
import pandas as pd

from .config import BENCHMARK_SIZES, DATA_DIR, SENTINEL_THRESHOLD, UNIVERSE_SIZES

_YM_RE = re.compile(r"^(\d{6})\.pkl$")


def available_yyyymm(kind: str = "factor/core") -> list[int]:
    """``data/{kind}/YYYYMM.pkl`` として存在する年月を昇順で返す."""
    d = DATA_DIR / kind
    return sorted(int(m.group(1)) for p in d.glob("*.pkl") if (m := _YM_RE.match(p.name)))


def drop_sentinel(df: pd.DataFrame, threshold: float = SENTINEL_THRESHOLD) -> pd.DataFrame:
    """欠損センチネル（-1e9 等の巨大負値）を NaN に置換した float 列を返す."""
    out = df.copy()
    num = out.select_dtypes(include=[np.number]).columns
    out[num] = out[num].mask(out[num].to_numpy() <= threshold)
    return out


def next_yyyymm(yyyymm: int, k: int = 1) -> int:
    return int((pd.Period(str(yyyymm), "M") + k).strftime("%Y%m"))


# --- 単月ロード ---------------------------------------------------------------


@lru_cache(maxsize=None)
def _read(kind: str, yyyymm: int) -> pd.DataFrame:
    return pd.read_pickle(DATA_DIR / kind / f"{yyyymm}.pkl")


def load_universe(yyyymm: int) -> pd.DataFrame:
    """ユニバース構成銘柄（bid をインデックス）. センチネルは NaN 化済み."""
    df = drop_sentinel(_read("universe", yyyymm))
    return df.set_index("bid")


def load_core(yyyymm: int, factors: list[str] | None = None) -> pd.DataFrame:
    """コアファクター（bid をインデックス）. センチネルは NaN 化済み."""
    df = _read("factor/core", yyyymm)
    if factors is not None:
        df = df[["bid", *factors]]
    return drop_sentinel(df).set_index("bid")


def load_forward_return(yyyymm: int, lag: int = 1, col: str = "rtn") -> pd.Series:
    """時点 yyyymm を基準とした lag ヶ月先のリターン（小数、現地通貨・配当込み）.

    ``barra/rtn`` は % 表記なので 100 で割って返す.
    """
    df = _read("barra/rtn", yyyymm)
    s = df.loc[df["lag"] == lag].set_index("bid")[col]
    s = s.mask(s.to_numpy() <= SENTINEL_THRESHOLD) / 100.0
    return s.rename(f"fwd_{col}")


# --- パネル構築 ---------------------------------------------------------------

_UNIV_COLS = ["gid", "name", "gics", "size", "cap", "price"]


def build_panel(
    factors: list[str],
    start: int | None = None,
    end: int | None = None,
    lag: int = 1,
    universe_sizes: tuple[int, ...] = UNIVERSE_SIZES,
    benchmark_sizes: tuple[int, ...] = BENCHMARK_SIZES,
    return_col: str = "rtn",
) -> pd.DataFrame:
    """月次パネル（MultiIndex: yyyymm × bid）を構築する.

    Parameters
    ----------
    factors : ``factor/core`` のカラム名リスト
    lag : 評価リターンの先行月数（1 = 翌月リターン）

    Returns
    -------
    DataFrame with columns: gics, sector, size, cap, in_bench, <factors>, fwd_rtn
    """
    yms = [ym for ym in available_yyyymm("factor/core") if (start is None or ym >= start) and (end is None or ym <= end)]

    frames = []
    for ym in yms:
        univ = load_universe(ym)
        univ = univ.loc[univ["size"].isin(universe_sizes), _UNIV_COLS]
        core = load_core(ym, factors)
        fwd = load_forward_return(ym, lag=lag, col=return_col)

        df = univ.join(core[factors], how="left").join(fwd, how="left")
        df["in_bench"] = univ["size"].isin(benchmark_sizes)
        df["yyyymm"] = ym
        frames.append(df.reset_index().set_index(["yyyymm", "bid"]))

    panel = pd.concat(frames).sort_index()
    panel["sector"] = panel["gics"].astype(str).str[:2]  # GICS セクター（上2桁）
    # サイズ中立化用のコントロール変数（浮動株調整後時価総額 USD の対数）
    panel["log_cap"] = np.log(panel["cap"].where(panel["cap"] > 0))
    return panel


def coverage(panel: pd.DataFrame, factors: list[str]) -> pd.DataFrame:
    """月次のカバレッジ（銘柄数・ファクター非欠損率）を返す."""
    g = panel.groupby(level="yyyymm")
    out = pd.DataFrame({
        "n_universe": g.size(),
        "n_bench": g["in_bench"].sum(),
        "n_fwd_rtn": g["fwd_rtn"].count(),
    })
    for f in factors:
        out[f"cov_{f}"] = g[f].count() / out["n_universe"]
    return out


def add_forward_returns(
    panel: pd.DataFrame,
    lags: tuple[int, ...] | list[int],
    return_col: str = "rtn",
    time_level: str = "yyyymm",
) -> pd.DataFrame:
    """パネルに ``fwd_rtn_h``（h ヶ月先の単月リターン）列を追加する（IC減衰用）."""
    out = panel.copy()
    for h in lags:
        pieces = []
        for ym in out.index.get_level_values(time_level).unique():
            s = load_forward_return(int(ym), lag=h, col=return_col)
            idx = out.xs(ym, level=time_level, drop_level=False).index
            pieces.append(pd.Series(s.reindex(idx.get_level_values("bid")).to_numpy(), index=idx))
        out[f"fwd_rtn_{h}"] = pd.concat(pieces).reindex(out.index)
    return out
