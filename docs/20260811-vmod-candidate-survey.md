# VMOD candidate survey — 2026-08-11

Status: informational. This note records the research behind the 2026-08 catalog expansion. The catalog (`vmods/*.yml`) remains the only normative pin source.

## Method

Three research streams, run 2026-08-11:

1. **v1 empirical sweep** (`../vcache-packaging-old/survey/`, report dated 2026-07-26): 113 VMODs built and load-smoked in clean Debian 13 containers against varnishd 9.0.3 and vinyld trunk. Caveat: both lanes carried pkg-config/m4 naming shims, so a survey "green" proves API compatibility, not build-system name compatibility. v2 carries no shims, so Varnish-flavoured configure scripts still red on vinyl columns and vice versa.
2. **Full enumeration of code.uplex.de/uplex-varnish** (50 repos, Forgejo listing + per-repo branch/tag/commit inspection).
3. **Ecosystem sweep**: the vinyl-cache.org VMOD directory (varnish-cache.org/vmods now redirects there), GitHub orgs (varnish, carlosabalde, nigoroll, xcir, otto-de, varnish-rs, varnishcache-friends), and download.gnu.org.ua releases, filtered to upstream activity since ~August 2025.

## The key finding: the UPLEX Vinylize sweep

In March 2026 UPLEX converted their active repos to target Vinyl Cache natively: master branches build against `vinylapi.pc` / `VINYL_*` autotools macros, and each active repo keeps a separate `8.0` branch for Varnish-Cache 8.0. The slash README states they support only Vinyl Cache going forward. Consequences for the matrix:

- uplex VMODs are the mirror image of redis/querystring/varnish-modules: expected **green on vinyl columns, red on Varnish 9** (no `VINYL_*` macros there). Same shape as selector, which was the first uplex module to vinylize.
- Tags are stale org-wide (mostly 2021–2022); the vinylized state exists only on branch heads. Pins are branches, per the selector precedent (`ref: master`, `version: "0~master"`).
- Canonical home is code.uplex.de (Forgejo); gitlab.com/uplex/varnish is the mirror that handles issues/MRs. Content is identical.

Other upstreams that have vinylized independently: varnishcache-friends/libvmod-geoip2 (2026-08-01), kenshaw/libvmod-dns (2026-07-25), nigoroll/libvmod-dynamic (master), xcir/libvmod-xcounter (`vinyl-main` branch).

## Group 1 — uplex, clean adds (17 entries)

All autotools with a `bootstrap` script, actively maintained (2026 commits), FreeBSD/BSD-licensed unless noted. All expected green-on-vinyl / red-on-varnish-9 → `package.families: vinyl`.

| id | repo | what it is | deps / notes |
|---|---|---|---|
| objvar | varnish-objvar | constant, globalvar, taskvar, topvar: VCL variables as objects; 4 modules, one tree | none; `package.modules` entry like varnish-modules; most active uplex repo (2026-07-28) |
| re | libvmod-re | PCRE2 regex with subexpression capture | pcre2 |
| re2 | libvmod-re2 | Google RE2 linear-time regex | libre2 (C++) |
| blobdigest | libvmod-blobdigest | digests/HMACs for BLOB | vendored librhash; Perl 5 at build |
| dispatch | libvmod-dispatch | dispatch to VCL labels/subs by integer id | none |
| cluster | libvmod-cluster | clustering/sharding director | none |
| blobsynth | libvmod-blobsynth | serve BLOBs as synthetic bodies | none; Public Domain |
| all-healthy | libvmod-all_healthy | director healthy only if all listed backends healthy | none |
| iconv | libvmod-iconv | charset conversion in VCL | libc iconv |
| frozen | libvmod-frozen | JSON parsing in VCL | git submodule (harness already checks out recursively) |
| j | libvmod-j | JSON formatter | git submodule; check jansson |
| crypto | libvmod-crypto | verify asymmetric crypto signatures | OpenSSL libcrypto |
| gcrypt | libvmod-gcrypt | libgcrypt access (AES, PRNG) | libgcrypt >= 1.6.3 |
| brotli | libvfp-brotli | VFP: brotli (de)compression of fetches | brotli libs; README says it needs vinyl master, so the release lane may red while trunk is green — that is trunk CI doing its job |
| pipe | libvdfp-pipe | VDP piping responses through external commands | none; upstream marks it development |
| hoailona | libvmod-hoailona | Akamai SecureHD policy support | niche; pairs with blobdigest |
| ipblocker | libvmod-ipblocker | dynamic IP blocklists fed by a companion daemon | VMOD builds standalone; the reference daemon is a Perl script we do not package |

## Group 2 — uplex, add with eyes open (4 entries)

- **pesi** (parallel ESI VDP), **tus** (resumable uploads), **zipflow** (ZIP streaming VDP): the v1 sweep — run in July 2026, after the Vinylize commits — classified all three `needs-source-tree`: configure demands the engine's source tree, which the harness does not provide. They will be permanently red at configure on every cell until upstream changes that. Red is information, but these reds are structural. pesi (real 1.3.2 release, 2024) is the one worth re-verifying first in case the verdict is stale.
- **file** (libvmod-file, re-read files at intervals): not vinylized; updated 2025-09 for Varnish 8.0. Sweep: green on Varnish 9.0.3, red on vinyl (`VRT_synth_page` removed). Add with `families: varnish`.

## Group 3 — non-uplex, active in the last year (8 entries)

