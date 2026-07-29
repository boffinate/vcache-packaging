#!/bin/bash
#
# Payload, ABI, hardening, rpmlint, installed-package smoke and behaviour for
# one generated-recipe VMOD's RPM. Runs in a FRESH almalinux:9 container that
# has never seen a build tree, so the subject is the installed package.
#
# Mount contract (set by ../verify-rpm.sh):
#   /lane   out/ the built RPMs, engine/ the verified engine RPMs,
#           tests/ the ported VTCs, src/ the verified upstream archive
#
# The same check families as the Debian half, expressed in RPM's vocabulary:
# the Requires are the arch-qualified vinyld() capabilities recipes/el9/
# find-provides injects on the runtime package, and the payload is compared
# against the overlay's declaration rather than trusted.

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
contents=$(rpm -qpl "$rpm")
printf '%s\n' "$contents"
printf '%s\n' "$contents" | grep -qxF "$VINYL_VMODDIR/$VMOD_OBJECT" ||
	die "$VMOD_OBJECT is not in $VINYL_VMODDIR"
printf '%s\n' "$contents" | grep -q "/share/man/$VMOD_MAN_PAGE" ||
	die "the declared manual page $VMOD_MAN_PAGE is missing"
stray=$(printf '%s\n' "$contents" | { grep -E '\.(la|a)$' || true; })
[ -z "$stray" ] || die "libtool archive or static library shipped: $stray"
echo "OK: no libtool archives or static libraries"
# Nothing may be installed outside the VMOD directory, the manual, the
# documentation and the licence tree -- plus the packaging's own artefacts,
# which are what B3 and B6 were both about.
#
# /usr/lib/.build-id/** is RPM's debuginfo hard-link farm. redhat-rpm-config's
# find-debuginfo adds it to the MAIN package of every debuginfo-enabled build,
# so it is present in every EL9 package this lane will ever produce; the
# allowlist was written from the overlay's declared payload and forgot the
# packaging's own output. Directly the twin of B3's lintian-overrides file on
# the Debian side, which is why both allowlists were swept together rather than
# this one being fixed alone -- see the Wave B note's allowlist-symmetry table.
#
# `rpm -qpl` prints owned directories indistinguishably from files, so the
# directory entries are matched here rather than filtered by shape the way
# `dpkg-deb -c`'s trailing slash lets the Debian half do.
#
# Every filter is guarded with `|| true`: an allowlist that happens to select
# nothing is a passing check, not a pipeline failure, and under `pipefail` an
# unguarded `grep -v` that filters everything out would abort the script with a
# success-shaped payload.
unexpected=$(printf '%s\n' "$contents" |
	{ grep -vF "$VINYL_VMODDIR/$VMOD_OBJECT" || true; } |
	{ grep -vE '^/usr/lib/\.build-id(/|$)' || true; } |
	{ grep -vE '^/usr/share/(man|doc|licenses)/' || true; })
[ -z "$unexpected" ] || die "unexpected files in the payload:
$unexpected"
echo "OK: payload contains only the declared object, manual, documentation, licence and RPM's build-id links"

note "4 -- the VMOD advertises no soname provide"
# It is a dlopen()ed plugin, not a system library. The generated spec's
# __provides_exclude_from suppresses the automatic soname provide; assert it,
# because a stray one would make the plugin look like a shared library that
# something could link against.
provides=$(rpm -qp --provides "$rpm")
printf '%s\n' "$provides"
printf '%s\n' "$provides" | grep -q "^${VMOD_OBJECT}" &&
	die "$VMOD_OBJECT is advertised as a soname provide"
echo "OK: no soname provide for the plugin"

note "5 -- hardening inspection"
# The same split as the Debian half, for the same reason: relro, BIND_NOW and
# PIC are properties of the linked object, and the stack protector is a property
# of the compile line. Asserted from mock's own build.log, which build-rpm.sh's
# EXIT trap copies into the lane. See check-build-flags.sh.
sh /lane/scripts/check-build-flags.sh /lane/logs/mock-build.log \
	-fstack-protector-strong ||
	die "the build did not apply the distribution hardening flags; see above"

