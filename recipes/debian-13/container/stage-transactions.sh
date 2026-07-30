#!/bin/bash
#
# One upgrade-transaction scenario, in one fresh container.
#
# The plan's "Upgrade transaction safety" section requires real transactions
# from a retained previous cohort to a candidate cohort, and requires recording
# for each one whether the resolver upgrades the whole cohort, holds Vinyl
# back, refuses the transaction, or proposes removing a VMOD.
#
# Structure of every scenario:
#
#   1. build a local apt repository containing only the BASELINE cohort;
#   2. install the baseline cohort through apt, so dpkg's own dependency state
#      is what a real installation would have;
#   3. publish the synthetic candidate into the same repository and apt update,
#      which is exactly what a security update landing in a stable repository
#      looks like to the client;
#   4. optionally apply an incident-response measure (apt-mark hold, apt pin);
#   5. run ONE transaction command and capture its full output and exit code;
#   6. record the resulting installed state, whether the VMOD shared object
#      survived, and whether vinyld can still compile a VCL that imports it.
#
# Nothing is shared between scenarios: transactions.sh runs this script in a
# throwaway container per scenario, so an outcome cannot contaminate the next.
#
# The scenario table is about the RESOLVER, so nothing in it is specific to one
# VMOD: the transactions act on the engine candidates and the assertions are
# about what happened to the VMOD that was installed alongside them. Which VMOD
# that is arrives in the environment, defaulted throughout to libvmod-cachetag's
# values, so the cachetag lane runs exactly what it ran before Step 8.
#
# Required environment:
#   SCENARIO            scenario id, used for logging
#   TRANSACTION         the command under test, run through `bash -c`
#   CANDIDATE_VARIANT   mismatch | sameabi | none
#   CANDIDATE_VERSION   the candidate Debian version (ignored when none)
#   BASE_VERSION        the baseline cohort Vinyl version
#   BASE_ABI            the baseline strict ABI hash
#   VMOD_VERSION        the baseline VMOD package's Debian version
#   DEB_HOST_ARCH       target architecture
#   VINYL_VMODDIR       the runtime VMOD directory
# Optional:
#   WITH_DEV=1          also install vinyl-cache-dev in the baseline
#   PRE_STEP            a command run after the candidate is published and
#                       before the transaction (hold/pin procedures)
# Optional, and defaulted to libvmod-cachetag's values:
#   VMOD_PACKAGE        the VMOD's binary package name
#   VMOD_IMPORT         the VCL import token, which is what `vinyld -C` resolves
#                       to VMOD_SO through vmod_path
#   VMOD_SO             the installed shared object
#   VMOD_PROBE_VCL      path, INSIDE the container, of the VCL `vinyld -C`
#                       compiles before and after the transaction. Empty is
#                       meaningful and is what a generated VMOD passes: the
#                       script then composes a bare `import` probe from
#                       VMOD_IMPORT, which is all the question needs -- vinyld
#                       loads the shared object and its VCC-generated symbols to
#                       compile an import at all. cachetag's default is its own
#                       reviewed probe, the one this lane has always used.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C
export NO_COLOR=1

: "${SCENARIO:?}" "${TRANSACTION:?}" "${CANDIDATE_VARIANT:?}"
: "${BASE_VERSION:?}" "${BASE_ABI:?}" "${VMOD_VERSION:?}"
: "${DEB_HOST_ARCH:?}" "${VINYL_VMODDIR:?}"
CANDIDATE_VERSION=${CANDIDATE_VERSION:-}
WITH_DEV=${WITH_DEV:-0}
PRE_STEP=${PRE_STEP:-}
VMOD_PACKAGE=${VMOD_PACKAGE:-libvmod-cachetag}
VMOD_IMPORT=${VMOD_IMPORT:-cachetag}
VMOD_SO=${VMOD_SO:-libvmod_cachetag.so}
# `-` and not `:-`: an explicitly EMPTY path is a caller's decision -- it selects
# the composed bare-import probe -- and must not fall back to cachetag's.
VMOD_PROBE_VCL=${VMOD_PROBE_VCL-/stage/probe-cachetag.vcl}

hdr() { printf '\n########## %s ##########\n' "$*"; }

ver() { dpkg-query -W -f='${Version}' "$1" 2>/dev/null || printf 'none'; }
inst() {
	# "installed version" or "none": dpkg keeps config-files entries around
	# after a remove, and those must not read as still installed.
	_s=$(dpkg-query -W -f='${Status} ${Version}' "$1" 2>/dev/null) || { printf 'none'; return; }
	case $_s in
	# "hold" is a selection state, not an installation state: a held package is
	# still installed and must not read as absent.
	"install ok installed "*|"hold ok installed "*) printf '%s' "${_s##* }" ;;
	*) printf 'none' ;;
	esac
}

