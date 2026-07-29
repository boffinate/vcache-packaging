#!/bin/bash
#
# Builds both Debian packages with pbuilder, inside the pinned debian:trixie
# container that debian-lane.sh starts. Mirrors scripts/ci/el9/container-mock.sh:
# the host drives, the pinned image builds.
#
# Mount contract (set by debian-lane.sh):
#   /repo   the vcache-packaging checkout, read-only (pins.env and
#           scripts/ci/lib/pbuilder.sh)
#   /out    dist/debian-13, writable; /out/work holds the assembled source
#           trees and /out/work/chroot the mmdebstrap base tarball
#
# The pbuilder clean-room itself -- toolchain, apt resolver, D hook, base
# tarball, local repository and the per-package build -- is
# scripts/ci/lib/pbuilder.sh, shared with the generated-recipe lane's
# build-deb.sh since Step 7 Wave 0. This file is what is true of THIS lane:
# which packages exist, where their source trees are, and which pins name their
# versions. See that library's header for why pbuilder and not sbuild, and for
# the reasoning behind each setting it applies.
#
# PBUILDER_SCOPE selects which package this run builds:
#   all      Vinyl, then cachetag against it -- the local, whole-cohort form
#   engine   Vinyl only, for CI's shared engine package job
#   vmod     cachetag only, against Vinyl .debs that are already in /out
#
# The split is what Phase 2 of
# docs/20260728_0833_plan_vmod-matrix-failure-isolation.md asks for: the engine
# is built once per engine input and target, and a VMOD row consumes the
# resulting packages instead of rebuilding them. It is package-neutral by
# construction. `pbuilder_build_one` is one function called with the same
# arguments in every scope; the local repository the cachetag build resolves
# vinyl-cache-dev from is assembled by the same `cp` from the same /out
# directory whether this run just built those .debs or downloaded them; and the
# buildroot is the same pinned mmdebstrap tarball, destroyed and recreated per
# package in all three scopes anyway.

set -euo pipefail

. /repo/recipes/debian-13/pins.env
# shellcheck source=../lib/pbuilder.sh
. /repo/scripts/ci/lib/pbuilder.sh

out=/out
work=$out/work
logdir=$out/logs
base_tar=$work/chroot/$DEBIAN_DISTRIBUTION-amd64.tar
localrepo=/localrepo
scope=${PBUILDER_SCOPE:-all}

# pbuilder_build_one reads it, so every build in this container uses the one
# base tarball this script compressed.
PBUILDER_BASE_TGZ=/base.tgz

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

case $scope in
all | engine | vmod) note "pbuilder scope: $scope" ;;
*) die "unknown PBUILDER_SCOPE '$scope' (all|engine|vmod)" ;;
esac

[ -f "$base_tar" ] || die "no base tarball at $base_tar; run make-chroot.sh first"
mkdir -p "$logdir"

###############################################################################
note "build toolchain"
###############################################################################
pbuilder_install_toolchain

note "pbuilder configuration and hooks"
pbuilder_configure

note "compressing the mmdebstrap base tarball"
pbuilder_base_tgz "$base_tar" "$PBUILDER_BASE_TGZ"

###############################################################################
if [ "$scope" != vmod ]; then
	pbuilder_build_one vinyl-cache \
		"$work/build/vinyl-cache-$VINYL_UPSTREAM_VERSION" \
		"$work/build/vinyl-cache_$VINYL_PACKAGE_VERSION.dsc" \
		"$VINYL_SOURCE_DATE_EPOCH" \
		"$out" \
		"$logdir/pbuilder-vinyl-cache.log"
fi
###############################################################################

#
# In `vmod` scope this is not an assertion about what this run built but about
# what it was handed: the engine .debs must already be in /out, put there by
# the engine artifact this row downloaded and verified. Failing here rather
# than inside pbuilder keeps "the engine artifact was not delivered" separate
# from "the cachetag build failed".
#
vinyl_deb=$(ls "$out"/vinyl-cache_"${VINYL_PACKAGE_VERSION}"_*.deb 2>/dev/null || true)
vinyl_dev_deb=$(ls "$out"/vinyl-cache-dev_"${VINYL_PACKAGE_VERSION}"_*.deb 2>/dev/null || true)
[ -n "$vinyl_deb" ] || die "vinyl-cache_${VINYL_PACKAGE_VERSION}_*.deb not present in $out"
[ -n "$vinyl_dev_deb" ] || die "vinyl-cache-dev_${VINYL_PACKAGE_VERSION}_*.deb not present in $out"

if [ "$scope" != engine ]; then
	###########################################################################
	note "publishing the Vinyl packages as a local repository"
	###########################################################################
	#
	# The glob is unchanged, and so is the set of files it matches: in `all`
	# scope those .debs were produced by the build above, in `vmod` scope they
	# were downloaded from the engine artifact into the same directory.
	#
	# shellcheck disable=SC2086 # the glob is the set of engine packages
	pbuilder_publish_localrepo "$localrepo" \
		"$out"/vinyl-cache*_"${VINYL_PACKAGE_VERSION}"_*.deb

	###########################################################################
	pbuilder_build_one libvmod-cachetag \
		"$work/build/libvmod-cachetag-$CACHETAG_VERSION" \
		"$work/build/libvmod-cachetag_$CACHETAG_DEBIAN_VERSION.dsc" \
		"$CACHETAG_SOURCE_DATE_EPOCH" \
		"$out" \
		"$logdir/pbuilder-libvmod-cachetag.log" \
		--bindmounts "$localrepo" \
		--othermirror "deb [trusted=yes] file://$localrepo ./"
	###########################################################################
fi

note "Debian 13 lane complete (scope: $scope)"
ls -la "$out"
