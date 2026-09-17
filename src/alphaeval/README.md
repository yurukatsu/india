# alphaeval

`input/template` のデータスキーマに基づくアルファ評価ライブラリ。特定のデータセットや市場に依存せず、
日付 × 銘柄のワイドパネル（`pandas.DataFrame`）で動作する。評価方法は `docs/india_tcg.md` に従う。

```txt
alphaeval/
├── io/            # input/template スキーマの読み書き（InputStore, dat/csv, risk_models）
├── dates.py       # YYYYMM ⇄ Timestamp
├── panel.py       # 期間リターン合成、フォワードリターン、ユニバースマスク
├── coverage.py    # カバレッジ
├── ic.py          # IC（Spearman / Pearson）、IC 要約、IC decay
├── quantile.py    # 分位分析（通常 / バッファ付き、等・時価・√時価ウェイト、キャップ、回転率）
├── metrics.py     # Return / Risk / R/R / MaxDD / Turnover / TE / IR / β
├── tax/
│   ├── schedule.py  # 税率スケジュール（インド FPI プリセット、CSV 読み込み、定数）
│   └── ledger.py    # FIFO ロット台帳による税控除後シミュレーション
└── pipeline.py    # evaluate_alpha: 上記を一括実行
```

## データ規約

| オブジェクト | 形 | 意味 |
| --- | --- | --- |
| `universe` / `benchmark` | 日付 × 銘柄（ウェイト、非構成銘柄は NaN） | `univ/` `bm/{name}/` の `dat` |
| `score` | 日付 × 銘柄（スコア、欠損は NaN） | `alpha/{name}/` の `dat` |
| `returns` | 日付 × 銘柄（小数） | 行 `t` は「`t` で終わる期間のリターン」 |
| `weights` | 日付 × 銘柄（合計 1） | 行 `t` は「`t` 時点のリバランス後ウェイト」で、次の期間に適用 |

- 日付は `YYYYMM`（月末扱い）/ `YYYYMMDD` の整数でも `Timestamp` でもよい。内部では `DatetimeIndex` に揃える。
- 順位パーセンタイルは中央順位 `(rank − 0.5) / N`。「上位 20%」は `p ≥ 0.8`。
- 分位番号は `Q1` が最上位（スコア高）。`higher_is_better=False` で反転。

## 使い方

```python
from alphaeval import InputStore, evaluate_alpha, india_tax_schedule

store = InputStore("input/msci_india_imi")
univ = store.universe(200812, 202606)
bm = store.benchmark("msci_india", 200812, 202606)
rtn = store.returns("GEMLT", "rtn", 200812, 202607)  # 評価に使う 1 か月先まで読む
score = store.alpha("core/roe_act", 200812, 202606)

res = evaluate_alpha(
    score,
    univ,
    rtn,
    bm,
    n_quantiles=5,
    buffers=(0.0, 0.1, 0.2),  # 通常分位 + バッファ付き
    scheme="equal",  # "size" / "sqrt_size" も可（size 省略時は univ のウェイトを使う）
    max_weight=None,  # 分位内 1 銘柄上限
    tax_schedule=india_tax_schedule("trust"),  # FPI 区分に応じて "company"
    tax_quantiles=(1,),  # 税シミュレーションを行う分位
    cost_buy=0.0015,
    cost_sell=0.0025,  # 手数料 + STT + スプレッド等
    burn_in=12,  # 初期化バイアス回避（§5.4）
)
res.coverage_summary  # カバレッジ
res.ic_summary  # ホライズン別 IC（mean / std / ICIR / t 値 / hit rate）
res.summary  # バッファ × 分位ごとの基本指標 + 税控除後指標
res.quantiles[0.1].returns  # バッファ 0.1 の分位別リターン系列
res.tax[
    (0.0, 1)
].annual  # 税務年度別（グロス / コスト後 / 税後、回転率、短期比率、繰越）
res.tax[(0.0, 1)].realized  # ロット単位の実現損益（監査・再計算用）
```

部品を個別に使う例:

```python
from alphaeval import (
    quantile_analysis,
    performance_table,
    portfolio_returns,
    simulate_after_tax,
    constant_tax_schedule,
)

q = quantile_analysis(
    score, rtn, univ, n_quantiles=5, buffer=0.1, scheme="sqrt_size", max_weight=0.05
)
table = performance_table(q.returns, portfolio_returns(bm, rtn), q.turnover)
sim = simulate_after_tax(
    q.weights[1],
    rtn,
    constant_tax_schedule(0.2392, 0.1495),
    cost_buy=0.001,
    cost_sell=0.002,
)
```

