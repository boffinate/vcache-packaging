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
	uidmap iproute2 debhelper dpkg-dev fakeroot

#
# sbuild comes from backports, deliberately. ubuntu-latest ships sbuild
# 0.85.10ubuntu0.2, whose unshare backend cannot use a chroot tarball on this
# runner: it unpacks the tarball and then dies with
#   runuser: failed to execute sh: Permission denied
#   E: read_command failed to execute dpkg
#   E: Can't determine architecture of chroot:
# (measured, run 30167536066). 0.89.3 from debian:trixie builds the same shape
# of job to "Status: successful" in a container, and <codename>-backports
# carries 0.88.3, which has the reworked unshare backend rather than the
# 2023-era one 0.85 froze on.
#
. /etc/os-release
backports="${VERSION_CODENAME:?}-backports"
note "installing sbuild from $backports"
printf 'deb http://archive.ubuntu.com/ubuntu %s main universe\n' "$backports" |
	sudo -n tee /etc/apt/sources.list.d/sbuild-backports.list >/dev/null
sudo -n apt-get update -qq
# libsbuild-perl carries the backend this needs and sbuild depends on the
# exact version, so both come from the same suite.
sudo -n apt-get install -y --no-install-recommends -t "$backports" \
	sbuild libsbuild-perl
sbuild --version | head -1

# An /etc/subuid + /etc/subgid range for the invoking user is a hard
# precondition of unshare mode. GitHub's runner images ship one; add it if
# this is running somewhere that does not.
user=$(id -un)
if ! grep -q "^$user:" /etc/subuid || ! grep -q "^$user:" /etc/subgid; then
	note "adding a subuid/subgid range for $user"
	sudo -n usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$user"
fi
grep "^$user:" /etc/subuid /etc/subgid

#
# The other hard precondition: this user must be able to create a user
# namespace. Ubuntu 24.04 -- which is what ubuntu-latest resolves to -- ships
# `kernel.apparmor_restrict_unprivileged_userns=1`, which denies exactly that
# to unconfined programs, and sbuild reports the consequence as the unhelpful
# "E: Can't determine architecture of chroot:" with an empty architecture,
# because its chroot session never started.
#
# Probe it here rather than letting sbuild fail opaquely three steps later.
#
note "user namespace preflight"
userns_sysctl=kernel.apparmor_restrict_unprivileged_userns
if sysctl -n "$userns_sysctl" >/dev/null 2>&1; then
	printf '%s = %s\n' "$userns_sysctl" "$(sysctl -n "$userns_sysctl")"
else
	printf '%s: not present on this kernel\n' "$userns_sysctl"
fi

if unshare --user --map-root-user true 2>/dev/null; then
	printf 'OK: this user can create a user namespace\n'
elif sysctl -n "$userns_sysctl" >/dev/null 2>&1; then
	note "user namespaces are denied; relaxing $userns_sysctl"
	# Restricting unprivileged user namespaces is a hardening measure for
	# multi-user systems. This is a single-use ephemeral runner whose whole
	# job is to run an unprivileged, namespaced build, and the alternative
	# is running sbuild as root, which its unshare backend refuses.
	sudo -n sysctl -w "$userns_sysctl=0"
	unshare --user --map-root-user true ||
		die "user namespaces are still denied after relaxing $userns_sysctl"
	printf 'OK: user namespaces available after relaxing the sysctl\n'
else
	die "this user cannot create a user namespace and $userns_sysctl does not exist to relax; sbuild --chroot-mode=unshare cannot work here"
fi

#
# Creating the namespace is necessary but not sufficient. Ubuntu 24.04 has a
# second knob, kernel.apparmor_restrict_unprivileged_unconfined, which
# transitions an unconfined program that creates a user namespace into a
# restricted AppArmor profile. Under it the namespace exists but execs inside
# it are refused -- which is exactly the EACCES on dpkg that survived the
# sbuild upgrade, the chroot repack and the session-directory move.
#
unconfined_sysctl=kernel.apparmor_restrict_unprivileged_unconfined
if sysctl -n "$unconfined_sysctl" >/dev/null 2>&1; then
	printf '%s = %s\n' "$unconfined_sysctl" "$(sysctl -n "$unconfined_sysctl")"
	if [ "$(sysctl -n "$unconfined_sysctl")" != "0" ]; then
		note "relaxing $unconfined_sysctl"
		sudo -n sysctl -w "$unconfined_sysctl=0"
	fi
else
	printf '%s: not present on this kernel\n' "$unconfined_sysctl"
fi

