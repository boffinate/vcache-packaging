#!/bin/bash
#
# Materialize an sbuild "unshare"-mode chroot from the SAME pinned, digest-
# addressed image the rest of the Debian 13 lane already trusts
# (recipes/debian-13/build.sh's IMAGE_REF@IMAGE_DIGEST), rather than
# introducing a second, differently-pinned buildroot (e.g. a mirror-snapshot
# debootstrap). See DESIGN.md section 4.
#
# Runs as the ORDINARY build user, not root. sbuild's unshare backend calls
# Sbuild::Utility::read_subuid_subgid, which looks up the *invoking* user in
# /etc/subuid and /etc/subgid and aborts with "invalid idmap" when there is no
# entry. A GitHub runner's /etc/subuid has an entry for the runner user and
# none for root, so running this (or sbuild itself) under `sudo` is what
# breaks unshare mode, not what enables it -- the opposite of what the first
# draft of this script assumed. Privileged operations are individually
# elevated with sudo below.
#
# Verified 2026-07-25 in a debian:trixie container (sbuild 0.89.3+deb13u4):
# an unprivileged user with a subuid/subgid range, given a chroot TARBALL,
# builds a package through --chroot-mode=unshare to "Status: successful".

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

[ "$(id -u)" -ne 0 ] ||
	die "run this as the ordinary build user, not root: sbuild's unshare backend
resolves the subuid/subgid range of the invoking user, and root has no
/etc/subuid entry on a GitHub runner."

note "installing the host-side build toolchain"
export DEBIAN_FRONTEND=noninteractive
sudo -n apt-get update -qq
# sbuild        the builder itself
# uidmap        newuidmap/newgidmap, used by sbuild-usernsexec
# iproute2      sbuild runs `ip link set lo up` in the build namespace; without
#               it the build fails with "failed running ip: No such file or
#               directory"
# debhelper     `dpkg-buildpackage -S` runs debian/rules clean on the host, and
#               both recipes' rules files are dh-based
# dpkg-dev/fakeroot  source-package assembly
sudo -n apt-get install -y --no-install-recommends \
	sbuild uidmap iproute2 debhelper dpkg-dev fakeroot

# An /etc/subuid + /etc/subgid range for the invoking user is a hard
# precondition of unshare mode. GitHub's runner images ship one; add it if
# this is running somewhere that does not.
user=$(id -un)
if ! grep -q "^$user:" /etc/subuid || ! grep -q "^$user:" /etc/subgid; then
	note "adding a subuid/subgid range for $user"
	sudo -n usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$user"
fi
grep "^$user:" /etc/subuid /etc/subgid

note "materializing $IMAGE into $CHROOT_TARBALL"
# The unshare backend takes a chroot TARBALL, not a directory: sbuild(1),
# --chroot: "With the unshare chroot mode, if this option is a path, then it
# specifies the location of the chroot tarball directly." Passing a directory
# makes sbuild treat it as a missing tarball and try to mmdebstrap a fresh
# one, which would silently replace the pinned buildroot with a live one.
mkdir -p "$(dirname -- "$CHROOT_TARBALL")"
rm -f "$CHROOT_TARBALL"

docker pull "$IMAGE" >/dev/null
cid=$(docker create --platform linux/amd64 "$IMAGE" true)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
docker export "$cid" > "$CHROOT_TARBALL"
trap - EXIT
docker rm -f "$cid" >/dev/null

# Assert the export really is a root filesystem before a build depends on it.
for path in ./etc/os-release ./proc/ ./sys/ ./dev/; do
	tar -tf "$CHROOT_TARBALL" "$path" >/dev/null 2>&1 ||
		die "$CHROOT_TARBALL does not contain $path; the docker export is not a usable chroot"
done
tar -xOf "$CHROOT_TARBALL" ./etc/os-release | head -3

printf 'OK: sbuild unshare chroot tarball ready at %s (%s bytes)\n' \
	"$CHROOT_TARBALL" "$(wc -c < "$CHROOT_TARBALL")"
