# Shared Debian build-driver core. Sourced inside a build container, never
# executed, and never on the host.
#
# ONE implementation of the pbuilder clean-room, used by both recipe
# strategies:
#
#   scripts/ci/debian13/container-pbuilder.sh    Vinyl and cachetag
#   scripts/ci/vmod/container/build-deb.sh       every generated recipe
#
# Both mount the repository checkout read-only at /repo, so both reach this
# file at /repo/scripts/ci/lib/pbuilder.sh.
#
# WHY PBUILDER AND NOT SBUILD
#
# The packaging plan asks for "sbuild or pbuilder for clean builds". sbuild was
# tried first and each of its three ways into a chroot turned out to be
# unavailable here:
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
#
# WHY IT IS SHARED. Step 6 kept the two drivers apart so that cachetag's
# package bytes provably could not move while a second VMOD was being brought
# up; the Wave A2 note recorded the merge as the right thing to do afterwards,
# in a change whose only purpose is the merge. This is that change. The measured
# cost of the split was three defects -- B3, B6 and B9 -- each a lesson one
# lane's script had and the other did not.

# shellcheck shell=bash

###############################################################################
# Toolchain
###############################################################################

# pbuilder_install_toolchain
#
# pbuilder   the builder
# debhelper  `dpkg-buildpackage -S` runs debian/rules clean, and every recipe
#            this project builds is dh-based
# dpkg-dev   dpkg-scanpackages, for the local repository below
# fakeroot   the default root command
# procps     pbuilder-buildpackage-funcs calls sysctl; without it the build
#            works but every run logs "sysctl: command not found"
pbuilder_install_toolchain() {
	export DEBIAN_FRONTEND=noninteractive
	apt-get update -qq
	apt-get install -y --no-install-recommends \
		pbuilder debhelper dpkg-dev fakeroot procps
	pbuilder --version 2>/dev/null | head -1 ||
		dpkg-query -W -f='pbuilder ${Version}\n' pbuilder
}

###############################################################################
# Configuration
###############################################################################

# pbuilder_configure
#
# Two settings, both learned by failing.
#
# pbuilder resolves Build-Depends with aptitude by default, and a buildd chroot
# has no aptitude: the first run got as far as unpacking the dummy dependency
# package and then failed with "env: 'aptitude': No such file or directory".
# apt is the resolver a minimal buildroot actually has, and using it keeps the
# resolution the same one dpkg-buildpackage would do.
#
# pbuilder does not refresh apt inside the chroot before resolving
# Build-Depends, and mmdebstrap ships the chroot with its package lists
# cleaned, so the first apt-resolver run reported every single build dependency
# as "not installable" from empty lists. A D hook runs inside the chroot after
# the apt lines are installed and before dependencies are resolved, which is
# also exactly what a VMOD build needs so apt can see the local repository
# added with --othermirror.
pbuilder_configure() {
	cat >/etc/pbuilderrc <<'PBUILDERRC'
PBUILDERSATISFYDEPENDSCMD=/usr/lib/pbuilder/pbuilder-satisfydepends-apt
PBUILDERRC
	cat /etc/pbuilderrc

	mkdir -p /pbuilder-hooks
	cat >/pbuilder-hooks/D05update <<'HOOK'
#!/bin/sh
set -e
apt-get update
HOOK
	chmod 0755 /pbuilder-hooks/D05update
}

# pbuilder_base_tgz SRC DEST
#
# pbuilder's --basetgz is a gzipped tarball; make-chroot.sh writes a plain tar
# because that is what it can assert against and record a package list from.
pbuilder_base_tgz() {
	gzip -1 -c "$1" >"$2"
	ls -la "$2"
}

###############################################################################
# The local repository the exact-version engine dependency resolves from
###############################################################################

