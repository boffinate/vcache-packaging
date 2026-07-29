#!/bin/bash
#
# Payload, ABI, hardening, lint, installed-package smoke and behaviour for one
# generated-recipe VMOD's Debian package. Runs in a FRESH debian:trixie
# container that has never seen a build tree, so the subject is the installed
# package and nothing else.
#
# Mount contract (set by ../run.sh):
#   /lane   out/ the built .debs, engine/ the verified engine .debs,
#           tests/ the ported VTCs, src/ the verified upstream archive,
#           scripts/ this script and the shared check libraries
#   /meta   names.json and the generated recipe's generation-record.json
#
# The check families are cachetag's, applied to a generated package: the
# payload is exactly what the overlay declared, the ABI and cohort dependencies
# are the ones the registry generated, the hardening flags survived the
# recipe, lint has an explicit expectation rather than a shrug, the runtime
# pair alone can load the VMOD, and upstream's own test expectations pass
# against the installed .so.
#
# Since Step 7 Wave 0 the payload allowlist, the hardening inspection and the
# behaviour suite are not written here: they are the shared implementations in
# scripts/ci/lib/package-checks.sh and scripts/ci/lib/vtc-suite.sh, which the
# cachetag lanes call too. generate.sh stages both into lane/scripts/ because
# this container mounts only the lane.

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
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

: "${VMOD_BINARY_NAME:?}" "${VMOD_OBJECT:?}" "${VMOD_DEBIAN_VERSION:?}"
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

apt-get update -qq
apt-get install -y --no-install-recommends \
	dpkg-dev lintian binutils python3 procps file >/dev/null

deb=$(find /lane/out -maxdepth 1 -name "${VMOD_BINARY_NAME}_${VMOD_DEBIAN_VERSION}_*.deb" | sort | head -1)
[ -f "$deb" ] || die "no built package for $VMOD_BINARY_NAME $VMOD_DEBIAN_VERSION"

note "1 -- package metadata"
dpkg-deb -I "$deb"

note "2 -- generated ABI and cohort dependencies"
depends=$(dpkg-deb -f "$deb" Depends)
echo "Depends: $depends"
for want in "vinyld-abi-$VINYL_STRICT_ABI" "vinyld-vrt (= $VINYL_VRT)" "vinyld-cohort-$COHORT_ID"; do
	case "$depends" in
	*"$want"*) echo "OK: depends on $want" ;;
	*) die "missing generated dependency: $want" ;;
	esac
done

note "3 -- payload is exactly what the overlay declared"
pc_assert_deb_payload "$deb" "$VMOD_BINARY_NAME" "$VINYL_VMODDIR" \
	"$VMOD_OBJECT" "$VMOD_MAN_PAGE" ||
	die "the payload is not what the overlay declared; see above"

note "4 -- hardening inspection"
mkdir -p /tmp/x && dpkg-deb -x "$deb" /tmp/x
pc_verify_build "/tmp/x$VINYL_VMODDIR/$VMOD_OBJECT" "$VMOD_OBJECT" \
	log /lane/logs/pbuilder-build.log ||
	die "hardening inspection failed"
# The extracted tree goes away before the uniqueness check below runs, so that
# check can stay maximally strict instead of learning to ignore a directory.
rm -rf /tmp/x

note "5 -- lintian, with an explicit expectation"
# Not `|| true`. The generated recipe carries its own overrides for the two
# tags every package of this shape emits; anything else is a finding about the
# generator and has to be seen. The cachetag lane runs the same --fail-on since
# Step 7 Wave 0.
lint_status=0
lintian --no-tag-display-limit --fail-on error,warning "$deb" 2>&1 | tee /tmp/lintian.log || lint_status=$?
[ "$lint_status" -eq 0 ] || die "lintian reported errors or warnings; see above.
A generated recipe is not hand-patched to silence a tag: either the tag is a
real defect in the templates or the overlay, or it belongs in the overlay's
reviewed lintian_overrides."

note "6 -- installed-package smoke: the runtime pair alone loads the VMOD"
mkdir -p /repo && cp /lane/out/*.deb /lane/engine/*.deb /repo/
(cd /repo && dpkg-scanpackages --multiversion . >Packages && gzip -9kf Packages)
printf 'deb [trusted=yes] file:/repo ./\n' >/etc/apt/sources.list.d/vinyl-cohort.list
apt-get update -qq
apt-get install -y vinyl-cache "$VMOD_BINARY_NAME" || die "runtime pair install failed"
[ "$(dpkg-query -W -f='${db:Status-Status}' vinyl-cache-dev 2>/dev/null)" != "installed" ] ||
	die "vinyl-cache-dev is installed; the suite must prove the runtime pair suffices"
# The prune list is the RPM half's, which learned it first: /tmp/x is the tree
# the hardening stage extracts (deleted above as well, so this is belt and
# braces) and /repo is the local repository this stage just built out of the
# lane's own packages. Wave B run 30412067149 failed this row on the extracted
# copy -- the check was right that there were two, and wrong about what the
# second one meant, because the Debian script never inherited the RPM script's
# prune list. Third instance of the same class after B3 and B6, which is why
# the two scripts were then swept side by side rather than patched again.
found=$(find / \( -path /proc -o -path /sys -o -path /tmp/x -o -path /repo \) -prune -o \
	-name "$VMOD_OBJECT" -print 2>/dev/null || true)
[ "$found" = "$VINYL_VMODDIR/$VMOD_OBJECT" ] ||
	die "$VMOD_OBJECT is not uniquely at \$VINYL_VMODDIR (found: $found)"
dpkg -S "$VINYL_VMODDIR/$VMOD_OBJECT"
[ "$(command -v vinyltest)" = /usr/bin/vinyltest ] || die "vinyltest is not the packaged one"
echo "OK: runtime pair installed, single packaged .so, packaged vinyltest driver"

note "7 -- behaviour: upstream's own expectations against the installed package"
archive=$(find /lane/src -maxdepth 1 -name "*.tar.gz" | sort | head -1)
# The installed fixture-package versions land in /lane/logs, which the workflow
# uploads with the rest of the lane logs; the registry's per-VMOD
# tests.fixture_packages is recorded from that file, never restated by hand.
# shellcheck disable=SC2086 # the declared package list is a deliberate word list
vtc_install_packages apt /lane/logs/fixture-packages.tsv $VMOD_TEST_PACKAGES ||
	die "the declared behaviour fixture packages could not be installed and recorded"
vtc_stage_fixtures "$archive" "$VMOD_SOURCE_SHA256" \
	"$VMOD_TEST_FIXTURE_ROOT" "$VMOD_TEST_FIXTURES" /tmp/fixtures ||
	die "the declared test fixtures could not be staged"
vtc_run_suite /lane/tests "$VINYL_VMODDIR" /tmp/fixtures \
	"$VMOD_TEST_DRIVER" "$VMOD_TEST_MACROS" ||
	die "the installed-package behaviour suite failed"

note "verify-deb complete"
