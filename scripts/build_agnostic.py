"""Bartram–Grinblatt (2018) "Agnostic Fundamental Analysis Works" を模倣したスコアを ``input`` の alpha 形式で生成する。

各月、ユニバースの非金融銘柄について、時価総額を財務項目の **企業全体の水準** に横断面回帰して
「同じ財務の企業群から見た公正価値」``P`` を推定し、乖離率

    M = (P − V) / V      （V は時価総額。大きいほど割安）

を各月 Blom 正規スコアに変換して ``alpha/agnostic/{name}/YYYYMM.dat`` に書き出す。

財務項目は ``core`` の比率から復元する（比率は同じ月末の時価総額 ``mv`` を分母にしていることを確認済み）:

- 総資産 ``TA = TL / tl_ta``、負債 ``TL = mv / mve_tl``（2013〜2020 年に一部銘柄の ``sales_ta`` が壊れているため、
  総資産は負債経由で求める）
- 価格を分母にした比率は ``比率 × mv``（簿価・純利益・売上・キャッシュ利益・営業 CF・配当）。
  FCF（``fcfp``）は 2019 年 4〜8 月に最大 85% の銘柄で欠損するため使わない
- 総資産・負債を分母にした比率は ``比率 × TA``（``× TL``）

前処理は論文に合わせ、各項目を総資産で割った比率を **その月までの全履歴**（ユニバースの非金融銘柄、
``history_start`` 以降）の 5% / 95% 点で winsorize してから総資産を掛け戻す（ルックアヘッドなし）。

モデル（すべて各月の横断面で推定。係数は毎月推定し直す）:

| スコア | モデル | 目的変数 | 特徴量 | 予測 |
| --- | --- | --- | --- | --- |
| ``afa_ols_is`` | OLS | 時価総額（水準） | 財務項目の水準 | 学習に使った銘柄そのもの（論文どおり） |
| ``afa_ols`` | OLS | 時価総額（水準） | 財務項目の水準 | K 分割の交差推定 |
| ``afa_ridge`` | Ridge（標準化、α は LOO） | 時価総額（水準） | 財務項目の水準 | K 分割の交差推定 |
| ``afa_lasso`` | Lasso（標準化、α は内側 CV） | 時価総額（水準） | 財務項目の水準 | K 分割の交差推定 |
| ``afa_rf`` | Random Forest | log 時価総額 | log 総資産 + 総資産比の比率 | K 分割の交差推定 |
| ``afa_lgbm`` | LightGBM | log 時価総額 | log 総資産 + 総資産比の比率 | K 分割の交差推定 |

交差推定は、自分の時価総額を学習に使わずに公正価値を出すため（柔軟なモデルは学習した銘柄の時価総額を
ほぼ再現してしまい、乖離が消える）。木系の特徴量は水準と同じ情報（log 総資産と比率から水準が一意に戻る）。

Examples:
    全スコアを生成::

        uv run python scripts/build_agnostic.py

    線形モデルだけ、期間とプロセス数を指定::

        uv run python scripts/build_agnostic.py --models ols_is ols ridge lasso --start 201001 --end 202606 --n-jobs 8
"""

from __future__ import annotations

import argparse
import logging
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from lightgbm import LGBMRegressor
from scipy.stats import norm, spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LassoCV, LinearRegression, RidgeCV
from sklearn.model_selection import KFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from alphaeval.dates import to_int
from alphaeval.io import InputStore, write_score_file

logger = logging.getLogger("build_agnostic")

DEFAULT_INPUT_ROOT = Path("input") / "msci_india_imi"
GROUP = "agnostic"
FINANCIALS_SECTOR = 40


@dataclass(frozen=True)
class ItemSpec:
    """復元する財務項目 1 つの定義。

    Attributes:
        name (str): 項目名（特徴量名）。
        label (str): 日本語名。
        source (str): ``core`` の比率名。
        base (str): 比率に掛ける水準（``mv`` / ``ta`` / ``tl``）。
    """

    name: str
    label: str
    source: str
    base: str


