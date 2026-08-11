# scripts/lib.sh -- shared helpers for build-engine.sh and build-vmod.sh.
# Sourced, never executed. Host needs only bash 3.2+, docker and python3;
# everything nontrivial runs inside the containers (DESIGN.md "Script
# contracts"). Every invocation writes exactly one cell-result JSON, on
# success and on failure alike; only infra_failed exits nonzero.

LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$LIB_DIR/.." && pwd)

die() { printf 'E: %s\n' "$*" >&2; exit 2; }

image_for_target() {
  case "$1" in
    debian-13-amd64) echo debian:13 ;;
    ubuntu-26.04-amd64) echo ubuntu:26.04 ;;
    el10-x86_64)     echo almalinux:10 ;;
    *) return 1 ;;
  esac
}

pkgfmt_for_target() {
  case "$1" in
    debian-*|ubuntu-*) echo deb ;;
    el10-x86_64)     echo rpm ;;
    *) return 1 ;;
  esac
}

# prepare_workdir DIR -> prints the absolute path, standard subdirs created.
prepare_workdir() {
  mkdir -p "$1/artifacts" "$1/results" "$1/tmp" "$1/logs" "$1/packages"
  (cd "$1" && pwd)
}

# run_in_container IMAGE WORKDIR SCRIPT_BASENAME LOGFILE
# Runs /work/tmp/SCRIPT inside IMAGE with the workdir mounted rw at /work and
# the repo ro at /repo. Streams output and keeps a log copy. Returns the
# container's exit status (callers set pipefail).
run_in_container() {
  docker run --rm \
    -v "$2:/work" \
    -v "$REPO_ROOT:/repo:ro" \
    "$1" bash "/work/tmp/$3" 2>&1 | tee "$4"
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
  case "$1" in
    digest|checkout|daemon|modules) echo build_failed ;;
    bootstrap|configure)            echo configure_failed ;;
    make)                           echo build_failed ;;
    load)                           echo load_failed ;;
    check)                          echo test_failed ;;
    pkg-build)                      echo package_failed ;;
    pkg-install|pkg-load)           echo install_failed ;;
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
# For package builds, retain the final diagnostics after excluding that known
# epilogue; every other step keeps the compact log-tail fallback.
failure_detail() {
  local log=$1 step=$2 detail=""
  if [ "$step" = pkg-build ]; then
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
  fi
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
  status=$(status_for_step "$step")
  detail="step '$step' failed: $(failure_detail "$workdir/logs/$tag.log" "$step" | tr '\n' ' ' | cut -c1-300 || true)"
  emit_result "$workdir" "$row" "$engine" "$target" "$mode" "$ref" "$commit" "$status" "$detail"
  if [ "$status" = infra_failed ]; then
    printf 'E: infra failure at step %s; see %s\n' "$step" "$workdir/logs/$tag.log" >&2
    exit 1
  fi
  printf 'cell failed at step %s (%s); see %s\n' "$step" "$status" "$workdir/logs/$tag.log" >&2
  exit 0
}
