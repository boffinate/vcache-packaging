#!/bin/bash
#
# Installed-package VTC behavior suite: the full cachetag default-storage VTC
# matrix, driven by the packaged vinyltest against the packaged VMOD.
#
# Runs in a fresh almalinux:9 container that has never seen the build lane.
# Only the runtime pair -- vinyl-cache and libvmod-cachetag, never the -devel
# package -- is installed, because the claim under test is that the installed
# runtime pair alone can load the VMOD and pass the suite. The VTC sources
# come from the pinned cachetag release archive; the .so under test is the
# packaged one in the runtime's VMOD directory, proven below.
#
# Deliberately a structural near-twin of recipes/el9/smoke/smoke.sh and of
# the Debian lane's stage-vtc-suite.sh; the two lanes' scripts stay parallel
# rather than shared, matching the smoke convention.

set -euo pipefail

. /recipes/cohort.env

vinyl_evr="$VINYL_VERSION-$VINYL_RELEASE.el9"
arch=$(uname -m)

die() { printf 'FAIL: %s\n' "$*"; exit 1; }
note() { printf '\n########## %s ##########\n' "$*"; }

# EPEL is a genuine runtime prerequisite of the EL9 cohort runtime package,
# not a convenience for this script: vinyld is built --with-unwind and
# libunwind.so.8 ships in neither BaseOS nor AppStream.
dnf -y install epel-release >/dev/null

note "1 -- install the runtime pair only"
dnf -y install \
	/out/packages/vinyl-cache-"$vinyl_evr"."$arch".rpm \
	/out/packages/libvmod-cachetag-"$CACHETAG_VERSION"-"$CACHETAG_RELEASE".el9."$arch".rpm
rpm -q vinyl-cache libvmod-cachetag
if rpm -q vinyl-cache-devel >/dev/null 2>&1; then
	die "vinyl-cache-devel is installed; the suite must prove the runtime pair suffices"
fi
echo "OK: runtime pair installed, no -devel package present"

note "2 -- prove the installed .so and driver are the test subjects"
vmoddir=$(pkg-config --variable=vmoddir vinylapi 2>/dev/null || echo "")
if [ -z "$vmoddir" ]; then
	# vinylapi.pc lives in the devel package, which this suite deliberately
	# does not install. Fall back to the runtime's own directory.
	vmoddir=$(rpm -ql vinyl-cache | sed -n 's#\(.*/vmods\)/libvmod_std\.so#\1#p' | head -1)
	printf 'vmoddir (from the runtime package file list): %s\n' "$vmoddir"
else
	printf 'vmoddir (from vinylapi.pc): %s\n' "$vmoddir"
fi
[ "$vmoddir" = "$(rpm --eval %{_libdir})/vinyl-cache/vmods" ] ||
	die "vmoddir $vmoddir is not the runtime's standard VMOD directory"

found=$(find / \( -path /proc -o -path /sys \) -prune -o -name 'libvmod_cachetag.so' -print 2>/dev/null)
[ "$found" = "$vmoddir/libvmod_cachetag.so" ] ||
	die "libvmod_cachetag.so not uniquely at the VMOD directory (found: $found)"
rpm -qf "$vmoddir/libvmod_cachetag.so"
[ "$(rpm -qf --qf '%{NAME}' "$vmoddir/libvmod_cachetag.so")" = libvmod-cachetag ] ||
	die "the installed .so is not owned by the cachetag package"
sha256sum "$vmoddir/libvmod_cachetag.so"
[ "$(command -v vinyltest)" = /usr/bin/vinyltest ] ||
	die "vinyltest is not the packaged /usr/bin/vinyltest"
[ "$(rpm -qf --qf '%{NAME}' /usr/bin/vinyltest)" = vinyl-cache ] ||
	die "/usr/bin/vinyltest is not owned by the vinyl-cache package"
echo "OK: single packaged .so at $vmoddir, packaged vinyltest driver"

printf '\nSELinux: '
if command -v getenforce >/dev/null 2>&1; then getenforce; else echo "not available in this container"; fi
printf 'vinyld runs unconfined in Docker; enforcing-mode coverage is deferred\n'
printf 'to a CI job on a host that can run SELinux enforcing.\n'

note "3 -- unpack the pinned cachetag release archive"
# The exact versioned filename, never a glob: stale archives of earlier
# versions can coexist in a local dist/ directory.
tarball=/out/rpmbuild/SOURCES/$CACHETAG_TARBALL
echo "$CACHETAG_SHA256  $tarball" | sha256sum -c - ||
	die "pinned archive digest mismatch for $tarball"
# Pinned bytes: the VTCs are immutable inputs of this run, so the
# VSC-staleness hazard of editing tests against a live tree is out of scope.
mkdir -p /tmp/cachetag-src
tar -C /tmp/cachetag-src --strip-components=1 -xzf "$tarball"
test -d /tmp/cachetag-src/src/vtc || die "unpacked archive has no src/vtc directory"

