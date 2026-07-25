#!/bin/bash
#
# Builds both Debian packages with sbuild, inside the pinned debian:trixie
# container that sbuild-lane.sh starts. Mirrors scripts/ci/el9/container-mock.sh:
# the host drives, the pinned image builds.
#
# Mount contract (set by sbuild-lane.sh):
#   /repo   the vcache-packaging checkout, read-only (for pins.env)
#   /out    dist/debian-13, writable; /out/work holds the assembled source
#           trees and /out/work/chroot the pinned chroot tarball
#
# WHY THIS RUNS IN A CONTAINER
#
# It used to run on the GitHub runner directly. sbuild's unshare backend
# unpacked the chroot there and then could not execute anything inside it:
#
#   I: Creating chroot session...
#   Can't exec "dpkg": Permission denied at /usr/libexec/sbuild-usernsexec:561
#   E: Can't determine architecture of chroot:
#
# That survived an sbuild upgrade (0.85 -> 0.88.3), three chroot tarball
# shapes, moving the session directory, and both of Ubuntu 24.04's AppArmor
# user-namespace sysctls; /tmp was measured and does permit exec. The
# discriminating fact was that the same tarball and the same invocation build
# a package to "Status: successful" in a debian:trixie container and fail
# exactly this way in an ubuntu:24.04 one. The runner's userland was an
# unpinned input to a lane whose entire purpose is a pinned buildroot, so it
# is no longer an input at all.
#
# sbuild still uses --chroot-mode=unshare, and still runs as an unprivileged
# user: the clean-room property comes from the chroot, not from the container.

set -euo pipefail

. /repo/recipes/debian-13/pins.env

out=/out
work=$out/work
chroot_tarball=$work/chroot/$DEBIAN_DISTRIBUTION-amd64.tar

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

[ -f "$chroot_tarball" ] || die "no chroot tarball at $chroot_tarball; run make-chroot.sh first"

###############################################################################
note "build toolchain"
###############################################################################

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# sbuild     the builder (trixie ships 0.89.3, the version this recipe is
#            known to work with)
# uidmap     newuidmap/newgidmap, used by sbuild-usernsexec
# iproute2   sbuild runs `ip link set lo up` in the build namespace
# debhelper  `dpkg-buildpackage -S` runs debian/rules clean, and both recipes
#            are dh-based
apt-get install -y --no-install-recommends \
	sbuild uidmap iproute2 debhelper dpkg-dev fakeroot
sbuild --version | head -1

###############################################################################
note "unprivileged build user"
###############################################################################
#
# sbuild's unshare backend resolves the invoking user's /etc/subuid range and
# refuses to be root, so the build runs as an ordinary user. That user is
# given the uid/gid that owns the bind-mounted /out, so it can write the
# assembled source trees and the packages land on the host owned by the
# account that started the job rather than by root.
#
build_uid=$(stat -c %u "$out")
build_gid=$(stat -c %g "$out")
[ "$build_uid" -ne 0 ] || die "/out is owned by root; sbuild cannot run as root"
getent group "$build_gid" >/dev/null || groupadd -g "$build_gid" sbuilder
useradd -o -u "$build_uid" -g "$build_gid" -m -d /home/sbuilder sbuilder
usermod --add-subuids 100000-165535 --add-subgids 100000-165535 sbuilder
grep '^sbuilder:' /etc/subuid /etc/subgid

# The source trees were assembled by an earlier container running as root.
chown -R "$build_uid:$build_gid" "$work"

as_builder() { runuser -u sbuilder -- "$@"; }

as_builder unshare --user --map-root-user true ||
	die "the build user cannot create a user namespace inside this container; sbuild --chroot-mode=unshare cannot work"
printf 'OK: user namespaces available to the build user\n'

###############################################################################
# One package: source package on the host side of the chroot, then sbuild.
#
# sbuild builds from the .dsc; handed the *_source.changes that
# dpkg-buildpackage writes beside it, it fails with "E: Failed to fetch source
# files". -us -uc are dpkg-buildpackage options, which sbuild's own parser
# rejects -- it passes them itself.
###############################################################################

build_one() {
	_name=$1; _srcdir=$2; _dsc=$3; _epoch=$4; shift 4

	[ -d "$_srcdir/debian" ] ||
		die "$_srcdir has no debian/ tree; run recipes/debian-13/build.sh source first"

	note "dpkg-buildpackage -S: $_name"
	as_builder env -C "$_srcdir" SOURCE_DATE_EPOCH="$_epoch" \
		dpkg-buildpackage -S -us -uc -d

	[ -f "$_dsc" ] || die "expected $_dsc after dpkg-buildpackage -S"

	note "sbuild: $_name (unshare chroot $chroot_tarball)"
	_sbuild_out=$work/sbuild-out/$_name
	mkdir -p "$_sbuild_out"
	chown "$build_uid:$build_gid" "$_sbuild_out"
	as_builder env -C "$_sbuild_out" SOURCE_DATE_EPOCH="$_epoch" \
		sbuild \
			--arch=amd64 \
			--dist="$DEBIAN_DISTRIBUTION" \
			--chroot-mode=unshare \
			--chroot="$chroot_tarball" \
			--no-run-lintian \
			--no-source \
			"$@" \
			"$_dsc"

	note "copying $_name output into $out"
	cp -v "$_sbuild_out"/*.deb "$_sbuild_out"/*.buildinfo "$_sbuild_out"/*.changes \
		"$out/" 2>/dev/null || true
	# The .dsc/.orig.tar.gz/.debian.tar.* sit next to the source tree, not in
	# sbuild's output directory.
	cp -v "$work/build/${_name}"_*.dsc \
		"$work/build/${_name}"_*.orig.tar.gz \
		"$work/build/${_name}"_*.debian.tar.* \
		"$out/" 2>/dev/null || true
}

###############################################################################
build_one vinyl-cache \
	"$work/build/vinyl-cache-$VINYL_UPSTREAM_VERSION" \
	"$work/build/vinyl-cache_$VINYL_PACKAGE_VERSION.dsc" \
	"$VINYL_SOURCE_DATE_EPOCH"
###############################################################################

vinyl_deb=$(ls "$out"/vinyl-cache_"${VINYL_PACKAGE_VERSION}"_*.deb 2>/dev/null || true)
vinyl_dev_deb=$(ls "$out"/vinyl-cache-dev_"${VINYL_PACKAGE_VERSION}"_*.deb 2>/dev/null || true)
[ -n "$vinyl_deb" ] || die "vinyl-cache_${VINYL_PACKAGE_VERSION}_*.deb not produced"
[ -n "$vinyl_dev_deb" ] || die "vinyl-cache-dev_${VINYL_PACKAGE_VERSION}_*.deb not produced"

###############################################################################
# libvmod-cachetag Build-Depends on vinyl-cache-dev (= exact version), which
# is on no mirror. --extra-package installs a local .deb into the chroot
# before Build-Depends resolution -- the documented way to build against a
# package from the same run.
###############################################################################
build_one libvmod-cachetag \
	"$work/build/libvmod-cachetag-$CACHETAG_VERSION" \
	"$work/build/libvmod-cachetag_$CACHETAG_DEBIAN_VERSION.dsc" \
	"$CACHETAG_SOURCE_DATE_EPOCH" \
	--extra-package="$vinyl_deb" \
	--extra-package="$vinyl_dev_deb"

note "sbuild lane complete"
ls -la "$out"
