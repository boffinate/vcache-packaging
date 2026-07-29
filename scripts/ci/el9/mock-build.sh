#!/bin/sh
#
# Host-side driver for the EL9 Mock lane. Replaces recipes/el9/build.sh's
# in-container `rpmbuild --rebuild` calls with Mock, while reusing that
# recipe's `deps`/`source` stages (pure git-archive/tar assembly, not a
# build) and its `report`/`lint` stages (post-build inspection of already-
# built RPMs) completely unchanged. See DESIGN.md section 5.
#
# Usage: mock-build.sh VINYL_GIT_DIR CACHETAG_GIT_DIR EL9_IMAGE [all|engine|vmod]
#
# Runs as the ordinary build user. `docker run --privileged` is what gives
# Mock the chroot/bind-mount isolation it needs, and that needs docker-group
# membership, not root: running the script itself under `sudo` would leave
# dist/el9/ owned by root, and the later non-privileged steps
# (recipes/el9/build.sh --smoke-only, artifact upload) write into it.
#
# The scope argument is Phase 2 of
# docs/20260728_0833_plan_vmod-matrix-failure-isolation.md: `engine` builds only
# the Vinyl RPMs, `vmod` builds only libvmod-cachetag against Vinyl RPMs already
# present in dist/el9/packages. The unused source directory is replaced with an
# empty stub in each narrowed scope, the same way tarball-mode release runs have
# always stubbed out /vinyl-src, so a job needs only the source it actually
# reads. Default `all` is the local, whole-cohort form and is unchanged.

set -eu

# Each source directory is required only by the scope that reads it, so an
# engine job can pass an empty second argument and a VMOD job an empty first
# one rather than inventing a path for a checkout it deliberately does not have.
vinyl_src=${1-}
cachetag_src=${2-}
image=${3:?EL9_IMAGE required}
scope=${4:-all}

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../../.." && pwd)
recipes=$repo/recipes/el9
out=$repo/dist/el9

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

case $scope in
all | engine | vmod) : ;;
*) die "unknown scope '$scope' (all|engine|vmod)" ;;
esac

[ "$scope" = vmod ] || [ -n "$vinyl_src" ] || die "VINYL_GIT_DIR required for scope $scope"
[ "$scope" = engine ] || [ -n "$cachetag_src" ] || die "CACHETAG_GIT_DIR required for scope $scope"

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

if [ "$scope" = vmod ]; then
	# Nothing in this scope reads a Vinyl source at all: the engine arrives as
	# built RPMs. Not even the tarball fetch below runs, because the source
	# stage that consumes it does not run either.
	vinyl_src=$out/vinyl-src-unused
	mkdir -p "$vinyl_src"
elif [ "${VINYL_SOURCE_KIND:-git}" = tarball ]; then
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

if [ "$scope" = engine ]; then
	# Same treatment in the other direction: an engine job has no VMOD source
	# and must not need one. The mount stays so the container layout is
	# identical across scopes.
	cachetag_src=$out/cachetag-src-unused
	mkdir -p "$cachetag_src"
fi

# `source` is the Vinyl source stage; there is no cachetag equivalent, because
# the cachetag build consumes its release archive directly. So this whole run is
# engine work and is skipped when only the VMOD is being built.
if [ "$scope" != vmod ]; then
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
fi

note "Mock ($scope): privileged, because Mock needs chroot/bind-mount isolation"
# /ci is scripts/ci, not scripts/ci/el9: since Step 7 Wave 0 container-mock.sh
# sources the shared Mock driver and the shared package checks out of
# scripts/ci/lib, which are the same files the generated-recipe lane uses. The
# mount widened by one directory level; nothing else about the invocation
# changed, and a read-only mount of a checkout cannot reach a package.
docker run --privileged --rm \
	-v "$recipes:/recipes:ro" \
	-v "$repo/scripts/ci:/ci:ro" \
	-v "$vinyl_src:/vinyl-src:ro" \
	-v "$cachetag_src:/cachetag:ro" \
	-v "$out:/out" \
	-e "VINYL_TRACK=$VINYL_TRACK" \
	-e "MOCK_SCOPE=$scope" \
	-w /out \
	"$image" \
	bash /ci/el9/container-mock.sh

note "EL9 Mock lane done (scope: $scope)"
ls -la "$out/packages"