#: 総資産・負債以外の財務項目。``value = core[source] × base``。
ITEM_SPECS: tuple[ItemSpec, ...] = (
    ItemSpec("be", "簿価（株主資本）", "bp_act", "mv"),
    ItemSpec("ni", "純利益", "ep_act", "mv"),
    ItemSpec("sales", "売上高", "sp_act", "mv"),
    ItemSpec("cfe", "キャッシュ利益", "cfp_act", "mv"),
    ItemSpec("ocf", "営業 CF", "ocfp", "mv"),
    ItemSpec("div", "配当", "dp_act", "mv"),
    ItemSpec("td", "有利子負債", "td_ta", "ta"),
    ItemSpec("re", "利益剰余金", "re_ta", "ta"),
    ItemSpec("wk", "運転資本", "wk_ta", "ta"),
    ItemSpec("ebit", "EBIT", "ebit_ta", "ta"),
    ItemSpec("gp", "売上総利益", "gpa_act", "ta"),
    ItemSpec("sga", "販管費", "sga_ta", "ta"),
    ItemSpec("pti", "税引前利益", "pti_tl", "tl"),
)

#: 特徴量の並び（総資産と負債を先頭に置く）。
FEATURES: tuple[str, ...] = ("ta", "tl") + tuple(s.name for s in ITEM_SPECS)

#: 読み込む ``core`` 列。
CORE_INPUTS: tuple[str, ...] = ("mv", "mve_tl", "tl_ta") + tuple(
    s.source for s in ITEM_SPECS
)

LINEAR_MODELS: tuple[str, ...] = ("ols_is", "ols", "ridge", "lasso")
TREE_MODELS: tuple[str, ...] = ("rf", "lgbm")
ALL_MODELS: tuple[str, ...] = LINEAR_MODELS + TREE_MODELS

MODEL_DESCRIPTIONS: Mapping[str, str] = {
    "ols_is": "OLS（定数項あり）。学習に使った銘柄そのものを予測（論文どおりの in-sample 公正価値）",
    "ols": "OLS（定数項あり）。K 分割の交差推定",
    "ridge": "Ridge。特徴量を学習データで標準化し、α は LOO（RidgeCV、10^-4〜10^4）で選ぶ。K 分割の交差推定",
    "lasso": "Lasso。特徴量を学習データで標準化し、α は内側 5 分割 CV（LassoCV）で選ぶ。K 分割の交差推定",
    "rf": "Random Forest（500 本、葉の最小 3 銘柄、分割候補 1/3）。log 時価総額を log 総資産 + 総資産比の比率で予測。K 分割の交差推定",
    "lgbm": "LightGBM（300 回、学習率 0.05、葉 15、葉の最小 10 銘柄、行・列 80% サンプリング、L2 1.0）。"
    "log 時価総額を log 総資産 + 総資産比の比率で予測。K 分割の交差推定",
}


def blom_score(values: pd.Series) -> pd.Series:
    """Blom 正規スコアに変換する（欠損は NaN のまま）。

    Args:
        values (pd.Series): 銘柄 → 値。

    Returns:
        pd.Series: 同じインデックスの正規スコア。

    Examples:
        >>> blom_score(pd.Series([1.0, 2.0, 3.0])).round(3).tolist()
        [-0.869, 0.0, 0.869]
    """
    ranks = values.rank(method="average")
    n = values.notna().sum()
    u = (ranks - 3.0 / 8.0) / (n + 1.0 / 4.0)
    return pd.Series(norm.ppf(u.to_numpy(dtype=float)), index=values.index)


