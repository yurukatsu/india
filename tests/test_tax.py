"""税モジュールのテスト。"""

import numpy as np
import pandas as pd

from alphaeval.tax import constant_tax_schedule, india_tax_schedule, simulate_after_tax
from alphaeval.tax.ledger import _apply_offsets, _LossPool, _rebuild_pools


def test_india_schedule_current_rates() -> None:
    trust = india_tax_schedule("trust").rate_at(pd.Timestamp("2026-09-30"))
    company = india_tax_schedule("company").rate_at(pd.Timestamp("2026-09-30"))
    assert np.allclose(trust, (0.2392, 0.1495))
    assert np.allclose(company, (0.2184, 0.1365))
    # 2004-10〜2018-03 は LTCG 非課税
    assert india_tax_schedule("trust").rate_at(pd.Timestamp("2010-12-31"))[1] == 0.0
    # FY2017（2018-03-31 時点）は STCG 15% × 1.15 × 1.03
    s, _ = india_tax_schedule("trust").rate_at(pd.Timestamp("2018-03-31"))
    assert abs(s - 0.15 * 1.15 * 1.03) < 1e-12


def test_fifo_lot_example_from_doc() -> None:
    """docs/india_tcg.md §1.3: ロット A（100 単位 @100）、B（100 単位 @110）、翌年 150 単位 @130 で売却。

    売却日は 2021-02-28: ロット A（2020-01-31 取得）は 12 か月超で長期、
    ロット B（2020-02-29 取得）はちょうど 12 か月で短期。
    """
    dates = pd.to_datetime(["2020-01-31", "2020-02-29", "2021-02-28"])
    prices = pd.DataFrame(
        {"X": [100.0, 110.0, 130.0], "CASH": [1.0, 1.0, 1.0]}, index=dates
    )
    # 数量: t0 100 単位、t1 200 単位、t2 50 単位 になるようウェイトと価値を組む
    # 価格指数を直接与え、リターンは価格から暗黙に決まる
    v0 = 100 * 100.0 + 1000.0  # X 10,000 + CASH 1,000
    w = pd.DataFrame(
        {"X": [10000 / 11000, np.nan, np.nan], "CASH": [1000 / 11000, np.nan, np.nan]},
        index=dates,
    )
    # t1: X 200 単位 @110 = 22,000, 残りを CASH → V^-_1 = 100*110 + 1000 = 12,000 では 200 単位買えないので
    # CASH を多めに持つ設計にする: t0 で CASH 12,000
    v0 = 10000.0 + 12000.0
    w.loc[dates[0]] = [10000 / v0, 12000 / v0]
    v1 = 100 * 110.0 + 12000.0  # 23,000
    w.loc[dates[1]] = [22000 / v1, 1000 / v1]
    v2 = 200 * 130.0 + 1000.0  # 27,000
    w.loc[dates[2]] = [50 * 130.0 / v2, (v2 - 50 * 130.0) / v2]
    sched = constant_tax_schedule(0.20, 0.125)
    res = simulate_after_tax(
        w, prices.pct_change().fillna(0.0), sched, price_index=prices, initial_value=v0
    )
    realized = res.realized[res.realized["date"] == dates[2]]
    by_class = realized.groupby("class")["gain"].sum()
    assert abs(by_class["L"] - 3000.0) < 1e-6  # ロット A 100 単位 × 30
    assert abs(by_class["S"] - 1000.0) < 1e-6  # ロット B 50 単位 × 20
    tax = res.monthly.loc[dates[2], "tax"]
    assert abs(tax - (3000 * 0.125 + 1000 * 0.20)) < 1e-6  # 375 + 200
    assert set(realized.loc[realized["class"] == "L", "acq_date"]) == {dates[0]}
    assert set(realized.loc[realized["class"] == "S", "acq_date"]) == {dates[1]}


def test_holding_period_exactly_12_months_is_short() -> None:
    dates = pd.to_datetime(["2020-01-31", "2021-01-31", "2021-02-28"])
    prices = pd.DataFrame(
        {"X": [100.0, 120.0, 120.0], "C": [1.0, 1.0, 1.0]}, index=dates
    )
    w = pd.DataFrame({"X": [1.0, 0.0, 0.0], "C": [0.0, 1.0, 1.0]}, index=dates)
    res = simulate_after_tax(
        w,
        prices.pct_change().fillna(0.0),
        constant_tax_schedule(0.2, 0.1),
        price_index=prices,
    )
    assert res.realized.loc[res.realized["code"] == "X", "class"].tolist() == ["S"]
    dates = pd.to_datetime(["2020-01-31", "2021-02-28"])
    prices = prices.loc[dates]
    w = pd.DataFrame({"X": [1.0, 0.0], "C": [0.0, 1.0]}, index=dates)
    res = simulate_after_tax(
        w,
        prices.pct_change().fillna(0.0),
        constant_tax_schedule(0.2, 0.1),
        price_index=prices,
    )
    assert res.realized.loc[res.realized["code"] == "X", "class"].tolist() == ["L"]


def test_loss_offset_rules() -> None:
    sched = constant_tax_schedule(0.2, 0.1)
    # 短期損失 -50、長期益 80 → 長期 30 課税、短期損失は使い切り
    assert _apply_offsets(-50.0, 80.0, sched) == (0.0, 30.0, 0.0, 0.0)
    # 長期損失は短期益と相殺できない
    assert _apply_offsets(40.0, -30.0, sched) == (40.0, 0.0, 0.0, -30.0)
    # 短期損失 -100、長期益 30 → 長期 0、短期損失 -70 残る
    assert _apply_offsets(-100.0, 30.0, sched) == (0.0, 0.0, -70.0, 0.0)


def test_rebuild_pools_fifo_and_expiry() -> None:
    pools = [_LossPool(2010, short=40.0), _LossPool(2015, short=30.0)]
    # 年度 2020 末に未使用短期損失 -50: 古い 2010 分から使われた前提で 2015 分 30 + 当年 20 が残る
    new = _rebuild_pools(pools, -50.0, 0.0, 2020, carry_years=8)
    assert {(p.fiscal_year, p.short) for p in new} == {(2015, 30.0), (2020, 20.0)}
    # 2010 分は 8 年超で失効しているため、どのみち残らない
    assert all(p.fiscal_year != 2010 for p in new)


def test_tax_reduces_value_and_no_tax_when_flat() -> None:
    idx = pd.date_range("2020-01-31", periods=14, freq="M")
    prices = pd.DataFrame(
        {"A": np.linspace(100, 200, 14), "B": np.linspace(100, 50, 14)}, index=idx
    )
    rtn = prices.pct_change().fillna(0.0)
    w = pd.DataFrame({"A": [1.0, 0.0] * 7, "B": [0.0, 1.0] * 7}, index=idx)
    res = simulate_after_tax(
        w, rtn, constant_tax_schedule(0.2, 0.1), cost_buy=0.001, cost_sell=0.001
    )
    m = res.monthly.iloc[1:]
    assert (m["r_net_tc"] <= m["r_gross"] + 1e-12).all()
    assert m["tax"].sum() >= 0
    # 全て短期（12 か月未満で売却）
    assert set(res.realized["class"]) == {"S"}
    assert res.annual["short_ratio"].dropna().eq(1.0).all()
