"""合成スコア v1（sleeve EW）の構築と永続化.

仕様書: docs/analyze_memo/06_composite_strategy.md
  - スリーブ A: rev1p, rev1r, rev3p, rev3r（+）
  - スリーブ B: mom12_1（+）, cgo_gain = cgo_60m の正側のみ（+）
  - スリーブ D: doe, npop, dp_act（+）/ xfin_sp, xfin_cf, ta_grw（−）
  - 各素材: Blom → sector + log(Cap) 回帰残差 → 符号調整
  - スリーブ: skipna 平均 → 再 Blom / 合成: スリーブ skipna 平均 → 再 Blom
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import cgo as cgo_mod
from . import data as data_mod
from . import preprocess
from .config import DATA_DIR

COMPOSITE_DIR = DATA_DIR / "composite"

# v1 仕様（docs/analyze_memo/06 の 6.3）
SLEEVES_V1: dict[str, list[tuple[str, int]]] = {
    "sleeve_a": [("rev1p", +1), ("rev1r", +1), ("rev3p", +1), ("rev3r", +1)],
    "sleeve_b": [("mom12_1", +1), ("cgo_gain", +1)],
    "sleeve_d": [("doe", +1), ("npop", +1), ("dp_act", +1),
                 ("xfin_sp", -1), ("xfin_cf", -1), ("ta_grw", -1)],
}
CORE_FACTORS_V1 = ["rev1p", "rev1r", "rev3p", "rev3r", "mom12_1",
                   "doe", "npop", "dp_act", "xfin_sp", "xfin_cf", "ta_grw"]


def build_composite_scores(
    start: int | None = 200801,
    end: int | None = None,
    sleeves: dict[str, list[tuple[str, int]]] | None = None,
) -> pd.DataFrame:
    """合成スコアを計算して返す（MultiIndex: yyyymm × bid）.

    列 = 各スリーブ（再Blom済み）+ ``comp_ew``。
    """
    sleeves = sleeves or SLEEVES_V1
    factors = sorted({f for ms in sleeves.values() for f, _ in ms if f != "cgo_gain"})
    need_cgo = any(f == "cgo_gain" for ms in sleeves.values() for f, _ in ms)

    panel = data_mod.build_panel(factors, start=start, end=end)
    if need_cgo:
        c = cgo_mod.load_cgo_monthly(["cgo_60m"], start=start, end=end)["cgo_60m"]
        panel["cgo_gain"] = c.reindex(panel.index).where(lambda s: s > 0)

    all_facs = factors + (["cgo_gain"] if need_cgo else [])
    panel = preprocess.standardize_panel(panel, all_facs, method="blom",
                                         group_col="sector", control_cols=["log_cap"],
                                         suffix="_ss")

    out_cols = []
    for sl, members in sleeves.items():
        mat = pd.concat({f: sgn * panel[f"{f}_ss"] for f, sgn in members}, axis=1)
        panel[f"__{sl}"] = mat.mean(axis=1, skipna=True)
        panel = preprocess.standardize_panel(panel, f"__{sl}", method="blom", suffix="_b")
        panel[sl] = panel[f"__{sl}_b"]
        out_cols.append(sl)

    panel["__c"] = panel[out_cols].mean(axis=1, skipna=True)
    panel = preprocess.standardize_panel(panel, "__c", method="blom", suffix="_b")
    panel["comp_ew"] = panel["__c_b"]
    return panel[out_cols + ["comp_ew"]].copy()


def load_composite(
    columns: list[str] | None = None,
    start: int | None = None,
    end: int | None = None,
) -> pd.DataFrame:
    """事前計算済みの合成スコア（``data/composite/{yyyymm}.pkl``）を読む.

    生成は ``scripts/build_composite.py``。
    Returns: MultiIndex (yyyymm, bid) の DataFrame
    （列 = sleeve_a, sleeve_b, sleeve_d, comp_ew）。
    """
    frames = []
    for p in sorted(COMPOSITE_DIR.glob("*.pkl")):
        ym = int(p.stem)
        if (start is not None and ym < start) or (end is not None and ym > end):
            continue
        df = pd.read_pickle(p)
        if columns is not None:
            df = df[["yyyymm", "bid", *columns]]
        frames.append(df)
    return pd.concat(frames, ignore_index=True).set_index(["yyyymm", "bid"]).sort_index()