hdr "SCENARIO $SCENARIO"
echo "transaction:        $TRANSACTION"
echo "candidate variant:  $CANDIDATE_VARIANT"
echo "candidate version:  ${CANDIDATE_VERSION:-(none)}"
echo "baseline version:   $BASE_VERSION"
echo "baseline ABI:       vinyld-abi-$BASE_ABI"
echo "VMOD package:       $VMOD_PACKAGE $VMOD_VERSION"
echo "VMOD import token:  $VMOD_IMPORT ($VMOD_SO)"
echo "vinyl-cache-dev in the baseline: $WITH_DEV"
echo "pre-step:           ${PRE_STEP:-(none)}"
apt --version

###############################################################################
hdr "1 -- local apt repository with the BASELINE cohort only"
###############################################################################
mkdir -p /repo
cp "/out/vinyl-cache_${BASE_VERSION}_${DEB_HOST_ARCH}.deb" /repo/
cp "/out/vinyl-cache-dev_${BASE_VERSION}_${DEB_HOST_ARCH}.deb" /repo/
cp "/out/${VMOD_PACKAGE}_${VMOD_VERSION}_${DEB_HOST_ARCH}.deb" /repo/
( cd /repo && dpkg-scanpackages --multiversion . > Packages 2>/dev/null && gzip -9kf Packages )
printf 'deb [trusted=yes] file:/repo ./\n' > /etc/apt/sources.list.d/vinyl-cohort.list
apt-get update -qq
echo "--- repository contents ---"
grep -E '^(Package|Version|Provides|Depends):' /repo/Packages

###############################################################################
hdr "2 -- install the baseline cohort through apt"
###############################################################################
if [ "$WITH_DEV" = 1 ]; then
	apt-get install -y vinyl-cache vinyl-cache-dev "$VMOD_PACKAGE"
else
	apt-get install -y vinyl-cache "$VMOD_PACKAGE"
fi
baseline_rc=$?
[ "$baseline_rc" -eq 0 ] || { echo "E: baseline cohort install failed"; exit 1; }

vinyl_before=$(inst vinyl-cache)
dev_before=$(inst vinyl-cache-dev)
vmod_before=$(inst "$VMOD_PACKAGE")
echo "installed baseline: vinyl-cache=$vinyl_before vinyl-cache-dev=$dev_before $VMOD_PACKAGE=$vmod_before"
[ "$vinyl_before" = "$BASE_VERSION" ] || { echo "E: baseline vinyl-cache version wrong"; exit 1; }
[ "$vmod_before" = "$VMOD_VERSION" ] || { echo "E: baseline $VMOD_PACKAGE version wrong"; exit 1; }

if [ -n "$VMOD_PROBE_VCL" ]; then
	[ -f "$VMOD_PROBE_VCL" ] || { echo "E: no probe VCL at $VMOD_PROBE_VCL"; exit 1; }
	cp "$VMOD_PROBE_VCL" /tmp/probe.vcl
else
	{
		printf 'vcl 4.1;\n\nimport %s;\n\n' "$VMOD_IMPORT"
		printf 'backend default {\n    .host = "127.0.0.1";\n    .port = "8080";\n}\n'
	} > /tmp/probe.vcl
fi
echo "--- probe VCL ---"
cat /tmp/probe.vcl

if vinyld -C -f /tmp/probe.vcl >/tmp/vcl-before.out 2>&1; then
	vcl_before=ok
else
	vcl_before=fail
	cat /tmp/vcl-before.out
fi
echo "baseline VCL compile with 'import $VMOD_IMPORT': $vcl_before"
[ "$vcl_before" = ok ] || { echo "E: the baseline itself cannot compile the probe VCL"; exit 1; }

###############################################################################
hdr "3 -- publish the synthetic candidate into the same repository"
###############################################################################
if [ "$CANDIDATE_VARIANT" = none ]; then
	echo "(no candidate: control scenario, the repository stays at the baseline)"
else
	cp "/out/mismatch/vinyl-cache_${CANDIDATE_VERSION}_${DEB_HOST_ARCH}.deb" /repo/
	cp "/out/mismatch/vinyl-cache-dev_${CANDIDATE_VERSION}_${DEB_HOST_ARCH}.deb" /repo/
	( cd /repo && dpkg-scanpackages --multiversion . > Packages 2>/dev/null && gzip -9kf Packages )
	apt-get update -qq
	echo "--- candidate as apt sees it ---"
	apt-cache policy vinyl-cache
	echo "--- what the candidate provides ---"
	apt-cache show "vinyl-cache=$CANDIDATE_VERSION" | grep -E '^(Package|Version|Provides|Depends):'
	echo "--- what still provides the baseline strict ABI ---"
	apt-cache showpkg "vinyld-abi-$BASE_ABI" | sed -n '1,10p'
	echo "--- apt list --upgradable ---"
	apt list --upgradable 2>/dev/null
