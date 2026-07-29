# Shared package-inspection checks. Sourced, never executed.
#
# ONE implementation of the checks that inspect a built package, used by every
# lane regardless of which recipe strategy produced it:
#
#   scripts/ci/debian13/assert-packages.sh   cachetag, Debian, on the runner
#   scripts/ci/el9/container-mock.sh         cachetag, EL9, in the build container
#   scripts/ci/vmod/container/verify-deb.sh  generated recipes, Debian
#   scripts/ci/vmod/container/verify-rpm.sh  generated recipes, EL9
#
# WHY THIS FILE EXISTS. Step 6 Wave B took ten defects to reach a green
# baseline and three of them -- B3, B6 and B9 -- were the same shape: a lesson
# one backend's script had learned and the other had not. The measured cost of
# that duplication was a CI round trip each. This file is where those lessons
# live once. If a check has to change, it changes here, and every lane that
# inspects a package changes with it.
#
# The two delivery mechanisms are different and the file is the same. The
# cachetag lanes reach it through a mount of scripts/ci (or a repository
# checkout on the runner); the generated-recipe lane's verify stages mount only
# their work directory, so scripts/ci/vmod/generate.sh copies this file and
# check-build-flags.sh into lane/scripts/ beside the verify scripts. PC_LIB_DIR
# resolves from this file's own location, so check-build-flags.sh is found in
# either layout without the caller knowing which one it is in.

# shellcheck shell=bash

PC_LIB_DIR=${PC_LIB_DIR:-$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}

###############################################################################
# Build-flag assertion
###############################################################################
#
# The distribution hardening policy this project asserts, from the compile
# lines the build actually issued. Both flags were measured present on every
# `libtool: compile:` line of all four green rows of run 30437775658 --
# cachetag and dict, Debian and EL9 -- before being written here.
#
#   -fstack-protector-strong  the flag B5 moved out of the linked object. The
#                             canary symbol it produces exists only if some
#                             function had a buffer worth instrumenting, so its
#                             absence is a fact about the source and not about
#                             the build.
#   -D_FORTIFY_SOURCE=2       the same defect class, unnoticed until this wave:
#                             the `__*_chk` symbols the old check looked for
#                             appear only if the translation unit called a
#                             fortifiable libc function. Written without the
#                             `-Wp,` prefix EL9's redhat-rpm-config uses,
#                             because the check is a substring match and
#                             `-Wp,-D_FORTIFY_SOURCE=2` contains it; Debian
#                             spells it bare.
#
# A flag added here must be verified present in a real captured build log for
# every row that will assert it. A flag assertion that fails on a correct
# package is the exact failure mode B5 was.
PC_REQUIRED_BUILD_FLAGS="-fstack-protector-strong -D_FORTIFY_SOURCE=2"

# pc_assert_build_flags LOGFILE
#
# Non-zero if the log does not show every required flag on every compile line
# of the package's own translation units. See check-build-flags.sh for why the
# `libtool: compile:` prefix is the selector and why at least one such line is
# required.
pc_assert_build_flags() {
	# shellcheck disable=SC2086 # PC_REQUIRED_BUILD_FLAGS is a deliberate word list
	sh "$PC_LIB_DIR/check-build-flags.sh" "$1" $PC_REQUIRED_BUILD_FLAGS
}

###############################################################################
# ELF hardening inspection
###############################################################################

_pc_check() { # STATUS LABEL DETAIL
	if [ "$1" -eq 0 ]; then
		printf 'PASS  %-18s %s\n' "$2" "$3"
	else
		printf 'FAIL  %-18s %s\n' "$2" "$3"
		_pc_fail=1
	fi
}

