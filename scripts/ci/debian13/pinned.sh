#!/bin/sh
#
# Pinned inputs shared by the scripts/ci/debian13/*.sh CI scripts.
#
# This mirrors the "PINNED INPUTS" block at the top of
# recipes/debian-13/build.sh verbatim. It is the one deliberate piece of
# constant duplication in this design beyond assert-packages.sh (see
# DESIGN.md section 2 and section 11): each CI step after `build.sh source`
# runs in its own shell invocation (a separate GitHub Actions step), so
# values build.sh computed in its own process are not otherwise visible to
# them. Keep this file byte-identical to build.sh's block; a divergence here
# is a bug, not a place to improvise a different value.
#
# DRAFT, unexecuted -- see ../../../DESIGN.md.

VINYL_GIT_COMMIT=a90954814766d933a75d4c808c449cb9bc0ae3d3
VINYL_STRICT_ABI=$VINYL_GIT_COMMIT
VINYL_UPSTREAM_VERSION=9.0.0~git20260613.a909548147
VINYL_PACKAGE_REVISION=1
VINYL_PACKAGE_VERSION=$VINYL_UPSTREAM_VERSION-$VINYL_PACKAGE_REVISION
VINYL_SOURCE_DATE_EPOCH=1781307021
VINYL_VRT_EXPECTED=23.0

CACHETAG_VERSION=1.0.0
CACHETAG_PACKAGE_REVISION=1
CACHETAG_DEBIAN_VERSION=$CACHETAG_VERSION-$CACHETAG_PACKAGE_REVISION
CACHETAG_SOURCE_DATE_EPOCH=1784926281

DEBIAN_DISTRIBUTION=trixie

IMAGE_REF=${IMAGE_REF:-debian:trixie}
IMAGE_DIGEST=${IMAGE_DIGEST:-sha256:fac46bff2e02f51425b6e33b0e1169f55dfb053d83511ca28aa50c09fd5ed7a4}
IMAGE="$IMAGE_REF@$IMAGE_DIGEST"

# Where make-chroot.sh materializes the sbuild unshare chroot.
CHROOT_DIR=${CHROOT_DIR:-/srv/chroot/vinyl-trixie-amd64}
