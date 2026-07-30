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
# ONE FAMILY BLOCK PER REQUIRED VMOD since Step 8 Wave 3e. It was cachetag-only
# from the beginning, because for most of this project's life cachetag was the
# only VMOD; the cohort now requires three, and a release that described one of
# them would be a release whose own manifest disagreed with the registry about
# what it contains. The VMOD list comes from the cohort's `required_vmods`, so
# a fourth VMOD needs no edit here.
#
# Usage: release-manifest.sh ASSETS_DIR RUN_ID RUN_URL [UPLOAD_DIR]
#
# ASSETS_DIR is the isolated graph's download layout:
#
#   packages/<packages-artifact>/...   one per selected package row
#   source/<vmod-source-artifact>/...  one per VMOD source channel
#
# Artifact names are the ledger's, so this script and the workflow cannot
# disagree about what to look for. Separate directories per artifact because
# every lane writes a file literally named SHA256SUMS and a shared directory
# would let one row clobber another's.
#
# UPLOAD_DIR (default ASSETS_DIR/../upload) receives every file that becomes a
# release asset, flat. Assembling it here rather than in the workflow keeps the
# checksum file honest: RELEASE-SHA256SUMS lists the names the assets are
# actually published under, so `sha256sum -c` works in a directory of
# downloaded release assets.

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

[ -d "$assets/packages" ] || die "missing $assets/packages"
[ -d "$assets/source" ] || die "missing $assets/source"

cachetag_src=${CACHETAG_SRC:-$repo/../libvmod-cachetag}
release_tool() { python3 "$repo/tools/release_tool.py" --cachetag-src "$cachetag_src" "$@"; }

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

# WHICH VMODs this release describes, from the cohort manifest rather than from
# a list here. registry/README.md makes required_vmods the cohort's own
# statement of what it must contain, cross-checked against the catalog in both
# directions, so it is the one authority that cannot disagree with what CI
# built.
vmods=$(python3 - "$repo/registry/cohorts/$COHORT_ID.yml" <<'PY'
import sys
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(sys.argv[1]), "..", "..", "tools"))
import yaml_subset
print(" ".join(yaml_subset.parse_file(sys.argv[1])["required_vmods"]))
PY
)
[ -n "$vmods" ] || die "cohort $COHORT_ID declares no required_vmods"
printf 'required VMODs: %s\n' "$vmods"

###############################################################################
note "per-VMOD generated names"
###############################################################################
# Every generated name comes from the registry, never from this script.
# `metadata` refuses to run against a template manifest without
# --allow-template, so this also proves a real cohort manifest backs the id the
# lanes just built with.
#
# `metadata --vmod` is the authority for NAMES and VERSIONS: it is the one
# generator of them, and registry/README.md is explicit that a recipe
# disagreeing with it is a bug in the recipe. It is not the authority for
# recorded digests -- those describe a previous run and this release is a fresh
# build, so every digest below is computed from the bytes just built.
#
# The pinned SOURCE ARCHIVE digest comes from `ci_matrix.py source-facts`,
# which reads the VMOD manifest: that is the one place a source archive's
# identity is pinned, for all three VMODs, and it replaces the single
# CACHETAG_SOURCE_SHA256 the cachetag-only script asserted against.
meta_get() { printf '%s\n' "$2" | sed -n "s/^CACHETAG_$1='\(.*\)'\$/\1/p"; }

# Per-VMOD facts, keyed by name. Plain variables through `eval` rather than an
# associative array: bash 3.2 has no `declare -A`, and that is the bash on a
# maintainer's macOS host, so a script only verifiable on a runner would be a
# script nobody checks before dispatching it. Every key component is a VMOD id
# (^[a-z][a-z0-9]*$) or a fixed word, so nothing here is attacker-shaped.
vset() { eval "_v_$1=\$2"; }
vget() { eval "printf '%s' \"\${_v_$1:-}\""; }

