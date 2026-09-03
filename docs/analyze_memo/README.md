# インド株ファクター検証メモ

作成: 2026-09 / 対象コード: `src/factorlab/`, `notebooks/01`〜`07`

## 目的

MSCI India IMI ユニバースに対する単体ファクター・合成スコアの有効性検証。
ベンチマークは MSCI India（size 1-2）、ユニバースは MSCI India IMI（size 1-2-3）。

## ドキュメント構成

| ファイル | 内容 |
|---|---|
| [01_data_and_conventions.md](01_data_and_conventions.md) | データ・期間・タイミング規約・実装上の落とし穴 |
| [02_methodology.md](02_methodology.md) | 前処理 / 分位分析 / IC / ファクターリターン / 中立化 / 行列比較の定義 |
| [03_results_core.md](03_results_core.md) | コアファクター（100本）の結果 |
| [04_results_alt_ai.md](04_results_alt_ai.md) | AI スコア・オルタナティブファクター（36本+AI）の結果 |
| [05_findings_next_steps.md](05_findings_next_steps.md) | 主要な発見・注意点・次のステップ |
| [06_composite_strategy.md](06_composite_strategy.md) | **合成スコア戦略 v1 の仕様書**（素材・符号・パイプライン・結果・制約・再現手順） |

## TL;DR

- **効く**: リビジョン（core `rev3*` / alt `rev*_gdb`）、モメンタム（`mom12`, `mom12_1`）、
  **AI スコア**（大型ユニバース限定、Q5 IR 1.05）、配当系（`dp_act`, `rdp`）
- **中立化（size+sector）して初めて効く**: TVL、`mom12`/`mom12_1` の Q5、`rdp`、esg_s_composite
- **効かない**: B/P などバリューのロング側（トラップ型）、ESG 水準系、従業員クチコミ、成長系
- **中立化で剥落**: マージン系（`ois_est` 等）の Q5 超過はセクター配分由来だった
- AI・rev_gdb・TVL は相互にもコア100本ともほぼ無相関 → 合成の有力候補

## 再現方法

```bash
uv sync
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/01_factor_validation.ipynb
# 02〜07 も同様。図は output/figures/{01_single_factor,...,07_significant}/ に出力される
```
