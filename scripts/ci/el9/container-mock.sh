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
# Reuses UNCHANGED: the SOURCES staged by container/build.sh's `source` stage
# (mock-build.sh runs `deps source` before this script).
#
# Duplicates DELIBERATELY (see DESIGN.md section 2): the spec-substitution
# and source-staging portions of container/build.sh's stage_vinyl (lines
# ~128-153) and stage_cachetag (lines ~179-224), because those functions
# interleave substitution with the `rpmbuild` call this script replaces and
# cannot be called for "just the substitution half".
#
# Does NOT duplicate: stage_report / stage_lint, which mock-build.sh's caller
# (ci.yml) invokes separately, unmodified, against the RPMs this script
# copies into /out/packages/.
#
# DRAFT, unexecuted -- see ../../../DESIGN.md section 5. In particular, the
# exact mock CLI sequence for "keep a root alive across --buildsrpm,
# --rebuild and --install invocations" (the --no-clean flag on every step
# after --init) is written from Mock's documented behaviour, not from a
# verified run, and should be the first thing checked in a real CI dry run.

set -euo pipefail

. /recipes/cohort.env

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

mkdir -p "$specdir" "$resultdir/vinyl" "$resultdir/cachetag" /out/packages "$logdir"

###############################################################################
say "install Mock"
###############################################################################

dnf -y install epel-release
dnf -y install mock mock-core-configs

#
# Mock refuses to run as root -- "mock will not run from the root account
# (needs an unprivileged uid so it can drop privs)" -- and /usr/bin/mock is a
# symlink to usermode's consolehelper, which on a GitHub runner fails with
# "Insufficient rights." (exit 6) rather than falling back to anything useful.
# So every mock invocation runs as an unprivileged user in the mock group.
#
# That user is given the uid/gid that owns the bind-mounted /out, so mock can
# write its resultdir and the RPMs land on the host owned by the account that
# started the job rather than by root.
#
build_uid=$(stat -c %u /out)
build_gid=$(stat -c %g /out)
[ "$build_uid" -ne 0 ] || die "/out is owned by root; mock cannot run as root and would not be able to write its results"
getent group "$build_gid" >/dev/null || groupadd -g "$build_gid" mockbuild
useradd -o -u "$build_uid" -g "$build_gid" -m -d /home/mockbuild mockbuild
usermod -aG mock mockbuild
chown -R "$build_uid:$build_gid" "$resultdir" "$topdir"

# mock, as that user. Used for every mock call below; a bare `mock` here is a bug.
mock_as() { runuser -u mockbuild -- mock "$@"; }

printf 'mock runs as %s (uid %s, groups: %s)\n' \
	mockbuild "$build_uid" "$(runuser -u mockbuild -- id -nG)"
mock_as --version

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

###############################################################################
say "Mock: initialize the alma+epel-9-x86_64 root"
###############################################################################

mock_as -r "$mock_cfg" --init

###############################################################################
say "Mock: vinyl-cache buildsrpm + rebuild"
###############################################################################

export SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH
mock_as -r "$mock_cfg" --no-clean \
	--resultdir="$resultdir/vinyl" \
	--buildsrpm --spec "$specdir/vinyl-cache.spec" --sources "$srcdir" \
	2>&1 | tee "$logdir/mock-vinyl-srpm.log"

vinyl_srpm=$(ls "$resultdir/vinyl"/vinyl-cache-"$vinyl_evr".src.rpm)
mock_as -r "$mock_cfg" --no-clean \
	--resultdir="$resultdir/vinyl" \
	--rebuild "$vinyl_srpm" \
	2>&1 | tee "$logdir/mock-vinyl-build.log"

find "$resultdir/vinyl" -name 'vinyl-cache*.rpm' -exec cp -p {} /out/packages/ \;
cp -p "$vinyl_srpm" /out/packages/

###############################################################################
say "publish the just-built Vinyl packages as a local repository"
###############################################################################
#
# libvmod-cachetag BuildRequires vinyl-cache-devel = <exact evr>, which is on
# no mirror. The first draft satisfied that by `mock --install`-ing the built
# packages into the same root and relying on --no-clean to keep them there for
# the --rebuild that follows. That is not what --no-clean does: every mock
# --rebuild starts with "chroot init", which restores the root cache and
# discards anything --install put there, so `dnf builddep` then reported
#   No matching package to install: 'vinyl-cache-devel = ...'
# (measured, run 30167536066). --addrepo is the documented mechanism for
# "build against packages I just built": mock's dnf runs outside the chroot,
# so a file:// URL to a path in this container resolves.
#
dnf -y install createrepo_c
rm -rf "$localrepo"
mkdir -p "$localrepo"
find "$resultdir/vinyl" -name 'vinyl-cache*.rpm' ! -name '*.src.rpm' \
	-exec cp -p {} "$localrepo/" \;
createrepo_c "$localrepo"
chown -R "$build_uid:$build_gid" "$localrepo"
ls -1 "$localrepo"

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

changelog_date=$(LC_ALL=C date -u -d "@$VINYL_SOURCE_DATE_EPOCH" '+%a %b %d %Y')
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

export SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH
mock_as -r "$mock_cfg" --no-clean \
	--addrepo="file://$localrepo" \
	--resultdir="$resultdir/cachetag" \
	--buildsrpm --spec "$specdir/libvmod-cachetag.spec" --sources "$srcdir" \
	2>&1 | tee "$logdir/mock-cachetag-srpm.log"

cachetag_srpm=$(ls "$resultdir/cachetag"/libvmod-cachetag-"$CACHETAG_VERSION-$CACHETAG_RELEASE.el9".src.rpm)
mock_as -r "$mock_cfg" --no-clean \
	--addrepo="file://$localrepo" \
	--resultdir="$resultdir/cachetag" \
	--rebuild "$cachetag_srpm" \
	2>&1 | tee "$logdir/mock-cachetag-build.log"

find "$resultdir/cachetag" -name 'libvmod-cachetag*.rpm' -exec cp -p {} /out/packages/ \;
cp -p "$cachetag_srpm" /out/packages/

###############################################################################
say "rpmbuild logs: the effective configure line and build flags"
###############################################################################
# Mock's own build.log is the only record of what %configure expanded to and
# which CFLAGS/LDFLAGS redhat-rpm-config supplied. The tee'd files above are
# Mock's stdout, which for --rebuild is a progress summary and contains none of
# it. Without this copy the registry target manifest's build.configure_options,
# build.cflags and build.ldflags fields -- "recorded output", per
# registry/README.md -- have no source on this lane but a guess at what the
# distribution's macros expand to, which is exactly the kind of hand-written
# value this repository's rules forbid. The Debian lane needs no equivalent:
# dh_auto_configure echoes the line and libtool echoes every compile and link
# command into the job log.
for pkg in vinyl cachetag; do
	if [ -f "$resultdir/$pkg/build.log" ]; then
		cp -p "$resultdir/$pkg/build.log" "$logdir/mock-$pkg-rpmbuild.log"
		printf 'copied %s build.log (%s lines)\n' \
			"$pkg" "$(wc -l < "$logdir/mock-$pkg-rpmbuild.log" | tr -d ' ')"
	else
		printf 'W: no %s/build.log in %s\n' "$pkg" "$resultdir" >&2
	fi
done

say "container-mock.sh complete"
ls -la /out/packages
