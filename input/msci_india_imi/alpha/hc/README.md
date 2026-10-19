# Healthy Compounder ファクター候補

`scripts/build_healthy_compounder.py` で `core` とリスクモデル（BETA）から生成。素材を符号調整して各月ユニバース内で Blom 化し、柱内で等ウェイト平均して再 Blom 化。すべて高いほど良い向き。

| スコア | 柱 | 定義 | 素材（`-` は符号反転） | 期間 |
| --- | --- | --- | --- | --- |
| `hc_cfoa` | ① キャッシュ収益性 | 営業 CF / 総資産（`ocf_ta`）。Ball et al. (2016) の CbOP の代替。インドの損益計算書は費用を性質別に開示するため売上総利益 − 販管費（`gpa_act − sga_ta`）が営業利益の代わりにならず、キャッシュ側の指標を使う | `ocf_ta` | 200301〜202606 |
| `hc_cfp` | ① キャッシュ収益性 | 営業 CF / 株価（`ocfp`） | `ocfp` | 200301〜202606 |
| `hc_dso` | ① キャッシュ収益性 | 売掛金回転日数（365/`rec_to`）の 12 か月変化を GICS 産業グループ中央値からの乖離にしたもの（負号）。金融セクターは対象外 | `-dso_chg_ind` | 200401〜202606 |
| `hc_cash` | ① キャッシュ収益性 | ① の 3 素材の合成 | `ocf_ta`, `ocfp`, `-dso_chg_ind` | 200301〜202606 |
| `hc_qmj_prof` | ② QMJ | QMJ 収益性: GPOA, ROE, ROA, CFOA, GMAR, −ACC。`core` の `acc_bs` は (営業 CF − 純利益)/総資産と正に相関する「負のアクルーアル」の向きなので符号はそのまま使う | `gpa_act`, `roe_act`, `roa_act`, `ocf_ta`, `gm_act`, `acc_bs` | 200301〜202606 |
| `hc_qmj_growth` | ② QMJ | QMJ 成長: 粗利・純利益・営業利益・営業 CF・粗利率の 5 年変化 | `dlt_gpa_5y`, `dlt_roe_5y`, `dlt_roa_5y`, `dlt_ocfa_5y`, `dlt_gps_5y` | 200301〜202606 |
| `hc_qmj_safety` | ② QMJ | QMJ 安全性: −BETA（Barra）, −VOLA60, −負債/総資産, −O-score, Z-score, −ROE の 5 年ばらつき（3 点以上） | `-beta`, `-vola60`, `-td_ta`, `-oscore`, `zscore`, `-roe_vol5y` | 200301〜202606 |
| `hc_qmj_payout` | ② QMJ | QMJ 還元: −純株式発行（`eiss`）, −純負債発行（`diss`）, 5 年純還元/営業利益（`npop`）。参考 | `eiss`, `diss`, `npop` | 200301〜202606 |
| `hc_qmj` | ② QMJ | QMJ = 収益性 + 成長 + 安全性 | `hc_qmj_prof`, `hc_qmj_growth`, `hc_qmj_safety` | 200301〜202606 |
| `hc_sales_stab` | ③ 期待過熱の回避 | 売上成長率（`sales_grw`）の過去 5 年（12 か月おき 5 点、3 点以上）の標準偏差（負号） | `-sales_grw_vol5y` | 200501〜202606 |
| `hc_issuance` | ④ 資本規律 | −総合株式発行（`csi`）と −純株式発行（`eiss`）の合成 | `-csi`, `eiss` | 200301〜202606 |
| `hc_all` | 統合 | ①②③④ の柱スコアの等ウェイト合成 | `hc_cash`, `hc_qmj`, `hc_sales_stab`, `hc_issuance` | 200301〜202606 |
