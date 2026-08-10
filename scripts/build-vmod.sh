#!/usr/bin/env bash
# scripts/build-vmod.sh <vmod-id> <engine-id> <target> <mode> <workdir>
#
# Build one VMOD against one engine on one target (DESIGN.md "Script
# contracts"). mode:
#   compat  - untar the engine prefix, autotools-build the resolved ref
#             against it, then compile a minimal VCL importing each built
#             module with the engine daemon (vinyld/varnishd -C).
#   package - install the engine .deb/.rpm set, build the generated recipe
#             (tools/recipe.py), then install engine + VMOD packages in a
#             fresh container and run the same import check. Installable
#             packages land under <workdir>/packages/.
# Engine artifacts are found under <workdir>/engine/artifacts/ (CI re-roots
# the downloaded engine artifact there) or <workdir>/artifacts/ (local runs
# sharing one workdir). Writes one cell result; exit 0 unless infra_failed.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

[ $# -eq 5 ] || die "usage: build-vmod.sh <vmod-id> <engine-id> <target> <mode> <workdir>"
VMOD_ARG=$1 ENGINE_ARG=$2 TARGET=$3 MODE=$4
WORKDIR=$(prepare_workdir "$5")
TAG="vmod-$VMOD_ARG-$ENGINE_ARG-$TARGET-$MODE"
ENVFILE="$WORKDIR/tmp/$TAG.env"

case "$MODE" in compat|package) ;; *) die "unknown mode: $MODE" ;; esac
IMAGE=$(image_for_target "$TARGET") \
  || infra_cell "$WORKDIR" "$VMOD_ARG" "$ENGINE_ARG" "$TARGET" "$MODE" "" "unknown target: $TARGET"
PKGFMT=$(pkgfmt_for_target "$TARGET")

python3 "$REPO_ROOT/tools/matrix.py" env --engine "$ENGINE_ARG" --vmod "$VMOD_ARG" --target "$TARGET" > "$ENVFILE" \
  || infra_cell "$WORKDIR" "$VMOD_ARG" "$ENGINE_ARG" "$TARGET" "$MODE" "" "matrix.py env failed for $VMOD_ARG/$ENGINE_ARG"
. "$ENVFILE"

ENGINE_ID=${ENGINE_ID:-$ENGINE_ARG}
VMOD_ID=${VMOD_ID:-$VMOD_ARG}
[ -n "${VMOD_REF:-}" ] \
  || infra_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" "$MODE" "" "matrix.py env provided no VMOD_REF"
PREFIX="/opt/$ENGINE_ID"

# Engine artifacts: CI layout first, local shared-workdir layout second.
ENGINE_ART="/work/engine/artifacts"
[ -d "$WORKDIR/engine/artifacts" ] || ENGINE_ART="/work/artifacts"
ENGINE_ART_HOST=${ENGINE_ART/#\/work/$WORKDIR}

{
  printf "TAG='%s'\nTARGET='%s'\nPKGFMT='%s'\nPREFIX='%s'\nMODE='%s'\n" \
    "$TAG" "$TARGET" "$PKGFMT" "$PREFIX" "$MODE"
  printf "ENGINE_ID='%s'\nVMOD_ID='%s'\nENGINE_ART='%s'\n" "$ENGINE_ID" "$VMOD_ID" "$ENGINE_ART"
} >> "$ENVFILE"

# ---------------------------------------------------------------- compat ----
if [ "$MODE" = compat ]; then
  [ -f "$ENGINE_ART_HOST/engine-$ENGINE_ID-$TARGET-prefix.tar.gz" ] \
    || infra_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" compat "$VMOD_REF" \
         "engine prefix tarball missing: $ENGINE_ART_HOST/engine-$ENGINE_ID-$TARGET-prefix.tar.gz"

  INNER="$WORKDIR/tmp/$TAG.sh"
  write_inner_prologue "$INNER" "$TAG"
  cat >> "$INNER" <<'EOF'

step deps
# The engine prefix ships vinyld/varnishd but not their shared-library
# dependencies; install the same library set build-engine.sh builds against
# so the daemon can run for the load check.
case "$PKGFMT" in
deb)
  apt-get update -qq
  apt-get install -y --no-install-recommends \
    build-essential automake autoconf autoconf-archive libtool pkg-config \
    git ca-certificates python3 python3-docutils \
    libedit-dev libjemalloc-dev libncurses-dev libpcre2-dev libunwind-dev \
    ${VMOD_BUILD_DEPS:-}
  ;;
