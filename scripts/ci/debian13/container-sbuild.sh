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
# WHY NOT --chroot-mode=unshare
#
# Containerising removed the userland variable but not the failure: inside
# this image, on a GitHub runner, sbuild still reported
#
#   Can't exec "dpkg": Permission denied at /usr/libexec/sbuild-usernsexec:613
#
# while the identical image, tarball, sbuild version and user setup complete
# arch detection when the same container runs on another host. What is left
# is the runner's own kernel and LSM policy, which this repository cannot
# pin and should not depend on.
#
# So the chroot is entered with schroot rather than a user namespace. (sudo
# mode, the other namespace-free option, was removed in sbuild 0.89: "E:
# CHROOT_MODE=sudo (or unset) is unsupported".) The
# clean-room property is unchanged and comes from where it always came from:
# a fresh chroot unpacked from the digest-pinned tarball for each build,
# resolving Build-Depends from debian/control inside it. The user namespace
# was only ever the mechanism for entering that chroot without privilege, and
# inside a container that we already start privileged it buys nothing.

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
# sbuild     the builder (trixie ships 0.89.3)
# schroot    how sbuild enters the chroot in its default chroot mode
# iproute2   sbuild brings up loopback in the build environment
# debhelper  `dpkg-buildpackage -S` runs debian/rules clean, and both recipes
#            are dh-based
apt-get install -y --no-install-recommends \
	sbuild schroot iproute2 debhelper dpkg-dev fakeroot
sbuild --version | head -1

###############################################################################
note "build user and chroot"
###############################################################################
#
# sbuild refuses to run as root, so the build still runs as an ordinary user,
# and that user gets the uid/gid owning the bind-mounted /out so the packages
# land on the host owned by the account that started the job.
#
build_uid=$(stat -c %u "$out")
build_gid=$(stat -c %g "$out")
[ "$build_uid" -ne 0 ] || die "/out is owned by root; sbuild cannot run as root"
getent group "$build_gid" >/dev/null || groupadd -g "$build_gid" sbuilder
useradd -o -u "$build_uid" -g "$build_gid" -m -d /home/sbuilder sbuilder
# schroot serves chroots only to members of the sbuild group.
sbuild-adduser sbuilder >/dev/null 2>&1 || usermod -aG sbuild sbuilder
id sbuilder
# The source trees were assembled by an earlier container running as root.
chown -R "$build_uid:$build_gid" "$work"

as_builder() { runuser -u sbuilder -- "$@"; }

note "unpacking the pinned chroot"
chroot_name=$DEBIAN_DISTRIBUTION-amd64-sbuild
chroot_dir=/srv/chroot/$DEBIAN_DISTRIBUTION-amd64
rm -rf "$chroot_dir"
mkdir -p "$chroot_dir"
tar -C "$chroot_dir" -xf "$chroot_tarball"

# mmdebstrap produces a real buildd chroot, so this is all it needs: a
# resolver (not part of any chroot's own contents) and the apt option the
# snapshot's backdated Release file requires, the same one mmdebstrap itself
# was given. Without the latter, apt inside the chroot cannot refresh its
# lists and every Build-Depends looks uninstallable.
cp /etc/resolv.conf "$chroot_dir/etc/resolv.conf"
printf 'Acquire::Check-Valid-Until "false";\n' \
	> "$chroot_dir/etc/apt/apt.conf.d/10snapshot"

cat > "/etc/schroot/chroot.d/$chroot_name" <<SCHROOT
[$chroot_name]
description=Debian $DEBIAN_DISTRIBUTION buildd chroot from snapshot $DEBIAN_SNAPSHOT
type=directory
directory=$chroot_dir
profile=sbuild
users=sbuilder
root-users=sbuilder
SCHROOT

# sbuild runs its chroot-locking command with the invoking directory as the
# working directory, and refuses the session when that directory does not
# exist inside the chroot ("Failed to change to directory ... The directory
# does not exist inside the chroot"). /build exists in every sbuild chroot
# and is where the sbuild schroot profile mounts the build tree, so the
# builds are driven from there.
build_root=/build
mkdir -p "$build_root"
chown "$build_uid:$build_gid" "$build_root"

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

	note "sbuild: $_name (schroot $chroot_name -> $chroot_dir)"
	_sbuild_out=$build_root/$_name
	mkdir -p "$_sbuild_out"
	chown "$build_uid:$build_gid" "$_sbuild_out"
	as_builder env -C "$_sbuild_out" SOURCE_DATE_EPOCH="$_epoch" \
		sbuild \
			--arch=amd64 \
			--dist="$DEBIAN_DISTRIBUTION" \
			--chroot-mode=schroot \
			--chroot="$chroot_name" \
			--bd-uninstallable-explainer=none \
			--verbose \
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