| id | upstream | evidence | notes |
|---|---|---|---|
| geoip2 | github.com/varnishcache-friends/libvmod-geoip2 | vinyl-native 2026-08-01; BSD-2 | libmaxminddb; pin `main` (v1.3.0 predates vinylization); supersedes fgsch original |
| dynamic | github.com/nigoroll/libvmod-dynamic | master targets Vinyl post-8.0; active 2026-06 | build without optional getdns; no usable tags, pin master |
| dns | github.com/kenshaw/libvmod-dns | vinylized 2026-07-25; Apache-2.0 | libresolv only; zero tags ever, pin master |
| xcounter | github.com/xcir/libvmod-xcounter | `vinyl-main` branch targets Vinyl 9.0.x; sweep green | pin `vinyl-main`, `families: vinyl`; verify COPYING |
| cfg | github.com/carlosabalde/libvmod-cfg | explicit `9.0-21.1` tag 2026-03; same maintainer as redis | libcurl + LuaJIT (`--disable-luajit` exists); pin the 9.0 tag — sweep failed *master* on both lanes, the tag matters |
| gossip | github.com/carlosabalde/libvmod-gossip | `9.0-18.0` tag; sweep: green Varnish 9, red vinyl (object-event API) | `families: varnish` |
| basicauth | download.gnu.org.ua/release/vmod-basicauth | release 2.2 in the Feb/Mar-2026 gnu.org.ua wave | sweep: green Varnish 9, red vinyl (version-guard macros); GPL-3; release tarball needs no autotools |
| uuid | github.com/otto-de/libvmod-uuid | sweep green both lanes (shimmed); v1 marked it deferred-not-rejected purely on tag policy | OSSP uuid; pin master; `families: varnish` |

## Deferred, deliberately

- **varnish-rs Rust family** (reqwest, fileserver, rers, fcgi): very active, varnish crate 0.7+ supports 8.0/9.0, but needs a cargo toolchain in the build containers — a DESIGN.md-level decision, not a catalog entry.
- **gnu.org.ua second string**: variable needs PCRE1 (removed from Debian 13, red everywhere); dbrw/sql drag in DB client libs and failed the sweep; binlog has no sweep verdict.
- **uplex embryonic**: acltools, less_than_all_healthy (1–3 commits, no README) — watch for next wave. weightadjust failed both sweep lanes.

## Checked and rejected

curl (tags abandoned at Varnish 6.3-era), valkey (author archiving; libvalkey not in Debian 13), digest/memcached/maxminddb/hashids/otp/dyncounters (dormant 2+ years), awsrest (caps at Varnish 7.0), ja4/harden (anonymous account, 9-untested), wasm (vendored Wasmtime), riscv/tinykvm (cmake, massive vendored runtimes), unleash (locked to Varnish 7.3), impress (no license), prequal (pins a personal fork), queryfilter/querymodifier (querystring overlap, 9 unknown).

uplex dead/EOL, not added: blob + blobcode (merged into core 5.2), blobsha256 (superseded by blobdigest), pcre2 (superseded by re), vslp (became the core shard director), backend_dyn, health (self-declared broken since 2018), ece, esiextra, esicookies, vtstor, oob_probe, dcs_classifier, objesi, etag. Non-VMOD uplex repos: slash, k8s-ingress, k8s-vcl-reloader, varnishapi, trackrdrd, varnishevent(3), uplex-varnish-dpkg (their sbuild/aptly packaging — useful prior art), archives.

## Corrections found while writing the Group 1 entries (2026-08-11, same day)

The 17 Group 1 entries were written and validated the same day; per-upstream verification corrected the table above in a few places. The catalog files are authoritative; deltas worth recording:

- Default branches: libvmod-iconv and libvmod-j default to `trunk`, libvmod-ipblocker to `main`; the rest use `master`. Pins follow the real branch (`0~trunk`, `0~main`).
- Licenses: blobdigest is `BSD-2-Clause AND MIT` (the vendored librhash code carries an MIT grant, not public domain); blobsynth is `Unlicense`; **ipblocker has no license file, README section, or source header at all** — its entry assumes BSD-2-Clause per UPLEX convention, flagged in the entry. Raise upstream before shipping a package that asserts it.
- Deps: re2 is C++ (`gcc-c++` added to its rpm build_deps; Debian's build-essential already ships g++). crypto's configure hard-requires the `openssl` CLI (test scaffolding, checked unconditionally) — added to both dep lists. blobdigest needs perl at build. j needs no jansson (the sweep hint was wrong; its configure has zero library checks). Only re needs autoconf-archive; the other 16 use no AX_ macros.
- Trunk-vs-release: brotli's README requires a Vinyl *master* commit (54af42d+), and pipe's configure declares `VINYL_PREREQ([9.0],[trunk])` — both may be red on the vinyl release column and green on trunk. Expected, and useful.
- all-healthy: hyphenated catalog id cannot serve as the module import name, so it declares `package.modules: [all_healthy]` (varnish-modules precedent).
- objvar module names confirmed from upstream configure (`VINYL_VMODS_GENERATED([constant globalvar taskvar topvar])`).

## Open risks

- **EL10 availability of new build deps** (libre2, brotli, LuaJIT, libmaxminddb, OSSP uuid, libgcrypt) is unverified; the Debian side is all in Debian 13. Check in a container before relying on RPM package cells.
- uplex `tests: make-check` suitability is unknown per module (v1 noted gcrypt's VTCs bind `${vmod_topbuild}`); Group 1 entries land without `tests:` and can gain it after container verification.
- No Group 1 pairing has been container-verified in v2 yet; the sweep evidence is shimmed-lane and branch-head from 2026-07-26.
