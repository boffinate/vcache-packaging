#!/bin/sh
#
# EL9 lane: the upgrade-transaction matrix.
#
# The plan requires every documented upgrade command to have a tested, recorded
# resolver outcome, and requires that any command capable of removing an
# imported VMOD be called out prominently. This script produces that evidence:
# for each dnf transaction in the plan's list, a FRESH almalinux:9-derived
# container installs the baseline cohort from a local repository, is then shown
# a mismatched candidate Vinyl, and runs the transaction for real. The installed
# state afterwards -- including whether a VCL that imports cachetag still
# compiles -- is the result.
#
# One container per scenario. Nothing is shared between scenarios and nothing
# runs on the host.
#
# Trust model: local repositories, unsigned, gpgcheck=0. This lane has no
# signing key. Whether signature checking changes any outcome below is untested
# and is CI work; it is recorded as a gap, not as a pass.
#
# Usage:
#   transactions.sh                      run the whole matrix
#   transactions.sh upgrade distro-sync  run named scenarios only
#   transactions.sh --no-prep ...        reuse the existing local repositories
#   transactions.sh --rebuild-image ...  force a rebuild of the scenario image
#
# Prerequisites: recipes/el9/build.sh (the baseline cohort) and
# recipes/el9/mismatch-fixture.sh (the candidate fixture) have both been run.
#
# Environment, all defaulted so the cachetag invocation is unchanged:
#   TXN_OUT_DIR      the directory mounted at /out by prep.sh. It must contain
#                    packages/ (the baseline cohort RPMs, engine and VMOD) and
#                    mismatch/packages/ (the fixture RPMs), and is where the
#                    repositories and logs are written. Defaults to dist/el9, the
#                    lane's own layout; the reusable workflow points it at a
#                    staging directory, because a generated VMOD's package lives
#                    in lane/out and the engine's in lane/engine.
#   VMOD_PACKAGE     the VMOD RPM name installed alongside the engine
#                    (libvmod-cachetag)
#   VMOD_IMPORT      its VCL import token (cachetag)
#   VMOD_PROBE_VCL   container path of the probe VCL; empty composes a bare
#                    `import VMOD_IMPORT` probe (see transactions/scenario.sh)

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)

. "$here/cohort.env"

image=${EL9_IMAGE:-almalinux:9}
txn_image=vinyl-el9-txn-base
out=${TXN_OUT_DIR:-$repo/dist/el9}

# Which VMOD is installed alongside the engine candidates. The scenario set is
# about the resolver and does not vary per VMOD; only the assertions about what
# survived do. Defaults are libvmod-cachetag's.
VMOD_PACKAGE=${VMOD_PACKAGE:-libvmod-cachetag}
VMOD_IMPORT=${VMOD_IMPORT:-cachetag}
VMOD_PROBE_VCL=${VMOD_PROBE_VCL-/recipes/smoke/smoke.vcl}
logs=$out/mismatch/logs
repos=$out/mismatch/repos

all_scenarios="sanity-candidate-installable
upgrade
upgrade-best
upgrade-nobest
upgrade-skip-broken
upgrade-allowerasing
upgrade-runtime-only
upgrade-allowerasing-runtime-only
upgrade-allowerasing-nobest
upgrade-targeted-allowerasing
distro-sync
distro-sync-allowerasing
install-candidate
install-candidate-allowerasing
versionlock
history-undo
same-abi
same-abi-targeted-allowerasing
same-abi-install-allowerasing"

do_prep=yes
rebuild_image=

while [ $# -gt 0 ]; do
	case $1 in
	--no-prep)       do_prep=; shift ;;
	--rebuild-image) rebuild_image=1; shift ;;
	-h|--help)       sed -n '2,30p' "$0"; exit 0 ;;
	*)               break ;;
	esac
done

scenarios=${*:-$all_scenarios}

[ -d "$out/mismatch/packages" ] || {
	printf 'no fixture in %s/mismatch/packages; run mismatch-fixture.sh first\n' "$out" >&2
	exit 2
}

mkdir -p "$logs"

printf '\n########## EL9 upgrade-transaction matrix ##########\n'
printf 'base image : %s\n' "$image"
printf 'VMOD       : %s (import %s)\n' "$VMOD_PACKAGE" "$VMOD_IMPORT"
printf 'packages   : %s\n' "$out"
printf 'scenarios  : %s\n' "$scenarios"
printf 'logs       : %s\n' "$logs"

# ------------------------------------------------------------- scenario image