for vmod in $vmods; do
	facts=$(python3 "$repo/tools/ci_matrix.py" source-facts \
		--manifest "$repo/registry/vmods/$vmod.yml" --id "$vmod" \
		--channel release --format shell) ||
		die "no source facts for $vmod"
	vset "srcsha_$vmod" "$(printf '%s\n' "$facts" |
		sed -n "s/^VMOD_SOURCE_ARCHIVE_SHA256='\(.*\)'\$/\1/p")"
	[ -n "$(vget "srcsha_$vmod")" ] || die "$vmod records no release archive digest"

	for target in debian-13-amd64 el9-x86_64; do
		m=$(release_tool metadata --cohort "$COHORT_ID" --target "$target" \
			--vmod "$vmod" --format shell) ||
			die "no registry evidence for $vmod on $COHORT_ID/$target"
		case $target in
		debian-13-amd64) fam=deb ;;
		el9-x86_64) fam=rpm ;;
		esac
		vset "native_${vmod}_$fam" "$(meta_get ARTIFACTS_NATIVE_FILENAME "$m")"
		vset "asset_${vmod}_$fam" "$(meta_get ARTIFACTS_RELEASE_ASSET_FILENAME "$m")"
		[ -n "$(vget "native_${vmod}_$fam")" ] ||
			die "release_tool.py metadata yielded no native filename for $vmod/$target"
		case $target in
		debian-13-amd64)
			vset "srcarchive_$vmod" "$(meta_get SOURCE_ARCHIVE "$m")"
			vset "upstream_$vmod" "$(meta_get VERSIONS_DEBIAN_UPSTREAM_VERSION "$m")"
			vset "revision_$vmod" "$(meta_get PACKAGE_REVISION "$m")"
			vset "debdepends_$vmod" "$(meta_get ABI_DEB_DEPENDS "$m")"
			vset "debcohort_$vmod" "$(meta_get ABI_COHORT_PROVIDE "$m")"
			;;
		el9-x86_64)
			vset "rpmcohort_$vmod" "$(meta_get ABI_RPM_COHORT_PROVIDE "$m")"
			;;
		esac
	done
	printf '%-10s %s  |  %s  |  %s\n' "$vmod" \
		"$(vget "native_${vmod}_deb")" "$(vget "native_${vmod}_rpm")" \
		"$(vget "srcarchive_$vmod")"
done

###############################################################################
note "upstream release-note references"
###############################################################################
# Pointers to upstream's own release statements, from the cohort manifest's
# vinyl.release_notes. Both renderings come from the same validated field, so
# the release body and the machine-readable manifest cannot diverge. Empty is
# a legitimate state (a trunk snapshot has no upstream release statement) and
# renders as an empty array and no body section, not an error.
release_notes_json=$(release_tool release-notes --cohort "$COHORT_ID" --format json) ||
	die "release_tool.py release-notes failed for $COHORT_ID"
release_notes_body=$(release_tool release-notes --cohort "$COHORT_ID" --format body) ||
	die "release_tool.py release-notes --format body failed for $COHORT_ID"
printf '%s\n' "$release_notes_json"

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
#
# Since Wave 3e the completeness GATE (ci_matrix.py verify-release-set) runs in
# the workflow before this script, over the reconciled ledger and the
# downloaded artifacts. The two are different questions and both belong: that
# one asks whether the RUN produced a complete set, this one asks whether the
# REGISTRY describes a releasable one. RELEASE_EVIDENCE_GAPS carries the first
# one's findings in, so a single evidence_gaps array covers both.
gaps=""
if release_tool validate --require-releasable > "$assets/validate-releasable.log" 2>&1
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

# Omissions the completeness gate found, passed in by the workflow so they land
# in the same array. Listed, never silently dropped: an experimental release
# says what it is missing or it is not honest about being experimental.
if [ -n "${RELEASE_EVIDENCE_GAPS:-}" ] && [ -f "$RELEASE_EVIDENCE_GAPS" ]; then
	set_gaps=$(sed -n 's/^ERROR *//p' "$RELEASE_EVIDENCE_GAPS")
	if [ -n "$set_gaps" ]; then
		printf '\nW: %d release-set omission(s) recorded from the completeness gate\n' \
			"$(printf '%s\n' "$set_gaps" | wc -l | tr -d ' ')"
		gaps=$(printf '%s\n%s' "${gaps}" "$set_gaps" | sed '/^$/d')
	fi
fi

###############################################################################
note "assembling $upload"
###############################################################################
rm -rf "$upload"
mkdir -p "$upload"

# Two classes of file, and the distinction is the point.
#
# MANDATORY: everything the registry NAMES for this cohort -- the native
# package on each target and the upstream source archive. If one is absent the
# release is missing something the registry says it contains, and that is a
# stop.
#
# INCIDENTAL: the debug package, the source package, the .changes and the
# .buildinfo. They are published because they are useful, they are discovered
# rather than named, and a lane that did not produce one is not a failure --
# the RPM lane produces no .buildinfo at all.
#
# Nothing else travels. The generated lanes' artifacts also carry the verify
# scripts, the ported VTCs, the rendered recipe and the transaction logs; those
# are evidence, they stay in the run's artifacts, and they are not release
# assets.
publish_from() {
	_dir=$1
	[ -d "$_dir" ] || return 0
	find "$_dir" -type f \
		\( -name '*.deb' -o -name '*.ddeb' -o -name '*.rpm' -o -name '*.dsc' \
		-o -name '*.changes' -o -name '*.buildinfo' -o -name '*.orig.tar.gz' \
		-o -name '*.debian.tar.xz' \) \
		! -path '*/logs/*' ! -path '*/txn/*' ! -path '*/mismatch/*' \
		! -path '*/recipe/*' ! -path '*/tests/*' ! -path '*/scripts/*' \
		-exec cp -p {} "$upload/" \;
}

