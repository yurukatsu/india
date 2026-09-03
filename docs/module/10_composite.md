# fl.composite — 合成スコア v1

## load_composite — 事前計算済みスコアの読み込み（推奨）

```python
comp = fl.composite.load_composite(
    columns=["comp_ew"],        # None なら sleeve_a / sleeve_b / sleeve_d / comp_ew の4列
    start=200801, end=202607,
)
panel["comp_ew"] = comp["comp_ew"].reindex(panel.index)
```

**入力**: `data/composite/`（`scripts/build_composite.py` で生成、~30秒）。
保存値は notebooks/15 の主要指標を厳密に再現（検証済み）。

## build_composite_scores — 再計算

```python
scores = fl.composite.build_composite_scores(start=200801)   # 仕様 v1（SLEEVES_V1）
```

スリーブ構成を変える場合は `sleeves={"name": [(factor, sign), ...]}` を渡す
（`cgo_gain` は特別扱いで `data/cgo` の `cgo_60m` 正側から自動生成）。

仕様の詳細（素材・符号・パイプライン・結果）: `docs/analyze_memo/06_composite_strategy.md`
