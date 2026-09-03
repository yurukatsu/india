# fl.quantile — 分位分析

## quantile_returns — 分位ポートフォリオの月次リターン一式

```python
res = fl.quantile.quantile_returns(
    panel,                    # パネル
    score_col="mom12_1_blom", # str  スコア列（標準化済みを推奨。順位しか使わないので raw でも動く）
    q=5,                      # 分位数
    ret_col="fwd_rtn",        # 評価リターン列
    cap_col="cap",            # CW 用ウェイト列
    bench_col="in_bench",     # ベンチマークフラグ列
    group_col=None,           # str | None 指定するとグループ内分位（例 "sector" でセクター内分割）
    ascending=True,           # True: Q5 = スコア最大（既定）/ False: Q1 = 最大
    min_universe=0,           # スコア保有銘柄がこの数未満の月を除外
    include_na=True,          # スコア欠損銘柄のバケット（列名 "NA"）も計算（既定 True）
)
```

**必要な列**: `score_col`, `ret_col`, `cap_col`, `bench_col`。

**出力**（dict of DataFrame、index=yyyymm）:

| キー | 内容 |
|---|---|
| `"ew"` / `"cw"` | 分位別リターン。列 Q1..Q5, "Q5-Q1", "bench" |
| `"ew_excess"` / `"cw_excess"` | ベンチ差引後（Q5-Q1 は元々ベンチ中立） |
| `"counts"` | 分位別銘柄数 |
| `"ew_turnover"` / `"cw_turnover"` | 片道回転率（**期中ドリフト調整済み**） |
| `"name_turnover"` | 分位メンバー入替率 |

`include_na=True`（既定）なら各フレームに **`"NA"` 列（スコア欠損銘柄のバケット）** が付く。
Q1..Q5 + NA の銘柄数合計 = 評価可能銘柄数（検証済み）。分位が組めない月の NA は NaN。
`quantile_summary` にも `NA` 行が自動で入る。欠損が情報を持つかの診断に使う。

規約: 分位は一様順位 u=(r−0.5)/n の等分割（昇順・降順で鏡像）。
評価リターンが1本も無い月（データ末尾）は自動除外。
ランダムスコアの片道回転率は 1−1/q（5分位で 0.80）に収束する（検証済み）。

## quantile_summary — 分位別サマリ表

```python
summ = fl.quantile.quantile_summary(res, weighting="ew")   # "ew" | "cw"
```

**入力**: `quantile_returns` の戻り値そのもの。

主要列: `ann_return`（幾何）/ `ann_excess`（幾何差）/ `ann_excess_arith`（算術）/
`ann_vol` / `te` / `IR` / `t_stat_NW` / `max_dd`（絶対）/ `max_dd_excess`（対ベンチ劣後）/
`turnover_1way` / `turnover_ann` / `name_turnover` / `n_stocks_mean`。

**注意**: `t_stat_NW` は算術平均の検定。幾何の棒と符号がズレることがある
（高ボラ分位のボラティリティ・ドラッグ）。

## assign_quantiles / monotonicity

```python
panel["q"] = fl.quantile.assign_quantiles(panel, "mom12_1_blom", q=5)  # 1..5 のラベル
fl.quantile.monotonicity(summ, q=5)  # +1 = Q1→Q5 完全単調増加
```
