#!/bin/sh
#
# Host-side driver for every containerised stage of the generated-recipe VMOD
# lane. One entry point rather than four near-identical wrappers, because the
# only thing that varies between them is the image, the privilege flag and the
# script to run inside.
#
#   run.sh build-deb|verify-deb|build-rpm|verify-rpm --lane DIR --id ID \
#          --overlay PATH --manifest PATH --cohort ID --target ID \
#          --engine-identity FILE
#
# The host contributes a pinned image reference and a mount. Every build tool
# comes from inside that image: the runner's own userland is never an
# acceptable place to build a package, which is the same rule the cachetag
# lanes follow.
#
# --engine-identity is where the image reference comes from, and it is not an
# optimisation. AGENTS.md makes scripts/ci/engine-identity.sh the one reader of
# the lane pin files that both sides of the engine comparison use; this script
# used to read pins.env and cohort.env itself, which made it a second reader
# and therefore a second thing that could disagree with the artifact the row
# actually verified. The file passed here is the one the calling job already
# wrote and already compared against the engine artifact's metadata, so the
# container is started from the image that engine was built in, by
# construction rather than by a matching pin.
#
# --privileged is what lets pbuilder and Mock chroot and mount inside the
# container. The verify stages do not need it: they install packages and run a
# test driver in a container that has seen no build tree, and that is the whole
# point of running them separately.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../../.." && pwd)
. "$here/lib.sh"

stage=${1:?stage required: build-deb|verify-deb|build-rpm|verify-rpm}
shift

lane=
vmod_id=
overlay=
manifest=
cohort=
target=
channel=release
engine_identity=

while [ $# -gt 0 ]; do
	case $1 in
	--lane) lane=${2:?}; shift 2 ;;
	--id) vmod_id=${2:?}; shift 2 ;;
	--overlay) overlay=${2:?}; shift 2 ;;
	--manifest) manifest=${2:?}; shift 2 ;;
	--cohort) cohort=${2:?}; shift 2 ;;
	--target) target=${2:?}; shift 2 ;;
	--channel) channel=${2:?}; shift 2 ;;
	--engine-identity) engine_identity=${2:?}; shift 2 ;;
	*) die "unknown argument $1" ;;
	esac
done

for required in lane vmod_id overlay manifest cohort target engine_identity; do
	eval "value=\$$required"
	[ -n "$value" ] || die "--${required} is required"
done
lane=$(CDPATH= cd -- "$lane" && pwd)
[ -f "$engine_identity" ] || die "no engine identity file at $engine_identity"

# Everything the container needs to know, derived rather than typed. The names
# come from the generator, the engine facts from the registry, and the payload
# from the reviewed overlay: three sources, each authoritative for its own
# facts, and none of them restated here.
eval "$(python3 "$repo/tools/ci_matrix.py" source-facts \
	--manifest "$manifest" --id "$vmod_id" --channel "$channel" --format shell)"
eval "$(python3 "$repo/tools/vmod_recipe.py" lane-env \
	--manifest "$manifest" --overlay "$overlay" \
	--cohort "$cohort" --target "$target" --channel "$channel")"

# sed, not `.`: engine-identity.sh emits values containing spaces -- the
# maintainer line for one -- so sourcing it would be both a quoting bug and an
# injection surface. Same reader the workflow uses for the cohort id.
image=$(sed -n 's/^build_image=//p' "$engine_identity" | head -1)
[ -n "$image" ] || die "no build_image in $engine_identity"
note "buildroot image, from the verified engine identity: $image"

# The Debian container half still needs DEBIAN_DISTRIBUTION to name the base
# tarball. That is a lane fact rather than an engine fact and engine-identity.sh
# does not carry it, so it is read from the pin file it belongs to -- which is
# not a second reading of the image pin, the thing the runbook rule is about.
. "$repo/recipes/debian-13/pins.env"

common_env="-e VMOD_ID=$vmod_id \
 -e VMOD_SOURCE_NAME=$VMOD_SOURCE_NAME \
 -e VMOD_BINARY_NAME=$VMOD_BINARY_NAME \
 -e VMOD_RPM_NAME=$VMOD_RPM_NAME \
 -e VMOD_UPSTREAM_VERSION=$VMOD_UPSTREAM_VERSION \
 -e VMOD_DEBIAN_VERSION=$VMOD_DEBIAN_VERSION \
 -e VMOD_RPM_RELEASE=$VMOD_RPM_RELEASE \
 -e VMOD_OBJECT=$VMOD_OBJECT \
 -e VMOD_MAN_PAGE=$VMOD_MAN_PAGE \
 -e VMOD_SOURCE_DATE_EPOCH=$VMOD_SOURCE_DATE_EPOCH \
 -e VMOD_SOURCE_SHA256=$VMOD_SOURCE_ARCHIVE_SHA256 \
 -e VINYL_VMODDIR=$VINYL_VMODDIR \
 -e VINYL_STRICT_ABI=$VINYL_STRICT_ABI \
 -e VINYL_VRT=$VINYL_VRT \
 -e COHORT_ID=$COHORT_ID"

case $stage in
build-deb)
	note "$vmod_id: pbuilder build in $image"
	[ -f "$lane/chroot/$DEBIAN_DISTRIBUTION-amd64.tar" ] ||
		die "no mmdebstrap base tarball in $lane/chroot; run scripts/ci/debian13/make-chroot.sh first"
	# shellcheck disable=SC2086 # common_env is a deliberate flag list
	docker run --privileged --rm \
		-v "$repo:/repo:ro" -v "$lane:/lane" \
		$common_env -w /lane \
		"$image" bash /repo/scripts/ci/vmod/container/build-deb.sh
	;;
verify-deb)
	note "$vmod_id: installed-package verification in a fresh $image"
	# shellcheck disable=SC2086
	docker run --rm \
		-v "$lane:/lane" \
		$common_env -w /lane \
		"$image" bash -c 'bash /lane/scripts/verify-deb.sh'
	;;
build-rpm)
	note "$vmod_id: Mock build in $image"
	# shellcheck disable=SC2086
	docker run --privileged --rm \
		-v "$repo:/repo:ro" -v "$lane:/lane" \
		$common_env -e MOCK_ROOT="${MOCK_ROOT:-alma+epel-9-x86_64}" -w /lane \
		"$image" bash /repo/scripts/ci/vmod/container/build-rpm.sh
	;;
verify-rpm)
	note "$vmod_id: installed-package verification in a fresh $image"
	# shellcheck disable=SC2086
	docker run --rm \
		-v "$lane:/lane" \
		$common_env -w /lane \
		"$image" bash -c 'bash /lane/scripts/verify-rpm.sh'
	;;
*)
	die "unknown stage '$stage' (build-deb|verify-deb|build-rpm|verify-rpm)"
	;;
esac

note "$stage complete"