mkdir -p /tmp/x && (cd /tmp/x && rpm2cpio "$rpm" | cpio -idm --quiet)
so=/tmp/x$VINYL_VMODDIR/$VMOD_OBJECT
fail=0
check() {
	if [ "$1" -eq 0 ]; then printf 'PASS  %-18s %s\n' "$2" "$3"; else
		printf 'FAIL  %-18s %s\n' "$2" "$3"
		fail=1
	fi
}
dyn=$(readelf -W --dyn-syms --syms "$so" 2>/dev/null || true)
seg=$(readelf -W -l "$so" 2>/dev/null || true)
dynm=$(readelf -W -d "$so" 2>/dev/null || true)
hdr=$(readelf -W -h "$so" 2>/dev/null || true)
case "$dyn" in
*__stack_chk_fail*)
	printf 'PASS  %-18s %s\n' stack-protector "__stack_chk_fail referenced (corroborating)" ;;
*)
	printf 'NOTE  %-18s %s\n' stack-protector \
		"no canary symbol: no function in this source needs one. Not a failure -- the flag is asserted from the build log above." ;;
esac
case "$seg" in *GNU_RELRO*) check 0 relro-segment "GNU_RELRO present" ;; *) check 1 relro-segment absent ;; esac
case "$dynm" in *BIND_NOW* | *NOW*) check 0 bind-now "BIND_NOW set" ;; *) check 1 bind-now absent ;; esac
case "$hdr" in *"Type:"*DYN*) check 0 pic "ELF type DYN" ;; *) check 1 pic "not DYN" ;; esac
[ "$fail" -eq 0 ] || die "hardening inspection failed"
echo "HARDENING INSPECTION: PASS"

note "6 -- rpmlint, with an explicit expectation"
# Not `|| true`, and stricter than the cachetag lane's waiver file. A generated
# recipe has no excuse for a diagnostic nobody reviewed: either the templates
# or the overlay is wrong, or it belongs in the overlay's reviewed overrides.
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
echo "$VMOD_SOURCE_SHA256  $archive" | sha256sum -c - || die "source archive digest mismatch"
mkdir -p /tmp/upstream /tmp/fixtures
tar -C /tmp/upstream --strip-components=1 -xzf "$archive"
cp -v /tmp/upstream/tests/*.dict /tmp/fixtures/
find /lane/tests -maxdepth 1 -name '*.vtc' | sort >/tmp/vtc-ledger
count=$(wc -l </tmp/vtc-ledger | tr -d ' ')
[ "$count" -gt 0 ] || die "no ported VTCs were staged"
echo "ledger: $count VTCs"
# debug=+vclrel ("Rapid VCL release", include/tbl/debug_bits.h, present in both
# 9.0.1 and trunk) makes workers release their cached VCL reference after every
# task, so vcl->busy is zero at stop and every VTC teardown's CLI stop
# completes promptly. Needed because 9.0.1 lacks 7de492b0e8 ("Shut down pools
# when stopping"): pools are not shut down on stop, so idle workers hold their
# VCL refs through a 60s cond-wait, and with -t 60 that is a timeout rather
# than a slow teardown. Ported from
# recipes/debian-13/container/stage-vtc-suite.sh:90-98; remove when the release
# track reaches a Vinyl containing 7de492b0e8.
status=0
# shellcheck disable=SC2046
vinyltest -v -k -j1 -t 60 \
	-p vmod_path="$VINYL_VMODDIR" \
	-p debug=+vclrel \
	-Ddictdir=/tmp/fixtures \
	$(cat /tmp/vtc-ledger) 2>&1 | tee /tmp/vtc.log || status=$?
passed=$(grep -c 'TEST .* passed' /tmp/vtc.log || true)
skipped=$(grep -c 'TEST .* skipped' /tmp/vtc.log || true)
[ "$status" -eq 0 ] || die "vinyltest reported failures"
[ "$passed" -eq "$count" ] || die "passed $passed of $count VTCs"
[ "$skipped" -eq 0 ] || die "$skipped VTCs skipped; the suite must run completely"
printf 'VTC-SUITE SUMMARY: %s/%s passed, 0 skipped\n' "$passed" "$count"

note "verify-rpm complete"
