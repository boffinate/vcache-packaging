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

- **dispatch** (integer-driven VCL dispatch), **pesi** (parallel ESI VDP), **tus** (resumable uploads), **zipflow** (ZIP streaming VDP): upstream configure demands the engine's source tree. The shared `engine_source: required` provisioning path supplies it; without that catalog declaration the failure is structural rather than compatibility evidence.
- **file** (libvmod-file, re-read files at intervals): not vinylized; updated 2025-09 for Varnish 8.0. Sweep: green on Varnish 9.0.3, red on vinyl (`VRT_synth_page` removed). Add with `families: varnish`.

### Group 2 verification (2026-08-11, same day)

The four entries were written same-day; the VINYLSRC requirement was confirmed verbatim in tus and zipflow configure.ac (not just pesi), and zipflow additionally needs zlib and carries the Adler code under the Zlib license in a submodule (`BSD-2-Clause AND Zlib`). file confirmed un-Vinylized (`VARNISH_PREREQ([6.5.0])`, no VINYL anywhere) and is the only one of the four needing autoconf-archive.

A container experiment tested whether the harness could satisfy VINYLSRC from the engine release tarball (`ENGINE_TARBALL_URL` is already in `matrix.py env` output): the extracted vinyl-cache-9.0.1 dist tree contains `include/miniobj.h`, pesi bootstraps and configures cleanly against it (`VINYL_PREREQ([9.0],[trunk])` accepts the 9.0.1 release), and the one missing piece was `VSC_main.h` — a header the engine build generates from `lib/libvsc/VSC_main.vsc`, which the dist archive does not ship but the installed prefix's own vsctool regenerates with a single command. Trunk clones can add another class of missing files: daemon-private headers present in the configured engine build but absent from the installed development prefix. That became DESIGN.md decision 14: the `engine_source: required` catalog flag, one shared provisioning step in the build scripts (fetch the engine's source pin, regenerate VSC headers, restore private headers captured from the exact engine build, export VINYLSRC/VARNISHSRC). A full in-tree engine configure was rejected (needs python3-sphinx, minutes of build).

Verified through the real harness on debian-13-arm64 against vinyl-9.0.1 (evidence `work/esrc-verify/`):

- **pesi: green in both modes.** Compat pass with both pesi and pesi_debug loading (after adding the missing zlib build dep its link needed); package pass with the .deb shipping both modules and the fresh-container install check importing both (`package.modules` added — without it the check covered only the id).
- **tus: green in compat** — first build ever, straight pass.
- **zipflow: default make target fixed.** configure passes, but its noinst test binary `zfr_iter_test` links `lib/libvinyl/libvinyl.la` from a *compiled* engine tree, which the source-only provisioning deliberately does not produce. The catalog now builds only `libvmod_zipflow.la`; the upstream suite remains opt-in. Its pandoc build dep (README regeneration when building from git) is real and declared.
- **Generator fix found along the way**: debhelper's default `dh_auto_test` was running every VMOD's full `make check` inside deb package builds — pesi's suite (52/54 against vinyl, two ESI logexpect timeouts) exposed it. The generated rules now no-op `dh_auto_test`; upstream suites run only via the compat lane's `tests:` opt-in, matching the RPM spec which never had a `%check`.

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

- **varnish-rs Rust family** (reqwest, fileserver, rers, fcgi): very active, varnish crate 0.7+ supports 8.0/9.0, but needs a cargo toolchain in the build containers — a DESIGN.md-level decision, not a catalog entry. Researched in depth 2026-08-11: this is a true second build-type (`cargo build` + bindgen/libclang, no autotools anywhere, different tests and recipes), so if adopted it becomes a `build:` discriminator in the catalog with its own script branch — the successor to decision 14's earmarked script idea, not an extension of the `engine_source` flag. Key facts: MSRV 1.90 exceeds Debian 13's rustc 1.85 (rustup or a newer base needed there; EL10 ≥10.2 distro rust suffices); varnish-sys probes `varnishapi` only, so vinyl columns are red today, but the maintainer has said a vinylapi probe would be merged if contributed and nigoroll volunteered — vinyl may go green upstream with no work here; Varnish Software's `all-packager` repo is direct deb/rpm prior art (rustup in dh_auto_configure, `cargo fetch --locked`, ABI-hash substvar engine dep).
- **riscv/tinykvm** (varnish org, GPL-3 dual-licensed, active 2026): researched 2026-08-11 — surprisingly NOT a second pipeline. Both keep a standard `VARNISH_VMODS` autotools front-end (their cmake machinery is internal, driven by the generated Makefiles) and locate the engine via pkg-config, no source tree. They would be ordinary catalog entries with heavier build deps (cmake, C++17/20 toolchain, libcrypto; tinykvm adds libcurl/libarchive/pcre/jemalloc and three submodules). Caveats that keep them deferred: unproven on 9.x (floor 6.0, upstream tests on 7.6/7.7), expected red on vinyl (varnishapi.pc), tinykvm is x86_64-only (never verifiable on this arm64 host) with KVM needed at run time though not for the load check, and both are effectively Varnish Software products whose first-class flavour is Enterprise.
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
