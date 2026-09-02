# india

## データ

### ディレクトリ構造

```txt
./data/
├── barra/
│   ├── exp/                     # 各銘柄のエクスポージャー
│   ├── fctcov/                  # BARRAファクター間共分散
│   ├── fctrtn/                  # BARRAファクターリターン
│   ├── rtn/                     # 各銘柄のリターン
│   └── barra_factor_list.csv    # BARRAファクター一覧
├── factor
│   ├── ai/                      # AIスコア
│   ├── alt/                     # オルタナティブスコア
│   │   ├── factor_id_1/
│   │   ├── factor_id_2/
│   │   └── factor_id_n/
│   ├── core/                    # コアファクター
│   └── core_factor_list.csv     # コアファクター一覧
├── map_code/                    # コードマッピング
├── universe/                    # ユニバース構成銘柄
└── fx.csv                       # 為替（USD/INR）
```

### データ概要

ベンチマークは MSCI India、ユニバースは MSCI India IMI を想定。
銘柄は BARRA ID `bid`（例: `INDAAA1`）で識別され、`sedol` / `isin` / `cusip` へのマッピングは `map_code/` にある。

`*.pkl` は 1 ファイル = 1 年月（ファイル名 `YYYYMM.pkl`）の `pandas.DataFrame` を pickle 化したもの。

| データ | パス | 粒度 | 期間（ファイル名ベース） | 1ファイルの行数目安 |
| --- | --- | --- | --- | --- |
| ユニバース | `universe/YYYYMM.pkl` | 年月 × 銘柄 | 200301〜202607 (283) | 37〜643 |
| コードマッピング | `map_code/YYYYMM.pkl` | 年月 × 銘柄 | 200301〜202607 (283) | ユニバースと同数 |
| BARRAエクスポージャー | `barra/exp/YYYYMM.pkl` | 年月 × 銘柄 × ファクター（縦持ち） | 200301〜202607 (283) | 約 6,700 |
| BARRAファクターリターン | `barra/fctrtn/YYYYMM.pkl` | 年月 × ファクター | 200001〜202607 (319) | 238 |
| BARRAファクター共分散 | `barra/fctcov/YYYYMM.pkl` | 年月 × ファクターペア | 200001〜202607 (319) | 28,441 |
| 銘柄リターン | `barra/rtn/YYYYMM.pkl` | 基準年月 × ラグ × 銘柄 | 200301〜202607 (283) | 銘柄数 × 13 |
| コアファクター | `factor/core/YYYYMM.pkl` | 年月 × 銘柄（横持ち） | 200301〜202607 (283) | ユニバースと同数 |
| AIスコア | `factor/ai/YYYYMM.pkl` | 日次 × 銘柄 | 201608〜202604 (117) | 約 3,900 |
| オルタナティブ | `factor/alt/{factor_id}/YYYYMM.pkl` | 年月 × 銘柄（1ファクター1系列） | ファクターにより異なる | 数十〜数百 |
| 為替 | `fx.csv` | 年月 | 197001〜202607 (679行) | - |

**欠損値の表現**: `factor/core/` と `universe/` の一部数値列では、`NaN` ではなく `-1e9`（`-999999998.0` 等の巨大負値）が欠損センチネルとして入っている。集計前に `df[df <= -1e8] = np.nan` 相当の置換が必要。

---

#### `universe/YYYYMM.pkl` — ユニバース構成銘柄

