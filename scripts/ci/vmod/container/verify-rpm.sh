#!/bin/bash
#
# Payload, ABI, hardening, rpmlint, installed-package smoke and behaviour for
# one generated-recipe VMOD's RPM. Runs in a FRESH almalinux:9 container that
# has never seen a build tree, so the subject is the installed package.
#
# Mount contract (set by ../run.sh):
#   /lane   out/ the built RPMs, engine/ the verified engine RPMs,
#           tests/ the ported VTCs, src/ the verified upstream archive,
#           scripts/ this script and the shared check libraries
#
# The same check families as the Debian half, expressed in RPM's vocabulary:
# the Requires are the arch-qualified vinyld() capabilities recipes/el9/
# find-provides injects on the runtime package, and the payload is compared
# against the overlay's declaration rather than trusted.
#
# Since Step 7 Wave 0 the payload allowlist, the hardening inspection and the
# behaviour suite come from scripts/ci/lib/package-checks.sh and
# scripts/ci/lib/vtc-suite.sh, staged into the lane by generate.sh. The two
# verify scripts had drifted apart three times by then -- B3, B6 and B9 -- and
# every one of those was a check one of them had and the other did not.

set -euo pipefail
export LC_ALL=C

note() {
	printf '\n########## %s ##########\n' "$*"
	# The stage marker the workflow classifies from. Without it every failure
	# in this script collapses to one status, and four statuses that exist in
	# the vocabulary with no producer that can emit them are latent lies.
	printf '%s\n' "$*" >/lane/verify-stage
}
die() {
	printf 'FAIL: %s\n' "$*" >&2
	exit 1
}

: "${VMOD_RPM_NAME:?}" "${VMOD_OBJECT:?}" "${VMOD_UPSTREAM_VERSION:?}" "${VMOD_RPM_RELEASE:?}"
: "${VINYL_VMODDIR:?}" "${VINYL_STRICT_ABI:?}" "${VINYL_VRT:?}" "${COHORT_ID:?}"
: "${VMOD_MAN_PAGE:?}" "${VMOD_SOURCE_SHA256:?}"
: "${VMOD_TEST_FIXTURE_ROOT:?}" "${VMOD_TEST_FIXTURES:?}" "${VMOD_TEST_MACROS:?}"
: "${VMOD_TEST_DRIVER:?}"
# VMOD_TEST_PACKAGES may legitimately be empty: dict's suite needs no fixture
# server. It is still declared, so `set -u` on an UNSET name stays a defect.
: "${VMOD_TEST_PACKAGES?}"

# shellcheck source=../../lib/package-checks.sh
. /lane/scripts/package-checks.sh
# shellcheck source=../../lib/vtc-suite.sh
. /lane/scripts/vtc-suite.sh

# epel-release first: rpmlint is in EPEL on EL9, not in AlmaLinux itself --
# the same trap `mock` set in build-rpm.sh, and the same fix
# recipes/el9/vtc-suite/vtc-suite.sh:30 already applies before installing.
dnf -y -q install epel-release >/dev/null
dnf -y -q install rpm-build rpmlint binutils python3 findutils >/dev/null
rpm -q rpmlint

rpm=$(find /lane/out -maxdepth 1 \
	-name "$VMOD_RPM_NAME-$VMOD_UPSTREAM_VERSION-$VMOD_RPM_RELEASE.*.rpm" \
	! -name '*debuginfo*' ! -name '*debugsource*' ! -name '*.src.rpm' |
	sort | head -1)
[ -n "$rpm" ] || die "no built package for $VMOD_RPM_NAME"

note "1 -- package metadata"
rpm -qip "$rpm"

note "2 -- generated ABI and cohort dependencies"
requires=$(rpm -qp --requires "$rpm")
printf '%s\n' "$requires"
isa=$(rpm --eval '%{?_isa}')
for want in "vinyld(abi)$isa = $VINYL_STRICT_ABI" \
	"vinyld(vrt)$isa = $VINYL_VRT" \
	"vinyld(cohort-$COHORT_ID)$isa"; do
	printf '%s\n' "$requires" | grep -qxF "$want" ||
		die "missing generated dependency: $want"
	echo "OK: requires $want"
done

note "3 -- payload is exactly what the overlay declared"
pc_assert_rpm_payload "$rpm" "$VINYL_VMODDIR" "$VMOD_OBJECT" "$VMOD_MAN_PAGE" ||
	die "the payload is not what the overlay declared; see above"

