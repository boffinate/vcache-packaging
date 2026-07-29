#!/bin/bash
#
# Runs inside the privileged almalinux:9 container mock-build.sh starts.
# Builds vinyl-cache, then libvmod-cachetag, with Mock instead of the
# `rpmbuild --rebuild` calls in recipes/el9/container/build.sh's `stage_vinyl`
# and `stage_cachetag`. See DESIGN.md sections 2 and 5.
#
# The exact invocation this mirrors is documented in the spec file itself:
# recipes/el9/vinyl-cache.spec.in and libvmod-cachetag/packaging/rpm/
# libvmod-cachetag.spec both carry a header comment naming
#   mock -r alma+epel-9-x86_64 --buildsrpm --spec ... --sources .
#   mock -r alma+epel-9-x86_64 --rebuild ...src.rpm
# as "the intended build venue" and "a CI requirement".
#
# Mounts (set by mock-build.sh):
#   /recipes    recipes/el9, read-only
#   /ci         scripts/ci, read-only -- lib/mock.sh
#   /vinyl-src  the pinned Vinyl checkout (or an empty stub), read-only
#   /cachetag   the libvmod-cachetag checkout (or an empty stub), read-only
#   /out        dist/el9, writable
#
# The Mock clean-room -- toolchain, build user, derived configs, epoch macros,
# local repository and log capture -- is /ci/lib/mock.sh, shared with the
# generated-recipe lane's build-rpm.sh since Step 7 Wave 0. See that file's
# header for every lesson it carries and what each one cost to learn.
#
# Reuses UNCHANGED: the SOURCES staged by container/build.sh's `source` stage
# (mock-build.sh runs `deps source` before this script).
#
# Duplicates DELIBERATELY (see DESIGN.md section 2): the spec-substitution
# and source-staging portions of container/build.sh's stage_vinyl and
# stage_cachetag, because those functions interleave substitution with the
# `rpmbuild` call this script replaces and cannot be called for "just the
# substitution half".
#
# Does NOT duplicate: stage_report / stage_lint, which mock-build.sh's caller
# (ci.yml) invokes separately, unmodified, against the RPMs this script
# copies into /out/packages/.
#
# MOCK_SCOPE (Phase 2 of the failure-isolation plan) selects which package this
# run builds: `all` (default, the local whole-cohort form), `engine` for
# vinyl-cache alone, or `vmod` for libvmod-cachetag alone against Vinyl RPMs
# already in /out/packages.
#
# The split cannot move a package byte. Every mock invocation below is reached
# with identical arguments in `all` and in the scope that owns it; the derived
# per-package configs, the two epoch macros and the SOURCE_DATE_EPOCH exports
# are unchanged; and the cachetag build resolves vinyl-cache-devel from a
# createrepo_c repository assembled by the same command from the same RPM files,
# whether this run produced them or downloaded them. In particular the cachetag
# --rebuild does not inherit anything from the vinyl build even in `all` scope:
# as the --addrepo note in the shared driver records, every mock --rebuild
# begins with a chroot init that restores the root cache and discards whatever
# the preceding builds and --install left behind.

set -euo pipefail

. /recipes/cohort.env
# shellcheck source=../lib/mock.sh
. /ci/lib/mock.sh

scope=${MOCK_SCOPE:-all}
mock_cfg=alma+epel-9-x86_64
topdir=/out/rpmbuild
logdir=/out/logs
srcdir=$topdir/SOURCES
specdir=$topdir/SPECS
resultdir=/out/mockresult
localrepo=/out/localrepo
vinyl_srcname=vinyl-cache-$VINYL_VERSION
vinyl_evr="$VINYL_VERSION-$VINYL_RELEASE.el9"

say() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

case $scope in
all | engine | vmod) say "mock scope: $scope" ;;
*) die "unknown MOCK_SCOPE '$scope' (all|engine|vmod)" ;;
esac

