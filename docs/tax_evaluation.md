# 税考慮評価の実装（`alphaeval.tax`）

`docs/india_tcg.md` §4「税控除後リターンの定式化」を、`src/alphaeval/tax/` に実装したもの。
本書は「何をどう計算しているか」「何を前提にしているか」「どう使うか」をまとめる。
数式の記号は `india_tcg.md` に合わせる。

---

## 1. 全体像

```
ウェイト w_{i,t}（所与）          リターン r_{i,t}（銘柄別）        税率スケジュール τ^S_t, τ^L_t
        │                               │                               │
        │                     価格指数 P_{i,t} = Π(1 + r)               │
        ▼                               ▼                               ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ simulate_after_tax（FIFO ロット台帳を月次で逐次更新）                     │
   │  1. グロスリターン      r^p_t = Σ w_{i,t-1} r_{i,t},  V^-_t = V_{t-1}(1+r^p_t) │
   │  2. 売買数量・回転率・売買コスト                                            │
   │  3. 売却の FIFO 充当 → ロット単位の実現損益を短期 / 長期に区分               │
   │  4. 税務年度内の累計損益に相殺ルール・繰越損失を適用 → 当期税額 T_t          │
   │  5. V_t = V^-_t − C_t − T_t,  r^net_t = r^p_t − (C_t + T_t)/V_{t-1}         │
   └─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
   monthly（月次表） / annual（税務年度別） / realized（ロット単位の実現損益） / trades
```

- **市場固有の要素は `TaxSchedule` に閉じ込める**。シミュレーション本体は税率・税務年度・長期判定閾値・繰越年数・相殺可否を `TaxSchedule` から受け取るだけで、インド固有の値は `india_tax_schedule()` というプリセット関数が作る。
- **ウェイトは外生**。税を意識した売却先送り（ロット単位のペナルティ）は最適化側の話で、ここでは評価のみ行う（`india_tcg.md` §5.4）。

## 2. 入力

| 引数 | 内容 | 備考 |
| --- | --- | --- |
| `weights` | リバランス日 × 銘柄のリバランス後ウェイト（合計 1、非保有は NaN / 0） | 分位ポートフォリオ（`quantile_analysis`）、bax の `wgt_o` など |
| `returns` | 日付 × 銘柄の期間リターン（小数） | 価格指数 $P_{i,t} = \prod_{s \le t}(1 + r_{i,s})$ の生成に使う。欠損は 0（価格据え置き） |
| `schedule` | `TaxSchedule` | 下記 §3 |
| `cost_buy`, `cost_sell` | 売買コスト率 $\kappa_{buy}, \kappa_{sell}$（約定金額比） | 手数料 + STT + スプレッドの合計を想定 |
| `initial_value` | 初期ポートフォリオ価値 $V_0$ | 既定 1.0 |
| `price_index` | リバランス日 × 銘柄の価格（任意） | 指定時は `returns` からの生成を使わない |
| `charge_initial_costs` | 初回構築の買付コストを計上するか | 既定 True |

**価格指数について**: 実際の株価ではなく、リターンから作った指数を「価格」として使う。数量 $q = wV/P$ と実現益 $(P_t - c_k) m_k$ は $P$ のスケールに対して整合するので、税額は正しく計算できる。配当込みリターンを渡すと配当分がキャピタルゲインに含まれる（配当課税は対象外、§5.4）。分割・ボーナス株は調整済みリターンを使う限り考慮不要。

**通貨について**: インドの課税は INR 建ての譲渡益に対するもの。USD 建てリターンで評価すると為替差損益がキャピタルゲインに混入する。`input/msci_india_imi/risk_models/GEMLT/return` の `rtn`（INR）を渡すのが本来の定義に近い。bax 出力の評価では `after_tax(out, schedule, returns=rtn_inr)` で差し替えられる（`notebooks/bax_evaluation_example.ipynb` §8 で USD と INR を比較している。差は年率 0.1% 程度）。

## 3. 税率スケジュール `TaxSchedule`

