#!/bin/bash
#
# Installed-package smoke test: the plan's 11-step scenario, run in a fresh
# almalinux:9 container that has never seen the build lane. The only inputs are
# the built RPMs under /out/packages.
#
# Every step prints the command output it asserts on. A silent pass is not
# evidence.

set -euo pipefail

. /recipes/cohort.env

vinyl_evr="$VINYL_VERSION-$VINYL_RELEASE.el9"
arch=$(uname -m)
isa="($(rpm --eval '%{_isa}' | tr -d '()'))"
fail=0

step() { printf '\n===== STEP %s =====\n' "$*"; }
ok()   { printf 'PASS: %s\n' "$*"; }
bad()  { printf 'FAIL: %s\n' "$*"; fail=1; }

# curl-minimal is already in the base image and provides /usr/bin/curl;
# installing full curl would conflict with it.
dnf -y install python3 >/dev/null
command -v curl >/dev/null

# EPEL is a genuine runtime prerequisite of the EL9 cohort runtime package, not a
# convenience for this script: vinyld is built --with-unwind and libunwind.so.8
# ships in neither BaseOS nor AppStream. Installation instructions for this lane
# must say so.
dnf -y install epel-release >/dev/null

# ---------------------------------------------------------------------------
step "0. inputs"
ls -l /out/packages/*.rpm
printf 'architecture: %s   isa: %s\n' "$arch" "$isa"

# ---------------------------------------------------------------------------
step "0b. the cachetag package's declared dependencies"
cachetag_rpm=$(ls /out/packages/libvmod-cachetag-"$CACHETAG_VERSION"-"$CACHETAG_RELEASE".el9."$arch".rpm)
rpm -qpR "$cachetag_rpm"

if rpm -qpR "$cachetag_rpm" | grep -qx "vinyld(abi)$isa = $VINYL_STRICT_ABI"; then
	ok "cachetag requires the exact strict ABI, architecture-qualified"
else
	bad "cachetag does not require vinyld(abi)$isa = $VINYL_STRICT_ABI"
fi

# The negative half of the assertion: with no Vinyl runtime present, the ABI
# dependency must be the thing that is unsatisfiable. If cachetag installed here
# the dependency would be decorative.
step "0c. cachetag must NOT install without the cohort runtime"
if dnf -y install "$cachetag_rpm" > /tmp/no-runtime.log 2>&1; then
	bad "cachetag installed with no Vinyl runtime present"
	cat /tmp/no-runtime.log
else
	grep -E 'nothing provides|conflicting requests|vinyld' /tmp/no-runtime.log || true
	if grep -q "vinyld(abi)$isa = $VINYL_STRICT_ABI" /tmp/no-runtime.log; then
		ok "dnf refuses cachetag, naming the unsatisfied vinyld(abi) dependency"
	else
		bad "dnf refused cachetag but not because of vinyld(abi)"
	fi
fi

# ---------------------------------------------------------------------------
step "1. install the matching Vinyl runtime package"
dnf -y install /out/packages/vinyl-cache-"$vinyl_evr"."$arch".rpm
rpm -q vinyl-cache
printf '\nruntime Provides:\n'
rpm -q --provides vinyl-cache | sed 's/^/  /'

if rpm -q --provides vinyl-cache | grep -qx "vinyld(abi)$isa = $VINYL_STRICT_ABI"; then
	ok "runtime provides the exact strict ABI"
else
	bad "runtime does not provide vinyld(abi)$isa = $VINYL_STRICT_ABI"
fi

# ---------------------------------------------------------------------------
step "2. install the cachetag package through dnf"
dnf -y install "$cachetag_rpm"
rpm -q libvmod-cachetag

printf '\nwhat satisfies the strict ABI dependency:\n'
# rpm --whatprovides matches on the capability name only, so the exact ABI is
# checked separately against that package's own provides.
provider=$(rpm -q --whatprovides "vinyld(abi)$isa")
printf '%s\n' "$provider"
if [ "$provider" = "vinyl-cache-$vinyl_evr.$arch" ] &&
   rpm -q --provides "$provider" | grep -qx "vinyld(abi)$isa = $VINYL_STRICT_ABI"; then
	ok "the cohort runtime is the sole provider of vinyld(abi), at the exact ABI"
else
	bad "vinyld(abi) is satisfied by something other than the cohort runtime"
fi

# ---------------------------------------------------------------------------
step "3. the installed .so is in the runtime's VMOD directory"
vmoddir=$(pkg-config --variable=vmoddir vinylapi 2>/dev/null || echo "")
if [ -z "$vmoddir" ]; then
	# vinylapi.pc lives in the devel package, which the smoke deliberately
	# does not install. Fall back to the runtime's own directory.
	vmoddir=$(rpm -ql vinyl-cache | sed -n 's#\(.*/vmods\)/libvmod_std\.so#\1#p' | head -1)
	printf 'vmoddir (from the runtime package file list): %s\n' "$vmoddir"