# $srcdir is normally created by container/build.sh's `source` stage, which the
# vmod scope does not run; mkdir -p is a no-op in the scopes that do.
mkdir -p "$specdir" "$srcdir" "$resultdir/vinyl" "$resultdir/cachetag" /out/packages "$logdir"

# Registered before anything can fail. The destinations are named here because
# they are read by name downstream -- by the artifact upload, and by the
# hardening flag assertion. The Debian lane needs no equivalent capture step:
# dh_auto_configure echoes the configure line and libtool echoes every compile
# and link command into the log pbuilder_build_one tees.
mock_watch_logs "$resultdir/vinyl" "$logdir/mock-vinyl-rpmbuild.log" "$logdir/mock-vinyl-root.log"
mock_watch_logs "$resultdir/cachetag" "$logdir/mock-cachetag-rpmbuild.log" "$logdir/mock-cachetag-root.log"
mock_install_log_trap

###############################################################################
say "install Mock"
###############################################################################

mock_install_toolchain

# The BIND MOUNT, not a subdirectory: see mock_setup_build_user.
mock_setup_build_user /out "$resultdir" "$topdir"

#
# Each package gets a derived config that forwards its own epoch into the
# chroot environment; the root name inside is pinned to the stock config's so
# every invocation keeps sharing the one --no-clean root.
#
vinyl_mock_cfg=$topdir/mock-vinyl.cfg
cachetag_mock_cfg=$topdir/mock-cachetag.cfg
mock_derived_config "$vinyl_mock_cfg" "$mock_cfg" "$VINYL_SOURCE_DATE_EPOCH"
mock_derived_config "$cachetag_mock_cfg" "$mock_cfg" "$CACHETAG_SOURCE_DATE_EPOCH"

if [ "$scope" != vmod ]; then
###############################################################################
say "vinyl-cache: generate the spec (duplicates container/build.sh stage_vinyl's substitution)"
###############################################################################

sed \
	-e "s|@VINYL_VERSION@|$VINYL_VERSION|g" \
	-e "s|@VINYL_RELEASE@|$VINYL_RELEASE|g" \
	-e "s|@VINYL_GIT_COMMIT@|$VINYL_GIT_COMMIT|g" \
	-e "s|@COHORT_ID@|$COHORT_ID|g" \
	-e "s|@RPM_CHANGELOG_DATE@|$(LC_ALL=C date -u -d "@$VINYL_SOURCE_DATE_EPOCH" '+%a %b %d %Y')|g" \
	-e "s|@MAINTAINER_NAME@|$MAINTAINER_NAME|g" \
	-e "s|@MAINTAINER_EMAIL@|$MAINTAINER_EMAIL|g" \
	/recipes/vinyl-cache.spec.in > "$specdir/vinyl-cache.spec"
grep -n '@[A-Z_]\+@' "$specdir/vinyl-cache.spec" && die "unsubstituted token in vinyl-cache.spec"

install -m 0755 /recipes/find-provides "$srcdir/"
install -m 0644 /recipes/systemd/vinyl-cache.service "$srcdir/"
install -m 0644 /recipes/systemd/vinylncsa.service "$srcdir/"
install -m 0755 /recipes/systemd/vinylreload "$srcdir/"
install -m 0644 /recipes/systemd/vinyl-cache.logrotate "$srcdir/"
install -m 0644 /recipes/systemd/vinyl-cache.tmpfiles "$srcdir/"
install -m 0644 /recipes/systemd/vinyl-cache.sysusers "$srcdir/"

[ -f "$srcdir/$vinyl_srcname.tar.gz" ] ||
	die "$srcdir/$vinyl_srcname.tar.gz missing; did mock-build.sh run 'deps source' first?"
fi

###############################################################################
say "Mock: initialize the alma+epel-9-x86_64 root"
###############################################################################

mock_as -r "$mock_cfg" --init

if [ "$scope" != vmod ]; then
###############################################################################
say "Mock: vinyl-cache buildsrpm + rebuild"
###############################################################################

