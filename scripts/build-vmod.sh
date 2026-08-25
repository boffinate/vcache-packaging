#!/usr/bin/env bash
# scripts/build-vmod.sh <vmod-id> <engine-id> <target> <mode> <workdir>
#
# Build one VMOD against one engine on one target (DESIGN.md "Script
# contracts"). mode:
#   compat  - untar the engine prefix, autotools-build the resolved ref
#             against it, compile a minimal VCL importing each built module
#             with the selected family daemon (-C), then run
#             upstream's own `make check` when the manifest declares
#             tests: make-check (retried once; twice -> test_failed).
#   package - install the engine .deb/.rpm set, build the generated recipe
#             (tools/recipe.py), then install engine + VMOD packages in a
#             fresh container and run an import check covering every name
#             in package.modules. Installable packages land under
#             <workdir>/packages/.
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
python3 "$REPO_ROOT/tools/matrix.py" env --engine "$ENGINE_ARG" --vmod "$VMOD_ARG" --target "$TARGET" > "$ENVFILE" \
  || infra_cell "$WORKDIR" "$VMOD_ARG" "$ENGINE_ARG" "$TARGET" "$MODE" "" "matrix.py env failed for $VMOD_ARG/$ENGINE_ARG"
. "$ENVFILE"
IMAGE=${TARGET_IMAGE:?}
PKGFMT=${TARGET_FORMAT:?}
# Result provenance comes from the inner build marker so an earlier harness
# failure does not claim that source code was changed.
SOURCE_API_NORMALIZATION=""
assert_target_platform "${TARGET_PLATFORM:?}" \
  || infra_cell "$WORKDIR" "$VMOD_ARG" "$ENGINE_ARG" "$TARGET" "$MODE" "" "target platform does not match this host"

ENGINE_ID=${ENGINE_ID:-$ENGINE_ARG}
VMOD_ID=${VMOD_ID:-$VMOD_ARG}
VMOD_PACKAGE_NAME=${VMOD_PACKAGE_NAME:?}
ENGINE_RUNTIME_PACKAGE=${ENGINE_RUNTIME_PACKAGE:?}
ENGINE_DEVELOPMENT_PACKAGE=${ENGINE_DEVELOPMENT_PACKAGE:?}
ENGINE_API=${ENGINE_API:?}
ENGINE_DAEMON=${ENGINE_DAEMON:?}
ENGINE_VMOD_DIR_COMPONENT=${ENGINE_VMOD_DIR_COMPONENT:?}
[ -n "${VMOD_REF:-}" ] \
  || infra_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" "$MODE" "" "matrix.py env provided no VMOD_REF"
PREFIX="/opt/$ENGINE_ID"

