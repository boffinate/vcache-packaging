#!/bin/bash
#
# Assemble the release upload directory, a merged RELEASE-SHA256SUMS and
# release-manifest.json for release-draft.yml's assemble-draft-release job.
#
# registry/README.md's "Deliberately not here yet" section says this file is
# "assembled by the release workflow from these manifests plus CI-only facts
# ... which cannot be checked in ahead of the run" -- this script is that
# assembly step.
#
# Until 2026-07-26 it could not do that: no cohort manifest had status
# `candidate`, so it fell back to the literal pinned values in the lane
# drivers under a "cohort_status": "unassigned-process-proof" label. The first
# real cohort has now been minted, so every identity below comes from the
# registry via tools/release_tool.py, and the lane pins are checked against it
# rather than copied.
#
# Usage: release-manifest.sh ASSETS_DIR RUN_ID RUN_URL [UPLOAD_DIR]
#
# ASSETS_DIR must contain the three subdirectories release-draft.yml downloads
# its artifacts into: source-archive/, debian-13/, el9/. They are separate
# directories because both lanes produce a file literally named SHA256SUMS and
# a shared download directory would let the second clobber the first.
#
# UPLOAD_DIR (default ASSETS_DIR/../upload) receives every file that becomes a
# release asset, flat. Assembling it here rather than in the workflow keeps the
# checksum file honest: RELEASE-SHA256SUMS lists the names the assets are
# actually published under, so `sha256sum -c` works in a directory of
# downloaded release assets. The workflow used to flatten the assets itself,
# with its own copy of each glob, while this script wrote lane-prefixed paths
# into the checksum file that matched nothing a user could download.

set -euo pipefail

assets=${1:?ASSETS_DIR required}
run_id=${2:?RUN_ID required}
run_url=${3:?RUN_URL required}
upload=${4:-}

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) # scripts/ci
repo=$(CDPATH= cd -- "$here/../.." && pwd)
. "$here/lib/common.sh"

assets=$(CDPATH= cd -- "$assets" && pwd)
[ -n "$upload" ] || upload=$(dirname -- "$assets")/upload

[ -d "$assets/source-archive" ] || die "missing $assets/source-archive"
[ -d "$assets/debian-13" ] || die "missing $assets/debian-13"
[ -d "$assets/el9" ] || die "missing $assets/el9"

###############################################################################
note "cohort identity"
###############################################################################
# Pinned values, read from the single definition in recipes/debian-13/pins.env
# rather than restated here. This script used to carry its own copy of every
# one of them, which made four copies of the cachetag digest in this
# repository; the 2026-07-25 re-pin had to find and move all four.
. "$repo/recipes/debian-13/pins.env"

# The EL9 lane keeps its own pins. The cohort id is the one value that must be
# identical in both, because it is baked into the Debian virtual package and
# the RPM capability that make a cohort a cohort. They used to hold two
# DIFFERENT placeholders, so assert rather than assume.
el9_cohort=$(
	# shellcheck disable=SC1091
	. "$repo/recipes/el9/cohort.env"
	printf '%s' "$COHORT_ID"
)
[ "$el9_cohort" = "$COHORT_ID" ] || die \
	"cohort id disagreement: recipes/debian-13/pins.env says '$COHORT_ID',
recipes/el9/cohort.env says '$el9_cohort'. Both lanes bake this into package
metadata; a release assembled from two different cohorts is not a cohort."

printf 'cohort: %s\n' "$COHORT_ID"

# Every generated name below comes from the registry, not from this script.
# `metadata` refuses to run against a template manifest without
# --allow-template, so this also proves a real cohort manifest backs the id
# the lanes just built with.
metadata_for() {
	python3 "$repo/tools/release_tool.py" \
		--cachetag-src "${CACHETAG_SRC:-$repo/../libvmod-cachetag}" \
		metadata --cohort "$COHORT_ID" --target "$1" --format shell
}

deb_meta=$(metadata_for debian-13-amd64) ||
	die "no registry target manifest for $COHORT_ID/debian-13-amd64"
