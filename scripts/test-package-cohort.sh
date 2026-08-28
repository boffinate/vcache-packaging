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

step install
case "$PKGFMT" in
deb)
  apt_update_retry
  apt_install_retry "${PACKAGES[@]}"
  ;;
rpm)
  dnf_install_retry dnf-plugins-core epel-release
  dnf config-manager --set-enabled crb
  dnf_install_retry --setopt=install_weak_deps=False "${PACKAGES[@]}"
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

step systemd-payload
case "$ENGINE_DAEMON" in
varnishd)
  SERVICE=varnish.service
  DEFAULT_VCL=/etc/varnish/default.vcl
  RELOAD=/usr/sbin/varnishreload
  ;;
vinyld)
  SERVICE=vinyl-cache.service
  DEFAULT_VCL=/etc/vinyl-cache/default.vcl
  RELOAD=/usr/sbin/vinylreload
  ;;
*) echo "no systemd contract for $ENGINE_DAEMON" >&2; exit 1 ;;
esac
SERVICE_PATH=$(find /usr/lib/systemd/system /lib/systemd/system -name "$SERVICE" -print -quit 2>/dev/null)
[ -f "$SERVICE_PATH" ] || { echo "missing $SERVICE" >&2; exit 1; }
[ -f "$DEFAULT_VCL" ] || { echo "missing $DEFAULT_VCL" >&2; exit 1; }
[ -x "$RELOAD" ] || { echo "missing executable $RELOAD" >&2; exit 1; }
grep -F -- "-p feature=+http2" "$SERVICE_PATH" >/dev/null \
  || { echo "$SERVICE does not enable HTTP/2" >&2; exit 1; }

step daemon-start
DAEMON=$(command -v "$ENGINE_DAEMON" || true)
[ -n "$DAEMON" ] || { echo "no $ENGINE_DAEMON on PATH after cohort install" >&2; exit 1; }
INSTANCE=$(mktemp -d)
"$DAEMON" -j none -F -a 127.0.0.1:0 -n "$INSTANCE" -f /tmp/cohort.vcl -p feature=+http2 \
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
