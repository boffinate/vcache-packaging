#!/bin/sh
#
# Print the resolved identity of one lane's Vinyl engine input, as `key=value`
# lines, read out of that lane's own pin file for the currently selected
# VINYL_TRACK.
#
# Usage: engine-identity.sh <deb|rpm>
#
# This exists because Phase 2 of
# docs/20260728_0833_plan_vmod-matrix-failure-isolation.md splits engine package
# production from VMOD package production. A VMOD package row no longer builds
# the engine, so it has no independent way to know what the artifact it
# downloaded actually is -- unlike a VMOD source archive, which the row can
# re-verify by checking the tag out again. The engine artifact therefore carries
# this identity inside it, and the consumer regenerates the same identity from
# its own checkout and requires the two to be equal before it builds anything.
#
# Both sides run THIS script, so the two identities are comparable by
# construction. The key list lives here rather than in tools/ci_matrix.py so a
# pin that gains or loses a name cannot be silently dropped from the comparison
# by a table in the other language that nobody updated: ci_matrix.py compares
# whatever keys it is given, and insists that a handful of load-bearing ones are
# present and non-empty so the comparison can never pass vacuously.
#
# It reads pins; it never writes, builds, fetches or installs anything, and it
# has no side effect on the lane. VINYL_TRACK selects the block inside the pin
# file exactly as it does for every other reader.
#
# A value that a track does not define prints as empty -- recipes/el9/cohort.env
# records no VINYL_SOURCE_SHA256 on the trunk track, for instance. That is
# correct rather than tolerated: producer and consumer read the same file for
# the same track, so an absent pin is absent on both sides and a pin that
# appears on only one of them is a mismatch.

set -eu

family=${1:-}
case $family in
deb | rpm) : ;;
*)
	printf 'usage: %s <deb|rpm>\n' "$0" >&2
	exit 2
	;;
esac

_here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
_repo=$(CDPATH= cd -- "$_here/../.." && pwd)

# One writer for the output format: `key=value`, no quoting, values may contain
# spaces. tools/ci_matrix.py parse_identity splits on the first `=` only.
emit() {
	printf '%s=%s\n' "$1" "$2"
}

case $family in
deb)
	. "$_repo/recipes/debian-13/pins.env"
	emit engine_family deb
	emit cohort_id "$COHORT_ID"
	emit vinyl_track "$VINYL_TRACK"
	emit vinyl_source_kind "$VINYL_SOURCE_KIND"
	emit vinyl_git_commit "$VINYL_GIT_COMMIT"
	emit vinyl_strict_abi "$VINYL_STRICT_ABI"
	emit vinyl_abi_string "$VINYL_ABI_STRING"
	emit vinyl_upstream_version "$VINYL_UPSTREAM_VERSION"
	emit vinyl_package_version "$VINYL_PACKAGE_VERSION"
	emit vinyl_source_sha256 "${VINYL_SOURCE_SHA256-}"
	emit vinyl_source_date_epoch "$VINYL_SOURCE_DATE_EPOCH"
	emit vinyl_vrt_expected "$VINYL_VRT_EXPECTED"
	emit vinyl_source_url "${VINYL_SOURCE_URL-}"
	emit build_image "$IMAGE"
	emit buildroot_snapshot "$DEBIAN_SNAPSHOT"
	emit maintainer "$MAINTAINER_NAME <$MAINTAINER_EMAIL>"
	;;
rpm)
	. "$_repo/recipes/el9/cohort.env"
	emit engine_family rpm
	emit cohort_id "$COHORT_ID"
	emit vinyl_track "$VINYL_TRACK"
	emit vinyl_source_kind "$VINYL_SOURCE_KIND"
	emit vinyl_git_commit "$VINYL_GIT_COMMIT"
	emit vinyl_strict_abi "$VINYL_STRICT_ABI"
	emit vinyl_abi_string "$VINYL_PACKAGE_STRING $VINYL_GIT_COMMIT"
	emit vinyl_upstream_version "$VINYL_VERSION"
	emit vinyl_package_version "$VINYL_VERSION-$VINYL_RELEASE.el9"
	emit vinyl_source_sha256 "${VINYL_SOURCE_SHA256-}"
	emit vinyl_source_date_epoch "$VINYL_SOURCE_DATE_EPOCH"
	emit vinyl_source_url "${VINYL_SOURCE_URL-}"
	emit build_image "$EL9_IMAGE"
	emit maintainer "$MAINTAINER_NAME <$MAINTAINER_EMAIL>"
	;;
esac