def build_levels(core: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """``core`` の比率から財務項目の水準（時価総額と同じ通貨単位）を復元する。

    Args:
        core (Mapping[str, pd.DataFrame]): ``CORE_INPUTS`` の各列 → 日付 × 銘柄パネル（同じ形）。

    Returns:
        dict[str, pd.DataFrame]: ``FEATURES`` と ``mv`` の各項目 → 日付 × 銘柄の水準。
        総資産が正でない銘柄はすべて NaN。

    Examples:
        >>> one = lambda v: pd.DataFrame({"A": [v]})
        >>> core = {c: one(0.1) for c in CORE_INPUTS}
        >>> core.update(mv=one(100.0), mve_tl=one(2.0), tl_ta=one(0.5), bp_act=one(0.4))
        >>> lv = build_levels(core)
        >>> float(lv["tl"].iloc[0, 0]), float(lv["ta"].iloc[0, 0]), float(lv["be"].iloc[0, 0])
        (50.0, 100.0, 40.0)
        >>> float(lv["td"].iloc[0, 0])  # td_ta 0.1 × 総資産 100
        10.0
    """
    mv = core["mv"].where(core["mv"] > 0)
    tl = mv / core["mve_tl"].where(core["mve_tl"] > 0)
    ta = (tl / core["tl_ta"].where(core["tl_ta"] > 0)).where(lambda x: x > 0)
    tl = tl.where(ta.notna())
    base = {"mv": mv, "ta": ta, "tl": tl}
    levels = {"mv": mv.where(ta.notna()), "ta": ta, "tl": tl}
    for spec in ITEM_SPECS:
        levels[spec.name] = (core[spec.source] * base[spec.base]).where(ta.notna())
    return levels


def expanding_bounds(ratio: pd.DataFrame, mask: pd.DataFrame, q: float) -> pd.DataFrame:
    """各月について、その月までの全履歴をプールした分位点（下側 ``q``・上側 ``1 − q``）を返す。

    Args:
        ratio (pd.DataFrame): 日付 × 銘柄の値（日付は昇順）。
        mask (pd.DataFrame): 同じ形の bool。``True`` の値だけを履歴に含める。
        q (float): 下側の分位（0 < q < 0.5）。

    Returns:
        pd.DataFrame: 日付 × ``["lo", "hi"]``。履歴が無い月は NaN。

    Examples:
        >>> r = pd.DataFrame({"A": [0.0, 10.0], "B": [1.0, 100.0]}, index=[1, 2])
        >>> b = expanding_bounds(r, r.notna(), 0.0)
        >>> b.loc[1].tolist(), b.loc[2].tolist()  # 2 か月目は 1 か月目の値も含む
        ([0.0, 1.0], [0.0, 100.0])
    """
    values = ratio.where(mask).to_numpy(dtype=float)
    out = np.full((len(ratio), 2), np.nan)
    pooled: list[np.ndarray] = []
    for i, row in enumerate(values):
        finite = row[np.isfinite(row)]
        if finite.size:
            pooled.append(finite)
        if pooled:
            hist = np.concatenate(pooled)
            out[i] = np.quantile(hist, [q, 1.0 - q])
    return pd.DataFrame(out, index=ratio.index, columns=["lo", "hi"])


def winsorize_levels(
    levels: Mapping[str, pd.DataFrame], mask: pd.DataFrame, q: float
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """各項目を総資産で割った比率を履歴の分位点で winsorize し、水準と比率を返す。

    Args:
        levels (Mapping[str, pd.DataFrame]): :func:`build_levels` の結果。
        mask (pd.DataFrame): 履歴に含める銘柄・月（ユニバースの非金融銘柄）。
        q (float): 下側の分位（論文は 0.05）。

    Returns:
        tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
            (winsorize 後の水準 ``FEATURES`` → パネル, winsorize 後の比率 ``FEATURES[1:]`` → パネル)。
            総資産自体は winsorize しない。

    Examples:
        >>> idx = [1, 2]
        >>> ta = pd.DataFrame({"A": [10.0, 10.0], "B": [10.0, 10.0]}, index=idx)
        >>> lv = {f: ta * 0.1 for f in FEATURES}
        >>> lv["ta"] = ta
        >>> lv["be"] = pd.DataFrame({"A": [1.0, 1.0], "B": [9.0, 50.0]}, index=idx)
        >>> w, r = winsorize_levels(lv, ta.notna(), 0.0)
        >>> float(r["be"].loc[2, "B"]), float(w["be"].loc[2, "B"])  # 比率 5.0 は履歴の最大なので残る
        (5.0, 50.0)
    """
    ta = levels["ta"]
    win_levels: dict[str, pd.DataFrame] = {"ta": ta}
    win_ratios: dict[str, pd.DataFrame] = {}
    for name in FEATURES[1:]:
        ratio = levels[name] / ta
        bounds = expanding_bounds(ratio, mask & ratio.notna(), q)
        clipped = ratio.clip(lower=bounds["lo"], upper=bounds["hi"], axis=0)
        win_ratios[name] = clipped
        win_levels[name] = clipped * ta
    return win_levels, win_ratios


def make_estimator(model: str, seed: int) -> object:
    """モデル名から scikit-learn 互換の推定器を作る。

    Args:
        model (str): ``ALL_MODELS`` のいずれか（``ols_is`` は ``ols`` と同じ推定器）。
        seed (int): 乱数シード。

    Returns:
        object: ``fit`` / ``predict`` を持つ推定器。

    Examples:
        >>> type(make_estimator("ols", 0)).__name__
        'LinearRegression'
    """
    if model in ("ols", "ols_is"):
        return LinearRegression()
    if model == "ridge":
        return make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-4, 4, 33)))
    if model == "lasso":
        return make_pipeline(
            StandardScaler(),
            LassoCV(n_alphas=50, cv=5, max_iter=20_000, random_state=seed),
        )
    if model == "rf":
        return RandomForestRegressor(
            n_estimators=500,
            min_samples_leaf=3,
            max_features=1.0 / 3.0,
            random_state=seed,
            n_jobs=1,
        )
    if model == "lgbm":
        return LGBMRegressor(
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=15,
            min_child_samples=10,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            random_state=seed,
            n_jobs=1,
            deterministic=True,
            force_row_wise=True,
            verbose=-1,
        )
    raise ValueError(f"未知のモデル: {model}")