その年月に投資対象ユニバースへ含まれる銘柄と、基本属性・価格・簡易バリュエーション。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `yyyymm` | int64 | 基準年月（例: `202012`） |
| `gid` | object (str) | GID |
| `bid` | object (str) | BID |
| `sedol` | object (str) | SEDOL |
| `cusip` | object (str) | CUSIP |
| `name` | object (str) | 銘柄名（スペースは `_` 区切り。例: `RELIANCE_INDUSTRIES`） |
| `country` | object (str) | 国コード。本データは全て `IN` |
| `currency` | object (str) | 現地通貨コード。本データは全て `INR` |
| `gics` | object (str) | GICSコード（8桁 = サブ業種レベル。例: `10102030`） |
| `size` | int64 | サイズ区分。`1`=大型、`2`=中型、`3`=小型 |
| `shares` | float64 | 発行済株式数（百万株） |
| `cap` | float64 | 浮動株調整後時価総額（USD百万）|
| `price` | float64 | 月末株価（現地通貨 INR） |
| `price_usd` | float64 | 月末株価（USD換算） |
| `rtn` | float64 | 当月リターン（％、現地通貨ベース） |
| `rtn_usd` | float64 | 当月リターン（％、USDベース） |
| `drtn` | float64 | 当月配当込みリターン（％、現地通貨ベース） |
| `drtn_usd` | float64 | 当月配当込みリターン（％、USDベース） |
| `pbr` | float64 | 株価純資産倍率（PBR、倍） |
| `pcfr` | float64 | 株価キャッシュフロー倍率（PCFR、倍）。欠損は `-1e9` |
| `per` | float64 | 株価収益率（PER、倍） |
| `dividend_yield` | float64 | 配当利回り（％）。欠損は `-1e9` |
| `roe` | float64 | 自己資本利益率（％）。欠損は `-1e9` |

#### `map_code/YYYYMM.pkl` — コードマッピング

`bid` と外部識別子の対応表。外部データ結合時のブリッジとして使う。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `date` | object (str) | 基準日（月末日、`YYYY-MM-DD` 形式） |
| `bid` | object (str) | BID |
| `sedol` | object (str) | SEDOLコード（7桁） |
| `isin` | object (str) | ISINコード（12桁。例: `INE208A01029`） |
| `cusip` | object (str) | CUSIPコード（9桁） |
| `yyyymm` | object (str) | 基準年月の文字列（例: `'202012'`）。**int ではない点に注意** |

#### `barra/exp/YYYYMM.pkl` — BARRAファクターエクスポージャー

各銘柄の各BARRAファクターに対するエクスポージャー（縦持ち / long format）。1銘柄あたり 20〜24 行（リスクインデックス16本 + 該当する業種・国・通貨・マーケット）。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `yyyymm` | int64 | 基準年月 |
| `bid` | object (str) | 銘柄ID |
| `fcd` | int64 | BARRAファクターコード。`barra_factor_list.csv` の `fcd` に対応 |
| `exp` | float64 | ファクターエクスポージャー（リスクインデックスは標準化値、業種・国等はダミー的な値） |

横持ちにする場合: `df.pivot(index='bid', columns='fcd', values='exp')`。

#### `barra/fctrtn/YYYYMM.pkl` — BARRAファクターリターン

| カラム | 型 | 説明 |
| --- | --- | --- |
| `yyyymm` | int64 | 基準年月 |
| `fcd` | object (str) | BARRAファクターコード。**文字列型**（`exp` 側は int64 なので結合時に型を揃える必要あり） |
| `fctrtn` | float64 | 当月のファクターリターン（小数表記。例: `0.017375` = 1.74%） |

全238ファクター分の行が毎月入る。

#### `barra/fctcov/YYYYMM.pkl` — BARRAファクター共分散

238ファクターの共分散行列を縦持ちにしたもの（上三角 + 対角 = 28,441行 = 238×239/2）。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `yyyymm` | int64 | 基準年月 |
| `fcd1` | object (str) | ファクターコード（行側） |
| `fcd2` | object (str) | ファクターコード（列側） |
| `cov` | float64 | ファクター間共分散（年率換算・％²単位。対角は分散） |

行列化する際は `fcd1`/`fcd2` を入れ替えた対称成分を補完する必要がある。

#### `barra/rtn/YYYYMM.pkl` — 銘柄リターン（ラグ付き）