| 属性 | 意味 | インド（`india_tax_schedule`） |
| --- | --- | --- |
| `rates` | 適用開始日 × `stcg` / `ltcg` の実効税率（小数）。各行は次の行の開始日まで有効 | §1.5 の推移から生成 |
| `ltcg_months` | 長期判定の保有期間閾値（月） | 12 |
| `fiscal_year_start_month` | 税務年度の開始月 | 4（4 月〜翌 3 月） |
| `loss_carry_years` | 損失の繰越年数 | 8 |
| `short_offsets_long` | 短期損失を長期益と相殺できるか | True |
| `long_offsets_short` | 長期損失を短期益と相殺できるか | False |

`india_tax_schedule(fpi_type)` は、法定税率（STCG / LTCG）、cess、サーチャージ（`fpi_type = "trust"`（信託・AOP）または `"company"`（外国法人））の各変更日で

$$
\tau^{\text{eff}} = \tau^{\text{base}} \times (1 + \text{surcharge}) \times (1 + \text{cess})
$$

を計算する（`india_tcg.md` §1.4〜1.5）。現行（2024-07-23 以降、FY2016 以降のサーチャージ 15%）で信託扱いなら STCG 23.92% / LTCG 14.95%、外国法人なら 21.84% / 13.65%。2004-10〜2018-03 は LTCG 非課税（0%）。サーチャージはカストディアン確認後に `surcharge_override` で固定できる。

税率は **譲渡日** に適用される値を使う（`rate_at(date)`）。月次リバランスでは月末日。2024-07-23 の月中変更は、2024 年 7 月末の譲渡から新税率になる。

カスタムの系列（例: `india_equity_cgt_monthly.csv`）は `tax_schedule_from_frame(df, "stcg_eff", "ltcg_eff")` で読める。全期間一定なら `constant_tax_schedule(stcg, ltcg)`。

## 4. 1 期の更新（`simulate_after_tax` の中身）

リバランス日 $t_0 < t_1 < \dots < t_T$（`weights` のインデックス）について逐次計算する。

**(1) グロスリターンとドリフト**

$r_{i,t} = P_{i,t}/P_{i,t-1} - 1$、$r^p_t = \sum_i w_{i,t-1} r_{i,t}$、$V^-_t = V_{t-1}(1 + r^p_t)$。
リバランス前ウェイト $w^-_{i,t} = w_{i,t-1}(1 + r_{i,t}) / (1 + r^p_t)$、片道回転率 $\text{TO}_t = \tfrac12 \sum_i |w_{i,t} - w^-_{i,t}|$。

**(2) 売買数量とコスト**

目標数量 $\hat q_{i,t} = w_{i,t} V^-_t / P_{i,t}$、$\Delta q = \hat q - q_{t-1}$、売却 $x = \max(0, -\Delta q)$、買付 $y = \max(0, \Delta q)$。
$C_t = \sum_i (\kappa_{sell} x_i + \kappa_{buy} y_i) P_{i,t}$。

**(3) FIFO 充当と実現損益**

銘柄ごとにロットの deque（取得日順）を持つ。売却数量を先頭ロットから消費し、ロット $k$ から $m_k$ 単位を充当したとき

- 実現損益 $g = (P_{i,t} - c_k) m_k$
- 区分: **売却日 > 取得日 + `ltcg_months` か月** なら長期（L）、そうでなければ短期（S）。日付ベースで判定し、**12 か月ちょうどは短期**（`india_tcg.md` §1.1、§5.4）。取得日が月末なら `pandas.DateOffset(months=12)` で翌年同月末（2020-02-29 → 2021-02-28）

買付は新ロット $(t, y_i, P_{i,t})$ として末尾に追加する。すべての充当は `realized` 表（日付、銘柄、ロット ID、取得日、保有日数、数量、取得単価、売却価格、損益、区分）に残す。

**(4) 税額（年度内通算・繰越）**

税務年度 $f$ の年初からの累計 $\bar G^S_t, \bar G^L_t$ に、失効していない繰越損失 $\Lambda^S, \Lambda^L$（$\le 0$）を加えて

$$
A^S = \bar G^S_t + \Lambda^S,\qquad A^L = \bar G^L_t + \Lambda^L
$$

相殺ルール（`_apply_offsets`）:

1. 長期損失（$A^L < 0$）は長期益とのみ相殺（`long_offsets_short = False` なら短期益には使わない）
2. 短期損失（$A^S < 0$）は短期益に充てた後、残りを長期益と相殺（`short_offsets_long = True`）

