# スタイル類似局面（DTW）予測の検証

野村「BPN スタイル配分戦略の改良方針」（2022/10）の DTW（直近 3 か月の日次チャートに最も似た過去 3 局面の翌期リターンで
スタイルの期待リターンを決める）を、インド株の core スタイルに移植して検証する。スタイル配分ではなく、予測を
個別株スコアに変換し、既存の規約（Q5 → IC → Q5−Q1）で評価する。

- スコア生成: `scripts/build_style_dtw.py` → `input/msci_india_imi/alpha/style_dtw/{name}/`
- 診断用 CSV: `output/`（日次スタイルリターン、スタイル別の予測と実現値、近傍の一覧）
- 評価: `01_style_dtw.ipynb`

## 定式化

| 記号 | 意味 |
| --- | --- |
| $t$, $\tau$, $\tau_t$ | 月末、営業日、月 $t$ の最終営業日 |
| $i \in U_t$ | 銘柄（MSCI India IMI ユニバース） |
| $x_{i,f,t}$, $d_f$ | core ファクターの値と符号（下表） |
| $r_{i,\tau}$ | 日次リターン（`data/turnover` の分割調整済み株価。配当は含まない） |
| $R_{i,t+1}$ | 翌月の月次リターン（drtn = 配当込み、srtn = 固有） |

1. **スタイルスコア**: $z_{i,f,t} = \mathrm{blom}(d_f x_{i,f,t})$、
   $S_{i,k,t} = \mathrm{blom}\big(\mathrm{mean}_{f \in \mathcal{F}_{i,k,t}} z_{i,f,t}\big)$。
   サイズは $S_{i,\mathrm{Size},t} = \mathrm{blom}(-\log w^{U}_{i,t})$（小型ほど高い）。
2. **日次スタイルリターン**: $p = (\mathrm{rank} - 0.5)/N$ で $Q5 = \{p \ge 0.8\}$、$Q1 = \{p < 0.2\}$ を月末 $t$ に固定し、
   $\tau \in$ 月 $t+1$ で $g_{k,\tau} = \overline{r}_{Q5} - \overline{r}_{Q1}$（等ウェイト）、$G_{k,\tau} = \sum_{u \le \tau} \log(1+g_{k,u})$。
3. **類似局面予測**: 窓 $\mathbf{c}_{k,u} = (G_{k,u-L+j} - G_{k,u-L})_{j=1}^{L}$、クエリ $\mathbf{q}_{k,t} = \mathbf{c}_{k,\tau_t}$。
   候補は $u \le \tau_t - \max(H, G_{\min})$（翌 $H$ 日が確定し、クエリの窓と重ならない）。
   距離は Sakoe-Chiba 帯つき DTW。距離の小さい順に、終端が互いに $G_{\min}$ 日以上離れた $K$ 個を選び、
   $\hat\mu_{k,t} = \frac{1}{K}\sum_m (G_{k,u_m+H} - G_{k,u_m})$。
4. **個別株スコア**: $\alpha_{i,t} = \mathrm{blom}\big(\sum_k \tilde\mu_{k,t} S_{i,k,t}\big)$、$\tilde\mu_{k,t} = \hat\mu_{k,t}/\hat\sigma_{k,t}$。
   $\hat\sigma_{k,t}$ は日次標準偏差（拡大窓）$\times \sqrt{H}$。スタイルスコアの欠損は 0 とし、5 スタイル未満の銘柄は欠損。
5. **評価**: Q5（等ウェイトの BM 超過、BM = MSCI India）→ IC（Spearman）→ Q5−Q1。drtn と srtn。

| パラメータ | 値 |
| --- | --- |
| $L$（窓） | 63 営業日 |
| $H$（ホライズン） | 21 営業日（1 か月） |
| $K$（近傍数） | 3 |
| $G_{\min}$（近傍どうし・クエリとの最小間隔） | 63 営業日 |
| DTW の帯幅 | 6（0.1 $L$） |
| スタイルリターンの期間 | 2008-02〜（2007 年以前はユニバースが 15〜52 銘柄しかないため除外） |
| 評価期間 | 2011-01〜2026-06（2008〜2010 年は照合用の過去データをためる期間） |

### 比較するスコア

| スコア | $\tilde\mu_{k,t}$ |
| --- | --- |
| `sdtw` | DTW 予測 / $\hat\sigma$（本命） |
| `sdtw_z` | 窓ごとに標準化した形で DTW 照合 / $\hat\sigma$ |
| `sdtw_sign` | $\mathrm{sign}(\hat\mu)$ |
| `sdtw_pos` | $\max(\hat\mu, 0)/\hat\sigma$ |
| `sknn_euc` | ユークリッド距離の kNN / $\hat\sigma$ |
| `base_ew` | 1（全スタイル等ウェイト） |
| `base_hist` | 過去の平均リターン（拡大窓）/ $\hat\sigma$ |
| `base_mom12` | 252 日モメンタム / $\hat\sigma$ |
| `base_mom3` | 63 日モメンタム / $\hat\sigma$ |

## 符号表

スタイル内で向きをそろえる目的で決めた（リターンは見ていない）。素の値でのスタイル内順位相関を確認し、
逆向きに相関していたもの（レバレッジ・倒産リスク・外部資金調達・総合株式発行など）を反転した。
DTW 予測はスタイルの向きに依存しない（予測の符号が向きを吸収する）が、`base_ew` の解釈のため、各スタイルは
「プレミアムが期待される側」が高くなる向きにした（Volatility は低ボラ、Size は小型）。

| スタイル | 反転（$d_f = -1$） | そのまま（$d_f = +1$） |
| --- | --- | --- |
| Stock Value | | bp_est, bp_act |
| Flow Value | | 全 18 本（PEG は元データで割安ほど高い向き） |
| Profitability | | 全 25 本 |
| Growth | | 全 11 本 |
| Shareholder Return | csi | doe, eiss, diss, npop（eiss / diss は元データで発行が少ないほど高い向き） |
| Momentum | | 全 9 本 |
| Volatility | vola60, skew60 | |
| Financial Quality | xfin_bs, xfin_cf, xfin_sp, ccc, sales_bep, tdint, dsr, gmi, aqi, depi, sgai, levi, td_ta, tl_ta, oscore, mscore | acc_bs, acc_cf（元データで負のアクルーアルの向き）, curr, ta_to, rec_to, invent_to, acpy_to, eqr, mve_tl, eq_tl, ocftd, zscore |