else
	printf 'vmoddir (from vinylapi.pc): %s\n' "$vmoddir"
fi

so="$vmoddir/libvmod_cachetag.so"
ls -l "$so"
rpm -qf "$so"
if [ "$(rpm -qf --qf '%{NAME}' "$so")" = libvmod-cachetag ] &&
   [ "$vmoddir" = "$(rpm --eval %{_libdir})/vinyl-cache/vmods" ]; then
	ok "libvmod_cachetag.so is owned by the cachetag package and sits in the standard VMOD directory"
else
	bad "the VMOD is not in the runtime's standard VMOD directory"
fi

printf '\nSELinux: '
if command -v getenforce >/dev/null 2>&1; then getenforce; else echo "not available in this container"; fi
printf 'matchpathcon/restorecon verification is deferred to a CI job on a host\n'
printf 'that can run SELinux enforcing; Docker on macOS cannot.\n'

# ---------------------------------------------------------------------------
step "4. compile a VCL containing 'import cachetag'"
install -m 0644 /recipes/smoke/smoke.vcl /tmp/smoke.vcl
vinyld -C -f /tmp/smoke.vcl > /tmp/vcl.c 2> /tmp/vcl.err && rc=0 || rc=$?
if [ $rc -eq 0 ]; then
	ok "VCL with 'import cachetag' compiled ($(wc -l < /tmp/vcl.c) lines of C emitted)"
else
	bad "VCL compilation failed"
	cat /tmp/vcl.err
fi

# ---------------------------------------------------------------------------
step "5. start Vinyl with Default storage"
python3 /recipes/smoke/backend.py > /tmp/backend.log 2>&1 &
backend_pid=$!
for _ in $(seq 50); do
	curl -sf -o /dev/null http://127.0.0.1:8080/ && break
	sleep 0.2
done

vinyld -a 127.0.0.1:6081 -f /tmp/smoke.vcl -n /tmp/vinylsmoke \
	-s default,64m -T 127.0.0.1:6082 -P /tmp/vinyld.pid
# Readiness is checked over the CLI, not with an HTTP request: a probe request
# would itself be cached and tagged, and step 6 would then measure a warm hit
# while claiming to measure a cold miss.
for _ in $(seq 100); do
	vinyladm -n /tmp/vinylsmoke status 2>/dev/null | grep -q running && break
	sleep 0.2
done
vinyladm -n /tmp/vinylsmoke status
vinyladm -n /tmp/vinylsmoke vcl.list
vinyladm -n /tmp/vinylsmoke storage.list
ok "vinyld started with Default storage and loaded the VCL"

