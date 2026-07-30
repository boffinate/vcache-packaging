#!/bin/bash
#
# The GENERIC source harness: build one VMOD from its own source tree against a
# Vinyl development prefix, then run the VTCs that VMOD declares, against what
# the build produced.
#
# Nothing in this file is specific to any VMOD. The only per-VMOD input is
# VMOD_TESTS, a glob out of the manifest's source-harness lane; everything else
# -- how to bootstrap, where the built shared object lands, what environment
# autoreconf needs -- is a property of autotools and is discovered. That is the
# shape survey/harness/build-and-load.sh proved across sixty third-party VMODs
# before this lane existed, promoted here and extended with the suite.
#
# Why generic rather than each VMOD's own script: at the roadmap's ~40-VMOD
# ambition a per-VMOD harness invocation is forty cross-repository couplings to
# keep working, and it wastes the shared engine build -- every one of those
# scripts would build Vinyl again for itself. See
# docs/20260730_0935_note_step-8-wave-3c-trunk-early-warning.md.
#
# Mount contract (set by the calling workflow):
#   /src     the VMOD source checkout, read-only
#   /prefix  the unpacked Vinyl trunk prefix is ALREADY at its own absolute
#            path; this is the path, passed as VINYL_PREFIX
#   /out     logs and the machine-readable outcome
#
# Environment:
#   VMOD_ID        the catalog id, for logging only
#   VMOD_TESTS     glob of VTCs to run, relative to the source root
#   VINYL_PREFIX   the Vinyl development prefix to build against
#
# Exit codes are the classification the workflow reads:
#   0  passed
#   12 bootstrap/configure/build failed   -> failed_source_harness
#   13 no VMOD shared object was produced -> failed_source_harness
#   14 the declared test glob matched nothing -> failed_source_harness
#   15 the VTC suite failed                -> failed_source_harness
# They are distinguished in the LOG rather than in the status, because all of
# them are the same finding: this VMOD's source does not work against today's
# Vinyl trunk, and the first place to look is Vinyl.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

: "${VMOD_ID:?}" "${VMOD_TESTS:?}" "${VINYL_PREFIX:?}"

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit "${2:-12}"; }

mkdir -p /out/logs

note "the engine surface this build sees"
export PATH="$VINYL_PREFIX/bin:$VINYL_PREFIX/sbin:$PATH"
export PKG_CONFIG_PATH="$VINYL_PREFIX/lib/pkgconfig"
export LD_LIBRARY_PATH="$VINYL_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[ -d "$VINYL_PREFIX" ] || die "no Vinyl prefix at $VINYL_PREFIX" 12
pkg-config --exists vinylapi || die "$VINYL_PREFIX advertises no vinylapi.pc" 12
printf 'vinylapi     : %s\n' "$(pkg-config --modversion vinylapi)"
printf 'vmoddir      : %s\n' "$(pkg-config --variable=vmoddir vinylapi)"
printf 'vinyld       : %s\n' "$(command -v vinyld || echo '(absent)')"
printf 'vinyltest    : %s\n' "$(command -v vinyltest || echo '(absent)')"
command -v vinyltest >/dev/null || die "the prefix carries no vinyltest" 12

# Many VMODs put ${VINYLAPI_DATAROOTDIR}/aclocal (or the varnish spelling) in
# ACLOCAL_AMFLAGS. autoreconf expands it from the environment, and an unset
# variable becomes a bogus `-I /aclocal` that fails in a way that names
# nothing. Exported under every spelling in use, which is what the survey
# sweep had to learn across sixty repositories.
VINYLAPI_DATAROOTDIR=$(pkg-config --variable=datarootdir vinylapi 2>/dev/null || true)
VARNISHAPI_DATAROOTDIR=$VINYLAPI_DATAROOTDIR
LIBVINYLAPI_DATAROOTDIR=$VINYLAPI_DATAROOTDIR
LIBVARNISHAPI_DATAROOTDIR=$VINYLAPI_DATAROOTDIR
export VINYLAPI_DATAROOTDIR VARNISHAPI_DATAROOTDIR
export LIBVINYLAPI_DATAROOTDIR LIBVARNISHAPI_DATAROOTDIR
export ACLOCAL_PATH="$VINYLAPI_DATAROOTDIR/aclocal${ACLOCAL_PATH:+:$ACLOCAL_PATH}"
printf 'datarootdir  : %s\n' "$VINYLAPI_DATAROOTDIR"

note "copy the source out of the read-only mount"
work=/tmp/work
rm -rf "$work"
cp -a /src "$work" || die "could not copy the source tree" 12
chmod -R u+w "$work"
cd "$work"

