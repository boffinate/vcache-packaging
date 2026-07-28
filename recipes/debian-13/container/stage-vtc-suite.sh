#!/bin/bash
#
# Installed-package VTC behavior suite: the full cachetag default-storage VTC
# matrix, driven by the packaged vinyltest against the packaged VMOD.
#
# Runs in a FRESH debian:trixie container that has seen neither build tree.
# Only the runtime pair -- vinyl-cache and libvmod-cachetag, never the -dev
# package -- is installed, because the claim under test is that the installed
# runtime pair alone can load the VMOD and pass the suite. The VTC sources
# come from the pinned cachetag release archive; the .so under test is the
# packaged one in the runtime's VMOD directory, proven below.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

die() { printf 'FAIL: %s\n' "$*"; exit 1; }
note() { printf '\n########## %s ##########\n' "$*"; }

apt-get update -qq
apt-get install -y --no-install-recommends dpkg-dev python3 procps curl >/dev/null

note "1 -- local apt repository, install the runtime pair only"
mkdir -p /repo
cp /out/*.deb /repo/
cd /repo
dpkg-scanpackages --multiversion . > Packages
gzip -9kf Packages
printf 'deb [trusted=yes] file:/repo ./\n' > /etc/apt/sources.list.d/vinyl-cohort.list
apt-get update -qq
echo "repository contents:"
grep -E '^(Package|Version|Provides|Depends):' Packages

apt-get install -y vinyl-cache libvmod-cachetag || die "runtime pair install failed"
dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' vinyl-cache libvmod-cachetag
# Status-field check, not bare dpkg-query -W: installed vinyl-cache carries
# Suggests: vinyl-cache-dev, and the relationship reference alone gives the
# status db a placeholder entry ("unknown ok not-installed") that a bare
# dpkg-query -W exits 0 for. The guard must fire on the package being
# INSTALLED, not on dpkg having heard of it.
[ "$(dpkg-query -W -f='${db:Status-Status}' vinyl-cache-dev 2>/dev/null)" != "installed" ] ||
	die "vinyl-cache-dev is installed; the suite must prove the runtime pair suffices"
echo "OK: runtime pair installed, no -dev package present"

note "2 -- prove the installed .so and driver are the test subjects"
# || true: find's own exit status (an unreadable path, an ENOENT race) must
# not kill the script messageless under set -e; the comparison below is the
# assertion and carries the diagnostic.
found=$(find / \( -path /proc -o -path /sys \) -prune -o -name 'libvmod_cachetag.so' -print 2>/dev/null || true)
[ "$found" = "$VINYL_VMODDIR/libvmod_cachetag.so" ] ||
	die "libvmod_cachetag.so not uniquely at \$VINYL_VMODDIR (found: $found)"
dpkg -S "$VINYL_VMODDIR/libvmod_cachetag.so"
sha256sum "$VINYL_VMODDIR/libvmod_cachetag.so"
[ "$(command -v vinyltest)" = /usr/bin/vinyltest ] ||
	die "vinyltest is not the packaged /usr/bin/vinyltest"
dpkg -S /usr/bin/vinyltest | grep -q '^vinyl-cache:' ||
	die "/usr/bin/vinyltest is not owned by the vinyl-cache package"
echo "OK: single packaged .so at $VINYL_VMODDIR, packaged vinyltest driver"

note "3 -- unpack the pinned cachetag release archive"
# The exact versioned filename, never a glob: stale archives of earlier
# versions can coexist in a local dist/ directory.
tarball=/out/libvmod-cachetag_$CACHETAG_VERSION.orig.tar.gz
echo "$CACHETAG_SOURCE_SHA256  $tarball" | sha256sum -c - ||
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
	-p vmod_path="$VINYL_VMODDIR" \
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
	-p vmod_path="$VINYL_VMODDIR" \
	-p debug=+vclrel \
	vtc/cachetag_pm00007.vtc 2>&1 | tee /tmp/vtc-pm00007.log || pm_status=$?
if [ "$pm_status" -ne 0 ]; then
	if grep -q 'HTTP rx timeout' /tmp/vtc-pm00007.log &&
	   grep -q 'HTC eof' /tmp/vtc-pm00007.log; then
		echo "pm00007 failed with the known load-flake signature; retrying once"
		pm_status=0
		vinyltest -v -j1 -t 60 \
			-p vmod_path="$VINYL_VMODDIR" \
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
