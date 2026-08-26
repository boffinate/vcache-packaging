#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/lib.sh"

[ $# -eq 2 ] || die "usage: resolve-engine-commit.sh <git-url> <branch>"
git_url=$1 branch=$2
line=$(git_retry "resolve engine branch $branch" ls-remote --exit-code --heads "$git_url" "refs/heads/$branch")
commit=${line%%$'\t'*}
printf '%s\n' "$commit"
