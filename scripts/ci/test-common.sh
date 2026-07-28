#!/bin/sh

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$here/scripts/ci/lib/common.sh"

tmp=$(mktemp -d "${TMPDIR:-/tmp}/vcache-ci-common.XXXXXX")
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

git init -q "$tmp/cachetag"
git -C "$tmp/cachetag" config user.name "vcache-packaging selftest"
git -C "$tmp/cachetag" config user.email "selftest@example.invalid"
git -C "$tmp/cachetag" commit -q --allow-empty -m "release input"
commit=$(git -C "$tmp/cachetag" rev-parse HEAD)
git -C "$tmp/cachetag" tag -a v1.0.0 -m "v1.0.0"

ci_verify_cachetag_release_checkout "$tmp/cachetag" v1.0.0 "$commit"

wrong_commit=0000000000000000000000000000000000000000
if (ci_verify_cachetag_release_checkout "$tmp/cachetag" v1.0.0 "$wrong_commit") >/dev/null 2>&1; then
	die "cachetag verifier accepted a tag whose peeled commit disagreed with the recorded commit"
fi

git -C "$tmp/cachetag" tag -d v1.0.0 >/dev/null
git -C "$tmp/cachetag" tag v1.0.0
if (ci_verify_cachetag_release_checkout "$tmp/cachetag" v1.0.0 "$commit") >/dev/null 2>&1; then
	die "cachetag verifier accepted a lightweight release tag"
fi

printf 'OK: CI common helper selftests passed\n'