## 税シミュレーションの仕様（`docs/india_tcg.md` §4）

- 価格は `returns` から作る価格指数（`Π(1 + r)`）。配当込みリターンを渡すと配当分もキャピタルゲインに含まれる。
- 売却は FIFO でロット充当。長期判定は日付ベース `sell > acq + ltcg_months`（12 か月ちょうどは短期）。
- 税額は税務年度内の累計損益に相殺ルール（短期損失 → 短期益 → 長期益、長期損失 → 長期益のみ）を適用し、
  年初来累計税額の差分として月次に計上する（損失実現月は税の戻し）。未使用損失は年度末に繰越（既定 8 年で失効）。
- 出力は `monthly`（`r_gross` / `r_net_tc` / `r_net`、回転率、コスト、税、未実現損益、清算時税額）、
  `annual`（短期比率、繰越）、`realized`（ロット単位）、`trades`。
- ウェイトは外生なので税を意識した売却先送りは反映しない（評価のみ）。

## テスト

```bash
uv run pytest
```

## 最適化出力（bax）の評価: `alphaeval.bax`

最適化システム bax の出力ディレクトリ（`A*.dat` `O*.dat` `S*.dat` `me_*.dat` `op_*.dat` `st_*.dat` `bc.txt`）を読み、
パフォーマンス・エクスポージャー・保有・寄与・アルファ実現度・税控除後を評価する。ファイル仕様と単位の規約は
`bax/reader.py` の冒頭に記載。ウェイト・リターン・回転率・コスト・`omega` は小数に変換して返す。

```python
from alphaeval.bax import BaxOutput, evaluate_bax, after_tax
from alphaeval import InputStore, india_tax_schedule

out = BaxOutput("bax/output/v0")
store = InputStore("input/msci_india_imi")
sector = (store.alpha("attributes/gics") // 1_000_000).reindex(
    index=out.rebalance_dates
)

res = evaluate_bax(
    out,
    mapping=sector,
    tax_schedule=india_tax_schedule("trust"),
    tax_returns=store.returns("GEMLT", "rtn"),
)  # 税は INR 建てリターンで評価
res.summary  # 年率リターン、超過（コスト前後）、実現 TE / 推定 TE、IR、コストドラッグ、回転率 …
res.yearly  # 年別
(
    res.exposures["risk_index"],
    res.exposures["industry"],
)  # アクティブエクスポージャー統計と制約への張り付き
res.holdings  # 銘柄数、集中度、上下限への張り付き
res.contributions, res.group_contributions  # 銘柄・業種別の寄与
res.alpha_ic  # アルファスコアと翌月リターンの順位相関
res.tax.annual, res.tax_table  # 税務年度別、税控除後の月次表
```

使い方の Notebook: `notebooks/bax_evaluation_example.ipynb`。

### 最適化条件（NAV・制約）の確認

`bc.txt` の全パラメータ（説明書の項番 1〜105）をカタログ化している（`bax/params.py`）。

```python
out.settings()  # NAV、TE 目標、個別・業種・リスクインデックス制約、回転率、コスト、アルファ、期間 を解釈付きで一覧
out.describe_params()  # 全パラメータの値・既定値・既定値との差分（is_default）・説明
out.initial_nav()  # 最初の op ファイルの NAV
```

### リスクモデルによる分解（Style / Industry / Country / Currency / Market / Specific）

`A` ファイルのアクティブエクスポージャー（接頭辞 R = Style、I = Industry、C = Country、c = Currency）を
`factor_list.csv` のファクター ID に対応付け、超過リターンと推定リスクをグループ別に分解する。

```python
from alphaeval.bax import factor_attribution, risk_decomposition

fl = store.factor_list("GEMLT")
groups, per_factor = factor_attribution(
    out, store.factor_return("GEMLT"), fl
)  # 超過リターン（コスト前）= Σ x_k f_k + specific
rd = risk_decomposition(
    out, lambda d: store.factor_covariance("GEMLT", d), fl
)  # omega² = x'Fx（グループ別）+ specific
```
