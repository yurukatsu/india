"""業種間・業種内分解モジュールのテスト。"""

import numpy as np
import pandas as pd
import pytest

from alphaeval.industry import (
    blend_across_within,
    industry_decompose,
    industry_labels,
    industry_variance_share,
    quantile_excess,
)

IDX = pd.to_datetime(["2020-01-31", "2020-02-29"])


def test_labels_fall_back_to_sector_for_small_groups() -> None:
    g = pd.DataFrame(
        [
            [45102010, 45103010, 45202030, 40101010],
            [45102010, 45202030, 45202030, np.nan],
        ],
        columns=list("ABCD"),
        index=IDX,
    )
    lab = industry_labels(g, "industry_group", min_size=2)
    assert lab.iloc[0].tolist() == [4510.0, 4510.0, -45.0, -40.0]
    # 2 月は 4520 が 2 銘柄になり、4510 が 1 銘柄になる。欠損は欠損のまま
    assert lab.iloc[1, :3].tolist() == [-45.0, 4520.0, 4520.0]
    assert np.isnan(lab.iloc[1, 3])


def test_labels_respect_universe() -> None:
    g = pd.DataFrame(
        [[45102010, 45103010, 45104010]], columns=list("ABC"), index=IDX[:1]
    )
    u = pd.DataFrame([[0.5, 0.5, np.nan]], columns=list("ABC"), index=IDX[:1])
    lab = industry_labels(g, "industry_group", universe=u, min_size=2)
    assert lab.iloc[0, :2].tolist() == [4510.0, 4510.0]
    assert np.isnan(lab.iloc[0, 2])


def test_decompose_reconstructs_value() -> None:
    rng = np.random.default_rng(0)
    v = pd.DataFrame(rng.normal(size=(2, 12)), index=IDX)
    g = pd.DataFrame(np.tile(np.repeat([1, 2, 3], 4), (2, 1)), index=IDX)
    a, w = industry_decompose(v, g)
    # 業種内成分は各業種で中央値 0
    long = pd.DataFrame({"w": w.stack(), "g": g.stack()})
    assert (
        long.groupby([long.index.get_level_values(0), "g"])["w"].median().abs().max()
        < 1e-12
    )
    # 業種間成分は業種内で一定
    assert (a.T.groupby(g.iloc[0].to_numpy()).nunique() == 1).all().all()


def test_decompose_zero_mad_uses_std_then_zero() -> None:
    v = pd.DataFrame(
        [[0.0, 0.0, 0.0, 1.0, 5.0, 5.0, 5.0]], columns=list("ABCDEFG"), index=IDX[:1]
    )
    g = pd.DataFrame([[1, 1, 1, 1, 2, 2, 2]], columns=list("ABCDEFG"), index=IDX[:1])
    _, w = industry_decompose(v, g)
    # 業種 1 は MAD = 0 なので標準偏差（0.5）で割る。業種 2 は標準偏差も 0 なので 0
    assert w.iloc[0, 3] == pytest.approx(2.0)
    assert w.iloc[0, 4:].tolist() == [0.0, 0.0, 0.0]


def test_blend_endpoints_and_validation() -> None:
    a = pd.DataFrame([[1.0, 1.0, 2.0, 3.0]], columns=list("ABCD"), index=IDX[:1])
    w = pd.DataFrame([[0.5, -0.5, 0.0, 1.0]], columns=list("ABCD"), index=IDX[:1])
    assert (
        blend_across_within(a, w, 1.0).iloc[0].rank().tolist()
        == a.iloc[0].rank().tolist()
    )
    assert (
        blend_across_within(a, w, 0.0).iloc[0].rank().tolist()
        == w.iloc[0].rank().tolist()
    )
    with pytest.raises(ValueError):
        blend_across_within(a, w, 1.5)


def test_variance_share_bounds() -> None:
    v = pd.DataFrame(
        [[1.0, 2.0, 3.0, 4.0], [1.0, 3.0, 2.0, 4.0]], columns=list("ABCD"), index=IDX
    )
    g = pd.DataFrame([[1, 1, 2, 2], [1, 2, 1, 2]], columns=list("ABCD"), index=IDX)
    r2 = industry_variance_share(v, g)
    assert r2.iloc[0] == pytest.approx(0.8)
    assert r2.iloc[1] == pytest.approx(0.8)


def test_quantile_excess_sums_to_zero_with_equal_sizes() -> None:
    s = pd.DataFrame([np.arange(10.0)], index=IDX[:1])
    f = pd.DataFrame([np.linspace(-0.1, 0.1, 10)], index=IDX[:1])
    q = quantile_excess(s, f)
    assert q[[f"Q{i}" for i in range(1, 6)]].sum(axis=1).iloc[0] == pytest.approx(0.0)
    assert q["spread"].iloc[0] == pytest.approx(q["Q5"].iloc[0] - q["Q1"].iloc[0])
