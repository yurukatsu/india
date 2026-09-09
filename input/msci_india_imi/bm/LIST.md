# Benchmark List

ベンチマーク一覧。いずれも `data/universe` の浮動株調整後時価総額 `cap` を
正規化した **時価総額ウェイトのプロキシ** であり、MSCI 公式のインデックスウェイトではない。

| ディレクトリ | 想定インデックス | 構成 | 期間 |
| --- | --- | --- | --- |
| `msci_india` | MSCI India (Standard) | `size ∈ {1, 2}`（大型 + 中型） | 200301〜202607 |
| `msci_india_imi` | MSCI India IMI | `size ∈ {1, 2, 3}`（大型 + 中型 + 小型） | 200301〜202607 |

生成: `uv run python scripts/build_input.py bm`
