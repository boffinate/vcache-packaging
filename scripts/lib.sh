# scripts/lib.sh -- shared helpers for build-engine.sh and build-vmod.sh.
# Sourced, never executed. Host needs only bash 3.2+, docker and python3;
# everything nontrivial runs inside the containers (DESIGN.md "Script
# contracts"). Every invocation writes exactly one cell-result JSON, on
# success and on failure alike; only infra_failed exits nonzero.

LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$LIB_DIR/.." && pwd)

die() { printf 'E: %s\n' "$*" >&2; exit 2; }

# retry_command ATTEMPTS LABEL COMMAND...
# retry_command_with_hook ATTEMPTS LABEL HOOK COMMAND...
#
# One bounded exponential-backoff policy for every repository-controlled
# network command. Protocol helpers supply cleanup/recovery hooks where a
# blind rerun is insufficient.
retry_command_with_hook() {
  local attempts=$1 label=$2 hook=$3 attempt=1 status delay
  shift 3
  case "$attempts" in *[!0-9]*|0) die "retry attempts must be a positive integer" ;; esac
  while :; do
    if "$@"; then return 0; else status=$?; fi
    [ "$attempt" -lt "$attempts" ] || return "$status"
    "$hook" || true
    delay=$((1 << (attempt - 1)))
    [ "$delay" -le 16 ] || delay=16
    echo "$label failed; retrying in $delay seconds ($attempt/$attempts)" >&2
    sleep "$delay"
    attempt=$((attempt + 1))
  done
}

retry_command() {
  local attempts=$1 label=$2
  shift 2
  retry_command_with_hook "$attempts" "$label" : "$@"
}

# download_retry URL DESTINATION
# Fetch HTTPS into a temporary file and publish it only after curl completes.
# Immutable callers still verify their pinned digest after this returns.
download_once() {
  local url=$1 partial=$2
  rm -f -- "$partial"
  curl --proto '=https' --tlsv1.2 -fL \
    --connect-timeout 20 --max-time 300 -o "$partial" "$url"
}

download_retry() {
  local url=$1 destination=$2 partial="${2}.part" status
  if retry_command 5 "download $url" download_once "$url" "$partial"; then
    mv -f -- "$partial" "$destination"
    return 0
  else
    status=$?
  fi
  rm -f -- "$partial"
  return "$status"
}

