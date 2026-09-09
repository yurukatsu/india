# Alpha List

`scripts/build_input.py alpha` で `data/` から生成したスコア一覧。
各スコアは `alpha/{group}/{score}/YYYYMM.dat` に格納。
欠損値の行は出力していない（`factor/core` の `-1e9` センチネルは欠損扱い）。
ユニバースによる絞り込みは行っていない。銘柄コードはすべて BID（7 桁）であることを検証済み。

| グループ | 元データ | 内容 | 基準日の定義 |
| --- | --- | --- | --- |
| `core` | `data/factor/core` | コアファクター 123 本（列ごとに 1 スコア） | ファイル年月 `dateym` |
| `ai` | `data/factor/ai` | AI スコア（月内最終営業日のスナップショット） | ファイル年月 |
| `alt` | `data/factor/alt/{id}` | オルタナティブファクター（`{id}_{name}`） | ファイル年月（`effective_yyyymmdd` ≤ 月末 のみ採用） |
| `cgo` | `data/cgo` | Capital Gain Overhang（遡及期間別） | ファイル年月 |
| `composite` | `data/composite` | 合成スコア v1（スリーブ別 + 等ウェイト合成） | ファイル年月 |
| `reprisk` | `data/reprisk` | RepRisk Index（RRI、0〜100 の ESG レピュテーションリスク。高いほどリスク大） | ファイル年月 |

## core

期間 200301〜202607。スタイルは `data/factor/core.yaml` に基づく（`-` はどのスタイルにも属さない補助列）。

| スコア | スタイル |
| --- | --- |
| `bp_est` | Stock Value |
| `bp_act` | Stock Value |
| `ep_est` | Flow Value |
| `ep_act` | Flow Value |
| `dp_est` | Flow Value |
| `dp_act` | Flow Value |
| `sp_est` | Flow Value |
| `sp_act` | Flow Value |
| `ebitdaev_est` | Flow Value |
| `ebitdaev_act` | Flow Value |
| `cfp_est` | Flow Value |
| `cfp_act` | Flow Value |
| `ocfp` | Flow Value |
| `fcfp` | Flow Value |
| `rdp` | Flow Value |
| `maratio` | Flow Value |
| `dlt_ep3` | Flow Value |
| `dlt_ep6` | Flow Value |
| `tobin_q` | - |
| `roe_est` | Profitability |
| `roe_act` | Profitability |
| `roa_est` | Profitability |
| `roa_act` | Profitability |
| `roic_est` | Profitability |
| `roic_act` | Profitability |
| `gpa_act` | Profitability |
| `gpa_act_fy` | - |
| `gm_act` | Profitability |
| `nis_est` | Profitability |
| `nis_act` | Profitability |
| `ois_est` | Profitability |
| `ois_act` | Profitability |
| `ocfs` | Profitability |
| `prof` | Profitability |
| `prof_ball_bs_fy` | - |
| `ltg` | Growth |
| `sales_grw` | Growth |
| `oi_grw` | Growth |
| `eps_grw` | Growth |
| `ni_grw` | Growth |
| `ppe_grw` | Growth |
| `ta_grw` | Growth |
| `ta_grw_fy` | - |
| `invent_grw` | Growth |
| `eq_grw` | Growth |
| `capex_grw` | Growth |
| `ia` | Growth |
| `csi` | Shareholder Return |
| `rev1p` | Momentum |
| `rev1r` | Momentum |
| `rev3p` | Momentum |
| `rev3r` | Momentum |
| `mom1` | Momentum |
| `mom12` | Momentum |
| `mom12_1` | Momentum |
| `availm12` | - |
| `mom60` | Momentum |
| `vola60` | Volatility |
| `skew60` | Volatility |
| `availm60` | - |
| `tover` | Momentum |
| `illiq` | - |
| `doe` | Shareholder Return |
| `buyback` | - |
| `gpy` | - |
| `npy` | - |
| `acc_bs` | Financial Quality |
| `acc_cf` | Financial Quality |
| `acc_cf2` | - |
| `xfin_bs` | Financial Quality |
| `xfin_cf` | Financial Quality |
| `xfin_sp` | Financial Quality |
| `curr` | Financial Quality |
| `ta_to` | Financial Quality |
| `rec_to` | Financial Quality |
| `invent_to` | Financial Quality |
| `acpy_to` | Financial Quality |
| `ccc` | Financial Quality |
| `sales_bep` | Financial Quality |
| `eqr` | Financial Quality |
| `wk_ta` | - |
| `re_ta` | Profitability |
| `ebit_ta` | Profitability |
| `mve_tl` | Financial Quality |
| `sales_ta` | Profitability |
| `sga_ta` | - |
| `rd_ta` | - |
| `ocf_ta` | - |
| `eq_tl` | Financial Quality |
| `tdint` | Financial Quality |
| `ocftd` | Financial Quality |
| `peg_est` | Flow Value |
| `peg_act` | Flow Value |
| `dsr` | Financial Quality |
| `gmi` | Financial Quality |
| `aqi` | Financial Quality |
| `sgi` | - |
| `depi` | Financial Quality |
| `sgai` | Financial Quality |
| `levi` | Financial Quality |
| `eiss` | Shareholder Return |
| `diss` | Shareholder Return |
| `npop` | Shareholder Return |
| `dlt_gpa_5y` | Profitability |
| `dlt_roe_5y` | Profitability |
| `dlt_roa_5y` | Profitability |
| `dlt_ocfa_5y` | Profitability |
| `dlt_gps_5y` | Profitability |
| `td_ta` | Financial Quality |
| `tl_ta` | Financial Quality |
| `ni_ta` | Profitability |
| `pti_tl` | Profitability |
| `intwo` | - |
| `chin` | Profitability |
| `noa_fy` | - |
| `noa_grw_fy` | - |
| `mv` | - |
| `mv_usd` | - |
| `tmv` | - |
| `tmv_usd` | - |
| `oscore` | Financial Quality |
| `zscore` | Financial Quality |
| `mscore` | Financial Quality |

