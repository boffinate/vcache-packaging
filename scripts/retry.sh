#!/usr/bin/env bash
# Thin command adapter for workflows that need scripts/lib.sh retry policies.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

case "${1:-}" in
release)
  [ $# -ge 5 ] || die "usage: retry.sh release <tag> <target> <notes-file> <asset> [asset ...]"
  shift
  replace_github_release_retry "$@"
  ;;
*) die "usage: retry.sh release <tag> <target> <notes-file> <asset> [asset ...]" ;;
esac
