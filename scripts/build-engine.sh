#!/usr/bin/env bash
# scripts/build-engine.sh <engine-id> <target> <workdir>
#
# Build one engine on one target inside a container (DESIGN.md "Script
# contracts"). Always produces a relocatable-by-agreement prefix tarball
#   <workdir>/artifacts/engine-<id>-<target>-prefix.tar.gz
# (configured --prefix=/opt/<id>; consumers untar at / so the baked-in paths
# are correct by construction). When the catalog says packages "true", also
# builds the selected family's runtime/development .deb or .rpm set into
#   <workdir>/artifacts/engine-<id>-<target>-pkgs/   (consumed by build-vmod)
# and mirrors it into <workdir>/packages/ for release collection. Package
# identity comes from the selected engine family contract in matrix.py env.
# Writes the engine's own cell result: row = engine id, mode = "engine".
set -euo pipefail
. "$(dirname "$0")/lib.sh"

[ $# -eq 3 ] || die "usage: build-engine.sh <engine-id> <target> <workdir>"
ENGINE_ARG=$1 TARGET=$2
WORKDIR=$(prepare_workdir "$3")
TAG="engine-$ENGINE_ARG-$TARGET"
ENVFILE="$WORKDIR/tmp/$TAG.env"

# Pins come exclusively from matrix.py env (sh-sourceable KEY=value lines).
python3 "$REPO_ROOT/tools/matrix.py" env --engine "$ENGINE_ARG" --target "$TARGET" > "$ENVFILE" \
  || infra_cell "$WORKDIR" "$ENGINE_ARG" "$ENGINE_ARG" "$TARGET" engine "" "matrix.py env failed for engine $ENGINE_ARG"
. "$ENVFILE"
IMAGE=${TARGET_IMAGE:?}
PKGFMT=${TARGET_FORMAT:?}
assert_target_platform "${TARGET_PLATFORM:?}" \
  || infra_cell "$WORKDIR" "$ENGINE_ARG" "$ENGINE_ARG" "$TARGET" engine "" "target platform does not match this host"

ENGINE_ID=${ENGINE_ID:-$ENGINE_ARG}
ENGINE_VERSION=${ENGINE_VERSION:?}
ENGINE_PACKAGE_NAME=${ENGINE_RUNTIME_PACKAGE:?}
ENGINE_DEVELOPMENT_PACKAGE=${ENGINE_DEVELOPMENT_PACKAGE:?}
ENGINE_SOURCE_NAME=${ENGINE_SOURCE_NAME:?}
ENGINE_RPM_ARCHIVE_STEM=${ENGINE_RPM_ARCHIVE_STEM:?}
ENGINE_RECIPE_DIR=${ENGINE_RECIPE_DIR:?}
ENGINE_DAEMON=${ENGINE_DAEMON:?}
if [ "${ENGINE_PACKAGES:-false}" = "true" ]; then
  ENGINE_PACKAGE_REVISION=${ENGINE_PACKAGE_REVISION:?}
fi
PREFIX="/opt/$ENGINE_ID"
if [ "${ENGINE_KIND:-release}" = trunk ]; then REF=${ENGINE_BRANCH:-trunk}; else REF=$ENGINE_VERSION; fi

{
  printf "TAG='%s'\nTARGET='%s'\nPKGFMT='%s'\nPREFIX='%s'\n" "$TAG" "$TARGET" "$PKGFMT" "$PREFIX"
  printf "ENGINE_ID='%s'\nENGINE_VERSION='%s'\n" "$ENGINE_ID" "$ENGINE_VERSION"
  printf "ENGINE_PACKAGE_NAME='%s'\nENGINE_DEVELOPMENT_PACKAGE='%s'\n" "$ENGINE_PACKAGE_NAME" "$ENGINE_DEVELOPMENT_PACKAGE"
  printf "ENGINE_SOURCE_NAME='%s'\nENGINE_RPM_ARCHIVE_STEM='%s'\n" "$ENGINE_SOURCE_NAME" "$ENGINE_RPM_ARCHIVE_STEM"
  printf "ENGINE_RECIPE_DIR='%s'\nENGINE_DAEMON='%s'\n" "$ENGINE_RECIPE_DIR" "$ENGINE_DAEMON"
  printf "MAINTAINER='%s'\n" "${MAINTAINER:-Vinyl Cache matrix CI <vcache-matrix-ci@invalid>}"
} >> "$ENVFILE"

INNER="$WORKDIR/tmp/$TAG.sh"
write_inner_prologue "$INNER" "$TAG"
cat >> "$INNER" <<'EOF'

step deps
case "$PKGFMT" in
deb)
  apt_update_retry
  apt_install_retry \
    build-essential automake autoconf autoconf-archive libtool pkg-config \
    git ca-certificates curl python3 python3-docutils python3-sphinx \
    libedit-dev libjemalloc-dev libncurses-dev libpcre2-dev libunwind-dev \
    libssl-dev debhelper
  ;;
rpm)
  dnf_install_retry dnf-plugins-core epel-release
  dnf config-manager --set-enabled crb
  # /usr/bin/curl, not curl: the base image ships curl-minimal, which provides
  # the binary and conflicts with the full curl package.
  dnf_install_retry gcc make automake autoconf autoconf-archive libtool \
    pkgconf-pkg-config git-core ca-certificates /usr/bin/curl python3 python3-docutils \
    python3-sphinx libedit-devel jemalloc-devel ncurses-devel pcre2-devel \
    libunwind-devel openssl-devel diffutils rpm-build
  ;;
esac

