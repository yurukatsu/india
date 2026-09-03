# fl.cgo — Capital Gain Overhang

## load_cgo_monthly — 事前計算済みスコアの読み込み（推奨）

```python
cgo = fl.cgo.load_cgo_monthly(
    columns=["cgo_12m", "cgo_60m"],  # None なら全16列（1m..13m, 24m, 36m, 60m）
    start=200801, end=202607,
)
# -> MultiIndex (yyyymm, bid) の DataFrame。0.13 秒 / 全列全期間
panel["cgo_60m"] = cgo["cgo_60m"].reindex(panel.index)   # パネルへの結合
```

**入力**: `data/cgo/`（`scripts/build_cgo.py` で生成済み。再生成 ~30秒）。
保存値と再計算値の一致はラウンドトリップ検証済み（diff = 0）。

## monthly_cgo_multi — 任意ホライズンでの再計算

```python
cgo = fl.cgo.monthly_cgo_multi(
    {"cgo_12m": 252, "cgo_5y": 1260},  # {列名: 遡及期間}。単位は frequency に依存
    start=200801, end=202607,
    frequency="daily",                 # "daily"（営業日数、1期ラグ=前営業日）| "weekly"（週数、=前週末）
    min_periods=None,                  # None = 各ホライズンのフル・ルックバック
)
```

**入力**: `data/turnover/`（`scripts/build_turnover.py` で生成）。
計算時間: 日次で 5y カーネル ~4秒、16ホライズン一括 ~25秒。
日次5y と週次5y の順位相関は 0.983。

## 低レベル API

```python
daily = fl.cgo.load_daily()                 # data/turnover の連結
P, V = fl.cgo.daily_panel(daily)            # 日次パネル（P=価格, V=回転率）
P, V = fl.cgo.weekly_panel(daily)           # 週次パネル（P=週末値, V=週内和）
weekly = fl.cgo.compute_cgo(P, V, 1260, 1260)   # numba カーネル（ナイーブ実装と一致検証済み）
s = fl.cgo.monthly_snapshot(weekly)         # 月次化（月末以前に終了した最後の期の値 = PIT）
```

定義・タイミング規約は `src/factorlab/cgo.py` の docstring と `notebooks/08`〜`11` を参照。
