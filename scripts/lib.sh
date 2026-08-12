# scripts/lib.sh -- shared helpers for build-engine.sh and build-vmod.sh.
# Sourced, never executed. Host needs only bash 3.2+, docker and python3;
# everything nontrivial runs inside the containers (DESIGN.md "Script
# contracts"). Every invocation writes exactly one cell-result JSON, on
# success and on failure alike; only infra_failed exits nonzero.

LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$LIB_DIR/.." && pwd)

die() { printf 'E: %s\n' "$*" >&2; exit 2; }

target_platform_for_machine() {
  case "${1:-$(uname -m)}" in
    x86_64) echo linux/amd64 ;;
    aarch64|arm64) echo linux/arm64 ;;
    *) return 1 ;;
  esac
}

# Reject cross-architecture output. Target metadata declares the intended
# platform, and both the host and container call this before building.
assert_target_platform() {
  local actual
  actual=$(target_platform_for_machine) || {
    printf 'E: unsupported machine architecture: %s\n' "$(uname -m)" >&2
    return 1
  }
  [ "$actual" = "$1" ] || {
    printf 'E: target requires %s, but this machine is %s; use a native runner\n' "$1" "$actual" >&2
    return 1
  }
}

# assert_package_arch FORMAT EXPECTED PACKAGE...
# Check the native architecture recorded in every finished binary package.
assert_package_arch() {
  local format=$1 expected=$2 package actual
  shift 2
  for package in "$@"; do
    case "$format" in
      deb) actual=$(dpkg-deb -f "$package" Architecture) ;;
      rpm) actual=$(rpm -qp --qf '%{ARCH}\n' "$package") ;;
      *) die "unknown package format: $format" ;;
    esac
    [ "$actual" = "$expected" ] \
      || die "package $package has architecture $actual, expected $expected"
  done
}

# prepare_workdir DIR -> prints the absolute path, standard subdirs created.
prepare_workdir() {
  mkdir -p "$1/artifacts" "$1/results" "$1/tmp" "$1/logs" "$1/packages"
  (cd "$1" && pwd)
}

# run_in_container IMAGE PLATFORM WORKDIR SCRIPT_BASENAME LOGFILE
# Runs /work/tmp/SCRIPT inside IMAGE with the workdir mounted rw at /work and
# the repo ro at /repo. Streams output and keeps a log copy. Returns the
# container's exit status (callers set pipefail).
run_in_container() {
  # Registry authentication and manifest requests occasionally time out on
  # hosted runners. Pull explicitly with bounded retries so a transient
  # Docker Hub failure does not turn an otherwise valid build into an
  # infra_failed cell. Once the image is local, avoid another registry hit.
  if ! docker image inspect "$1" >/dev/null 2>&1; then
    local attempt
    for attempt in 1 2 3; do
      if docker pull --platform "$2" "$1"; then
        break
      fi
      [ "$attempt" -lt 3 ] || return 1
      sleep $((attempt * 5))
    done
  fi
  docker run --rm \
    --platform "$2" \
    -v "$3:/work" \
    -v "$REPO_ROOT:/repo:ro" \
    "$1" bash "/work/tmp/$4" 2>&1 | tee "$5"
}

# write_inner_prologue PATH TAG
# Every generated container script starts with this: strict mode, a step()
# helper recording the current step for the host-side classifier, and the
# sourced pin environment (matrix.py env output plus host additions).
write_inner_prologue() {
  cat > "$1" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C
step() { echo "\$1" > /work/tmp/$2.step; printf '\n===== %s =====\n' "\$1"; }
. /work/tmp/$2.env
. /repo/scripts/lib.sh
assert_target_platform "\${TARGET_PLATFORM:?}"
EOF
}

