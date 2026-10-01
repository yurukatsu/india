# 中立化スコア

各月ユニバース内でスコアを Blom 正規化し、説明変数（Blom 化した連続変数 + セクターダミー）に OLS 回帰した残差を再 Blom 化したもの。`scripts/build_neutralize.py` で生成。

| スコア | 元スコア | 説明変数 | 期間 |
| --- | --- | --- | --- |
| `ens_lgbm_dnn_neu` | `my_ai/ens_lgbm_dnn` | log 時価総額, core/vola60, GICS セクターダミー | 201212〜202607 |
| `ens_lgbm_dnn_neu_sz` | `my_ai/ens_lgbm_dnn` | log 時価総額, GICS セクターダミー | 201212〜202607 |
