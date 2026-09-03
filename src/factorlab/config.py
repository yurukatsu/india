"""パス・共通定数."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

# factor/core・universe の一部数値列では NaN ではなく巨大負値が欠損センチネル
SENTINEL_THRESHOLD = -1e8

# ベンチマーク = MSCI India (size 1, 2) / ユニバース = MSCI India IMI (size 1, 2, 3)
UNIVERSE_SIZES = (1, 2, 3)
BENCHMARK_SIZES = (1, 2)

PERIODS_PER_YEAR = 12
