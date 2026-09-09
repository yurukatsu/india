# input

分析に用いるインプットデータ。フォーマットの定義は `./template/` 配下の README を参照。
`data/` の生データから `scripts/build_input.py` で生成する（`.dat` / `.pkl` は git 管理外）。
ユニバースごとにディレクトリを分け、現状は `msci_india_imi/` のみ。

```bash
uv run python scripts/build_input.py all            # すべて生成（既定の出力先 input/msci_india_imi）
uv run python scripts/build_input.py univ bm        # 一部のみ
uv run python scripts/build_input.py alpha --alpha-groups core alt --gzip
uv run python scripts/build_input.py all --out-dir input/another_universe
```

## ディレクトリ構造

```txt
./input/
├── msci_india_imi/
│   ├── univ/YYYYMM.dat                     # ユニバース（MSCI India IMI 想定、時価総額ウェイト）
│   ├── bm/
│   │   ├── msci_india/YYYYMM.dat           # MSCI India プロキシ（universe.size ∈ {1,2}）
│   │   ├── msci_india_imi/YYYYMM.dat       # MSCI India IMI プロキシ（全銘柄）
│   │   └── LIST.md
│   ├── risk_models/GEMLTL/                 # BARRA GEMLTL
│   │   ├── exposure/YYYYMM.pkl             # date, bid, factor_id, value
│   │   ├── factor_covariance/YYYYMM.pkl    # date, factor_id_1, factor_id_2, value（上三角 + 対角）
│   │   ├── factor_return/YYYYMM.pkl        # date, factor_id, value
│   │   ├── return/YYYYMM.pkl               # date, bid, rtn, srtn, *_usd, fwd_*_{1..12}m
│   │   ├── factor_list.csv
│   │   └── return_list.csv
│   └── alpha/
│       ├── core/{factor}/YYYYMM.dat        # コアファクター 123 本
│       ├── ai/ai_v1/YYYYMM.dat             # AI スコア（月末スナップショット）
│       ├── alt/{id}_{name}/YYYYMM.dat      # オルタナティブファクター 36 本
│       ├── cgo/{cgo_Nm}/YYYYMM.dat         # Capital Gain Overhang
│       ├── composite/{score}/YYYYMM.dat    # 合成スコア v1
│       ├── reprisk/repr_current_rri/YYYYMM.dat  # RepRisk Index（RRI）
│       └── LIST.md
└── template/                               # フォーマット定義
```

## 銘柄コード

すべての出力の銘柄コードは BID（`IND` + 英数字 3 桁 + 数字 1 桁、計 7 桁。例 `INDAAA1`, `INDB201`）。
`data/` の全データセット（universe / barra / core / ai / alt / cgo / composite）を走査し、
SEDOL・CUSIP・ISIN・GID が混入していないことを確認済み（`gid == bid` も全期間で成立）。
スクリプトは出力時に BID 形式を検証し、不一致があれば例外で停止するため、`map_code` による変換は不要。

## 変換ルール

