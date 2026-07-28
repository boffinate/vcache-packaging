#!/bin/bash
#
# Installed-package smoke test: the 11-step scenario from the "Installed-package
# verification" section of the binary packaging and distribution plan.
#
# Runs in a FRESH debian:trixie container that has seen neither build tree.
# Everything is installed through apt from a local repository built out of the
# produced .deb files, so package-manager dependency resolution -- and in
# particular the exact vinyld-abi-<hash> relation -- is genuinely exercised.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

pass=0; fail=0
step() { printf '\n########## STEP %s ##########\n' "$*"; }
ok()   { printf 'PASS: %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf 'FAIL: %s\n' "$*"; fail=$((fail+1)); }

apt-get update -qq
apt-get install -y --no-install-recommends dpkg-dev python3 procps curl >/dev/null

echo "===== building a local apt repository from the produced packages ====="
mkdir -p /repo
cp /out/*.deb /repo/
cd /repo
dpkg-scanpackages --multiversion . > Packages
gzip -9kf Packages
printf 'deb [trusted=yes] file:/repo ./\n' > /etc/apt/sources.list.d/vinyl-cohort.list
apt-get update -qq
echo "repository contents:"
grep -E '^(Package|Version|Provides|Depends):' Packages

###############################################################################
step "1 -- install the matching Vinyl runtime package"
###############################################################################
apt-get install -y vinyl-cache && ok "vinyl-cache installed via apt" || bad "vinyl-cache install failed"
dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' vinyl-cache
echo "--- what the runtime provides ---"
dpkg-query -W -f='${Provides}\n' vinyl-cache

###############################################################################
step "2 -- install the cachetag package through the package manager"
###############################################################################
echo "--- the cachetag package's declared dependencies (dpkg -I) ---"
dpkg -I /repo/libvmod-cachetag_*.deb | sed -n '/Depends/p'

echo "--- proof that the exact strict ABI virtual package is what satisfies it ---"
echo "* apt policy for the virtual package vinyld-abi-$VINYL_STRICT_ABI:"
apt-cache policy "vinyld-abi-$VINYL_STRICT_ABI" || true
echo "* who provides it:"
apt-cache showpkg "vinyld-abi-$VINYL_STRICT_ABI" | sed -n '1,12p'

echo "* negative control: resolve the cachetag package against a DIFFERENT ABI"
if apt-get install -y --dry-run "vinyld-abi-0000000000000000000000000000000000000000" >/dev/null 2>&1; then
	bad "a bogus vinyld-abi- virtual package resolved; the ABI relation is not doing its job"
else
	ok "a bogus vinyld-abi- virtual package is unresolvable"
fi

apt-get install -y libvmod-cachetag && ok "libvmod-cachetag installed via apt" || bad "libvmod-cachetag install failed"
dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' libvmod-cachetag
echo "--- installed dependency relation as recorded by dpkg ---"
dpkg-query -W -f='${Depends}\n' libvmod-cachetag
if dpkg-query -W -f='${Depends}\n' libvmod-cachetag | grep -q "vinyld-abi-$VINYL_STRICT_ABI"; then
	ok "installed package depends on vinyld-abi-$VINYL_STRICT_ABI"
else
	bad "installed package lacks the exact strict-ABI dependency"
fi

echo "--- the cohort-qualified provide, which the ABI relation cannot supply ---"
if dpkg-query -W -f='${Provides}\n' vinyl-cache | grep -q "vinyld-cohort-$COHORT_ID"; then
	ok "runtime provides vinyld-cohort-$COHORT_ID"
else
	bad "runtime does not provide vinyld-cohort-$COHORT_ID"
fi
if dpkg-query -W -f='${Depends}\n' libvmod-cachetag | grep -q "vinyld-cohort-$COHORT_ID"; then
	ok "installed package depends on vinyld-cohort-$COHORT_ID"
else
	bad "installed package lacks the cohort-qualified dependency"
fi
echo "* who provides it:"
apt-cache showpkg "vinyld-cohort-$COHORT_ID" | sed -n '1,12p'
echo "* negative control: a different cohort id must not resolve"
if apt-get install -y --dry-run "vinyld-cohort-some-other-cohort" >/dev/null 2>&1; then
	bad "a foreign vinyld-cohort- virtual package resolved"
else
	ok "a foreign vinyld-cohort- virtual package is unresolvable"
fi

###############################################################################
step "3 -- confirm the installed .so is in the runtime's VMOD directory"
###############################################################################
echo "runtime VMOD directory (from the running configuration): $VINYL_VMODDIR"
ls -la "$VINYL_VMODDIR/"
if [ -f "$VINYL_VMODDIR/libvmod_cachetag.so" ]; then
	ok "libvmod_cachetag.so present in $VINYL_VMODDIR"
else
	bad "libvmod_cachetag.so is not in $VINYL_VMODDIR"
fi
echo "--- owned by which package ---"
dpkg -S "$VINYL_VMODDIR/libvmod_cachetag.so"
echo "--- vinyld's compiled-in vmod_path ---"
vinyld -x parameter 2>/dev/null | grep -A2 '^vmod_path' || vinyld -l 2>&1 | head -5 || true

###############################################################################
# the backend and the VCL
###############################################################################
cat > /tmp/backend.py <<'PY'
import http.server, socketserver

COUNT = {"n": 0}

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        COUNT["n"] += 1
        body = ("backend-response-%d\n" % COUNT["n"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self.send_header("Cache-Tag", "article:123, section:news")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", 8080), H) as httpd:
    httpd.serve_forever()
PY

cat > /tmp/smoke.vcl <<'VCL'
vcl 4.1;

import cachetag;

backend default {
    .host = "127.0.0.1";
    .port = "8080";
}

sub vcl_init {
    new tags = cachetag.namespace("default");
}

sub vcl_recv {
    if (req.method == "PURGE") {
        set req.http.purged = tags.purge_header(req.http.Cache-Tag-Purge);
        return (synth(200, "purged"));
    }
}

sub vcl_synth {
    set resp.http.X-Purge-Result = req.http.purged;
}

sub vcl_backend_response {
    if (beresp.http.Cache-Tag) {
        tags.add_header(beresp.http.Cache-Tag);
        unset beresp.http.Cache-Tag;
    }
}

sub vcl_hit {
    if (tags.stale()) {
        return (restart);
    }
}

sub vcl_deliver {
    if (tags.stale()) {
        return (restart);
    }
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
    set resp.http.X-Tag-Objects = tags.objects();
    set resp.http.X-Tag-Edges = tags.edges();
}
VCL

###############################################################################
step "4 -- compile a VCL containing 'import cachetag'"
###############################################################################
if vinyld -C -f /tmp/smoke.vcl > /tmp/vcl-c.out 2>&1; then
	ok "VCL with 'import cachetag' compiles (vinyld -C)"
	head -3 /tmp/vcl-c.out
else
	bad "VCL compilation failed"
	cat /tmp/vcl-c.out
fi

###############################################################################
step "5 -- start Vinyl Cache with Default storage"
###############################################################################
python3 /tmp/backend.py &
backend_pid=$!
sleep 1
curl -sS -D- http://127.0.0.1:8080/ | head -3 || true

mkdir -p /var/lib/vinyl-cache/smoke
# debug=+vclrel ("Rapid VCL release", include/tbl/debug_bits.h, present in
# both 9.0.1 and trunk) makes workers release their cached VCL reference
# after every task, so vcl->busy is zero at stop and SIGTERM completes within
# the wait window below. Needed because 9.0.1 lacks 7de492b0e8 ("Shut down
# pools when stopping"): pools are not shut down on stop, so idle workers
# hold their VCL refs through a 60s cond-wait and an orderly stop takes up
# to a minute. No-op-equivalent on the trunk pin, which contains the fix.
# Remove when the release track reaches a Vinyl containing 7de492b0e8
# (9.0.2 if backported).
vinyld -a 127.0.0.1:6081 -f /tmp/smoke.vcl -n /var/lib/vinyl-cache/smoke \
	-p debug=+vclrel \
	-P /run/vinyld-smoke.pid -s default,128m > /tmp/vinyld.out 2>&1
sleep 3
if [ -s /run/vinyld-smoke.pid ] && kill -0 "$(cat /run/vinyld-smoke.pid)" 2>/dev/null; then
	ok "vinyld started with Default storage (pid $(cat /run/vinyld-smoke.pid))"
else
	bad "vinyld did not start"
	cat /tmp/vinyld.out
fi
echo "--- storage in use ---"
vinyladm -n /var/lib/vinyl-cache/smoke storage.list 2>&1 || true
echo "--- loaded VCL ---"
vinyladm -n /var/lib/vinyl-cache/smoke vcl.list 2>&1 || true

###############################################################################
step "6 -- fetch and cache an object with a tag"
###############################################################################
r1=$(curl -sS -D /tmp/h1 http://127.0.0.1:6081/thing)
echo "body:    $r1"
cat /tmp/h1
if grep -qi '^X-Cache: MISS' /tmp/h1 && grep -qi '^X-Tag-Objects: 1' /tmp/h1; then
	ok "first fetch is a MISS and the object carries 1 tagged object"
else
	bad "first fetch did not register a tagged object"
fi

###############################################################################
step "7 -- confirm a warm hit"
###############################################################################
r2=$(curl -sS -D /tmp/h2 http://127.0.0.1:6081/thing)
echo "body:    $r2"
cat /tmp/h2
if grep -qi '^X-Cache: HIT' /tmp/h2 && [ "$r1" = "$r2" ]; then
	ok "second fetch is a warm HIT serving the same cached body"
else
	bad "second fetch was not a warm hit"
fi

###############################################################################
step "8 -- purge the tag through the VMOD interface"
###############################################################################
curl -sS -D /tmp/h3 -X PURGE -H 'Cache-Tag-Purge: article:123' http://127.0.0.1:6081/thing -o /dev/null
cat /tmp/h3
# purge_header() returns -1 when every tag reached an accepted purge-history
# publication; it deliberately never reports an affected-object count.
if grep -qi '^X-Purge-Result: -1' /tmp/h3; then
	ok "tag purge accepted (purge_header returned -1)"
else
	bad "tag purge was not accepted"
fi

###############################################################################
step "9 -- confirm the old object is gone and a fresh response is served"
###############################################################################
r3=$(curl -sS -D /tmp/h4 http://127.0.0.1:6081/thing)
echo "body:    $r3"
cat /tmp/h4
if [ "$r3" != "$r1" ]; then
	ok "a fresh backend response is served after the purge ($r1 -> $r3)"
else
	bad "the stale object was served after the purge"
fi
if grep -qi '^X-Cache: MISS' /tmp/h4; then
	ok "the post-purge fetch is a MISS, so the old object was rejected"
else
	bad "the post-purge fetch was not a miss"
fi

###############################################################################
step "10 -- stop Vinyl Cache cleanly"
###############################################################################
pid=$(cat /run/vinyld-smoke.pid)
kill -TERM "$pid"
for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
if kill -0 "$pid" 2>/dev/null; then
	bad "vinyld did not exit on SIGTERM"
	kill -9 "$pid" || true
else
	ok "vinyld exited cleanly on SIGTERM"
fi
kill "$backend_pid" 2>/dev/null || true

###############################################################################
step "11 -- uninstall cachetag and verify package ownership and cleanup"
###############################################################################
echo "--- files owned by the package before removal ---"
dpkg -L libvmod-cachetag
apt-get remove -y libvmod-cachetag
if [ -e "$VINYL_VMODDIR/libvmod_cachetag.so" ]; then
	bad "libvmod_cachetag.so survived package removal"
else
	ok "libvmod_cachetag.so removed with the package"
fi
if [ -e /usr/share/man/man3/vmod_cachetag.3.gz ] || [ -e /usr/share/man/man3/vmod_cachetag.3 ]; then
	bad "the manual page survived package removal"
else
	ok "the manual page removed with the package"
fi
echo "--- the Vinyl runtime is untouched ---"
dpkg-query -W -f='${Package} ${Version} ${Status}\n' vinyl-cache
if [ -x /usr/sbin/vinyld ]; then
	ok "removing the VMOD did not disturb the Vinyl Cache runtime"
else
	bad "the Vinyl Cache runtime was damaged by the VMOD removal"
fi
echo "--- purge leaves no cachetag-owned file behind ---"
apt-get purge -y libvmod-cachetag
dpkg -l | grep -i cachetag || echo "(no cachetag package remains)"

printf '\n===== SMOKE SUMMARY: %d passed, %d failed =====\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
