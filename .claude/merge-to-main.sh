#!/usr/bin/env bash
# session 結束時自動把 claude/* branch merge 進 main

set -e

REPO_DIR="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$REPO_DIR"

CURRENT=$(git branch --show-current 2>/dev/null)

# 只處理 claude/* branch
if [[ "$CURRENT" != claude/* ]]; then
  exit 0
fi

# 沒有比 main 多的 commit，不需要 merge
AHEAD=$(git rev-list --count "origin/main..HEAD" 2>/dev/null || echo 0)
if [ "$AHEAD" -eq 0 ]; then
  exit 0
fi

echo ">>> 自動 merge $CURRENT → main"

git fetch origin main --quiet

# 切到 main
git checkout main --quiet
git pull origin main --quiet

# merge session branch，state.json 衝突時保留 main 的版本
if ! git merge --no-ff "$CURRENT" --no-edit -m "chore: auto-merge $CURRENT into main" 2>/dev/null; then
  if git diff --name-only --diff-filter=U | grep -q "state.json"; then
    git checkout --ours state.json
    git add state.json
    git commit --no-edit -m "chore: auto-merge $CURRENT into main (keep main state.json)" 2>/dev/null || true
  else
    echo ">>> merge 有衝突，需要手動處理"
    git merge --abort 2>/dev/null || true
    git checkout "$CURRENT" --quiet
    exit 1
  fi
fi

# push main
git push origin main --quiet && echo ">>> ✅ main 已更新"

# 切回 session branch
git checkout "$CURRENT" --quiet
