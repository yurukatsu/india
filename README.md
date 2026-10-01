# インド株式アクティブ戦略

- `data/`: 生データ（git 管理外）
- `input/`: `scripts/build_input.py` で `data/` から生成した評価用インプット（`input/README.md`）
- `src/alphaeval/`: アルファ評価ライブラリ（カバレッジ・IC・分位分析・基本指標・税控除後評価）。`src/alphaeval/README.md`
- `docs/india_tcg.md`: インド固有の税考慮の枠組み
- `docs/tax_evaluation.md`: 税考慮評価の実装（`alphaeval.tax`）の説明

## ブランチ運用

- `main`: `experiments/`（`README.md` 以外）と `input/**/*.dat` を git 管理外にする（`.gitignore`）。
- `dev`: `experiments/`（検証ノートブック・レポート・図）と `input/**/*.dat`（評価用インプット）も含めて GitHub に上げる。`.pkl` と `.dat.gz` は両ブランチとも管理外。
- `dev` → `main` のマージは `bash scripts/merge_dev_to_main.sh`（main 上で実行）。`main` の `.gitignore` に該当するファイルを除外し、`.gitignore` を維持したままマージコミットを作る。
