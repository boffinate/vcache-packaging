#!/bin/sh
#
# Pinned inputs for the scripts/ci/debian13/*.sh CI scripts.
#
# This file no longer states any pinned value. It reads them from
# recipes/debian-13/pins.env, the single definition it shares with
# recipes/debian-13/build.sh, and adds only what exists because CI runs the
# lane differently: where the chroot tarball lives.
#
# It used to be a hand-maintained mirror of build.sh's pinned block, with a
# header instructing whoever changed one to change the other. On 2026-07-25
# the cachetag re-pin moved CACHETAG_SOURCE_DATE_EPOCH in build.sh and not
# here, and nothing caught it: each copy was internally consistent, so the
# only symptom would have been a package whose file timestamps disagreed with
# its own changelog. Mirrors rot; readers do not.

_here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
_repo=$(CDPATH= cd -- "$_here/../../.." && pwd)

. "$_repo/recipes/debian-13/pins.env"

# Where make-chroot.sh materializes the sbuild unshare chroot. It lives under
# the lane's own work directory because sbuild runs inside a container that
# has that directory mounted; see scripts/ci/debian13/sbuild-lane.sh.
CHROOT_TARBALL=${CHROOT_TARBALL:-$_repo/dist/debian-13/work/chroot/$DEBIAN_DISTRIBUTION-amd64.tar}
