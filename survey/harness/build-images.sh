#!/bin/sh
# Build the two survey lane images. Stages the pinned inputs (varnish release
# tarball, vinyl .debs) into stage/ and runs docker build for each lane.
#
# Both lanes are built for the same platform so cross-lane results compare
# like with like: the host arch when local recipes/debian-13 debs exist,
# else linux/amd64 using the verified pre-release debs.

set -eu
cd "$(dirname "$0")"
. ./pins.env

note() { printf '### %s\n' "$*" >&2; }

mkdir -p stage/debs
rm -f stage/debs/*.deb 2>/dev/null || true

# Varnish tarball: reuse the survey cache download when present.
tarball="stage/varnish-${VARNISH_VERSION}.tar.gz"
if [ ! -f "$tarball" ]; then
    if [ -f "../cache/varnish-${VARNISH_VERSION}.tar.gz" ]; then
        cp "../cache/varnish-${VARNISH_VERSION}.tar.gz" "$tarball"
    else
        curl -fsSL --max-time 600 -o "$tarball" "$VARNISH_SOURCE_URL"
    fi
fi
echo "${VARNISH_SOURCE_SHA256}  ${tarball}" | shasum -a 256 -c - >/dev/null \
    || { echo "varnish tarball sha256 mismatch" >&2; exit 1; }

# Vinyl debs: local recipes output first, else the pre-release amd64 assets.
platform=""
runtime_deb=$(ls "${VINYL_LOCAL_DIST}"/vinyl-cache_*.deb 2>/dev/null | head -n 1 || true)
dev_deb=$(ls "${VINYL_LOCAL_DIST}"/vinyl-cache-dev_*.deb 2>/dev/null | head -n 1 || true)
if [ -n "$runtime_deb" ] && [ -n "$dev_deb" ]; then
    note "using local dist debs: $(basename "$runtime_deb"), $(basename "$dev_deb")"
    cp "$runtime_deb" "$dev_deb" stage/debs/
else
    note "no local dist debs; downloading verified amd64 pre-release assets"
    platform="--platform linux/amd64"
    for asset_sha in \
        "${VINYL_DEB_RUNTIME_AMD64} ${VINYL_DEB_RUNTIME_AMD64_SHA256}" \
        "${VINYL_DEB_DEV_AMD64} ${VINYL_DEB_DEV_AMD64_SHA256}"; do
        asset=${asset_sha% *}
        sha=${asset_sha#* }
        curl -fsSL --max-time 600 -o "stage/debs/${asset}" "${VINYL_RELEASE_BASE}/${asset}"
        echo "${sha}  stage/debs/${asset}" | shasum -a 256 -c - >/dev/null \
            || { echo "${asset} sha256 mismatch" >&2; exit 1; }
    done
fi

note "building vmod-survey-varnish9"
# shellcheck disable=SC2086
docker build $platform -f Dockerfile.varnish9 \
    --build-arg IMAGE="$IMAGE" \
    --build-arg VARNISH_SOURCE_SHA256="$VARNISH_SOURCE_SHA256" \
    -t vmod-survey-varnish9 .

note "building vmod-survey-vinyl9"
# shellcheck disable=SC2086
docker build $platform -f Dockerfile.vinyl9 \
    --build-arg IMAGE="$IMAGE" \
    -t vmod-survey-vinyl9 .

note "lane images ready"
docker image ls --format '{{.Repository}} {{.ID}} {{.Size}}' | grep '^vmod-survey-' >&2
