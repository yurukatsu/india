## 税控除後リターンの定式化

### 入力

- $t = 0, 1, \dots, T$：リバランス時点（月末）
- $P_{i,t}$：銘柄 $i$ の株価、$r_{i,t} = P_{i,t}/P_{i,t-1} - 1$
- $w_{i,t}$：時点 $t$ のリバランス後ウェイト（所与）
- $\tau^{S}_t,\ \tau^{L}_t$：時点 $t$ に適用される STCG / LTCG 税率（所与の系列）
- $H$：長期判定の保有期間閾値（12 か月、日付ベース）
- $f(t)$：時点 $t$ が属する税務年度（4 月〜3 月）
- $\kappa_{\text{buy}},\ \kappa_{\text{sell}}$：売買コスト率（手数料・STT・スプレッド・インパクト）

### 状態変数（経路依存性の源）

ウェイトが所与でも、税額は「どのロットをいくらの含み益で売ったか」に依存するため、次の状態を逐次更新する。

- $V_t$：ポートフォリオ価値（税・コスト控除後）
- $q_{i,t} = \dfrac{w_{i,t} V_t}{P_{i,t}}$：保有数量
- ロット台帳 $\mathcal{L}_{i,t} = \{ (a_k, n_k, c_k) \}_k$：銘柄 $i$ の各ロットの取得日 $a_k$、残数量 $n_k$、取得単価 $c_k$（取得日順）
- 損失繰越プール $\Lambda^{S}_f,\ \Lambda^{L}_f \le 0$：税務年度 $f$ 末時点の未使用短期損失・長期損失

$q_{i,t}$ が $V_t$ に依存し、$V_t$ が税額に依存するため、時系列は逐次計算となる（ベクトル化不可）。

### 1 期の更新手順（時点 $t$）

**(1) グロスリターン**

$$
r^{p}_t = \sum_i w_{i,t-1}\, r_{i,t}, \qquad V^{-}_t = V_{t-1}(1 + r^{p}_t)
$$

**(2) 売買数量**（暫定的に $V^{-}_t$ で目標数量を決める）

$$
\hat{q}_{i,t} = \frac{w_{i,t} V^{-}_t}{P_{i,t}}, \qquad
\Delta q_{i,t} = \hat{q}_{i,t} - q_{i,t-1}, \qquad
x_{i,t} = \max(0, -\Delta q_{i,t}), \quad
y_{i,t} = \max(0, \Delta q_{i,t})
$$

回転率（片道）

$$
\text{TO}_t = \frac{1}{2}\sum_i \left| w_{i,t} - w^{-}_{i,t} \right|,
\qquad
w^{-}_{i,t} = \frac{w_{i,t-1}(1 + r_{i,t})}{1 + r^{p}_t}
$$

売買コスト

$$
C_t = \sum_i \left( \kappa_{\text{sell}}\, x_{i,t} + \kappa_{\text{buy}}\, y_{i,t} \right) P_{i,t}
$$

**(3) 売却のロット充当（FIFO）と実現損益**

銘柄 $i$ の売却数量 $x_{i,t}$ を台帳の先頭ロットから順に消費する。ロット $k$ から $m_k$ 単位を充当したとき

$$
g_{k,t} = \left( P_{i,t} - c_k \right) m_k, \qquad
\ell_{k,t} =
\begin{cases}
L & \text{if } t - a_k > H \\
S & \text{otherwise}
\end{cases}
$$

期中の実現損益を区分集計する。

$$
G^{S}_t = \sum_{k:\, \ell_{k,t} = S} g_{k,t}, \qquad
G^{L}_t = \sum_{k:\, \ell_{k,t} = L} g_{k,t}
$$

買付分は新ロット $(t,\ y_{i,t},\ P_{i,t})$ として台帳末尾に追加する。

**(4) 税額（年度内通算・繰越つき）**

税務年度 $f$ の年初から時点 $t$ までの累計実現損益を

$$
\bar{G}^{S}_t = \sum_{s \in f(t),\ s \le t} G^{S}_s, \qquad
\bar{G}^{L}_t = \sum_{s \in f(t),\ s \le t} G^{L}_s
$$

とし、前年度からの繰越損失を加えた上で相殺ルールを適用する。

$$
\begin{aligned}
A^{S} &= \bar{G}^{S}_t + \Lambda^{S}_{f(t)-1}, \qquad
A^{L} = \bar{G}^{L}_t + \Lambda^{L}_{f(t)-1} \\[4pt]
\text{(i)}\quad & A^{L} < 0 \ \Rightarrow\ A^{L} \leftarrow 0
\quad \text{（長期損失は長期益とのみ相殺、残りは繰越）} \\
\text{(ii)}\quad & A^{S} < 0 \ \Rightarrow\
u = \min(-A^{S},\ A^{L}),\ \
A^{L} \leftarrow A^{L} - u,\ \
A^{S} \leftarrow 0
\quad \text{（短期損失は短期益→長期益の順に相殺）}
\end{aligned}
$$

