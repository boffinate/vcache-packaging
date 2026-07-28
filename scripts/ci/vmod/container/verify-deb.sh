#!/bin/bash
#
# Payload, ABI, hardening, lint, installed-package smoke and behaviour for one
# generated-recipe VMOD's Debian package. Runs in a FRESH debian:trixie
# container that has never seen a build tree, so the subject is the installed
# package and nothing else.
#
# Mount contract (set by ../verify-deb.sh):
#   /lane   out/ the built .debs, engine/ the verified engine .debs,
#           tests/ the ported VTCs, src/ the verified upstream archive
#   /meta   names.json and the generated recipe's generation-record.json
#
# The check families are cachetag's, applied to a generated package: the
# payload is exactly what the overlay declared, the ABI and cohort dependencies
# are the ones the registry generated, the hardening flags survived the
# recipe, lint has an explicit expectation rather than a shrug, the runtime
# pair alone can load the VMOD, and upstream's own test expectations pass
# against the installed .so.

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
# Read the listing into a variable first. `dpkg-deb -c | grep -q` makes grep
# exit on the first match, dpkg-deb dies of SIGPIPE, and under pipefail the
# pipeline reports failure even though the file WAS found. That trap already
# cost a run in the cachetag work; do not reintroduce it.
contents=$(dpkg-deb -c "$deb")
printf '%s\n' "$contents"
case "$contents" in
*"$VINYL_VMODDIR/$VMOD_OBJECT"*) echo "OK: $VMOD_OBJECT is packaged into $VINYL_VMODDIR" ;;
*) die "$VMOD_OBJECT is not in $VINYL_VMODDIR" ;;
esac
case "$contents" in
*"/usr/share/man/$VMOD_MAN_PAGE"*) echo "OK: the declared manual page is packaged" ;;
*) die "the declared manual page /usr/share/man/$VMOD_MAN_PAGE is missing" ;;
esac
stray=$(printf '%s\n' "$contents" | { grep -E '\.(la|a)$' || true; })
[ -z "$stray" ] || die "libtool archive or static library shipped: $stray"
echo "OK: no libtool archives or static libraries"
# Nothing may be installed outside the VMOD directory and the documentation
# and manual trees. A generated recipe with a wrong payload declaration would
# otherwise ship whatever `make install` happened to produce.
# The recipe's own lintian override file is named exactly, not allowed by
# directory: debhelper installs debian/<binary>.lintian-overrides at
# /usr/share/lintian/overrides/<binary>, and cachetag ships one too. Narrowness
# is this check's entire value, so it gets the one path it is owed and not the
# directory it sits in.
unexpected=$(printf '%s\n' "$contents" | awk '{ print $NF }' |
	grep -v '/$' |
	grep -vF "$VINYL_VMODDIR/$VMOD_OBJECT" |
	grep -vFx "./usr/share/lintian/overrides/$VMOD_BINARY_NAME" |
	{ grep -vE '^\./usr/share/(man|doc)/' || true; })
[ -z "$unexpected" ] || die "unexpected files in the payload:
$unexpected"
echo "OK: payload contains only the declared VMOD object, manual and documentation"

note "4 -- hardening inspection"
mkdir -p /tmp/x && dpkg-deb -x "$deb" /tmp/x
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
case "$dyn" in *__stack_chk_fail*) check 0 stack-protector "__stack_chk_fail referenced" ;; *) check 1 stack-protector absent ;; esac
case "$seg" in *GNU_RELRO*) check 0 relro-segment "GNU_RELRO present" ;; *) check 1 relro-segment absent ;; esac
case "$dynm" in *BIND_NOW* | *NOW*) check 0 bind-now "BIND_NOW set" ;; *) check 1 bind-now absent ;; esac
case "$hdr" in *"Type:"*DYN*) check 0 pic "ELF type DYN" ;; *) check 1 pic "not DYN" ;; esac
[ "$fail" -eq 0 ] || die "hardening inspection failed"
echo "HARDENING INSPECTION: PASS"

note "5 -- lintian, with an explicit expectation"
# Not `|| true`. The generated recipe carries its own overrides for the two
# tags every package of this shape emits; anything else is a finding about the
# generator and has to be seen.
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
found=$(find / \( -path /proc -o -path /sys \) -prune -o -name "$VMOD_OBJECT" -print 2>/dev/null || true)
[ "$found" = "$VINYL_VMODDIR/$VMOD_OBJECT" ] ||
	die "$VMOD_OBJECT is not uniquely at \$VINYL_VMODDIR (found: $found)"
dpkg -S "$VINYL_VMODDIR/$VMOD_OBJECT"
[ "$(command -v vinyltest)" = /usr/bin/vinyltest ] || die "vinyltest is not the packaged one"
echo "OK: runtime pair installed, single packaged .so, packaged vinyltest driver"

note "7 -- behaviour: upstream's own expectations against the installed package"
# The fixture is upstream's tests/num.dict, taken from the verified release
# archive rather than copied into this repository, so there is one copy of it
# and the oracle cannot drift.
archive=$(find /lane/src -maxdepth 1 -name "*.tar.gz" | sort | head -1)
echo "$VMOD_SOURCE_SHA256  $archive" | sha256sum -c - || die "source archive digest mismatch"
mkdir -p /tmp/upstream /tmp/fixtures
tar -C /tmp/upstream --strip-components=1 -xzf "$archive"
cp -v /tmp/upstream/tests/*.dict /tmp/fixtures/
ls -1 /lane/tests/*.vtc >/tmp/vtc-ledger
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

note "verify-deb complete"
