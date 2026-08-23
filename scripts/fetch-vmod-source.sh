#!/usr/bin/env bash
# scripts/fetch-vmod-source.sh <vmod-id> <engine-id> <artifact-dir>
#
# Resolve one catalog source, including submodules, into a workflow artifact.
# Build jobs can then fan out without repeating upstream Git traffic.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

[ $# -eq 3 ] || die "usage: fetch-vmod-source.sh <vmod-id> <engine-id> <artifact-dir>"
VMOD_ARG=$1 ENGINE_ARG=$2
ARTIFACT_DIR=$(mkdir -p "$3" && cd "$3" && pwd)
SOURCE_WORK=$(mktemp -d "${TMPDIR:-/tmp}/vcache-vmod-source.XXXXXX")
trap 'rm -rf "$SOURCE_WORK"' EXIT

python3 "$REPO_ROOT/tools/matrix.py" env --engine "$ENGINE_ARG" --vmod "$VMOD_ARG" \
  > "$SOURCE_WORK/source.env"
. "$SOURCE_WORK/source.env"

materialize_vmod_source "${VMOD_GIT:?}" "${VMOD_REF:?}" "${VMOD_EXPECTED_COMMIT:-}" \
  "$SOURCE_WORK/source" "$SOURCE_WORK/commit"
RESOLVED_COMMIT=$(cat "$SOURCE_WORK/commit")
archive_vmod_source "$SOURCE_WORK/source" "$ARTIFACT_DIR" "$VMOD_ID" "$VMOD_GIT" \
  "$VMOD_REF" "$RESOLVED_COMMIT"
