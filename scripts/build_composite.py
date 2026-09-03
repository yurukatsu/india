"""合成スコア v1 を事前計算して data/composite/{yyyymm}.pkl に保存する.

仕様: docs/analyze_memo/06_composite_strategy.md（sleeve EW）
出力列: yyyymm, bid, sleeve_a, sleeve_b, sleeve_d, comp_ew（すべて Blom 標準化済み）

実行: uv run python scripts/build_composite.py   （~30秒）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factorlab import composite  # noqa: E402


def main() -> None:
    composite.COMPOSITE_DIR.mkdir(parents=True, exist_ok=True)
    scores = composite.build_composite_scores(start=200801)
    n = 0
    for ym, blk in scores.groupby(level="yyyymm"):
        out = blk.droplevel("yyyymm").reset_index()
        out.insert(0, "yyyymm", int(ym))
        out.to_pickle(composite.COMPOSITE_DIR / f"{int(ym)}.pkl")
        n += 1
    print(f"wrote {n} files -> {composite.COMPOSITE_DIR}")
    print("columns:", list(scores.columns))
    print("期間:", int(scores.index.get_level_values('yyyymm').min()),
          "-", int(scores.index.get_level_values('yyyymm').max()),
          " rows:", len(scores))


if __name__ == "__main__":
    main()
