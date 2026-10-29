# 中立化スコア

各月ユニバース内でスコアを Blom 正規化し、説明変数（Blom 化した連続変数 + セクターダミー）に OLS 回帰した残差を再 Blom 化したもの。`scripts/build_neutralize.py` で生成。

| スコア | 元スコア | 説明変数 | 期間 |
| --- | --- | --- | --- |
| `abn_to_neu` | `abn_to/abn_to` | log 時価総額, core/mom1, core/mom12_1, core/tover, core/vola60, core/skew60, GICS セクターダミー | 200801〜202607 |
