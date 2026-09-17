"""RepRisk 検証レポート用の図と数値を生成する。

実行: ``uv run python experiments/reprisk/docs/make_figures.py``
出力: ``experiments/reprisk/docs/fig/*.png`` と ``experiments/reprisk/docs/fig/numbers.json``
"""

from __future__ import annotations

import json
from pathlib import Path

import japanize_matplotlib  # noqa: F401
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from alphaeval import (
    InputStore,
    coverage,
    forward_returns,
    ic_summary,
    information_coefficient,
    portfolio_returns,
    quantile_analysis,
    turnover,
    weight_portfolio,
)

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "fig"
OUT.mkdir(exist_ok=True)

INPUT_ROOT = ROOT / "input" / "msci_india_imi"
SCORE = "reprisk/repr_current_rri"
START, END = 200801, 202606
TAIL = 24.5
SPLIT = pd.Timestamp("2018-01-01")
PERIODS = {
    "全期間": (None, None),
    "前期 2008–2017": (None, "2017-12-31"),
    "後期 2018–2026": ("2018-01-01", None),
}
SCHEMES = {
    "等ウェイト": ("equal", None),
    "Root Cap": ("sqrt_size", 0.10),
    "Cap Weight": ("size", 0.10),
}
SECTOR_NAMES = {
    10: "エネルギー",
    15: "素材",
    20: "資本財",
    25: "一般消費財",
    30: "生活必需品",
    35: "ヘルスケア",
    40: "金融",
    45: "情報技術",
    50: "通信",
    55: "公益",
    60: "不動産",
}

plt.rcParams.update(
    {
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "font.size": 10,
    }
)
numbers: dict[str, object] = {}


def save(fig: plt.Figure, name: str) -> None:
    fig.tight_layout()
    fig.savefig(OUT / name)
    plt.close(fig)
    print("saved", name)


def period_slice(s: pd.Series, per: tuple) -> pd.Series:
    lo, hi = per
    return s.loc[lo:hi]


def stats(z: pd.Series) -> dict[str, float]:
    z = z.dropna()
    years = len(z) / 12
    ann, vol = z.mean() * 12, z.std() * np.sqrt(12)
    return {
        "ann": ann,
        "vol": vol,
        "rr": ann / vol if vol > 0 else np.nan,
        "hit": float((z > 0).mean()),
        "n": len(z),
        "se_ann": vol / np.sqrt(years) if years > 0 else np.nan,
    }


# ---------------------------------------------------------------------------
# データ
# ---------------------------------------------------------------------------
store = InputStore(INPUT_ROOT)
univ = store.universe(START, END)
bm = store.benchmark("msci_india", START, END)
score = store.alpha(SCORE, START, END)
gics = store.alpha("attributes/gics", START, END).reindex(columns=univ.columns)
returns = {
    "rtn": store.returns("GEMLT", "rtn", START, 202607),
    "srtn": store.returns("GEMLT", "srtn", START, 202607),
}
rtn = returns["rtn"]