# clone_branch URL BRANCH DESTINATION
# Prefer a shallow checkout for the normal smart-HTTP case, but retry without
# depth when an upstream only exposes Git's dumb HTTP transport. Initialize
# submodules in both cases: trunk engine configure scripts can require them.
clone_branch() {
  local url=$1 branch=$2 destination=$3
  if git clone --depth 1 --recurse-submodules --branch "$branch" "$url" "$destination"; then
    return 0
  fi
  rm -rf "$destination"
  echo "shallow clone unavailable; retrying full clone" >&2
  git clone --recurse-submodules --branch "$branch" "$url" "$destination"
}

# clone_vmod URL DESTINATION
# VMOD source hosts can temporarily reject a burst of concurrent clone
# requests (for example, GitHub's HTTP 429 response). Retry a bounded number
# of times so such transport failures do not immediately fail a matrix cell.
clone_vmod() {
  local url=$1 destination=$2 attempt
  for attempt in 1 2 3; do
    rm -rf "$destination"
    if git clone "$url" "$destination"; then
      return 0
    fi
    [ "$attempt" -lt 3 ] || return 1
    echo "VMOD clone failed; retrying in $((attempt * 5)) seconds ($attempt/3)" >&2
    sleep $((attempt * 5))
  done
}

# Clone and resolve the selected VMOD source into the standard container path.
checkout_vmod() {
  step clone
  SRC="/work/tmp/$TAG-src"
  clone_vmod "${VMOD_GIT:?}" "$SRC"
  step checkout
  # A branch named by the catalog is not guaranteed to be materialised as a
  # local branch by `git clone` (GNU's Git transport is one example). Fetch
  # the ref explicitly and detach at FETCH_HEAD; this also works for tags and
  # avoids Git's ambiguous `checkout --detach <name>` path handling.
  if ! git -C "$SRC" rev-parse --verify "${VMOD_REF}^{commit}" >/dev/null 2>&1; then
    git -C "$SRC" fetch --depth 1 origin "$VMOD_REF"
    VMOD_CHECKOUT=FETCH_HEAD
  else
    VMOD_CHECKOUT="${VMOD_REF}^{commit}"
  fi
  git -C "$SRC" checkout --detach "$VMOD_CHECKOUT"
  git -C "$SRC" submodule update --init --recursive
  git -C "$SRC" rev-parse HEAD > "/work/tmp/$TAG.commit"
}

# Install the pinned Rust toolchain, validate the lockfile, and fetch once.
# /work is persistent across local container runs, so an existing toolchain is
# reused after its exact version has been checked.
prepare_cargo() {
  step cargo-deps
  case "$PKGFMT" in
    deb) apt-get install -y --no-install-recommends clang libclang-dev ;;
    rpm) dnf -y -q install clang clang-devel ;;
  esac

  step cargo-bootstrap
  export RUSTUP_HOME=/work/rustup
  export CARGO_HOME=/work/cargo
  export CARGO_TARGET_DIR="/work/cargo-target/$VMOD_ID-$ENGINE_ID-$TARGET-$MODE"
  export RUSTUP_TOOLCHAIN="${RUST_VERSION:?}"
  mkdir -p "$RUSTUP_HOME" "$CARGO_HOME" "$CARGO_TARGET_DIR"
  case "${RUST_BOOTSTRAP:?}" in
    rustup)
      if [ ! -x "$CARGO_HOME/bin/rustup" ]; then
        curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
          | sh -s -- -y --profile minimal --default-toolchain "$RUSTUP_TOOLCHAIN" --no-modify-path
      fi
      ;;
    *) echo "unsupported Rust bootstrap: $RUST_BOOTSTRAP" >&2; exit 1 ;;
  esac
  export PATH="$CARGO_HOME/bin:$PATH"
  if ! rustup run "$RUSTUP_TOOLCHAIN" rustc --version >/dev/null 2>&1; then
    rustup toolchain install "$RUSTUP_TOOLCHAIN" --profile minimal
  fi
  rustc --version | grep -F "rustc $RUST_VERSION "
  cargo --version | grep -F "cargo $RUST_VERSION "

  cd "$SRC"
  step cargo-preflight
  [ -f Cargo.lock ] || { echo "Cargo.lock is required" >&2; exit 1; }
  cargo metadata --locked --offline --no-deps >/dev/null

  step cargo-fetch
  if ! cargo fetch --locked; then
    echo "cargo fetch failed; retrying once"
    cargo fetch --locked
  fi
}

