# risk_models

リスクモデルのデータフォーマットについて整理します。

## 1. リスクモデルのフォルダ構造

リスクモデルごとにフォルダを作成し、以下のディレクトリ構造にする。


```text
.
└── ${RISK_MODEL}
    ├── exposure
    │   └── yyyymmdd.pkl
    ├── factor_covariance
    │   └── yyyymmdd.pkl
    ├── factor_return
    │   └── yyyymmdd.pkl
    ├── return
    │   └── yyyymmdd.pkl
    ├── factor_list.csv
    └── return_list.csv
```

## 2. ファイルフォーマット

### 2.1 エクスポージャーファイル

以下の構造を持つデータフレーム（ファイル形式：`csv`、`pkl`、`gz`)。
格納先は `${RISK_MODEL}/exposure/`。

| カラム名 | 型 | 説明 |
|---|---|--|
| `date` | `int` | 基準日。YYYYMM型 or YYYYMMDD型|
| `bid` | `str` | BARRA ID |
| `factor_id` | `int` or `str` | ファクター ID |
| `value` | `float` |  エクスポージャーの値 |

### 2.2 ファクター間共分散行列

以下の構造を持つデータフレーム（ファイル形式：`csv`、`pkl`、`gz`)。
格納先は `${RISK_MODEL}/factor_covariance/`。

| カラム名 | 型 | 説明 |
|---|---|--|
| `date` | `int` | 基準日。YYYYMM型 or YYYYMMDD型|
| `factor_id_1` | `int` or `str` | ファクター ID 1 |
| `factor_id_2` | `int` or `str` | ファクター ID 2 |
| `value` | `float` |  ファクター ID 1 と ファクター ID 2 の共分散|

### 2.3 ファクターリターン

以下の構造を持つデータフレーム（ファイル形式：`csv`、`pkl`、`gz`)。
格納先は `${RISK_MODEL}/factor_return/`。

| カラム名 | 型 | 説明 |
|---|---|--|
| `date` | `int` | 基準日。YYYYMM型 or YYYYMMDD型|
| `factor_id` | `int` or `str` | ファクター ID |
| `value` | `float` | ファクターリターンの値 |

### 2.4 リターン

以下の構造を持つデータフレーム（ファイル形式：`csv`、`pkl`、`gz`)。
格納先は `${RISK_MODEL}/return/`。

| カラム名 | 型 | 説明 |
|---|---|--|
| `date` | `int` | 基準日。YYYYMM型 or YYYYMMDD型|
| `bid` | `str` | BARRA ID |
| `${return_1}` | `float` | 1番目のリターンの値 |
| ... | ... | ... |
| `${return_n}` | `float` | 2番目のリターンの値 |

`${return_i}` に関しては、`./${RISK_MODEL}/return_list.csv` に `id` 列に登録されているもののみ利用可能。

### 2.5 ファクターリスト

以下の構造を持つデータフレーム（ファイル形式：`csv`、`pkl`、`gz`)。
ファイル名は `${RISK_MODEL}/factor_list.csv`。

| カラム名 | 型 | 説明 |
|---|---|--|
| `id` | `int` or `str` | ファクターID |
| `symbol` | `str` | ファクターシンボル。ファクター名の略記など。BARRA でいう `BTOB` (Book to Price) など。|
| `name` | `str` | ファクター名。BARRA でいう `Book to Price` など。 |
| `description` | `str` | ファクターの説明 |
| `group` | `str` | ファクターグループ名 |
| `group_alt_1` | `str` or `None` | （オプション）代理ファクターグループ名 |
| `group_alt_1` | `str` or `None` | （オプション）代理ファクターグループ名  |

### 2.6 リターンリスト

以下の構造を持つデータフレーム（ファイル形式：`csv`、`pkl`、`gz`)。
ファイル名は `${RISK_MODEL}/return_list.csv`。

| カラム名 | 型 | 説明 |
|---|---|--|
| `id` | `int` or `str` | リターンID。カラム名に利用。|
| `name` | `str` | リターン名。図などに利用されることがある。 |
| `description` | `str` | リターンの説明 |