rpm)
  dnf -y -q install dnf-plugins-core epel-release
  dnf config-manager --set-enabled crb
  dnf -y -q install gcc make automake autoconf autoconf-archive libtool \
    pkgconf-pkg-config git-core python3 python3-docutils diffutils \
    libedit-devel jemalloc-devel ncurses-devel pcre2-devel libunwind-devel \
    ${VMOD_BUILD_DEPS:-}
  ;;
esac

step unpack-engine
tar -xzf "$ENGINE_ART/engine-$ENGINE_ID-$TARGET-prefix.tar.gz" -C /
export PATH="$PREFIX/bin:$PREFIX/sbin:$PATH"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export LD_LIBRARY_PATH="$PREFIX/lib"
export ACLOCAL_PATH="$PREFIX/share/aclocal"
# ACLOCAL_AMFLAGS in many VMODs expands these; unset they become '-I /aclocal'.
VARNISHAPI_DATAROOTDIR=$(pkg-config --variable=datarootdir varnishapi 2>/dev/null || true)
VINYLAPI_DATAROOTDIR=$(pkg-config --variable=datarootdir vinylapi 2>/dev/null || true)
export VARNISHAPI_DATAROOTDIR VINYLAPI_DATAROOTDIR
export LIBVARNISHAPI_DATAROOTDIR="$VARNISHAPI_DATAROOTDIR" LIBVINYLAPI_DATAROOTDIR="$VINYLAPI_DATAROOTDIR"
DAEMON=""
for c in vinyld varnishd; do
  if [ -x "$PREFIX/sbin/$c" ]; then DAEMON="$PREFIX/sbin/$c"; fi
done
[ -n "$DAEMON" ] || { echo "no vinyld/varnishd in $PREFIX/sbin" >&2; exit 1; }

step clone
SRC="/work/tmp/$TAG-src"
rm -rf "$SRC"
git clone "${VMOD_GIT:?}" "$SRC"
step checkout
git -C "$SRC" checkout --detach "$VMOD_REF" 2>/dev/null \
  || git -C "$SRC" checkout --detach "origin/$VMOD_REF"
# Build-system boilerplate may live in submodules (vmod-dict's acvmod).
git -C "$SRC" submodule update --init --recursive
git -C "$SRC" rev-parse HEAD > "/work/tmp/$TAG.commit"

step bootstrap
cd "$SRC"
# Some repos keep the build system in a single subdirectory (urlsort-style).
if [ ! -f configure ] && [ ! -f configure.ac ] && [ ! -f autogen.sh ] && [ ! -f bootstrap ]; then
  sub=$(for d in */; do if [ -f "$d/configure.ac" ]; then echo "$d"; fi; done)
  if [ "$(printf '%s\n' "$sub" | grep -c . || true)" = 1 ]; then cd "$sub"; fi
fi
if [ ! -f configure ]; then
  if [ -f bootstrap ]; then sh ./bootstrap || autoreconf -f -i
  elif [ -f autogen.sh ]; then sh ./autogen.sh || autoreconf -f -i
  else autoreconf -f -i; fi
fi
[ -f configure ] || { echo "bootstrap produced no configure script" >&2; exit 1; }

step configure
# Second chance via autoreconf: autogen.sh often leaves aux files uninstalled.
./configure || { autoreconf -f -i && ./configure; }
step make
# Sequential retry: old vmodtool rules race under -j.
make -j"$(nproc)" || make

