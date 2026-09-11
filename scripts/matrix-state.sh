#!/usr/bin/env bash
# Shared state checkout and publication used by matrix Pages workflows.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

STATE_BRANCH=${VCACHE_STATE_BRANCH:-ci-state/matrix}

case "${1:-}" in
checkout)
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  if git_remote_head_exists_retry origin "$STATE_BRANCH" >/dev/null; then
    git_retry "fetch matrix state" fetch --depth 1 origin "$STATE_BRANCH"
    git checkout -B "$STATE_BRANCH" FETCH_HEAD
  else
    status=$?
    [ "$status" -eq 2 ] || exit "$status"
    git checkout --orphan "$STATE_BRANCH"
    git rm -rf --quiet . >/dev/null 2>&1 || true
  fi
  ;;
publish)
  [ $# -eq 2 ] || die "usage: matrix-state.sh publish <run-id>"
  git add -A
  if git diff --cached --quiet; then
    echo "state unchanged; nothing to commit"
  else
    git commit -m "matrix state after run $2"
    git_retry "push matrix state" push origin "HEAD:$STATE_BRANCH"
  fi
  ;;
*) die "usage: matrix-state.sh checkout|publish [run-id]" ;;
esac