# pc_verify_build ELF LABEL log FILE
# pc_verify_build ELF LABEL nolog REASON
#
# The B5 ruling, expressed so it cannot be applied by halves.
#
# relro, BIND_NOW and PIC are properties of the LINKED OBJECT: the linker either
# produced them or it did not, whatever the source looks like. They are asserted
# against the binary in both forms.
#
# The stack protector and _FORTIFY_SOURCE are properties of the COMPILE LINE.
# Their symptoms in the binary -- `__stack_chk_fail` and the `__*_chk` family --
# appear only if some function in this particular source needed instrumenting.
# So they are demoted to corroboration ONLY in the `log` form, where the flags
# themselves have just been asserted from the build log. In the `nolog` form
# there is no build log for this package in this row and the symbol checks stay
# fatal, because a weaker check is better than none and silently demoting one
# without a replacement is how a check stops testing anything.
#
# Passing the log path is therefore not an option a caller can forget: the two
# forms are named, the `log` form fails if the log is absent, and the `nolog`
# form must say why in words that end up in the job log.
pc_verify_build() {
	_pc_elf=$1
	_pc_label=$2
	_pc_mode=$3
	_pc_arg=${4:-}
	_pc_fail=0

	[ -f "$_pc_elf" ] || {
		printf 'FAIL  %-18s no such file: %s\n' "$_pc_label" "$_pc_elf"
		return 1
	}

	case $_pc_mode in
	log)
		[ -n "$_pc_arg" ] || {
			printf 'FAIL  %-18s pc_verify_build log form needs a log path\n' "$_pc_label"
			return 1
		}
		pc_assert_build_flags "$_pc_arg" || return 1
		;;
	nolog)
		[ -n "$_pc_arg" ] || {
			printf 'FAIL  %-18s pc_verify_build nolog form needs a reason\n' "$_pc_label"
			return 1
		}
		printf 'NOTE  %-18s no build log in this row (%s);\n' \
			"$_pc_label" "$_pc_arg"
		printf '      %-18s the compile-line evidence is unavailable, so the canary and\n' ""
		printf '      %-18s fortify SYMBOL checks below stay fatal rather than corroborating.\n' ""
		;;
	*)
		printf 'FAIL  %-18s unknown pc_verify_build mode "%s"\n' "$_pc_label" "$_pc_mode"
		return 1
		;;
	esac

	_pc_dyn=$(readelf -W --dyn-syms --syms "$_pc_elf" 2>/dev/null || true)
	_pc_seg=$(readelf -W -l "$_pc_elf" 2>/dev/null || true)
	_pc_dynm=$(readelf -W -d "$_pc_elf" 2>/dev/null || true)
	_pc_hdr=$(readelf -W -h "$_pc_elf" 2>/dev/null || true)

	case "$_pc_seg" in
	*GNU_RELRO*) _pc_check 0 relro-segment "GNU_RELRO present ($_pc_label)" ;;
	*) _pc_check 1 relro-segment "absent ($_pc_label)" ;;
	esac
	case "$_pc_dynm" in
	*BIND_NOW* | *NOW*) _pc_check 0 bind-now "BIND_NOW set ($_pc_label)" ;;
	*) _pc_check 1 bind-now "absent ($_pc_label)" ;;
	esac
	case "$_pc_hdr" in
	*"Type:"*DYN*) _pc_check 0 pic "ELF type DYN ($_pc_label)" ;;
	*) _pc_check 1 pic "not DYN ($_pc_label)" ;;
	esac

	_pc_chk=$({ printf '%s' "$_pc_dyn" | grep -oE '__[a-z0-9_]+_chk\b' || true; } |
		sort -u | tr '\n' ' ')
	case "$_pc_mode" in
	log)
		case "$_pc_dyn" in
		*__stack_chk_fail*)
			printf 'PASS  %-18s __stack_chk_fail referenced (corroborating)\n' stack-protector ;;
		*)
			printf 'NOTE  %-18s no canary symbol: no function in this source needs one.\n' stack-protector
			printf '      %-18s Not a failure -- the flag is asserted from the build log above.\n' "" ;;
		esac
		if [ -n "$_pc_chk" ]; then
			printf 'PASS  %-18s %s(corroborating)\n' fortify-source "$_pc_chk"
		else
			printf 'NOTE  %-18s no __*_chk symbols: no fortifiable libc call in this source.\n' fortify-source
			printf '      %-18s Not a failure -- the flag is asserted from the build log above.\n' ""
		fi
		;;
	nolog)
		case "$_pc_dyn" in
		*__stack_chk_fail*) _pc_check 0 stack-protector "__stack_chk_fail referenced ($_pc_label)" ;;
		*) _pc_check 1 stack-protector "absent ($_pc_label)" ;;
		esac
		if [ -n "$_pc_chk" ]; then
			_pc_check 0 fortify-source "$_pc_chk($_pc_label)"
		else
			_pc_check 1 fortify-source "no __*_chk symbols ($_pc_label)"
		fi
		;;
	esac

	[ "$_pc_fail" -eq 0 ] || return 1
	printf 'HARDENING INSPECTION: PASS (%s)\n' "$_pc_label"
}

###############################################################################
# Payload allowlists
###############################################################################
#
# One VMOD binary package may contain the packaged object, its manual page, its
# documentation, its licence text where the packaging system has a separate
# place for one, and the packaging's own generated artefacts -- and nothing
# else. Everything else is a declaration the recipe got wrong.
#
# Narrowness is this check's entire value, so the allowlist names paths rather
# than directories wherever the packaging system lets it: the recipe's own
# lintian override file is allowed by its exact name (B3), not by the directory
# it sits in.
#
# The two backends differ in three measured ways, each recorded so a future
# reader can tell a fact about the packaging system from an oversight in one of
# the two lists:
#
#   * debhelper puts the debug objects and their build-id links in a SEPARATE
#     -dbgsym binary package, so the Debian list deliberately has no build-id
#     rule. redhat-rpm-config's find-debuginfo puts /usr/lib/.build-id/** in the
#     MAIN package of every debuginfo-enabled build, so the RPM list must have
#     one (B6).
#   * `dpkg-deb -c` prints directory entries with a trailing slash, so they are
#     filtered by shape. `rpm -qpl` prints them indistinguishably from files,
#     so they are matched by path.
#   * RPM has %license and a /usr/share/licenses tree; Debian's licence text is
#     /usr/share/doc/<pkg>/copyright and is covered by the documentation rule.
#
# Every filter is guarded with `|| true`: an allowlist that happens to select
# nothing is a passing check, not a pipeline failure, and under `pipefail` an
# unguarded `grep -v` that filtered everything out would abort the caller with
# a success-shaped payload.