年初来累計税額と当期税額は

$$
\bar{T}_t = \tau^{S}_t A^{S} + \tau^{L}_t A^{L}, \qquad
T_t = \bar{T}_t - \bar{T}_{t-1}
\quad (\text{年度初月は } \bar{T}_{t-1} = 0)
$$

差分で定義することで、期中に損失が実現した場合は $T_t < 0$（既計上税額の戻し）となり、年度合計は年次課税と一致する。年度末には未使用の $A^{S}, A^{L}$ の負値を $\Lambda^{S}_f, \Lambda^{L}_f$ として次年度へ繰り越す。

**(5) 税控除後の価値・リターン**

$$
V_t = V^{-}_t - C_t - T_t, \qquad
q_{i,t} = \frac{w_{i,t} V_t}{P_{i,t}}
$$

$$
r^{\text{gross}}_t = r^{p}_t, \qquad
r^{\text{net,TC}}_t = r^{p}_t - \frac{C_t}{V_{t-1}}, \qquad
r^{\text{net}}_t = r^{p}_t - \frac{C_t + T_t}{V_{t-1}}
$$

厳密には $V_t$ 確定後に $q_{i,t}$ が (2) の $\hat{q}_{i,t}$ からわずかにずれるが、$C_t + T_t \ll V_t$ であるため (2) の売買数量をそのまま用いて差分は次期のドリフトに吸収させる。

### 補助指標

- 短期比率：$\text{SR}_f = \dfrac{\sum_{t \in f} G^{S+}_t}{\sum_{t \in f} \left( G^{S+}_t + G^{L+}_t \right)}$
- 未実現損益（post-liquidation 用）：$U^{S}_t, U^{L}_t = \sum_{k} (P_{i,t} - c_k)\, n_k$ を保有期間で区分
- 清算時税額：$\tau^{S}_t \max(U^{S}_t, 0) + \tau^{L}_t \max(U^{L}_t, 0)$

---

## データ保持の設計

### 入力テーブル

| テーブル | キー | 列 | 備考 |
|---|---|---|---|
| prices | (date, stock) | close, adj_factor | 分割・ボーナス株はロット数量側で調整 |
| weights | (date, stock) | w | リバランス後目標ウェイト |
| tax_rates | date | stcg, ltcg | 適用開始日ベース |
| calendar | date | fiscal_year, is_rebalance | 税務年度は 4–3 月 |

### 状態テーブル（逐次更新）

| テーブル | キー | 列 | 備考 |
|---|---|---|---|
| lots | (stock, lot_id) | acq_date, qty_remaining, unit_cost | FIFO のためロットは取得日順に保持 |
| loss_carry | fiscal_year | stcl, ltcl, expiry_year | 8 年で失効 |
| nav | date | V | |

### 出力テーブル（監査・分析用）

| テーブル | キー | 列 |
|---|---|---|
| trades | (date, stock) | side, qty, price, cost |
| realized | (date, stock, lot_id) | qty, acq_date, holding_days, unit_cost, sell_price, gain, class(S/L) |
| monthly | date | r_gross, TO, C, G_S, G_L, T, r_net_TC, r_net, U_S, U_L |
| annual | fiscal_year | sum of above, SR, tax_paid, carry_in, carry_out |

`realized` をロット単位で残しておくことが要点で、月次集計はここから再計算できる。税率変更やしきい値変更の再計算もこのテーブルからやり直せる。

### 実装上の注意

- **初期化バイアス**：$t=0$ で全ロットの取得日が同一になるため、最初の 12 か月は全売却が STCG になる。評価期間は 12 か月以上のバーンイン後から取る。
- **保有期間判定は日付**：月インデックスの差ではなく `sell_date > acq_date + 12 months` で判定する。月次リバランスでは 12 か月ちょうどが短期に落ちるケースがある。
- **コーポレートアクション**：分割・ボーナス株は既存ロットの数量と単価を按分調整し、取得日は維持する（インドでは取得日が引き継がれる）。
- **配当**：キャピタルゲインとは別課税（源泉徴収）。本定式化の対象外とし、$r_{i,t}$ に配当を含める場合は別途源泉税率を控除する。
- **ウェイト所与の制約**：この枠組みでは $w_{i,t}$ が外生なので、税を考慮した売却先送りは反映されない。税を意識した構築（ロット単位のペナルティ）は最適化側の話であり、ここでは評価のみを行う。
