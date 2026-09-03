# fl.preprocess — 標準化と中立化

## standardize_panel — 毎月クロスセクションで標準化列を追加

```python
panel = fl.preprocess.standardize_panel(
    panel,                    # build_panel の出力
    factors="mom12_1",        # str | list[str]  パネルに存在する生ファクター列
    method="blom",            # "blom" | "zscore" | "rank" | "raw"
    winsor=None,              # float | None 分位点ウィンザライズ片側割合（zscore なら 0.01 推奨）
    winsor_mad=None,          # float | None MAD クリップ係数（3〜5）
    group_col=None,           # str | None 中立化グループ列（例 "sector"）
    control_cols=None,        # list[str] | None 中立化連続変数（例 ["log_cap"]）
    suffix=None,              # 出力列接尾辞。既定 f"_{method}"
)
# -> panel に "mom12_1_blom" が追加される
```

**必要な列**: `factors` の各列。中立化する場合は `group_col` / `control_cols` の列
（`build_panel` 由来なら `sector` / `log_cap` が既にある）。

パイプライン: winsorize → 変換 → 中立化（回帰残差、SD=1 に再スケール）。
Blom は `Φ⁻¹((rank − 3/8)/(n + 1/4))`、同順位は平均順位。

**注意**: Spearman IC は単調変換で不変なので、IC 目的なら method は結果に影響しない。
分位分割・回帰には影響する。

## make_neutral_variants — 中立化4バリアントを一括生成

```python
panel, score_cols = fl.preprocess.make_neutral_variants(panel, "mom12_1", method="blom")
# score_cols = {"none": "mom12_1_blom", "size": "mom12_1_blom_size",
#               "sector": "mom12_1_blom_sector", "size_sector": "mom12_1_blom_size_sector"}
```

**必要な列**: 生ファクター列 + `sector` + `log_cap`。

- `size_sector` は「セクターダミー + log_cap を**1本の回帰で同時に**」落とす（逐次ではない）
- 計画行列はダミー全部+定数で共線だが、残差はダミー1本落としの教科書型と厳密一致（検証済み）

## neutralization_check — 中立化の効き目診断

```python
fl.preprocess.neutralization_check(panel, score_cols)
# corr_log_cap（サイズ中立なら≈0）/ sector_disp（セクター中立なら≈0）/ corr_with_none
```

**必要な列**: `score_cols` の各列 + `log_cap` + `sector`。

## tail_stats — 裾の厚さ診断（Blom を使うべきかの判断材料）

```python
fl.preprocess.tail_stats(panel, ["bp_act", "bp_act_blom"])
# skew / excess_kurt / frac_|z|>3 / frac_|z|>5 / coverage
```
