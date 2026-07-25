#!/bin/bash
#
# Materialize an sbuild "unshare"-mode chroot from the SAME pinned, digest-
# addressed image the rest of the Debian 13 lane already trusts
# (recipes/debian-13/build.sh's IMAGE_REF@IMAGE_DIGEST), rather than
# introducing a second, differently-pinned buildroot (e.g. a mirror-snapshot
# debootstrap). See DESIGN.md section 4.
#
# Must run as root (the workflow step runs this under `sudo`): docker export
# needs to write device/special files sbuild's chroot expects, and sbuild's
# unshare backend is simplest to reason about run as root in ephemeral CI,
# avoiding subuid/subgid range setup that a rootless invocation would need.
#
# DRAFT, unexecuted -- see ../../../DESIGN.md section 4.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

note "installing sbuild"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends sbuild fakeroot

note "materializing $IMAGE into $CHROOT_DIR"
rm -rf "$CHROOT_DIR"
mkdir -p "$CHROOT_DIR"

docker pull "$IMAGE" >/dev/null
cid=$(docker create --platform linux/amd64 "$IMAGE" true)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
docker export "$cid" | tar -C "$CHROOT_DIR" -xf -
trap - EXIT
docker rm -f "$cid" >/dev/null

# A container filesystem export is missing a few directories the kernel
# expects a chroot to already have; sbuild's unshare backend bind-mounts
# /proc, /sys and /dev itself per invocation, but the mount points need to
# exist first.
mkdir -p "$CHROOT_DIR"/proc "$CHROOT_DIR"/sys "$CHROOT_DIR"/dev

note "quick sanity check: a trivial command inside the chroot"
sbuild-shell --chroot-mode=unshare --chroot="$CHROOT_DIR" -- \
	sh -c 'echo chroot OK; cat /etc/os-release | head -3' ||
	die "the materialized chroot failed a trivial sbuild-shell smoke check"

printf 'OK: sbuild unshare chroot ready at %s\n' "$CHROOT_DIR"