ファイル名の年月をユニバース基準月とし、そこから 0〜12 ヶ月先までのリターンを収めた将来リターン系列。`銘柄数 × 13` 行。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `yyyymm` | int64 | リターンが実現した年月（`yyyymm_unv` + `lag` ヶ月） |
| `bid` | object (str) | 銘柄ID |
| `rtn` | float64 | 当該月のトータルリターン（％表記。例: `3.581` = 3.58%） |
| `srtn` | float64 | BARRAモデルの固有リターン / 残差リターン（％表記） |
| `lag` | int64 | 基準月からの経過月数（0〜12） |
| `yyyymm_unv` | int64 | ユニバース基準年月（ファイル名と一致） |

当月リターンだけが欲しい場合は `lag == 0` で絞る。

#### `factor/core/YYYYMM.pkl` — コアファクター

126列の横持ちファクターテーブル。各ファクターの定義は `factor/core_factor_list.csv` を参照。

キー列:

| カラム | 型 | 説明 |
| --- | --- | --- |
| `dateym` | int64 | 基準年月（他データセットの `yyyymm` に相当） |
| `bid` | object (str) | 銘柄ID |
| `sedol` | object (str) | SEDOLコード |

残り123列はすべてファクター値（`availm12` / `availm60` / `intwo` のみ int64、他は float64）。カテゴリ別の内訳:

| カテゴリ | 主なカラム | 説明 |
| --- | --- | --- |
| バリュー | `bp_est`, `bp_act`, `ep_est`, `ep_act`, `dp_est`, `dp_act`, `sp_est`, `sp_act`, `ebitdaev_est`, `ebitdaev_act`, `cfp_est`, `cfp_act`, `ocfp`, `fcfp`, `rdp`, `maratio`, `dlt_ep3`, `dlt_ep6`, `tobin_q`, `peg_est`, `peg_act` | B/P・E/P・D/P・S/P・EBITDA/EV・CF/P 等の益回り系。`_est` は予想ベース、`_act` は実績ベース |
| 収益性 | `roe_est`, `roe_act`, `roa_est`, `roa_act`, `roic_est`, `roic_act`, `gpa_act`, `gpa_act_fy`, `gm_act`, `nis_est`, `nis_act`, `ois_est`, `ois_act`, `ocfs`, `prof`, `prof_ball_bs_fy` | ROE/ROA/ROIC、粗利益率、売上高利益率、営業CF比率 |
| 成長 | `ltg`, `sales_grw`, `oi_grw`, `eps_grw`, `ni_grw`, `ppe_grw`, `ta_grw`, `ta_grw_fy`, `invent_grw`, `eq_grw`, `capex_grw`, `ia`, `dlt_gpa_5y`, `dlt_roe_5y`, `dlt_roa_5y`, `dlt_ocfa_5y`, `dlt_gps_5y`, `chin` | 売上・利益・資産・設備投資の成長率、5年変化幅 |
| リビジョン | `csi`, `rev1p`, `rev1r`, `rev3p`, `rev3r` | アナリスト予想改定（1ヶ月/3ヶ月、パーセント方式とローゼンバーグ方式） |
| モメンタム / リスク | `mom1`, `mom12`, `mom12_1`, `mom60`, `vola60`, `skew60`, `availm12`, `availm60` | 各期間のモメンタム、60ヶ月ボラティリティ・歪度。`availm*` はデータ利用可能月数 |
| 流動性 | `tover`, `illiq` | 売買回転率、Amihud 非流動性指標 |
| 株主還元 | `doe`, `buyback`, `gpy`, `npy` | DOE、自社株買い、グロス／ネットペイアウト利回り |
| アクルーアル / 外部資金 | `acc_bs`, `acc_cf`, `acc_cf2`, `xfin_bs`, `xfin_cf`, `xfin_sp`, `eiss`, `diss`, `npop`, `noa_fy`, `noa_grw_fy` | 会計発生高、外部ファイナンス、株式・負債発行 |
| 効率性 / 財務健全性 | `curr`, `ta_to`, `rec_to`, `invent_to`, `acpy_to`, `ccc`, `sales_bep`, `eqr`, `wk_ta`, `re_ta`, `ebit_ta`, `mve_tl`, `sales_ta`, `sga_ta`, `rd_ta`, `ocf_ta`, `eq_tl`, `tdint`, `ocftd`, `td_ta`, `tl_ta`, `ni_ta`, `pti_tl`, `intwo` | 回転率、CCC、自己資本比率、レバレッジ、対総資産比率群。`intwo` は2期連続赤字フラグ（0/1） |
| 会計不正・倒産スコア | `dsr`, `gmi`, `aqi`, `sgi`, `depi`, `sgai`, `levi`, `oscore`, `zscore`, `mscore` | Beneish M-Score の構成要素、Ohlson O-Score、Altman Z-Score |
| サイズ | `mv`, `mv_usd`, `tmv`, `tmv_usd` | 株式時価総額 / 企業価値。`mv` は現地通貨（INR百万）、`mv_usd` は `mv ÷ fx` の USD百万 |

