#!/bin/sh
# Build one VMOD from /src (read-only mount) against this lane's installed
# development surface, then smoke-load every built module .so with the lane
# daemon. Emits ::stage::/::vmod::/::result:: markers for the sweep driver.
#
# Exit codes: 0 pass, 10 copy, 11 bootstrap, 12 configure, 13 build,
# 14 no vmod built, 15 load.

set -u
NAME="${1:?vmod name}"
: "${LANE:?}" "${DAEMON:?}"
WORK=/tmp/work

echo "::lane::${LANE}::daemon=${DAEMON}"

# Many VMODs reference ${VARNISHAPI_DATAROOTDIR}/aclocal (or the vinyl name)
# in ACLOCAL_AMFLAGS; autoreconf expands it from the environment, and an
# unset variable turns into a bogus '-I /aclocal'.
VARNISHAPI_DATAROOTDIR=$(pkg-config --variable=datarootdir varnishapi 2>/dev/null || true)
VINYLAPI_DATAROOTDIR=$(pkg-config --variable=datarootdir vinylapi 2>/dev/null || true)
export VARNISHAPI_DATAROOTDIR VINYLAPI_DATAROOTDIR

echo "::stage::copy"
cp -a /src "$WORK" && cd "$WORK" || { echo "::result::copy-failed"; exit 10; }
chmod -R u+w .

echo "::stage::bootstrap"
if [ ! -f configure ]; then
    if [ -f bootstrap ]; then
        # varnish-modules style: computes the aclocal include itself and may
        # run configure as a side effect.
        sh ./bootstrap || autoreconf -f -i
    elif [ -f autogen.sh ]; then
        sh ./autogen.sh || autoreconf -f -i
    else
        autoreconf -f -i
    fi
fi
[ -f configure ] || { echo "::result::bootstrap-failed"; exit 11; }

echo "::stage::configure"
# Second chance via autoreconf: autogen.sh scripts frequently leave aux
# files (compile/missing/depcomp) or archive macros uninstalled.
./configure || { autoreconf -f -i && ./configure; } \
    || { echo "::result::configure-failed"; exit 12; }

echo "::stage::build"
# Sequential retry: old-style vmodtool Makefile rules race under -j
# (vcc_if.c and vcc_if.h both invoking vmodtool concurrently).
make -j"$(nproc)" || make || { echo "::result::build-failed"; exit 13; }

echo "::stage::load"
sos=$(find . -path '*/.libs/libvmod_*.so' -type f | sort)
if [ -z "$sos" ]; then
    echo "::result::no-vmod-built"
    exit 14
fi

fail=0
for so in $sos; do
    mod=$(basename "$so" .so)
    mod=${mod#libvmod_}
    abs=$(cd "$(dirname "$so")" && pwd)/$(basename "$so")
    vcl="/tmp/smoke_${mod}.vcl"
    printf 'vcl 4.1;\nimport %s from "%s";\nbackend default none;\n' "$mod" "$abs" > "$vcl"
    if "$DAEMON" -j none -C -n "/tmp/vd_${mod}" -f "$vcl" > "/tmp/load_${mod}.log" 2>&1; then
        echo "::vmod::${mod}::pass"
    else
        echo "::vmod::${mod}::fail"
        sed -n '1,40p' "/tmp/load_${mod}.log"
        fail=1
    fi
done

if [ "$fail" -eq 0 ]; then
    echo "::result::pass"
    exit 0
fi
echo "::result::load-failed"
exit 15
