#!/usr/bin/env bash
# dev ブランチを main にマージする（experiments/ や input/**/*.dat など main で無視するファイルは持ち込まない）。
#
# 使い方（main 上で実行）:
#   bash scripts/merge_dev_to_main.sh
#
# 手順:
#   1. dev を --no-commit でマージ
#   2. .gitignore は main の内容を維持
#   3. main の .gitignore に該当する追跡ファイルをインデックスから外す（experiments/README.md は残す）
#   4. コミット
set -euo pipefail

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "main ブランチ上で実行してください（現在: $branch）" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "作業ツリーに未コミットの変更があります" >&2
  exit 1
fi

git merge --no-ff --no-commit dev || true
# main の .gitignore を維持し、それに該当する追跡ファイル（experiments/、input/**/*.dat など）をインデックスから外す
git checkout HEAD -- .gitignore
git ls-files -ci --exclude-standard -z | xargs -0 -r git rm -r --cached -q --ignore-unmatch
git add -f experiments/README.md
if git diff --cached --quiet; then
  echo "マージする変更はありません"
  git merge --abort 2>/dev/null || true
  exit 0
fi
git commit -m "Merge dev into main (ignored files excluded)"
echo "完了: main の .gitignore に該当するファイルは含めていません"
