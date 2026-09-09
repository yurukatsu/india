# alpha

期待リターン（アルファ）のデータフォーマットについて整理します。

## 1. ファイルフォーマット

- ファイル形式：`dat` または `csv`
- gzip圧縮：可能（例：`.dat.gz`、`.csv.gz`）
- 区切り文字：
    - `dat`ファイル：半角スペース（` `）
    - `csv`ファイル：カンマ（`,`）

## 2. データ形式

| 項目名 | 説明 |
|---|---|
| `code` | 対象銘柄の取引コードを指定。今回は `bid`（BARRA ID）を指定 |
| `alpha` | 対象銘柄コードに対するalpha値 |

## 3. 入力サンプル

### 3.1 dat形式

一行目にヘッダー `#bid ${SCORE_NAME}` を記載。

```text
#bid alpha
INDABV1	1
INDAGI1	1
INDAVD1	0.8
INDBOT1	0.8
INDDTA1	0
INDCWV1	-0.3
```

### 3.2 csv形式

一行目にヘッダー `#bid, ${SCORE_NAME}` を記載。
`csv` の場合ヘッダーが省略されている場合もある。

```csv
INDABV1, 1
INDAGI1, 1
INDAVD1, 0.8
INDBOT1, 0.8
INDDTA1, 0
INDCWV1, -0.3
```

## 4. アルファの格納方法

スコアごとにディレクトリを作成し、基準日ごとにファイルを作成。
ディレクトリ名はスコア名にすることを推奨。また、ディレクトリの階層は深くてもいい。
ファイル名は `YYYYMM.dat` または `YYYYMMDD.dat` にすること。

```text
.
└── alpha
    ├── group_a
    │   ├── factor_a_1
    │   │   └── yyyymm.dat
    │   └── factor_a_2
    │       └── yyyymm.dat
    └── factor_b
        └── yyyymm.dat
```

## 5. 注意事項

- `code`には該当株の取引コードを指定する。
- `alpha`には該当コードに対するalpha値を指定する。欠損値なし。
- ファイルはgzip形式で圧縮してもよい。


## 6. アルファリスト

`./LIST.md` に作成したアルファ概要を記載しておく。
