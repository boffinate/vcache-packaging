# Shared installed-package behaviour-suite runner. Sourced, never executed.
#
# ONE implementation of "run the ported VTCs against the packaged .so", used by
# both generated-recipe verify stages. It is staged into the lane by
# scripts/ci/vmod/generate.sh beside the verify scripts, for the same reason
# package-checks.sh is: those stages mount only the lane.
#
# WHY IT IS FUNCTIONS WITH ARGUMENTS AND NOT A SCRIPT WITH A VMOD IN IT.
# Nothing in this file names a VMOD, a file extension, a vinyltest macro or a
# test driver. Every one of those is declared in the VMOD's overlay under
# `behaviour:` and arrives here as an argument, rendered by
# `vmod_recipe.py lane-env` beside the package names. Step 7 Wave 1 replaced
# dict's hardcoded values with that contract and added a second consumer whose
# shape is entirely different -- redis stages upstream's own suite runner out of
# the archive and is driven one VTC at a time -- which is what makes "generic"
# a measurement rather than an intention. Adding a per-VMOD branch to any
# function here would be the wrong fix for the same problem.

# shellcheck shell=bash

# vtc_stage_fixtures ARCHIVE SHA256 ROOT PATTERNS DEST
#
# Unpack the digest-verified upstream release archive and copy the fixtures the
# suite needs out of it. The fixtures are NOT copied into this repository: there
# is one copy of the oracle's input data and it is upstream's, so it cannot
# drift from the expectations the ported VTCs assert.
#
# ROOT is the directory inside the archive the patterns are relative to, and
# PATTERNS is a deliberately word-split list of globs beneath it. A match keeps
# its path relative to ROOT, so a fixture in a subdirectory arrives in one --
# upstream's suite runner resolves its own assets relative to itself, and a
# flattened copy would leave it looking for files that are no longer there.
vtc_stage_fixtures() {
	_vtc_archive=$1
	_vtc_sha=$2
	_vtc_root=$3
	_vtc_glob=$4
	_vtc_dest=$5

	echo "$_vtc_sha  $_vtc_archive" | sha256sum -c - || {
		printf 'FAIL: source archive digest mismatch\n' >&2
		return 1
	}
	rm -rf /tmp/upstream
	mkdir -p /tmp/upstream "$_vtc_dest"
	tar -C /tmp/upstream --strip-components=1 -xzf "$_vtc_archive"

	_vtc_base=/tmp/upstream/$_vtc_root
	[ -d "$_vtc_base" ] || {
		printf 'FAIL: the declared fixture root "%s" is not a directory in the release archive.\n' \
			"$_vtc_root" >&2
		return 1
	}

	_vtc_staged=0
	for _vtc_pattern in $_vtc_glob; do
		# shellcheck disable=SC2086 # the pattern is expanded on purpose
		for _vtc_file in $_vtc_base/$_vtc_pattern; do
			[ -e "$_vtc_file" ] || continue
			_vtc_rel=${_vtc_file#"$_vtc_base"/}
			mkdir -p "$_vtc_dest/$(dirname "$_vtc_rel")"
			cp -pR "$_vtc_file" "$_vtc_dest/$_vtc_rel"
			_vtc_staged=$((_vtc_staged + 1))
		done
	done
	[ "$_vtc_staged" -gt 0 ] || {
		printf 'FAIL: the declared fixture pattern "%s" matched nothing under %s in the release archive.\n' \
			"$_vtc_glob" "$_vtc_root" >&2
		printf 'A suite that runs without its fixtures tests whatever the VCL does when they are missing.\n' >&2
		return 1
	}
	printf 'fixtures staged: %s file(s) from %s/{%s}\n' \
		"$_vtc_staged" "$_vtc_root" "$_vtc_glob"
}

# vtc_run_suite VTC_DIR VMODDIR FIXTURE_DIR DRIVER MACROS
#
# Every VTC in VTC_DIR must run and pass; none may be skipped. The ledger is
# built first and compared against the count of passes, because vinyltest
# exiting 0 having silently skipped everything is the failure mode a suite is
# least likely to notice.
#
# DRIVER is `none` for a suite vinyltest can run by itself, or a path relative
# to FIXTURE_DIR naming an executable staged out of the release archive that
# takes the test driver as its first argument and one VTC as its last. The
# second form exists because some suites address fixture SERVERS rather than
# fixture files: upstream's runner launches them, computes the -D macros that
# describe where they are, and tears them down again, so it has to be given one
# VTC at a time.
#
# MACROS is a word-split list of NAME=VALUE passed as -D. The literal token
# @FIXTURES@ is replaced with FIXTURE_DIR; everything else goes through
# unchanged. That one substitution covers both shapes seen so far -- a suite
# that wants to be told where its data files are, and one whose macro is a
# constant -- without this file knowing which is which.
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
	_vtc_fixtures=$3
	_vtc_driver=$4
	_vtc_macros=$5

	find "$_vtc_dir" -maxdepth 1 -name '*.vtc' | sort >/tmp/vtc-ledger
	_vtc_count=$(wc -l </tmp/vtc-ledger | tr -d ' ')
	[ "$_vtc_count" -gt 0 ] || {
		printf 'FAIL: no ported VTCs were staged\n' >&2
		return 1
	}
	printf 'ledger: %s VTCs\n' "$_vtc_count"

	# The -D arguments, built once. Word splitting is the interface.
	_vtc_dflags=""
	for _vtc_macro in $_vtc_macros; do
		_vtc_dflags="$_vtc_dflags -D$(printf '%s' "$_vtc_macro" |
			sed "s|@FIXTURES@|$_vtc_fixtures|g")"
	done
	printf 'macros: %s\n' "${_vtc_dflags# }"
	printf 'driver: %s\n' "$_vtc_driver"

	: >/tmp/vtc.log
	_vtc_status=0
	if [ "$_vtc_driver" = none ]; then
		# shellcheck disable=SC2046,SC2086 # the ledger and the -D flags are deliberate word lists
		vinyltest -v -k -j1 -t 60 \
			-p vmod_path="$_vtc_vmoddir" \
			-p debug=+vclrel \
			$_vtc_dflags \
			$(cat /tmp/vtc-ledger) 2>&1 | tee -a /tmp/vtc.log || _vtc_status=$?
	else
		_vtc_run=$_vtc_fixtures/$_vtc_driver
		[ -x "$_vtc_run" ] || {
			printf 'FAIL: the declared suite driver %s is not executable\n' "$_vtc_run" >&2
			return 1
		}
		while IFS= read -r _vtc_case; do
			printf '\n--- %s\n' "$(basename "$_vtc_case")" | tee -a /tmp/vtc.log
			_vtc_one=0
			# shellcheck disable=SC2086 # the -D flags are a deliberate word list
			"$_vtc_run" vinyltest -v -k -j1 -t 60 \
				-p vmod_path="$_vtc_vmoddir" \
				-p debug=+vclrel \
				$_vtc_dflags \
				"$_vtc_case" 2>&1 | tee -a /tmp/vtc.log || _vtc_one=$?
			[ "$_vtc_one" -eq 0 ] || _vtc_status=$_vtc_one
		done </tmp/vtc-ledger
	fi

	_vtc_passed=$(grep -c 'TEST .* passed' /tmp/vtc.log || true)
	_vtc_skipped=$(grep -c 'TEST .* skipped' /tmp/vtc.log || true)
	[ "$_vtc_status" -eq 0 ] || {
		printf 'FAIL: vinyltest reported failures\n' >&2
		return 1
	}
	# One pass line per ledger entry. On the driver path this is also what
	# catches a driver that decided by itself not to run a case and exited 0 --
	# upstream's runner does exactly that below a minimum fixture version. A
	# skip nobody declared is indistinguishable from a test that never ran,
	# which is the whole reason the count is asserted rather than the status.
	[ "$_vtc_passed" -eq "$_vtc_count" ] || {
		printf 'FAIL: passed %s of %s VTCs. A case that neither passed nor failed was\n' \
			"$_vtc_passed" "$_vtc_count" >&2
		printf 'skipped by the driver or by vinyltest; the suite must run completely.\n' >&2
		return 1
	}
	[ "$_vtc_skipped" -eq 0 ] || {
		printf 'FAIL: %s VTCs skipped; the suite must run completely\n' "$_vtc_skipped" >&2
		return 1
	}
	printf 'VTC-SUITE SUMMARY: %s/%s passed, 0 skipped\n' "$_vtc_passed" "$_vtc_count"
}

# vtc_install_packages MANAGER PACKAGES
#
# The fixture packages the overlay declares its behaviour suite needs -- a
# database server the VTCs talk to, for instance. Empty for a VMOD whose suite
# needs nothing but the engine, and then this is a no-op rather than a branch in
# the caller.
vtc_install_packages() {
	_vtc_mgr=$1
	shift
	[ $# -gt 0 ] || {
		printf 'behaviour fixture packages: none declared\n'
		return 0
	}
	printf 'behaviour fixture packages: %s\n' "$*"
	case $_vtc_mgr in
	apt) apt-get install -y --no-install-recommends "$@" >/dev/null ;;
	dnf) dnf -y -q install "$@" >/dev/null ;;
	*)
		printf 'FAIL: unknown package manager %s\n' "$_vtc_mgr" >&2
		return 1
		;;
	esac
}
