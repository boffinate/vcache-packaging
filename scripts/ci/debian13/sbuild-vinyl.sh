#!/bin/bash
#
# Build the Vinyl Cache 9 Debian binary packages with sbuild, replacing
# recipes/debian-13/container/stage-vinyl.sh's `dpkg-buildpackage` call (which
# runs in a shared, cumulative container rather than a minimal per-build
# chroot -- see DESIGN.md sections 1, 2 and 4).
#
# Everything BEFORE this script (recipes/debian-13/build.sh source, which
# assembles work/build/vinyl-cache-<uv>/ with its debian/ tree already
# substituted and in place) and everything AFTER it (assert-packages.sh,
# then the unmodified build.sh lint/smoke/sums stages) is reused unchanged.
#
# Runs as the ordinary build user, not root: sbuild's unshare backend needs
# the invoking user to have an /etc/subuid range. See make-chroot.sh.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

repo_dir=$(CDPATH= cd -- "$here/../../.." && pwd)
out_dir=$repo_dir/dist/debian-13
work_dir=$out_dir/work
src_dir=$work_dir/build/vinyl-cache-$VINYL_UPSTREAM_VERSION

[ "$(id -u)" -ne 0 ] || die "run this as the ordinary build user, not root (see make-chroot.sh)"
[ -d "$src_dir/debian" ] ||
	die "$src_dir has no debian/ tree; run recipes/debian-13/build.sh source first"
[ -f "$CHROOT_TARBALL" ] ||
	die "no sbuild chroot tarball at $CHROOT_TARBALL; run make-chroot.sh first"

note "dpkg-buildpackage -S: Vinyl Cache source package"
(
	cd "$src_dir"
	export SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH
	dpkg-buildpackage -S -us -uc -d
)

# sbuild builds from the .dsc. Handing it the *_source.changes that
# `dpkg-buildpackage -S` writes alongside it fails with "E: Failed to fetch
# source files" (measured 2026-07-25, sbuild 0.89.3+deb13u4).
dsc="$work_dir/build/vinyl-cache_${VINYL_PACKAGE_VERSION}.dsc"
[ -f "$dsc" ] || die "expected $dsc after dpkg-buildpackage -S"

note "sbuild: vinyl-cache (unshare chroot $CHROOT_TARBALL)"
sbuild_out=$work_dir/sbuild-out/vinyl
mkdir -p "$sbuild_out"
(
	cd "$sbuild_out"
	SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH \
	sbuild \
		--arch=amd64 \
		--dist="$DEBIAN_DISTRIBUTION" \
		--chroot-mode=unshare \
		--chroot="$CHROOT_TARBALL" \
		--no-run-lintian \
		--no-source \
		"$dsc"
)

note "copying sbuild output into $out_dir"
cp -v "$sbuild_out"/*.deb "$sbuild_out"/*.buildinfo "$sbuild_out"/*.changes "$out_dir/" 2>/dev/null || true
# The .dsc/.orig.tar.gz/.debian.tar.xz produced by dpkg-buildpackage -S sit
# next to the source tree, not in sbuild's own output directory.
cp -v "$work_dir/build"/vinyl-cache_*.dsc \
	"$work_dir/build"/vinyl-cache_*.orig.tar.gz \
	"$work_dir/build"/vinyl-cache_*.debian.tar.* \
	"$out_dir/" 2>/dev/null || true

ls -la "$out_dir"
printf 'OK: sbuild produced the Vinyl Cache 9 package set\n'