score_u = score.where(univ.notna()).reindex(columns=univ.columns)
log_size = np.log(univ.where(univ > 0))
size_bin = np.ceil(log_size.rank(axis=1, pct=True) * 5).clip(1, 5)
in_bm = bm.reindex(index=univ.index, columns=univ.columns).notna()
seg = size_bin.isin([3, 4, 5])
s_seg = score_u.where(seg)
scored = s_seg.notna()
sector = (gics // 1_000_000).where(univ.notna())
numbers["n_months"] = len(univ)

# ---------------------------------------------------------------------------
# fig01 カバレッジ
# ---------------------------------------------------------------------------
cov_all = coverage(score_u, univ)
cov_in = coverage(score_u, univ.where(in_bm))
cov_out = coverage(score_u, univ.where(~in_bm))
fig, axes = plt.subplots(1, 2, figsize=(12, 4))
axes[0].plot(cov_all.index, cov_all["coverage"], label="全体（銘柄数）")
axes[0].plot(
    cov_all.index, cov_all["weight_coverage"], label="全体（時価総額ウェイト）"
)
axes[0].plot(cov_in.index, cov_in["coverage"], label="ベンチマーク内（大型・中型）")
axes[0].plot(cov_out.index, cov_out["coverage"], label="ベンチマーク外（小型）")
axes[0].set_ylim(0, 1.02)
axes[0].set_title("RRI のカバレッジ（MSCI India IMI）")
axes[0].legend(fontsize=8, loc="lower left")
axes[1].plot(cov_all.index, cov_all["n_universe"], label="ユニバース銘柄数")
axes[1].plot(cov_all.index, cov_all["n_covered"], label="RRI あり")
axes[1].plot(cov_all.index, (s_seg >= TAIL).sum(axis=1), label="RRI 25 以上（S3–S5）")
axes[1].set_title("銘柄数")
axes[1].legend(fontsize=8)
save(fig, "fig01_coverage.png")
numbers["coverage"] = {
    "all_mean": cov_all["coverage"].mean(),
    "weight_mean": cov_all["weight_coverage"].mean(),
    "in_bm_mean": cov_in["coverage"].mean(),
    "out_bm_mean": cov_out["coverage"].mean(),
    "out_bm_min": cov_out["coverage"].min(),
}

# ---------------------------------------------------------------------------
# fig02 分布とサイズ
# ---------------------------------------------------------------------------
vals = score_u.stack()
zero_share = (score_u == 0).sum(axis=1) / score_u.notna().sum(axis=1)
median = score_u.median(axis=1)
bands = [
    (-0.5, 0.5, "0"),
    (0.5, 24.5, "1–24"),
    (24.5, 49.5, "25–49"),
    (49.5, 100.5, "50+"),
]
w_norm = univ.div(univ.sum(axis=1), axis=0)
n_univ = univ.notna().sum(axis=1)
rel_size = {}
counts = {}
for lo, hi, lab in bands:
    m = (score_u >= lo) & (score_u < hi)
    cnt = m.sum(axis=1)
    rel_size[lab] = ((w_norm.where(m).sum(axis=1) / cnt.where(cnt > 0)) * n_univ).mean()
    counts[lab] = cnt.mean()
fig, axes = plt.subplots(1, 3, figsize=(14, 3.8))
axes[0].hist(vals, bins=range(0, 72, 2))
axes[0].set_title(f"RRI の分布（ゼロ {float((vals == 0).mean()):.0%}）")
axes[0].set_xlabel("RRI")
axes[1].plot(zero_share.index, zero_share, label="RRI = 0 の割合")
axes[1].plot(median.index, median / 100, label="中央値 ÷ 100")
axes[1].set_ylim(0, 1)
axes[1].set_title("ゼロ比率と中央値の推移")
axes[1].legend(fontsize=8)
axes[2].bar(list(rel_size), list(rel_size.values()))
axes[2].axhline(1, color="k", linewidth=0.8)
axes[2].set_title("RRI 帯別の相対サイズ（1 = ユニバース平均）")
for i, (lab, c) in enumerate(counts.items()):
    axes[2].text(i, rel_size[lab] + 0.05, f"n={c:.0f}", ha="center", fontsize=8)
save(fig, "fig02_distribution.png")
corr_size = pd.Series(
    {
        d: s_seg.loc[d].rank().corr(log_size.loc[d].where(seg.loc[d]).rank())
        for d in s_seg.index
    }
).mean()
numbers["distribution"] = {
    "zero_share": float((vals == 0).mean()),
    "max": float(vals.max()),
    "rel_size": rel_size,
    "counts": counts,
    "zero_share_2008": float(zero_share.loc["2008"].mean()),
    "zero_share_2026": float(zero_share.loc["2026"].mean()),
    "spearman_size_s3s5": float(corr_size),
}


# ---------------------------------------------------------------------------
# 3 群ポートフォリオ（S3–S5）
# ---------------------------------------------------------------------------
def build(member: pd.DataFrame, scheme_key: str) -> pd.DataFrame:
    scheme, cap = SCHEMES[scheme_key]
    return weight_portfolio(member.fillna(False), scheme, size=univ, max_weight=cap)


members3 = {
    "ゼロ": (s_seg >= -0.5) & (s_seg < 0.5),
    "1–24": (s_seg >= 0.5) & (s_seg < TAIL),
    "25以上": s_seg >= TAIL,
}
group_ret = {}
ref_ret = {}
for key in SCHEMES:
    for col, r in returns.items():
        ref_ret[(key, col)] = portfolio_returns(build(scored, key), r)
        for g, m in members3.items():
            group_ret[(key, col, g)] = portfolio_returns(build(m, key), r)
spread25 = {
    (key, col): ref_ret[(key, col)] - group_ret[(key, col, "25以上")]
    for key in SCHEMES
    for col in returns
}
numbers["spread25"] = {
    f"{key}|{col}|{p}": stats(period_slice(z, per))
    for (key, col), z in spread25.items()
    for p, per in PERIODS.items()
}
numbers["group_excess_ew_rtn"] = {
    g: {
        p: stats(
            period_slice(
                group_ret[("等ウェイト", "rtn", g)] - ref_ret[("等ウェイト", "rtn")],
                per,
            )
        )
        for p, per in PERIODS.items()
    }
    for g in members3
}
numbers["turnover_ew"] = {
    g: float(turnover(build(m, "等ウェイト"), rtn).mean() * 12)
    for g, m in members3.items()
}
numbers["n_tail_mean"] = float((s_seg >= TAIL).sum(axis=1).mean())

# fig03 累積相対リターン（3 群 EW）と 参照−25以上 のスプレッド（方式別）
fig, axes = plt.subplots(1, 2, figsize=(13, 4.2))
ref = ref_ret[("等ウェイト", "rtn")]
for g in members3:
    r = group_ret[("等ウェイト", "rtn", g)]
    rel = (1 + r).cumprod() / (1 + ref.reindex(r.index)).cumprod()
    axes[0].plot(rel.index, rel, label=g)
axes[0].axhline(1, color="k", linewidth=0.8)
axes[0].axvline(SPLIT, color="k", linestyle="--", linewidth=0.8)
axes[0].set_yscale("log")
axes[0].set_title("RRI 帯別（S3–S5、等ウェイト）: 参照に対する累積相対リターン")
axes[0].legend()
for key in SCHEMES:
    z = spread25[(key, "rtn")]
    axes[1].plot(z.index, (1 + z.fillna(0)).cumprod(), label=key)
axes[1].axhline(1, color="k", linewidth=0.8)
axes[1].axvline(SPLIT, color="k", linestyle="--", linewidth=0.8)
axes[1].set_yscale("log")
axes[1].set_title("参照 − 「25 以上」の累積スプレッド（ウェイト方式別）")
axes[1].legend()
save(fig, "fig03_spread_cumulative.png")

# fig04 年別スプレッド
z = spread25[("等ウェイト", "rtn")]
zs = spread25[("等ウェイト", "srtn")]
ys = pd.DataFrame(
    {
        "トータルリターン": z.groupby(z.index.year).apply(lambda x: (1 + x).prod() - 1),
        "固有リターン": zs.groupby(zs.index.year).apply(lambda x: (1 + x).prod() - 1),
    }
)
fig, ax = plt.subplots(figsize=(11, 3.8))
ys.plot.bar(ax=ax, width=0.8)
ax.axhline(0, color="k", linewidth=0.8)
ax.axvline(9.5, color="k", linestyle="--", linewidth=0.8)
ax.set_title("参照 − 「25 以上」の年別スプレッド（S3–S5、等ウェイト）")
ax.set_ylabel("年間スプレッド")
ax.grid(axis="x", visible=False)
save(fig, "fig04_yearly_spread.png")
numbers["yearly_spread"] = {
    str(k): {"rtn": float(v["トータルリターン"]), "srtn": float(v["固有リターン"])}
    for k, v in ys.iterrows()
}
numbers["yearly_positive_pre"] = int((ys.loc[:2017, "トータルリターン"] > 0).sum())
numbers["yearly_positive_post"] = int((ys.loc[2018:, "トータルリターン"] > 0).sum())
numbers["yearly_n_post"] = len(ys.loc[2018:])

# ---------------------------------------------------------------------------
# fig05 テール定義（水準 vs 順位）前期・後期
# ---------------------------------------------------------------------------
pct = s_seg.rank(axis=1, pct=True)
tails = {
    "水準 25以上": s_seg >= TAIL,
    "上位 5%": (pct > 0.95) & (s_seg > 0),
    "上位 10%": (pct > 0.90) & (s_seg > 0),
    "上位 15%": (pct > 0.85) & (s_seg > 0),
}
tail_spread = {
    (k, col): ref_ret[("等ウェイト", col)]
    - portfolio_returns(build(m, "等ウェイト"), returns[col])
    for k, m in tails.items()
    for col in returns
}
numbers["tail_defs"] = {
    f"{k}|{col}|{p}": stats(period_slice(z, per))
    for (k, col), z in tail_spread.items()
    for p, per in PERIODS.items()
}
fig, axes = plt.subplots(1, 2, figsize=(12, 3.8), sharey=True)
for ax, col, title in zip(axes, ["rtn", "srtn"], ["トータルリターン", "固有リターン"]):
    data = pd.DataFrame(
        {
            p: [stats(period_slice(tail_spread[(k, col)], per))["ann"] for k in tails]
            for p, per in list(PERIODS.items())[1:]
        },
        index=list(tails),
    )
    data.plot.bar(ax=ax, width=0.7)
    ax.axhline(0, color="k", linewidth=0.8)
    ax.set_title(f"参照 − テール の年率スプレッド（{title}）")
    ax.grid(axis="x", visible=False)
    ax.tick_params(axis="x", rotation=0)
save(fig, "fig05_tail_definitions.png")


# ---------------------------------------------------------------------------
# fig06 分解: 固有 vs ファクター、業種中立
# ---------------------------------------------------------------------------
def sector_neutral(
    w: pd.DataFrame, member_ref: pd.DataFrame, r: pd.DataFrame
) -> pd.Series:
    total = None
    for k in SECTOR_NAMES:
        mask = (sector == k).reindex(index=w.index, columns=w.columns).fillna(False)
        w_k = w.where(mask).fillna(0.0)
        part = portfolio_returns(w_k, r)
        r_k = portfolio_returns(weight_portfolio(member_ref & (sector == k)), r)
        share = pd.Series(w_k.sum(axis=1).to_numpy()[: len(part)], index=part.index)
        c = part - share * r_k.reindex(part.index).fillna(0.0)
        total = c if total is None else total + c
    return total


w_tail = build(members3["25以上"], "等ウェイト")
sec_neutral = -sector_neutral(w_tail, scored, rtn)
decomp = {}
for p, per in PERIODS.items():
    tot = period_slice(spread25[("等ウェイト", "rtn")], per).dropna()
    spec = period_slice(spread25[("等ウェイト", "srtn")], per).reindex(tot.index)
    secn = period_slice(sec_neutral, per).reindex(tot.index)
    decomp[p] = {
        "total": stats(tot),
        "specific": stats(spec),
        "factor": stats(tot - spec),
        "sector_neutral": stats(secn),
    }
numbers["decomp"] = decomp
fig, ax = plt.subplots(figsize=(8, 3.8))
labels = ["トータル", "業種中立", "固有（srtn）", "ファクター寄与（トータル − 固有）"]
keys = ["total", "sector_neutral", "specific", "factor"]
x = np.arange(len(labels))
for i, (p, per) in enumerate(list(PERIODS.items())[1:]):
    vals_ = [decomp[p][k]["ann"] for k in keys]
    errs = [2 * decomp[p][k]["se_ann"] for k in keys]
    ax.bar(x + (i - 0.5) * 0.36, vals_, width=0.34, yerr=errs, capsize=3, label=p)
ax.axhline(0, color="k", linewidth=0.8)
ax.set_xticks(x, labels, fontsize=9)
ax.set_ylabel("年率スプレッド（参照 − 25 以上）")
ax.set_title("「25 以上」劣後の分解（S3–S5、等ウェイト、誤差棒 = ±2SE）")
ax.legend()
ax.grid(axis="x", visible=False)
save(fig, "fig06_decomposition.png")

# ---------------------------------------------------------------------------
# fig07 イベントスタディ（新規 25 以上）
# ---------------------------------------------------------------------------
prev = score_u.shift(1)
event = ((s_seg >= TAIL) & (prev < TAIL)).fillna(False).astype(bool)
PRE, POST = 6, 12


def event_paths(ev: pd.DataFrame, rel_panel: pd.DataFrame) -> pd.DataFrame:
    idx = rel_panel.index
    arr = rel_panel.to_numpy()
    col_pos = {c: i for i, c in enumerate(rel_panel.columns)}
    rows, keys_ = [], []
    for di, ci in zip(*np.where(ev.to_numpy())):
        t = idx.searchsorted(ev.index[di])
        c = col_pos.get(ev.columns[ci])
        if c is None or t - PRE < 0 or t + POST >= len(idx):
            continue
        rows.append(arr[t - PRE : t + POST + 1, c])
        keys_.append((ev.index[di], ev.columns[ci]))
    return pd.DataFrame(
        rows,
        columns=range(-PRE, POST + 1),
        index=pd.MultiIndex.from_tuples(keys_, names=["date", "code"]),
    )


fig, axes = plt.subplots(1, 2, figsize=(12, 3.8))
ev_stats = {}
for ax, col, title in zip(axes, ["rtn", "srtn"], ["トータルリターン", "固有リターン"]):
    rel_panel = returns[col].sub(
        ref_ret[("等ウェイト", col)].reindex(returns[col].index), axis=0
    )
    pth = event_paths(event, rel_panel)
    dates = pth.index.get_level_values("date")
    for p, per in list(PERIODS.items())[1:]:
        lo, hi = per
        sub = pth[(dates >= (lo or dates.min())) & (dates <= (hi or dates.max()))]
        m = sub.mean()
        se = sub.std() / np.sqrt(len(sub))
        cp = pd.concat([m.loc[-PRE:0].cumsum(), m.loc[1:POST].cumsum()])
        ax.plot(
            cp.index,
            cp.to_numpy(),
            marker="o",
            markersize=3,
            label=f"{p} (n={len(sub)})",
        )
        ev_stats[f"{col}|{p}"] = {
            "n": len(sub),
            "pre": float(m.loc[-PRE:0].sum()),
            "month0": float(m.loc[0]),
            "post3": float(m.loc[1:3].sum()),
            "post12": float(m.loc[1:12].sum()),
            "t_post12": float(m.loc[1:12].sum() / np.sqrt((se.loc[1:12] ** 2).sum())),
        }
    ax.axvline(0, color="k", linewidth=0.8)
    ax.axhline(0, color="k", linewidth=0.8)
    ax.set_title(f"新規に 25 以上となった銘柄の相対リターン（{title}）")
    ax.set_xlabel("イベントからの月数")
    ax.legend(fontsize=8)
save(fig, "fig07_event_study.png")
numbers["event"] = ev_stats
numbers["event_count"] = int(event.sum().sum())
# カレンダータイム 1 か月保有
for col, ret_panel in returns.items():
    r = portfolio_returns(weight_portfolio(event & scored), ret_panel)
    z = r - ref_ret[("等ウェイト", col)].reindex(r.index)
    numbers[f"event_calendar_1m|{col}"] = {
        p: stats(period_slice(z, per)) for p, per in PERIODS.items()
    }

# ---------------------------------------------------------------------------
# fig08 IC
# ---------------------------------------------------------------------------
seg_univ = univ.where(seg)
ic = {}
for col, ret_panel in returns.items():
    fwd1 = forward_returns(ret_panel, 1)
    ic[(col, "Spearman(−RRI)")] = information_coefficient(
        -s_seg, fwd1, seg_univ, "spearman"
    )
    ic[(col, "25以上ダミー")] = information_coefficient(
        -(s_seg >= TAIL).astype(float).where(scored), fwd1, seg_univ, "pearson"
    )
numbers["ic"] = {
    f"{col}|{name}|{p}": ic_summary(period_slice(s, per))[
        ["mean", "std", "icir", "t_stat", "hit_rate", "n"]
    ].to_dict()
    for (col, name), s in ic.items()
    for p, per in PERIODS.items()
}
fig, ax = plt.subplots(figsize=(11, 3.8))
for (col, name), s in ic.items():
    ax.plot(
        s.index,
        s.rolling(36, min_periods=24).mean(),
        label=f"{'トータル' if col == 'rtn' else '固有'}: {name}",
    )
ax.axhline(0, color="k", linewidth=0.8)
ax.axvline(SPLIT, color="k", linestyle="--", linewidth=0.8)
ax.set_title("翌月リターンに対する IC（S3–S5、36 か月移動平均）")
ax.legend(fontsize=8)
save(fig, "fig08_ic_rolling.png")

# ---------------------------------------------------------------------------
# fig09 5 分位の失敗（同順位）
# ---------------------------------------------------------------------------
q5 = quantile_analysis(score_u, rtn, univ, n_quantiles=5, higher_is_better=False)
fig, ax = plt.subplots(figsize=(8, 3.5))
q5.n_holdings.plot(ax=ax)
ax.set_title("通常の 5 分位（RRI 低 = Q1）の分位別銘柄数: 同順位で分位が成立しない")
ax.set_ylabel("銘柄数")
save(fig, "fig09_quintile_failure.png")
numbers["q5_n_mean"] = q5.n_holdings.mean().to_dict()

# ---------------------------------------------------------------------------
# 検出力: 後期 103 か月で前期の効果を検出できたか
# ---------------------------------------------------------------------------
post = stats(period_slice(spread25[("等ウェイト", "rtn")], PERIODS["後期 2018–2026"]))
pre = stats(period_slice(spread25[("等ウェイト", "rtn")], PERIODS["前期 2008–2017"]))
numbers["power"] = {
    "post_ann": post["ann"],
    "post_se": post["se_ann"],
    "post_ci_hi": post["ann"] + 1.96 * post["se_ann"],
    "pre_ann": pre["ann"],
    "z_pre_vs_post": (pre["ann"] - post["ann"])
    / np.sqrt(pre["se_ann"] ** 2 + post["se_ann"] ** 2),
}

with open(OUT / "numbers.json", "w", encoding="utf-8") as f:
    json.dump(
        numbers,
        f,
        ensure_ascii=False,
        indent=1,
        default=float,
    )
print("numbers.json written")


# ---------------------------------------------------------------------------
# fig10 除外スクリーンの回転率・税コスト（09_exclusion_cost）
# ---------------------------------------------------------------------------
from alphaeval import (
    buffered_membership,
    india_tax_schedule,
    performance_summary,
    simulate_after_tax,
)

COST_BUY, COST_SELL = 0.0015, 0.0025
BURN_IN = 12
SCREENS = {
    "ベース": None,
    "25以上除外": (24.5, 24.5),
    "25以上除外\n（バッファ 25/20）": (24.5, 19.5),
    "35以上除外\n（バッファ 35/30）": (34.5, 29.5),
}
schedule = india_tax_schedule("trust")
score_full = score.reindex(columns=univ.columns)
base_bm = bm.reindex(columns=univ.columns)


def screened(base: pd.DataFrame, screen: tuple | None) -> pd.DataFrame:
    if screen is None:
        w = base.copy()
    else:
        ex = buffered_membership(score_full, lower_in=screen[0], lower_out=screen[1])
        w = base.where(
            ~ex.reindex(index=base.index, columns=base.columns).fillna(False)
        )
    return w.div(w.sum(axis=1), axis=0)


excl_rows: dict[str, dict[str, float]] = {}
sims_bm = {}
for name, scr in SCREENS.items():
    w = screened(base_bm, scr)
    sims_bm[name] = simulate_after_tax(
        w, rtn, schedule, cost_buy=COST_BUY, cost_sell=COST_SELL
    )
base_m = sims_bm["ベース"].monthly.iloc[BURN_IN:]
for name, sim in sims_bm.items():
    m = sim.monthly.iloc[BURN_IN:]
    excluded_w = (
        float(
            (
                base_bm.where(screened(base_bm, SCREENS[name]).isna()).sum(axis=1)
                / base_bm.sum(axis=1)
            ).mean()
        )
        if SCREENS[name]
        else 0.0
    )
    row: dict[str, float] = {"excluded_weight": excluded_w}
    for p, per in PERIODS.items():
        lo, hi = per
        mm, bb = m.loc[lo:hi], base_m.loc[lo:hi]
        g = performance_summary(mm["r_gross"], bb["r_gross"], mm["turnover"])
        tc = performance_summary(mm["r_net_tc"])
        net = performance_summary(mm["r_net"], bb["r_net"])
        pos_s, pos_l = (
            mm["gain_short"].clip(lower=0).sum(),
            mm["gain_long"].clip(lower=0).sum(),
        )
        row[f"{p}|turnover"] = g["turnover_annual"]
        row[f"{p}|cost_drag"] = g["ann_return"] - tc["ann_return"]
        row[f"{p}|tax_drag"] = tc["ann_return"] - net["ann_return"]
        row[f"{p}|gross_excess"] = g["active_return"]
        row[f"{p}|net_excess"] = net["active_return"]
        row[f"{p}|te"] = g["tracking_error"]
        row[f"{p}|short_ratio"] = (
            pos_s / (pos_s + pos_l) if pos_s + pos_l > 0 else np.nan
        )
    row["liquidation_tax_ratio"] = float(
        m["liquidation_tax"].iloc[-1] / m["value"].iloc[-1]
    )
    excl_rows[name.replace("\n", "")] = row
numbers["exclusion_bm"] = excl_rows

names = [n for n in SCREENS if n != "ベース"]
fig, axes = plt.subplots(1, 3, figsize=(15, 4))
x = np.arange(len(names))
base_to = excl_rows["ベース"]["全期間|turnover"]
axes[0].bar(
    x, [excl_rows[n.replace("\n", "")]["全期間|turnover"] for n in names], color="C0"
)
axes[0].axhline(
    base_to, color="k", linestyle="--", linewidth=0.8, label=f"ベース {base_to:.0%}"
)
axes[0].set_xticks(x, names, fontsize=9)
axes[0].set_title("年率回転率（片道）")
axes[0].legend()
axes[0].grid(axis="x", visible=False)
cost = [
    excl_rows[n.replace("\n", "")]["全期間|cost_drag"]
    - excl_rows["ベース"]["全期間|cost_drag"]
    for n in names
]
tax = [
    excl_rows[n.replace("\n", "")]["全期間|tax_drag"]
    - excl_rows["ベース"]["全期間|tax_drag"]
    for n in names
]
axes[1].bar(x, cost, label="売買コスト", color="C1")
axes[1].bar(x, tax, bottom=cost, label="税", color="C3")
axes[1].set_xticks(x, names, fontsize=9)
axes[1].set_title("ベースに対する追加ドラッグ（年率）")
axes[1].legend()
axes[1].grid(axis="x", visible=False)
for i, p in enumerate(["前期 2008–2017", "後期 2018–2026"]):
    axes[2].bar(
        x + (i - 0.5) * 0.36,
        [excl_rows[n.replace("\n", "")][f"{p}|net_excess"] for n in names],
        width=0.34,
        label=p,
    )
axes[2].axhline(0, color="k", linewidth=0.8)
axes[2].set_xticks(x, names, fontsize=9)
axes[2].set_title("税控除後の超過リターン（対ベース、年率）")
axes[2].legend()
axes[2].grid(axis="x", visible=False)
fig.suptitle(
    "MSCI India をベースに RRI 除外スクリーンを課した場合のコスト（FIFO ロット台帳、信託扱い税率）",
    fontsize=11,
)
save(fig, "fig10_exclusion_cost.png")

with open(OUT / "numbers.json", "w", encoding="utf-8") as f:
    json.dump(numbers, f, ensure_ascii=False, indent=1, default=float)
print("numbers.json updated")
