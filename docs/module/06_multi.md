# fl.multi — 多ファクター一括比較

## load_style_map / flatten_styles — core.yaml のスタイル定義

```python
style_map = fl.multi.load_style_map()          # {style: [factor, ...]}。Seasonality は既定で除外
factors, groups = fl.multi.flatten_styles(style_map)
# factors: 100本のリスト / groups: Series(factor -> style)
```

**入力**: `data/factor/core.yaml`（引数不要）。

## build_scores — 全ファクター一括標準化

```python
panel = fl.data.build_panel(factors, start=200701, end=202607)
panel, score_cols = fl.multi.build_scores(panel, factors, method="blom")
# score_cols = {factor: f"{factor}_blom"}

# 中立化版を同じパネルに共存させる場合は suffix を変える（列名衝突回避）
panel, _ = fl.multi.build_scores(panel, factors, method="blom",
                                 group_col="sector", control_cols=["log_cap"],
                                 suffix="_blom_ss")
```

## factor_scoreboard — 1行1ファクターの成績表

```python
scoreboard, results = fl.multi.factor_scoreboard(
    panel,
    score_cols,        # dict {表示名: スコア列名}
    groups=groups,     # Series | None（"style" 列として付与。plot_scoreboard_bars の色分けに使う）
    q=5, weighting="ew",
    min_universe=0,    # alt のようにカバレッジが薄い場合は 50 程度を推奨
)
```

**必要な列**: 各スコア列 + `fwd_rtn` + `cap` + `in_bench`。

**出力**:
- `scoreboard`: 列 `ls_*`（Q5−Q1）/ `top_*`（Q5）の ann_excess・IR・t_NW・max_dd・turnover、
  `mean_IC` / `ICIR_ann` / `IC_t_NW`、`coverage`
- `results`: `{表示名: quantile_returns の戻り値}` — 時系列プロットや `return_matrix` に再利用

## cross_sectional_corr — スコアの順位相関行列（月次平均）

```python
C = fl.multi.cross_sectional_corr(panel, [score_cols[f] for f in factors], labels=factors)
```

**必要な列**: 対象スコア列。欠損はペアワイズ。
高速化のため「列ごとに1回ランク→ペア内積」: 完全ケースで pandas と厳密一致、
欠損ペアで最大 ~0.02 の差（順序判断には影響しない）。

## quantile_overlap — 上位分位メンバーの重複率

```python
O = fl.multi.quantile_overlap(panel, cols, q=5, which="top", labels=factors)
# 無相関なら 1/q = 0.20。ロングオンリー併用時の銘柄二重計上の指標
```

## return_matrix — 分位リターン系列を束ねる

```python
ls = fl.multi.return_matrix(results, key="Q5-Q1", weighting="ew")   # 月次 L/S リターン行列
corr_ls = ls.corr()   # 損益の相関（合成設計はスコア相関よりこちらが本命）
```

## style_composite — スタイル合成スコア

```python
panel, style_cols = fl.multi.style_composite(panel, style_map, score_cols)
# 構成ファクターの標準化スコア平均 → 再標準化。style_cols = {style: 列名}
```
