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

sha256() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	else
		shasum -a 256 "$1" | awk '{print $1}'
	fi
}

# Resolve the track on the host side too: this driver needs to know whether
# the run reads the Vinyl checkout at all (tarball mode never does) and, on
# the release track, to fetch and digest-check the upstream tarball the
# container source stage consumes. VINYL_TRACK is then passed into both
# containers so /recipes/cohort.env resolves to the same values inside.
. "$recipes/cohort.env"

mkdir -p "$out/logs" "$out/packages"

if [ "${VINYL_SOURCE_KIND:-git}" = tarball ]; then
	# Release track (drafted 2026-07-26, unexecuted until the first
	# release-track run). Mirrors the fetch in recipes/el9/build.sh -- the
	# procedure is duplicated deliberately, the pinned values are not: both
	# read cohort.env. The digest check is the sole authority on the bytes.
	tarball=$out/vinyl-cache-$VINYL_VERSION.tgz
	if [ ! -f "$tarball" ]; then
		note "fetching pinned upstream Vinyl tarball (release track)"
		curl -fsSL -o "$tarball" "$VINYL_SOURCE_URL"
	fi
	got=$(sha256 "$tarball")
	[ "$got" = "$VINYL_SOURCE_SHA256" ] ||
		die "upstream Vinyl tarball digest $got != pinned $VINYL_SOURCE_SHA256"
	printf 'OK: upstream Vinyl tarball digest matches the pinned value\n'

	# Nothing reads /vinyl-src in tarball mode; mount an empty stub so the
	# container layout stays identical across tracks and CI's release lanes
	# can skip the Vinyl checkout entirely.
	vinyl_src=$out/vinyl-src-unused
	mkdir -p "$vinyl_src"
else
	[ -d "$vinyl_src" ] || die "VINYL_GIT_DIR $vinyl_src does not exist"
fi

note "deps + source (unchanged: recipes/el9/container/build.sh)"
docker run --rm \
	-v "$recipes:/recipes:ro" \
	-v "$vinyl_src:/vinyl-src:ro" \
	-v "$cachetag_src:/cachetag:ro" \
	-v "$out:/out" \
	-e "VINYL_TRACK=$VINYL_TRACK" \
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
	-e "VINYL_TRACK=$VINYL_TRACK" \
	-w /out \
	"$image" \
	bash /ci/container-mock.sh

note "EL9 Mock lane done"
ls -la "$out/packages"
