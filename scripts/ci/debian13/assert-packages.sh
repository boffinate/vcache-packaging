#!/bin/bash
#
# Payload, ABI and hardening assertions against the pbuilder-produced .deb
# files in dist/debian-13/.
#
# The checks themselves are scripts/ci/lib/package-checks.sh, shared with the
# EL9 lane and with both generated-recipe verify stages since Step 7 Wave 0.
# This file is the lane-specific half: which packages exist, which pins name
# their versions, and which of them has a build log in this row.
#
# Two copies of the ELF hardening block remain outside that library, in
# recipes/debian-13/container/stage-vinyl.sh and stage-cachetag.sh. They belong
# to the local whole-cohort lane, which CI does not run, and folding them in
# needs a build-log tee and a compile-line selector for the engine's non-libtool
# translation units. Recorded as a named carry-forward rather than done
# half-way; see the Step 7 Wave 0 note.
#
# Does not need to run as root; only reads the produced .deb files.
#
# Usage: assert-packages.sh [all|engine|vmod]   (default: all)
#
# `engine` asserts only the Vinyl half and `vmod` only the cachetag half, for
# CI's split engine and VMOD package jobs (Phase 2 of
# docs/20260728_0833_plan_vmod-matrix-failure-isolation.md). This script reads
# packages and asserts; it produces nothing, so the scope cannot move a package
# byte. The VMOD job still runs the default `all` scope, because by then both
# halves are present in dist/debian-13 and its evidence should not shrink.
#
# DRAFT, unexecuted -- see ../../../DESIGN.md sections 2 and 4.

set -euo pipefail

scope=${1:-all}
case $scope in
all | engine | vmod) : ;;
*) printf 'usage: %s [all|engine|vmod]\n' "$0" >&2; exit 2 ;;
esac

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"
# The payload allowlist and the ELF hardening inspection, shared with the EL9
# lane and with both generated-recipe verify stages since Step 7 Wave 0. The
# duplication this replaces was the measured cost of Wave B: B3, B6 and B9 were
# each a lesson one lane's copy had and another's had not.
# shellcheck source=../lib/package-checks.sh
. "$(CDPATH= cd -- "$here/../lib" && pwd)/package-checks.sh"

repo_dir=$(CDPATH= cd -- "$here/../../.." && pwd)
out_dir=$repo_dir/dist/debian-13
log_dir=$out_dir/logs

command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not available on this runner"
command -v readelf >/dev/null 2>&1 || {
	note "installing binutils for readelf"
	sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends binutils
}

if [ "$scope" != vmod ]; then
###############################################################################
note "vinyl-cache runtime: ABI Provides (mirrors stage-vinyl.sh assertions)"
###############################################################################

runtime_deb=$(ls "$out_dir"/vinyl-cache_"${VINYL_PACKAGE_VERSION}"_*.deb)
provides=$(dpkg-deb -f "$runtime_deb" Provides)
printf 'Provides: %s\n' "$provides"
case "$provides" in
	*"vinyld-abi-$VINYL_STRICT_ABI"*) printf 'OK: runtime provides vinyld-abi-%s\n' "$VINYL_STRICT_ABI" ;;
	*) die "runtime does not provide vinyld-abi-$VINYL_STRICT_ABI" ;;
esac
case "$provides" in
	*"vinyld-vrt (= "*) printf 'OK: runtime provides a versioned vinyld-vrt\n' ;;
	*) die "runtime does not provide vinyld-vrt" ;;
esac

dev_deb=$(ls "$out_dir"/vinyl-cache-dev_"${VINYL_PACKAGE_VERSION}"_*.deb)
dev_depends=$(dpkg-deb -f "$dev_deb" Depends)
printf 'vinyl-cache-dev Depends: %s\n' "$dev_depends"
case "$dev_depends" in
	*"vinyl-cache (= $VINYL_PACKAGE_VERSION)"*) printf 'OK: dev package depends on the exact matching runtime\n' ;;
	*) die "dev package does not depend on the exact matching runtime" ;;
esac

note "vinyl-cache: upstream purity, no benchmark-scaffolding vmod_tag"
# Mirrors the assertion stage-vinyl.sh gained in 7b54802. It has to be
# repeated here for the same reason the ABI assertions above are: the CI lane
# builds with pbuilder and never runs stage-vinyl.sh, so without this the
# clean-room lane would be the one lane that cannot catch a Vinyl re-pin
# regressing to a tree carrying an in-tree vmod_tag.
tag_files=$(
	for f in "$runtime_deb" "$dev_deb"; do
		dpkg-deb -c "$f" | awk '{print $NF}'
	done | { grep -E 'vmod_tag|libvmod_tag' || true; }
)
if [ -n "$tag_files" ]; then
	printf '%s\n' "$tag_files" >&2
	die "packaged Vinyl contains vmod_tag artifacts"
