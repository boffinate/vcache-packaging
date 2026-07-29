#!/bin/bash
#
# Builds one generated-recipe VMOD's Debian package with pbuilder, inside the
# pinned debian:trixie container that ../run.sh starts.
#
# Mount contract (set by ../run.sh):
#   /repo   the vcache-packaging checkout, read-only (pins.env and
#           scripts/ci/lib/pbuilder.sh)
#   /lane   the per-row work directory: build/ holds the source tree and the
#           orig tarball, engine/ the verified engine .debs, out/ the results
#
# The pbuilder clean-room is scripts/ci/lib/pbuilder.sh, the SAME file
# scripts/ci/debian13/container-pbuilder.sh sources. Step 6 kept two copies of
# it so cachetag's package bytes provably could not move while the second VMOD
# was brought up; Step 7 Wave 0 merged them, which is what the Wave A2 note said
# should happen once the proof existed. What is left here is what is true of
# THIS lane: one package, whose names come from the generator rather than from a
# pin file, and whose engine .debs arrive in the lane instead of /out.

set -euo pipefail

. /repo/recipes/debian-13/pins.env
# shellcheck source=../../lib/pbuilder.sh
. /repo/scripts/ci/lib/pbuilder.sh

lane=/lane
work=$lane/build
out=$lane/out
logdir=$lane/logs
base_tar=$lane/chroot/$DEBIAN_DISTRIBUTION-amd64.tar
localrepo=/localrepo

PBUILDER_BASE_TGZ=/base.tgz

note() { printf '\n===== %s =====\n' "$*"; }
die() {
	printf 'E: %s\n' "$*" >&2
	exit 1
}

: "${VMOD_SOURCE_NAME:?}" "${VMOD_UPSTREAM_VERSION:?}" "${VMOD_DEBIAN_VERSION:?}"
: "${VMOD_SOURCE_DATE_EPOCH:?}"

[ -f "$base_tar" ] || die "no base tarball at $base_tar; run make-chroot.sh first"
mkdir -p "$out" "$logdir"

note "build toolchain"
pbuilder_install_toolchain

note "pbuilder configuration and hooks"
pbuilder_configure

note "compressing the mmdebstrap base tarball"
pbuilder_base_tgz "$base_tar" "$PBUILDER_BASE_TGZ"

note "publishing the verified engine packages as a local repository"
# shellcheck disable=SC2086 # the glob is the set of engine packages
pbuilder_publish_localrepo "$localrepo" "$lane"/engine/*.deb

srcdir=$work/$VMOD_SOURCE_NAME-$VMOD_UPSTREAM_VERSION
dsc=$work/${VMOD_SOURCE_NAME}_${VMOD_DEBIAN_VERSION}.dsc

# The log name is the one verify-deb.sh reads its hardening evidence from. It
# is fixed rather than derived from the package name, because the verify stage
# runs in a fresh container that mounts only the lane and knows nothing about
# what the package is called.
pbuilder_build_one "$VMOD_SOURCE_NAME" \
	"$srcdir" \
	"$dsc" \
	"$VMOD_SOURCE_DATE_EPOCH" \
	"$out" \
	"$logdir/pbuilder-build.log" \
	--bindmounts "$localrepo" \
	--othermirror "deb [trusted=yes] file://$localrepo ./"

note "Debian VMOD lane complete"
ls -la "$out"