def fair_value(
    model: str,
    x_level: np.ndarray,
    x_tree: np.ndarray,
    mv: np.ndarray,
    folds: int,
    seed: int,
) -> np.ndarray:
    """1 か月分の横断面で公正価値 ``P`` を推定する。

    Args:
        model (str): ``ALL_MODELS`` のいずれか。
        x_level (np.ndarray): N × K の財務項目の水準（線形モデル用）。
        x_tree (np.ndarray): N × K の木系用特徴量（log 総資産 + 総資産比の比率）。
        mv (np.ndarray): 長さ N の時価総額。
        folds (int): 交差推定の分割数（``ols_is`` では使わない）。
        seed (int): 分割と推定器の乱数シード。

    Returns:
        np.ndarray: 長さ N の公正価値（時価総額と同じ単位。線形モデルは負もありうる）。

    Examples:
        >>> rng = np.random.default_rng(0)
        >>> x = rng.uniform(1, 10, size=(200, 2))
        >>> v = 3 * x[:, 0] + 2 * x[:, 1]
        >>> p = fair_value("ols", x, x, v, folds=5, seed=0)
        >>> bool(np.allclose(p, v))
        True
    """
    is_tree = model in TREE_MODELS
    x = x_tree if is_tree else x_level
    y = np.log(mv) if is_tree else mv
    if model == "ols_is":
        pred = make_estimator(model, seed).fit(x, y).predict(x)
    else:
        pred = np.empty(len(y))
        splitter = KFold(n_splits=folds, shuffle=True, random_state=seed)
        for train, test in splitter.split(x):
            est = make_estimator(model, seed)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConvergenceWarning)
                est.fit(x[train], y[train])
            pred[test] = est.predict(x[test])
    return np.exp(pred) if is_tree else pred


def month_sample(
    date: pd.Timestamp,
    levels: Mapping[str, pd.DataFrame],
    ratios: Mapping[str, pd.DataFrame],
    eligible: pd.DataFrame,
) -> tuple[pd.Index, np.ndarray, np.ndarray, np.ndarray]:
    """その月の推定サンプル（全特徴量がそろう対象銘柄）を取り出す。

    Args:
        date (pd.Timestamp): 月末日付。
        levels (Mapping[str, pd.DataFrame]): winsorize 後の水準（``FEATURES`` と ``mv``）。
        ratios (Mapping[str, pd.DataFrame]): winsorize 後の比率（``FEATURES[1:]``）。
        eligible (pd.DataFrame): 対象銘柄（ユニバースの非金融銘柄）の bool パネル。

    Returns:
        tuple[pd.Index, np.ndarray, np.ndarray, np.ndarray]:
            (銘柄, 水準の特徴量 N × K, 木系の特徴量 N × K, 時価総額 N)。

    Examples:
        >>> codes, xl, xt, v = month_sample(date, levels, ratios, eligible)  # doctest: +SKIP
    """
    xl = pd.DataFrame({f: levels[f].loc[date] for f in FEATURES})
    xt = pd.DataFrame(
        {"log_ta": np.log(levels["ta"].loc[date])}
        | {f: ratios[f].loc[date] for f in FEATURES[1:]}
    )
    mv = levels["mv"].loc[date]
    ok = eligible.loc[date] & xl.notna().all(axis=1) & mv.gt(0)
    ok &= np.isfinite(xt).all(axis=1)
    codes = ok[ok].index
    return (
        codes,
        xl.loc[codes].to_numpy(dtype=float),
        xt.loc[codes].to_numpy(dtype=float),
        mv.loc[codes].to_numpy(dtype=float),
    )


