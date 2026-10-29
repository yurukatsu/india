"""``scripts/build_smooth.py`` のテスト。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd


def _load_module() -> ModuleType:
    """スクリプトをモジュールとして読み込む。

    Returns:
        ModuleType: ``build_smooth`` モジュール。
    """
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_smooth.py"
    spec = importlib.util.spec_from_file_location("build_smooth", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_smooth"] = module
    spec.loader.exec_module(module)
    return module


bs = _load_module()


def test_window_one_preserves_order() -> None:
    rng = np.random.default_rng(0)
    s = pd.DataFrame(rng.normal(size=(5, 20)))
    out = bs.smooth_score(s, window=1, min_periods=1)
    pd.testing.assert_frame_equal(out.rank(axis=1), s.rank(axis=1))


def test_requires_current_score_and_min_periods() -> None:
    s = pd.DataFrame(
        {
            "A": [1.0, 2.0, np.nan],
            "B": [2.0, 1.0, 1.0],
            "C": [3.0, 3.0, 2.0],
            "D": [np.nan, np.nan, 3.0],
        }
    )
    out = bs.smooth_score(s, window=3, min_periods=2)
    assert out.iloc[0].isna().all()
    assert np.isnan(out.loc[2, "A"])  # 当月のスコアが無い
    assert np.isnan(out.loc[2, "D"])  # 1 か月しか無い
    assert out.loc[2, ["B", "C"]].notna().all()


def test_smoothing_reduces_rank_changes() -> None:
    rng = np.random.default_rng(1)
    persistent = rng.normal(size=(1, 200)).repeat(36, axis=0)
    s = pd.DataFrame(persistent + rng.normal(scale=2.0, size=(36, 200)))
    raw_change = s.rank(axis=1).diff().abs().iloc[12:].mean().mean()
    smooth = bs.smooth_score(s, window=12, min_periods=12)
    smooth_change = smooth.rank(axis=1).diff().abs().iloc[13:].mean().mean()
    assert smooth_change < raw_change / 3
