#!/bin/bash
#
# Build libvmod-cachetag against the INSTALLED vinyl-cache-dev package, in a
# clean container layer that has never seen the Vinyl source tree.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

src=/work/build/libvmod-cachetag-$CACHETAG_VERSION

echo "===== base build tooling ====="
apt-get update -qq
apt-get install -y --no-install-recommends \
	build-essential dpkg-dev debhelper fakeroot ca-certificates

echo "===== installing the cohort Vinyl packages ====="
# apt is given the .deb files so that it resolves their own dependencies.
apt-get install -y --no-install-recommends \
	/out/vinyl-cache_"$VINYL_PACKAGE_VERSION"_*.deb \
	/out/vinyl-cache-dev_"$VINYL_PACKAGE_VERSION"_*.deb

echo "===== the installed development surface ====="
pkg-config --modversion vinylapi
echo "libdir:  $(pkg-config --variable=libdir vinylapi)"
vinyl_libdir=$(pkg-config --variable=libdir vinylapi)
vmoddir=$(pkg-config --define-variable=libdir="$vinyl_libdir" --variable=vmoddir vinylapi)
echo "vmoddir: $vmoddir"
echo "expected vmoddir: $VINYL_VMODDIR"
[ "$vmoddir" = "$VINYL_VMODDIR" ] || { echo "E: vmoddir mismatch" >&2; exit 1; }
echo "installed vmod_abi.h:"
cat "$(pkg-config --variable=pkgincludedir vinylapi)/vmod_abi.h"

echo "===== declared build dependencies ====="
cd "$src"
# This is the load-bearing check for the cohort model: Build-Depends names
# vinyl-cache-dev at an exact version, and it must be satisfiable by the
# package installed above.
apt-get build-dep -y ./
dpkg-checkbuilddeps

echo "===== resolved buildroot contents ====="
dpkg-query -W -f='${binary:Package}=${Version}\n' | sort > /out/logs/cachetag-buildroot-packages.txt
wc -l < /out/logs/cachetag-buildroot-packages.txt

echo "===== dpkg-buildpackage ====="
export SOURCE_DATE_EPOCH=$CACHETAG_SOURCE_DATE_EPOCH
dpkg-buildpackage -us -uc

echo "===== produced files ====="
cd /work/build
ls -la libvmod-cachetag*

echo "===== ABI metadata as built ====="
deb=$(ls libvmod-cachetag_"$CACHETAG_DEBIAN_VERSION"_*.deb)
dpkg-deb -I "$deb"
echo "--- contents ---"
dpkg-deb -c "$deb"

echo "===== assertions ====="
depends=$(dpkg-deb -f "$deb" Depends)
echo "Depends: $depends"
case "$depends" in
	*"vinyld-abi-$VINYL_STRICT_ABI"*) echo "OK: depends on vinyld-abi-$VINYL_STRICT_ABI" ;;
	*) echo "E: missing exact strict-ABI dependency" >&2; exit 1 ;;
esac
case "$depends" in
	*"vinyld-vrt"*) echo "OK: depends on vinyld-vrt" ;;
	*) echo "E: missing vinyld-vrt dependency" >&2; exit 1 ;;
esac
# The cohort-qualified dependency. The ABI token above is a hash of the upstream
# source revision and cannot distinguish two builds of it; this one can.
case "$depends" in
	*"vinyld-cohort-$COHORT_ID"*) echo "OK: depends on vinyld-cohort-$COHORT_ID" ;;
	*) echo "E: missing cohort-qualified dependency vinyld-cohort-$COHORT_ID" >&2; exit 1 ;;
esac
# NB: read the listing into a variable first. "dpkg-deb -c | grep -q" makes
# grep exit on the first match, dpkg-deb then dies of SIGPIPE, and under
# `set -o pipefail` the pipeline reports failure even though the file WAS
# found. This exact trap already cost a run in the step-6 hardening work.
contents=$(dpkg-deb -c "$deb")
case "$contents" in
	*"$VINYL_VMODDIR/libvmod_cachetag.so"*)
		echo "OK: libvmod_cachetag.so is packaged into $VINYL_VMODDIR" ;;
	*) echo "E: libvmod_cachetag.so is not in $VINYL_VMODDIR" >&2; exit 1 ;;
esac
stray=$(printf '%s\n' "$contents" | { grep -E '\.(la|a)$' || true; })
if [ -n "$stray" ]; then
	echo "E: libtool archive or static library shipped:" >&2
	printf '%s\n' "$stray" >&2
	exit 1
fi
echo "OK: no libtool archives or static libraries"

echo "===== hardening inspection ====="
mkdir -p /tmp/cx && dpkg-deb -x "$deb" /tmp/cx
so=/tmp/cx$VINYL_VMODDIR/libvmod_cachetag.so
fail=0
check() { if [ "$1" -eq 0 ]; then printf 'PASS  %-18s %s\n' "$2" "$3"; else printf 'FAIL  %-18s %s\n' "$2" "$3"; fail=1; fi; }
dyn=$(readelf -W --dyn-syms --syms "$so" 2>/dev/null || true)
seg=$(readelf -W -l "$so" 2>/dev/null || true)
dynm=$(readelf -W -d "$so" 2>/dev/null || true)
hdr=$(readelf -W -h "$so" 2>/dev/null || true)
case "$dyn" in *__stack_chk_fail*) check 0 stack-protector "__stack_chk_fail referenced";; *) check 1 stack-protector "absent";; esac
case "$seg" in *GNU_RELRO*) check 0 relro-segment "GNU_RELRO present";; *) check 1 relro-segment "absent";; esac
case "$dynm" in *BIND_NOW*|*NOW*) check 0 bind-now "BIND_NOW set";; *) check 1 bind-now "absent";; esac
case "$hdr" in *"Type:"*DYN*) check 0 pic "ELF type DYN";; *) check 1 pic "not DYN";; esac
chk=$( { printf '%s' "$dyn" | grep -oE '__[a-z0-9_]+_chk\b' || true; } | sort -u | tr '\n' ' ')
if [ -n "$chk" ]; then check 0 fortify-source "$chk"; else check 1 fortify-source "no __*_chk symbols"; fi
[ "$fail" -eq 0 ] || { echo "E: hardening inspection failed" >&2; exit 1; }
echo "HARDENING INSPECTION: PASS"

echo "===== publishing artifacts ====="
cp -v /work/build/libvmod-cachetag*.deb /work/build/libvmod-cachetag*.dsc \
	/work/build/libvmod-cachetag*.changes /work/build/libvmod-cachetag*.buildinfo \
	/work/build/libvmod-cachetag*.tar.* /out/ 2>/dev/null || true
ls -la /out
echo "===== stage-cachetag complete ====="