def fit_month(
    date: pd.Timestamp,
    codes: pd.Index,
    x_level: np.ndarray,
    x_tree: np.ndarray,
    mv: np.ndarray,
    models: Sequence[str],
    folds: int,
    seed: int,
) -> tuple[dict[str, pd.Series], list[dict[str, object]]]:
    """1 か月分の全モデルを推定し、乖離率 ``M`` と当てはまりの指標を返す。

    Args:
        date (pd.Timestamp): 月末日付。
        codes (pd.Index): 銘柄。
        x_level (np.ndarray): 水準の特徴量。
        x_tree (np.ndarray): 木系の特徴量。
        mv (np.ndarray): 時価総額。
        models (Sequence[str]): 推定するモデル。
        folds (int): 交差推定の分割数。
        seed (int): 基準シード（月ごとに ``seed + YYYYMM`` を使う）。

    Returns:
        tuple[dict[str, pd.Series], list[dict[str, object]]]:
            (モデル → 銘柄別の ``M``, 指標の行のリスト)。指標は、銘柄数、水準の R²、
            公正価値と時価総額の順位相関、公正価値が 0 以下の銘柄の割合。

    Examples:
        >>> m, stats = fit_month(date, codes, xl, xt, v, ["ols"], 5, 0)  # doctest: +SKIP
    """
    month_seed = seed + to_int(date)
    out: dict[str, pd.Series] = {}
    stats: list[dict[str, object]] = []
    for model in models:
        p = fair_value(model, x_level, x_tree, mv, folds, month_seed)
        out[model] = pd.Series(p / mv - 1.0, index=codes)
        sst = float(((mv - mv.mean()) ** 2).sum())
        stats.append(
            {
                "yyyymm": to_int(date),
                "model": model,
                "n": len(mv),
                "r2_level": 1.0 - float(((mv - p) ** 2).sum()) / sst,
                "rank_corr": float(spearmanr(p, mv).statistic),
                "nonpos_share": float((p <= 0).mean()),
            }
        )
    return out, stats


