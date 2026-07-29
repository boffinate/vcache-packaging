#!/bin/bash
#
# Builds one generated-recipe VMOD's RPM with Mock, inside the pinned
# almalinux:9 container that ../run.sh starts.
#
# Mount contract (set by ../run.sh):
#   /repo   the vcache-packaging checkout, read-only (scripts/ci/lib/mock.sh)
#   /lane   the per-row work directory: recipe/ the generated spec, src/ the
#           verified upstream archive, engine/ the verified engine RPMs,
#           out/ the results
#
# The Mock clean-room is scripts/ci/lib/mock.sh, the SAME file
# scripts/ci/el9/container-mock.sh sources. Until Step 7 Wave 0 this script was
# a copy of that one, annotated line by line with what it had been ported from,
# because Wave B's contract was that cachetag's package bytes could not move
# while the second VMOD was brought up. Two of Wave B's ten defects (B2 and B4)
# were lessons that copy had not yet absorbed. The library's header now carries
# them, once; what is left here is what is true of THIS lane.

set -euo pipefail

# shellcheck source=../../lib/mock.sh
. /repo/scripts/ci/lib/mock.sh

lane=/lane
out=$lane/out
topdir=$lane/rpmbuild
logdir=$lane/logs
resultdir=$lane/mockresult
localrepo=$lane/localrepo
mock_cfg=${MOCK_ROOT:-alma+epel-9-x86_64}

note() { printf '\n===== %s =====\n' "$*"; }
die() {
	printf 'E: %s\n' "$*" >&2
	exit 1
}

: "${VMOD_RPM_NAME:?}" "${VMOD_UPSTREAM_VERSION:?}" "${VMOD_RPM_RELEASE:?}"
: "${VMOD_SOURCE_DATE_EPOCH:?}"

mkdir -p "$out" "$logdir" "$resultdir" "$topdir/SPECS" "$topdir/SOURCES"

# The log names are the ones verify-rpm.sh reads its hardening evidence from,
# and the row's artifact upload publishes. Registered before anything can fail.
mock_watch_logs "$resultdir" "$logdir/mock-build.log" "$logdir/mock-root.log"
mock_install_log_trap

note "build toolchain"
mock_install_toolchain

note "an unprivileged user for mock"
# The BIND MOUNT, not a subdirectory: see mock_setup_build_user.
mock_setup_build_user "$lane" "$resultdir" "$topdir" "$out" "$logdir"

note "publish the verified engine packages as a local repository"
mock_publish_localrepo "$localrepo" "$lane/engine" '*.rpm'

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

# Reviewed source patches, if the overlay declared any. They are GENERATED
# output beside the spec -- rendered from the overlay's digested patch list --
# so they are copied like the spec is and are never edited here. rpmbuild
# resolves PatchN out of SOURCES, so this is where they have to be for
# %autosetup -p1 to find them; a declared Patch with no file in SOURCES fails
# the SRPM build, which is the honest place for it to fail.
patch_count=0
for patch in "$lane"/recipe/*.patch; do
	[ -e "$patch" ] || continue
	install -m 0644 "$patch" "$topdir/SOURCES/"
	patch_count=$((patch_count + 1))
done
printf 'reviewed source patches staged: %s\n' "$patch_count"
declared=$(grep -c '^Patch[0-9]\{1,\}:' "$spec" || true)
[ "$patch_count" -eq "$declared" ] ||
	die "the spec declares $declared Patch line(s) but $patch_count patch file(s)
were rendered beside it. The recipe is generated content: fix the overlay or the
generator, never the spec."
chown -R "$MOCK_BUILD_UID:$MOCK_BUILD_GID" "$topdir"

note "derived Mock configuration"
cfg=$topdir/mock-$VMOD_RPM_NAME.cfg
mock_derived_config "$cfg" "$mock_cfg" "$VMOD_SOURCE_DATE_EPOCH"

note "Mock: initialise the $mock_cfg root"
mock_as -r "$mock_cfg" --init

srpm_name=$VMOD_RPM_NAME-$VMOD_UPSTREAM_VERSION-$VMOD_RPM_RELEASE.src.rpm

note "Mock: source RPM"
# The export covers anything running outside the chroot; the chroot itself
# takes the value from the derived config above.
export SOURCE_DATE_EPOCH=$VMOD_SOURCE_DATE_EPOCH
# shellcheck disable=SC2154 # mock_epoch_defines comes from lib/mock.sh
mock_as -r "$cfg" --no-clean "${mock_epoch_defines[@]}" \
	--addrepo="file://$localrepo" \
	--resultdir="$resultdir" \
	--buildsrpm --spec "$topdir/SPECS/$VMOD_RPM_NAME.spec" \
	--sources "$topdir/SOURCES" 2>&1 | tee "$logdir/mock-srpm.log"

srpm=$resultdir/$srpm_name
[ -f "$srpm" ] || die "Mock produced no $srpm_name"

note "Mock: rebuild the source RPM in a fresh chroot"
# shellcheck disable=SC2154 # mock_epoch_defines comes from lib/mock.sh
mock_as -r "$cfg" --no-clean "${mock_epoch_defines[@]}" \
	--addrepo="file://$localrepo" \
	--resultdir="$resultdir" \
	--rebuild "$srpm" 2>&1 | tee "$logdir/mock-rebuild.log"

note "record the buildroot package set"
# The registry's per-VMOD build.build_dependencies has no other honest source
# on this lane. Debian's equivalent falls out of dpkg for free -- the
# .buildinfo's Installed-Build-Depends is dpkg's own record of the chroot -- but
# Mock resolves the buildroot itself and writes no such list, and root.log
# records only the packages each transaction ADDED, not what the build finally
# saw. So the chroot is asked directly, after the build, which is the same thing
# recipes/el9/container/build.sh:76-77 does with `dnf repoquery --installed` on
# its own lane.
#
# Not fatal if it fails: the packages are already built and copied below, and a
# row that produced a good package must not be failed by a bookkeeping step.
if mock_as -r "$cfg" --no-clean --quiet \
	--chroot -- rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}.%{ARCH}\n' \
	>"$logdir/buildroot-packages.tsv" 2>"$logdir/buildroot-packages.err"; then
	sort -o "$logdir/buildroot-packages.tsv" "$logdir/buildroot-packages.tsv"
	printf 'buildroot: %s packages\n' \
		"$(wc -l <"$logdir/buildroot-packages.tsv" | tr -d ' ')"
else
	printf 'W: could not query the buildroot package set; see buildroot-packages.err\n' >&2
fi

find "$resultdir" -maxdepth 1 -name '*.rpm' -exec cp -p {} "$out/" \;
chown -R "$MOCK_BUILD_UID:$MOCK_BUILD_GID" "$out" "$logdir"

note "EL9 VMOD lane complete"
ls -la "$out"