# The export covers anything running outside the chroot; the chroot itself
# gets the value from the derived config.
export SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH
# shellcheck disable=SC2154 # mock_epoch_defines comes from lib/mock.sh
mock_as -r "$vinyl_mock_cfg" --no-clean "${mock_epoch_defines[@]}" \
	--resultdir="$resultdir/vinyl" \
	--buildsrpm --spec "$specdir/vinyl-cache.spec" --sources "$srcdir" \
	2>&1 | tee "$logdir/mock-vinyl-srpm.log"

vinyl_srpm=$(ls "$resultdir/vinyl"/vinyl-cache-"$vinyl_evr".src.rpm)
mock_as -r "$vinyl_mock_cfg" --no-clean "${mock_epoch_defines[@]}" \
	--resultdir="$resultdir/vinyl" \
	--rebuild "$vinyl_srpm" \
	2>&1 | tee "$logdir/mock-vinyl-build.log"

find "$resultdir/vinyl" -name 'vinyl-cache*.rpm' -exec cp -p {} /out/packages/ \;
cp -p "$vinyl_srpm" /out/packages/
fi

if [ "$scope" = engine ]; then
	# The rpmbuild build.log and root.log copies happen in the
	# mock_capture_logs EXIT trap registered at the top of this script, on
	# success and failure alike.
	say "container-mock.sh complete (scope: engine)"
	ls -la /out/packages
	exit 0
fi

#
# Where the Vinyl RPMs the cachetag build resolves against come from. In `all`
# scope they are the ones this run just built; in `vmod` scope they were
# downloaded from the verified engine artifact into /out/packages. Both
# directories hold exactly the same files -- the `all` path copies them into
# /out/packages immediately above -- so the createrepo_c repository below has
# identical content either way.
#
if [ "$scope" = vmod ]; then
	vinyl_rpm_dir=/out/packages
else
	vinyl_rpm_dir=$resultdir/vinyl
fi

###############################################################################
say "publish the cohort Vinyl packages as a local repository"
###############################################################################

mock_publish_localrepo "$localrepo" "$vinyl_rpm_dir" 'vinyl-cache*.rpm'

###############################################################################
say "Mock: install vinyl-cache + vinyl-cache-devel into the SAME root"
###############################################################################

arch=$(uname -m)
mock_as -r "$mock_cfg" --no-clean --install \
	/out/packages/vinyl-cache-"$vinyl_evr.$arch".rpm \
	/out/packages/vinyl-cache-devel-"$vinyl_evr.$arch".rpm \
	2>&1 | tee "$logdir/mock-install-vinyl.log"

###############################################################################
say "read the substitution values back from the mock-installed devel package"
###############################################################################

vmoddir=$(mock_as -r "$mock_cfg" --no-clean --quiet --chroot -- \
	pkg-config --define-variable=libdir=/usr/lib64 --variable=vmoddir vinylapi)
incdir=$(mock_as -r "$mock_cfg" --no-clean --quiet --chroot -- \
	pkg-config --variable=pkgincludedir vinylapi)
vrt_major=$(mock_as -r "$mock_cfg" --no-clean --quiet --chroot -- \
	sed -n 's/^#define[[:space:]]\+VRT_MAJOR_VERSION[[:space:]]\+\([0-9]\+\).*/\1/p' "$incdir/vrt.h")
vrt_minor=$(mock_as -r "$mock_cfg" --no-clean --quiet --chroot -- \
	sed -n 's/^#define[[:space:]]\+VRT_MINOR_VERSION[[:space:]]\+\([0-9]\+\).*/\1/p' "$incdir/vrt.h")
vrt="$vrt_major.$vrt_minor"
abi=$(mock_as -r "$mock_cfg" --no-clean --quiet --chroot -- \
	sed -n 's/^#define[[:space:]]\+VMOD_ABI_Version[[:space:]]\+"\(.*\)"[[:space:]]*$/\1/p' "$incdir/vmod_abi.h" |
	awk 'NR == 1 { print $NF }')