# Engine artifacts: CI layout first, local shared-workdir layout second.
ENGINE_ART="/work/engine/artifacts"
[ -d "$WORKDIR/engine/artifacts" ] || ENGINE_ART="/work/artifacts"
ENGINE_ART_HOST=${ENGINE_ART/#\/work/$WORKDIR}

VMOD_SOURCE_ARTIFACT=""
if [ -f "$WORKDIR/vmod-source/source.tar.gz" ]; then
  VMOD_SOURCE_ARTIFACT=/work/vmod-source
elif [ "${VCACHE_REQUIRE_PREFETCHED_VMOD_SOURCE:-}" = 1 ]; then
  infra_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" "$MODE" "$VMOD_REF" \
    "prefetched VMOD source artifact is missing"
fi

{
  printf "TAG='%s'\nTARGET='%s'\nPKGFMT='%s'\nPREFIX='%s'\nMODE='%s'\n" \
    "$TAG" "$TARGET" "$PKGFMT" "$PREFIX" "$MODE"
  printf "ENGINE_ID='%s'\nVMOD_ID='%s'\nENGINE_ART='%s'\n" "$ENGINE_ID" "$VMOD_ID" "$ENGINE_ART"
  printf "VMOD_SOURCE_ARTIFACT='%s'\n" "$VMOD_SOURCE_ARTIFACT"
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
# The engine prefix ships the family daemon but not its shared-library
# dependencies; install the same library set build-engine.sh builds against
# so the daemon can run for the load check.
case "$PKGFMT" in
deb)
  apt_update_retry
  apt_install_retry \
    build-essential automake autoconf autoconf-archive libtool pkg-config \
    git ca-certificates curl python3 python3-docutils \
    libedit-dev libjemalloc-dev libncurses-dev libpcre2-dev libunwind-dev \
    ${VMOD_BUILD_DEPS:-}
  ;;
rpm)
  dnf_install_retry dnf-plugins-core epel-release
  dnf config-manager --set-enabled crb
  # /usr/bin/curl, never the curl package: EL ships curl-minimal, which
  # provides the binary and conflicts with full curl.
  dnf_install_retry gcc make automake autoconf autoconf-archive libtool \
    pkgconf-pkg-config git-core python3 python3-docutils diffutils \
    /usr/bin/curl \
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
# Some upstream Makefiles use the other engine family's variable in
# ACLOCAL_AMFLAGS. Point both aliases at the selected engine's macro
# directory so an incompatible engine reports its real configure error,
# rather than trying the invalid '/aclocal' directory.
# The relocatable engine prefix has a fixed datarootdir. Do not read it from
# a .pc file: unexpanded upstream placeholders here become paths such as
# '/aclocal' during autoreconf.
ENGINE_API_DATAROOTDIR="$PREFIX/share"
VARNISHAPI_DATAROOTDIR="$ENGINE_API_DATAROOTDIR"
VINYLAPI_DATAROOTDIR="$ENGINE_API_DATAROOTDIR"
export VARNISHAPI_DATAROOTDIR VINYLAPI_DATAROOTDIR
export LIBVARNISHAPI_DATAROOTDIR="$VARNISHAPI_DATAROOTDIR" LIBVINYLAPI_DATAROOTDIR="$VINYLAPI_DATAROOTDIR"
DAEMON="$PREFIX/sbin/$ENGINE_DAEMON"
[ -x "$DAEMON" ] || { echo "no $ENGINE_DAEMON in $PREFIX/sbin" >&2; exit 1; }
EOF
  write_engine_source_step "$INNER"
  cat >> "$INNER" <<'EOF'

