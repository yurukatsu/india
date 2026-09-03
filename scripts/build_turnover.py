"""data/turnover_org/*.csv -> data/turnover/*.pkl の整形.

- sedol -> bid 変換（全期間の map_code を統合した時点依存マップ。
  sedol 再割当（11件）は「その月以前で最新の対応」を採用）
- market_date -> yyyymmdd (int 8桁)
- adj_close -> price にリネーム
- 出力列: yyyymmdd, bid, price, turnover

実行: uv run python scripts/build_turnover.py
"""

from __future__ import annotations

import bisect
import glob
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "turnover_org"
DST = ROOT / "data" / "turnover"
MAP = ROOT / "data" / "map_code"

USECOLS = ["sedol", "market_date", "turnover", "adj_close"]


def build_sedol_map() -> dict[str, list[tuple[int, str]]]:
    """sedol -> [(yyyymm, bid), ...]（月昇順）の時点依存マップ."""
    out: dict[str, list[tuple[int, str]]] = {}
    for f in sorted(MAP.glob("*.pkl")):
        ym = int(f.stem)
        m = pd.read_pickle(f)
        for sed, bid in zip(m["sedol"].astype(str).str.strip().str.upper(),
                            m["bid"].astype(str).str.strip()):
            if sed in ("", "None", "NONE", "nan"):
                continue
            lst = out.setdefault(sed, [])
            if not lst or lst[-1][1] != bid:
                lst.append((ym, bid))
    return out


def lookup(smap: dict[str, list[tuple[int, str]]], sed: str, ym: int) -> str | None:
    lst = smap.get(sed)
    if not lst:
        return None
    if len(lst) == 1:
        return lst[0][1]
    # その月以前で最新の対応。無ければ最初の対応（上場前の履歴に将来の bid を割当）
    i = bisect.bisect_right([e[0] for e in lst], ym) - 1
    return lst[max(i, 0)][1]


def current_sedols(ym: int) -> set[str]:
    f = MAP / f"{ym}.pkl"
    if not f.exists():
        return set()
    m = pd.read_pickle(f)
    return set(m["sedol"].astype(str).str.strip().str.upper())


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    smap = build_sedol_map()
    files = sorted(SRC.glob("*.csv"))
    total_in = total_out = n_dup = 0
    for f in files:
        ym = int(f.stem)
        d = pd.read_csv(f, usecols=USECOLS, dtype={"sedol": str})
        d["sedol"] = d["sedol"].str.strip().str.upper()
        d["bid"] = [lookup(smap, s, ym) for s in d["sedol"]]
        n_in = len(d)
        d = d.dropna(subset=["bid"])
        d["yyyymmdd"] = d["market_date"].str.replace("-", "", regex=False).astype("int64")

        # 同一 (bid, 日) に新旧 sedol の2系列が並存することがある（sedol 変更月の前後）。
        # 当月 map_code の sedol を優先し、無ければ turnover の大きい行を採る。
        cur = current_sedols(ym)
        d["_pri"] = (~d["sedol"].isin(cur)).astype(int)  # 0 = 当月 sedol
        d = d.sort_values(["bid", "yyyymmdd", "_pri", "turnover"],
                          ascending=[True, True, True, False])
        before = len(d)
        d = d.drop_duplicates(["bid", "yyyymmdd"], keep="first")
        n_dup += before - len(d)

        out = pd.DataFrame({
                "yyyymmdd": d["yyyymmdd"],
                "bid": d["bid"],
                "price": d["adj_close"].astype(float),
                "turnover": d["turnover"].astype(float),
            }).reset_index(drop=True)
        out.to_pickle(DST / f"{ym}.pkl")
        total_in += n_in
        total_out += len(out)
    print(f"files={len(files)}  rows in={total_in:,} -> out={total_out:,} "
          f"(dedup除去 {n_dup:,} 行)")


if __name__ == "__main__":
    main()
