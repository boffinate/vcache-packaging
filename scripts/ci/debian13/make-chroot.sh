#!/bin/bash
#
# Materialize the sbuild chroot from the SAME pinned, digest-addressed image
# the rest of the Debian 13 lane already trusts (pins.env's
# IMAGE_REF@IMAGE_DIGEST), rather than introducing a second, differently-pinned
# buildroot such as a mirror-snapshot debootstrap.
#
# Producing this tarball is now all the host does for the Debian lane. sbuild
# itself runs inside a pinned container (sbuild-lane.sh, container-sbuild.sh),
# so nothing here installs a build tool or changes a kernel setting.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

note "materializing $IMAGE into $CHROOT_TARBALL"
mkdir -p "$(dirname -- "$CHROOT_TARBALL")"
rm -f "$CHROOT_TARBALL"

#
# The unshare backend takes a chroot TARBALL, not a directory: sbuild(1),
# --chroot -- "if this option is a path, then it specifies the location of the
# chroot tarball directly". Given a directory it treats it as a missing
# tarball and tries to mmdebstrap a fresh one, which would silently replace
# the pinned buildroot with a live one.
#
# The export is unpacked and repacked rather than used as-is, so the tarball
# has the shape sbuild's extraction expects:
#
#   --owner=0 --group=0   every member root-owned. sbuild unpacks inside a
#                         user namespace that maps only root and the build
#                         user's subuid range, so a member owned by anything
#                         else arrives with an unmapped owner.
#   ./-prefixed members   docker export omits the leading ./ that
#                         `tar -C <root> -cf - .` produces.
#   no device nodes       dev/ stays an empty mount point; sbuild bind-mounts
#                         the real /dev per invocation, and device nodes
#                         cannot be created inside a user namespace anyway.
#
docker pull "$IMAGE" >/dev/null
cid=$(docker create --platform linux/amd64 "$IMAGE" true)
rootfs=$(mktemp -d)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true; rm -rf "$rootfs"' EXIT
docker export "$cid" | tar -C "$rootfs" -x --exclude='dev/*' --no-same-owner
docker rm -f "$cid" >/dev/null
mkdir -p "$rootfs/dev" "$rootfs/proc" "$rootfs/sys"
tar -C "$rootfs" --owner=0 --group=0 --numeric-owner -cf "$CHROOT_TARBALL" .
rm -rf "$rootfs"
trap - EXIT

#
# Assert the tarball really is a root filesystem before a build depends on it.
# The member list goes to a file and grep reads that file rather than a pipe:
# under `set -o pipefail`, `printf ... | grep -q` reports 141 even on a match,
# because grep exits at the first hit and printf takes SIGPIPE.
#
members=$(mktemp)
trap 'rm -f "$members"' EXIT
tar -tf "$CHROOT_TARBALL" > "$members"
for path in etc/os-release proc/ sys/ dev/; do
	grep -qx -e "$path" -e "./$path" "$members" ||
		die "$CHROOT_TARBALL does not contain $path; the export is not a usable chroot"
done
# /etc/os-release is a symlink to ../usr/lib/os-release on Debian, so read the
# target; extracting the symlink member itself yields no content.
os_release=$(tar -xOf "$CHROOT_TARBALL" ./usr/lib/os-release 2>/dev/null || true)
[ -n "$os_release" ] || die "$CHROOT_TARBALL has no readable os-release"
printf '%s\n' "$os_release" | sed -n '1,3p'

printf 'OK: sbuild chroot tarball ready at %s (%s bytes)\n' \
	"$CHROOT_TARBALL" "$(wc -c < "$CHROOT_TARBALL")"
