#!/usr/bin/env bash
# Shared ci-state/matrix checkout and publication used by matrix/trunk Pages.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

case "${1:-}" in
checkout)
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  if git_remote_head_exists_retry origin ci-state/matrix >/dev/null; then
    git_retry "fetch matrix state" fetch --depth 1 origin ci-state/matrix
    git checkout -B ci-state/matrix FETCH_HEAD
  else
    status=$?
    [ "$status" -eq 2 ] || exit "$status"
    git checkout --orphan ci-state/matrix
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
    git_retry "push matrix state" push origin HEAD:ci-state/matrix
  fi
  ;;
*) die "usage: matrix-state.sh checkout|publish [run-id]" ;;
esac
