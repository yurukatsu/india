"""I/O・IC・指標のテスト。"""

from pathlib import Path

import numpy as np
import pandas as pd

from alphaeval.coverage import coverage
from alphaeval.ic import ic_summary, information_coefficient
from alphaeval.io import (
    InputStore,
    read_score_file,
    read_weight_file,
    write_score_file,
    write_weight_file,
)
from alphaeval.metrics import annualized_return, max_drawdown, performance_summary
from alphaeval.panel import forward_returns, period_returns


def test_dat_roundtrip(tmp_path: Path) -> None:
    w = pd.Series({"INDAAA1": 0.6, "INDAAB1": 0.4})
    p = write_weight_file(tmp_path / "202012.dat", 202012, w, use_gzip=True)
    wf = read_weight_file(p)
    assert wf.date == 202012 and wf.format == "weight"
    assert np.allclose(wf.weights.reindex(w.index), w)
    s = pd.Series({"INDAAA1": 1.5, "INDAAB1": np.nan, "INDAAC2": -0.3})
    p = write_score_file(tmp_path / "202012.csv", "myscore", s)
    out = read_score_file(p)
    assert out.name == "myscore" and out.to_dict() == {"INDAAA1": 1.5, "INDAAC2": -0.3}


def test_score_file_without_header(tmp_path: Path) -> None:
    p = tmp_path / "202012.csv"
    p.write_text("INDAAA1, 1\nINDAAB1, 0.8\n")
    s = read_score_file(p)
    assert s.name == "alpha" and s.tolist() == [1.0, 0.8]


def test_input_store_layout(tmp_path: Path) -> None:
    for d, w in [
        (202011, {"A": 0.5, "B": 0.5}),
        (202012, {"A": 0.3, "B": 0.3, "C": 0.4}),
    ]:
        write_weight_file(tmp_path / "univ" / f"{d}.dat", d, pd.Series(w))
        write_weight_file(tmp_path / "bm" / "x" / f"{d}.dat", d, pd.Series({"A": 1.0}))
        write_score_file(
            tmp_path / "alpha" / "g" / "s" / f"{d}.dat",
            "s",
            pd.Series({"A": 1.0, "C": 2.0}),
        )
        rdir = tmp_path / "risk_models" / "M" / "return"
        rdir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": d, "bid": ["A", "B"], "rtn": [0.01, 0.02]}).to_pickle(
            rdir / f"{d}.pkl"
        )
    store = InputStore(tmp_path)
    univ = store.universe()
    assert univ.shape == (2, 3) and np.isnan(univ.loc["2020-11-30", "C"])
    assert store.list_alphas() == ["g/s"] and store.list_benchmarks() == ["x"]
    assert store.alpha("g/s").loc["2020-12-31", "C"] == 2.0
    rtn = store.returns("M", "rtn")
    assert rtn.index.tolist() == pd.to_datetime(["2020-11-30", "2020-12-31"]).tolist()
    cov = coverage(store.alpha("g/s"), univ)
    assert cov["coverage"].tolist() == [0.5, 2 / 3]


def test_forward_and_period_returns() -> None:
    idx = pd.date_range("2020-01-31", periods=4, freq="M")
    r = pd.DataFrame({"A": [0.1, 0.1, 0.1, np.nan]}, index=idx)
    f = forward_returns(r, 2)["A"]
    assert abs(f.iloc[0] - 0.21) < 1e-12 and np.isnan(f.iloc[1])
    pr = period_returns(r, idx[[0, 2, 3]])["A"]
    assert abs(pr.iloc[0] - 0.21) < 1e-12 and np.isnan(pr.iloc[1])


def test_ic_and_summary() -> None:
    idx = pd.date_range("2020-01-31", periods=3, freq="M")
    s = pd.DataFrame(
        {"A": [1, 2, 3], "B": [2, 3, 1], "C": [3, 1, 2]}, index=idx, dtype=float
    )
    f = s.copy()
    ic = information_coefficient(s, f, min_obs=3)
    assert np.allclose(ic, 1.0)
    summ = ic_summary(ic)
    assert summ["hit_rate"] == 1.0 and summ["n"] == 3


def test_metrics() -> None:
    r = pd.Series([0.01] * 12)
    assert abs(annualized_return(r) - (1.01**12 - 1)) < 1e-12
    assert max_drawdown(pd.Series([0.1, -0.5, 0.2])) == -0.5
    b = pd.Series([0.01] * 12)
    s = performance_summary(r, b, turnover=pd.Series([0.1] * 12))
    assert s["tracking_error"] == 0.0 and abs(s["turnover_annual"] - 1.2) < 1e-12