load_modules() {
  step load
  for so in "$@"; do
    mod=$(basename "$so" .so); mod=${mod#libvmod_}
    abs="$(cd "$(dirname "$so")" && pwd)/$(basename "$so")"
    vd=$(mktemp -d)
    printf 'vcl 4.1;\nimport %s from "%s";\nbackend default none;\n' "$mod" "$abs" > "$vd/t.vcl"
    if ! "$DAEMON" -j none -C -n "$vd/n" -f "$vd/t.vcl" > "$vd/out.log" 2>&1; then
      echo "load check failed for $mod:"; sed -n '1,40p' "$vd/out.log"; exit 1
    fi
    echo "loaded $mod OK"
  done
}

build_autotools() {
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
  # VMOD generators are not reliably parallel-safe.
  make -j1 ${VMOD_BUILD_TARGET:-all}

  step modules
  # package.modules is the public module contract. Some projects also build
  # test-only libvmod_*.so files with the same VCL identity (for example
  # Slash's witness build); loading every matching file would misidentify
  # those as separate VMODs and turn a good build into a false red cell.
  built_sos=()
  while IFS= read -r -d '' so; do built_sos+=("$so"); done < <(find . -path '*/.libs/libvmod_*.so' -type f -print0)
  sos=()
  for mod in ${VMOD_MODULES:-$VMOD_ID}; do
    matches=()
    for so in "${built_sos[@]}"; do
      [ "${so##*/}" = "libvmod_$mod.so" ] && matches+=("$so")
    done
    [ "${#matches[@]}" -eq 1 ] || {
      echo "expected exactly one public libvmod_$mod.so, found ${#matches[@]}" >&2
      exit 1
    }
    sos+=("${matches[0]}")
  done
  load_modules "${sos[@]}"

  step check
  # Upstream's own suite, only when the manifest says so (tests: make-check).
  if [ "${VMOD_TESTS:-}" = make-check ]; then
    if ! make check; then
      echo "make check failed; retrying once (known VTC load-flakes)"
      if ! make check; then
        fails=$( { grep -hE '^FAIL' test-suite.log src/test-suite.log 2>/dev/null || true; } \
          | head -n 5 | tr '\n' ' ' )
        echo "make check failed twice: ${fails:-no FAIL lines found in test-suite.log}"
        exit 1
      fi
    fi
    echo "make check OK"
  else
    echo "no test suite declared; skipping"
  fi
}

build_cargo() {
  prepare_cargo
  cargo_feature_args=()
  if [ -n "${VMOD_CARGO_FEATURES:-}" ]; then
    cargo_feature_args=(--features "$VMOD_CARGO_FEATURES")
  fi

  step cargo-build
  cargo build --release --locked --offline "${cargo_feature_args[@]}"

  step cargo-artifacts
  modules=( $VMOD_MODULES )
  artifacts=( $VMOD_ARTIFACTS )
  [ "${#modules[@]}" -eq "${#artifacts[@]}" ] || { echo "module/artifact contract mismatch" >&2; exit 1; }
  VMOD_DIR=$(pkg-config --variable=vmoddir "$ENGINE_API")
  [ -n "$VMOD_DIR" ] || { echo "$ENGINE_API reports an empty VMOD directory" >&2; exit 1; }
  artifact_args=()
  for i in "${!artifacts[@]}"; do
    artifact_args+=(--mapping "${modules[$i]}=${artifacts[$i]}")
  done
  python3 /repo/tools/cargo-artifacts.py --release-dir "$CARGO_TARGET_DIR/release" \
    --destination "$VMOD_DIR" "${artifact_args[@]}"
  sos=()
  for module in "${modules[@]}"; do sos+=("$VMOD_DIR/libvmod_$module.so"); done
  load_modules "${sos[@]}"

  step cargo-test
  if [ "${VMOD_TESTS:-}" = cargo-test ]; then
    if ! cargo test --release --locked --offline "${cargo_feature_args[@]}"; then
      echo "cargo test failed; retrying once"
      cargo test --release --locked --offline "${cargo_feature_args[@]}"
    fi
  else
    echo "no Cargo test suite declared; skipping"
  fi
}

checkout_vmod
if [ -n "${VMOD_SOURCE_API_FAMILY:-}" ] && [ "$VMOD_SOURCE_API_FAMILY" != "$ENGINE_FAMILY" ]; then
  step source-api-normalize
  printf '%s-to-%s\n' "$VMOD_SOURCE_API_FAMILY" "$ENGINE_FAMILY" > "/work/tmp/$TAG.source-api-normalization"
  python3 /repo/tools/source_api_normalize.py \
    --source-family "$VMOD_SOURCE_API_FAMILY" --target-family "$ENGINE_FAMILY" "$SRC"
fi
case "${VMOD_BUILD:-autotools}" in
autotools) build_autotools ;;
cargo) build_cargo ;;
*) echo "unsupported VMOD build: $VMOD_BUILD" >&2; exit 1 ;;
esac
EOF

  LOG="$WORKDIR/logs/$TAG.log"
  run_in_container "$IMAGE" "$TARGET_PLATFORM" "$WORKDIR" "$TAG.sh" "$LOG" \
    || fail_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" compat "$VMOD_REF" "$TAG"
  SOURCE_API_NORMALIZATION=$(read_source_api_normalization "$WORKDIR" "$TAG")
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
  apt_update_retry
  apt_install_retry \
    build-essential automake autoconf autoconf-archive libtool pkg-config \
    git ca-certificates curl python3 python3-docutils debhelper ${VMOD_BUILD_DEPS:-}
  ;;