rpm_meta=$(metadata_for el9-x86_64) ||
	die "no registry target manifest for $COHORT_ID/el9-x86_64"

deb_native=$(printf '%s\n' "$deb_meta" | sed -n "s/^CACHETAG_ARTIFACTS_NATIVE_FILENAME='\(.*\)'$/\1/p")
deb_asset=$(printf '%s\n' "$deb_meta" | sed -n "s/^CACHETAG_ARTIFACTS_RELEASE_ASSET_FILENAME='\(.*\)'$/\1/p")
rpm_native=$(printf '%s\n' "$rpm_meta" | sed -n "s/^CACHETAG_ARTIFACTS_NATIVE_FILENAME='\(.*\)'$/\1/p")
rpm_asset=$(printf '%s\n' "$rpm_meta" | sed -n "s/^CACHETAG_ARTIFACTS_RELEASE_ASSET_FILENAME='\(.*\)'$/\1/p")
source_archive=$(printf '%s\n' "$deb_meta" | sed -n "s/^CACHETAG_SOURCE_ARCHIVE='\(.*\)'$/\1/p")
deb_depends=$(printf '%s\n' "$deb_meta" | sed -n "s/^CACHETAG_ABI_DEB_DEPENDS='\(.*\)'$/\1/p")
rpm_cohort_provide=$(printf '%s\n' "$rpm_meta" | sed -n "s/^CACHETAG_ABI_RPM_COHORT_PROVIDE='\(.*\)'$/\1/p")
deb_cohort_provide=$(printf '%s\n' "$deb_meta" | sed -n "s/^CACHETAG_ABI_COHORT_PROVIDE='\(.*\)'$/\1/p")

for v in deb_native deb_asset rpm_native rpm_asset source_archive; do
	eval "[ -n \"\$$v\" ]" || die "release_tool.py metadata did not yield $v"
done

###############################################################################
note "release-readiness gate"
###############################################################################
# The registry's own answer to "is there something publishable here?". A
# release must not be able to claim more evidence than the manifests record,
# so the gate runs here, next to the assembly, and not only as an early
# workflow step that a later change could quietly stop feeding.
#
# RELEASE_ALLOW_INCOMPLETE_EVIDENCE=1 downgrades it for an explicitly
# experimental pre-release -- but it does not make the shortfall disappear:
# every failing check is copied verbatim into release-manifest.json as
# evidence_gaps, so the published artefact says what it is missing.
gaps=""
if python3 "$repo/tools/release_tool.py" \
	--cachetag-src "${CACHETAG_SRC:-$repo/../libvmod-cachetag}" \
	validate --require-releasable > "$assets/validate-releasable.log" 2>&1
then
	printf 'OK: the registry reports cohort %s releasable\n' "$COHORT_ID"
else
	cat "$assets/validate-releasable.log"
	gaps=$(sed -n 's/^ERROR *//p' "$assets/validate-releasable.log")
	[ -n "$gaps" ] || gaps="validate --require-releasable failed without naming a check"
	if [ "${RELEASE_ALLOW_INCOMPLETE_EVIDENCE:-0}" != 1 ]; then
		die "the registry does not describe a releasable cohort (see above).
Fix the evidence, or dispatch with allow_incomplete_evidence for an explicitly
experimental pre-release, which records every gap in release-manifest.json."
	fi
	printf '\nW: assembling an experimental release with %d recorded evidence gap(s)\n' \
		"$(printf '%s\n' "$gaps" | wc -l | tr -d ' ')"
fi

###############################################################################
note "assembling $upload"
###############################################################################
rm -rf "$upload"
mkdir -p "$upload"

# The per-artifact selection mirrors each lane job's upload-artifact globs.
# Lane SHA256SUMS files are deliberately excluded: the merged
# RELEASE-SHA256SUMS written below is the release asset, and two files with
# the same name cannot both be published anyway.
# The canonical archive by name, never a glob: release-source-archive.sh also
# leaves libvmod-cachetag-X.Y.Z.dist-raw.tar.gz beside it, which is the
# pre-canonicalisation intermediate and must not be published as if it were
# the release source.
[ -f "$assets/source-archive/$source_archive" ] ||
	die "no $source_archive in $assets/source-archive"