step modules
SOS=$(find . -path '*/.libs/libvmod_*.so' | sort)
[ -n "$SOS" ] || { echo "build produced no libvmod_*.so" >&2; exit 1; }

step load
for so in $SOS; do
  mod=$(basename "$so" .so); mod=${mod#libvmod_}
  abs="$(cd "$(dirname "$so")" && pwd)/$(basename "$so")"
  vd=$(mktemp -d)
  printf 'vcl 4.1;\nimport %s from "%s";\nbackend default none;\n' "$mod" "$abs" > "$vd/t.vcl"
  if ! "$DAEMON" -j none -C -n "$vd/n" -f "$vd/t.vcl" > "$vd/out.log" 2>&1; then
    echo "load check failed for $mod:"; sed -n '1,40p' "$vd/out.log"; exit 1
  fi
  echo "loaded $mod OK"
done
EOF

  LOG="$WORKDIR/logs/$TAG.log"
  run_in_container "$IMAGE" "$WORKDIR" "$TAG.sh" "$LOG" \
    || fail_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" compat "$VMOD_REF" "$TAG"
  COMMIT=$(cat "$WORKDIR/tmp/$TAG.commit" 2>/dev/null || true)
  emit_result "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" compat "$VMOD_REF" "$COMMIT" pass ""
  echo "OK: $VMOD_ID compat against $ENGINE_ID on $TARGET"
  exit 0
fi

# --------------------------------------------------------------- package ----
[ -d "$ENGINE_ART_HOST/engine-$ENGINE_ID-$TARGET-pkgs" ] \
  || infra_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" package "$VMOD_REF" \
       "engine package set missing: $ENGINE_ART_HOST/engine-$ENGINE_ID-$TARGET-pkgs"

# The recipe is generated on the host (stdlib-only tooling), consumed in the
# container. A generation failure is our tooling breaking, hence infra.
RECIPE_DIR="$WORKDIR/tmp/$TAG-recipe"
rm -rf "$RECIPE_DIR"; mkdir -p "$RECIPE_DIR"
python3 "$REPO_ROOT/tools/recipe.py" generate --vmod "$VMOD_ARG" --engine "$ENGINE_ARG" \
    --target "$TARGET" --out "$RECIPE_DIR" \
  || infra_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" package "$VMOD_REF" "recipe.py generate failed"

INNER="$WORKDIR/tmp/$TAG.sh"
write_inner_prologue "$INNER" "$TAG"
cat >> "$INNER" <<'EOF'

step deps
case "$PKGFMT" in
deb)
  apt-get update -qq
  apt-get install -y --no-install-recommends \
    build-essential automake autoconf autoconf-archive libtool pkg-config \
    git ca-certificates python3 python3-docutils debhelper ${VMOD_BUILD_DEPS:-}
  ;;
rpm)
  dnf -y -q install dnf-plugins-core epel-release
  dnf config-manager --set-enabled crb
  dnf -y -q install gcc make automake autoconf autoconf-archive libtool \
    pkgconf-pkg-config git-core python3 python3-docutils diffutils rpm-build \
    ${VMOD_BUILD_DEPS:-}
  ;;
esac

