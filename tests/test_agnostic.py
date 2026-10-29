"""``scripts/build_agnostic.py`` のテスト。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest


def _load_module() -> ModuleType:
    """スクリプトをモジュールとして読み込む。

    Returns:
        ModuleType: ``build_agnostic`` モジュール。
    """
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_agnostic.py"
    spec = importlib.util.spec_from_file_location("build_agnostic", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_agnostic"] = module
    spec.loader.exec_module(module)
    return module


ba = _load_module()


def _synthetic_core(
    n_firms: int, dates: pd.DatetimeIndex, seed: int = 0
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    """真の評価式 V = 0.6·BE + 8·NI（を誤差つき）で値付けされた仮想市場の ``core`` を作る。

    Args:
        n_firms (int): 銘柄数。
        dates (pd.DatetimeIndex): 月末日付。
        seed (int): 乱数シード。

    Returns:
        tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
            (``CORE_INPUTS`` → パネル, 埋め込んだ割安度 log(公正価値/V), 対象銘柄の bool パネル)。
    """
    rng = np.random.default_rng(seed)
    cols = [f"F{i:03d}" for i in range(n_firms)]
    shape = (len(dates), n_firms)
    ta = np.exp(rng.normal(8.0, 1.0, size=(1, n_firms))).repeat(len(dates), axis=0)
    be = ta * rng.uniform(0.3, 0.6, size=shape)
    ni = ta * rng.uniform(0.02, 0.12, size=shape)
    fair = 0.6 * be + 8.0 * ni
    mis = rng.normal(0.0, 0.3, size=shape)
    mv = fair * np.exp(-mis)
    tl = ta - be
    frame = lambda a: pd.DataFrame(a, index=dates, columns=cols)
    # 評価に効かない項目も「総資産 × 乱数」の水準として作る（価格を分母にする比率は水準 / mv）。
    # 比率そのものを乱数にすると、復元した水準が mv に比例してしまい時価総額が特徴量に漏れる。
    core = {}
    for spec in ba.ITEM_SPECS:
        noise = ta * rng.uniform(0.01, 0.2, size=shape)
        core[spec.source] = frame(noise / mv if spec.base == "mv" else noise / ta)
    core["pti_tl"] = frame(ta * rng.uniform(0.01, 0.2, size=shape) / tl)
    core.update(
        mv=frame(mv),
        mve_tl=frame(mv / tl),
        tl_ta=frame(tl / ta),
        bp_act=frame(be / mv),
        ep_act=frame(ni / mv),
    )
    eligible = frame(np.ones(shape, dtype=bool))
    eligible.iloc[:, -5:] = False
    return core, frame(mis), eligible


def test_build_levels_reconstructs_items() -> None:
    dates = pd.date_range("2020-01-31", periods=2, freq="M")
    core, _, _ = _synthetic_core(30, dates)
    lv = ba.build_levels(core)
    tl = core["mv"] / core["mve_tl"]
    np.testing.assert_allclose(lv["tl"], tl)
    np.testing.assert_allclose(lv["ta"], tl / core["tl_ta"])
    np.testing.assert_allclose(lv["be"], core["bp_act"] * core["mv"])
    np.testing.assert_allclose(lv["td"], core["td_ta"] * lv["ta"])
    np.testing.assert_allclose(lv["pti"], core["pti_tl"] * lv["tl"])
    assert set(ba.FEATURES) | {"mv"} == set(lv)


def test_build_levels_drops_nonpositive_assets() -> None:
    one = lambda v: pd.DataFrame({"A": [v], "B": [v]})
    core = {c: one(0.1) for c in ba.CORE_INPUTS}
    core["tl_ta"] = pd.DataFrame({"A": [0.5], "B": [-0.5]})
    lv = ba.build_levels(core)
    assert lv["ta"]["A"].notna().all()
    assert all(lv[f]["B"].isna().all() for f in lv)


def test_expanding_bounds_has_no_lookahead() -> None:
    base = pd.DataFrame({"A": [1.0, 2.0, 3.0], "B": [2.0, 3.0, 4.0]}, index=[1, 2, 3])
    shocked = base.copy()
    shocked.loc[3, "A"] = 1e6
    b0 = ba.expanding_bounds(base, base.notna(), 0.05)
    b1 = ba.expanding_bounds(shocked, shocked.notna(), 0.05)
    pd.testing.assert_frame_equal(b0.loc[[1, 2]], b1.loc[[1, 2]])
    assert b1.loc[3, "hi"] > b0.loc[3, "hi"]


def test_winsorize_clips_ratio_and_keeps_assets() -> None:
    idx = [1, 2]
    ta = pd.DataFrame(
        {"A": [10.0, 10.0], "B": [10.0, 10.0], "C": [10.0, 10.0]}, index=idx
    )
    lv = {f: ta * 0.1 for f in ba.FEATURES}
    lv["ta"] = ta
    lv["ni"] = pd.DataFrame(
        {"A": [1.0, 1.0], "B": [1.0, 1.0], "C": [1.0, 1000.0]}, index=idx
    )
    win, ratio = ba.winsorize_levels(lv, ta.notna(), 0.2)
    assert ratio["ni"].loc[2, "C"] < 100.0
    np.testing.assert_allclose(win["ni"].loc[2, "C"], ratio["ni"].loc[2, "C"] * 10.0)
    pd.testing.assert_frame_equal(win["ta"], ta)


@pytest.mark.parametrize("model", ["ols_is", "ols", "ridge", "lasso", "rf", "lgbm"])
def test_fair_value_recovers_planted_mispricing(model: str) -> None:
    rng = np.random.default_rng(1)
    n = 400
    ta = np.exp(rng.normal(8.0, 1.0, size=n))
    ratios = rng.uniform(0.02, 0.6, size=(n, 3))
    x_level = np.column_stack([ta, ratios * ta[:, None]])
    x_tree = np.column_stack([np.log(ta), ratios])
    fair = x_level[:, 1:] @ np.array([0.5, 4.0, 1.0])
    mis = rng.normal(0.0, 0.3, size=n)
    mv = fair * np.exp(-mis)
    p = ba.fair_value(model, x_level, x_tree, mv, folds=5, seed=0)
    m = p / mv - 1.0
    corr = pd.Series(m).corr(pd.Series(mis), method="spearman")
    assert corr > 0.5, f"{model}: {corr:.2f}"


def test_build_scores_end_to_end() -> None:
    dates = pd.date_range("2019-01-31", periods=6, freq="M")
    core, mis, eligible = _synthetic_core(150, dates)
    levels = ba.build_levels(core)
    scores, stats = ba.build_scores(
        levels, eligible, 201904, 201906, models=["ols_is", "lgbm"], min_obs=50
    )
    assert sorted(scores) == ["afa_lgbm", "afa_ols_is"]
    for panel in scores.values():
        assert [d.strftime("%Y%m") for d in panel.index] == [
            "201904",
            "201905",
            "201906",
        ]
        assert panel.iloc[:, -5:].isna().all().all()
        assert panel.iloc[:, :-5].notna().all().all()
        assert abs(panel.mean(axis=1)).max() < 1e-9
    last = scores["afa_ols_is"].iloc[-1].dropna()
    assert last.corr(mis.iloc[-1].reindex(last.index), method="spearman") > 0.5
    assert set(stats["model"]) == {"ols_is", "lgbm"}
    assert (stats["n"] == 145).all()


def test_build_scores_skips_small_months() -> None:
    dates = pd.date_range("2019-01-31", periods=3, freq="M")
    core, _, eligible = _synthetic_core(40, dates)
    scores, stats = ba.build_scores(
        ba.build_levels(core), eligible, 201901, 201903, models=["ols"], min_obs=100
    )
    assert scores["afa_ols"].empty
    assert stats.empty