note "4 -- derive the test ledger from the archive's own Makefile.am"
cd /tmp/cachetag-src/src
sed -n '/^VTC_TESTS[[:space:]]*=/,/[^\\]$/p' Makefile.am |
	grep -o 'vtc/[A-Za-z0-9_]*\.vtc' > /tmp/vtc-ledger
while IFS= read -r t; do
	[ -f "$t" ] || die "ledger entry missing from the archive: $t"
done < /tmp/vtc-ledger
count=$(wc -l < /tmp/vtc-ledger | tr -d ' ')
[ "$count" = "$CACHETAG_VTC_COUNT" ] ||
	die "ledger has $count VTCs, pinned CACHETAG_VTC_COUNT is $CACHETAG_VTC_COUNT"
echo "ledger: $count VTCs from VTC_TESTS"
# Deliberately excluded: the 25 FELLOW_VTC_TESTS entries (TESTS never
# includes them; they need the Fellow storage backend this cohort does not
# ship) and cachetag_wal_test, a check_PROGRAMS unit test that cannot be
# built or run against installed packages -- it remains source-archive-only
# evidence.
echo "excluded: FELLOW_VTC_TESTS ($(sed -n '/^FELLOW_VTC_TESTS[[:space:]]*=/,/[^\\]$/p' Makefile.am | grep -c 'vtc/')) and cachetag_wal_test"

note "5 -- run the suite (minus the quarantined pm00007) with the packaged driver"
grep -v 'cachetag_pm00007\.vtc' /tmp/vtc-ledger > /tmp/vtc-main
# debug=+vclrel ("Rapid VCL release", include/tbl/debug_bits.h, present in
# both 9.0.1 and trunk) makes workers release their cached VCL reference
# after every task, so vcl->busy is zero at stop and every VTC teardown's
# CLI stop completes promptly. Needed because 9.0.1 lacks 7de492b0e8 ("Shut
# down pools when stopping"): pools are not shut down on stop, so idle
# workers hold their VCL refs through a 60s cond-wait. No-op-equivalent on
# the trunk pin, which contains the fix. Remove when the release track
# reaches a Vinyl containing 7de492b0e8 (9.0.2 if backported).
main_status=0
# shellcheck disable=SC2046
vinyltest -v -k -j1 -t 60 \
	-p vmod_path="$vmoddir" \
	-p debug=+vclrel \
	$(cat /tmp/vtc-main) 2>&1 | tee /tmp/vtc-main.log || main_status=$?
main_passed=$(grep -c 'TEST .* passed' /tmp/vtc-main.log || true)
main_skipped=$(grep -c 'TEST .* skipped' /tmp/vtc-main.log || true)
echo "main set: status=$main_status passed=$main_passed skipped=$main_skipped of $(wc -l < /tmp/vtc-main | tr -d ' ')"
[ "$main_status" -eq 0 ] || die "vinyltest reported failures in the main set"

note "6 -- cachetag_pm00007, serial, with the signature-gated single retry"
# pm00007 is quarantined to a serial, last-place run: the upstream note
# records a 20-30% failure rate under load with a specific signature. On a
# failure showing BOTH decisive markers -- the client HTTP rx timeout and
# the HTC eof -- exactly one retry is allowed and recorded; any other
# failure, or a second failure, is a hard fail.
pm_result=clean
pm_status=0
vinyltest -v -j1 -t 60 \
	-p vmod_path="$vmoddir" \
	-p debug=+vclrel \
	vtc/cachetag_pm00007.vtc 2>&1 | tee /tmp/vtc-pm00007.log || pm_status=$?
if [ "$pm_status" -ne 0 ]; then
	if grep -q 'HTTP rx timeout' /tmp/vtc-pm00007.log &&
	   grep -q 'HTC eof' /tmp/vtc-pm00007.log; then
		echo "pm00007 failed with the known load-flake signature; retrying once"
		pm_status=0
		vinyltest -v -j1 -t 60 \
			-p vmod_path="$vmoddir" \
			-p debug=+vclrel \
			vtc/cachetag_pm00007.vtc 2>&1 | tee /tmp/vtc-pm00007-retry.log || pm_status=$?
		[ "$pm_status" -eq 0 ] || die "cachetag_pm00007 failed twice"
		pm_result='flaky-pass(1 retry, known signature)'
	else
		die "cachetag_pm00007 failed without the known flake signature"
	fi
fi

note "7 -- summary"
total_passed=$((main_passed + 1))
[ "$total_passed" -eq "$CACHETAG_VTC_COUNT" ] ||
	die "passed $total_passed of $CACHETAG_VTC_COUNT VTCs"
[ "$main_skipped" -eq 0 ] || die "$main_skipped VTCs skipped; the suite must run completely"
printf 'VTC-SUITE SUMMARY: %s/%s passed, 0 skipped, pm00007=%s\n' \
	"$total_passed" "$CACHETAG_VTC_COUNT" "$pm_result"
