"""Agnostic Fundamental Analysis の線形モデルの係数を月ごとに推定し、説明変数別に集計する。

``scripts/build_agnostic.py`` と同じサンプル（ユニバースの非金融銘柄、各月 100 銘柄以上）・同じ前処理
（``core`` の比率から金額を復元し、総資産比を履歴の 5% / 95% 点で winsorize）で、各月の横断面回帰

    V_i = a + Σ_k b_k X_{i,k} + ε_i      （V は時価総額、X は財務項目の金額）

の係数を推定する。スコアの生成では予測値（公正価値）しか保存していないため、係数はここで推定し直す。

| モデル | 係数 |
| --- | --- |
| ``ols`` | 全銘柄の OLS（論文 OLS の in-sample 推定と同じ。交差推定の OLS も推定器は同じ） |
| ``ts`` | 無作為 100 銘柄の OLS を ``n_draws`` 回推定した係数の中央値。スコアは予測値の中央値を使うので、係数はその要約 |
| ``ridge`` | 特徴量を標準化した Ridge（α は LOO）。全銘柄で推定 |
| ``lasso`` | 特徴量を標準化した Lasso（α は内側 5 分割 CV）。全銘柄で推定。係数が 0 なら「選ばれなかった」 |

出力（``--output-dir``、既定は ``experiments/agnostic/output``）:

- ``coefficients.csv``: 月 × モデル × 説明変数の係数。``coef`` は元の単位（時価総額 ÷ 財務項目の金額 = 倍率）、
  ``std_coef`` は標準化係数 ``b_k · sd(X_k) / sd(V)``（説明変数の 1 標準偏差が時価総額の何標準偏差に当たるか）
- ``coefficient_summary.csv``: モデル × 説明変数の要約（標準化係数の平均と Newey–West t、正の月の割合、
  元の単位の係数の中央値、Lasso で選ばれた月の割合）
- ``collinearity.csv``: 月ごとの共線性（標準化した説明変数の条件数、最大 VIF、``TA`` と ``TL + be`` の相関）

Examples:
    全期間（Theil–Sen は 2,000 回）::

        uv run python scripts/agnostic_coefficients.py --n-jobs 8
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from sklearn.exceptions import ConvergenceWarning

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_agnostic as ba  # noqa: E402

from alphaeval.dates import to_int  # noqa: E402
from alphaeval.io import InputStore  # noqa: E402

logger = logging.getLogger("agnostic_coefficients")

DEFAULT_OUTPUT = Path("experiments") / "agnostic" / "output"
MODELS: tuple[str, ...] = ("ols", "ts", "ridge", "lasso")


def ols_coefficients(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """定数項付き OLS の係数（定数項を除く）を返す。

    Args:
        x (np.ndarray): N × K の説明変数。
        y (np.ndarray): 長さ N の目的変数。

    Returns:
        np.ndarray: 長さ K の係数。

    Examples:
        >>> x = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 1.0]])
        >>> ols_coefficients(x, 1.0 + 2.0 * x[:, 0] + 3.0 * x[:, 1]).round(6).tolist()
        [2.0, 3.0]
    """
    design = np.column_stack([np.ones(len(y)), x])
    return np.linalg.lstsq(design, y, rcond=None)[0][1:]


def theil_sen_coefficients(
    x: np.ndarray, y: np.ndarray, n_sub: int = 100, n_draws: int = 2_000, seed: int = 0
) -> np.ndarray:
    """無作為な部分標本の OLS 係数の中央値を返す（論文の Theil–Sen 型推定の係数版）。

    Args:
        x (np.ndarray): N × K の説明変数。
        y (np.ndarray): 長さ N の目的変数。
        n_sub (int): 1 回の推定に使う銘柄数（N 以上なら N − 1 に切り詰める）。
        n_draws (int): 繰り返し回数。
        seed (int): 乱数シード。

    Returns:
        np.ndarray: 長さ K の係数の中央値。

    Examples:
        >>> rng = np.random.default_rng(0)
        >>> x = rng.uniform(1, 10, size=(300, 2))
        >>> y = 3 * x[:, 0] + 2 * x[:, 1]
        >>> y[0] = 1e4  # 外れ値 1 銘柄
        >>> theil_sen_coefficients(x, y, n_draws=300).round(6).tolist()
        [3.0, 2.0]
    """
    rng = np.random.default_rng(seed)
    n = len(y)
    m = min(n_sub, n - 1)
    design = np.column_stack([np.ones(n), x])
    coefs = np.empty((n_draws, x.shape[1]))
    for b in range(n_draws):
        idx = rng.choice(n, size=m, replace=False)
        coefs[b] = np.linalg.lstsq(design[idx], y[idx], rcond=None)[0][1:]
    return np.median(coefs, axis=0)


def penalized_coefficients(
    model: str, x: np.ndarray, y: np.ndarray, seed: int
) -> np.ndarray:
    """Ridge・Lasso（標準化の pipeline）を全銘柄で推定し、元の単位の係数を返す。

    Args:
        model (str): ``"ridge"`` または ``"lasso"``。
        x (np.ndarray): N × K の説明変数。
        y (np.ndarray): 長さ N の目的変数。
        seed (int): 乱数シード（Lasso の CV 分割）。

    Returns:
        np.ndarray: 長さ K の係数（元の単位。標準化後の係数 ÷ 説明変数の標準偏差）。

    Examples:
        >>> rng = np.random.default_rng(0)
        >>> x = rng.normal(size=(200, 3))
        >>> c = penalized_coefficients("ridge", x, 2.0 * x[:, 0] + rng.normal(scale=0.1, size=200), 0)
        >>> bool(abs(c[0] - 2.0) < 0.1 and abs(c[1]) < 0.1)
        True
    """
    est = ba.make_estimator(model, seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        est.fit(x, y)
    scaler, reg = est[0], est[-1]
    return np.asarray(reg.coef_, dtype=float) / scaler.scale_


def standardized(coef: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """標準化係数 ``b_k · sd(X_k) / sd(y)`` を返す（標準偏差は母標準偏差）。

    Args:
        coef (np.ndarray): 長さ K の係数（元の単位）。
        x (np.ndarray): N × K の説明変数。
        y (np.ndarray): 長さ N の目的変数。

    Returns:
        np.ndarray: 長さ K の標準化係数。

    Examples:
        >>> x = np.array([[0.0], [2.0]])
        >>> standardized(np.array([3.0]), x, np.array([0.0, 6.0])).tolist()
        [1.0]
    """
    return coef * x.std(axis=0) / y.std()


def collinearity(x: np.ndarray) -> dict[str, float]:
    """標準化した説明変数の条件数と最大 VIF を返す。

    Args:
        x (np.ndarray): N × K の説明変数。

    Returns:
        dict[str, float]: ``cond``（条件数）と ``max_vif``。

    Examples:
        >>> rng = np.random.default_rng(0)
        >>> a = rng.normal(size=(500, 2))
        >>> r = collinearity(np.column_stack([a, a[:, 0] + a[:, 1]]))  # 完全な共線性
        >>> r["cond"] > 1e6
        True
    """
    z = (x - x.mean(axis=0)) / x.std(axis=0)
    corr = np.corrcoef(z, rowvar=False)
    cond = float(np.linalg.cond(z))
    try:
        vif = np.diag(np.linalg.inv(corr))
        max_vif = float(np.max(vif))
    except np.linalg.LinAlgError:
        max_vif = float("inf")
    return {"cond": cond, "max_vif": max_vif}


def month_coefficients(
    date: pd.Timestamp, x: np.ndarray, y: np.ndarray, n_draws: int, seed: int
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """1 か月分の全モデルの係数と共線性の指標を返す。

    Args:
        date (pd.Timestamp): 月末日付。
        x (np.ndarray): N × K の財務項目の金額（``ba.FEATURES`` の順）。
        y (np.ndarray): 長さ N の時価総額。
        n_draws (int): Theil–Sen の繰り返し回数。
        seed (int): 基準シード（月ごとに ``seed + YYYYMM``）。

    Returns:
        tuple[list[dict[str, object]], dict[str, object]]:
            (係数の行のリスト, 共線性の行)。

    Examples:
        >>> rows, col = month_coefficients(date, x, y, 2000, 0)  # doctest: +SKIP
    """
    ym = to_int(date)
    s = seed + ym
    coefs = {
        "ols": ols_coefficients(x, y),
        "ts": theil_sen_coefficients(x, y, n_draws=n_draws, seed=s),
        "ridge": penalized_coefficients("ridge", x, y, s),
        "lasso": penalized_coefficients("lasso", x, y, s),
    }
    rows: list[dict[str, object]] = []
    for model, c in coefs.items():
        sc = standardized(c, x, y)
        for feature, b, z in zip(ba.FEATURES, c, sc):
            rows.append(
                {
                    "yyyymm": ym,
                    "model": model,
                    "feature": feature,
                    "coef": float(b),
                    "std_coef": float(z),
                }
            )
    i = {f: k for k, f in enumerate(ba.FEATURES)}
    col = {"yyyymm": ym, "n": len(y), **collinearity(x)}
    col["corr_ta_tl_be"] = float(
        np.corrcoef(x[:, i["ta"]], x[:, i["tl"]] + x[:, i["be"]])[0, 1]
    )
    return rows, col


def newey_west_t(x: pd.Series, lags: int = 6) -> float:
    """平均の Newey–West t 値を返す。

    Args:
        x (pd.Series): 時系列（欠損は除く）。
        lags (int): ラグ数。

    Returns:
        float: t 値（観測が 10 未満なら NaN）。

    Examples:
        >>> round(newey_west_t(pd.Series([1.0, 2.0, 3.0] * 10), lags=0), 2)
        13.42
    """
    v = pd.Series(x).dropna().to_numpy(dtype=float)
    n = len(v)
    if n < 10:
        return float("nan")
    e = v - v.mean()
    s = (e**2).sum() / n
    for lag in range(1, lags + 1):
        s += 2 * (1 - lag / (lags + 1)) * (e[lag:] * e[:-lag]).sum() / n
    return float(v.mean() / np.sqrt(s / n))


def summarize(coefs: pd.DataFrame) -> pd.DataFrame:
    """モデル × 説明変数の要約表を作る。

    Args:
        coefs (pd.DataFrame): ``coefficients.csv`` と同じ形（yyyymm, model, feature, coef, std_coef）。

    Returns:
        pd.DataFrame: 列は ``std_mean``（標準化係数の平均）、``std_t``（Newey–West t、ラグ 6）、
        ``share_pos``（正の月の割合）、``coef_median``（元の単位の中央値）、``share_selected``（係数が 0 でない月の割合）。

    Examples:
        >>> df = pd.DataFrame({"yyyymm": [1, 2], "model": "ols", "feature": "be", "coef": [1.0, 3.0], "std_coef": [0.1, 0.3]})
        >>> summarize(df).loc[("ols", "be"), "coef_median"]
        2.0
    """
    rows = {}
    for (model, feature), g in coefs.groupby(["model", "feature"], sort=False):
        g = g.sort_values("yyyymm")
        rows[(model, feature)] = {
            "std_mean": g["std_coef"].mean(),
            "std_t": newey_west_t(g["std_coef"]),
            "share_pos": float((g["std_coef"] > 0).mean()),
            "coef_median": g["coef"].median(),
            "share_selected": float((g["coef"].abs() > 1e-12).mean()),
        }
    out = pd.DataFrame(rows).T
    out.index.names = ["model", "feature"]
    return out


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解釈する。

    Args:
        argv (Sequence[str] | None): 引数（``None`` なら ``sys.argv``）。

    Returns:
        argparse.Namespace: 解釈結果。

    Examples:
        >>> parse_args([]).n_draws
        2000
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input-root", type=Path, default=ba.DEFAULT_INPUT_ROOT)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--start", type=int, default=200801)
    p.add_argument("--end", type=int, default=202607)
    p.add_argument(
        "--history-start", type=int, default=200301, help="winsorize の履歴の開始年月"
    )
    p.add_argument(
        "--n-draws", type=int, default=2_000, help="Theil–Sen の繰り返し回数"
    )
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--winsor", type=float, default=0.05)
    p.add_argument("--min-obs", type=int, default=100)
    p.add_argument("--n-jobs", type=int, default=1)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """係数を推定して CSV に書き出す。

    Args:
        argv (Sequence[str] | None): 引数。

    Returns:
        None

    Examples:
        >>> main(["--start", "202001", "--end", "202012"])  # doctest: +SKIP
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    store = InputStore(args.input_root)
    levels, eligible = ba.load_inputs(store, args.history_start, args.end)
    win_levels, win_ratios = ba.winsorize_levels(levels, eligible, args.winsor)
    win_levels["mv"] = levels["mv"]
    jobs = []
    for d in eligible.index:
        if not args.start <= to_int(d) <= args.end:
            continue
        codes, xl, _, mv = ba.month_sample(d, win_levels, win_ratios, eligible)
        if len(codes) < args.min_obs:
            continue
        jobs.append(delayed(month_coefficients)(d, xl, mv, args.n_draws, args.seed))
    results = Parallel(n_jobs=args.n_jobs)(jobs)
    coefs = pd.DataFrame([r for rows, _ in results for r in rows])
    col = pd.DataFrame([c for _, c in results])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    coefs.to_csv(args.output_dir / "coefficients.csv", index=False)
    summarize(coefs).to_csv(args.output_dir / "coefficient_summary.csv")
    col.to_csv(args.output_dir / "collinearity.csv", index=False)
    logger.info(
        "%d か月 × %d モデル × %d 変数 → %s",
        col.shape[0],
        len(MODELS),
        len(ba.FEATURES),
        args.output_dir,
    )


if __name__ == "__main__":
    main()
