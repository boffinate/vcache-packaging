#!/bin/sh
#
# Assert that a build actually used the compiler flags it was supposed to use,
# by reading the build log the lane captured rather than by looking for a
# symptom in the linked object.
#
#   check-build-flags.sh LOGFILE FLAG [FLAG...]
#
# WHY THIS EXISTS. Wave B run 30409242057 failed the Debian row on
#
#   FAIL  stack-protector    absent
#
# for a package whose compile lines read `... -fstack-protector-strong -c
# vmod_dict.c ...`. The check looked for a `__stack_chk_fail` reference, and
# -fstack-protector-strong instruments only functions that have something worth
# a canary. vmod_dict.c has none, so GCC emitted no reference and the check
# reported the flag as missing. Absence of that symbol means "no function
# needed one", not "the flag was off"; cachetag passes the identical check only
# because cachetag's source happens to have such buffers. A check whose verdict
# depends on the shape of the source is not a check of the build.
#
# So the flag is asserted where it is actually stated. relro, BIND_NOW and PIC
# stay binary-level assertions in the verify scripts, because those ARE
# properties of the linked object and are source-independent.
#
# WHY `libtool: compile:`. That prefix is libtool's echo of the real compiler
# invocation for one of the package's own translation units. Selecting on it
# gets exactly the lines that matter and nothing else: configure's conftest
# compiles never go through libtool, so they cannot dilute the check, and
# nothing has to be excluded by name. It is a property of the autotools adapter
# -- every VMOD it packages builds a `vmod_LTLIBRARIES` through libtool -- and a
# future adapter with a different build system needs its own selector here
# rather than a loosened one.
#
# Requiring at least one such line is half the check. A log with no compile
# lines at all would otherwise pass vacuously, which is the failure mode that
# makes a flag assertion worthless.

set -eu

die() {
	printf 'FAIL: %s\n' "$*" >&2
	exit 1
}

log=${1:-}
[ -n "$log" ] || die "usage: check-build-flags.sh LOGFILE FLAG [FLAG...]"
shift
[ $# -gt 0 ] || die "usage: check-build-flags.sh LOGFILE FLAG [FLAG...]"

[ -f "$log" ] || die "no build log at $log.
The hardening evidence for this package is the build log, so a missing log is
a failed check and not a reason to skip one. The Debian lane writes it in
build-deb.sh; the EL9 lane copies mock's own build.log in build-rpm.sh's EXIT
trap."

compiles=$(grep -F 'libtool: compile:' "$log" || true)
[ -n "$compiles" ] || die "no compile lines in $log.
Nothing in this log records a compiler invocation for one of the package's own
translation units, so it proves nothing about the flags the build used."

count=$(printf '%s\n' "$compiles" | wc -l | tr -d ' ')
printf 'compile lines for the package''s own objects, from %s:\n' "$log"
printf '%s\n' "$compiles"
printf 'ledger: %s compile lines\n' "$count"

status=0
for flag in "$@"; do
	missing=$(printf '%s\n' "$compiles" | { grep -v -F -e "$flag" || true; })
	if [ -n "$missing" ]; then
		printf 'FAIL  %-28s absent from %s of %s compile lines:\n' \
			"$flag" "$(printf '%s\n' "$missing" | wc -l | tr -d ' ')" "$count"
		printf '%s\n' "$missing"
		status=1
	else
		printf 'PASS  %-28s present on all %s compile lines\n' "$flag" "$count"
	fi
done

[ "$status" -eq 0 ] || die "the build did not use the required flags on every
translation unit. This is never fixed by relaxing the assertion: either the
generated recipe stopped requesting the distribution's hardening policy, or the
upstream build system is overriding it."

printf 'BUILD-FLAG ASSERTION: PASS\n'