# pc_assert_deb_payload DEB BINARY_NAME VMODDIR OBJECT MAN_PAGE
pc_assert_deb_payload() {
	_pc_deb=$1
	_pc_binary=$2
	_pc_vmoddir=$3
	_pc_object=$4
	_pc_man=$5

	# Read the listing into a variable first. `dpkg-deb -c | grep -q` makes
	# grep exit on the first match, dpkg-deb dies of SIGPIPE, and under
	# pipefail the pipeline reports failure even though the file WAS found.
	# That trap already cost a run in the cachetag work; do not reintroduce it.
	_pc_contents=$(dpkg-deb -c "$_pc_deb")
	printf '%s\n' "$_pc_contents"

	case "$_pc_contents" in
	*"$_pc_vmoddir/$_pc_object"*)
		printf 'OK: %s is packaged into %s\n' "$_pc_object" "$_pc_vmoddir" ;;
	*)
		printf 'FAIL: %s is not in %s\n' "$_pc_object" "$_pc_vmoddir" >&2
		return 1 ;;
	esac
	case "$_pc_contents" in
	*"/usr/share/man/$_pc_man"*)
		printf 'OK: the declared manual page is packaged\n' ;;
	*)
		printf 'FAIL: the declared manual page /usr/share/man/%s is missing\n' "$_pc_man" >&2
		return 1 ;;
	esac

	_pc_stray=$(printf '%s\n' "$_pc_contents" | { grep -E '\.(la|a)$' || true; })
	[ -z "$_pc_stray" ] || {
		printf 'FAIL: libtool archive or static library shipped: %s\n' "$_pc_stray" >&2
		return 1
	}
	printf 'OK: no libtool archives or static libraries\n'

	_pc_unexpected=$(printf '%s\n' "$_pc_contents" | awk '{ print $NF }' |
		{ grep -v '/$' || true; } |
		{ grep -vF "$_pc_vmoddir/$_pc_object" || true; } |
		{ grep -vFx "./usr/share/lintian/overrides/$_pc_binary" || true; } |
		{ grep -vE '^\./usr/share/(man|doc)/' || true; })
	[ -z "$_pc_unexpected" ] || {
		printf 'FAIL: unexpected files in the payload:\n%s\n' "$_pc_unexpected" >&2
		return 1
	}
	printf 'OK: payload contains only the declared VMOD object, manual and documentation\n'
}

# pc_assert_rpm_payload RPM VMODDIR OBJECT MAN_PAGE
pc_assert_rpm_payload() {
	_pc_rpm=$1
	_pc_vmoddir=$2
	_pc_object=$3
	_pc_man=$4

	_pc_contents=$(rpm -qpl "$_pc_rpm")
	printf '%s\n' "$_pc_contents"

	printf '%s\n' "$_pc_contents" | grep -qxF "$_pc_vmoddir/$_pc_object" || {
		printf 'FAIL: %s is not in %s\n' "$_pc_object" "$_pc_vmoddir" >&2
		return 1
	}
	printf '%s\n' "$_pc_contents" | grep -q "/share/man/$_pc_man" || {
		printf 'FAIL: the declared manual page %s is missing\n' "$_pc_man" >&2
		return 1
	}

	_pc_stray=$(printf '%s\n' "$_pc_contents" | { grep -E '\.(la|a)$' || true; })
	[ -z "$_pc_stray" ] || {
		printf 'FAIL: libtool archive or static library shipped: %s\n' "$_pc_stray" >&2
		return 1
	}
	printf 'OK: no libtool archives or static libraries\n'

	_pc_unexpected=$(printf '%s\n' "$_pc_contents" |
		{ grep -vF "$_pc_vmoddir/$_pc_object" || true; } |
		{ grep -vE '^/usr/lib/\.build-id(/|$)' || true; } |
		{ grep -vE '^/usr/share/(man|doc|licenses)/' || true; })
	[ -z "$_pc_unexpected" ] || {
		printf 'FAIL: unexpected files in the payload:\n%s\n' "$_pc_unexpected" >&2
		return 1
	}
	printf 'OK: payload contains only the declared object, manual, documentation, licence and RPM build-id links\n'
}