#### `factor/core_factor_list.csv` — コアファクター一覧

| カラム | 型 | 説明 |
| --- | --- | --- |
| `factor` | str | ファクター名（大文字。`factor/core` のカラム名を大文字化したもの） |
| `description` | str | 計算式・定義のメモ（Markdown混じりの複数行テキスト。空欄・断片のみの行も多い） |

#### `factor/ai/YYYYMM.pkl` — AIスコア

唯一の日次データ。1ファイルにその月の全営業日 × 銘柄のスコアが入る。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `yyyymmdd` | int64 | 基準日（例: `20201201`） |
| `bid` | object (str) | 銘柄ID |
| `ai` | float64 | AIファクタースコア（クロスセクションZスコア。概ね ±3.2 にクリップ） |
| `yyyymm` | object (str) | 基準年月の文字列（ファイル名と一致） |

#### `factor/alt/{factor_id}/YYYYMM.pkl` — オルタナティブファクター

ファクターIDごとにサブディレクトリが分かれ、1ファイル = 1年月 × 1ファクターの縦持ち系列。全データセットで同一スキーマ。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `effective_yyyymmdd` | int64 | データが利用可能になった日（発効日）。`data_yyyymmdd` に lag を加えた日付 |
| `data_yyyymmdd` | int64 | データの基準日（観測日） |
| `bid` | object (str) | 銘柄ID |
| `value` | float64 | ファクター値。スケールは `factor_list.csv` の `data_type` に依存（`Z`=Zスコア、`score`=生スコア、`binary`=0/1） |
| `yyyymm` | object (str) | 基準年月の文字列 |

ルックアヘッドバイアスを避けるため、月次バックテストでは `effective_yyyymmdd` を基準に利用可否を判定する。

収録されているファクターID（ディレクトリ）と期間:

| factor_id | ファクター名 | 期間 | ファイル数 |
| --- | --- | --- | --- |
| 1〜9 | 従業員クチコミ系（`overall_rating`, `culture_and_values`, `work_life`, `senior_management`, `comp_and_benefits`, `career_opportunities`, `recommend`, `ceo_rating`, `biz_outlook`） | 201605〜202607 | 123 |
| 11〜16 | ESGコントラバーシー系（`controversy_score_ms` 他、環境／顧客／統制／労働者／人権） | 201804〜202607 | 100 |
| 65〜68 | リビジョン系（`rev1p_gdb`, `rev1r_gdb`, `rev3p_gdb`, `rev3r_gdb`） | 201604〜202607 | 124 |
| 340〜355 | 自社ESGスコア（`nam_6_pillars_*` / `nam_3_pillars_*` / `nam_1_pillars_3_esg_score`。344 は欠番） | 201302〜202607 | 162 |
| 400 | `tvl`（TVLファクター、日次由来） | 200701〜202607 | 235 |
| 402 | `tvl_v3`（TVL_v3ファクター） | 200702〜202607 | 234 |

