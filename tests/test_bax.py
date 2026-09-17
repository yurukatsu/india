"""bax リーダー・評価のテスト（合成した最小限の出力ディレクトリを使う）。"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from alphaeval.bax import (
    BaxOutput,
    evaluate_bax,
    is_cash,
    performance_table,
    stock_contribution,
)
from alphaeval.tax import constant_tax_schedule


@pytest.fixture
def bax_dir(tmp_path: Path) -> Path:
    """3 か月分（201101〜201103）の bax 出力を合成する。"""
    d = tmp_path
    (d / "bc.txt").write_text(
        "# gbax\ntarget_omega = 5\ncs_rskidx_default = -2,2\ncs_industry_default = -0.03,0.03\n"
    )
    (d / "st_prfm.dat").write_text(
        "#month bm_rtn fund_rtn fund_ex_rtn tr_cost fixed_fee tr_fee br_cost rot w_cash altbm_rtn altbm2_rtn\n"
        "201101 NA NA NA 0.5 NA 0 NA 50 -0.5 NA NA\n"
        "201102 1.0 2.0 1.0 0.1 0 0 0 10 -0.1 NA NA\n"
        "201103 -2.0 -1.0 1.0 0.1 0 0 0 5 -0.1 NA NA\n"
    )
    (d / "st_objv.dat").write_text(
        "#day opt_obj alpha omega tr_cost h_tr_cost br_cost altrisk alt2risk rc time\n"
        "201101 0.6 1.0 4.5 0.5 0.5 0 0 0 0 1\n201102 1.0 1.1 4.8 0.1 0.8 0 0 0 0 1\n201103 0.9 1.0 4.9 0.1 0.9 0 0 0 0 1\n"
    )
    (d / "st_trad.dat").write_text(
        "#day rot tr_cost\n201101 50 0.5\n201102 10 0.1\n201103 5 0.1\n"
    )
    holdings = {
        201101: "#code wgt_o wgt_b wgt_i alpha cs_wgt_l cs_wgt_u\nUSACURR 0 0 100 0 0 0\nAAA 60 50 0 1.5 0 63\nBBB 40 30 0 1.0 0 33\n",
        201102: "#code wgt_o wgt_b wgt_i alpha cs_wgt_l cs_wgt_u\nAAA 60 50 61 1.4 0 63\nBBB 40 30 39 1.1 0 33\nCCC 0 20 0 -0.5 0 23\n",
        201103: "#code wgt_o wgt_b wgt_i alpha cs_wgt_l cs_wgt_u\nAAA 50 50 59 1.0 0 63\nBBB 50 30 41 1.2 0 33\n",
    }
    for ym, text in holdings.items():
        (d / f"O{ym}.dat").write_text(text)
        (d / f"S{ym}.dat").write_text(
            "#day opt_obj alpha omega tr_cost h_tr_cost br_cost altrisk alt2risk rc time\n"
            + f"{ym} 1 1 4.8 0.1 0.8 0 0 0 0 1\n"
        )
        (d / f"A{ym}.dat").write_text(
            "#attribute opt bm init opt_active opt_long opt_short\n"
            "sum_pos_w 100 100 100 40 100 0\nR01_BETA 0.1 0.2 0.1 -0.1 0.1 0\nR02_BTOP 0.5 0.0 0.4 0.5 0.5 0\n"
            "I01_AEROSPCE 0.6 0.5 0.6 0.03 0.6 0\nC39_IND 1 1 1 0 1 0\nc39_INRC 1 1 1 0 1 0\n"
        )
        (d / f"op_{ym}.dat").write_text(
            f"# format = shares\n# date = {ym}\n# nav = 1000\n#code shares price\nAAA 60 10\nBBB 40 10\n"
        )
    (d / "me_201102.dat").write_text(
        "#code prev_awgt rtn\nAAA 10 5\nBBB 10 3\nCCC -20 2\n"
    )
    (d / "me_201103.dat").write_text(
        "#code prev_awgt rtn\nAAA 10 -2\nBBB 10 -1\nCCC -20 -4\n"
    )
    return d


def test_read_table_and_cash() -> None:
    assert is_cash(pd.Series(["USACURR", "00000", "INDAAA1"])).tolist() == [
        True,
        True,
        False,
    ]


def test_reader_units_and_alignment(bax_dir: Path) -> None:
    out = BaxOutput(bax_dir)
    assert (
        out.rebalance_dates.tolist()
        == pd.to_datetime(["2011-01-31", "2011-02-28", "2011-03-31"]).tolist()
    )
    perf = out.performance()
    assert (
        abs(perf.loc["2011-02-28", "fund_ex_rtn"] - 0.01) < 1e-12
        and abs(perf.loc["2011-02-28", "rot"] - 0.10) < 1e-12
    )
    h = out.holdings(201101)
    assert "USACURR" not in h.index and abs(h.loc["AAA", "wgt_o"] - 0.6) < 1e-12
    assert "USACURR" in out.holdings(201101, include_cash=True).index
    w = out.holdings_panel("wgt_o")
    assert (
        np.isnan(w.loc["2011-01-31", "CCC"]) and abs(w.loc["2011-02-28", "CCC"]) < 1e-12
    )
    awgt, rtn = out.monthly_panels()
    assert (
        abs(awgt.loc["2011-02-28", "CCC"] + 0.2) < 1e-12
        and abs(rtn.loc["2011-03-31", "AAA"] + 0.02) < 1e-12
    )
    e = out.exposures(201102)
    assert (
        e.loc["R01_BETA", "group"] == "risk_index"
        and e.loc["I01_AEROSPCE", "group"] == "industry"
    )
    assert (
        e.loc["C39_IND", "group"] == "country"
        and e.loc["c39_INRC", "group"] == "currency"
    )
    pos, nav = out.positions(201101)
    assert nav == 1000 and abs(pos["weight"].sum() - 1.0) < 1e-12
    assert out.params["target_omega"] == "5"


def test_performance_table_and_contribution(bax_dir: Path) -> None:
    out = BaxOutput(bax_dir)
    t = performance_table(out)
    assert abs(t.loc["2011-02-28", "excess_gross"] - 0.011) < 1e-12
    assert (
        abs(t.loc["2011-02-28", "omega"] - 0.045) < 1e-12
    )  # 201101 の推定 TE が 2 月に対応
    assert t.loc["2011-02-28", "n_hold"] == 2
    c = stock_contribution(out)
    # AAA: 0.1*0.05 + 0.1*(-0.02) = 0.003, CCC: -0.2*0.02 + -0.2*(-0.04) = 0.004
    assert (
        abs(c.loc["AAA", "contribution"] - 0.003) < 1e-12
        and abs(c.loc["CCC", "contribution"] - 0.004) < 1e-12
    )


def test_evaluate_bax_with_tax_and_groups(bax_dir: Path) -> None:
    out = BaxOutput(bax_dir)
    mapping = pd.Series({"AAA": "X", "BBB": "X", "CCC": "Y"})
    res = evaluate_bax(
        out, mapping=mapping, tax_schedule=constant_tax_schedule(0.2, 0.1), burn_in=0
    )
    assert res.summary["n_periods"] == 2
    assert res.exposures["risk_index"].loc["R02_BTOP", "abs_mean"] == 0.5
    assert (
        "at_upper" in res.exposures["industry"].columns
        and res.exposures["industry"].loc["I01_AEROSPCE", "at_upper"] == 1.0
    )
    assert abs(res.group_contributions.loc["Y", "contribution"] - 0.004) < 1e-12
    assert res.tax is not None and "fund_after_tax" in res.tax_table.columns
    assert res.yearly.loc[2011, "n_months"] == 2


def test_params_catalog_and_settings(bax_dir: Path) -> None:
    from alphaeval.bax import PARAM_CATALOG, describe_params, optimization_settings

    assert (
        len(PARAM_CATALOG) >= 105
        and PARAM_CATALOG["cs_rot"].category == "turnover_cost"
    )
    params = {
        "system": "g",
        "target_omega": "5",
        "coef_rskavr": "0",
        "cs_owgt_default": "0,0.1",
        "cs_a_owgt_default": "-0.03,0.03",
        "cs_industry_default": "-0.03,0.03",
        "cs_country_default": "-1,1",
        "cs_rskidx_default": "-2,2",
        "cs_rot": "0.15",
        "est_tr_cost_default": "1,0.5,0",
        "fn_init_fund": "`$I/create_pf_cash -C $N -n$2`",
        "fn_abbr2": "100",
        "numeraire": "USA",
        "f_same_nav": "1",
        "coef_horiz": "12",
        "unknown_param": "x",
    }
    df = describe_params(params).set_index("parameter")
    assert (
        df.loc["cs_rot", "is_default"] is np.False_
        or df.loc["cs_rot", "is_default"] == False
    )
    assert df.loc["coef_horiz", "is_default"] == True
    assert df.loc["unknown_param", "category"] == "unknown"
    s = optimization_settings(params).set_index("item")
    assert s.loc["初期 NAV", "value"].startswith("100,000,000")
    assert s.loc["ターゲット TE（omega）", "interpretation"] == "推定 TE ≤ 5%（年率）"
    assert s.loc["個別銘柄アクティブウェイト", "interpretation"] == "対 BM -3% 〜 3%"
    assert "実質制約なし" in s.loc["アクティブ・国ウェイト", "interpretation"]
    assert s.loc["回転率制約", "interpretation"].startswith(
        "1 回のリバランスで 15% 以下"
    )
    assert s.loc["推定売買コスト", "interpretation"].startswith("線形型、固定 0.5%")
    out = BaxOutput(bax_dir)
    assert out.initial_nav() == 1000
    assert "初期 NAV" in out.settings()["item"].tolist()


def test_factor_attribution_and_risk_decomposition(bax_dir: Path) -> None:
    from alphaeval.bax import (
        active_factor_exposures,
        factor_attribution,
        risk_decomposition,
    )

    out = BaxOutput(bax_dir)
    factor_list = pd.DataFrame(
        {
            "id": [101, 102, 201, 301, 401],
            "symbol": ["BETA", "BTOP", "AEROSPCE", "IND", "INRC"],
            "group": [
                "Risk Indices",
                "Risk Indices",
                "Industries",
                "Countries",
                "Currencies",
            ],
        }
    )
    exp = active_factor_exposures(out, factor_list)
    assert exp.columns.tolist() == [101, 102, 201, 301, 401]
    assert exp.loc["2011-02-28", 102] == 0.5 and exp.loc["2011-02-28", 201] == 0.03
    idx = pd.to_datetime(["2011-01-31", "2011-02-28", "2011-03-31"])
    fr = pd.DataFrame(
        {
            101: [0.0, 0.01, 0.02],
            102: [0.0, 0.02, -0.01],
            201: [0.0, 0.1, 0.0],
            301: 0.0,
            401: 0.0,
        },
        index=idx,
    )
    groups, _per_factor = factor_attribution(out, fr, factor_list)
    # 2011-02: Style = -0.1*0.01 + 0.5*0.02 = 0.009, Industry = 0.03*0.1 = 0.003
    assert abs(groups.loc["2011-02-28", "Style"] - 0.009) < 1e-12
    assert abs(groups.loc["2011-02-28", "Industry"] - 0.003) < 1e-12
    assert abs(groups.loc["2011-02-28", "specific"] - (0.011 - 0.012)) < 1e-12
    cov = pd.DataFrame(
        np.diag([100.0, 400.0, 25.0, 0.0, 0.0]),
        index=factor_list["id"],
        columns=factor_list["id"],
    )
    rd = risk_decomposition(out, lambda d: cov, factor_list)
    # var_factor = (0.1^2*100 + 0.5^2*400 + 0.03^2*25) * 1e-4 = (1 + 100 + 0.0225) * 1e-4
    assert abs(rd.loc["2011-02-28", "var_factor"] - 101.0225e-4) < 1e-12
    assert abs(rd.loc["2011-02-28", "var_total"] - 0.048**2) < 1e-12
    assert rd.loc["2011-02-28", "var_specific"] == 0.0  # omega² < x'Fx なので 0 に丸め
    assert (
        abs(
            rd.loc["2011-02-28", "share_Style"]
            + rd.loc["2011-02-28", "share_Industry"]
            - rd.loc["2011-02-28", "var_factor"] / 0.048**2
        )
        < 1e-12
    )
