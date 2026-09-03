# fl.data — パネル構築

## build_panel — ほぼ全分析の起点

```python
panel = fl.data.build_panel(
    factors,                 # list[str]  factor/core のカラム名（例 ["mom12_1", "bp_act"]。[] も可）
    start=200701, end=202607,# int | None yyyymm の範囲（両端含む）
    lag=1,                   # int        評価リターンの先行月数（1 = 翌月）
    universe_sizes=(1,2,3),  # ユニバース定義（IMI）
    benchmark_sizes=(1,2),   # in_bench フラグの定義（MSCI India）
    return_col="rtn",        # "rtn"（トータル）| "srtn"（BARRA固有リターン）
)
```

**入力**: `data/` ディレクトリ（`universe/`, `factor/core/`, `barra/rtn/`）。引数のみでファイル指定は不要。

**出力**: MultiIndex (yyyymm, bid) の DataFrame。列 =
`gid, name, gics, size, cap, price, <factors...>, fwd_rtn, in_bench, sector, log_cap`

- `fwd_rtn` は**小数**（3% → 0.03）。`barra/rtn` の % 表記を /100 済み
- ファクター値の欠損センチネルは NaN 化済み

## add_forward_returns — IC 減衰用の複数ホライズン

```python
panel = fl.data.add_forward_returns(panel, lags=range(1, 13))
# -> fwd_rtn_1 ... fwd_rtn_12 列を追加（h ヶ月先の「単月」リターン）
```

**必要な列**: なし（index の yyyymm/bid から `barra/rtn` を引く）。

## coverage — 月次カバレッジ

```python
cov = fl.data.coverage(panel, ["mom12_1"])
# 列: n_universe, n_bench, n_fwd_rtn, cov_mom12_1（非欠損率）
```

## 単月ローダー（低レベル API）

```python
fl.data.load_universe(202012)              # bid インデックスの DataFrame（センチネル NaN 化済み）
fl.data.load_core(202012, ["bp_act"])      # 同上
fl.data.load_forward_return(202012, lag=1) # Series（小数）
fl.data.available_yyyymm("factor/core")    # 存在する年月のリスト
```