printf 'vmoddir=%s\nvrt=%s\nabi=%s\n' "$vmoddir" "$vrt" "$abi" | tee "$logdir/cachetag-substitutions.txt"
[ "$abi" = "$VINYL_STRICT_ABI" ] || die "mock-installed Vinyl ABI $abi does not match the pinned $VINYL_STRICT_ABI"

###############################################################################
say "libvmod-cachetag: generate the spec (duplicates container/build.sh stage_cachetag's substitution)"
###############################################################################

# Dated from the cachetag release commit, not the Vinyl commit; until
# 2026-07-28 this derived from VINYL_SOURCE_DATE_EPOCH and stamped the
# cachetag package with the wrong repository's history.
changelog_date=$(LC_ALL=C date -u -d "@$CACHETAG_SOURCE_DATE_EPOCH" '+%a %b %d %Y')
sed \
	-e "s|@CACHETAG_VERSION@|$CACHETAG_VERSION|g" \
	-e "s|@PACKAGE_REVISION@|$CACHETAG_RELEASE|g" \
	-e "s|@SOURCE_URL@|$CACHETAG_SOURCE_URL|g" \
	-e "s|@VINYL_PACKAGE_VERSION@|$vinyl_evr|g" \
	-e "s|@VINYL_STRICT_ABI@|$VINYL_STRICT_ABI|g" \
	-e "s|@VINYL_VRT@|$vrt|g" \
	-e "s|@VINYL_VMODDIR@|$vmoddir|g" \
	-e "s|@COHORT_ID@|$COHORT_ID|g" \
	-e "s|@RPM_CHANGELOG_DATE@|$changelog_date|g" \
	-e "s|@MAINTAINER_NAME@|$MAINTAINER_NAME|g" \
	-e "s|@MAINTAINER_EMAIL@|$MAINTAINER_EMAIL|g" \
	/cachetag/packaging/rpm/libvmod-cachetag.spec \
	> "$specdir/libvmod-cachetag.spec"

sh /cachetag/packaging/check-tokens.sh --substituted "$specdir"

install -m 0644 "/cachetag/release/dist/$CACHETAG_TARBALL" "$srcdir/"
echo "$CACHETAG_SHA256  $srcdir/$CACHETAG_TARBALL" | sha256sum -c -

###############################################################################
say "Mock: libvmod-cachetag buildsrpm + rebuild, against the installed vinyl-cache-devel"
###############################################################################

# The cachetag epoch, not the Vinyl epoch used for the builds above; the
# cachetag derived config forwards it into the chroot.
export SOURCE_DATE_EPOCH=$CACHETAG_SOURCE_DATE_EPOCH
mock_as -r "$cachetag_mock_cfg" --no-clean "${mock_epoch_defines[@]}" \
	--addrepo="file://$localrepo" \
	--resultdir="$resultdir/cachetag" \
	--buildsrpm --spec "$specdir/libvmod-cachetag.spec" --sources "$srcdir" \
	2>&1 | tee "$logdir/mock-cachetag-srpm.log"

cachetag_srpm=$(ls "$resultdir/cachetag"/libvmod-cachetag-"$CACHETAG_VERSION-$CACHETAG_RELEASE.el9".src.rpm)
mock_as -r "$cachetag_mock_cfg" --no-clean "${mock_epoch_defines[@]}" \
	--addrepo="file://$localrepo" \
	--resultdir="$resultdir/cachetag" \
	--rebuild "$cachetag_srpm" \
	2>&1 | tee "$logdir/mock-cachetag-build.log"

find "$resultdir/cachetag" -name 'libvmod-cachetag*.rpm' -exec cp -p {} /out/packages/ \;
cp -p "$cachetag_srpm" /out/packages/

# The rpmbuild build.log and root.log copies happen in the mock_capture_logs
# EXIT trap registered at the top of this script, on success and failure alike.

say "container-mock.sh complete"
ls -la /out/packages
