"""bax（jbax / gbax）のパラメータ・カタログと、``bc.txt`` からの最適化条件の抽出。

パラメータ説明書（項番 1〜105）を :data:`PARAM_CATALOG` に収め、

- :func:`describe_params`: 実行時の ``bc.txt`` を説明・既定値付きの表にする（既定値との差分が分かる）
- :func:`optimization_settings`: NAV と制約条件（リスク、ウェイト、ファクター、回転率・コスト、アルファ）を
  人が読める形でまとめる

を提供する。``bc.txt`` の値は文字列のまま保持し、解釈（%換算など）は ``interpretation`` 列で示す。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ParamSpec:
    """パラメータの説明。

    Attributes:
        name (str): パラメータ名。
        category (str): 分類（``backtest`` / ``risk`` / ``weight`` / ``factor`` / ``turnover_cost`` /
            ``alpha`` / ``io`` / ``solver`` / ``other``）。
        default (str): 既定値（説明書。jbax / gbax で異なる場合は ``"j: … / g: …"``）。
        description (str): 説明（要約）。
    """

    name: str
    category: str
    default: str
    description: str


_RAW: list[tuple[str, str, str, str]] = [
    # name, category, default, description
    ("NUOPT_VERSION", "solver", "19", "NuOpt のバージョン"),
    ("OWNADJ", "other", "True", "gdb 上の異常値の自動修正"),
    (
        "fn_optz_portfolio",
        "io",
        "$O/op_$B.dat",
        "最適ポートフォリオ（株数・株価・NAV）の格納先",
    ),
    (
        "fn_optz_trad",
        "io",
        "$O/st_trad.dat",
        "最適売買情報（回転率・取引コスト）の格納先",
    ),
    ("fn_rr_pf_bm", "io", "$O/rr_pf_bm.dat", "再開用ベンチマーク格納先"),
    ("fn_rr_pf_fund", "io", "$O/rr_pf_fund.dat", "再開用ファンド格納先"),
    ("fn_rr_bc", "io", "$O/rr_bc.txt", "再開用 bc 格納先"),
    ("fn_op_bc", "io", "$O/bc.txt", "実行時 bc の格納先"),
    ("covtype", "risk", "2", "共分散の型。1: フル共分散、2: ファクターモデル"),
    ("SHIFT_BETA", "factor", "1", "一次制約（β値）算出用のシフト"),
    ("f_output_simple", "io", "1", "【デバッグ用】SIMPLE ファイル出力フラグ"),
    ("system", "backtest", "j: j / g: g", "体系（jbax = j、gbax = g）"),
    (
        "mode",
        "backtest",
        "1",
        "動作モード。1: OP（最適化バックテスト）、2: RR（途中再開）、3: PC（パフォーマンス再計算）、4: QY（bc を標準出力）",
    ),
    (
        "fn_dyn_bc",
        "io",
        "NONE",
        "動的 bc ファイル格納先（日付ごとにパラメータを上書き）",
    ),
    ("fn_dyn_bc2", "io", "NONE", "動的 bc ファイル格納先 2"),
    ("numeraire", "backtest", "NONE", "基軸通貨（gbax のみ）"),
    (
        "f_cur_policy_bm",
        "backtest",
        "0",
        "ベンチマークの為替ヘッジ。0: なし、1: フルヘッジ",
    ),
    (
        "f_cur_policy_fund",
        "backtest",
        "0",
        "ファンドの為替ヘッジ。0: なし、1: フルヘッジ、2: 最適化のみフルヘッジ、3: 最適ヘッジ、4: 最適ヘッジ（オーバーヘッジ禁止）",
    ),
    (
        "date_from",
        "backtest",
        "j: 198412 / g: 200312",
        "バックテスト開始日（YYYYMM / YYYYMMDD、-1: 前月末、0: 前営業日）",
    ),
    ("date_to", "backtest", "-1", "バックテスト終了日"),
    (
        "f_date_init_nolag",
        "backtest",
        "j: 1 / g: 0",
        "開始時にベンチマーク計算を遅らせるか",
    ),
    ("dir_in_base", "io", "NONE", "（廃止）入力ディレクトリ"),
    ("dir_out", "io", "out", "（廃止）出力ディレクトリ（--out を使う）"),
    ("f_clean_dir_out", "io", "0", "実行前に出力ディレクトリを削除するか"),
    ("fn_date", "io", "NONE", "日付指定ファイル"),
    ("fn_bm", "io", "NONE", "ベンチマークファイル"),
    ("fn_altbm", "io", "NONE", "第二ベンチマークファイル"),
    ("fn_altbm2", "io", "NONE", "第三ベンチマークファイル"),
    ("fn_mkt", "io", "NONE", "マーケットポートフォリオファイル"),
    ("fn_unv", "io", "NONE", "ユニバースファイル"),
    (
        "f_only_unv",
        "weight",
        "0",
        "保有をユニバースに限定するか。1: 限定（ユニバース外は強制売却）",
    ),
    (
        "fn_init_bm",
        "backtest",
        "NONE",
        "開始時のベンチマークファイル（`create_pf_cash -C $N` は現金）",
    ),
    (
        "fn_init_fund",
        "backtest",
        "NONE",
        "開始時のポートフォリオファイル（`create_pf_cash -C $N -n$2` は現金 $2 百万）",
    ),
    ("fn_cur_policy", "io", "NONE", "通貨保有制約ファイル"),
    ("coef_rskavr", "risk", "0.1", "リスク回避度（効用 = α − λ·TE² の λ）"),
    (
        "target_omega",
        "risk",
        "NONE",
        "ターゲット TE（年率 %）。正: 上限制約、負: 下限制約",
    ),
    ("coef_altrskavr", "risk", "0", "代替（第二 BM）リスク回避度"),
    (
        "target_altrsk",
        "risk",
        "NONE",
        "ターゲット代替リスク（年率 %）。正: 上限、負: 下限",
    ),
    ("coef_alt2rskavr", "risk", "0", "第三リスク回避度"),
    (
        "coef_p_cash_outflow",
        "turnover_cost",
        "0",
        "保有ポジションにかかる売買コスト係数",
    ),
    ("cs_cash_wgt", "weight", "0", "キャッシュポジションへの制約"),
    (
        "cs_sum_rskas_wgt_s",
        "weight",
        "0,0",
        "リスク資産ショート側合計ウェイト制約（min,max）",
    ),
    ("cs_owgt_default", "weight", "0,1", "個別銘柄の保有ウェイト制約（min,max、小数）"),
    ("fn_cs_owgt", "io", "NONE", "個別銘柄ウェイト制約ファイル"),
    (
        "cs_a_owgt_default",
        "weight",
        "-1,1",
        "個別銘柄の対ベンチマーク・ウェイト制約（min,max、小数）",
    ),
    (
        "fn_add_cs_owgt",
        "io",
        "NONE",
        "個別銘柄ウェイトの追加制約ファイル（記載銘柄のみ上書き）",
    ),
    ("rskmdl_name", "risk", "j: barra_jpe3 / g: barra_gemlt", "リスクモデル名"),
    (
        "f_not_in_rskmdl",
        "weight",
        "1",
        "リスクモデルに無い銘柄。0: 保有しない、1: BM ウェイトでパッシブ保有",
    ),
    ("cs_fbeta", "factor", "-1,+1", "アクティブ・ファンダメンタル β 制約（min,max）"),
    (
        "cs_fbeta_loc",
        "factor",
        "-1,+1",
        "アクティブ・ファンダメンタル β（対ローカル市場）制約",
    ),
    (
        "cs_rskidx_default",
        "factor",
        "-10,+10",
        "アクティブ・リスクインデックス制約（min,max、標準偏差単位）",
    ),
    (
        "cs_industry_default",
        "factor",
        "-1,+1",
        "アクティブ・業種ウェイト制約（min,max、小数）",
    ),
    (
        "cs_country_default",
        "factor",
        "-1,+1",
        "アクティブ・国ウェイト制約（min,max、小数）",
    ),
    (
        "cs_currency_default",
        "factor",
        "j: 0,0 / g: -1,+1",
        "アクティブ・通貨ウェイト制約（min,max、小数）",
    ),
    ("fn_cs_rskexp", "io", "NONE", "アクティブ・コモンファクター制約ファイル"),
    (
        "fn_cs_net_abs_rskexp",
        "io",
        "NONE",
        "通貨を含む絶対ベースのコモンファクター制約ファイル",
    ),
    ("num_cs_addl", "factor", "0", "追加する一次制約式の数"),
    ("fn_exp_cs_addl", "io", "NONE", "一次制約のエクスポージャーファイル"),
    ("cs_addl_default", "factor", "0,0", "一次制約の下限・上限の既定値"),
    ("num_cs_addq", "factor", "0", "追加する二次制約式の数（負ならファイルの列数）"),
    ("fn_exp_cs_addq", "io", "NONE", "二次制約のエクスポージャーファイル"),
    ("cs_addq_default", "factor", "0,0", "二次制約の下限・上限の既定値"),
    ("cs_addq_scaling_adj", "factor", "1.0", "二次制約のスケーリング調整値"),
    ("fn_cs_add", "io", "NONE", "一次・二次制約の上限・下限ファイル"),
    (
        "cs_rot",
        "turnover_cost",
        "1",
        "売買回転率制約（小数、% 表記ではない）。個別ウェイト制約超過時は自動緩和",
    ),
    ("f_allow_cs_violation", "solver", "1", "最適化で制約違反を許すか。1: 許可"),
    (
        "act_tr_cost_default",
        "turnover_cost",
        "0.5,0.5,0",
        "実績売買コストの既定値（p, Cfix(%), Cvar）",
    ),
    ("fn_act_tr_cost", "io", "NONE", "実績売買コスト係数の生成コマンド / ファイル"),
    (
        "disc_fn_act_tr_cost",
        "turnover_cost",
        "j: 1e9 / g: 1",
        "実績売買コスト Cvar の割引額（基軸通貨）",
    ),
    (
        "est_tr_cost_default",
        "turnover_cost",
        "1,5,0",
        "推定売買コストの既定値（線形型 p, Cfix(%), Cvar）",
    ),
    ("fn_est_tr_cost", "io", "NONE", "推定売買コスト係数の生成コマンド / ファイル"),
    (
        "disc_fn_est_tr_cost",
        "turnover_cost",
        "j: 1e9 / g: 1",
        "推定売買コスト Cvar の割引額（基軸通貨）",
    ),
    ("act_borrow_r_default", "turnover_cost", "0", "実績借株レート（年率 %）"),
    ("fn_act_borrow_r", "io", "NONE", "実績借株レートファイル"),
    ("est_borrow_r_default", "turnover_cost", "0", "推定借株レート（年率 %）"),
    ("fn_est_borrow_r", "io", "NONE", "推定借株レートファイル"),
    ("act_fixed_fee", "turnover_cost", "0", "固定信託報酬率（年率 %）"),
    ("act_tr_fee", "turnover_cost", "0", "1 回あたりの売買手数料（基軸通貨建）"),
    ("fn_alpha", "alpha", "NONE", "期待リターン（アルファ）ファイル"),
    ("i_alpha", "alpha", "1", "使用する期待リターンの列番号"),
    ("fn_zero_alpha", "alpha", "NONE", "期待リターンを強制ゼロにする銘柄のファイル"),
    ("coef_horiz", "alpha", "12", "投資ホライズン（か月）。取引コストの償却期間"),
    ("coef_init_horiz_tc", "alpha", "NONE", "開始時の投資ホライズン"),
    ("max_num_hold", "weight", "NONE", "最大保有銘柄数（0 以下は制約なし）"),
    (
        "min_hold_wgt",
        "weight",
        "0",
        "最低保有ウェイト（絶対値がこれ以下ならキャッシュへ。最適化が 2 回走る）",
    ),
    (
        "th_straddle_allow",
        "weight",
        "1",
        "両建てポジションの許容幅（ショートがある場合）",
    ),
    (
        "type_w2s",
        "solver",
        "0,0",
        "ウェイト → 株数変換。0: 連続値、1: 四捨五入、2: 整数計画法、3: スペシフィック分散加重",
    ),
    (
        "f_passive_linked",
        "weight",
        "1",
        "linked 資産（優先株・ADR 等）を BM ウェイトでパッシブ保有",
    ),
    ("optz_method", "solver", "auto", "最適化アルゴリズム"),
    ("optz_altmethod", "solver", "lipm", "第二最適化アルゴリズム"),
    ("optz_scaling_default", "solver", "OFF", "NuOpt スケーリング"),
    ("optz_scaling_num_switch_date", "solver", "0", "NuOpt スケーリング切替日数"),
    ("optz_scaling_switch_dates", "solver", "NONE", "NuOpt スケーリング切替日"),
    ("num_cnstr_relax", "solver", "10", "制約緩和の回数"),
    ("coef_cnstr_relax", "solver", "0.25", "制約緩和の係数"),
    (
        "f_only_relax_rot",
        "solver",
        "0",
        "求解不能時の緩和対象。0: OFF、1: 回転率制約のみ、2: 回転率と追加線形制約",
    ),
    (
        "f_same_nav",
        "backtest",
        "1",
        "常に一定の NAV で運用するか（1: 毎回 NAV をリセット）",
    ),
    ("f_output_daily", "io", "0", "パフォーマンス出力の日次化"),
    ("f_output_mepf", "io", "1", "月末アクティブポートフォリオ（me ファイル）を出力"),
    ("f_get_attr", "io", "0", "銘柄属性（社名・売買単位）の取得"),
    ("fn_abbr1", "io", "j: q7 / g: MODEL_VER", "ファイル名置換記号 $1"),
    ("fn_abbr2", "io", "100", "ファイル名置換記号 $2（既定では初期 NAV の百万単位）"),
    ("fn_abbr3", "io", "NONE", "ファイル名置換記号 $3"),
    ("fn_abbr4", "io", "NONE", "ファイル名置換記号 $4"),
    ("fn_abbr5", "io", "NONE", "ファイル名置換記号 $5"),
    ("f_dbopen_retry_interval", "other", "0", "DB 再接続の間隔"),
    ("f_dbopen_retry_num", "other", "0", "DB 再接続の回数"),
    ("f_use_rskmdl_refresh_data", "other", "0", "リスクモデルの更新データを使うか"),
    ("fn_gdbdata_dir", "io", "NONE", "（未使用）ベンチマークファイル格納先"),
    ("fn_dbdata_dir", "io", "NONE", "（未使用）ファイル格納先"),
]

#: パラメータ名 → 説明
PARAM_CATALOG: dict[str, ParamSpec] = {
    n: ParamSpec(n, c, d, desc) for n, c, d, desc in _RAW
}

#: 分類の表示順と日本語名
CATEGORY_LABELS: dict[str, str] = {
    "backtest": "バックテスト設定",
    "risk": "リスク",
    "weight": "ウェイト制約",
    "factor": "ファクター制約",
    "turnover_cost": "回転率・コスト",
    "alpha": "アルファ・ホライズン",
    "solver": "ソルバー",
    "io": "入出力",
    "other": "その他",
}


def describe_params(params: dict[str, str], system: str | None = None) -> pd.DataFrame:
    """``bc.txt`` のパラメータを説明・既定値付きの表にする。

    Args:
        params (dict[str, str]): :func:`alphaeval.bax.reader.read_params` の戻り値。
        system (str | None): ``"j"`` / ``"g"``。既定値が体系で異なる項目の表示に使う。``None`` なら
            ``params["system"]`` を使う。

    Returns:
        pd.DataFrame: ``category`` ``parameter`` ``value`` ``default`` ``is_default`` ``description`` 列。
        カタログに無いパラメータは ``category = "unknown"``。分類順 → 説明書の順に並ぶ。

    Examples:
        >>> df = describe_params({"target_omega": "5", "cs_rot": "0.15", "system": "g"})
        >>> df.set_index("parameter").loc["cs_rot", "is_default"]
        False
    """
    system = system or params.get("system")
    rows = []
    order = {n: i for i, n in enumerate(PARAM_CATALOG)}
    for name, value in params.items():
        spec = PARAM_CATALOG.get(name)
        default = _resolve_default(spec.default, system) if spec else ""
        rows.append(
            {
                "category": spec.category if spec else "unknown",
                "parameter": name,
                "value": value,
                "default": default,
                "is_default": _same_value(value, default) if spec else np.nan,
                "description": spec.description if spec else "",
                "_order": order.get(name, 10_000),
            }
        )
    df = pd.DataFrame(rows)
    cat_order = {c: i for i, c in enumerate(CATEGORY_LABELS)}
    df["_cat"] = df["category"].map(cat_order).fillna(len(cat_order))
    return (
        df.sort_values(["_cat", "_order"])
        .drop(columns=["_cat", "_order"])
        .reset_index(drop=True)
    )


def _resolve_default(default: str, system: str | None) -> str:
    """``"j: … / g: …"`` 形式の既定値から体系に応じた値を取り出す。"""
    if " / " in default and default.startswith(("j:", "g:")):
        parts = dict(p.split(": ", 1) for p in default.split(" / "))
        return parts.get(system or "", default)
    return default


def _same_value(value: str, default: str) -> bool:
    """文字列としての比較に加え、数値・カンマ区切り数値の一致も許す。"""
    if value.strip() == default.strip():
        return True
    try:
        a = [float(x) for x in value.replace("+", "").split(",")]
        b = [float(x) for x in default.replace("+", "").split(",")]
        return len(a) == len(b) and all(abs(x - y) < 1e-12 for x, y in zip(a, b))
    except ValueError:
        return False


def _pair(value: str | None) -> tuple[float, float] | None:
    """``"min,max"`` を数値の組にする。"""
    if not value or value.upper() == "NONE" or "," not in value:
        return None
    try:
        lo, hi = (float(x) for x in value.replace("+", "").split(",")[:2])
        return lo, hi
    except ValueError:
        return None


def _pct(x: float) -> str:
    return f"{x * 100:g}%"


def optimization_settings(
    params: dict[str, str], nav: float | None = None
) -> pd.DataFrame:
    """NAV と制約条件を人が読める形でまとめる。

    Args:
        params (dict[str, str]): ``bc.txt`` のパラメータ。
        nav (float | None): 実際の初期 NAV（``op`` ファイルのヘッダー等から）。``None`` なら
            ``fn_init_fund`` の ``-n$2``（百万単位）から推定する。

    Returns:
        pd.DataFrame: ``item``（項目）、``value``（設定値）、``interpretation``（解釈）、``parameter``（元のパラメータ）
        の列。行は NAV → リスク → ウェイト → ファクター → 回転率・コスト → アルファ → バックテスト設定の順。

    Examples:
        >>> p = {"target_omega": "5", "cs_owgt_default": "0,0.1", "cs_rot": "0.15", "fn_abbr2": "100",
        ...      "fn_init_fund": "`$I/create_pf_cash -C $N -n$2`", "numeraire": "USA", "f_same_nav": "1"}
        >>> s = optimization_settings(p).set_index("item")
        >>> s.loc["ターゲット TE（omega）", "interpretation"]
        '推定 TE ≤ 5%（年率）'
        >>> s.loc["個別銘柄ウェイト", "interpretation"]
        '0% 〜 10%'
    """
    g = params.get
    rows: list[tuple[str, str, str, str]] = []

    # ---- NAV
    init_fund = g("fn_init_fund", "")
    abbr2 = g("fn_abbr2")
    if nav is not None:
        nav_text = f"{nav:,.0f} {g('numeraire', '')}".strip()
        nav_interp = "op ファイル等から取得した初期 NAV"
    elif "create_pf_cash" in init_fund and "-n" in init_fund and abbr2:
        try:
            nav_val = float(abbr2) * 1e6
            nav_text = f"{nav_val:,.0f} {g('numeraire', '')}".strip()
            nav_interp = (
                f"fn_init_fund の -n$2 = {abbr2}（百万単位）→ 現金 {abbr2} 百万で開始"
            )
        except ValueError:
            nav_text, nav_interp = init_fund, "fn_init_fund から推定できない"
    else:
        nav_text, nav_interp = init_fund or "不明", "fn_init_fund を参照"
    rows.append(("初期 NAV", nav_text, nav_interp, "fn_init_fund / fn_abbr2"))
    rows.append(
        (
            "NAV の扱い",
            g("f_same_nav", "1"),
            "毎回リバランス時に NAV を初期値へリセット（一定 NAV）"
            if g("f_same_nav", "1") == "1"
            else "NAV はパフォーマンスに応じて変動",
            "f_same_nav",
        )
    )
    rows.append(
        (
            "初期ポートフォリオ",
            init_fund or "NONE",
            "現金 100% から開始"
            if "create_pf_cash" in init_fund
            else "指定ファイルから開始",
            "fn_init_fund",
        )
    )

    # ---- リスク
    omega = g("target_omega")
    if omega and omega.upper() != "NONE":
        v = float(omega)
        rows.append(
            (
                "ターゲット TE（omega）",
                omega,
                f"推定 TE {'≤' if v > 0 else '≥'} {abs(v):g}%（年率）",
                "target_omega",
            )
        )
    else:
        rows.append(
            (
                "ターゲット TE（omega）",
                "NONE",
                "TE 制約なし（リスク回避度で調整）",
                "target_omega",
            )
        )
    rows.append(
        (
            "リスク回避度",
            g("coef_rskavr", "0.1"),
            "効用 = α − λ·TE² の λ"
            + (
                "（0: 効用にリスク項なし、TE 制約のみ）"
                if g("coef_rskavr") == "0"
                else ""
            ),
            "coef_rskavr",
        )
    )
    rows.append(
        (
            "リスクモデル",
            g("rskmdl_name", ""),
            "共分散: "
            + ("ファクターモデル" if g("covtype", "2") == "2" else "フル共分散"),
            "rskmdl_name / covtype",
        )
    )
    for key, label in [
        ("target_altrsk", "第二 BM 乖離リスク"),
        ("coef_altrskavr", "第二 BM リスク回避度"),
    ]:
        v = g(key)
        if v and v.upper() != "NONE" and v != "0":
            rows.append((label, v, "", key))

    # ---- ウェイト
    p = _pair(g("cs_owgt_default"))
    if p:
        rows.append(
            (
                "個別銘柄ウェイト",
                g("cs_owgt_default"),
                f"{_pct(p[0])} 〜 {_pct(p[1])}",
                "cs_owgt_default",
            )
        )
    p = _pair(g("cs_a_owgt_default"))
    if p:
        rows.append(
            (
                "個別銘柄アクティブウェイト",
                g("cs_a_owgt_default"),
                f"対 BM {_pct(p[0])} 〜 {_pct(p[1])}",
                "cs_a_owgt_default",
            )
        )
    for key, label in [
        ("fn_cs_owgt", "個別ウェイト制約ファイル"),
        ("fn_add_cs_owgt", "個別ウェイト追加制約ファイル"),
    ]:
        if g(key) and g(key).upper() != "NONE":
            rows.append((label, g(key), "銘柄別に上下限を上書き", key))
    rows.append(
        (
            "キャッシュ制約",
            g("cs_cash_wgt", "0"),
            "キャッシュウェイトの制約値",
            "cs_cash_wgt",
        )
    )
    mh = g("max_num_hold", "NONE")
    rows.append(
        (
            "最大保有銘柄数",
            mh,
            "制約なし" if mh.upper() == "NONE" or float(mh) <= 0 else f"{mh} 銘柄以下",
            "max_num_hold",
        )
    )
    rows.append(
        (
            "最低保有ウェイト",
            g("min_hold_wgt", "0"),
            "制約なし"
            if g("min_hold_wgt", "0") == "0"
            else f"{_pct(float(g('min_hold_wgt')))} 未満はキャッシュへ",
            "min_hold_wgt",
        )
    )
    rows.append(
        (
            "ユニバース限定",
            g("f_only_unv", "0"),
            "ユニバース外は強制売却"
            if g("f_only_unv") == "1"
            else "リバランス前の保有銘柄も保有可",
            "f_only_unv",
        )
    )
    rows.append(
        (
            "リスクモデル外銘柄",
            g("f_not_in_rskmdl", "1"),
            "BM ウェイトでパッシブ保有"
            if g("f_not_in_rskmdl", "1") == "1"
            else "保有しない",
            "f_not_in_rskmdl",
        )
    )

    # ---- ファクター
    for key, label, unit in [
        ("cs_fbeta", "アクティブ β（世界）", "β"),
        ("cs_fbeta_loc", "アクティブ β（ローカル）", "β"),
        ("cs_rskidx_default", "アクティブ・リスクインデックス", "sd"),
        ("cs_industry_default", "アクティブ・業種ウェイト", "%"),
        ("cs_country_default", "アクティブ・国ウェイト", "%"),
        ("cs_currency_default", "アクティブ・通貨ウェイト", "%"),
    ]:
        p = _pair(g(key))
        if p is None:
            continue
        if unit == "%":
            interp = f"{_pct(p[0])} 〜 {_pct(p[1])}"
            if abs(p[0]) >= 1 and abs(p[1]) >= 1:
                interp += "（実質制約なし）"
        elif unit == "sd":
            interp = f"{p[0]:g} 〜 {p[1]:g} 標準偏差"
        else:
            interp = f"{p[0]:g} 〜 {p[1]:g}"
        rows.append((label, g(key), interp, key))
    for key, label in [
        ("fn_cs_rskexp", "コモンファクター制約ファイル"),
        ("fn_cs_net_abs_rskexp", "絶対ベース・コモンファクター制約ファイル"),
        ("fn_cs_add", "追加制約の上下限ファイル"),
    ]:
        if g(key) and g(key).upper() != "NONE":
            rows.append((label, g(key), "", key))
    for n_key, f_key, d_key, label in [
        ("num_cs_addl", "fn_exp_cs_addl", "cs_addl_default", "追加一次制約"),
        ("num_cs_addq", "fn_exp_cs_addq", "cs_addq_default", "追加二次制約"),
    ]:
        n = g(n_key, "0")
        if n not in ("0", "NONE", ""):
            rows.append(
                (
                    label,
                    f"{n} 本",
                    f"エクスポージャー: {g(f_key)}、既定の上下限: {g(d_key)}",
                    f"{n_key} / {f_key} / {d_key}",
                )
            )

    # ---- 回転率・コスト
    rot = g("cs_rot")
    if rot and rot.upper() != "NONE":
        rows.append(
            (
                "回転率制約",
                rot,
                f"1 回のリバランスで {_pct(float(rot))} 以下（小数指定）"
                + (
                    "、求解不能時は回転率制約のみ緩和"
                    if g("f_only_relax_rot") == "1"
                    else ""
                ),
                "cs_rot / f_only_relax_rot",
            )
        )
    rows.append(
        (
            "推定売買コスト",
            g("est_tr_cost_default", ""),
            _cost_text(g("est_tr_cost_default", ""))
            + (
                f"。係数ファイル: {g('fn_est_tr_cost')}"
                if g("fn_est_tr_cost", "NONE").upper() != "NONE"
                else ""
            ),
            "est_tr_cost_default / fn_est_tr_cost",
        )
    )
    rows.append(
        (
            "実績売買コスト",
            g("act_tr_cost_default", ""),
            _cost_text(g("act_tr_cost_default", ""))
            + (
                f"。係数ファイル: {g('fn_act_tr_cost')}"
                if g("fn_act_tr_cost", "NONE").upper() != "NONE"
                else ""
            ),
            "act_tr_cost_default / fn_act_tr_cost",
        )
    )
    for key, label, unit in [
        ("act_fixed_fee", "固定信託報酬", "年率 %"),
        ("act_tr_fee", "売買手数料（1 回）", "基軸通貨"),
        ("act_borrow_r_default", "借株レート（実績）", "年率 %"),
    ]:
        v = g(key, "0")
        if v not in ("0", "0.0", "NONE"):
            rows.append((label, v, unit, key))
    rows.append(
        (
            "制約違反の許容",
            g("f_allow_cs_violation", "1"),
            "許可（緩和して求解）"
            if g("f_allow_cs_violation", "1") == "1"
            else "不許可",
            "f_allow_cs_violation",
        )
    )

    # ---- アルファ
    rows.append(
        (
            "アルファファイル",
            g("fn_alpha", "NONE"),
            f"列 {g('i_alpha', '1')} を使用",
            "fn_alpha / i_alpha",
        )
    )
    rows.append(
        (
            "投資ホライズン",
            g("coef_horiz", "12"),
            f"{g('coef_horiz', '12')} か月（取引コストの償却期間）",
            "coef_horiz",
        )
    )
    if g("fn_zero_alpha", "NONE").upper() != "NONE":
        rows.append(
            (
                "アルファ強制ゼロ",
                g("fn_zero_alpha"),
                "記載銘柄の期待リターンを 0 にする",
                "fn_zero_alpha",
            )
        )

    # ---- バックテスト
    rows.append(
        (
            "期間",
            f"{g('date_from', '')} 〜 {g('date_to', '')}",
            "",
            "date_from / date_to",
        )
    )
    rows.append(
        ("基軸通貨", g("numeraire", "NONE"), "リターン・NAV の通貨", "numeraire")
    )
    rows.append(
        (
            "為替ヘッジ",
            f"BM: {g('f_cur_policy_bm', '0')}, ファンド: {g('f_cur_policy_fund', '0')}",
            "0 = ヘッジなし",
            "f_cur_policy_bm / f_cur_policy_fund",
        )
    )
    rows.append(("ベンチマーク", g("fn_bm", "NONE"), "", "fn_bm"))
    rows.append(("ユニバース", g("fn_unv", "NONE"), "", "fn_unv"))
    rows.append(
        (
            "モード",
            g("mode", "1"),
            {
                "1": "OP: 最適化バックテスト",
                "2": "RR: 途中再開",
                "3": "PC: パフォーマンス再計算",
                "4": "QY: bc 出力",
            }.get(g("mode", "1"), ""),
            "mode",
        )
    )
    if g("fn_dyn_bc", "NONE").upper() != "NONE":
        rows.append(
            (
                "動的 bc",
                g("fn_dyn_bc"),
                "日付ごとにパラメータを上書きするファイル（出力には含まれないため別途確認）",
                "fn_dyn_bc",
            )
        )

    return pd.DataFrame(rows, columns=["item", "value", "interpretation", "parameter"])


def _cost_text(value: str) -> str:
    """``p,Cfix(%),Cvar`` 形式の売買コスト指定を説明文にする。"""
    parts = value.split(",")
    if len(parts) < 3:
        return value
    try:
        p, cfix, cvar = (float(x) for x in parts[:3])
    except ValueError:
        return value
    shape = "線形" if p == 1 else ("平方根" if p == 0.5 else f"べき {p:g}")
    return f"{shape}型、固定 {cfix:g}%、変動係数 {cvar:g}"
