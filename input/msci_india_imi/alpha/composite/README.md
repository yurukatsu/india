# 複合スコア

## `composite/comp_ew` × `my_ai/ens_lgbm_dnn`

`composite/comp_ew`（A）と `my_ai/ens_lgbm_dnn`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_50_ai_50` | 50% | 50% | 201212〜202607 |
| `comp_ew_60_ai_40` | 60% | 40% | 201212〜202607 |
| `comp_ew_70_ai_30` | 70% | 30% | 201212〜202607 |
| `comp_ew_80_ai_20` | 80% | 20% | 201212〜202607 |
| `comp_ew_90_ai_10` | 90% | 10% | 201212〜202607 |

## `composite/comp_ew` × `my_ai/ens_lgbm_dnn_hl1`

`composite/comp_ew`（A）と `my_ai/ens_lgbm_dnn_hl1`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_50_ai_hl1_50` | 50% | 50% | 201212〜202607 |
| `comp_ew_60_ai_hl1_40` | 60% | 40% | 201212〜202607 |
| `comp_ew_70_ai_hl1_30` | 70% | 30% | 201212〜202607 |
| `comp_ew_80_ai_hl1_20` | 80% | 20% | 201212〜202607 |
| `comp_ew_90_ai_hl1_10` | 90% | 10% | 201212〜202607 |

## `composite/comp_ew` × `my_ai_neu/ens_lgbm_dnn_neu`

`composite/comp_ew`（A）と `my_ai_neu/ens_lgbm_dnn_neu`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_50_ai_neu_50` | 50% | 50% | 201212〜202607 |
| `comp_ew_60_ai_neu_40` | 60% | 40% | 201212〜202607 |
| `comp_ew_70_ai_neu_30` | 70% | 30% | 201212〜202607 |
| `comp_ew_80_ai_neu_20` | 80% | 20% | 201212〜202607 |
| `comp_ew_90_ai_neu_10` | 90% | 10% | 201212〜202607 |

## `composite/comp_ew` × `hc/hc_all`

`composite/comp_ew`（A）と `hc/hc_all`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_90_hc_10` | 90% | 10% | 200801〜202606 |
| `comp_ew_80_hc_20` | 80% | 20% | 200801〜202606 |
| `comp_ew_70_hc_30` | 70% | 30% | 200801〜202606 |

## `composite/comp_ew` × `hc_neu/hc_all_neu`

`composite/comp_ew`（A）と `hc_neu/hc_all_neu`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_90_hcneu_10` | 90% | 10% | 200801〜202606 |
| `comp_ew_80_hcneu_20` | 80% | 20% | 200801〜202606 |
| `comp_ew_70_hcneu_30` | 70% | 30% | 200801〜202606 |

## `composite/comp_ew` × `abn_to/abn_to`

`composite/comp_ew`（A）と `abn_to/abn_to`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_90_abnto_10` | 90% | 10% | 200801〜202607 |
| `comp_ew_80_abnto_20` | 80% | 20% | 200801〜202607 |
| `comp_ew_70_abnto_30` | 70% | 30% | 200801〜202607 |

## `composite/comp_ew` × `abn_to_neu/abn_to_neu`

`composite/comp_ew`（A）と `abn_to_neu/abn_to_neu`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_90_abntoneu_10` | 90% | 10% | 200801〜202607 |
| `comp_ew_80_abntoneu_20` | 80% | 20% | 200801〜202607 |
| `comp_ew_70_abntoneu_30` | 70% | 30% | 200801〜202607 |

## `composite/comp_ew` × `agnostic_smooth/afa_ts_neu_avg12`

`composite/comp_ew`（A）と `agnostic_smooth/afa_ts_neu_avg12`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_90_afats12_10` | 90% | 10% | 200806〜202606 |
| `comp_ew_80_afats12_20` | 80% | 20% | 200806〜202606 |
| `comp_ew_70_afats12_30` | 70% | 30% | 200806〜202606 |

## `composite/comp_ew` × `agnostic_neu/afa_ts_neu`

`composite/comp_ew`（A）と `agnostic_neu/afa_ts_neu`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_90_afats_10` | 90% | 10% | 200801〜202606 |
| `comp_ew_80_afats_20` | 80% | 20% | 200801〜202606 |
| `comp_ew_70_afats_30` | 70% | 30% | 200801〜202606 |

## `composite/comp_ew` × `agnostic_neu/afa_lgbm_neu`

`composite/comp_ew`（A）と `agnostic_neu/afa_lgbm_neu`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_90_afalgbm_10` | 90% | 10% | 200801〜202606 |
| `comp_ew_80_afalgbm_20` | 80% | 20% | 200801〜202606 |
| `comp_ew_70_afalgbm_30` | 70% | 30% | 200801〜202606 |

## `composite/comp_ew` × `agnostic_neu/afa_rf_neu`

`composite/comp_ew`（A）と `agnostic_neu/afa_rf_neu`（B）の複合スコア。各月ユニバース内で両者を Blom 正規スコアに変換し、
`w·z_A + (1−w)·z_B` を再 Blom 化したもの（片方しか無い銘柄は存在する方のみ）。`scripts/build_blend.py` で生成。

| スコア | A の重み | B の重み | 期間 |
| --- | --- | --- | --- |
| `comp_ew_90_afarf_10` | 90% | 10% | 200801〜202606 |
| `comp_ew_80_afarf_20` | 80% | 20% | 200801〜202606 |
| `comp_ew_70_afarf_30` | 70% | 30% | 200801〜202606 |