#
# sbuild's unshare backend unpacks the chroot into $unshare_tmpdir_template
# (/tmp/tmp.sbuild.XXXXXXXXXX) and execs dpkg inside it, which failed with
#
#   Can't exec "dpkg": Permission denied at /usr/libexec/sbuild-usernsexec line 561
#
# EACCES on a mode-0755 binary looks like a noexec mount, but it is not:
# measured on the runner, /tmp is not a separate mount and does permit exec
# (run 30168996900). Pointing the session directory elsewhere made it worse,
# not better -- the subuid-mapped user could not traverse into $HOME -- so the
# default location stays and the measurement stays with it, to keep the next
# session failure from being blamed on the mount again.
#
note "exec-from-/tmp preflight"
tmp_exec_probe=$(mktemp /tmp/exec-probe.XXXXXX)
printf '#!/bin/sh\necho executable\n' > "$tmp_exec_probe"
chmod 0755 "$tmp_exec_probe"
if "$tmp_exec_probe" >/dev/null 2>&1; then
	printf 'OK: /tmp permits exec\n'
else
	printf 'NOTE: /tmp does NOT permit exec -- this is what breaks sbuild unshare\n'
fi
grep -E ' /tmp ' /proc/mounts || printf '/tmp is not a separate mount\n'
rm -f "$tmp_exec_probe"

note "materializing $IMAGE into $CHROOT_TARBALL"
# The unshare backend takes a chroot TARBALL, not a directory: sbuild(1),
# --chroot: "With the unshare chroot mode, if this option is a path, then it
# specifies the location of the chroot tarball directly." Passing a directory
# makes sbuild treat it as a missing tarball and try to mmdebstrap a fresh
# one, which would silently replace the pinned buildroot with a live one.
mkdir -p "$(dirname -- "$CHROOT_TARBALL")"
rm -f "$CHROOT_TARBALL"

#
# The export is unpacked and repacked rather than used as sbuild's chroot
# tarball directly. A raw `docker export` gets as far as being unpacked and
# then nothing inside it can be executed:
#
#   I: Unpacking /home/runner/.cache/sbuild/trixie-amd64.tar to /tmp/tmp.sbuild.*
#   I: Creating chroot session...
#   Can't exec "dpkg": Permission denied at /usr/libexec/sbuild-usernsexec line 561
#   E: Can't determine architecture of chroot:
#
# reproduced locally against a real export (sbuild 0.88.3 and 0.89.3 alike),
# while a rootfs repacked this way builds a package to "Status: successful".
# Three differences, all of which this reproduces deliberately:
#
#   --owner=0 --group=0   every member is root-owned. sbuild unpacks inside a
#                         user namespace that maps only root and the subuid
#                         range, so members owned by the runner's uid land as
#                         an unmapped owner and cannot be executed.
#   ./-prefixed members   the shape `tar -C <root> -cf - .` produces.
#   no device nodes       dev/ stays as an empty mount point; sbuild
#                         bind-mounts the real /dev per invocation, and
#                         device nodes cannot be created in a user namespace
#                         anyway.
#
docker pull "$IMAGE" >/dev/null
cid=$(docker create --platform linux/amd64 "$IMAGE" true)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
rootfs=$(mktemp -d)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true; rm -rf "$rootfs"' EXIT
docker export "$cid" | tar -C "$rootfs" -x --exclude='dev/*' --no-same-owner
docker rm -f "$cid" >/dev/null
mkdir -p "$rootfs/dev" "$rootfs/proc" "$rootfs/sys"
tar -C "$rootfs" --owner=0 --group=0 --numeric-owner -cf "$CHROOT_TARBALL" .
rm -rf "$rootfs"
trap - EXIT

# Assert the export really is a root filesystem before a build depends on it.
# `docker export` names members without a leading "./" (etc/, proc/, dev/),
# unlike `tar -C / -cf - .`, so accept either spelling rather than assuming.
#
# The member list goes to a file, and grep reads that file rather than a pipe.
# Under `set -o pipefail`, `printf '%s\n' "$members" | grep -q ...` reports 141
# even on a match: grep exits at the first hit, printf takes SIGPIPE, and
# pipefail surfaces printf's status as the pipeline's.
#
members=$(mktemp)
trap 'rm -f "$members"' EXIT
tar -tf "$CHROOT_TARBALL" > "$members"
for path in etc/os-release proc/ sys/ dev/; do
	grep -qx -e "$path" -e "./$path" "$members" ||
		die "$CHROOT_TARBALL does not contain $path; the docker export is not a usable chroot"
done
if grep -qx etc/os-release "$members"; then prefix=""; else prefix="./"; fi
# /etc/os-release is a symlink to ../usr/lib/os-release on Debian, so read the
# target: extracting the symlink member itself yields no content. Captured
# rather than piped to `head` for the pipefail reason above -- `head` exiting
# early would leave tar with SIGPIPE.
os_release=$(tar -xOf "$CHROOT_TARBALL" "${prefix}usr/lib/os-release" 2>/dev/null || true)
[ -n "$os_release" ] || die "$CHROOT_TARBALL has no readable os-release"
printf '%s\n' "$os_release" | sed -n '1,3p'

printf 'OK: sbuild unshare chroot tarball ready at %s (%s bytes)\n' \
	"$CHROOT_TARBALL" "$(wc -c < "$CHROOT_TARBALL")"
