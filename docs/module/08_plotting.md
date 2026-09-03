# fl.plotting — プロット関数の入力仕様

図中テキストは英語。`fl.plotting.set_style()` を最初に1回呼ぶ。
保存は `fl.plotting.savefig(fig, "07_significant/name")` — `output/figures/` 配下、
サブフォルダは自動作成。

| 関数 | 入力 | 図 |
|---|---|---|
| `plot_coverage(cov)` | `fl.data.coverage` の出力 | ユニバース銘柄数 + カバレッジ |
| `plot_transform_comparison(panel, factor)` | 生列 + `_zscore` + `_blom` 列があるパネル | 分布 + QQ の 3 変換比較 |
| `plot_quantile_timeseries(res, weighting, q, factor, show_na=False)` | **`quantile_returns` の戻り値 dict** | 分位別累積超過 + Q5−Q1（`show_na=True` で NA バケットを灰色破線） |
| `plot_quantile_bar(summary, counts, ..., include_na=False)` | `quantile_summary` の出力（+ `res["counts"]`） | 分位棒（`include_na=True` で NA の灰色バー） |
| `plot_quantile_panel(res, summaries, ...)` | res dict + `{"ew":…, "cw":…}` | EW / CW 横並び棒 |
| `plot_quantile_dashboard(summary, ..., include_na=False)` | `quantile_summary` の出力 | 超過・TE・IR・DD・ボラ・回転率の6面 |
| `plot_ic(ic, ma_window, factor)` | `information_coefficient` の Series | 月次 IC + 移動平均 + 累積 IC |
| `plot_ic_decay(decay, factor)` | `ic_decay` の summary（index=horizon） | 平均 IC とNW t のホライズン棒 |
| `plot_ic_decay_compare(decays, ...)` | `{variant: decay summary}` | バリアント別減衰の折れ線 |
| `plot_cumulative_ic_compare(ics, ...)` | `{variant: ic Series}` | バリアント別累積 IC |
| `plot_factor_return(fr, factor)` | `factor_return` の出力 | 累積（和+複利）+ 月次 f |
| `plot_factor_return_compare(frs, ...)` | `{variant: fr}` | 累積 f の比較 |
| `plot_factor_return_diagnostics(fr)` | 同上 | 月次 t 値 + R² |
| `plot_variant_quantile_bar(summaries, ...)` | `{variant: quantile_summary}` | 中立化バリアント別グループ棒 |
| `plot_scoreboard_bars(scoreboard, value_col, tstat_col)` | `factor_scoreboard` の出力（`style` 列で色分け） | 縦棒スコアボード（* = NW \|t\|≥2） |
| `plot_factor_matrix(M, groups=...)` | 正方 DataFrame（`cross_sectional_corr` / `quantile_overlap`） | ヒートマップ（スタイル色帯 + 区切り線） |

`plot_factor_matrix` の主な引数:

```python
fl.plotting.plot_factor_matrix(
    corr,                    # 正方 DataFrame（index=columns=表示名）
    groups=groups,           # Series(表示名 -> グループ) | None。色帯と区切り線
    off_vmin=-1, off_vmax=1, # カラースケール（重複率なら 0..1 + off_cmap="Blues"）
    annotate=None,           # None = 20 本以下なら数値注釈
)
```
