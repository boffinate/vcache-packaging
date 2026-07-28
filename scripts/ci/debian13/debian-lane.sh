#!/bin/sh
#
# Host-side driver for the Debian 13 package lane. Starts the pinned
# debian:trixie container that container-pbuilder.sh builds both packages in.
#
# The same shape as scripts/ci/el9/mock-build.sh: the host contributes only a
# pinned image reference and a mount, and every build tool comes from inside
# that image. See container-pbuilder.sh's header for why the runner's own
# userland stopped being an acceptable place to run sbuild.
#
# --privileged is what lets pbuilder chroot and mount inside the container,
# exactly as the Mock lane needs it for Mock's chroot isolation. The buildroot
# is still the pinned mmdebstrap tarball, unpacked and destroyed per package;
# the container is the toolchain, not the buildroot.
#
# Usage: debian-lane.sh [all|engine|vmod]   (default: all)
#
# `engine` builds only the Vinyl packages, `vmod` only libvmod-cachetag against
# Vinyl packages already present in dist/debian-13. See container-pbuilder.sh's
# header for why the split does not move any package byte.

set -eu

scope=${1:-all}
case $scope in
all | engine | vmod) : ;;
*) printf 'usage: %s [all|engine|vmod]\n' "$0" >&2; exit 2 ;;
esac

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

repo_dir=$(CDPATH= cd -- "$here/../../.." && pwd)
out_dir=$repo_dir/dist/debian-13

[ -d "$out_dir/work/build" ] ||
	die "$out_dir/work/build is missing; run recipes/debian-13/build.sh source first"
[ -f "$CHROOT_TARBALL" ] ||
	die "no chroot tarball at $CHROOT_TARBALL; run make-chroot.sh first"

note "building the $scope packages inside $IMAGE"
# container-pbuilder.sh re-sources pins.env inside the container, so the
# resolved track must travel with it: without this, a release-track run would
# build 9.0.1 source trees against trunk pin values in there. The scope travels
# the same way and for the same reason.
docker run --privileged --rm \
	-v "$repo_dir:/repo:ro" \
	-v "$out_dir:/out" \
	-e "VINYL_TRACK=$VINYL_TRACK" \
	-e "PBUILDER_SCOPE=$scope" \
	-w /out \
	"$IMAGE" \
	bash /repo/scripts/ci/debian13/container-pbuilder.sh

note "Debian 13 lane done"
ls -la "$out_dir"