for dir in "$assets"/packages/*/; do
	[ -d "$dir" ] || continue
	printf 'publishing from %s\n' "$(basename "$dir")"
	publish_from "$dir"
done

# The upstream source archives, by name and never by glob: the cachetag
# derivation also leaves libvmod-cachetag-X.Y.Z.dist-raw.tar.gz beside the real
# one, which is the pre-canonicalisation intermediate and must not be published
# as if it were the release source.
for vmod in $vmods; do
	archive=$(vget "srcarchive_$vmod")
	found=$(find "$assets/source" -type f -name "$archive" | head -1)
	[ -n "$found" ] || die "no $archive in $assets/source (the $vmod source artifact)"
	cp -p "$found" "$upload/"
	for extra in "$(dirname "$found")/$archive.sha256" "$(dirname "$found")"/*.metadata.json; do
		case "$extra" in *dist-raw*) continue ;; esac
		[ -e "$extra" ] && cp -p "$extra" "$upload/"
	done
done

###############################################################################
note "renaming assets GitHub would rename anyway"
###############################################################################
# GitHub rewrites characters outside [A-Za-z0-9._-] in a release asset name.
# The Vinyl snapshot version contains a tilde -- 9.0.0~git20260520.25761f8505,
# deliberately, because ~ sorts below a future real 9.0.0 in both dpkg and rpm
# -- so every Vinyl asset was published as ...9.0.0.git..., while
# RELEASE-SHA256SUMS named it ...9.0.0~git.... `sha256sum -c` then failed on
# every one of them (observed on draft-20260726T074622Z).
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

###############################################################################
note "the registry-named files must all be here"
###############################################################################
# Every VMOD's native package on both targets, and its source archive. Asserted
# after the rename, so a name the rename would have moved is caught rather than
# silently recorded as something that is not published.
for vmod in $vmods; do
	for name in "$(vget "native_${vmod}_deb")" "$(vget "native_${vmod}_rpm")" \
		"$(vget "srcarchive_$vmod")"; do
		safe=$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '.')
		[ "$name" = "$safe" ] || die \
			"$name would be renamed to $safe on upload, so release-manifest.json would
name a file that is not published. Teach the manifest the published name."
		[ -f "$upload/$name" ] || die "$vmod: the release is missing $name"
	done
	# The archive digest is pinned; the release must not publish a different
	# one. Do NOT update a pin to make this pass: the archive is a function of
	# the pinned source, so a mismatch means the release is about to publish
	# something other than what the packages were built from.
	got=$(sha256_file "$upload/$(vget "srcarchive_$vmod")")
	[ "$got" = "$(vget "srcsha_$vmod")" ] || die \
		"$(vget "srcarchive_$vmod") sha256 $got does not match the pinned $(vget "srcsha_$vmod")"
	printf 'OK: %-10s %s matches its pinned digest\n' "$vmod" "$(vget "srcarchive_$vmod")"
done

# The cachetag lane pin is asserted against the manifest as well, which is the
# guard that caught the four-copies problem in 2026-07-25 and is cheap to keep.
[ "$(vget srcsha_cachetag)" = "$CACHETAG_SOURCE_SHA256" ] || die \
	"registry/vmods/cachetag.yml and recipes/debian-13/pins.env disagree about the
cachetag source digest: '$(vget srcsha_cachetag)' vs '$CACHETAG_SOURCE_SHA256'."

###############################################################################
note "writing $upload/RELEASE-SHA256SUMS"
###############################################################################
# Names exactly as published, so `sha256sum -c RELEASE-SHA256SUMS` works in a
# directory of downloaded assets.
{
	printf '# Cohort %s: %s\n' "$COHORT_ID" "$vmods"
	printf '# Every family and both targets, all from one CI run: %s\n' "$run_url"
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
# JSON-escape and wrap the gap lines as an array. They come from the validator
# and the completeness gate, so they contain no control characters; quotes and
# backslashes are escaped anyway rather than assumed absent.
if [ -n "$gaps" ]; then
	gaps_json=$(printf '%s\n' "$gaps" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
		-e 's/^/    "/' -e 's/$/",/' | sed '$ s/,$//')
	gaps_json=$(printf '\n%s\n  ' "$gaps_json")
else
	gaps_json=""
fi

# One block per required VMOD, each with both targets. Built by iteration so a
# fourth VMOD appears without an edit; the trailing comma is trimmed by the
# same `sed` idiom the gaps array uses.
vmods_json=$(
	for vmod in $vmods; do
		deb=$(vget "native_${vmod}_deb")
		rpm=$(vget "native_${vmod}_rpm")
		cat <<VMODJSON
    {
      "vmod": "$vmod",
      "upstream_version": "$(vget "upstream_$vmod")",
      "package_revision": "$(vget "revision_$vmod")",
      "source_archive": "$(vget "srcarchive_$vmod")",
      "source_archive_sha256": "$(vget "srcsha_$vmod")",
      "abi": {
        "deb_cohort_provide": "$(vget "debcohort_$vmod")",
        "rpm_cohort_provide": "$(vget "rpmcohort_$vmod")",
        "deb_depends": "$(vget "debdepends_$vmod")"
      },
      "targets": [
        {
          "target": "debian-13-amd64",
          "package_format": "deb",
          "filename": "$deb",
          "sha256": "$(sha256_file "$upload/$deb")",
          "release_asset_filename": "$(vget "asset_${vmod}_deb")"
        },
        {
          "target": "el9-x86_64",
          "package_format": "rpm",
          "filename": "$rpm",
          "sha256": "$(sha256_file "$upload/$rpm")",
          "release_asset_filename": "$(vget "asset_${vmod}_rpm")"
        }
      ]
    },
VMODJSON
	done | sed '$ s/,$//'
)

cat > "$assets/release-manifest.json" <<JSON
{
  "schema": "vcache-packaging-release-manifest/v2",
  "note": "Assembled by scripts/ci/release-manifest.sh from the registry manifests plus this run's facts. One block per VMOD the cohort requires. Package filenames are the native ones, which are also the published asset names; release_asset_filename is the distro-bearing name registry/README.md generates, recorded here rather than used as a filename because renaming a .deb would contradict the .changes and .buildinfo published beside it.",
  "cohort": "$COHORT_ID",
  "cohort_status": "candidate",
  "channel": "pre-release",
  "evidence_gaps": [$gaps_json],
  "vinyl": {
    "git_commit": "$VINYL_GIT_COMMIT",
    "upstream_version": "$VINYL_UPSTREAM_VERSION",
    "package_version": "$VINYL_PACKAGE_VERSION",
    "vrt": "$VINYL_VRT_EXPECTED",
    "strict_abi": "$VINYL_STRICT_ABI",
    "source_sha256": "$VINYL_SOURCE_SHA256"
  },
  "upstream_release_notes": $release_notes_json,
  "storage_support": ["default"],
  "vmods": [
$vmods_json
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

###############################################################################
note "writing $assets/release-body.md"
###############################################################################
# The complete release body, generated here from the registry and this run's
# facts: the draft (and the pre-release a human flips it to) carries no
# hand-written content, so publishing is a flip, not a writing task. Upstream
# content is referenced, never restated. Written beside the upload directory,
# not inside it: the body is release metadata, not a release asset.
body=$assets/release-body.md
{
	printf 'Internal draft assembled by release-draft.yml run %s (%s): cohort %s.\n' \
		"$run_id" "$run_url" "$COHORT_ID"
	printf '\nNOT a public pre-release. A human or orchestrator publishes the real pre-release from a validated draft, per the step-10 gate decision. Every line of this body is generated by scripts/ci/release-manifest.sh from the registry manifests.\n'
	printf '\n## Package families\n'
	for vmod in $vmods; do
		printf '\n### %s %s-%s\n\n' \
			"$vmod" "$(vget "upstream_$vmod")" "$(vget "revision_$vmod")"
		printf '| target | package | sha256 |\n'
		printf '| --- | --- | --- |\n'
		for pair in "debian-13-amd64 deb" "el9-x86_64 rpm"; do
			set -- $pair
			n=$(vget "native_${vmod}_$2")
			printf '| %s | `%s` | `%s` |\n' "$1" "$n" "$(sha256_file "$upload/$n")"
		done
		printf '\nSource: `%s` (sha256 `%s`)\n' \
			"$(vget "srcarchive_$vmod")" "$(vget "srcsha_$vmod")"
	done
	if [ -n "$release_notes_body" ]; then
		printf '\n## Upstream release notes\n\n'
		printf '%s\n' "$release_notes_body" | sed 's/^/- /'
	fi
	printf '\nVerify downloaded assets with RELEASE-SHA256SUMS. The machine-readable release identity, evidence status, and per-VMOD artifact record is release-manifest.json.\n'
	if [ -n "$gaps" ]; then
		printf '\n## Evidence gaps\n\n'
		printf 'This draft is explicitly experimental. It is missing:\n\n'
		printf '%s\n' "$gaps" | sed 's/^/- /'
	fi
} > "$body"
cat "$body"

note "upload directory"
ls -la "$upload"
