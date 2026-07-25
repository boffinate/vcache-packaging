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
# Must run as root (workflow step: `sudo bash ...`), same as make-chroot.sh.
#
# DRAFT, unexecuted -- see ../../../DESIGN.md section 4.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

repo_dir=$(CDPATH= cd -- "$here/../../.." && pwd)
out_dir=$repo_dir/dist/debian-13
work_dir=$out_dir/work
src_dir=$work_dir/build/vinyl-cache-$VINYL_UPSTREAM_VERSION

[ -d "$src_dir/debian" ] ||
	die "$src_dir has no debian/ tree; run recipes/debian-13/build.sh source first"
[ -d "$CHROOT_DIR" ] || die "no sbuild chroot at $CHROOT_DIR; run make-chroot.sh first"

note "dpkg-buildpackage -S: Vinyl Cache source package"
(
	cd "$src_dir"
	export SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH
	dpkg-buildpackage -S -us -uc -d
)

dsc="$work_dir/build/vinyl-cache_${VINYL_PACKAGE_VERSION}_source.changes"
[ -f "$dsc" ] || die "expected $dsc after dpkg-buildpackage -S"

note "sbuild: vinyl-cache (unshare chroot $CHROOT_DIR)"
sbuild_out=$work_dir/sbuild-out/vinyl
mkdir -p "$sbuild_out"
(
	cd "$sbuild_out"
	SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH \
	sbuild \
		--arch=amd64 \
		--dist="$DEBIAN_DISTRIBUTION" \
		--chroot-mode=unshare \
		--chroot="$CHROOT_DIR" \
		--no-run-lintian \
		--no-source \
		-us -uc \
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