if [ -n "$rebuild_image" ] || ! docker image inspect "$txn_image" >/dev/null 2>&1; then
	printf '\n===== build the scenario base image =====\n'
	docker build --pull -t "$txn_image" -f "$here/transactions/Dockerfile" "$here/transactions" \
		> "$logs/txn-image-build.log" 2>&1 || {
		tail -n 40 "$logs/txn-image-build.log" >&2
		printf 'scenario image build failed\n' >&2
		exit 1
	}
	tail -n 3 "$logs/txn-image-build.log"
fi
docker image inspect "$txn_image" --format 'scenario image: {{.Id}}' | tee "$out/mismatch/txn-image.txt"

# ------------------------------------------------------- local dnf repositories

if [ -n "$do_prep" ]; then
	printf '\n===== build the local repositories =====\n'
	docker run --rm \
		-v "$here:/recipes:ro" \
		-v "$out:/out" \
		"$txn_image" \
		bash /recipes/transactions/prep.sh 2>&1 | tee "$logs/txn-prep.log"
fi
[ -d "$repos/baseline/repodata" ] || {
	printf 'no repodata under %s; run without --no-prep\n' "$repos" >&2
	exit 2
}

# ------------------------------------------------------------------- scenarios

summary=$logs/summary.tsv
: > "$summary"

status=0
for s in $scenarios; do
	printf '\n===== scenario: %s =====\n' "$s"
	candidate_repo=candidate
	case $s in same-abi*) candidate_repo=sameabi ;; esac

	rc=0
	# VINYL_TRACK must cross the container boundary: scenario.sh re-sources
	# cohort.env inside the container, and without the variable its
	# baseline EVR falls back to the trunk pin block regardless of which
	# track built the repositories it is resolving against. Same
	# passthrough as build.sh's container invocations; prep.sh needs no
	# equivalent because it copies by glob and reads no pin.
	docker run --rm \
		-e "VINYL_TRACK=$VINYL_TRACK" \
		-e "CANDIDATE_REPO=$candidate_repo" \
		-e "VMOD_PACKAGE=$VMOD_PACKAGE" \
		-e "VMOD_IMPORT=$VMOD_IMPORT" \
		-e "VMOD_PROBE_VCL=$VMOD_PROBE_VCL" \
		-v "$here:/recipes:ro" \
		-v "$repos:/repos:ro" \
		"$txn_image" \
		bash /recipes/transactions/scenario.sh "$s" \
		> "$logs/txn-$s.log" 2>&1 || rc=$?

	if [ "$rc" -ne 0 ]; then
		printf 'scenario harness FAILED (exit %s); see %s\n' "$rc" "$logs/txn-$s.log" >&2
		tail -n 20 "$logs/txn-$s.log" >&2
		status=1
		continue
	fi

	sed -n 's/^SUMMARY\t//p' "$logs/txn-$s.log" >> "$summary"
	sed -n '/^===== SCENARIO RESULT/,$p' "$logs/txn-$s.log" | sed '/^SUMMARY/d'
done

# --------------------------------------------------------------------- matrix

printf '\n########## transaction matrix ##########\n'
printf 'scenario\tcommand\tdnf exit\tvinyl after\tcachetag\tVCL import\toutcome\tclass\n'
cat "$summary"

printf '\ncommands that removed an imported VMOD (prominent warning required):\n'
if grep -q 'WARNING-REQUIRED' "$summary"; then
	awk -F'\t' '$8 == "WARNING-REQUIRED" { printf "  %s\n", $2 }' "$summary"
else
	printf '  none\n'
fi

# ------------------------------------------------ pinned per-scenario outcomes
#
# Classification alone is not a gate: it scored dict's silent erasure and
# cachetag's refusal of the identical scenario as the same accepted class, so
# the two VMODs diverging produced no red anywhere
# (docs/20260730_1231_note_step-8-dict-el9-allowerasing-root-cause.md). Every
# scenario's outcome and dnf exit code is therefore pinned per VMOD in
# transactions/expected/<package>.tsv, and any difference -- a mismatch, an
# unpinned scenario, a pin the run did not produce -- fails the matrix. A
# legitimate outcome change (a package rename, a resolver fix) must update the
# pin file in the same review.
printf '\n########## pinned per-scenario expectations ##########\n'
subset=
[ "$scenarios" = "$all_scenarios" ] || subset=--subset
python3 "$repo/tools/ci_matrix.py" check-transaction-pins \
	--summary "$summary" \
	--expected "$here/transactions/expected/$VMOD_PACKAGE.tsv" \
	--format el9 $subset || status=1

exit "$status"
