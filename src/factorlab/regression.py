r"""単変量クロスセクション回帰によるファクターリターン推定（Fama-MacBeth 型）.

各時点 t で

.. math:: r_{i, t \to t+1} = a_t + f_t \, x_{i,t} + u_{i,t}

を推定し、傾き :math:`f_t` を「ファクターリターン」として時系列に並べる.
:math:`x` を標準化（z-score / Blom）しておくと、:math:`f_t` は
「エクスポージャー1標準偏差あたりの月次リターン」と解釈できる.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics
from .config import PERIODS_PER_YEAR


def _wls(y: np.ndarray, x: np.ndarray, w: np.ndarray) -> tuple[float, float, float, float]:
    """定数項つき単回帰（加重）. 戻り値: (intercept, slope, t_slope, r2)."""
    X = np.column_stack([np.ones_like(x), x])
    sw = np.sqrt(w)
    Xw, yw = X * sw[:, None], y * sw
    XtX = Xw.T @ Xw
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        return (np.nan,) * 4
    beta = XtX_inv @ (Xw.T @ yw)
    resid = yw - Xw @ beta
    n, k = len(y), 2
    if n <= k:
        return (np.nan,) * 4
    s2 = (resid @ resid) / (n - k)
    se = np.sqrt(s2 * XtX_inv[1, 1])
    ybar = (w * y).sum() / w.sum()
    ss_tot = (w * (y - ybar) ** 2).sum()
    r2 = 1.0 - (resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    return float(beta[0]), float(beta[1]), float(beta[1] / se) if se > 0 else np.nan, float(r2)


def factor_return(
    panel: pd.DataFrame,
    score_col: str,
    ret_col: str = "fwd_rtn",
    weighting: str = "ew",
    cap_col: str = "cap",
    time_level: str = "yyyymm",
    min_obs: int = 20,
) -> pd.DataFrame:
    """月次のクロスセクション回帰を実行し、ファクターリターン系列を返す.

    Parameters
    ----------
    weighting : ``"ew"`` = 等ウェイト OLS / ``"cap"`` = 時価総額加重 WLS /
                ``"sqrt_cap"`` = 時価総額の平方根加重（Barra 流のロバスト版）

    Returns
    -------
    DataFrame（index=yyyymm）: ``alpha``, ``f`` (ファクターリターン), ``t_stat``,
    ``r2``, ``n``, ``cum_f``（f の累積和）, ``cum_f_compound``（複利累積）
    """
    rows = {}
    for ym, blk in panel.groupby(level=time_level, sort=True):
        s, r = blk[score_col].astype(float), blk[ret_col].astype(float)
        ok = s.notna() & r.notna()
        if weighting == "ew":
            w = pd.Series(1.0, index=blk.index)
        else:
            cap = blk[cap_col].astype(float)
            w = np.sqrt(cap) if weighting == "sqrt_cap" else cap
            ok &= cap.notna() & (cap > 0)
        if ok.sum() < min_obs:
            continue
        a, f, t, r2 = _wls(r[ok].to_numpy(), s[ok].to_numpy(), np.asarray(w[ok], dtype=float))
        rows[ym] = {"alpha": a, "f": f, "t_stat": t, "r2": r2, "n": int(ok.sum())}

    out = pd.DataFrame(rows).T.sort_index()
    out.index.name = time_level
    out["cum_f"] = out["f"].cumsum()
    out["cum_f_compound"] = (1.0 + out["f"]).cumprod() - 1.0
    return out


def factor_return_summary(fr: pd.DataFrame, periods: int = PERIODS_PER_YEAR) -> pd.Series:
    """ファクターリターン系列の要約（Fama-MacBeth t値を含む）."""
    f = fr["f"].dropna()
    s = metrics.summarize(f, periods)
    s["fm_t_stat"] = f.mean() / (f.std(ddof=1) / np.sqrt(len(f))) if len(f) > 1 else np.nan
    s["mean_abs_t"] = fr["t_stat"].abs().mean()
    s["frac_|t|>2"] = float(fr["t_stat"].abs().gt(2).mean())
    s["mean_r2"] = fr["r2"].mean()
    s["mean_n"] = fr["n"].mean()
    return s.rename("factor_return")


def fama_macbeth(
    panel: pd.DataFrame,
    x_cols: list[str],
    ret_col: str = "fwd_rtn",
    time_level: str = "yyyymm",
    min_obs: int = 30,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """多変量 Fama-MacBeth: 毎月 r = a + Σ b_k x_k + u を推定し係数系列を返す.

    Returns
    -------
    (coefs, summary):
      coefs   : 月次係数（列 = const + x_cols）
      summary : 各係数の mean / t_FM / t_NW / hit_ratio
    """
    rows = {}
    for ym, blk in panel.groupby(level=time_level, sort=True):
        X = blk[x_cols].astype(float)
        y = blk[ret_col].astype(float)
        ok = y.notna() & X.notna().all(axis=1)
        if ok.sum() < min_obs:
            continue
        A = np.column_stack([np.ones(int(ok.sum())), X.loc[ok].to_numpy()])
        beta, *_ = np.linalg.lstsq(A, y.loc[ok].to_numpy(), rcond=None)
        rows[ym] = beta
    coefs = pd.DataFrame(rows).T
    coefs.columns = ["const", *x_cols]
    coefs.index.name = time_level
    summary = pd.DataFrame({
        c: {
            "mean": coefs[c].mean(),
            "t_FM": coefs[c].mean() / (coefs[c].std(ddof=1) / np.sqrt(len(coefs))),
            "t_NW": metrics.newey_west_tstat(coefs[c]),
            "hit_ratio": float((coefs[c] > 0).mean()),
            "n_months": len(coefs),
        } for c in coefs.columns
    }).T
    return coefs, summary