# Debian mirrors can briefly serve an object that does not match the current
# package index during mirror publication. Refresh the indexes and retry the
# dependency transaction a bounded number of times before classifying it as
# infrastructure failure.
apt_reset_indexes() {
  apt-get clean
  rm -rf /var/lib/apt/lists/*
}

apt_update_retry() {
  retry_command_with_hook 3 "apt-get update" apt_reset_indexes apt-get update -qq
}

apt_install_recover() {
  apt_reset_indexes
  # This refresh is part of the outer three-attempt install transaction. Do
  # not nest another retry loop and multiply the advertised attempt bound.
  apt-get update -qq
}

apt_install_retry() {
  retry_command_with_hook 3 "apt-get install" apt_install_recover \
    apt-get install -y --no-install-recommends "$@"
}

dnf_install_recover() {
  dnf clean all >/dev/null 2>&1 || true
}

dnf_install_retry() {
  retry_command_with_hook 3 "dnf install" dnf_install_recover dnf -y -q install "$@"
}

git_retry() {
  local label=$1
  shift
  retry_command 3 "$label" git "$@"
}

git_clone_once() {
  local url=$1 destination=$2
  shift 2
  rm -rf "$destination"
  git clone "$@" "$url" "$destination"
}

git_clone_retry() {
  local url=$1 destination=$2
  shift 2
  retry_command 3 "git clone $url" git_clone_once "$url" "$destination" "$@"
}

git_remote_head_exists_retry() {
  local remote=$1 branch=$2 status
  if git ls-remote --exit-code --heads "$remote" "$branch"; then
    return 0
  else
    status=$?
  fi
  # Exit 2 means the request succeeded and the ref is absent, not that the
  # transport failed. Only retry genuine lookup failures.
  [ "$status" -ne 2 ] || return 2
  retry_command 2 "git ls-remote $branch" git ls-remote --exit-code --heads "$remote" "$branch"
}

ensure_container_image() {
  local image=$1 platform=${2:-}
  if docker image inspect "$image" >/dev/null 2>&1; then return 0; fi
  if [ -n "$platform" ]; then
    retry_command 3 "docker pull $image" docker pull --platform "$platform" "$image"
  else
    retry_command 3 "docker pull $image" docker pull "$image"
  fi
}

replace_github_release_once() {
  local tag=$1 target=$2 notes_file=$3
  shift 3
  # A failed create may leave a draft. Delete on every attempt so retrying the
  # whole transaction is idempotent; absence is the expected first-run case.
  gh release delete "$tag" --cleanup-tag --yes >/dev/null 2>&1 || true
  gh release create "$tag" "$@" \
    --target "$target" --title "$tag" --notes-file "$notes_file"
}

replace_github_release_retry() {
  local tag=$1
  retry_command 3 "replace GitHub release $tag" replace_github_release_once "$@"
}

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
  ensure_container_image "$1" "$2"
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
  # A shallow clone may be unsupported by dumb HTTP. Try it once, then use
  # the fully retried compatible form for both that case and transport flakes.
  if git_clone_once "$url" "$destination" \
    --depth 1 --recurse-submodules --branch "$branch"; then
    return 0
  fi
  echo "shallow clone unavailable; retrying full clone" >&2
  git_clone_retry "$url" "$destination" --recurse-submodules --branch "$branch"
}

# clone_vmod URL DESTINATION
# VMOD source hosts can temporarily reject a burst of concurrent clone
# requests with HTTP 429. Retry a bounded number
# of times so such transport failures do not immediately fail a matrix cell.
clone_vmod() {
  local url=$1 destination=$2
  git_clone_retry "$url" "$destination"
}

materialize_vmod_source() {
  local url=$1 ref=$2 expected_commit=$3 destination=$4 commit_file=$5
  local checkout resolved_commit actual_commit
  clone_vmod "$url" "$destination"
  # A branch named by the catalog is not guaranteed to be materialised as a
  # local branch by `git clone` (GNU's Git transport is one example). Fetch
  # the ref explicitly and detach at FETCH_HEAD; this also works for tags and
  # avoids Git's ambiguous `checkout --detach <name>` path handling.
  if ! git -C "$destination" rev-parse --verify "${ref}^{commit}" >/dev/null 2>&1; then
    git_retry "fetch VMOD ref $ref" -C "$destination" fetch --depth 1 origin "$ref"
    checkout=FETCH_HEAD
  else
    checkout="${ref}^{commit}"
  fi
  resolved_commit=$(git -C "$destination" rev-parse "$checkout")
  if [ -n "$expected_commit" ]; then
    git -C "$destination" cat-file -e "$expected_commit^{commit}" 2>/dev/null \
      || { echo "pinned commit $expected_commit is unavailable from source ref $ref" >&2; return 1; }
    checkout="$expected_commit^{commit}"
    if [ "$resolved_commit" != "$expected_commit" ]; then
      echo "source ref $ref moved to $resolved_commit; building pinned commit $expected_commit" >&2
    fi
  fi
  git -C "$destination" checkout --detach "$checkout"
  git_retry "update VMOD submodules" -C "$destination" submodule update --init --recursive
  actual_commit=$(git -C "$destination" rev-parse HEAD)
  [ -z "$expected_commit" ] || [ "$actual_commit" = "$expected_commit" ] \
    || { echo "checked out $actual_commit, expected $expected_commit" >&2; return 1; }
  printf '%s\n' "$actual_commit" > "$commit_file"
}

archive_vmod_source() {
  local source=$1 artifact_dir=$2 vmod_id=$3 url=$4 ref=$5 commit=$6
  mkdir -p "$artifact_dir"
  # macOS bsdtar otherwise emits AppleDouble files for extended attributes;
  # those names collide with Git pack indexes when Linux extracts the archive.
  COPYFILE_DISABLE=1 tar -czf "$artifact_dir/source.tar.gz.part" -C "$source" .
  mv -f "$artifact_dir/source.tar.gz.part" "$artifact_dir/source.tar.gz"
  printf '%s\n' "$vmod_id" > "$artifact_dir/vmod-id"
  printf '%s\n' "$url" > "$artifact_dir/url"
  printf '%s\n' "$ref" > "$artifact_dir/ref"
  printf '%s\n' "$commit" > "$artifact_dir/commit"
}

restore_vmod_source() {
  local artifact_dir=$1 destination=$2 expected_id=$3 expected_url=$4 expected_ref=$5
  local expected_commit=$6 commit_file=$7 artifact_commit submodule_status
  for file in source.tar.gz vmod-id url ref commit; do
    [ -f "$artifact_dir/$file" ] || { echo "prefetched VMOD source is missing $file" >&2; return 1; }
  done
  [ "$(cat "$artifact_dir/vmod-id")" = "$expected_id" ] \
    || { echo "prefetched VMOD source has the wrong VMOD id" >&2; return 1; }
  [ "$(cat "$artifact_dir/url")" = "$expected_url" ] \
    || { echo "prefetched VMOD source has the wrong repository URL" >&2; return 1; }
  [ "$(cat "$artifact_dir/ref")" = "$expected_ref" ] \
    || { echo "prefetched VMOD source has the wrong ref" >&2; return 1; }
  artifact_commit=$(cat "$artifact_dir/commit")
  [ -z "$expected_commit" ] || [ "$artifact_commit" = "$expected_commit" ] \
    || { echo "prefetched VMOD source commit $artifact_commit does not match pin $expected_commit" >&2; return 1; }
  rm -rf "$destination"
  mkdir -p "$destination"
  tar -xzf "$artifact_dir/source.tar.gz" -C "$destination"
  [ -d "$destination/.git" ] \
    || { echo "prefetched VMOD source has no Git metadata" >&2; return 1; }
  [ "$(git -C "$destination" rev-parse HEAD)" = "$artifact_commit" ] \
    || { echo "prefetched VMOD source archive does not match its commit metadata" >&2; return 1; }
  submodule_status=$(git -C "$destination" submodule status --recursive) || return
  if printf '%s\n' "$submodule_status" | grep -Eq '^[-+U]'; then
    echo "prefetched VMOD source contains an unmaterialised submodule" >&2
    return 1
  fi
  [ -z "$(git -C "$destination" status --porcelain --untracked-files=all --ignore-submodules=none)" ] \
    || { echo "prefetched VMOD source archive does not match its Git tree" >&2; return 1; }
  printf '%s\n' "$artifact_commit" > "$commit_file"
}

# Resolve the selected VMOD source into the standard container path. CI uses
# a workflow artifact so build fan-out cannot amplify traffic to upstream.
checkout_vmod() {
  SRC="/work/tmp/$TAG-src"
  if [ -n "${VMOD_SOURCE_ARTIFACT:-}" ]; then
    step source-artifact
    restore_vmod_source "$VMOD_SOURCE_ARTIFACT" "$SRC" "$VMOD_ID" "$VMOD_GIT" \
      "$VMOD_REF" "${VMOD_EXPECTED_COMMIT:-}" "/work/tmp/$TAG.commit"
    return
  fi
  step clone
  materialize_vmod_source "$VMOD_GIT" "$VMOD_REF" "${VMOD_EXPECTED_COMMIT:-}" \
    "$SRC" "/work/tmp/$TAG.commit"
}

# Install the pinned Rust toolchain, validate the lockfile, and fetch once.
# /work is persistent across local container runs, so an existing toolchain is
# reused after its exact version has been checked.
prepare_cargo() {
  step cargo-deps
  case "$PKGFMT" in
    deb) apt_install_retry clang libclang-dev ;;
    rpm) dnf_install_retry clang clang-devel ;;
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
        RUSTUP_INIT="/work/tmp/$TAG-rustup-init.sh"
        download_retry https://sh.rustup.rs "$RUSTUP_INIT"
        retry_command 3 "rustup bootstrap" sh "$RUSTUP_INIT" \
          -y --profile minimal --default-toolchain "$RUSTUP_TOOLCHAIN" --no-modify-path
        rm -f "$RUSTUP_INIT"
      fi
      ;;
    *) echo "unsupported Rust bootstrap: $RUST_BOOTSTRAP" >&2; exit 1 ;;
  esac
  export PATH="$CARGO_HOME/bin:$PATH"
  if ! rustup run "$RUSTUP_TOOLCHAIN" rustc --version >/dev/null 2>&1; then
    retry_command 3 "install Rust toolchain $RUSTUP_TOOLCHAIN" rustup toolchain install \
      "$RUSTUP_TOOLCHAIN" --profile minimal
  fi
  rustc --version | grep -F "rustc $RUST_VERSION "
  cargo --version | grep -F "cargo $RUST_VERSION "

  cd "$SRC"
  step cargo-preflight
  [ -f Cargo.lock ] || { echo "Cargo.lock is required" >&2; exit 1; }
  cargo metadata --locked --offline --no-deps >/dev/null

  step cargo-fetch
  retry_command 3 "cargo fetch" cargo fetch --locked
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
      apt_install_retry "$package_dir"/"$ENGINE_RUNTIME_PACKAGE"_*.deb \
        "$package_dir"/"$ENGINE_DEVELOPMENT_PACKAGE"_*.deb "$@"
      ;;
    rpm)
      assert_package_arch "$PKGFMT" "$TARGET_PACKAGE_ARCH" \
        "$package_dir"/"$ENGINE_RUNTIME_PACKAGE"-*.rpm \
        "$package_dir"/"$ENGINE_DEVELOPMENT_PACKAGE"-*.rpm "$@"
      dnf_install_retry "$package_dir"/"$ENGINE_RUNTIME_PACKAGE"-*.rpm \
        "$package_dir"/"$ENGINE_DEVELOPMENT_PACKAGE"-*.rpm "$@"
      ;;
  esac
}

# select_native_package FORMAT EXPECTED_NAME PACKAGE...
# Print the one binary package whose native metadata declares EXPECTED_NAME.
# Filename prefixes are insufficient for RPM because rpmbuild also emits
# <name>-debuginfo and <name>-debugsource packages by default.
select_native_package() {
  local format=$1 expected_name=$2
  shift 2
  local package actual_name selected="" matches=0
  for package in "$@"; do
    [ -f "$package" ] || continue
    case "$format" in
      deb) actual_name=$(dpkg-deb -f "$package" Package) || return ;;
      rpm) actual_name=$(rpm -qp --qf '%{NAME}\n' "$package") || return ;;
      *) die "unknown package format: $format" ;;
    esac
    [ "$actual_name" = "$expected_name" ] || continue
    selected=$package
    matches=$((matches + 1))
  done
  [ "$matches" -eq 1 ] || {
    echo "expected exactly one native package named $expected_name, found $matches" >&2
    return 1
  }
  printf '%s\n' "$selected"
}

# Preserve generated daemon-private headers in the relocatable engine prefix.
# Upstream install rules are not a stable source for these files on trunk, but
# engine_source VMODs must compile against the headers from the actual engine
# build rather than a separately cloned source tree.
preserve_engine_private_headers() {
  local source_tree=$1 prefix=$2 daemon=$3
  local source_cache="$source_tree/bin/$daemon/cache"
  [ -d "$source_cache" ] || return 0
  local artifact_dir="$prefix/share/vcache-packaging/engine-source/$daemon/cache"
  mkdir -p "$artifact_dir"
  local header
  for header in "$source_cache"/*.h; do
    [ -f "$header" ] || continue
    cp -a "$header" "$artifact_dir/"
  done
}

# Seed a provisioned engine source tree from the exact engine build artifact,
# falling back to installed development headers for older prefix artifacts.
seed_engine_private_headers() {
  local prefix=$1 engine_tree=$2 daemon=$3 source_name=$4 api=$5
  local includedir=""
  includedir=$(pkg-config --variable=includedir "$api" 2>/dev/null || true)
  local cache_dir
  for cache_dir in \
    "$prefix/share/vcache-packaging/engine-source/$daemon/cache" \
    "$includedir/cache" \
    "$prefix/include/$source_name/cache"; do
    if [ -d "$cache_dir" ]; then
      mkdir -p "$engine_tree/bin/$daemon/cache"
      cp -a "$cache_dir/." "$engine_tree/bin/$daemon/cache/"
      return 0
    fi
  done
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
    download_retry "${ENGINE_TARBALL_URL:?}" "$ETREE_ROOT/engine.tgz"
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
  # Restore daemon-private headers captured from the exact engine build, with
  # an installed-header fallback for older artifacts.
  seed_engine_private_headers "$PREFIX" "$ENGINE_TREE" "$ENGINE_DAEMON" \
    "$ENGINE_SOURCE_NAME" "$ENGINE_API"
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
    pkg-build|pkg-verify)           echo package_failed ;;
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
