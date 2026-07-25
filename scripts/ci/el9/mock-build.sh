#!/bin/sh
#
# Host-side driver for the EL9 Mock lane. Replaces recipes/el9/build.sh's
# in-container `rpmbuild --rebuild` calls with Mock, while reusing that
# recipe's `deps`/`source` stages (pure git-archive/tar assembly, not a
# build) and its `report`/`lint` stages (post-build inspection of already-
# built RPMs) completely unchanged. See DESIGN.md section 5.
#
# Usage: mock-build.sh VINYL_GIT_DIR CACHETAG_GIT_DIR EL9_IMAGE
#
# Runs as the ordinary build user. `docker run --privileged` is what gives
# Mock the chroot/bind-mount isolation it needs, and that needs docker-group
# membership, not root: running the script itself under `sudo` would leave
# dist/el9/ owned by root, and the later non-privileged steps
# (recipes/el9/build.sh --smoke-only, artifact upload) write into it.

set -eu

vinyl_src=${1:?VINYL_GIT_DIR required}
cachetag_src=${2:?CACHETAG_GIT_DIR required}
image=${3:?EL9_IMAGE required}

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../../.." && pwd)
recipes=$repo/recipes/el9
out=$repo/dist/el9

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

mkdir -p "$out/logs" "$out/packages"

note "deps + source (unchanged: recipes/el9/container/build.sh)"
docker run --rm \
	-v "$recipes:/recipes:ro" \
	-v "$vinyl_src:/vinyl-src:ro" \
	-v "$cachetag_src:/cachetag:ro" \
	-v "$out:/out" \
	-w /out \
	"$image" \
	bash /recipes/container/build.sh deps source

note "Mock: vinyl-cache, then libvmod-cachetag (privileged: Mock needs chroot/bind-mount isolation)"
docker run --privileged --rm \
	-v "$recipes:/recipes:ro" \
	-v "$here:/ci:ro" \
	-v "$vinyl_src:/vinyl-src:ro" \
	-v "$cachetag_src:/cachetag:ro" \
	-v "$out:/out" \
	-w /out \
	"$image" \
	bash /ci/container-mock.sh

note "EL9 Mock lane done"
ls -la "$out/packages"
