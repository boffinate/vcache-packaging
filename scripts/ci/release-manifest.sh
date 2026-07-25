#!/bin/bash
#
# Assemble release-manifest.json and a merged RELEASE-SHA256SUMS for the
# internal draft GitHub Release (release-draft.yml's assemble-draft-release
# job). See DESIGN.md section 7.
#
# registry/README.md's "Deliberately not here yet" section says this file is
# "assembled by the release workflow from these manifests plus CI-only facts
# ... which cannot be checked in ahead of the run" -- this script is that
# assembly step. Since no `candidate` cohort manifest exists yet (checked-in
# cohorts are all `status: template`; see DESIGN.md open question #2), this
# falls back to the literal pinned values already hardcoded in
# recipes/debian-13/build.sh and recipes/el9/cohort.env, labelled
# "cohort_status": "unassigned-process-proof" rather than inventing a real
# cohort id CI has no authority to mint.
#
# Usage: release-manifest.sh ASSETS_DIR RUN_ID RUN_URL
#
# ASSETS_DIR must contain the three subdirectories release-draft.yml
# downloads its artifacts into: source-archive/, debian-13/, el9/.
#
# DRAFT, unexecuted -- see ../../DESIGN.md section 7.

set -euo pipefail

assets=${1:?ASSETS_DIR required}
run_id=${2:?RUN_ID required}
run_url=${3:?RUN_URL required}

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) # scripts/ci
. "$here/lib/common.sh"

[ -d "$assets/source-archive" ] || die "missing $assets/source-archive"
[ -d "$assets/debian-13" ] || die "missing $assets/debian-13"
[ -d "$assets/el9" ] || die "missing $assets/el9"

note "merging checksums into $assets/RELEASE-SHA256SUMS"
{
	printf '# Combined checksums, both lanes, one CI run.\n'
	printf '# Native package filenames as built -- NOT the plan release-asset\n'
	printf '# names (libvmod-cachetag-1.0.0-1-debian-13-amd64.deb), which require a\n'
	printf '# candidate cohort manifest that does not exist yet (open question #2).\n'
	printf '#\n'
	awk '{ print $1"  debian-13/"$2 }' "$assets/debian-13/SHA256SUMS"
	awk '{ print $1"  el9/"$2 }' "$assets/el9/SHA256SUMS"
	for f in "$assets/source-archive"/*.tar.gz; do
		[ -e "$f" ] || continue
		printf '%s  source-archive/%s\n' "$(sha256_file "$f")" "$(basename "$f")"
	done
} > "$assets/RELEASE-SHA256SUMS"
cat "$assets/RELEASE-SHA256SUMS"

note "writing $assets/release-manifest.json"

# Pinned values, read from the single definition in
# recipes/debian-13/pins.env rather than restated here. This script used to
# carry its own copy of every one of them, which made four copies of the
# cachetag digest in this repository; the 2026-07-25 re-pin had to find and
# move all four, and an earlier one missed a fifth (scripts/ci/debian13/pinned.sh).
. "$(CDPATH= cd -- "$here/../.." && pwd)/recipes/debian-13/pins.env"

vinyl_commit=$VINYL_GIT_COMMIT
vinyl_strict_abi=$VINYL_STRICT_ABI
vinyl_upstream_version=$VINYL_UPSTREAM_VERSION
vinyl_vrt=$VINYL_VRT_EXPECTED
cachetag_version=$CACHETAG_VERSION
cachetag_source_sha256=$CACHETAG_SOURCE_SHA256

cat > "$assets/release-manifest.json" <<JSON
{
  "schema": "vcache-packaging-release-manifest/v1-draft",
  "note": "Assembled by scripts/ci/release-manifest.sh. cohort_status is 'unassigned-process-proof' because no registry/cohorts/*.yml with status: candidate exists yet -- see DESIGN.md open question 2. This manifest's shape anticipates registry/README.md's documented, not-yet-implemented release-manifest.json emission and will need reconciling with the real generator once a candidate cohort exists.",
  "cohort_status": "unassigned-process-proof",
  "cachetag": {
    "version": "$cachetag_version",
    "source_archive_sha256": "$cachetag_source_sha256"
  },
  "vinyl": {
    "git_commit": "$vinyl_commit",
    "upstream_version": "$vinyl_upstream_version",
    "vrt": "$vinyl_vrt",
    "strict_abi": "$vinyl_strict_abi"
  },
  "storage_support": ["default"],
  "targets": ["debian-13-amd64", "el9-x86_64"],
  "channel": "draft",
  "publication_note": "Internal draft, not a public pre-release. See the step-10 gate decision (docs/20260725_1602_note_step-10-gate-decisions.md): a human/orchestrator publishes the real pre-release from a validated draft.",
  "ci": {
    "workflow": "release-draft.yml",
    "run_id": "$run_id",
    "run_url": "$run_url"
  },
  "checksums_file": "RELEASE-SHA256SUMS"
}
JSON
cat "$assets/release-manifest.json"