note "4 -- the VMOD advertises no soname provide"
# It is a dlopen()ed plugin, not a system library. The generated spec's
# __provides_exclude_from suppresses the automatic soname provide; assert it,
# because a stray one would make the plugin look like a shared library that
# something could link against.
#
# Deliberately has no Debian twin: dpkg generates no provides for a plugin
# outside the linker path, so there is nothing there to assert the absence of.
provides=$(rpm -qp --provides "$rpm")
printf '%s\n' "$provides"
printf '%s\n' "$provides" | grep -q "^${VMOD_OBJECT}" &&
	die "$VMOD_OBJECT is advertised as a soname provide"
echo "OK: no soname provide for the plugin"

note "5 -- hardening inspection"
mkdir -p /tmp/x && (cd /tmp/x && rpm2cpio "$rpm" | cpio -idm --quiet)
pc_verify_build "/tmp/x$VINYL_VMODDIR/$VMOD_OBJECT" "$VMOD_OBJECT" \
	log /lane/logs/mock-build.log ||
	die "hardening inspection failed"
# Deleted here as well as pruned below, so the uniqueness check does not depend
# on a prune list staying in step with an extraction path.
rm -rf /tmp/x

note "6 -- rpmlint, with an explicit expectation"
# Not `|| true`. A generated recipe has no excuse for a diagnostic nobody
# reviewed: either the templates or the overlay is wrong, or it belongs in a
# reviewed override.
#
# ASYMMETRY, measured and left standing in Step 7 Wave 0. The cachetag EL9 lane
# is STRICTER here, not weaker: recipes/el9/container/build.sh's stage_lint
# filters through a reviewed waiver file and then asserts "0 errors, 0
# warnings", while rpmlint's own exit status is non-zero only for errors. On the
# green baseline 30437775658 this lane reported `0 errors, 6 warnings` and
# passed. Closing it needs an rpmlint-override mechanism in the overlay -- the
# twin of the lintian_overrides list that already exists -- because the
# alternative is editing dict's Summary and %description, which moves package
# bytes whose digests are recorded release evidence. That mechanism is Wave 1
# work; see the Wave 0 note.
lint_status=0
rpmlint "$rpm" 2>&1 | tee /tmp/rpmlint.log || lint_status=$?
[ "$lint_status" -eq 0 ] || die "rpmlint reported findings; see above.
A generated recipe is not hand-patched to silence one."

note "7 -- installed-package smoke: the runtime pair alone loads the VMOD"
mkdir -p /repo && cp /lane/out/*.rpm /lane/engine/*.rpm /repo/ 2>/dev/null || true
rm -f /repo/*.src.rpm
dnf -y -q install createrepo_c >/dev/null
createrepo_c --quiet /repo
cat >/etc/yum.repos.d/vinyl-cohort.repo <<'REPO'
[vinyl-cohort]
name=vinyl-cohort
baseurl=file:///repo
enabled=1
gpgcheck=0
REPO
dnf -y install vinyl-cache "$VMOD_RPM_NAME" || die "runtime pair install failed"
rpm -q vinyl-cache "$VMOD_RPM_NAME"
rpm -q vinyl-cache-devel >/dev/null 2>&1 &&
	die "vinyl-cache-devel is installed; the suite must prove the runtime pair suffices"
found=$(find / \( -path /proc -o -path /sys -o -path /tmp/x -o -path /repo \) -prune -o \
	-name "$VMOD_OBJECT" -print 2>/dev/null || true)
[ "$found" = "$VINYL_VMODDIR/$VMOD_OBJECT" ] ||
	die "$VMOD_OBJECT is not uniquely at \$VINYL_VMODDIR (found: $found)"
rpm -qf "$VINYL_VMODDIR/$VMOD_OBJECT"
[ "$(command -v vinyltest)" = /usr/bin/vinyltest ] || die "vinyltest is not the packaged one"
echo "OK: runtime pair installed, single packaged .so, packaged vinyltest driver"

note "8 -- behaviour: upstream's own expectations against the installed package"
archive=$(find /lane/src -maxdepth 1 -name '*.tar.gz' | sort | head -1)
# shellcheck disable=SC2086 # the declared package list is a deliberate word list
vtc_install_packages dnf $VMOD_TEST_PACKAGES ||
	die "the declared behaviour fixture packages could not be installed"
vtc_stage_fixtures "$archive" "$VMOD_SOURCE_SHA256" \
	"$VMOD_TEST_FIXTURE_ROOT" "$VMOD_TEST_FIXTURES" /tmp/fixtures ||
	die "the declared test fixtures could not be staged"
vtc_run_suite /lane/tests "$VINYL_VMODDIR" /tmp/fixtures \
	"$VMOD_TEST_DRIVER" "$VMOD_TEST_MACROS" ||
	die "the installed-package behaviour suite failed"

note "verify-rpm complete"