cp -p "$assets/source-archive/$source_archive" "$upload/"
for extra in "$assets/source-archive/$source_archive.sha256" \
	"$assets"/source-archive/*.metadata.json; do
	case "$extra" in *dist-raw*) continue ;; esac
	[ -e "$extra" ] && cp -p "$extra" "$upload/"
done
cp -p "$assets"/debian-13/*.deb "$upload/"
cp -p "$assets"/debian-13/*.dsc "$assets"/debian-13/*.changes \
	"$assets"/debian-13/*.buildinfo "$assets"/debian-13/*.tar.* "$upload/"
cp -p "$assets"/el9/packages/*.rpm "$upload/"

# The native names are kept as the published names, deliberately, even though
# registry/README.md also generates a distro-bearing release-asset name
# (libvmod-cachetag-1.0.1-1-debian-13-amd64.deb). Renaming the .deb would break
# the .changes and .buildinfo files published beside it, which reference the
# native filename and its digest, and dpkg/apt tooling expects that name. The
# generated asset name is recorded in release-manifest.json instead, so it is
# still published, as data rather than as a filename that contradicts its own
# metadata. Both native names already carry the architecture, and the RPM
# carries the dist tag, so nothing is ambiguous in a flat release.
###############################################################################
note "renaming assets GitHub would rename anyway"
###############################################################################
# GitHub rewrites characters outside [A-Za-z0-9._-] in a release asset name.
# The Vinyl snapshot version contains a tilde -- 9.0.0~git20260520.25761f8505,
# deliberately, because ~ sorts below a future real 9.0.0 in both dpkg and rpm
# -- so every Vinyl asset was published as ...9.0.0.git..., while
# RELEASE-SHA256SUMS named it ...9.0.0~git.... `sha256sum -c` then failed on
# every one of them, which is the same defect as the lane-prefixed paths this
# script was already fixing, arriving from a different direction (observed on
# draft-20260726T074622Z).
#
# So rename here, before the checksums are computed, and let the checksum file
# describe what is actually downloadable. The package version inside the
# metadata is untouched: apt and dnf read it from the control header, not from
# the filename, and release-manifest.json still records the true version.
for f in "$upload"/*; do
	b=$(basename -- "$f")
	safe=$(printf '%s' "$b" | tr -c 'A-Za-z0-9._-' '.')
	if [ "$b" != "$safe" ]; then
		printf 'renaming %s -> %s\n' "$b" "$safe"
		mv "$f" "$upload/$safe"
	fi
done
# The cachetag names carry no tilde, so these are unchanged by the rename and
# release-manifest.json's filenames stay true. Assert rather than assume.
for n in "$deb_native" "$rpm_native" "$source_archive"; do
	safe=$(printf '%s' "$n" | tr -c 'A-Za-z0-9._-' '.')
	[ "$n" = "$safe" ] || die \
		"$n would be renamed to $safe on upload, so release-manifest.json would
name a file that is not published. Teach the manifest the published name."
done

[ -f "$upload/$deb_native" ] || die "the Debian lane produced no $deb_native"
[ -f "$upload/$rpm_native" ] || die "the EL9 lane produced no $rpm_native"
[ -f "$upload/$source_archive" ] || die "no $source_archive in the source-archive artifact"

# The archive digest is pinned; the release must not publish a different one.
got_archive_sha=$(sha256_file "$upload/$source_archive")
[ "$got_archive_sha" = "$CACHETAG_SOURCE_SHA256" ] || die \
	"$source_archive sha256 $got_archive_sha does not match the pinned
CACHETAG_SOURCE_SHA256 $CACHETAG_SOURCE_SHA256. Do NOT update the pin to make
this pass: the archive is a function of the pinned cachetag commit, so a
mismatch means the release is about to publish something other than what the
packages were built from."
printf 'OK: %s matches the pinned digest\n' "$source_archive"

deb_sha=$(sha256_file "$upload/$deb_native")
rpm_sha=$(sha256_file "$upload/$rpm_native")

###############################################################################
note "writing $upload/RELEASE-SHA256SUMS"
###############################################################################
# Names exactly as published, so `sha256sum -c RELEASE-SHA256SUMS` works in a
# directory of downloaded assets.
{
	printf '# libvmod-cachetag %s, cohort %s\n' "$CACHETAG_VERSION" "$COHORT_ID"
	printf '# Both lanes plus the source archive, all from one CI run: %s\n' "$run_url"
	printf '# Filenames are the published release-asset names.\n'
	printf '#\n'
	(
		cd "$upload"
		for f in *; do
			[ -f "$f" ] || continue
			printf '%s  %s\n' "$(sha256_file "$f")" "$f"
		done
	)
} > "$assets/RELEASE-SHA256SUMS"
cp -p "$assets/RELEASE-SHA256SUMS" "$upload/RELEASE-SHA256SUMS"
cat "$assets/RELEASE-SHA256SUMS"

###############################################################################
note "writing $upload/release-manifest.json"
###############################################################################
# JSON-escape and wrap the gap lines as an array. They come from the
# validator, so they contain no control characters; quotes and backslashes are
# escaped anyway rather than assumed absent.
if [ -n "$gaps" ]; then
	gaps_json=$(printf '%s\n' "$gaps" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
		-e 's/^/    "/' -e 's/$/",/' | sed '$ s/,$//')
	gaps_json=$(printf '\n%s\n  ' "$gaps_json")
else
	gaps_json=""
fi

cat > "$assets/release-manifest.json" <<JSON
{
  "schema": "vcache-packaging-release-manifest/v1",
  "note": "Assembled by scripts/ci/release-manifest.sh from the registry manifests plus this run's facts. Package filenames are the native ones, which are also the published asset names; release_asset_filename is the distro-bearing name registry/README.md generates, recorded here rather than used as a filename because renaming a .deb would contradict the .changes and .buildinfo published beside it.",
  "cohort": "$COHORT_ID",
  "cohort_status": "candidate",
  "channel": "pre-release",
  "evidence_gaps": [$gaps_json],
  "cachetag": {
    "version": "$CACHETAG_VERSION",
    "git_commit": "$CACHETAG_GIT_COMMIT",
    "source_archive": "$source_archive",
    "source_archive_sha256": "$CACHETAG_SOURCE_SHA256"
  },
  "vinyl": {
    "git_commit": "$VINYL_GIT_COMMIT",
    "upstream_version": "$VINYL_UPSTREAM_VERSION",
    "package_version": "$VINYL_PACKAGE_VERSION",
    "vrt": "$VINYL_VRT_EXPECTED",
    "strict_abi": "$VINYL_STRICT_ABI",
    "source_sha256": "$VINYL_SOURCE_SHA256"
  },
  "abi": {
    "deb_cohort_provide": "$deb_cohort_provide",
    "rpm_cohort_provide": "$rpm_cohort_provide",
    "deb_depends": "$deb_depends"
  },
  "storage_support": ["default"],
  "targets": [
    {
      "target": "debian-13-amd64",
      "package_format": "deb",
      "filename": "$deb_native",
      "sha256": "$deb_sha",
      "release_asset_filename": "$deb_asset"
    },
    {
      "target": "el9-x86_64",
      "package_format": "rpm",
      "filename": "$rpm_native",
      "sha256": "$rpm_sha",
      "release_asset_filename": "$rpm_asset"
    }
  ],
  "ci": {
    "workflow": "release-draft.yml",
    "run_id": "$run_id",
    "run_url": "$run_url"
  },
  "checksums_file": "RELEASE-SHA256SUMS"
}
JSON
cp -p "$assets/release-manifest.json" "$upload/release-manifest.json"
cat "$assets/release-manifest.json"

python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$upload/release-manifest.json" ||
	die "release-manifest.json is not valid JSON"

note "upload directory"
ls -la "$upload"