#### `factor/alt/factor_list.csv` / `factor_group_list.csv` — オルタナティブファクター定義

両ファイルは**内容が完全に同一**（448ファクター分の定義）。BOM付きUTF-8、`definition` 列に改行を含むため `pd.read_csv` でそのまま読める（`encoding='utf-8-sig'` 推奨）。定義されているファクターIDは448件あるが、実データが存在するのは上表の38ディレクトリのみ。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `factor_id` | int | ファクターID。`factor/alt/` のサブディレクトリ名に対応 |
| `group_id` | int | ファクターグループID（1=AI、3〜5=従業員クチコミ、8=ESGコントラバーシー、11〜17=財務系、18〜21=ESGスコア 等） |
| `factor_name` | str | ファクター名（英語） |
| `factor_name_jp` | str | ファクター名（日本語） |
| `frequency` | str | 更新頻度。`M`=月次、`D`=日次、`T`=不定期 |
| `data_type` | str | 値の型。`Z`=Zスコア、`score`=スコア、`binary`=0/1、`only 1`=フラグ |
| `area_data_method` | str | 地域別データ生成方法（全て `auto`） |
| `lag_type` | int | ラグの種類（1=基準日ベース、2=営業日ベース） |
| `lag_days` | int | ラグ日数。`data_yyyymmdd` → `effective_yyyymmdd` の遅延 |
| `definition` | str | データソース・更新タイミング・計算方法を記した定義文（Markdown、改行含む。空欄も多い） |
| `from_dateymd` | int | データ提供開始日（`YYYYMMDD`） |
| `to_dateymd` | int | データ提供終了日（現行は `99991231`） |
| `created_at` | str | レコード作成日時 |
| `updated_at` | str | レコード更新日時 |
| `create_update_user` | str | 更新者（全て空欄） |

#### `barra/barra_factor_list.csv` — BARRAファクター一覧

238ファクター（MSCI GEMLTL モデル）の定義。

| カラム | 型 | 説明 |
| --- | --- | --- |
| `fcd` | int | ファクターコード（101〜501）。`barra/exp` の `fcd`、`barra/fctrtn`・`fctcov` の `fcd`/`fcd1`/`fcd2` に対応 |
| `fac` | str | ファクター識別子（例: `GEMLTL_BETA`） |
| `fdname` | str | ファクター表示名（例: `GEM_Beta`） |
| `fnum` | int | 通し番号（1〜238）。共分散行列の並び順に対応 |
| `fgroup` | str | ファクターグループ（末尾に空白パディングあり、`.str.strip()` 推奨） |

`fgroup` の内訳と `fcd` の割り当て:

| fgroup | fcd 帯 | 本数 | 内容 |
| --- | --- | --- | --- |
| `1-Risk_Indices` | 101〜116 | 16 | Beta, Book-to-Price, Dividend Yield, Earnings Quality/Variability/Yield, Growth, Investment Quality, Leverage, Liquidity, Long-Term Reversal, Mid Capitalization, Momentum, Profitability, Residual Volatility, Size |
| `2-Industries` | 201〜245 | 45 | 業種ファクター（Aerospace & Defense, Airlines, … ） |
| `3-Countries` | 301〜388 | 88 | 国ファクター |
| `4-Currencies` | 401〜488 | 88 | 通貨ファクター |
| `5-Market` | 501 | 1 | マーケットファクター |

#### `fx.csv` — 為替レート

| カラム | 型 | 説明 |
| --- | --- | --- |
| `yyyymm` | int | 年月（`197001`〜`202607`、679行） |
| `currency` | str | 通貨コード。本ファイルは全て `INR` |
| `rate` | float | USD/INR レート（1USDあたりのINR。例: `7.547`） |

現地通貨建ての値を USD 換算する際に使用。`universe` の `price_usd = price ÷ rate`、`factor/core` の `mv_usd = mv ÷ rate` が同月の `rate` で厳密に一致することを確認済み。