課税対象 $A^S_{tax}, A^L_{tax}$（$\ge 0$）から年初来累計税額 $\bar T_t = \tau^S_t A^S_{tax} + \tau^L_t A^L_{tax}$ を求め、**当期税額は差分** $T_t = \bar T_t - \bar T_{t-1}$（年度初月は $\bar T_{t-1} = 0$）。期中に損失を実現すると $T_t < 0$（既計上税額の戻し）になり、年度合計は年次課税と一致する。

年度末（次の年度の最初の月に処理）: 未使用の損失を発生年度別のプール（`_LossPool`）に再配分する。古い損失から使われた前提で、残った損失は新しい年度に帰属させる（`_rebuild_pools`）。発生から `loss_carry_years` 年を超えたプールは失効する。

**(5) 税控除後の価値**

$V_t = V^-_t - C_t - T_t$、$q_{i,t} = \hat q_{i,t}$（数量は $V^-$ ベースのまま。$C_t + T_t \ll V_t$ のためずれは次期のドリフトに吸収させる。§4.4）。

$$
r^{\text{gross}}_t = r^p_t,\qquad
r^{\text{net,TC}}_t = r^p_t - \frac{C_t}{V_{t-1}},\qquad
r^{\text{net}}_t = r^p_t - \frac{C_t + T_t}{V_{t-1}}
$$

**補助指標（毎期）**: 保有ロットの未実現損益 $U^S_t, U^L_t$（保有期間で区分）と清算時税額 $\tau^S_t \max(U^S_t, 0) + \tau^L_t \max(U^L_t, 0)$（繰越損失は考慮しない）。

## 5. 出力 `TaxSimulationResult`

| 表 | キー | 主な列 |
| --- | --- | --- |
| `monthly` | リバランス日 | `r_gross` `r_net_tc` `r_net`、`turnover`、`cost`、`tax`、`gain_short` `gain_long`、`unrealized_short` `unrealized_long`、`liquidation_tax`、`value`、`stcg_rate` `ltcg_rate`、`fiscal_year`、`n_holdings` `n_lots` |
| `annual` | 税務年度 | `r_gross` `r_net_tc` `r_net`（複利）、`turnover`、`cost`、`gain_short` `gain_long`、`tax`、`short_ratio`、`carry_in_*` `carry_out_*`、`n_periods` |
| `realized` | 充当ごと | `date` `code` `lot_id` `acq_date` `holding_days` `qty` `unit_cost` `sell_price` `gain` `class` |
| `trades` | 売買ごと | `date` `code` `side` `qty` `price` `cost` |

`returns` プロパティは `monthly` から 3 段階のリターンだけを抜いたもの。

- **短期比率** $\text{SR}_f = \sum_t G^{S+}_t / \sum_t (G^{S+}_t + G^{L+}_t)$（月次の正の実現益の合計比、§4.5）
- **税ドラッグ** は利用側で `r_net_tc` と `r_net` の年率差として計算する（`alphaeval.metrics.annualized_return`）
- `realized` をロット単位で残しているので、税率や閾値を変えた再計算はこの表からやり直せる（§5.3）

## 6. 使い方

**分位ポートフォリオの税控除後評価**（`evaluate_alpha` に組み込み済み）

```python
from alphaeval import InputStore, evaluate_alpha, india_tax_schedule

store = InputStore("input/msci_india_imi")
res = evaluate_alpha(
    store.alpha("core/roe_act", 200812, 202606), store.universe(200812, 202606),
    store.returns("GEMLT", "rtn", 200812, 202607), store.benchmark("msci_india", 200812, 202606),
    buffers=(0.0, 0.1, 0.2), tax_schedule=india_tax_schedule("trust"), tax_quantiles=(1,),
    cost_buy=0.0015, cost_sell=0.0025, burn_in=12,
)
res.summary                 # r_net_tc_* / r_net_* / tax_drag_annual / short_ratio_mean を含む
res.tax[(0.1, 1)].annual    # バッファ 0.1・Q1 の税務年度別
```

**任意のウェイトを直接評価**

```python
from alphaeval import simulate_after_tax, india_tax_schedule
sim = simulate_after_tax(weights, returns, india_tax_schedule("trust"), cost_buy=0.0015, cost_sell=0.0025)
sim.monthly.iloc[12:]       # バーンイン後
```