# pbuilder_publish_localrepo DIR FILE...
#
# Every VMOD this project packages Build-Depends on vinyl-cache-dev at an exact
# version, which is on no mirror. This is the same shape as the EL9 lane's
# createrepo_c step: publish the cohort's engine packages as a repository the
# buildroot can resolve from, rather than trying to keep an installed package
# alive across a chroot that is deliberately destroyed between builds.
#
# The caller passes the files. pbuilder cannot tell whether this run built them
# or downloaded them from the verified engine artifact, which is the point of
# the engine split.
pbuilder_publish_localrepo() {
	_pb_repo=$1
	shift
	rm -rf "$_pb_repo"
	mkdir -p "$_pb_repo"
	cp -v "$@" "$_pb_repo/"
	(cd "$_pb_repo" && dpkg-scanpackages -m . /dev/null >Packages && gzip -9c Packages >Packages.gz)
	ls -1 "$_pb_repo"
}

###############################################################################
# One package
###############################################################################

# pbuilder_build_one NAME SRCDIR DSC EPOCH RESULTDIR LOGFILE [extra pbuilder args...]
#
# Source package first, then a fresh chroot from the base tarball, built and
# destroyed by pbuilder.
#
# --override-config points the chroot's apt at the pinned snapshot (and, where
# the caller passes --othermirror, at the local repository above). The chroot
# already carries Acquire::Check-Valid-Until "false" from mmdebstrap's
# customize-hook, which it needs because a snapshot's Release file is older
# than it claims to be valid for.
#
# LOGFILE is not optional and the build is tee'd into it as it runs rather than
# copied afterwards, so a FAILING build still leaves its log behind -- the same
# lesson the EL9 lane's EXIT trap records. It is the only honest source for the
# hardening flag assertion (see package-checks.sh: -fstack-protector-strong and
# -D_FORTIFY_SOURCE=2 are properties of the compile line, not of the linked
# object) and it lands in the row's uploaded artifact for free, because every
# lane publishes its logs/ directory.
#
# The source-package artefacts are copied from the .dsc's own directory: both
# lanes put the .dsc, the .orig.tar.gz and the .debian.tar.* in one place, so
# there is nothing for a caller to state twice.
pbuilder_build_one() {
	_pb_name=$1
	_pb_srcdir=$2
	_pb_dsc=$3
	_pb_epoch=$4
	_pb_result=$5
	_pb_log=$6
	shift 6

	[ -d "$_pb_srcdir/debian" ] ||
		{ printf 'E: %s has no debian/ tree\n' "$_pb_srcdir" >&2; return 1; }

	mkdir -p "$_pb_result" "$(dirname -- "$_pb_log")"

	printf '\n===== dpkg-buildpackage -S: %s =====\n' "$_pb_name"
	(cd "$_pb_srcdir" && SOURCE_DATE_EPOCH=$_pb_epoch dpkg-buildpackage -S -us -uc -d)
	[ -f "$_pb_dsc" ] ||
		{ printf 'E: expected %s after dpkg-buildpackage -S\n' "$_pb_dsc" >&2; return 1; }

	printf '\n===== pbuilder build: %s =====\n' "$_pb_name"
	SOURCE_DATE_EPOCH=$_pb_epoch pbuilder build \
		--basetgz "$PBUILDER_BASE_TGZ" \
		--buildresult "$_pb_result" \
		--override-config \
		--distribution "$DEBIAN_DISTRIBUTION" \
		--components main \
		--mirror "$DEBIAN_SNAPSHOT_URI" \
		--architecture amd64 \
		--hookdir /pbuilder-hooks \
		--no-auto-cross \
		"$@" \
		"$_pb_dsc" 2>&1 | tee "$_pb_log"

	printf '\n===== source package artefacts for %s =====\n' "$_pb_name"
	_pb_srcart=$(dirname -- "$_pb_dsc")
	cp -v "$_pb_srcart/${_pb_name}"_*.dsc \
		"$_pb_srcart/${_pb_name}"_*.orig.tar.gz \
		"$_pb_srcart/${_pb_name}"_*.debian.tar.* \
		"$_pb_result/" 2>/dev/null || true
}
