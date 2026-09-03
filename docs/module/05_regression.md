# fl.regression — ファクターリターン（クロスセクション回帰）

毎月 `r_{i,t→t+1} = a_t + f_t·x_{i,t} + u_{i,t}` を推定する（Fama-MacBeth 第1段階）。

## factor_return

```python
fr = fl.regression.factor_return(
    panel,
    score_col="mom12_1_blom",  # 標準化済みスコア列（→ f_t は 1SD あたり月次リターン）
    ret_col="fwd_rtn",
    weighting="ew",            # "ew"（OLS）| "sqrt_cap"（Barra流 WLS）| "cap"（時価総額 WLS）
    cap_col="cap",             # WLS 時に必要
    min_obs=20,
)
# -> DataFrame（index=yyyymm）: alpha, f, t_stat, r2, n, cum_f（単純和）, cum_f_compound（複利）
```

**必要な列**: `score_col`, `ret_col`（WLS なら `cap_col` も）。

性質: 標準化スコアなら `f_t ≈ IC^Pearson_t × σ_t(r)`（実測 corr 0.9997）。
f_t はゼロ投資ポートのリターンなので累積は**単純和**が素直。

## factor_return_summary

```python
fl.regression.factor_return_summary(fr)
# ann_return / ann_vol / IR / t_stat_NW / fm_t_stat（Fama-MacBeth）/ hit_ratio /
# skew / worst_month / max_drawdown / frac_|t|>2 / mean_r2 / mean_n
```

**解釈上の注意**: IR・歪度・最悪月は比較指標として有効。
**max_drawdown の水準は実装 P&L の予測に使えない**（ウェイト無制約・コストゼロ・グロス変動。
実装に近い DD は分位 Q5-Q1 の方を見る — 実測で約2倍深い）。
