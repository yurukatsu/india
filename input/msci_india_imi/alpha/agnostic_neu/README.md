# 中立化スコア

各月ユニバース内でスコアを Blom 正規化し、説明変数（Blom 化した連続変数 + セクターダミー）に OLS 回帰した残差を再 Blom 化したもの。`scripts/build_neutralize.py` で生成。

| スコア | 元スコア | 説明変数 | 期間 |
| --- | --- | --- | --- |
| `afa_ols_is_neu` | `agnostic/afa_ols_is` | log 時価総額, GICS セクターダミー | 200801〜202607 |
| `afa_ts_neu` | `agnostic/afa_ts` | log 時価総額, GICS セクターダミー | 200801〜202607 |
| `afa_ols_neu` | `agnostic/afa_ols` | log 時価総額, GICS セクターダミー | 200801〜202607 |
| `afa_ridge_neu` | `agnostic/afa_ridge` | log 時価総額, GICS セクターダミー | 200801〜202607 |
| `afa_lasso_neu` | `agnostic/afa_lasso` | log 時価総額, GICS セクターダミー | 200801〜202607 |
| `afa_rf_neu` | `agnostic/afa_rf` | log 時価総額, GICS セクターダミー | 200801〜202607 |
| `afa_lgbm_neu` | `agnostic/afa_lgbm` | log 時価総額, GICS セクターダミー | 200801〜202607 |