fi

###############################################################################
hdr "4 -- incident-response pre-step"
###############################################################################
if [ -n "$PRE_STEP" ]; then
	echo "\$ $PRE_STEP"
	bash -c "$PRE_STEP"
	echo "pre-step exit: $?"
	echo "--- apt-mark showhold ---"
	apt-mark showhold
	echo "--- apt-cache policy vinyl-cache after the pre-step ---"
	apt-cache policy vinyl-cache
else
	echo "(none)"
fi

###############################################################################
hdr "5 -- THE TRANSACTION"
###############################################################################
echo "\$ $TRANSACTION"
bash -c "$TRANSACTION" </dev/null 2>&1
tx_exit=$?
echo "--- transaction exit code: $tx_exit ---"

###############################################################################
hdr "6 -- resulting state"
###############################################################################
vinyl_after=$(inst vinyl-cache)
dev_after=$(inst vinyl-cache-dev)
vmod_after=$(inst "$VMOD_PACKAGE")
echo "vinyl-cache:        $vinyl_before -> $vinyl_after"
echo "vinyl-cache-dev:    $dev_before -> $dev_after"
printf '%-19s %s -> %s\n' "$VMOD_PACKAGE:" "$vmod_before" "$vmod_after"
echo "--- dpkg -l for the cohort ---"
dpkg -l vinyl-cache vinyl-cache-dev "$VMOD_PACKAGE" 2>/dev/null | tail -n +6

if [ -f "$VINYL_VMODDIR/$VMOD_SO" ]; then
	vmod_so=present
else
	vmod_so=absent
fi
echo "VMOD shared object in $VINYL_VMODDIR: $vmod_so"
ls -la "$VINYL_VMODDIR/" 2>/dev/null || echo "(vmod directory is gone)"

if [ -x /usr/sbin/vinyld ]; then
	if vinyld -C -f /tmp/probe.vcl >/tmp/vcl-after.out 2>&1; then
		vcl_after=ok
	else
		vcl_after=fail
	fi
	echo "--- vinyld -C -f probe.vcl (import $VMOD_IMPORT) ---"
	head -n 20 /tmp/vcl-after.out
else
	vcl_after=no-vinyld
fi
echo "post-transaction VCL compile with 'import $VMOD_IMPORT': $vcl_after"

###############################################################################
hdr "7 -- classification"
###############################################################################
# The four outcomes the plan asks to distinguish, plus the two that only exist
# because an incident-response measure was applied.
if [ "$vmod_after" = none ]; then
	outcome="REMOVED-VMOD"
elif [ "$CANDIDATE_VARIANT" = none ]; then
	outcome="NO-OP"
elif [ "$vinyl_after" = "$CANDIDATE_VERSION" ]; then
	outcome="UPGRADED-VINYL-VMOD-KEPT"
elif [ "$vinyl_after" = "$vinyl_before" ] && [ "$tx_exit" -eq 0 ]; then
	outcome="HELD-BACK"
elif [ "$vinyl_after" = "$vinyl_before" ]; then
	outcome="REFUSED"
else
	outcome="UNCLASSIFIED"
fi

# A VMOD-removing outcome is the dangerous one the plan wants flagged; so is a
# state where the daemon survives but can no longer compile its own VCL.
if [ "$outcome" = "REMOVED-VMOD" ] || [ "$vcl_after" != ok ]; then
	danger=YES
else
	danger=no
fi

{
	printf 'RESULT scenario=%s\n' "$SCENARIO"
	printf 'RESULT candidate=%s\n' "$CANDIDATE_VARIANT"
	printf 'RESULT candidate_version=%s\n' "${CANDIDATE_VERSION:-none}"
	printf 'RESULT prestep=%s\n' "${PRE_STEP:-none}"
	printf 'RESULT command=%s\n' "$TRANSACTION"
	printf 'RESULT exit=%s\n' "$tx_exit"
	printf 'RESULT vmod_package=%s\n' "$VMOD_PACKAGE"
	printf 'RESULT vinyl=%s->%s\n' "$vinyl_before" "$vinyl_after"
	printf 'RESULT dev=%s->%s\n' "$dev_before" "$dev_after"
	printf 'RESULT vmod=%s->%s\n' "$vmod_before" "$vmod_after"
	printf 'RESULT vmod_so=%s\n' "$vmod_so"
	printf 'RESULT vcl_compile=%s\n' "$vcl_after"
	printf 'RESULT outcome=%s\n' "$outcome"
	printf 'RESULT needs_warning=%s\n' "$danger"
} | tee /out/logs/transactions/"$SCENARIO".result

exit 0
