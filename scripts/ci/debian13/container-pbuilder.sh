#!/bin/bash
#
# Builds both Debian packages with pbuilder, inside the pinned debian:trixie
# container that debian-lane.sh starts. Mirrors scripts/ci/el9/container-mock.sh:
# the host drives, the pinned image builds.
#
# Mount contract (set by debian-lane.sh):
#   /repo   the vcache-packaging checkout, read-only (for pins.env)
#   /out    dist/debian-13, writable; /out/work holds the assembled source
#           trees and /out/work/chroot the mmdebstrap base tarball
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
# construction. `build_one` is one function called with the same arguments in
# every scope; the local repository the cachetag build resolves
# vinyl-cache-dev from is assembled by the same `cp` from the same /out
# directory whether this run just built those .debs or downloaded them; and the
# buildroot is the same pinned mmdebstrap tarball, destroyed and recreated per
# package in all three scopes anyway.
#
# WHY PBUILDER AND NOT SBUILD
#
# The plan asks for "sbuild or pbuilder for clean builds". sbuild was tried
# first and each of its three ways into a chroot turned out to be unavailable
# here:
#
#   unshare  the runner's kernel refuses to exec inside the namespace-mapped
#            chroot ("Can't exec dpkg: Permission denied"), and that survived
#            every input this repository can pin -- sbuild version, tarball
#            shape and ownership, session directory, and both of Ubuntu's
#            AppArmor userns sysctls.
#   sudo     removed in sbuild 0.89: "CHROOT_MODE=sudo (or unset) is
#            unsupported".
#   schroot  worked, but schroot is retired upstream, and its value -- named
#            reusable sessions, locking, profile-driven bind-mounts -- is all
#            persistent-buildd machinery that a container which exists for one
#            build has no use for.
#
# pbuilder is the plan's other named option and wants none of that: unpack a
# base tarball, build, destroy, as plain root. What the clean-room requirement
# actually asks for is preserved exactly -- a minimal Essential-only buildroot
# from a pinned snapshot, build dependencies resolved only from debian/control,
# the build driven from the .dsc, and a fresh root per package.

set -euo pipefail

. /repo/recipes/debian-13/pins.env

out=/out
work=$out/work
base_tar=$work/chroot/$DEBIAN_DISTRIBUTION-amd64.tar
base_tgz=/base.tgz
localrepo=/localrepo
scope=${PBUILDER_SCOPE:-all}

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

case $scope in
all | engine | vmod) note "pbuilder scope: $scope" ;;
*) die "unknown PBUILDER_SCOPE '$scope' (all|engine|vmod)" ;;
esac

[ -f "$base_tar" ] || die "no base tarball at $base_tar; run make-chroot.sh first"

###############################################################################
note "build toolchain"
###############################################################################

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# pbuilder   the builder
# debhelper  `dpkg-buildpackage -S` runs debian/rules clean, and both recipes
#            are dh-based
# dpkg-dev   dpkg-scanpackages, for the local repository below
# procps     pbuilder-buildpackage-funcs calls sysctl; without it the build
#            works but every run logs "sysctl: command not found"
apt-get install -y --no-install-recommends \
	pbuilder debhelper dpkg-dev fakeroot procps
pbuilder --version 2>/dev/null | head -1 || dpkg-query -W -f='pbuilder ${Version}\n' pbuilder

#
# pbuilder resolves Build-Depends with aptitude by default, and a buildd
# chroot has no aptitude: the first run got as far as unpacking the dummy
# dependency package and then failed with "env: 'aptitude': No such file or
# directory". apt is the resolver a minimal buildroot actually has, and using
# it keeps the resolution the same one dpkg-buildpackage would do.
#
note "pbuilder configuration"
cat > /etc/pbuilderrc <<'PBUILDERRC'
PBUILDERSATISFYDEPENDSCMD=/usr/lib/pbuilder/pbuilder-satisfydepends-apt
PBUILDERRC
cat /etc/pbuilderrc

