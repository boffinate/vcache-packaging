#!/bin/sh
#
# Host-side driver for the Debian 13 sbuild lane. Starts the pinned
# debian:trixie container that container-sbuild.sh builds both packages in,
# replacing the pair of host-side sbuild-vinyl.sh / sbuild-cachetag.sh scripts.
#
# The same shape as scripts/ci/el9/mock-build.sh: the host contributes only a
# pinned image reference and a mount, and every build tool comes from inside
# that image. See container-sbuild.sh's header for why the runner's own
# userland stopped being an acceptable place to run sbuild.
#
# --privileged is what lets the unprivileged user inside create the user
# namespace sbuild's unshare backend needs, exactly as the Mock lane needs it
# for Mock's chroot isolation. The buildroot is still the pinned chroot
# tarball, unpacked per build; the container is the toolchain, not the
# buildroot.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

repo_dir=$(CDPATH= cd -- "$here/../../.." && pwd)
out_dir=$repo_dir/dist/debian-13

[ -d "$out_dir/work/build" ] ||
	die "$out_dir/work/build is missing; run recipes/debian-13/build.sh source first"
[ -f "$CHROOT_TARBALL" ] ||
	die "no chroot tarball at $CHROOT_TARBALL; run make-chroot.sh first"

note "sbuild both packages inside $IMAGE"
docker run --privileged --rm \
	-v "$repo_dir:/repo:ro" \
	-v "$out_dir:/out" \
	-w /out \
	"$IMAGE" \
	bash /repo/scripts/ci/debian13/container-sbuild.sh

note "Debian 13 sbuild lane done"
ls -la "$out_dir"
