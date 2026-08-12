#!/usr/bin/env bash
# scripts/test-package-cohort.sh <engine-id> <target> <package-dir> <workdir>
#
# Install the complete publishable package set in one fresh native target
# container, compile one generated VCL importing every declared module, and
# prove that the daemon remains running after startup.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

[ $# -eq 4 ] || die "usage: test-package-cohort.sh <engine-id> <target> <package-dir> <workdir>"
ENGINE_ARG=$1 TARGET=$2
PACKAGE_DIR=$(cd "$3" && pwd)
WORKDIR=$(prepare_workdir "$4")
case "$PACKAGE_DIR" in
  "$WORKDIR"/*) ;;
  *) die "package-dir must be inside workdir so the container can read it" ;;
esac

TAG="cohort-$ENGINE_ARG-$TARGET"
ENVFILE="$WORKDIR/tmp/$TAG.env"
python3 "$REPO_ROOT/tools/matrix.py" cohort-env --engine "$ENGINE_ARG" --target "$TARGET" > "$ENVFILE"
. "$ENVFILE"
assert_target_platform "${TARGET_PLATFORM:?}"
PACKAGE_REL=${PACKAGE_DIR#"$WORKDIR"/}
{
  printf "TAG='%s'\nTARGET='%s'\nPKGFMT='%s'\n" "$TAG" "$TARGET" "$TARGET_FORMAT"
  printf "PACKAGE_ROOT='/work/%s'\n" "$PACKAGE_REL"
} >> "$ENVFILE"

INNER="$WORKDIR/tmp/$TAG.sh"
write_inner_prologue "$INNER" "$TAG"
cat >> "$INNER" <<'EOF'

step package-discovery
case "$PKGFMT" in
deb) mapfile -t PACKAGES < <(find "$PACKAGE_ROOT" -type f -name '*.deb' | sort) ;;
rpm) mapfile -t PACKAGES < <(find "$PACKAGE_ROOT" -type f -name '*.rpm' | sort) ;;
esac
[ "${#PACKAGES[@]}" -gt 0 ] || { echo "no native packages under $PACKAGE_ROOT" >&2; exit 1; }
case "$PKGFMT" in
deb) ACTUAL_NAMES=$(for package in "${PACKAGES[@]}"; do dpkg-deb -f "$package" Package; done | sort) ;;
rpm) ACTUAL_NAMES=$(rpm -qp --qf '%{NAME}\n' "${PACKAGES[@]}" | sort) ;;
esac
EXPECTED_NAMES=$(printf '%s\n' $COHORT_PACKAGE_NAMES | sort)
[ "$ACTUAL_NAMES" = "$EXPECTED_NAMES" ] || {
  echo "local package cohort differs from catalog" >&2
  diff -u <(printf '%s\n' "$EXPECTED_NAMES") <(printf '%s\n' "$ACTUAL_NAMES") >&2 || true
  exit 1
}

step install
case "$PKGFMT" in
deb)
  apt-get update -qq
  apt-get install -y --no-install-recommends "${PACKAGES[@]}"
  dpkg-query -W $COHORT_PACKAGE_NAMES >/dev/null
  ;;
rpm)
  dnf -y -q install dnf-plugins-core epel-release
  dnf config-manager --set-enabled crb
  dnf -y --setopt=install_weak_deps=False install "${PACKAGES[@]}"
  rpm -q $COHORT_PACKAGE_NAMES >/dev/null
  ;;
esac

step generated-vcl
VMOD_DIR=$(pkg-config --variable=vmoddir "$ENGINE_API")
[ -n "$VMOD_DIR" ] || { echo "$ENGINE_API reports an empty VMOD directory" >&2; exit 1; }
{
  printf 'vcl 4.1;\n'
  for module in $COHORT_MODULES; do
    [ -f "$VMOD_DIR/libvmod_$module.so" ] \
      || { echo "missing $VMOD_DIR/libvmod_$module.so" >&2; exit 1; }
    printf 'import %s;\n' "$module"
  done
  printf 'backend default none;\n'
} > /tmp/cohort.vcl

step daemon-start
DAEMON=$(command -v "$ENGINE_DAEMON" || true)
[ -n "$DAEMON" ] || { echo "no $ENGINE_DAEMON on PATH after cohort install" >&2; exit 1; }
INSTANCE=$(mktemp -d)
"$DAEMON" -j none -F -a 127.0.0.1:0 -n "$INSTANCE" -f /tmp/cohort.vcl \
  > /tmp/cohort-daemon.log 2>&1 &
PID=$!
sleep 2
if ! kill -0 "$PID" 2>/dev/null; then
  echo "cohort daemon did not survive startup:" >&2
  tail -n 80 /tmp/cohort-daemon.log >&2
  wait "$PID" || true
  exit 1
fi
kill -TERM "$PID"
wait "$PID" || true
echo "cohort runtime smoke OK ($ENGINE_DAEMON; modules: $COHORT_MODULES)"
EOF

LOG="$WORKDIR/logs/$TAG.log"
run_in_container "$TARGET_IMAGE" "$TARGET_PLATFORM" "$WORKDIR" "$TAG.sh" "$LOG"
