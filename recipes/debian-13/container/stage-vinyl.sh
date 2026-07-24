#!/bin/bash
#
# Build the Vinyl Cache 9 Debian source and binary packages.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

src=/work/build/vinyl-cache-$VINYL_UPSTREAM_VERSION

echo "===== buildroot ====="
head -3 /etc/os-release

echo "===== base build tooling ====="
apt-get update -qq
apt-get install -y --no-install-recommends \
	build-essential dpkg-dev debhelper fakeroot ca-certificates
dpkg-architecture -qDEB_HOST_ARCH

echo "===== declared build dependencies ====="
cd "$src"
# apt resolves debian/control's Build-Depends. Using the declared list rather
# than a hand-written apt-get line is the gate that catches an undeclared
# build dependency.
apt-get build-dep -y ./

echo "===== resolved buildroot contents ====="
dpkg-query -W -f='${binary:Package}=${Version}\n' | sort | tee /out/logs/vinyl-buildroot-packages.txt | wc -l
echo "--- toolchain ---"
gcc --version | head -1
dpkg-query -W -f='${Version}\n' debhelper dpkg-dev

echo "===== effective build flags ====="
DEB_BUILD_MAINT_OPTIONS=hardening=+all dpkg-buildflags | tee /out/logs/vinyl-buildflags.txt

echo "===== dpkg-buildpackage ====="
export SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH
dpkg-buildpackage -us -uc

echo "===== produced files ====="
cd /work/build
ls -la

echo "===== ABI metadata as built ====="
for f in vinyl-cache_*.deb; do
	[ -e "$f" ] || continue
	echo "--- dpkg-deb -I $f ---"
	dpkg-deb -I "$f"
done

echo "===== assertions ====="
runtime_deb=$(ls vinyl-cache_"$VINYL_PACKAGE_VERSION"_*.deb)
provides=$(dpkg-deb -f "$runtime_deb" Provides)
echo "Provides: $provides"
case "$provides" in
	*"vinyld-abi-$VINYL_STRICT_ABI"*) echo "OK: runtime provides vinyld-abi-$VINYL_STRICT_ABI" ;;
	*) echo "E: runtime does not provide vinyld-abi-$VINYL_STRICT_ABI" >&2; exit 1 ;;
esac
case "$provides" in
	*"vinyld-vrt (= "*) echo "OK: runtime provides a versioned vinyld-vrt" ;;
	*) echo "E: runtime does not provide vinyld-vrt" >&2; exit 1 ;;
esac
vrt=$(printf '%s' "$provides" | tr ',' '\n' | sed -n 's/.*vinyld-vrt (= \([0-9.]*\)).*/\1/p')
echo "VRT version as built: $vrt"
printf '%s\n' "$vrt" > /out/logs/vinyl-vrt.txt

dev_deb=$(ls vinyl-cache-dev_"$VINYL_PACKAGE_VERSION"_*.deb)
dev_depends=$(dpkg-deb -f "$dev_deb" Depends)
echo "vinyl-cache-dev Depends: $dev_depends"
case "$dev_depends" in
	*"vinyl-cache (= $VINYL_PACKAGE_VERSION)"*)
		echo "OK: dev package depends on the exact matching runtime" ;;
	*) echo "E: dev package does not depend on the exact matching runtime" >&2; exit 1 ;;
esac

echo "===== hardening inspection (production profile) ====="
mkdir -p /tmp/hx
dpkg-deb -x "$runtime_deb" /tmp/hx
vinyld=/tmp/hx/usr/sbin/vinyld
fail=0
check() { if [ "$1" -eq 0 ]; then printf 'PASS  %-18s %s\n' "$2" "$3"; else printf 'FAIL  %-18s %s\n' "$2" "$3"; fail=1; fi; }
dyn=$(readelf -W --dyn-syms --syms "$vinyld" 2>/dev/null || true)
seg=$(readelf -W -l "$vinyld" 2>/dev/null || true)
dynm=$(readelf -W -d "$vinyld" 2>/dev/null || true)
hdr=$(readelf -W -h "$vinyld" 2>/dev/null || true)
case "$dyn" in *__stack_chk_fail*) check 0 stack-protector "__stack_chk_fail referenced";; *) check 1 stack-protector "absent";; esac
case "$seg" in *GNU_RELRO*) check 0 relro-segment "GNU_RELRO present";; *) check 1 relro-segment "absent";; esac
case "$dynm" in *BIND_NOW*|*NOW*) check 0 bind-now "BIND_NOW set (full RELRO)";; *) check 1 bind-now "absent";; esac
case "$hdr" in *"Type:"*DYN*) check 0 pie "ELF type DYN";; *) check 1 pie "not DYN";; esac
chk=$( { printf '%s' "$dyn" | grep -oE '__[a-z0-9_]+_chk\b' || true; } | sort -u | tr '\n' ' ')
if [ -n "$chk" ]; then check 0 fortify-source "$chk"; else check 1 fortify-source "no __*_chk symbols"; fi
[ "$fail" -eq 0 ] || { echo "E: hardening inspection failed" >&2; exit 1; }
echo "HARDENING INSPECTION: PASS"

echo "===== publishing artifacts ====="
cp -v /work/build/*.deb /work/build/*.dsc /work/build/*.changes \
	/work/build/*.buildinfo /work/build/*.tar.* /out/ 2>/dev/null || true
ls -la /out
echo "===== stage-vinyl complete ====="