# Install one engine package pair and any additional packages supplied by the
# caller, checking every package's native architecture first.
install_engine_packages() {
  local package_dir=$1
  shift
  case "$PKGFMT" in
    deb)
      assert_package_arch "$PKGFMT" "$TARGET_PACKAGE_ARCH" \
        "$package_dir"/"$ENGINE_RUNTIME_PACKAGE"_*.deb \
        "$package_dir"/"$ENGINE_DEVELOPMENT_PACKAGE"_*.deb "$@"
      apt-get install -y "$package_dir"/"$ENGINE_RUNTIME_PACKAGE"_*.deb \
        "$package_dir"/"$ENGINE_DEVELOPMENT_PACKAGE"_*.deb "$@"
      ;;
    rpm)
      assert_package_arch "$PKGFMT" "$TARGET_PACKAGE_ARCH" \
        "$package_dir"/"$ENGINE_RUNTIME_PACKAGE"-*.rpm \
        "$package_dir"/"$ENGINE_DEVELOPMENT_PACKAGE"-*.rpm "$@"
      dnf -y install "$package_dir"/"$ENGINE_RUNTIME_PACKAGE"-*.rpm \
        "$package_dir"/"$ENGINE_DEVELOPMENT_PACKAGE"-*.rpm "$@"
      ;;
  esac
}