**bax 出力の評価**（`alphaeval.bax.after_tax`）: bax の `fund_rtn` はすでに取引コスト控除後なので、シミュレーション側のコストは 0 にし、税額（NAV 比）だけを bax のリターンから差し引く。

```python
from alphaeval.bax import BaxOutput, after_tax
sim, table = after_tax(BaxOutput("bax/output/v0"), india_tax_schedule("trust"), returns=store.returns("GEMLT", "rtn"), burn_in=12)
table[["fund", "tax", "fund_after_tax", "bm", "excess_after_tax"]]
```

**除外スクリーンのコスト評価**: `experiments/reprisk/09_exclusion_cost.ipynb`（ベースとスクリーン版を同じ手続きで評価し、差分を追加ドラッグとして報告）。

## 7. 前提と注意点

| 項目 | 扱い | 影響 |
| --- | --- | --- |
| 初期化バイアス | $t_0$ に全ロットを取得するため、最初の 12 か月は売却がすべて短期になる | 評価は `burn_in=12` 以降で行う（§5.4） |
| 価格 = リターン指数 | 実際の株価・株数は使わない | 税額は正しいが、`trades` の数量・価格は実単位ではない |
| 配当 | 配当込みリターンを渡すと配当分がキャピタルゲインに含まれる | 配当源泉税は別途。厳密には価格リターンを渡す |
| 通貨 | 渡したリターンの通貨で損益を計算 | INR 建てリターンを推奨（§2） |
| 数量の丸め | $q_t = \hat q_t$（$V^-$ ベース） | 税・コスト分だけ保有価値が NAV を上回るが次期のドリフトで吸収 |
| 繰越損失の帰属 | 古い損失から使い、残りを新しい年度に帰属 | 失効判定にのみ影響。8 年以内なら税額は同じ |
| 年度内の税率変更 | 年初来累計に当月の税率を掛ける（$\bar T_t$ の差分） | 変更月に過去分の再評価が入る（`india_tcg.md` §4.4 の定義どおり）。2024-07 のみ該当 |
| グランドファザリング（2018-01-31 時価） | 未実装 | 2018 年以前からのロットを持つ実運用の再現には追加処理が必要（§5.4） |
| 損失繰越の申告要件・予定納税 | 未考慮 | 税額は取引日ベースで NAV から引き当てる想定（§1.2） |
| コーポレートアクション | 調整済みリターンに依存 | 取得日は引き継がれる前提（§5.4） |

## 8. 検証

`tests/test_tax.py`:

- `india_tax_schedule` の現行税率（信託 23.92% / 14.95%、外国法人 21.84% / 13.65%）、2004-10〜2018-03 の LTCG 0%、FY2017 の STCG 15% × 1.15 × 1.03
- `india_tcg.md` §1.3 のロット充当例（ロット A 100 単位 @100、B 100 単位 @110、翌年 150 単位 @130 で売却 → LTCG 3,000 / STCG 1,000、税 375 + 200）
- 12 か月ちょうどは短期、1 日超えれば長期
- 相殺ルール（短期損失 → 長期益、長期損失は短期益と相殺不可）
- 繰越プールの再配分と失効
- 税ありでリターンが下がること、フラットな価格で税が出ないこと

整合性: `simulate_after_tax` を税率 0・コスト 0 で走らせたグロスリターンと回転率は、`quantile_analysis` の分位リターン・回転率と誤差 1e-16 で一致する（`src/alphaeval` 実装時に実データで確認）。

## 9. 関連ファイル

| ファイル | 内容 |
| --- | --- |
| `docs/india_tcg.md` | 定式化と制度の整理（本書の元） |
| `src/alphaeval/tax/schedule.py` | `TaxSchedule`、`india_tax_schedule`、`constant_tax_schedule`、`tax_schedule_from_frame` |
| `src/alphaeval/tax/ledger.py` | `simulate_after_tax`、`price_index_from_returns`、`TaxSimulationResult` |
| `src/alphaeval/pipeline.py` | `evaluate_alpha` への組み込み |
| `src/alphaeval/bax/evaluate.py` | `after_tax`（bax 出力向け） |
| `tests/test_tax.py` | 単体テスト |
| `experiments/reprisk/09_exclusion_cost.ipynb` | 除外スクリーンの税コスト評価（利用例） |
| `notebooks/bax_evaluation_example.ipynb` §8 | bax 出力の税控除後評価（利用例） |