note "bootstrap"
# The order the survey sweep settled on: a repository's own bootstrap entry
# point first, because it may compute include paths this script cannot, and
# plain autoreconf as the fallback for the ones that ship neither.
if [ ! -f configure ]; then
	if [ -f bootstrap ]; then
		sh ./bootstrap 2>&1 | tee /out/logs/bootstrap.log || autoreconf -f -i 2>&1 | tee -a /out/logs/bootstrap.log
	elif [ -f autogen.sh ]; then
		sh ./autogen.sh 2>&1 | tee /out/logs/bootstrap.log || autoreconf -f -i 2>&1 | tee -a /out/logs/bootstrap.log
	else
		autoreconf -f -i 2>&1 | tee /out/logs/bootstrap.log
	fi
fi
[ -f configure ] || die "no configure after bootstrap" 12

note "configure"
# Second chance through autoreconf: an autogen.sh frequently leaves aux files
# (compile, missing, depcomp) or archive macros uninstalled, and the retry is
# cheaper than the class of failure it removes.
./configure 2>&1 | tee /out/logs/configure.log ||
	{ autoreconf -f -i && ./configure 2>&1 | tee -a /out/logs/configure.log; } ||
	die "configure failed against Vinyl trunk" 12

note "make"
# Sequential retry: several VMODs have vmodtool rules whose prerequisites are
# not fully declared, which is correct at -j1 and a race above it.
make -j"$(nproc)" 2>&1 | tee /out/logs/make.log ||
	make 2>&1 | tee -a /out/logs/make.log ||
	die "build failed against Vinyl trunk" 12

note "locate what was built"
# No -type filter: libtool leaves the .so as a symlink for some modules.
sos=$(find . -path '*/.libs/libvmod_*.so' | sort)
[ -n "$sos" ] || die "the build produced no libvmod_*.so" 13
printf '%s\n' "$sos"
# Every directory holding one, joined -- a VMOD that builds more than one
# module needs all of them reachable, and vmod_path takes a list.
vmod_path=$(printf '%s\n' "$sos" | while IFS= read -r so; do
	(cd "$(dirname "$so")" && pwd)
done | sort -u | paste -sd: -)
printf 'vmod_path    : %s\n' "$vmod_path"

note "the declared VTC suite: $VMOD_TESTS"
# The glob is expanded HERE, inside the source tree, so a manifest cannot reach
# outside the checkout: GLOB_RE forbids a leading slash and `..`, and this
# expansion is rooted at the copy.
# shellcheck disable=SC2086 # the declared glob is a deliberate pattern
ls -1 $VMOD_TESTS > /tmp/vtc-ledger 2>/dev/null || true
count=$(grep -c . /tmp/vtc-ledger || true)
[ "${count:-0}" -gt 0 ] ||
	die "the declared test glob '$VMOD_TESTS' matched nothing in this source tree.
A harness row that runs no case cannot report a result: fix harness.tests in the
manifest, or find out why the suite moved." 14
printf 'cases        : %s\n' "$count"
sed 's/^/  /' /tmp/vtc-ledger

note "run the suite against the freshly built module"
# -k so one failing case does not stop the rest: the value of an early-warning
# run is the whole picture of what trunk broke, not the first thing it broke.
# -t 60 and debug=+vclrel match the installed-package suites in
# scripts/ci/lib/vtc-suite.sh; the flag is harmless where the VCL-release fix
# is present and necessary where it is not, and trunk is exactly where nobody
# can say in advance which it is.
status=0
# shellcheck disable=SC2046 # the ledger is a deliberate word list
vinyltest -v -k -j1 -t 60 \
	-p vmod_path="$vmod_path" \
	-p debug=+vclrel \
	$(cat /tmp/vtc-ledger) 2>&1 | tee /out/logs/vtc.log || status=$?

passed=$(grep -c 'TEST .* passed' /out/logs/vtc.log || true)
failed=$(grep -c 'TEST .* FAILED' /out/logs/vtc.log || true)
skipped=$(grep -c 'TEST .* skipped' /out/logs/vtc.log || true)
printf '\nHARNESS SUMMARY: %s/%s passed, %s failed, %s skipped (vinyltest exit %s)\n' \
	"$passed" "$count" "$failed" "$skipped" "$status"

{
	printf 'vmod=%s\n' "$VMOD_ID"
	printf 'cases=%s\n' "$count"
	printf 'passed=%s\n' "$passed"
	printf 'failed=%s\n' "$failed"
	printf 'skipped=%s\n' "$skipped"
	printf 'vinyltest_exit=%s\n' "$status"
} > /out/harness-summary.env

[ "$status" -eq 0 ] || die "the VTC suite failed against Vinyl trunk" 15
# A pass count short of the ledger means a case neither passed nor failed,
# which is indistinguishable from a case that never ran. The installed-package
# suite asserts the same thing for the same reason.
[ "$passed" -eq "$count" ] ||
	die "passed $passed of $count cases; a case that neither passed nor failed was skipped" 15

note "$VMOD_ID builds and passes its suite against Vinyl trunk"