def load_inputs(
    store: InputStore, history_start: int, end: int
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """``core`` の比率、ユニバース、GICS を読み、水準と対象銘柄のマスクを作る。

    Args:
        store (InputStore): 入力ストア。
        history_start (int): winsorize の履歴に使う最初の年月。
        end (int): 終了年月。

    Returns:
        tuple[dict[str, pd.DataFrame], pd.DataFrame]:
            (:func:`build_levels` の水準, 対象銘柄＝ユニバースの非金融銘柄の bool パネル)。

    Examples:
        >>> levels, eligible = load_inputs(InputStore("input/msci_india_imi"), 200301, 202607)  # doctest: +SKIP
    """
    univ = store.universe(history_start, end)
    idx, cols = univ.index, univ.columns
    core = {
        c: store.alpha(f"core/{c}", history_start, end).reindex(index=idx, columns=cols)
        for c in CORE_INPUTS
    }
    gics = store.alpha("attributes/gics", history_start, end).reindex(
        index=idx, columns=cols
    )
    sector = gics // 1_000_000
    eligible = univ.notna() & sector.notna() & sector.ne(FINANCIALS_SECTOR)
    return build_levels(core), eligible


def build_scores(
    levels: Mapping[str, pd.DataFrame],
    eligible: pd.DataFrame,
    start: int,
    end: int,
    models: Sequence[str] = ALL_MODELS,
    folds: int = 5,
    seed: int = 0,
    winsor_q: float = 0.05,
    min_obs: int = 100,
    n_jobs: int = 1,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """全月についてモデルを推定し、Blom 化したスコアと当てはまりの指標を返す。

    Args:
        levels (Mapping[str, pd.DataFrame]): :func:`build_levels` の水準（履歴の期間を含む）。
        eligible (pd.DataFrame): 対象銘柄の bool パネル（同じ形）。
        start (int): スコアを作る最初の年月。
        end (int): スコアを作る最後の年月。
        models (Sequence[str]): 推定するモデル。
        folds (int): 交差推定の分割数。
        seed (int): 乱数シード。
        winsor_q (float): winsorize の下側分位。
        min_obs (int): 推定に必要な最小銘柄数（未満の月はスキップ）。
        n_jobs (int): 月を並列に処理するプロセス数。

    Returns:
        tuple[dict[str, pd.DataFrame], pd.DataFrame]:
            (スコア名 ``afa_{model}`` → 日付 × 銘柄の Blom スコア, 月 × モデルの指標表)。

    Examples:
        >>> scores, stats = build_scores(levels, eligible, 200801, 202607)  # doctest: +SKIP
        >>> sorted(scores)  # doctest: +SKIP
        ['afa_lasso', 'afa_lgbm', 'afa_ols', 'afa_ols_is', 'afa_rf', 'afa_ridge']
    """
    win_levels, win_ratios = winsorize_levels(levels, eligible, winsor_q)
    win_levels["mv"] = levels["mv"]
    dates = [d for d in eligible.index if start <= to_int(d) <= end]
    jobs = []
    job_dates: list[pd.Timestamp] = []
    for d in dates:
        codes, xl, xt, mv = month_sample(d, win_levels, win_ratios, eligible)
        if len(codes) < min_obs:
            logger.warning(
                "%d: 銘柄数 %d < %d のためスキップ", to_int(d), len(codes), min_obs
            )
            continue
        jobs.append(delayed(fit_month)(d, codes, xl, xt, mv, models, folds, seed))
        job_dates.append(d)
    results = Parallel(n_jobs=n_jobs)(jobs)
    rows: dict[str, dict[pd.Timestamp, pd.Series]] = {m: {} for m in models}
    stats: list[dict[str, object]] = []
    for (m_by_model, month_stats), date in zip(results, job_dates):
        for model, m in m_by_model.items():
            rows[model][date] = blom_score(m)
        stats.extend(month_stats)
    scores = {
        f"afa_{m}": pd.DataFrame(rows[m])
        .T.sort_index()
        .reindex(columns=eligible.columns)
        for m in models
    }
    return scores, pd.DataFrame(stats)


def write_outputs(
    scores: Mapping[str, pd.DataFrame], stats: pd.DataFrame, dest_root: Path
) -> None:
    """スコアを ``{dest_root}/{name}/YYYYMM.dat`` に、指標を ``fit_stats.csv`` に書き出し、README を作る。

    Args:
        scores (Mapping[str, pd.DataFrame]): スコア名 → 日付 × 銘柄。
        stats (pd.DataFrame): :func:`build_scores` の指標表。
        dest_root (Path): ``alpha/agnostic`` ディレクトリ。

    Returns:
        None

    Examples:
        >>> write_outputs(scores, stats, Path("input/msci_india_imi/alpha/agnostic"))  # doctest: +SKIP
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, panel in scores.items():
        model = name.removeprefix("afa_")
        dates = []
        for date, row in panel.iterrows():
            v = row.dropna()
            if v.empty:
                continue
            write_score_file(dest_root / name / f"{to_int(date)}.dat", name, v)
            dates.append(to_int(date))
        s = stats[stats["model"] == model]
        logger.info(
            "%s: %d ファイル（%s〜%s）、R² 中央値 %.3f、順位相関 中央値 %.3f",
            name,
            len(dates),
            dates[0] if dates else None,
            dates[-1] if dates else None,
            s["r2_level"].median(),
            s["rank_corr"].median(),
        )
        lines.append(
            f"| `{name}` | {MODEL_DESCRIPTIONS[model]} | {s['r2_level'].median():.3f} | "
            f"{s['rank_corr'].median():.3f} | {s['nonpos_share'].mean():.1%} | "
            f"{dates[0] if dates else '-'}〜{dates[-1] if dates else '-'} |"
        )
    stats.to_csv(dest_root / "fit_stats.csv", index=False)
    items = "\n".join(
        f"| `{s.name}` | {s.label} | `{s.source}` × {s.base.upper()} |"
        for s in ITEM_SPECS
    )
    (dest_root / "README.md").write_text(
        "# Agnostic Fundamental Analysis（Bartram–Grinblatt 2018 の模倣）\n\n"
        "`scripts/build_agnostic.py` で `core` から生成。各月、ユニバースの非金融銘柄の時価総額 V を財務項目の水準に"
        "横断面回帰して公正価値 P を推定し、乖離率 M = (P − V) / V を Blom 化したもの。**高いほど割安**。"
        "金融セクター（GICS 40）はスコアなし。\n\n"
        "## スコア\n\n"
        "R² は水準の決定係数、順位相関は P と V の Spearman、P ≤ 0 は公正価値が 0 以下になった銘柄の割合"
        "（いずれも月次の中央値・平均。月別は `fit_stats.csv`）。\n\n"
        "| スコア | モデル | R² 中央値 | 順位相関 中央値 | P ≤ 0 | 期間 |\n"
        "| --- | --- | --- | --- | --- | --- |\n" + "\n".join(lines) + "\n\n"
        "## 財務項目\n\n"
        "総資産 TA = TL / `tl_ta`、負債 TL = `mv` / `mve_tl`。ほかは下表。各項目を総資産で割った比率を、"
        "その月までの全履歴（2003 年以降のユニバースの非金融銘柄）の 5% / 95% 点で winsorize してから TA を掛け戻す。"
        "線形モデルは水準（TA, TL と下表）、木系は log TA と各項目 / TA の比率を特徴量にする。\n\n"
        "| 項目 | 内容 | 復元式 |\n| --- | --- | --- |\n" + items + "\n\n"
        "## 論文との違い\n\n"
        "- 財務データは `core` の `*_act`（年次決算。3 月期決算の発表後、主に 5〜7 月に更新）。論文は四半期の直近 12 か月と"
        "直近四半期末の残高（Compustat PIT）、28 項目。\n"
        "- `sales_ta` は 2013〜2020 年に一部銘柄で壊れている（為替レート分ずれる）ため使わず、売上は `sp_act × mv`。\n"
        "- FCF（`fcfp`）は 2019 年 4〜8 月に最大 85% の銘柄で欠損するため使わない（営業 CF は使う）。\n"
        "- 交差推定（K 分割）は論文に無い。論文と同じ in-sample の推定は `afa_ols_is`。\n"
        "- 木系は論文に無い（Geertsema–Lu 2023 型の拡張）。\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解釈する。

    Args:
        argv (Sequence[str] | None): 引数（``None`` なら ``sys.argv``）。

    Returns:
        argparse.Namespace: 解釈結果。

    Examples:
        >>> parse_args(["--models", "ols", "rf"]).models
        ['ols', 'rf']
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    p.add_argument("--start", type=int, default=200801)
    p.add_argument("--end", type=int, default=202607)
    p.add_argument(
        "--history-start", type=int, default=200301, help="winsorize の履歴の開始年月"
    )
    p.add_argument("--models", nargs="+", choices=ALL_MODELS, default=list(ALL_MODELS))
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--winsor", type=float, default=0.05)
    p.add_argument("--min-obs", type=int, default=100)
    p.add_argument("--n-jobs", type=int, default=1)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """スコアを生成して書き出す。

    Args:
        argv (Sequence[str] | None): 引数。

    Returns:
        None

    Examples:
        >>> main(["--models", "ols_is", "--start", "202001", "--end", "202012"])  # doctest: +SKIP
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    store = InputStore(args.input_root)
    levels, eligible = load_inputs(store, args.history_start, args.end)
    scores, stats = build_scores(
        levels,
        eligible,
        args.start,
        args.end,
        models=args.models,
        folds=args.folds,
        seed=args.seed,
        winsor_q=args.winsor,
        min_obs=args.min_obs,
        n_jobs=args.n_jobs,
    )
    write_outputs(scores, stats, args.input_root / "alpha" / GROUP)


if __name__ == "__main__":
    main()
