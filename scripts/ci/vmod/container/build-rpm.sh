#!/bin/bash
#
# Builds one generated-recipe VMOD's RPM with Mock, inside the pinned
# almalinux:9 container that ../build-rpm.sh starts.
#
# Mount contract (set by ../build-rpm.sh):
#   /repo   the vcache-packaging checkout, read-only
#   /lane   the per-row work directory: recipe/ the generated spec, src/ the
#           verified upstream archive, engine/ the verified engine RPMs,
#           out/ the results
#
# Structurally the same as scripts/ci/el9/container-mock.sh, and separate from
# it for the same reason build-deb.sh is separate from container-pbuilder.sh:
# that script produces cachetag's package bytes and this wave's equivalence
# contract is that they do not move. See ../lib.sh.
#
# Mock, not a host rpmbuild: Mock supplies the minimal buildroot that exposes
# an undeclared BuildRequires, which is the whole point of a clean-room build.

set -euo pipefail

lane=/lane
out=$lane/out
topdir=/builddir/rpmbuild
localrepo=/localrepo

note() { printf '\n===== %s =====\n' "$*"; }
die() {
	printf 'E: %s\n' "$*" >&2
	exit 1
}

: "${VMOD_RPM_NAME:?}" "${VMOD_UPSTREAM_VERSION:?}" "${VMOD_RPM_RELEASE:?}"
: "${VMOD_SOURCE_DATE_EPOCH:?}" "${MOCK_ROOT:?}"

mkdir -p "$out" "$topdir/SPECS" "$topdir/SOURCES" "$topdir/SRPMS" "$topdir/RPMS"

note "build toolchain"
dnf -y -q install mock rpm-build createrepo_c >/dev/null
rpm -q mock createrepo_c

note "publishing the verified engine packages as a local repository"
# The generated spec BuildRequires the engine development package at an exact
# version, which is on no mirror. Same fix as the cachetag EL9 lane's.
rm -rf "$localrepo"
mkdir -p "$localrepo"
cp -v "$lane"/engine/*.rpm "$localrepo/"
createrepo_c --quiet "$localrepo"

spec=$lane/recipe/$VMOD_RPM_NAME.spec
[ -f "$spec" ] || die "no generated spec at $spec"
install -m 0644 "$spec" "$topdir/SPECS/"

note "an unsubstituted token must never reach rpmbuild"
if grep -n '@[A-Z][A-Z0-9_]\{1,\}@' "$topdir/SPECS/$VMOD_RPM_NAME.spec"; then
	die "an unsubstituted token is present in the generated spec"
fi

note "stage the verified upstream archive"
archive=$(find "$lane/src" -maxdepth 1 -name '*.tar.gz' | sort | head -1)
[ -n "$archive" ] || die "no verified source archive in $lane/src"
install -m 0644 "$archive" "$topdir/SOURCES/"

# Mock configuration: the pinned root plus the local engine repository, and the
# recorded epoch so the build is dated from this VMOD's own release commit.
#
# EL9's rpm derives SOURCE_DATE_EPOCH from the newest %changelog entry unless
# these two macros are set, so exporting the variable alone is not enough --
# the same lesson recipes/el9/container/build.sh records.
cfg=/etc/mock/vmod-lane.cfg
{
	printf 'include("%s.cfg")\n' "$MOCK_ROOT"
	printf 'config_opts["root"] = "vmod-lane"\n'
	printf 'config_opts["dnf.conf"] += """\n[vinyl-cohort]\nname=vinyl-cohort\nbaseurl=file://%s\nenabled=1\ngpgcheck=0\npriority=1\n"""\n' "$localrepo"
	printf 'config_opts["macros"]["%%source_date_epoch_from_changelog"] = "0"\n'
	printf 'config_opts["macros"]["%%clamp_mtime_to_source_date_epoch"] = "1"\n'
	printf 'config_opts["environment"]["SOURCE_DATE_EPOCH"] = "%s"\n' "$VMOD_SOURCE_DATE_EPOCH"
} >"$cfg"
cat "$cfg"

srpm=$VMOD_RPM_NAME-$VMOD_UPSTREAM_VERSION-$VMOD_RPM_RELEASE.src.rpm

note "Mock: source RPM"
mock -r vmod-lane --resultdir "$out" --buildsrpm \
	--spec "$topdir/SPECS/$VMOD_RPM_NAME.spec" \
	--sources "$topdir/SOURCES" 2>&1 | tail -40
[ -f "$out/$srpm" ] || die "Mock produced no $srpm"

note "Mock: rebuild the source RPM in a fresh chroot"
mock -r vmod-lane --resultdir "$out" --rebuild "$out/$srpm" 2>&1 | tail -60

note "EL9 VMOD lane complete"
ls -la "$out"
