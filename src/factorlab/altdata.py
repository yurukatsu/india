"""AI スコア・オルタナティブファクターの読み込み.

タイミング規約:
  - alt: 時点 t のスコア = ``factor/alt/{id}/{t}.pkl`` のうち
    ``effective_yyyymmdd`` が t の月末以前のレコード（発効日ベースの PIT）。
    1ファイル = 1スナップショットなので実質そのまま使えるが、フィルタは常に掛ける。
  - ai : 時点 t のスコア = 月 t 内で最後に観測された日次値（月末スナップショット）。
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import data as data_mod
from .config import DATA_DIR

ALT_DIR = DATA_DIR / "factor" / "alt"

# factor_id 帯 -> 表示グループ
ALT_GROUPS = [
    (range(1, 10), "Employee Reviews"),
    (range(11, 17), "ESG Controversy"),
    (range(65, 69), "Revision (GDB)"),
    (range(340, 356), "NAM ESG"),
    (range(400, 403), "TVL"),
]


def _group_of(fid: int) -> str:
    for rng, name in ALT_GROUPS:
        if fid in rng:
            return name
    return "Other"


def list_alt_factors() -> pd.DataFrame:
    """実データが存在する alt ファクターの一覧（定義・期間・ファイル数つき）."""
    meta = pd.read_csv(ALT_DIR / "factor_list.csv", encoding="utf-8-sig")
    ids = sorted(int(p.name) for p in ALT_DIR.iterdir() if p.is_dir() and p.name.isdigit())
    rows = []
    for fid in ids:
        files = sorted((ALT_DIR / str(fid)).glob("*.pkl"))
        m = meta.loc[meta["factor_id"] == fid].iloc[0]
        rows.append({
            "factor_id": fid,
            "name": m["factor_name"].strip(),
            "group": _group_of(fid),
            "frequency": m["frequency"],
            "data_type": m["data_type"],
            "lag_days": m["lag_days"],
            "first": int(files[0].stem),
            "last": int(files[-1].stem),
            "n_files": len(files),
        })
    df = pd.DataFrame(rows).set_index("factor_id")
    # 重複名（例: 342/343）は id を付けて区別する
    dup = df["name"].duplicated(keep=False)
    df.loc[dup, "name"] = df.loc[dup, "name"] + "_" + df.index[dup].astype(str)
    return df


def _month_end(ym: int) -> int:
    return int(pd.Period(str(ym), "M").end_time.strftime("%Y%m%d"))


@lru_cache(maxsize=None)
def load_alt_monthly(factor_id: int) -> pd.Series:
    """1ファクターの月次スコア（MultiIndex: yyyymm × bid）.

    各月ファイルから ``effective_yyyymmdd <= 月末`` のレコードのみ採用し、
    同一銘柄に複数レコードがあれば発効日が最新のものを使う。
    """
    d = ALT_DIR / str(factor_id)
    pieces = []
    for p in sorted(d.glob("*.pkl")):
        ym = int(p.stem)
        df = pd.read_pickle(p)
        df = df[df["effective_yyyymmdd"] <= _month_end(ym)]
        if df.empty:
            continue
        df = df.sort_values("effective_yyyymmdd").drop_duplicates("bid", keep="last")
        s = df.set_index("bid")["value"].astype(float)
        s.index = pd.MultiIndex.from_product([[ym], s.index], names=["yyyymm", "bid"])
        pieces.append(s)
    out = pd.concat(pieces).sort_index()
    out.name = f"alt_{factor_id}"
    return out


@lru_cache(maxsize=None)
def load_ai_monthly() -> pd.Series:
    """AI スコアの月末スナップショット（MultiIndex: yyyymm × bid）.

    各月について、月内で最後に観測された日次値を採用する（銘柄ごと）。
    """
    d = DATA_DIR / "factor" / "ai"
    pieces = []
    for p in sorted(d.glob("*.pkl")):
        ym = int(p.stem)
        df = pd.read_pickle(p)
        df = df.sort_values("yyyymmdd").drop_duplicates("bid", keep="last")
        s = df.set_index("bid")["ai"].astype(float)
        s.index = pd.MultiIndex.from_product([[ym], s.index], names=["yyyymm", "bid"])
        pieces.append(s)
    out = pd.concat(pieces).sort_index()
    out.name = "ai"
    return out


def build_alt_panel(
    factor_ids: list[int] | None = None,
    include_ai: bool = True,
    start: int | None = None,
    end: int | None = None,
    core_factors: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """ユニバースパネルに alt / ai スコアを結合する.

    Returns
    -------
    (panel, inventory):
      panel     : ``data.build_panel`` と同形式 + alt/ai 列（列名は factor_name）
      inventory : ``list_alt_factors()``（+ ai の行）に ``column`` 列を足したもの
    """
    inv = list_alt_factors()
    if factor_ids is not None:
        inv = inv.loc[inv.index.isin(factor_ids)]

    panel = data_mod.build_panel(core_factors or [], start=start, end=end)

    cols = {}
    for fid, row in inv.iterrows():
        s = load_alt_monthly(int(fid))
        panel[row["name"]] = s.reindex(panel.index)
        cols[fid] = row["name"]
    inv = inv.assign(column=pd.Series(cols))

    if include_ai:
        panel["ai"] = load_ai_monthly().reindex(panel.index)
        ai_row = pd.DataFrame([{
            "name": "ai", "group": "AI", "frequency": "D", "data_type": "Z",
            "lag_days": 0, "first": 201608, "last": 202604, "n_files": np.nan, "column": "ai",
        }], index=pd.Index([9999], name="factor_id"))
        inv = pd.concat([inv, ai_row])
    return panel, inv


def score_characteristics(
    panel: pd.DataFrame,
    cols: list[str],
    time_level: str = "yyyymm",
) -> pd.DataFrame:
    """スコアの基本特性: カバレッジ・離散度・持続性.

    - ``coverage_univ`` / ``coverage_bench`` : ユニバース / ベンチマーク銘柄に対する非欠損率
      （そのファクターにデータがある月のみで計算）
    - ``n_months`` : データがある月数
    - ``n_stocks_mean`` : 月あたり平均銘柄数
    - ``n_unique_mean`` : 月次クロスセクションのユニーク値数の平均（離散度。少ないと分位分割が不安定）
    - ``frac_mode`` : 最頻値の占有率の平均（例: コントラバーシーの「0 = 問題なし」の塊）
    - ``rank_autocorr`` : 月次順位の1次自己相関の平均（スコアの持続性 ≒ 低回転で使えるか）
    """
    rows = {}
    for c in cols:
        s = panel[c]
        has = s.groupby(level=time_level).count()
        months = has[has > 0].index
        sub = panel[panel.index.get_level_values(time_level).isin(months)]
        g = sub.groupby(level=time_level)[c]

        wide = sub[c].unstack("bid")
        r = wide.rank(axis=1)
        ac = r.corrwith(r.shift(1), axis=1, method="pearson")

        def _frac_mode(x: pd.Series) -> float:
            v = x.dropna()
            return float(v.value_counts().iloc[0] / len(v)) if len(v) else np.nan

        rows[c] = {
            "n_months": len(months),
            "first": int(months.min()),
            "last": int(months.max()),
            "n_stocks_mean": float(g.count().mean()),
            "coverage_univ": float((g.count() / sub.groupby(level=time_level).size()).mean()),
            "coverage_bench": float(
                sub[sub["in_bench"]].groupby(level=time_level)[c]
                .count().div(sub[sub["in_bench"]].groupby(level=time_level).size()).mean()
            ),
            "n_unique_mean": float(g.nunique().mean()),
            "frac_mode": float(g.apply(_frac_mode).mean()),
            "rank_autocorr": float(ac.mean()),
        }
    return pd.DataFrame(rows).T