step engine-install
case "$PKGFMT" in
deb) apt-get install -y "$ENGINE_ART/engine-$ENGINE_ID-$TARGET-pkgs"/*.deb ;;
rpm) dnf -y install "$ENGINE_ART/engine-$ENGINE_ID-$TARGET-pkgs"/*.rpm ;;
esac

step clone
SRC="/work/tmp/$TAG-src"
rm -rf "$SRC"
git clone "${VMOD_GIT:?}" "$SRC"
step checkout
git -C "$SRC" checkout --detach "$VMOD_REF" 2>/dev/null \
  || git -C "$SRC" checkout --detach "origin/$VMOD_REF"
# Build-system boilerplate may live in submodules (vmod-dict's acvmod).
git -C "$SRC" submodule update --init --recursive
git -C "$SRC" rev-parse HEAD > "/work/tmp/$TAG.commit"

step pkg-build
OUT="/work/packages/vmod-$VMOD_ID-$ENGINE_ID-$TARGET"
rm -rf "$OUT"; mkdir -p "$OUT"
case "$PKGFMT" in
deb)
  cp -R "/work/tmp/$TAG-recipe/debian" "$SRC/debian"
  (cd "$SRC" && dpkg-buildpackage -us -uc -b)
  step collect
  cp /work/tmp/vinyl-vmod-"$VMOD_ID"_*.deb "$OUT/"
  ;;
rpm)
  NAMEDIR="vinyl-vmod-$VMOD_ID-${VMOD_VERSION:?}"
  TOPD="/work/tmp/$TAG-rpmtop"
  rm -rf "$TOPD" "/work/tmp/$NAMEDIR"
  mkdir -p "$TOPD/SOURCES" "$TOPD/BUILD" "$TOPD/RPMS" "$TOPD/SRPMS"
  cp -a "$SRC" "/work/tmp/$NAMEDIR"
  tar -C /work/tmp -czf "$TOPD/SOURCES/$NAMEDIR.tar.gz" "$NAMEDIR"
  rpmbuild -bb --define "_topdir $TOPD" "/work/tmp/$TAG-recipe/"*.spec
  step collect
  cp "$TOPD"/RPMS/*/*.rpm "$OUT/"
  ;;
esac
EOF

LOG="$WORKDIR/logs/$TAG.log"
run_in_container "$IMAGE" "$WORKDIR" "$TAG.sh" "$LOG" \
  || fail_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" package "$VMOD_REF" "$TAG"

# Fresh container: install engine + VMOD packages, then the same import check.
TAG2="$TAG-install"
cp "$ENVFILE" "$WORKDIR/tmp/$TAG2.env"
INNER2="$WORKDIR/tmp/$TAG2.sh"
write_inner_prologue "$INNER2" "$TAG2"
cat >> "$INNER2" <<'EOF'

step deps
case "$PKGFMT" in
deb) apt-get update -qq ;;
rpm) dnf -y -q install epel-release ;;  # engine runtime needs libunwind
esac

step pkg-install
VPKG="/work/packages/vmod-$VMOD_ID-$ENGINE_ID-$TARGET"
case "$PKGFMT" in
deb) apt-get install -y "$ENGINE_ART/engine-$ENGINE_ID-$TARGET-pkgs"/*.deb "$VPKG"/*.deb ;;
rpm) dnf -y install "$ENGINE_ART/engine-$ENGINE_ID-$TARGET-pkgs"/*.rpm "$VPKG"/*.rpm ;;
esac

step pkg-load
DAEMON=""
for c in vinyld varnishd; do
  if command -v "$c" >/dev/null 2>&1; then DAEMON=$c; fi
done
[ -n "$DAEMON" ] || { echo "no vinyld/varnishd on PATH after install" >&2; exit 1; }
printf 'vcl 4.1;\nimport %s;\nbackend default none;\n' "$VMOD_ID" > /tmp/load.vcl
if ! "$DAEMON" -j none -C -n /tmp/vd -f /tmp/load.vcl > /tmp/load.log 2>&1; then
  echo "installed load check failed:"; sed -n '1,40p' /tmp/load.log; exit 1
fi
echo "installed load check OK ($DAEMON, import $VMOD_ID)"
EOF

LOG2="$WORKDIR/logs/$TAG2.log"
run_in_container "$IMAGE" "$WORKDIR" "$TAG2.sh" "$LOG2" \
  || fail_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" package "$VMOD_REF" "$TAG2" "$TAG"

COMMIT=$(cat "$WORKDIR/tmp/$TAG.commit" 2>/dev/null || true)
emit_result "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" package "$VMOD_REF" "$COMMIT" pass ""
echo "OK: $VMOD_ID packaged and install-checked against $ENGINE_ID on $TARGET"
