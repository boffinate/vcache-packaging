#!/bin/sh
#
# EL9 lane: build the synthetic mismatched-candidate Vinyl packages that the
# upgrade-transaction matrix (transactions.sh) resolves against.
#
# The plan requires a mismatched cohort to test upgrade transactions against,
# and -- until a previous supported cohort exists to use as the natural fixture
# -- one synthetic mismatched package fixture per release line, with its source
# and digest retained. This script produces that fixture, in two variants:
#
#   mismatch  higher version-release, DIFFERENT vinyld(abi) hash. The candidate
#             a strict-ABI VMOD cannot resolve against.
#   sameabi   higher version-release, SAME vinyld(abi) hash, different content.
#             The plan's documented known limitation, made concrete.
#
# Both are respins of the baseline cohort's own binary packages: the payload is
# the baseline's files, re-wrapped with new package metadata. The reasoning for
# that choice is in mismatch/vinyl-cache-fixture.spec.in and in the session note.
#
# Nothing is built on the host. Nothing is signed: the lane has no signing key,
# so the fixture is consumed through the same unsigned local repository the
# transaction harness uses (gpgcheck=0). Signed-repository behaviour --
# repo_gpgcheck, key rotation, and how dnf reports an unsigned candidate -- is
# CI work and is untested here.
#
# Usage:
#   mismatch-fixture.sh                  build both variants
#   mismatch-fixture.sh mismatch         build one variant
#   mismatch-fixture.sh --check-reproducible
#                                        build twice, in two containers, and
#                                        fail unless the digests match
#
# Environment:
#   EL9_IMAGE     build image (default from cohort.env)
#   TXN_OUT_DIR   the directory mounted at /out. It must contain packages/ (the
#                 baseline cohort RPMs) and SHA256SUMS, and mismatch/ is written
#                 inside it. Defaults to dist/el9, the lane's own layout; the
#                 reusable workflow points it at a staging directory, because a
#                 generated VMOD's package lives in lane/out and the engine's in
#                 lane/engine.
#
# The fixture variants are ENGINE packages and carry no VMOD name: mismatch/
# container.sh respins vinyl-cache and vinyl-cache-devel only, so unlike the
# Debian lane's fixture script this one needs no VMOD parameters at all.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)

. "$here/cohort.env"

image=${EL9_IMAGE:-almalinux:9}
out=${TXN_OUT_DIR:-$repo/dist/el9}

check_reproducible=
case ${1:-} in
--check-reproducible) check_reproducible=1; shift ;;
esac

variants=${*:-"mismatch sameabi"}

[ -d "$out/packages" ] || {
	printf 'no baseline cohort in %s/packages; run build.sh first\n' "$out" >&2
	exit 2
}
[ -f "$out/SHA256SUMS" ] || {
	printf 'no baseline digests in %s/SHA256SUMS; run build.sh first\n' "$out" >&2
	exit 2
}

printf '\n########## EL9 mismatch fixture ##########\n'
printf 'image     : %s\n' "$image"
printf 'baseline  : %s-%s.el9\n' "$VINYL_VERSION" "$VINYL_RELEASE"
printf 'variants  : %s\n' "$variants"
printf 'output    : %s/mismatch\n' "$out"

docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"

mkdir -p "$out/mismatch/logs"

status=0
# VINYL_TRACK must cross the container boundary: container.sh re-sources
# cohort.env inside the container, and without the variable the pin dispatch
# falls back to trunk while this host script (and its header above) resolved
# release -- which is exactly how nightly run 30357124289 came to look for a
# trunk-shaped baseline filename in a release dist/. Same passthrough as
# build.sh's container invocations.
docker run --rm \
	-e "VINYL_TRACK=$VINYL_TRACK" \
	-v "$here:/recipes:ro" \
	-v "$out:/out" \
	-w /out \
	"$image" \
	bash /recipes/mismatch/container.sh $variants \
	> "$out/mismatch/logs/fixture.log" 2>&1 || status=$?
cat "$out/mismatch/logs/fixture.log"
[ "$status" -eq 0 ] || {
	printf '\nfixture build FAILED (exit %s)\n' "$status" >&2
	exit "$status"
}

printf '\n########## fixture built ##########\n'
cat "$out/mismatch/SHA256SUMS"

# A retained digest that a rebuild does not reproduce is a receipt, not a
# provenance record. Two things had to be pinned to make this hold across
# containers -- BUILDTIME and BUILDHOST -- and both are easy to lose silently,
# so the check is a command rather than a comment.
if [ -n "$check_reproducible" ]; then
	printf '\n########## reproducibility check: second build ##########\n'
	cp "$out/mismatch/SHA256SUMS" "$out/mismatch/logs/SHA256SUMS.first"
	docker run --rm \
		-e "VINYL_TRACK=$VINYL_TRACK" \
		-v "$here:/recipes:ro" \
		-v "$out:/out" \
		-w /out \
		"$image" \
		bash /recipes/mismatch/container.sh $variants \
		> "$out/mismatch/logs/fixture-second.log" 2>&1 || {
		printf 'second fixture build failed; see logs/fixture-second.log\n' >&2
		exit 1
	}
	if diff -u "$out/mismatch/logs/SHA256SUMS.first" "$out/mismatch/SHA256SUMS"; then
		printf 'REPRODUCIBLE: two builds in two containers produced identical digests\n'
	else
		printf 'NOT REPRODUCIBLE: the digests above differ between builds\n' >&2
		exit 1
	fi
fi