#
# pbuilder does not refresh apt inside the chroot before resolving
# Build-Depends, and mmdebstrap ships the chroot with its package lists
# cleaned, so the first apt-resolver run reported every single build
# dependency as "not installable" from empty lists. A D hook runs inside the
# chroot after the apt lines are installed and before dependencies are
# resolved, which is also exactly what the cachetag build needs so apt can
# see the local repository added with --othermirror.
#
note "pbuilder hooks"
mkdir -p /pbuilder-hooks
cat > /pbuilder-hooks/D05update <<'HOOK'
#!/bin/sh
set -e
apt-get update
HOOK
chmod 0755 /pbuilder-hooks/D05update

note "compressing the mmdebstrap base tarball"
# pbuilder's --basetgz is a gzipped tarball; make-chroot.sh writes a plain tar
# because that is what it can assert against and record a package list from.
gzip -1 -c "$base_tar" > "$base_tgz"
ls -la "$base_tgz"

###############################################################################
# One package: source package first, then a fresh chroot from the base
# tarball, built and destroyed by pbuilder.
#
# --override-config points the chroot's apt at the pinned snapshot (and, for
# cachetag, at the local repository below). The chroot already carries
# Acquire::Check-Valid-Until "false" from mmdebstrap's customize-hook, which
# it needs because a snapshot's Release file is older than it claims to be
# valid for.
###############################################################################

build_one() {
	_name=$1; _srcdir=$2; _dsc=$3; _epoch=$4; shift 4

	[ -d "$_srcdir/debian" ] ||
		die "$_srcdir has no debian/ tree; run recipes/debian-13/build.sh source first"

	note "dpkg-buildpackage -S: $_name"
	( cd "$_srcdir" && SOURCE_DATE_EPOCH=$_epoch dpkg-buildpackage -S -us -uc -d )
	[ -f "$_dsc" ] || die "expected $_dsc after dpkg-buildpackage -S"

	note "pbuilder build: $_name"
	SOURCE_DATE_EPOCH=$_epoch pbuilder build \
		--basetgz "$base_tgz" \
		--buildresult "$out" \
		--override-config \
		--distribution "$DEBIAN_DISTRIBUTION" \
		--components main \
		--mirror "$DEBIAN_SNAPSHOT_URI" \
		--architecture amd64 \
		--hookdir /pbuilder-hooks \
		--no-auto-cross \
		"$@" \
		"$_dsc"

	note "source package artefacts for $_name"
	cp -v "$work/build/${_name}"_*.dsc \
		"$work/build/${_name}"_*.orig.tar.gz \
		"$work/build/${_name}"_*.debian.tar.* \
		"$out/" 2>/dev/null || true
}

###############################################################################
if [ "$scope" != vmod ]; then
	build_one vinyl-cache \
		"$work/build/vinyl-cache-$VINYL_UPSTREAM_VERSION" \
		"$work/build/vinyl-cache_$VINYL_PACKAGE_VERSION.dsc" \
		"$VINYL_SOURCE_DATE_EPOCH"
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
	# libvmod-cachetag Build-Depends on vinyl-cache-dev (= exact version),
	# which is on no mirror. This is the same shape as the EL9 lane's
	# createrepo_c fix: publish the cohort's engine packages as a repository
	# the buildroot can resolve from, rather than trying to keep an installed
	# package alive across a chroot that is deliberately destroyed between
	# builds.
	#
	# The `cp` glob is unchanged, and so is the set of files it matches: in
	# `all` scope those .debs were produced by the build above, in `vmod`
	# scope they were downloaded from the engine artifact into the same
	# directory. pbuilder cannot tell the difference, which is the point.
	#
	rm -rf "$localrepo"
	mkdir -p "$localrepo"
	cp -v "$out"/vinyl-cache*_"${VINYL_PACKAGE_VERSION}"_*.deb "$localrepo/"
	( cd "$localrepo" && dpkg-scanpackages -m . /dev/null > Packages && gzip -9c Packages > Packages.gz )
	ls -1 "$localrepo"

	###########################################################################
	build_one libvmod-cachetag \
		"$work/build/libvmod-cachetag-$CACHETAG_VERSION" \
		"$work/build/libvmod-cachetag_$CACHETAG_DEBIAN_VERSION.dsc" \
		"$CACHETAG_SOURCE_DATE_EPOCH" \
		--bindmounts "$localrepo" \
		--othermirror "deb [trusted=yes] file://$localrepo ./"
	###########################################################################
fi

note "Debian 13 lane complete (scope: $scope)"
ls -la "$out"