fetch() { curl -sS -D /tmp/hdr -o /tmp/body "$@" http://127.0.0.1:6081/article; }

# ---------------------------------------------------------------------------
step "6. fetch and cache an object carrying a tag"
# The backend readiness probe in step 5 consumed generation 1, so the first
# cached generation here is 2. Only the difference between generations matters.
fetch
gen_first=$(cat /tmp/body)
grep -E '^(HTTP|X-Cache|X-Tag-Objects)' /tmp/hdr
printf 'body: %s\n' "$gen_first"
if grep -qi '^X-Cache: MISS' /tmp/hdr && grep -qi '^X-Tag-Objects: [1-9]' /tmp/hdr; then
	ok "object fetched, cached, and registered against its tags"
else
	bad "first fetch was not a tagged miss"
fi

# ---------------------------------------------------------------------------
step "7. confirm a warm hit"
fetch
grep -E '^(HTTP|X-Cache)' /tmp/hdr
printf 'body: %s\n' "$(cat /tmp/body)"
if grep -qi '^X-Cache: HIT' /tmp/hdr && [ "$(cat /tmp/body)" = "$gen_first" ]; then
	ok "second fetch was a cache hit serving the same object"
else
	bad "second fetch was not a warm hit"
fi

# ---------------------------------------------------------------------------
step "8. purge the tag through the VMOD interface"
curl -sS -X PURGE -H 'Cache-Tag-Purge: article:1' -D /tmp/phdr -o /dev/null \
	http://127.0.0.1:6081/article
grep -E '^(HTTP|X-Purged)' /tmp/phdr
# purge_header() returns -1 for an accepted purge; -2, -3 and -4 are the error
# codes. It is a status, not a count of invalidated objects.
if grep -qi '^X-Purged: -1' /tmp/phdr; then
	ok "purge_header() accepted the tag purge (status -1)"
else
	bad "purge_header() rejected the purge: $(grep -i '^X-Purged' /tmp/phdr)"
fi

# ---------------------------------------------------------------------------
step "9. the old object is gone and a fresh backend response is served"
fetch
grep -E '^(HTTP|X-Cache)' /tmp/hdr
gen_after=$(cat /tmp/body)
printf 'body before purge: %s\nbody after purge : %s\n' "$gen_first" "$gen_after"
if [ "$gen_after" != "$gen_first" ] && grep -qi '^X-Cache: MISS' /tmp/hdr; then
	ok "the purged object was not served; the backend was consulted again"
else
	bad "a purged object was served from cache"
fi

# ---------------------------------------------------------------------------
step "10. stop Vinyl cleanly"
vinyl_pid=$(cat /tmp/vinyld.pid)
kill -TERM "$vinyl_pid"
for _ in $(seq 100); do
	kill -0 "$vinyl_pid" 2>/dev/null || break
	sleep 0.2
done
if kill -0 "$vinyl_pid" 2>/dev/null; then
	bad "vinyld did not exit on SIGTERM"
	kill -KILL "$vinyl_pid" || true
else
	ok "vinyld exited cleanly on SIGTERM"
fi
kill "$backend_pid" 2>/dev/null || true

# ---------------------------------------------------------------------------
step "11. uninstall cachetag and verify file cleanup"
mapfile -t owned < <(rpm -ql libvmod-cachetag)
printf 'package-owned paths:\n'; printf '  %s\n' "${owned[@]}"
dnf -y remove libvmod-cachetag
left=0
for p in "${owned[@]}"; do
	case $p in
	"$vmoddir") continue ;;      # owned by the runtime package, must survive
	/usr/lib/.build-id*) continue ;;  # shared directory, co-owned by every
	                                  # package that ships an ELF object
	esac
	if [ -e "$p" ]; then printf 'left behind: %s\n' "$p"; left=1; fi
done
if [ $left -eq 0 ]; then ok "every cachetag-owned path was removed"; else bad "cachetag left files behind"; fi
if rpm -q vinyl-cache >/dev/null && [ -d "$vmoddir" ]; then
	ok "the Vinyl runtime and its VMOD directory are untouched"
else
	bad "removing cachetag disturbed the Vinyl runtime"
fi
if [ -f /tmp/smoke.vcl ]; then ok "user VCL untouched by the uninstall"; else bad "user VCL disappeared"; fi

# ---------------------------------------------------------------------------
printf '\n===== SMOKE RESULT =====\n'
if [ $fail -eq 0 ]; then echo "ALL STEPS PASSED"; else echo "SMOKE FAILED"; fi
exit $fail
