# factorlab モジュールガイド

`src/factorlab/` の使い方。各ページに「必要な入力」と実行例を記載する。

## セットアップ

```python
import sys
sys.path.insert(0, "src")          # リポジトリルートから実行する場合
import factorlab as fl
fl.plotting.set_style()            # 図の共通スタイル（任意）
```

## モジュール一覧

| ページ | モジュール | 役割 |
|---|---|---|
| [01_data.md](01_data.md) | `fl.data` | パネル構築・先行リターン・カバレッジ |
| [02_preprocess.md](02_preprocess.md) | `fl.preprocess` | 標準化（Blom / z-score）・中立化 |
| [03_quantile.md](03_quantile.md) | `fl.quantile` | 分位分析・回転率・サマリ |
| [04_ic.md](04_ic.md) | `fl.ic` | IC 時系列・要約・減衰 |
| [05_regression.md](05_regression.md) | `fl.regression` | クロスセクション回帰（ファクターリターン） |
| [06_multi.md](06_multi.md) | `fl.multi` | 多ファクター一括比較・相関/重複行列 |
| [07_altdata.md](07_altdata.md) | `fl.altdata` | AI・オルタナティブファクターのローダー |
| [08_plotting.md](08_plotting.md) | `fl.plotting` | 全プロット関数の入力仕様 |
| [09_cgo.md](09_cgo.md) | `fl.cgo` | Capital Gain Overhang（事前計算スコアの読込 / 再計算） |
| [10_composite.md](10_composite.md) | `fl.composite` | 合成スコア v1（事前計算スコアの読込 / 再計算） |

## ⭐ 中心となるデータ構造: 「パネル」の契約

ほぼ全ての関数は **`fl.data.build_panel()` が返す DataFrame（以下「パネル」）** を入力とする。

```
index   : MultiIndex (yyyymm: int64 例 202012, bid: str 例 "INDAAA1")
columns : 関数ごとに必要な列が異なる（下表）
```

| 列 | 型 | 作られる場所 | 必要とする関数 |
|---|---|---|---|
| `<factor>` | float | `build_panel(factors=[...])` | preprocess 全般 |
| `<factor>_blom` 等 | float | `standardize_panel` / `build_scores` | quantile / ic / regression の `score_col` |
| `fwd_rtn` | float（小数, 翌月） | `build_panel` | quantile / ic / regression |
| `fwd_rtn_h` | float（h ヶ月先の単月） | `add_forward_returns` | `ic_decay` |
| `cap` | float（浮動株調整時価総額 USD 百万） | `build_panel` | quantile の CW・`weighting="cap"` |
| `in_bench` | bool（MSCI India = size1-2） | `build_panel` | quantile のベンチ計算、`score_characteristics` |
| `sector` | str（GICS 上2桁） | `build_panel` | 中立化 `group_col="sector"` |
| `log_cap` | float | `build_panel` | 中立化 `control_cols=["log_cap"]` |

自前のデータを流す場合も、この index / 列名の契約さえ満たせば全関数が動く。

## 最小の一気通貫例

```python
import sys; sys.path.insert(0, "src")
import factorlab as fl

# 1) パネル（ユニバース x ファクター x 翌月リターン）
panel = fl.data.build_panel(["mom12_1"], start=200701, end=202607)

# 2) Blom 標準化（毎月クロスセクション）
panel = fl.preprocess.standardize_panel(panel, "mom12_1", method="blom")

# 3) 分位分析（Q5 = スコア最上位）
res  = fl.quantile.quantile_returns(panel, "mom12_1_blom", q=5)
summ = fl.quantile.quantile_summary(res, weighting="ew")
print(summ[["ann_excess", "IR", "t_stat_NW", "turnover_ann"]])

# 4) IC
ic = fl.ic.information_coefficient(panel, "mom12_1_blom", "fwd_rtn")
print(fl.ic.ic_summary(ic))

# 5) ファクターリターン
fr = fl.regression.factor_return(panel, "mom12_1_blom", weighting="ew")
print(fl.regression.factor_return_summary(fr))
```

## 共通の規約

- すべての統計は**毎月のクロスセクションのみ**で計算（PIT、全期間統計は不使用）
- スコアは時点 t、リターンは区間 (t, t+1]（`barra/rtn` の `lag=1`、現地通貨・配当込み）
- 欠損センチネル（≤ −1e8）はローダー内で NaN 化済み
- `yyyymm` は int（例 `202012`）。日付型にしたい場合は `fl.plotting.to_datetime_index`

## 補助モジュール

- `fl.metrics`: `summarize(r)`（年率・IR・NW t・歪度・最悪月・最大DD）、`newey_west_tstat` など。
  リターン Series を渡すだけで使える汎用要約
- `fl.config`: パス（`DATA_DIR`, `OUTPUT_DIR`）・ユニバース定義・センチネル閾値の定数