| 出力 | 元データ | ルール |
| --- | --- | --- |
| `univ` | `data/universe` | `weight = cap / Σcap`（浮動株調整後時価総額）。`cap` 欠損・非正は除外 |
| `bm/msci_india` | `data/universe` | `size ∈ {1, 2}` を `cap` で正規化。MSCI 公式ウェイトではなくプロキシ |
| `bm/msci_india_imi` | `data/universe` | 全銘柄を `cap` で正規化（`univ` と同一内容） |
| `risk_models/.../exposure` | `data/barra/exp` | 列名変更のみ（`yyyymm→date`, `fcd→factor_id`, `exp→value`） |
| `risk_models/.../factor_covariance` | `data/barra/fctcov` | `fcd1/fcd2` を int 化。元データ同様 **上三角 + 対角のみ**（年率 %²）。行列化時は対称補完が必要 |
| `risk_models/.../factor_return` | `data/barra/fctrtn` | `fcd` を int 化。小数表記（0.0174 = 1.74%） |
| `risk_models/.../return` | `data/barra/rtn`, `data/fx.csv` | `lag == 0` を当月、`lag == 1〜12` を将来単月リターンとして横持ち。％ → **小数** に換算。USD 建ては下記 |
| `risk_models/.../factor_list.csv` | `data/barra/barra_factor_list.csv` | `id=fcd`, `symbol=fac` の `GEMLTL_` 除去, `name=fdname` の `GEM_` 除去, `group=fgroup` の番号除去 |
| `alpha/core` | `data/factor/core` | 列ごとに 1 スコア。`-1e9` センチネルを欠損として除外。`Seasonality` は生成しない |
| `alpha/ai/ai_v1` | `data/factor/ai` | 月内最終営業日の値 |
| `alpha/alt` | `data/factor/alt/{id}` | `effective_yyyymmdd ≤ 月末` の行のみ（ルックアヘッド防止）。同一 `bid` は最新発効日を採用 |
| `alpha/cgo`, `alpha/composite`, `alpha/reprisk` | `data/cgo`, `data/composite`, `data/reprisk` | 列ごとに 1 スコア（`YYYYMM.pkl` / `YYYYMM.csv`）。NaN 行は除外 |

### リターン（`risk_models/GEMLTL/return`）

`barra/rtn` の `rtn` / `srtn` はともに INR 建て・％表記。これを小数に直し、`fx.csv`（1USD あたり INR）で USD 建てを追加する。
さらに `lag = 1〜12` の行を将来単月リターン `fwd_*` として横持ちにする（計 52 列。一覧は `return_list.csv`）。

| 列 | 定義 |
| --- | --- |
| `rtn` | `barra/rtn.rtn / 100`（基準月 t の INR 建てトータルリターン、`lag = 0`） |
| `srtn` | `barra/rtn.srtn / 100`（基準月 t の INR 建て固有リターン、`lag = 0`） |
| `rtn_usd` | `(1 + rtn) · fx[t−1] / fx[t] − 1` |
| `srtn_usd` | `(1 + srtn) · fx[t−1] / fx[t] − 1`（機械的換算。下記注意） |
| `fwd_rtn_{k}m` | t+k 月の **単月** トータルリターン（INR、`lag = k`）。k = 1〜12 |
| `fwd_srtn_{k}m` | t+k 月の単月固有リターン（INR、`lag = k`） |
| `fwd_rtn_usd_{k}m` | `(1 + fwd_rtn_{k}m) · fx[t+k−1] / fx[t+k] − 1` |
| `fwd_srtn_usd_{k}m` | `fwd_srtn_{k}m` を同じ式で機械的に USD 換算 |

- 行は `lag = 0` を持つ銘柄。将来月の行が無い場合（期間末尾、途中で脱落した銘柄、為替レートが無い月）は NaN。
- `fwd_*_{k}m` は単月リターンなので、k ヶ月累積が必要なら `Π(1 + fwd_rtn_{i}m) − 1`（i = 1..k）と複利計算する。
- 基準月 t の `fwd_rtn_{k}m` は t+k 月ファイルの `rtn` と一致することを確認済み（INR / USD、total / specific とも）。
- `rtn` は `universe.drtn`（配当込み現地通貨リターン）と大半の銘柄で一致し、`rtn_usd` の換算式は `universe.rtn_usd` を誤差 1e-5 で再現することを確認済み。
- 固有リターンは本来ニューメレール（表示通貨）に依存しない。GEM モデルでは為替の影響は通貨ファクターに帰属し、残差である `srtn` は INR 建てでも USD 建てでも同じ値になる。`srtn_usd` / `fwd_srtn_usd_*` は「リターン列をすべて USD 換算する」用途のために機械的に作った列であり、為替リターン分だけ INR 建てから乖離する。リスク分解・固有リターン分析には INR 建てをそのまま使うことを推奨。

注意:

- `alpha` はユニバースで絞り込んでいない（`alt` は universe 外の銘柄を含む）。利用側で `univ` と結合すること。
- `data/turnover`, `data/map_code` は変換対象外。
