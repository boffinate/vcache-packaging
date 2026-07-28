#!/bin/bash
#
# CI wrapper around libvmod-cachetag/scripts/release-source-archive.sh
# (Phase 1 of the plan: "Make source archive production deterministic and
# release-oriented"). Produces the canonical, pinned cachetag source archive
# and asserts its digest against the value the two packaging lanes already
# pin (CACHETAG_SOURCE_SHA256 in recipes/debian-13/build.sh and
# recipes/el9/cohort.env), so a mismatch fails the run rather than silently
# feeding a different archive into the package lanes.
#
# Usage (run from the libvmod-cachetag checkout root; ci.yml sets
# working-directory: libvmod-cachetag before invoking this):
#   scripts/ci/source-archive.sh VINYL_GIT_DIR VINYL_GIT_COMMIT PINNED_SHA256
#     CACHETAG_GIT_COMMIT [MODE]
#
# MODE is `dev` (default) or `release`. See the comment on the invocation
# below for what the difference is and, importantly, what it is not.
#
# Requires: docker/vinyl-cache-ubuntu-build.Dockerfile already built as the
# image `vinyl-cache-ubuntu-build` (a separate workflow step, so its own log
# stays a separate, readable CI step).

set -euo pipefail

vinyl_git_dir=${1:?VINYL_GIT_DIR required}
vinyl_git_commit=${2:?VINYL_GIT_COMMIT required}
pinned_sha256=${3:?PINNED_SHA256 required}
cachetag_git_commit=${4:?CACHETAG_GIT_COMMIT required}
mode=${5:-dev}

here=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd) # vcache-packaging root
. "$here/scripts/ci/lib/common.sh"

[ -f scripts/release-source-archive.sh ] ||
	die "run this from the libvmod-cachetag checkout root (scripts/release-source-archive.sh not found)"

note "release-source-archive.sh: pinned Vinyl commit $vinyl_git_commit"

cachetag_head=$(git rev-parse HEAD)
[ "$cachetag_head" = "$cachetag_git_commit" ] ||
	die "cachetag checkout HEAD is $cachetag_head, not recorded CACHETAG_GIT_COMMIT $cachetag_git_commit"
note "cachetag release commit: $cachetag_git_commit"

# dev mode remains available for an explicit development checkout. Every
# package workflow passes release mode: it additionally demands a clean tree
# and an annotated vX.Y.Z tag on HEAD.
#
# The mode does NOT change the archive. release-source-archive.sh uses it only
# for the `mode` and `release_stamp` fields of the metadata sidecar and its own
# console output; the tarball's contents and its file mtimes are functions of
# the commit alone. The digest assertion below is therefore the same assertion
# in both modes, and if it ever fails only in release mode, that is a finding
# about the script, not a licence to move the pin.
release_flag=""
case "$mode" in
	dev) ;;
	release) release_flag="--release" ;;
	*) die "unknown MODE: $mode (expected dev or release)" ;;
esac
note "archive mode: $mode"

# shellcheck disable=SC2086 # release_flag is deliberately word-split (empty or one flag)
scripts/release-source-archive.sh \
	--vinyl-git "$vinyl_git_dir" \
	--vinyl-ref "$vinyl_git_commit" \
	--build-profile diagnostic \
	$release_flag \
	--from-archive-target check

archive=$(ls release/dist/libvmod-cachetag-*.tar.gz 2>/dev/null | grep -v dist-raw || true)
[ -n "$archive" ] || die "release-source-archive.sh reported success but produced no archive"
[ "$(printf '%s\n' "$archive" | wc -l)" -eq 1 ] || die "expected exactly one archive, found: $archive"

got=$(sha256_file "$archive")
note "archive digest check"
printf 'produced : %s  %s\n' "$got" "$archive"
printf 'pinned   : %s\n' "$pinned_sha256"

if [ "$got" != "$pinned_sha256" ]; then
	die "$archive sha256 $got does not match the pinned CACHETAG_SOURCE_SHA256 $pinned_sha256.
Do NOT update the pinned value to make this pass. A mismatch means either the
Vinyl input, the cachetag source tree, or the archive-production procedure
itself has drifted from what recipes/debian-13/build.sh and
recipes/el9/cohort.env were written against. Find out which, fix that, and
only then does the pin get updated -- by the maintainer, deliberately, in the
same change that explains why."
fi

printf 'OK: archive digest matches the pinned value\n'