# write_engine_source_step PATH
# Append the engine-source provisioning step to a generated container script
# (DESIGN.md decision 14). When the manifest sets engine_source: required,
# fetch the engine's own source pin (release: sha256-checked dist tarball;
# trunk: shallow-first clone with a full-clone fallback), regenerate the VSC
# headers the dist archive omits
# using the installed engine's vsctool, and export VINYLSRC/VARNISHSRC for
# the VMOD's configure. Entries without the key skip the whole block. A
# failure here is the harness breaking (infra), like deps or unpack-engine.
write_engine_source_step() {
  cat >> "$1" <<'EOF'

step engine-source
if [ "${VMOD_ENGINE_SOURCE:-}" = required ]; then
  ETREE_ROOT="/work/tmp/$TAG-engine-src"
  rm -rf "$ETREE_ROOT"; mkdir -p "$ETREE_ROOT"
  case "$ENGINE_KIND" in
  release)
    curl -fsSL "${ENGINE_TARBALL_URL:?}" -o "$ETREE_ROOT/engine.tgz"
    echo "${ENGINE_SHA256:?}  $ETREE_ROOT/engine.tgz" | sha256sum -c -
    tar -xzf "$ETREE_ROOT/engine.tgz" -C "$ETREE_ROOT"
    rm "$ETREE_ROOT/engine.tgz"
    ;;
  trunk)
    clone_branch "${ENGINE_GIT_URL:?}" "${ENGINE_BRANCH:?}" "$ETREE_ROOT/tree"
    ;;
  esac
  ENGINE_TREE=""
  for d in "$ETREE_ROOT"/*/; do
    [ -z "$ENGINE_TREE" ] || { echo "engine source extracted to more than one directory" >&2; exit 1; }
    ENGINE_TREE=$(cd "$d" && pwd)
  done
  [ -n "$ENGINE_TREE" ] || { echo "engine source extracted to no directory" >&2; exit 1; }
  # The dist archive ships VSC counter definitions (*.vsc) but not the
  # headers the engine build generates from them, and VMODs that reach into
  # engine internals include those headers (pesi: VSC_main.h).
  VSCTOOL=$(pkg-config --variable=vsctool "$ENGINE_API" 2>/dev/null || true)
  if [ -n "$VSCTOOL" ] && [ -d "$ENGINE_TREE/lib/libvsc" ]; then
    (cd "$ENGINE_TREE/lib/libvsc" && for vsc in *.vsc; do
       if [ -f "$vsc" ]; then python3 "$VSCTOOL" -h "$vsc"; fi
     done)
  fi
  # Trunk source clones do not contain the generated daemon-private headers
  # that deep-integration VMODs include (for example
  # bin/vinyld/cache/cache_vinyld.h). The installed engine development
  # prefix does contain them, so seed the source tree from that authoritative
  # build output. Release archives already carry these files; copying the
  # installed versions is harmless and keeps both paths consistent.
  ENGINE_INCLUDEDIR=$(pkg-config --variable=includedir "$ENGINE_API" 2>/dev/null || true)
  for includedir in "$ENGINE_INCLUDEDIR" "$PREFIX/include/$ENGINE_SOURCE_NAME"; do
    if [ -d "$includedir/cache" ]; then
      mkdir -p "$ENGINE_TREE/bin/$ENGINE_DAEMON/cache"
      cp -a "$includedir/cache/." "$ENGINE_TREE/bin/$ENGINE_DAEMON/cache/"
      break
    fi
  done
  export VINYLSRC="$ENGINE_TREE" VARNISHSRC="$ENGINE_TREE"
  echo "engine source tree provisioned at $ENGINE_TREE"
fi
EOF
}

# status_for_step STEP -> the cell status a failure at STEP maps to.
# Steps whose failure means the harness/plumbing broke are infra_failed
# (deps, fetch, clone, unpack-engine, engine-install, recipe, prefix-tar,
# collect); everything else is an honest red (DESIGN.md status vocabulary).
# A digest mismatch and a bad ref are real failures, not infra. 'check' is
# the VMOD make-check step (upstream's own suite); the engine's daemon smoke
# test is the separate 'daemon' step.
status_for_step() {
  local step=$1 mode=${2:-}
  case "$1" in
    digest|checkout|daemon|modules|cargo-build|cargo-artifacts) echo build_failed ;;
    cargo-preflight) [ "$mode" = package ] && echo package_failed || echo build_failed ;;
    bootstrap|configure)            echo configure_failed ;;
    make)                           echo build_failed ;;
    load)                           echo load_failed ;;
    check|cargo-test)               echo test_failed ;;
    pkg-build)                      echo package_failed ;;
    pkg-install|pkg-load)           echo install_failed ;;
    cargo-fetch|cargo-bootstrap|cargo-deps) echo infra_failed ;;
    *)                              echo infra_failed ;;
  esac
}

# emit_result WORKDIR ROW ENGINE TARGET MODE REF COMMIT STATUS DETAIL
# Writes <workdir>/results/<row>--<engine>--<target>--<mode>.json (cell/1).
emit_result() {
  mkdir -p "$1/results"
  RES_ROW="$2" RES_ENGINE="$3" RES_TARGET="$4" RES_MODE="$5" \
  RES_REF="$6" RES_COMMIT="$7" RES_STATUS="$8" RES_DETAIL="$9" \
  RES_TS="$(date -u +%FT%TZ)" \
  RES_RUN_URL="${GITHUB_RUN_ID:+${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID}}" \
  RES_OUT="$1/results/$2--$3--$4--$5.json" \
  python3 - <<'PY'
import json, os
e = os.environ
with open(e["RES_OUT"], "w") as f:
    json.dump({
        "schema": "cell/1",
        "row": e["RES_ROW"], "engine": e["RES_ENGINE"],
        "target": e["RES_TARGET"], "mode": e["RES_MODE"],
        "ref": e["RES_REF"], "commit": e["RES_COMMIT"],
        "status": e["RES_STATUS"], "detail": e["RES_DETAIL"],
        "run_url": e["RES_RUN_URL"], "finished_at": e["RES_TS"],
    }, f)
    f.write("\n")
PY
}

# infra_cell WORKDIR ROW ENGINE TARGET MODE REF DETAIL
# Emit an infra_failed cell for a failure before/outside any container, then
# exit 1 (the only nonzero exit in the contract).
infra_cell() {
  emit_result "$1" "$2" "$3" "$4" "$5" "$6" "" infra_failed "$7"
  printf 'E: %s\n' "$7" >&2
  exit 1
}

# failure_detail LOG STEP
# Return a short, human-useful diagnostic from a failed container log. RPM
# appends headings, macro warnings and a generic exit status after the useful
# error, so a physical log tail reports the wrapper rather than the cause.
# Make and package builds need the same diagnostic selection; every other step
# keeps the compact log-tail fallback.
failure_detail() {
  local log=$1 step=$2 detail=""
  case "$step" in make|pkg-build|cargo-build)
    detail=$(awk '
      function rpm_epilogue(line) {
        return line ~ /^[[:space:]]*RPM build (warnings|errors):[[:space:]]*$/ ||
          line ~ /^[[:space:]]*(error: )?Bad exit status from / ||
          line ~ /^[[:space:]]*Macro expanded in comment on line [0-9]+:/
      }
      function strong_diagnostic(line) {
        return line ~ /(^|[[:space:]:])error:/ ||
          line ~ /[[:alnum:]_]+Error:/ ||
          line ~ /fatal[[:space:]]+(error:)?/ ||
          line ~ /undefined reference/
      }
      function weak_diagnostic(line) {
        return line ~ /(cannot|couldn.t|No such file|not found)/ ||
          line ~ /require(s)? [[:alnum:].-]+ [0-9]/
      }
      !rpm_epilogue($0) && strong_diagnostic($0) {
        strong[count_strong % 3] = $0
        count_strong++
      }
      !rpm_epilogue($0) && !strong_diagnostic($0) && weak_diagnostic($0) {
        weak[count_weak % 3] = $0
        count_weak++
      }
      END {
        if (count_strong) {
          first = count_strong > 3 ? count_strong - 3 : 0
          for (i = first; i < count_strong; i++) print strong[i % 3]
        } else {
          first = count_weak > 3 ? count_weak - 3 : 0
          for (i = first; i < count_weak; i++) print weak[i % 3]
        }
      }
    ' "$log" 2>/dev/null || true)
  ;; esac
  if [ -z "$detail" ]; then
    detail=$(tail -n 3 "$log" 2>/dev/null || true)
  fi
  printf '%s' "$detail"
}

# fail_cell WORKDIR ROW ENGINE TARGET MODE REF TAG [COMMIT_TAG]
# Classify a failed container run via TAG's step file and a concise diagnostic,
# emit the cell result, then exit: 1 for infra_failed, 0 for an honest red cell.
fail_cell() {
  local workdir=$1 row=$2 engine=$3 target=$4 mode=$5 ref=$6 tag=$7 ctag=${8:-$7}
  local commit step status detail
  commit=$(cat "$workdir/tmp/$ctag.commit" 2>/dev/null || true)
  step=$(cat "$workdir/tmp/$tag.step" 2>/dev/null || echo unknown)
  status=$(status_for_step "$step" "$mode")
  detail="step '$step' failed: $(failure_detail "$workdir/logs/$tag.log" "$step" | tr '\n' ' ' | cut -c1-300 || true)"
  emit_result "$workdir" "$row" "$engine" "$target" "$mode" "$ref" "$commit" "$status" "$detail"
  if [ "$status" = infra_failed ]; then
    printf 'E: infra failure at step %s; see %s\n' "$step" "$workdir/logs/$tag.log" >&2
    exit 1
  fi
  printf 'cell failed at step %s (%s); see %s\n' "$step" "$status" "$workdir/logs/$tag.log" >&2
  exit 0
}