rpm)
  dnf_install_retry dnf-plugins-core epel-release
  dnf config-manager --set-enabled crb
  # /usr/bin/curl, never the curl package: EL ships curl-minimal, which
  # provides the binary and conflicts with full curl.
  dnf_install_retry gcc make automake autoconf autoconf-archive libtool \
    pkgconf-pkg-config git-core python3 python3-docutils diffutils rpm-build \
    /usr/bin/curl \
    ${VMOD_BUILD_DEPS:-}
  ;;
esac

step engine-install
ENGINE_PKGDIR="$ENGINE_ART/engine-$ENGINE_ID-$TARGET-pkgs"
install_engine_packages "$ENGINE_PKGDIR"
EOF
write_engine_source_step "$INNER"
cat >> "$INNER" <<'EOF'

checkout_vmod

if [ -n "${VMOD_SOURCE_API_FAMILY:-}" ] && [ "$VMOD_SOURCE_API_FAMILY" != "$ENGINE_FAMILY" ]; then
  step source-api-normalize
  printf '%s-to-%s\n' "$VMOD_SOURCE_API_FAMILY" "$ENGINE_FAMILY" > "/work/tmp/$TAG.source-api-normalization"
  python3 /repo/tools/source_api_normalize.py \
    --source-family "$VMOD_SOURCE_API_FAMILY" --target-family "$ENGINE_FAMILY" "$SRC"
fi

if [ "${VMOD_BUILD:-autotools}" = cargo ]; then
  prepare_cargo
fi

step pkg-build
OUT="/work/packages/$VMOD_PACKAGE_NAME-$ENGINE_ID-$TARGET"
rm -rf "$OUT"; mkdir -p "$OUT"
case "$PKGFMT" in
deb)
  # The generated recipe is authoritative; copying it over an upstream
  # debian/ directory would nest it and silently build with upstream's engine
  # dependencies instead of this cell's family-specific contract.
  rm -rf "$SRC/debian"
  cp -R "/work/tmp/$TAG-recipe/debian" "$SRC/debian"
  (cd "$SRC" && dpkg-buildpackage -us -uc -b)
  assert_package_arch "$PKGFMT" "$TARGET_PACKAGE_ARCH" /work/tmp/*.deb
  step collect
  PACKAGE_FILE=$(select_native_package deb "$VMOD_PACKAGE_NAME" /work/tmp/*.deb)
  cp "$PACKAGE_FILE" "$OUT/"
  ;;
rpm)
  NAMEDIR="$VMOD_PACKAGE_NAME-${VMOD_VERSION:?}"
  TOPD="/work/tmp/$TAG-rpmtop"
  rm -rf "$TOPD" "/work/tmp/$NAMEDIR"
  mkdir -p "$TOPD/SOURCES" "$TOPD/BUILD" "$TOPD/RPMS" "$TOPD/SRPMS"
  cp -a "$SRC" "/work/tmp/$NAMEDIR"
  tar -C /work/tmp -czf "$TOPD/SOURCES/$NAMEDIR.tar.gz" "$NAMEDIR"
  rpmbuild -bb --define "_topdir $TOPD" "/work/tmp/$TAG-recipe/$VMOD_PACKAGE_NAME.spec"
  assert_package_arch "$PKGFMT" "$TARGET_PACKAGE_ARCH" "$TOPD"/RPMS/*/*.rpm
  step collect
  PACKAGE_FILE=$(select_native_package rpm "$VMOD_PACKAGE_NAME" "$TOPD"/RPMS/*/*.rpm)
  cp "$PACKAGE_FILE" "$OUT/"
  ;;
esac

step pkg-verify
VMOD_DIR=$(pkg-config --variable=vmoddir "$ENGINE_API")
[ -n "$VMOD_DIR" ] || { echo "$ENGINE_API reports an empty VMOD directory" >&2; exit 1; }
PACKAGE_FILE=$(select_native_package "$PKGFMT" "$VMOD_PACKAGE_NAME" "$OUT"/*)
python3 /repo/tools/package_contract.py \
  --format "$PKGFMT" \
  --package "$PACKAGE_FILE" \
  --name "$VMOD_PACKAGE_NAME" \
  --arch "$TARGET_PACKAGE_ARCH" \
  --engine-package "$ENGINE_RUNTIME_PACKAGE" \
  --vmod-dir "$VMOD_DIR" \
  --modules $VMOD_MODULES
EOF

LOG="$WORKDIR/logs/$TAG.log"
run_in_container "$IMAGE" "$TARGET_PLATFORM" "$WORKDIR" "$TAG.sh" "$LOG" \
  || fail_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" package "$VMOD_REF" "$TAG"

# Fresh container: install engine + VMOD packages, then the same import check.
TAG2="$TAG-install"
cp "$ENVFILE" "$WORKDIR/tmp/$TAG2.env"
INNER2="$WORKDIR/tmp/$TAG2.sh"
write_inner_prologue "$INNER2" "$TAG2"
cat >> "$INNER2" <<'EOF'

step deps
case "$PKGFMT" in
deb) apt_update_retry ;;
rpm) dnf_install_retry epel-release ;;  # engine runtime needs libunwind
esac

step pkg-install
VPKG="/work/packages/$VMOD_PACKAGE_NAME-$ENGINE_ID-$TARGET"
ENGINE_PKGDIR="$ENGINE_ART/engine-$ENGINE_ID-$TARGET-pkgs"
case "$PKGFMT" in
deb)
  install_engine_packages "$ENGINE_PKGDIR" "$VPKG"/"$VMOD_PACKAGE_NAME"_*.deb
  ;;
rpm)
  install_engine_packages "$ENGINE_PKGDIR" "$VPKG"/"$VMOD_PACKAGE_NAME"-*.rpm
  ;;
esac

step pkg-load
DAEMON=$(command -v "$ENGINE_DAEMON" || true)
[ -n "$DAEMON" ] || { echo "no $ENGINE_DAEMON on PATH after install" >&2; exit 1; }
# One VCL importing every module name the package ships (package.modules,
# defaulted to the VMOD id by matrix.py env).
VMOD_DIR=$(pkg-config --variable=vmoddir "$ENGINE_API")
[ -n "$VMOD_DIR" ] || { echo "$ENGINE_API reports an empty VMOD directory" >&2; exit 1; }
case "$VMOD_DIR" in
  */"$ENGINE_VMOD_DIR_COMPONENT"/vmods) ;;
  *) echo "$ENGINE_API VMOD directory $VMOD_DIR does not match family component $ENGINE_VMOD_DIR_COMPONENT" >&2; exit 1 ;;
