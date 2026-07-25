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

#
# User namespaces are a host-kernel property, and AppArmor is a host-kernel
# LSM: `docker run --privileged` makes the container unconfined, but the
# unprivileged user *inside* it still has no CAP_SYS_ADMIN in the initial
# namespace, so Ubuntu 24.04's
# kernel.apparmor_restrict_unprivileged_userns=1 denies it a namespace just
# as it does on the runner. Measured: container-sbuild.sh's preflight
# reported "the build user cannot create a user namespace inside this
# container" until this was relaxed (run 30169877504).
#
# Containerising fixed the other half of the problem -- the userland that
# could not exec inside the chroot -- and this is the half that has to be
# fixed out here. Restricting unprivileged user namespaces is a hardening
# measure for multi-user systems; this is a single-use ephemeral runner whose
# job is to run one namespaced build.
#
userns_sysctl=kernel.apparmor_restrict_unprivileged_userns
if sysctl -n "$userns_sysctl" >/dev/null 2>&1; then
	note "$userns_sysctl = $(sysctl -n "$userns_sysctl")"
	if [ "$(sysctl -n "$userns_sysctl")" != "0" ]; then
		sudo -n sysctl -w "$userns_sysctl=0"
	fi
else
	printf '%s: not present on this kernel\n' "$userns_sysctl"
fi

note "sbuild both packages inside $IMAGE"
docker run --privileged --rm \
	-v "$repo_dir:/repo:ro" \
	-v "$out_dir:/out" \
	-w /out \
	"$IMAGE" \
	bash /repo/scripts/ci/debian13/container-sbuild.sh

note "Debian 13 sbuild lane done"
ls -la "$out_dir"
