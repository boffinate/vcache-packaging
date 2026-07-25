#!/bin/bash
#
# Build libvmod-cachetag with sbuild against the vinyl-cache-dev package
# sbuild-vinyl.sh just built, replacing
# recipes/debian-13/container/stage-cachetag.sh's `dpkg-buildpackage` call.
# See DESIGN.md section 4, point 4.
#
# libvmod-cachetag/packaging/debian/control declares
#   Build-Depends: ... vinyl-cache-dev (= @VINYL_PACKAGE_VERSION@)
# which is not on any Debian mirror, so sbuild's chroot-apt resolution alone
# cannot satisfy it. sbuild's --extra-package installs a given local .deb
# into the chroot before Build-Depends resolution runs -- the documented
# mechanism for "build against a package I just built".
#
# Must run as root, same as make-chroot.sh and sbuild-vinyl.sh.
#
# DRAFT, unexecuted -- see ../../../DESIGN.md section 4.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

repo_dir=$(CDPATH= cd -- "$here/../../.." && pwd)
out_dir=$repo_dir/dist/debian-13
work_dir=$out_dir/work
src_dir=$work_dir/build/libvmod-cachetag-$CACHETAG_VERSION

[ -d "$src_dir/debian" ] ||
	die "$src_dir has no debian/ tree; run recipes/debian-13/build.sh source first"
[ -d "$CHROOT_DIR" ] || die "no sbuild chroot at $CHROOT_DIR; run make-chroot.sh first"

vinyl_deb=$(ls "$out_dir"/vinyl-cache_"${VINYL_PACKAGE_VERSION}"_*.deb 2>/dev/null || true)
vinyl_dev_deb=$(ls "$out_dir"/vinyl-cache-dev_"${VINYL_PACKAGE_VERSION}"_*.deb 2>/dev/null || true)
[ -n "$vinyl_deb" ] || die "vinyl-cache_${VINYL_PACKAGE_VERSION}_*.deb not found in $out_dir; run sbuild-vinyl.sh first"
[ -n "$vinyl_dev_deb" ] || die "vinyl-cache-dev_${VINYL_PACKAGE_VERSION}_*.deb not found in $out_dir; run sbuild-vinyl.sh first"

note "dpkg-buildpackage -S: libvmod-cachetag source package"
(
	cd "$src_dir"
	export SOURCE_DATE_EPOCH=$CACHETAG_SOURCE_DATE_EPOCH
	dpkg-buildpackage -S -us -uc -d
)

dsc="$work_dir/build/libvmod-cachetag_${CACHETAG_DEBIAN_VERSION}_source.changes"
[ -f "$dsc" ] || die "expected $dsc after dpkg-buildpackage -S"

note "sbuild: libvmod-cachetag, with the just-built vinyl-cache-dev as --extra-package"
sbuild_out=$work_dir/sbuild-out/cachetag
mkdir -p "$sbuild_out"
(
	cd "$sbuild_out"
	SOURCE_DATE_EPOCH=$CACHETAG_SOURCE_DATE_EPOCH \
	sbuild \
		--arch=amd64 \
		--dist="$DEBIAN_DISTRIBUTION" \
		--chroot-mode=unshare \
		--chroot="$CHROOT_DIR" \
		--extra-package="$vinyl_deb" \
		--extra-package="$vinyl_dev_deb" \
		--no-run-lintian \
		--no-source \
		-us -uc \
		"$dsc"
)

note "copying sbuild output into $out_dir"
cp -v "$sbuild_out"/*.deb "$sbuild_out"/*.buildinfo "$sbuild_out"/*.changes "$out_dir/" 2>/dev/null || true
cp -v "$work_dir/build"/libvmod-cachetag_*.dsc \
	"$work_dir/build"/libvmod-cachetag_*.orig.tar.gz \
	"$work_dir/build"/libvmod-cachetag_*.debian.tar.* \
	"$out_dir/" 2>/dev/null || true

ls -la "$out_dir"
printf 'OK: sbuild produced the libvmod-cachetag package\n'