esac
{
  printf 'vcl 4.1;\n'
  for mod in ${VMOD_MODULES:-$VMOD_ID}; do
    [ -f "$VMOD_DIR/libvmod_$mod.so" ] || { echo "missing $VMOD_DIR/libvmod_$mod.so" >&2; exit 1; }
    printf 'import %s;\n' "$mod"
  done
  printf 'backend default none;\n'
} > /tmp/load.vcl
if ! "$DAEMON" -j none -C -n /tmp/vd -f /tmp/load.vcl > /tmp/load.log 2>&1; then
  echo "installed load check failed:"; tail -n 40 /tmp/load.log; exit 1
fi
echo "installed load check OK ($DAEMON, import: ${VMOD_MODULES:-$VMOD_ID})"
EOF

LOG2="$WORKDIR/logs/$TAG2.log"
run_in_container "$IMAGE" "$TARGET_PLATFORM" "$WORKDIR" "$TAG2.sh" "$LOG2" \
  || fail_cell "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" package "$VMOD_REF" "$TAG2" "$TAG"

COMMIT=$(cat "$WORKDIR/tmp/$TAG.commit" 2>/dev/null || true)
SOURCE_API_NORMALIZATION=$(read_source_api_normalization "$WORKDIR" "$TAG")
emit_result "$WORKDIR" "$VMOD_ID" "$ENGINE_ID" "$TARGET" package "$VMOD_REF" "$COMMIT" pass ""
echo "OK: $VMOD_ID packaged and install-checked against $ENGINE_ID on $TARGET"
