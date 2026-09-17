#!/usr/bin/env bash
# dev ブランチを main にマージする（experiments/ は main に持ち込まない）。
#
# 使い方（main 上で実行）:
#   bash scripts/merge_dev_to_main.sh
#
# 手順:
#   1. dev を --no-commit でマージ
#   2. マージで入ってきた experiments/ 配下（README.md 以外）をインデックスから外す
#   3. .gitignore は main の内容を維持
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
git rm -r --cached -q --ignore-unmatch experiments
git add -f experiments/README.md
git checkout HEAD -- .gitignore
if git diff --cached --quiet; then
  echo "マージする変更はありません"
  git merge --abort 2>/dev/null || true
  exit 0
fi
git commit -m "Merge dev into main (experiments excluded)"
echo "完了: experiments/ は main には含めていません"
