"""インド株ファクター検証ライブラリ.

ベンチマーク: MSCI India (size 1, 2) / ユニバース: MSCI India IMI (size 1, 2, 3)
"""

from . import altdata, cgo, composite, config, data, ic, metrics, multi, plotting, preprocess, quantile, regression

__all__ = ["altdata", "cgo", "composite", "config", "data", "ic", "metrics", "multi", "plotting", "preprocess", "quantile", "regression"]
