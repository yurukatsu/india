"""CGO スコアを事前計算して data/cgo/{yyyymm}.pkl に保存する.

- 日次データ（data/turnover/）から計算。1ヶ月 = 21営業日換算
- ホライズン: 1m〜13m（1ヶ月刻み）+ 24m / 36m / 60m の16本（カラムで持つ）
- min_periods = N（フル・ルックバック確保後から出力）
- 各ファイル: columns = [yyyymm, bid, cgo_1m, ..., cgo_60m]
  （ホライズンが未成立の月はその列が NaN）

実行: uv run python scripts/build_cgo.py   （全体で ~30秒）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factorlab import cgo as cgo_mod  # noqa: E402

DST = ROOT / "data" / "cgo"

MONTHS = list(range(1, 14)) + [24, 36, 60]
HORIZONS = {f"cgo_{m}m": 21 * m for m in MONTHS}


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    wide = cgo_mod.monthly_cgo_multi(HORIZONS, frequency="daily")  # 全期間
    n_files = 0
    for ym, blk in wide.groupby(level="yyyymm"):
        out = blk.droplevel("yyyymm").reset_index()
        out.insert(0, "yyyymm", int(ym))
        out.to_pickle(DST / f"{int(ym)}.pkl")
        n_files += 1
    print(f"wrote {n_files} files -> {DST}")
    print("columns:", list(wide.columns))
    print("期間:", int(wide.index.get_level_values('yyyymm').min()),
          "-", int(wide.index.get_level_values('yyyymm').max()))


if __name__ == "__main__":
    main()
