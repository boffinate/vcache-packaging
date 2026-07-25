#!/bin/bash
#
# Build the sbuild chroot tarball at $CHROOT_TARBALL. Same name and same
# contract as before; what changed is where the chroot comes from.
#
# It used to be `docker export` of the pinned debian:trixie image, adapted
# afterwards. That was abandoned on 2026-07-25 after six CI runs, each of
# which found another way in which a container image is not a build chroot:
# missing mount points, no /run/lock for the lock file sbuild creates at
# /var/lock/sbuild inside the chroot, no resolv.conf (docker keeps it outside
# the image), no build-essential, and a rootfs whose ownership and member
# naming sbuild's unshare backend would not accept. The full elimination
# table is in docs/20260725_1655_note_step-10-ci-first-run-findings.md.
#
# mmdebstrap builds a real buildd chroot in one step instead, and pins it
# harder than the image did: the packages come from the snapshot.debian.org
# timestamp recorded in pins.env, so the buildroot is reproducible rather than
# merely auditable. Measured at 45-80s.
#
# mmdebstrap runs inside the pinned image rather than on the runner, for the
# same reason sbuild does: the runner's userland is not an input this
# repository can pin. The host contributes a digest and a mount.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

repo_dir=$(CDPATH= cd -- "$here/../../.." && pwd)
out_dir=$repo_dir/dist/debian-13

mkdir -p "$(dirname -- "$CHROOT_TARBALL")" "$out_dir/logs"
rm -f "$CHROOT_TARBALL"

note "building the buildd chroot from $DEBIAN_SNAPSHOT_URI"
docker run --privileged --rm -i \
	-v "$out_dir:/out" \
	-e "DEBIAN_DISTRIBUTION=$DEBIAN_DISTRIBUTION" \
	-e "DEBIAN_SNAPSHOT_URI=$DEBIAN_SNAPSHOT_URI" \
	-e "CHROOT_TARBALL_IN_CONTAINER=/out/work/chroot/$DEBIAN_DISTRIBUTION-amd64.tar" \
	"$IMAGE" \
	bash /dev/stdin <<'CONTAINER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends mmdebstrap ca-certificates
mkdir -p "$(dirname "$CHROOT_TARBALL_IN_CONTAINER")"

# --variant=buildd is the difference between a rootfs and a build chroot: it
#   installs build-essential, which sbuild expects to find already there.
# --include=ca-certificates because the snapshot is served over https and the
#   buildd variant carries no CA bundle, so apt inside the chroot could not
#   fetch its own package lists ("Package build dependencies not satisfied").
# --aptopt Check-Valid-Until because a snapshot's Release file is, by
#   construction, older than it claims to be valid for.
mmdebstrap \
	--variant=buildd \
	--mode=root \
	--architectures=amd64 \
	--include=ca-certificates \
	--aptopt='Acquire::Check-Valid-Until "false";' \
	--format=tar \
	"$DEBIAN_DISTRIBUTION" \
	"$CHROOT_TARBALL_IN_CONTAINER" \
	"deb $DEBIAN_SNAPSHOT_URI $DEBIAN_DISTRIBUTION main"
CONTAINER

[ -f "$CHROOT_TARBALL" ] || die "mmdebstrap produced no tarball at $CHROOT_TARBALL"

#
# Record what the snapshot resolved to. The snapshot URI is the pin; this is
# the evidence of what it contained, and the Debian counterpart of the EL9
# lane's logs/buildroot-packages.tsv.
#
note "recording the buildroot package list"
tar -xOf "$CHROOT_TARBALL" ./var/lib/dpkg/status 2>/dev/null |
	awk '/^Package: / { p = $2 } /^Version: / { print p"="$2 }' |
	sort > "$out_dir/logs/buildroot-packages.txt"
[ -s "$out_dir/logs/buildroot-packages.txt" ] ||
	die "$CHROOT_TARBALL has no readable dpkg status; that is not a Debian chroot"

printf 'snapshot : %s\npackages : %s\ntarball  : %s (%s bytes)\n' \
	"$DEBIAN_SNAPSHOT" \
	"$(wc -l < "$out_dir/logs/buildroot-packages.txt" | tr -d ' ')" \
	"$CHROOT_TARBALL" "$(wc -c < "$CHROOT_TARBALL")"
grep -E '^(build-essential|dpkg|gcc|libc6)=' "$out_dir/logs/buildroot-packages.txt" || true
