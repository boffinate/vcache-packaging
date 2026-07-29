# Shared installed-package behaviour-suite runner. Sourced, never executed.
#
# ONE implementation of "run the ported VTCs against the packaged .so", used by
# both generated-recipe verify stages. It is staged into the lane by
# scripts/ci/vmod/generate.sh beside the verify scripts, for the same reason
# package-checks.sh is: those stages mount only the lane.
#
# WHY IT IS A FUNCTION WITH ARGUMENTS AND NOT A SCRIPT WITH A VMOD IN IT.
# Step 7 Wave 1 adds a third VMOD and a declared fixture contract: the overlay
# will state which files out of the upstream archive are test fixtures and what
# macro the suite addresses them by, and the generator will pass those down the
# same way it passes the package names. Nothing in this file names a VMOD, a
# file extension or a macro, so that change replaces the values the caller
# computes and touches no logic here. Adding a per-VMOD branch to this function
# would be the wrong fix for the same problem.

# shellcheck shell=bash

# vtc_stage_fixtures ARCHIVE SHA256 GLOB DEST
#
# Unpack the digest-verified upstream release archive and copy the fixtures the
# suite needs out of it. The fixtures are NOT copied into this repository: there
# is one copy of the oracle's input data and it is upstream's, so it cannot
# drift from the expectations the ported VTCs assert.
#
# GLOB is relative to the top of the unpacked archive and is deliberately word
# split: a VMOD with fixtures in two places states two patterns.
vtc_stage_fixtures() {
	_vtc_archive=$1
	_vtc_sha=$2
	_vtc_glob=$3
	_vtc_dest=$4

	echo "$_vtc_sha  $_vtc_archive" | sha256sum -c - || {
		printf 'FAIL: source archive digest mismatch\n' >&2
		return 1
	}
	rm -rf /tmp/upstream
	mkdir -p /tmp/upstream "$_vtc_dest"
	tar -C /tmp/upstream --strip-components=1 -xzf "$_vtc_archive"

	_vtc_staged=0
	for _vtc_pattern in $_vtc_glob; do
		# shellcheck disable=SC2086 # the pattern is expanded on purpose
		for _vtc_file in /tmp/upstream/$_vtc_pattern; do
			[ -e "$_vtc_file" ] || continue
			cp -v "$_vtc_file" "$_vtc_dest/"
			_vtc_staged=$((_vtc_staged + 1))
		done
	done
	[ "$_vtc_staged" -gt 0 ] || {
		printf 'FAIL: the declared fixture pattern "%s" matched nothing in the release archive.\n' \
			"$_vtc_glob" >&2
		printf 'A suite that runs without its fixtures tests whatever the VCL does when they are missing.\n' >&2
		return 1
	}
	printf 'fixtures staged: %s file(s) from %s\n' "$_vtc_staged" "$_vtc_glob"
}

# vtc_run_suite VTC_DIR VMODDIR MACRO_NAME FIXTURE_DIR
#
# Every VTC in VTC_DIR must run and pass; none may be skipped. The ledger is
# built first and compared against the count of passes, because vinyltest
# exiting 0 having silently skipped everything is the failure mode a suite is
# least likely to notice.
#
# debug=+vclrel ("Rapid VCL release", include/tbl/debug_bits.h, present in both
# 9.0.1 and trunk) makes workers release their cached VCL reference after every
# task, so vcl->busy is zero at stop and every VTC teardown's CLI stop completes
# promptly. Needed because 9.0.1 lacks 7de492b0e8 ("Shut down pools when
# stopping"): pools are not shut down on stop, so idle workers hold their VCL
# refs through a 60s cond-wait, and with -t 60 that is a timeout rather than a
# slow teardown. The cachetag lane's own suites carry the same flag with the
# same reasoning in recipes/debian-13/container/stage-vtc-suite.sh and
# recipes/el9/vtc-suite/vtc-suite.sh; they are not merged into this function
# because they run under a different mount contract and drive a fixed suite
# rather than a declared one. Remove from all three when the release track
# reaches a Vinyl containing 7de492b0e8.
vtc_run_suite() {
	_vtc_dir=$1
	_vtc_vmoddir=$2
	_vtc_macro=$3
	_vtc_fixtures=$4

	find "$_vtc_dir" -maxdepth 1 -name '*.vtc' | sort >/tmp/vtc-ledger
	_vtc_count=$(wc -l </tmp/vtc-ledger | tr -d ' ')
	[ "$_vtc_count" -gt 0 ] || {
		printf 'FAIL: no ported VTCs were staged\n' >&2
		return 1
	}
	printf 'ledger: %s VTCs\n' "$_vtc_count"

	_vtc_status=0
	# shellcheck disable=SC2046 # the ledger is a deliberate word list of paths
	vinyltest -v -k -j1 -t 60 \
		-p vmod_path="$_vtc_vmoddir" \
		-p debug=+vclrel \
		-D"$_vtc_macro=$_vtc_fixtures" \
		$(cat /tmp/vtc-ledger) 2>&1 | tee /tmp/vtc.log || _vtc_status=$?

	_vtc_passed=$(grep -c 'TEST .* passed' /tmp/vtc.log || true)
	_vtc_skipped=$(grep -c 'TEST .* skipped' /tmp/vtc.log || true)
	[ "$_vtc_status" -eq 0 ] || {
		printf 'FAIL: vinyltest reported failures\n' >&2
		return 1
	}
	[ "$_vtc_passed" -eq "$_vtc_count" ] || {
		printf 'FAIL: passed %s of %s VTCs\n' "$_vtc_passed" "$_vtc_count" >&2
		return 1
	}
	[ "$_vtc_skipped" -eq 0 ] || {
		printf 'FAIL: %s VTCs skipped; the suite must run completely\n' "$_vtc_skipped" >&2
		return 1
	}
	printf 'VTC-SUITE SUMMARY: %s/%s passed, 0 skipped\n' "$_vtc_passed" "$_vtc_count"
}