fi
printf 'OK: no vmod_tag file in the runtime or dev package\n'

note "vinyl-cache runtime: hardening inspection (production profile)"
mkdir -p /tmp/hx-vinyl
dpkg-deb -x "$runtime_deb" /tmp/hx-vinyl
# `nolog`, deliberately, and it is the reason the two halves of this script read
# differently. In a VMOD row the engine was DOWNLOADED as a verified artifact
# and this row has no build log for it at all; in an engine row there is one,
# but `libtool: compile:` is a selector for libtool-built objects and vinyld is
# a program, so asserting flags from it would be asserting them about the
# convenience libraries and calling that a statement about the daemon. Until
# that selector question is decided the engine keeps the canary and fortify
# SYMBOL checks as hard failures, which is exactly what it had before Wave 0 --
# a weaker check is better than a demoted one with nothing put in its place.
pc_verify_build /tmp/hx-vinyl/usr/sbin/vinyld vinyld \
	nolog "the engine is a verified artifact in a VMOD row, and its own row has no libtool compile lines for vinyld" ||
	die "hardening inspection failed for vinyld"
fi

if [ "$scope" = engine ]; then
	printf '\nOK: all engine ABI and hardening assertions passed against the pbuilder-produced packages\n'
	exit 0
fi

###############################################################################
note "libvmod-cachetag: ABI Depends and content (mirrors stage-cachetag.sh assertions)"
###############################################################################

cachetag_deb=$(ls "$out_dir"/libvmod-cachetag_"${CACHETAG_DEBIAN_VERSION}"_*.deb)
depends=$(dpkg-deb -f "$cachetag_deb" Depends)
printf 'Depends: %s\n' "$depends"
case "$depends" in
	*"vinyld-abi-$VINYL_STRICT_ABI"*) printf 'OK: depends on vinyld-abi-%s\n' "$VINYL_STRICT_ABI" ;;
	*) die "missing exact strict-ABI dependency" ;;
esac
case "$depends" in
	*"vinyld-vrt"*) printf 'OK: depends on vinyld-vrt\n' ;;
	*) die "missing vinyld-vrt dependency" ;;
esac

vmoddir=$(sed -n 3p "$repo_dir/dist/debian-13/work/target.txt")
[ -n "$vmoddir" ] || die "cannot read the target VMOD directory from dist/debian-13/work/target.txt"

note "libvmod-cachetag: payload is exactly what the recipe declares"
# STEP 7 WAVE 0, asymmetry settlement (b). The generated-recipe lane has had an
# explicit payload allowlist since Wave A2 and this one had only "the .so is
# present, and no .la or .a" -- so anything else `make install` produced would
# have shipped unnoticed. This is the same pc_assert_deb_payload verify-deb.sh
# calls, which is why it already knows to allow the recipe's own
# /usr/share/lintian/overrides/<binary> file by exact name (B3) and to have no
# build-id rule (debhelper puts those in the separate -dbgsym package).
#
# Byte-neutral by construction: it reads a built package and asserts. Measured
# against the green baseline 30437775658's package before being written -- the
# payload is the .so, the man page, five documentation files and the override
# file, and nothing else.
pc_assert_deb_payload "$cachetag_deb" libvmod-cachetag "$vmoddir" \
	libvmod_cachetag.so man3/vmod_cachetag.3 ||
	die "the libvmod-cachetag payload is not what the recipe declares; see above"

note "libvmod-cachetag: hardening inspection"
# STEP 7 WAVE 0, asymmetry settlement (c). `log`, not `nolog`: the two
# distribution flags are asserted from the pbuilder build log, and the canary
# and fortify SYMBOL checks drop to corroborating because their absence is a
# fact about the source and not about the build (B5). Before Wave 0 this lane
# passed the symbol checks only because cachetag's source happens to have
# canary-worthy buffers and fortifiable libc calls, which is the accident B5
# ruled out as evidence. Measured on the baseline: seven `libtool: compile:`
# lines on both channels, every one carrying both flags.
mkdir -p /tmp/hx-cachetag
dpkg-deb -x "$cachetag_deb" /tmp/hx-cachetag
pc_verify_build "/tmp/hx-cachetag$vmoddir/libvmod_cachetag.so" libvmod_cachetag.so \
	log "$log_dir/pbuilder-libvmod-cachetag.log" ||
	die "hardening inspection failed for libvmod_cachetag.so"

printf '\nOK: all payload, ABI and hardening assertions passed against the pbuilder-produced packages\n'
