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
# The 2026-07-25 cachetag re-pin (92fac70) moved
# CACHETAG_SOURCE_DATE_EPOCH in build.sh and left this copy behind, which is
# exactly the divergence this header warns about: the sbuild lane would have
# stamped the package with one epoch while the changelog build.sh substituted
# carried another. If a later change adds a value here, add it to build.sh's
# block too, and vice versa.

VINYL_GIT_COMMIT=25761f8505817ac50df994270bfe75b60073e33e
VINYL_STRICT_ABI=$VINYL_GIT_COMMIT
VINYL_UPSTREAM_VERSION=9.0.0~git20260520.25761f8505
VINYL_PACKAGE_REVISION=1
VINYL_PACKAGE_VERSION=$VINYL_UPSTREAM_VERSION-$VINYL_PACKAGE_REVISION
VINYL_SOURCE_DATE_EPOCH=1779265093
VINYL_VRT_EXPECTED=23.0

CACHETAG_GIT_COMMIT=fcc369d23b199cc8e41086f28f2322256a8843d9
CACHETAG_VERSION=1.0.0
CACHETAG_PACKAGE_REVISION=1
CACHETAG_DEBIAN_VERSION=$CACHETAG_VERSION-$CACHETAG_PACKAGE_REVISION
CACHETAG_SOURCE_DATE_EPOCH=1784997430

DEBIAN_DISTRIBUTION=trixie

IMAGE_REF=${IMAGE_REF:-debian:trixie}
IMAGE_DIGEST=${IMAGE_DIGEST:-sha256:fac46bff2e02f51425b6e33b0e1169f55dfb053d83511ca28aa50c09fd5ed7a4}
IMAGE="$IMAGE_REF@$IMAGE_DIGEST"

# Where make-chroot.sh materializes the sbuild unshare chroot. sbuild's
# unshare backend consumes a TARBALL (sbuild(1), --chroot), and it must be
# readable by the unprivileged user that runs sbuild, so this lives in that
# user's cache directory -- which is also where sbuild looks by default.
CHROOT_TARBALL=${CHROOT_TARBALL:-${XDG_CACHE_HOME:-$HOME/.cache}/sbuild/$DEBIAN_DISTRIBUTION-amd64.tar}
