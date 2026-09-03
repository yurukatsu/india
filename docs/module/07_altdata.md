# fl.altdata — AI・オルタナティブファクター

## list_alt_factors — 収録一覧

```python
inv = fl.altdata.list_alt_factors()
# index=factor_id。列: name, group, frequency, data_type, lag_days, first, last, n_files
# 重複名（342/343）は "_342" / "_343" を付けて区別済み
```

**入力**: `data/factor/alt/`（引数不要）。

## build_alt_panel — alt/AI をパネルに結合（推奨エントリポイント）

```python
panel, inv = fl.altdata.build_alt_panel(
    factor_ids=None,     # list[int] | None（None = 全36本。[] で alt なし）
    include_ai=True,     # AI スコア列 "ai" を付ける
    start=201301, end=202607,
    core_factors=None,   # コアファクターも同時に載せたい場合
)
# panel: build_panel と同形式 + alt/ai 列（列名 = inv["column"]）
# inv:   list_alt_factors + "column" 列（+ ai の行）
```

タイミング規約（関数内で適用済み）:
- alt: `effective_yyyymmdd` ≤ 月末のレコードのみ・銘柄ごとに発効日最新（**発効日ベースの PIT**）
- ai: 月内で最後に観測された日次値（月末スナップショット）

以降は通常のパイプラインに接続できる:

```python
cols = list(inv["column"])
panel, _ = fl.multi.build_scores(panel, cols, method="blom")
sb, results = fl.multi.factor_scoreboard(
    panel, {c: f"{c}_blom" for c in cols},
    groups=inv.set_index("column")["group"],
    min_universe=50,     # カバレッジが薄いので月次銘柄数フィルタ推奨
)
```

## score_characteristics — 評価前の特性チェック

```python
fl.altdata.score_characteristics(panel, cols)
# n_months / first / last / n_stocks_mean /
# coverage_univ / coverage_bench（データがある月のみで計算）/
# n_unique_mean（離散度）/ frac_mode（最頻値占有率）/ rank_autocorr（持続性）
```

**必要な列**: 対象列 + `in_bench`。

読み方: `frac_mode` が高い（例 コントラバーシーの 0.5〜0.9）と分位境界がタイの中を通り
Q5−Q1 が擬似乱数的になる → IC を主に読む。`rank_autocorr` が高ければ低回転で使える。

## 低レベルローダー

```python
fl.altdata.load_alt_monthly(65)   # Series（MultiIndex yyyymm x bid）。lru_cache 済み
fl.altdata.load_ai_monthly()      # 同上
```
