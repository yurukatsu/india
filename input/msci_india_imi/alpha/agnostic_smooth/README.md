# 平滑化スコア（長期保有版）

元スコアを各月 Blom 化して過去 L か月（当月を含む）で平均し、当月の銘柄内で再 Blom 化したもの。`scripts/build_smooth.py` で生成。Bartram–Grinblatt (2018) の 1 年保有戦略（ラグ 0〜L−1 のシグナルによる重なり合うポートフォリオの平均）をスコア側で近似する。

| スコア | 元スコア | 窓 | 期間 |
| --- | --- | --- | --- |
| `afa_ols_is_neu_avg12` | `agnostic_neu/afa_ols_is_neu` | 12 か月（最小 6 か月） | 200806〜202607 |
| `afa_ts_neu_avg12` | `agnostic_neu/afa_ts_neu` | 12 か月（最小 6 か月） | 200806〜202607 |
| `afa_ols_neu_avg12` | `agnostic_neu/afa_ols_neu` | 12 か月（最小 6 か月） | 200806〜202607 |
| `afa_ridge_neu_avg12` | `agnostic_neu/afa_ridge_neu` | 12 か月（最小 6 か月） | 200806〜202607 |
| `afa_lasso_neu_avg12` | `agnostic_neu/afa_lasso_neu` | 12 か月（最小 6 か月） | 200806〜202607 |
| `afa_rf_neu_avg12` | `agnostic_neu/afa_rf_neu` | 12 か月（最小 6 か月） | 200806〜202607 |
| `afa_lgbm_neu_avg12` | `agnostic_neu/afa_lgbm_neu` | 12 か月（最小 6 か月） | 200806〜202607 |