`Seasonality`（JAN, FEB, MAR, APR, MAY, JUN, JUL, AUG, SEP, OCT, NOV, DEC）は実データが無いため生成していない。

## ai

期間 201608〜202604。スコア名は `ai_v1`（`data/factor/ai` の `ai` 列）。

## alt

| ディレクトリ | factor_id | ファクター名 | 型 | 頻度 | 期間 |
| --- | --- | --- | --- | --- | --- |
| `1_overall_rating` | 1 | overall_rating | Z | M | 201605〜202607 |
| `2_culture_and_values` | 2 | culture_and_values | Z | M | 201605〜202607 |
| `3_work_life` | 3 | work_life | Z | M | 201605〜202607 |
| `4_senior_management` | 4 | senior_management | Z | M | 201605〜202607 |
| `5_comp_and_benefits` | 5 | comp_and_benefits | Z | M | 201605〜202607 |
| `6_career_opportunities` | 6 | career_opportunities | Z | M | 201605〜202607 |
| `7_recommend` | 7 | recommend | Z | M | 201605〜202607 |
| `8_ceo_rating` | 8 | ceo_rating | Z | M | 201605〜202607 |
| `9_biz_outlook` | 9 | biz_outlook | Z | M | 201605〜202607 |
| `11_controversy_score_ms` | 11 | controversy_score_ms | score | M | 201804〜202607 |
| `12_environment_controversy_score_ms` | 12 | environment_controversy_score_ms | score | M | 201804〜202607 |
| `13_customer_controversy_score_ms` | 13 | customer_controversy_score_ms | score | M | 201804〜202607 |
| `14_governance_controversy_score_ms` | 14 | governance_controversy_score_ms | score | M | 201804〜202607 |
| `15_labor_rights_controversy_score_ms` | 15 | labor_rights_controversy_score_ms | score | M | 201804〜202607 |
| `16_human_rights_controversy_score_ms` | 16 | human_rights_controversy_score_ms | score | M | 201804〜202607 |
| `65_rev1p_gdb` | 65 | rev1p_gdb | Z | M | 201604〜202607 |
| `66_rev1r_gdb` | 66 | rev1r_gdb | Z | M | 201604〜202607 |
| `67_rev3p_gdb` | 67 | rev3p_gdb | Z | M | 201604〜202607 |
| `68_rev3r_gdb` | 68 | rev3r_gdb | Z | M | 201604〜202607 |
| `340_nam_6_pillars_environment_global_issue_score` | 340 | nam_6_pillars_environment_global_issue_score | score | M | 201302〜202607 |
| `341_nam_6_pillars_environment_global_issue_score_diff` | 341 | nam_6_pillars_environment_global_issue_score_diff | score | M | 201302〜202607 |
| `342_nam_6_pillars_environment_social_stakeholder_score` | 342 | nam_6_pillars_environment_social_stakeholder_score | score | M | 201302〜202607 |
| `343_nam_6_pillars_environment_social_stakeholder_score` | 343 | nam_6_pillars_environment_social_stakeholder_score | score | M | 201302〜202607 |
| `345_nam_6_pillars_social_global_issue_score_diff` | 345 | nam_6_pillars_social_global_issue_score_diff | score | M | 201302〜202607 |
| `346_nam_6_pillars_social_social_stakeholder_score` | 346 | nam_6_pillars_social_social_stakeholder_score | score | M | 201302〜202607 |
| `347_nam_6_pillars_social_social_stakeholder_score_diff` | 347 | nam_6_pillars_social_social_stakeholder_score_diff | score | M | 201302〜202607 |
| `348_nam_6_pillars_governance_score` | 348 | nam_6_pillars_governance_score | score | M | 201302〜202607 |
| `349_nam_6_pillars_governance_score_diff` | 349 | nam_6_pillars_governance_score_diff | score | M | 201302〜202607 |
| `350_nam_6_pillars_behavior_score` | 350 | nam_6_pillars_behavior_score | score | M | 201302〜202607 |
| `351_nam_6_pillars_behavior_score_diff` | 351 | nam_6_pillars_behavior_score_diff | score | M | 201302〜202607 |
| `352_nam_3_pillars_6_e_score` | 352 | nam_3_pillars_6_e_score | score | M | 201302〜202607 |
| `353_nam_3_pillars_6_s_score` | 353 | nam_3_pillars_6_s_score | score | M | 201302〜202607 |
| `354_nam_3_pillars_6_g_score` | 354 | nam_3_pillars_6_g_score | score | M | 201302〜202607 |
| `355_nam_1_pillars_3_esg_score` | 355 | nam_1_pillars_3_esg_score | score | M | 201302〜202607 |
| `400_tvl` | 400 | tvl | score | D | 200701〜202607 |
| `402_tvl_v3` | 402 | tvl_v3 | score | D | 200702〜202607 |

## cgo

期間 200301〜202607。スコア: `cgo_1m`, `cgo_2m`, `cgo_3m`, `cgo_4m`, `cgo_5m`, `cgo_6m`, `cgo_7m`, `cgo_8m`, `cgo_9m`, `cgo_10m`, `cgo_11m`, `cgo_12m`, `cgo_13m`, `cgo_24m`, `cgo_36m`, `cgo_60m`

## composite

期間 200801〜202607。スコア: `sleeve_a`, `sleeve_b`, `sleeve_d`, `comp_ew`

## reprisk

期間 200701〜202607。スコア: `repr_current_rri`
