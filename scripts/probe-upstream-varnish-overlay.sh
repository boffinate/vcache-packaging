#!/usr/bin/env bash
# Non-publishing Debian/amd64 proof for the upstream-Varnish overlay model.
# Downloads the exact upstream Varnish cohort, builds basicauth against its
# development package, stamps exact-version + strict-ABI dependencies into a
# small native package, then installs and starts the assembled cohort.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

[ $# -eq 3 ] || die "usage: probe-upstream-varnish-overlay.sh <engine-id> <target> <workdir>"
ENGINE_ID=$1
TARGET=$2
WORKDIR=$(prepare_workdir "$3")
VMOD_ID=basicauth
OVERLAY_PACKAGE_NAME="varnish-overlay-vmod-$VMOD_ID"
TAG="upstream-overlay-$ENGINE_ID-$TARGET-$VMOD_ID"
ENVFILE="$WORKDIR/tmp/$TAG.env"
python3 "$REPO_ROOT/tools/matrix.py" env --engine "$ENGINE_ID" --vmod "$VMOD_ID" --target "$TARGET" > "$ENVFILE"
. "$ENVFILE"
assert_target_platform "$TARGET_PLATFORM"
printf "TAG='%s'\nPKGFMT='deb'\nOVERLAY_PACKAGE_NAME='%s'\n" \
  "$TAG" "$OVERLAY_PACKAGE_NAME" >> "$ENVFILE"

INNER="$WORKDIR/tmp/$TAG.sh"
write_inner_prologue "$INNER" "$TAG"
cat >> "$INNER" <<'EOF'

step upstream-repository
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl gpg
mkdir -p /etc/apt/keyrings
curl -fsSL https://packages.varnish-software.com/varnish/varnish.pub.asc \
  | gpg --dearmor -o /etc/apt/keyrings/varnish.gpg
. /etc/os-release
printf 'deb [signed-by=/etc/apt/keyrings/varnish.gpg] https://packages.varnish-software.com/varnish/%s %s main\n' \
  "$ID" "$VERSION_CODENAME" > /etc/apt/sources.list.d/varnish.list
apt-get update -qq

step upstream-cohort
UPSTREAM_VERSION=$(apt-cache madison varnish | awk -v prefix="$ENGINE_VERSION-" '$3 ~ ("^" prefix) { print $3; exit }')
[ -n "$UPSTREAM_VERSION" ] \
  || { echo "upstream repository offers no varnish $ENGINE_VERSION package" >&2; exit 1; }
apt-get install -y --no-install-recommends \
  "varnish=$UPSTREAM_VERSION" "varnish-dev=$UPSTREAM_VERSION" \
  build-essential automake autoconf autoconf-archive libtool pkg-config git python3-docutils
STRICT_ABI=$(dpkg-query -W -f='${Provides}\n' varnish \
  | tr ',' '\n' | sed -n 's/^[[:space:]]*\(varnishd-abi-[^[:space:]]*\).*$/\1/p' | head -1)
[ -n "$STRICT_ABI" ] || { echo "upstream varnish package exposes no strict varnishd ABI" >&2; exit 1; }
VMOD_DIR=$(pkg-config --variable=vmoddir varnishapi)
[ -n "$VMOD_DIR" ] || { echo "upstream varnishapi reports no VMOD directory" >&2; exit 1; }

checkout_vmod

step build-overlay
cd "$SRC"
[ -x configure ] || { [ -f autogen.sh ] && sh ./autogen.sh || autoreconf -fi; }
./configure
make -j1
STAGE=/work/tmp/$TAG-stage
rm -rf "$STAGE"
make DESTDIR="$STAGE" install
find "$STAGE" \( -name '*.la' -o -name '*.a' \) -delete
EXPECTED="$STAGE$VMOD_DIR/libvmod_$VMOD_ID.so"
[ -f "$EXPECTED" ] || { echo "overlay build did not install $EXPECTED" >&2; exit 1; }

step package-overlay
mkdir -p "$STAGE/DEBIAN"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: ${OVERLAY_PACKAGE_NAME}
Version: ${VMOD_VERSION}-1~upstream${ENGINE_VERSION}
Architecture: ${TARGET_PACKAGE_ARCH}
Maintainer: Vinyl Cache matrix CI <vcache-matrix-ci@invalid>
Depends: varnish (= ${UPSTREAM_VERSION}), ${STRICT_ABI}, @SHLIBS_DEPENDS@
Description: Experimental basicauth VMOD overlay for the upstream Varnish cohort
 This non-publishing proof package is built against the exact upstream Varnish
 package cohort and bound to both its package version and strict VMOD ABI.
CONTROL
mkdir -p debian
cat > debian/control <<CONTROL
Source: ${OVERLAY_PACKAGE_NAME}
Section: web
Priority: optional
Maintainer: Vinyl Cache matrix CI <vcache-matrix-ci@invalid>

Package: ${OVERLAY_PACKAGE_NAME}
Architecture: any
Description: dependency-analysis metadata for the experimental overlay proof
CONTROL
dpkg-shlibdeps -O -e"$EXPECTED" -T"$STAGE/DEBIAN/substvars" -l"$(dirname "$EXPECTED")"
SHLIBS_DEPENDS=$(sed -n 's/^shlibs:Depends=//p' "$STAGE/DEBIAN/substvars")
[ -n "$SHLIBS_DEPENDS" ] || { echo "dpkg-shlibdeps produced no runtime dependencies" >&2; exit 1; }
sed -i "s/@SHLIBS_DEPENDS@/$SHLIBS_DEPENDS/" "$STAGE/DEBIAN/control"
OUT=/work/packages/upstream-varnish-overlay
mkdir -p "$OUT"
PACKAGE="$OUT/${OVERLAY_PACKAGE_NAME}_${VMOD_VERSION}-1~upstream${ENGINE_VERSION}_${TARGET_PACKAGE_ARCH}.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$PACKAGE"
dpkg-deb -f "$PACKAGE" Depends | grep -F "$STRICT_ABI"
python3 /repo/tools/package_contract.py \
  --format deb --package "$PACKAGE" --name "$OVERLAY_PACKAGE_NAME" \
  --arch "$TARGET_PACKAGE_ARCH" --engine-package varnish --vmod-dir "$VMOD_DIR" \
  --modules $VMOD_MODULES --manifest-out "$OUT/PACKAGE-CONTRACT.json"

step assembled-cohort
apt-get install -y "$PACKAGE"
printf 'vcl 4.1;\nimport %s;\nbackend default none;\n' "$VMOD_ID" > /tmp/overlay.vcl
INSTANCE=$(mktemp -d)
varnishd -j none -F -a 127.0.0.1:0 -n "$INSTANCE" -f /tmp/overlay.vcl > /tmp/overlay.log 2>&1 &
PID=$!
sleep 2
kill -0 "$PID" || { tail -n 80 /tmp/overlay.log >&2; exit 1; }
kill -TERM "$PID"; wait "$PID" || true

step cohort-manifest
SOURCE_COMMIT=$(cat "/work/tmp/$TAG.commit")
PACKAGE_SHA256=$(sha256sum "$PACKAGE" | awk '{print $1}')
export UPSTREAM_VERSION STRICT_ABI VMOD_DIR SOURCE_COMMIT PACKAGE_SHA256 PACKAGE VMOD_ID
python3 - <<'PY'
import json, os
manifest = {
    "schema": "external-cohort/1",
    "provider": "packages.varnish-software.com",
    "target": os.environ["TARGET_ID"],
    "engine": {"package": "varnish", "version": os.environ["UPSTREAM_VERSION"],
               "strict_abi": os.environ["STRICT_ABI"]},
    "overlay": {"package": os.path.basename(os.environ["PACKAGE"]),
                "sha256": os.environ["PACKAGE_SHA256"],
                "vmod": os.environ["VMOD_ID"], "source_commit": os.environ["SOURCE_COMMIT"],
                "vmod_dir": os.environ["VMOD_DIR"]},
    "published": False,
}
with open("/work/packages/upstream-varnish-overlay/COHORT.json", "w") as f:
    json.dump(manifest, f, indent=2, sort_keys=True)
    f.write("\n")
PY
echo "experimental upstream Varnish overlay proof passed"
EOF

LOG="$WORKDIR/logs/$TAG.log"
run_in_container "$TARGET_IMAGE" "$TARGET_PLATFORM" "$WORKDIR" "$TAG.sh" "$LOG"