SRC="/work/tmp/$TAG-src"
rm -rf "$SRC"; mkdir -p "$SRC"
COMMIT=""
if [ "${ENGINE_KIND:?}" = release ]; then
  step fetch
  curl -fsSL -o "/work/tmp/$TAG.tar.gz" "${ENGINE_TARBALL_URL:?}"
  step digest
  echo "${ENGINE_SHA256:?}  /work/tmp/$TAG.tar.gz" | sha256sum -c -
  tar -xzf "/work/tmp/$TAG.tar.gz" -C "$SRC" --strip-components=1
else
  step clone
  clone_branch "${ENGINE_GIT_URL:?}" "${ENGINE_BRANCH:?}" "$SRC"
  COMMIT=$(git -C "$SRC" rev-parse HEAD)
fi
printf '%s\n' "$COMMIT" > "/work/tmp/$TAG.commit"

cd "$SRC"
step bootstrap
[ -f configure ] || sh ./autogen.sh
step configure
./configure --prefix="$PREFIX"
step make
make -j"$(nproc)"
make install

step daemon
[ -x "$PREFIX/sbin/$ENGINE_DAEMON" ] \
  || { echo "no $ENGINE_DAEMON in $PREFIX/sbin" >&2; exit 1; }
"$PREFIX/sbin/$ENGINE_DAEMON" -V 2>&1

step prefix-tar
tar -C / -czf "/work/artifacts/engine-$ENGINE_ID-$TARGET-prefix.tar.gz" "${PREFIX#/}"

if [ "${ENGINE_PACKAGES:-false}" = "true" ]; then
  PKGOUT="/work/artifacts/engine-$ENGINE_ID-$TARGET-pkgs"
  rm -rf "$PKGOUT"; mkdir -p "$PKGOUT"
  step pkg-build
  case "$PKGFMT" in
  deb)
    PKGWORK="/work/tmp/$TAG-pkg"
    rm -rf "$PKGWORK"; mkdir -p "$PKGWORK/build"
    tar -xzf "/work/tmp/$TAG.tar.gz" -C "$PKGWORK/build" --strip-components=1
    cp -R "/repo/$ENGINE_RECIPE_DIR/debian" "$PKGWORK/build/debian"
    # The changelog is generated, not committed: version stamped from the pins.
    cat > "$PKGWORK/build/debian/changelog" <<CHANGELOG
$ENGINE_SOURCE_NAME ($ENGINE_VERSION-$ENGINE_PACKAGE_REVISION) unstable; urgency=medium

  * Automated matrix build of $ENGINE_PACKAGE_NAME $ENGINE_VERSION
    (engine $ENGINE_ID, target $TARGET).

 -- $MAINTAINER  $(date -R)
CHANGELOG
    (cd "$PKGWORK/build" && dpkg-buildpackage -us -uc -b)
    step collect
    cp "$PKGWORK"/"$ENGINE_PACKAGE_NAME"_*.deb \
       "$PKGWORK"/"$ENGINE_DEVELOPMENT_PACKAGE"_*.deb "$PKGOUT/"
    assert_package_arch "$PKGFMT" "$TARGET_PACKAGE_ARCH" "$PKGOUT"/*.deb
    ;;
  rpm)
    TOPD="/work/tmp/$TAG-rpmtop"
    rm -rf "$TOPD"; mkdir -p "$TOPD/SOURCES" "$TOPD/BUILD" "$TOPD/RPMS" "$TOPD/SRPMS"
    cp "/work/tmp/$TAG.tar.gz" "$TOPD/SOURCES/$ENGINE_RPM_ARCHIVE_STEM-$ENGINE_VERSION.tar.gz"
    SRCDIR=$(tar -tzf "/work/tmp/$TAG.tar.gz" | head -1 | cut -d/ -f1 || true)
    [ -n "$SRCDIR" ] || { echo "cannot read tarball top directory" >&2; exit 1; }
    rpmbuild -bb \
      --define "_topdir $TOPD" \
      --define "engine_version $ENGINE_VERSION" \
      --define "engine_release $ENGINE_PACKAGE_REVISION" \
      --define "engine_srcdir $SRCDIR" \
      --define "build_date $(date '+%a %b %d %Y')" \
      "/repo/$ENGINE_RECIPE_DIR/$ENGINE_PACKAGE_NAME.spec"
    step collect
    cp "$TOPD"/RPMS/*/"$ENGINE_PACKAGE_NAME"-*.rpm \
       "$TOPD"/RPMS/*/"$ENGINE_DEVELOPMENT_PACKAGE"-*.rpm "$PKGOUT/"
    assert_package_arch "$PKGFMT" "$TARGET_PACKAGE_ARCH" "$PKGOUT"/*.rpm
    ;;
  esac
  # Mirror for release collection (release.yml gathers <workdir>/packages/).
  mkdir -p "/work/packages/engine-$ENGINE_ID-$TARGET"
  cp "$PKGOUT"/* "/work/packages/engine-$ENGINE_ID-$TARGET/"
fi

echo "engine build complete"
EOF

LOG="$WORKDIR/logs/$TAG.log"
run_in_container "$IMAGE" "$TARGET_PLATFORM" "$WORKDIR" "$TAG.sh" "$LOG" \
  || fail_cell "$WORKDIR" "$ENGINE_ID" "$ENGINE_ID" "$TARGET" engine "$REF" "$TAG"

COMMIT=$(cat "$WORKDIR/tmp/$TAG.commit" 2>/dev/null || true)
emit_result "$WORKDIR" "$ENGINE_ID" "$ENGINE_ID" "$TARGET" engine "$REF" "$COMMIT" pass ""
echo "OK: engine $ENGINE_ID on $TARGET (prefix tarball$( [ "${ENGINE_PACKAGES:-false}" = true ] && echo ' + packages' ))"
