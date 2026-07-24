# Steps 7–9, Debian 13 lane: the first coordinated Vinyl 9 + cachetag package cohort

Date: 2026-07-24

Status: Built, linted and smoke-tested end to end. Local arm64 process proof; the authoritative amd64 artifacts come from CI.

Implements the Debian 13 half of implementation steps 7–9 of [the accepted binary packaging and distribution plan](../../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md): turn the audited `pkg-vinyl-cache` material into minimal Vinyl 9 Debian packages with strict-ABI virtual provides, build the first Default-only coordinated Vinyl/cachetag cohort, and add native lint plus an installed-package smoke test.

This is the first time anything in `libvmod-cachetag/packaging/debian/` has ever been built. Its own README opened with "**Nothing in this directory has ever been built.**" That is no longer true.

## Headline result

Six packages built, `lintian` reduced to a single deliberately-unsuppressed warning, and the plan's 11-step installed-package scenario passed 16 of 16 assertions on its first execution. The cachetag package's dependency on `vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3` is satisfied by the Vinyl runtime package's virtual provide, and a bogus ABI hash is unresolvable — the coupling the whole plan exists to enforce works.

## Architecture caveat, stated once and up front

The development host is Apple Silicon, so this lane built **arm64** packages. The plan's first milestone target is Debian 13 **amd64**, and the authoritative release artifacts must come from CI on an amd64 runner. Nothing here should be read as an amd64 result. Three things are architecture-dependent and are recorded as such below: the resolved `vmoddir` (`aarch64-linux-gnu` rather than `x86_64-linux-gnu`), the jemalloc decision, and the architecture-specific hardening flags (`-mbranch-protection=standard` rather than `-fcf-protection=full`). Everything else — the ABI provider mechanism, the package split, the dependency metadata, the lint result, the smoke scenario — is architecture-independent and should reproduce on amd64.

## Packages produced

All in `dist/debian-13/` (gitignored), with a `SHA256SUMS` alongside.

| Package | Version | Arch | Bytes | SHA-256 |
| --- | --- | --- | --- | --- |
| `vinyl-cache` | `9.0.0~git20260613.a909548147-1` | arm64 | 1 083 728 | `6d6c5250e421bef6e5fc452dec984ad8b6c4228f80afef77d6bc0bb3dce1db36` |
| `vinyl-cache-dev` | `9.0.0~git20260613.a909548147-1` | arm64 | 136 468 | `45062e04d29a5adae6fc0fb34a3221a1e433d148f70e5fe16ede0e7ec3d96e49` |
| `vinyl-cache-dbgsym` | `9.0.0~git20260613.a909548147-1` | arm64 | 2 504 792 | `363864dc8ef49fe4e16c509c8b68a060a9be473fd915f4ea516187ac1a7be851` |
| `libvmod-cachetag` | `1.0.0-1` | arm64 | 93 740 | `1a7bcea972e34039dad2ba9e8f0934c588ca8ddf9eaa6a9bec90826ffd75e21f` |
| `libvmod-cachetag-dbgsym` | `1.0.0-1` | arm64 | 356 172 | `bec9a7c4e7986d866d1d43d5c534a44223860093101ad953164f68912250495d` |

Source packages: `vinyl-cache_9.0.0~git20260613.a909548147-1.dsc` (`c885118e…`) and `libvmod-cachetag_1.0.0-1.dsc` (`dad4e269…`), both `3.0 (quilt)` with no patches. Debug symbols come from native `dh_strip` dbgsym generation; no manual splitting.

## ABI metadata exactly as built

Read back out of the finished packages with `dpkg-deb -f`, not from the recipes:

```text
vinyl-cache
  Provides: vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3, vinyld-vrt (= 23.0)

vinyl-cache-dev
  Depends: vinyl-cache (= 9.0.0~git20260613.a909548147-1), pkgconf, python3

libvmod-cachetag
  Depends: libc6 (>= 2.38),
           vinyl-cache (>= 9.0.0~git20260613.a909548147),
           vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3,
           vinyld-vrt (= 23.0)
```

**VRT version found: `23.0`**, from `VRT_MAJOR_VERSION 23U` / `VRT_MINOR_VERSION 0U` in `include/vrt.h`, extracted from the built tree with `cpp` at `debian/rules` parse time. This matches the illustrative value in the plan.

Note the `vinyl-cache (>= 9.0.0~git20260613.a909548147)` relation in cachetag's `Depends`: that is **not** hand-written. It is `dh_shlibdeps` resolving the `libvinylapi.so.3` link, and it appears alongside — not instead of — the exact ABI relation. Two independent mechanisms therefore have to agree before the package installs.

## Audit of the vendored upstream Debian material

`upstream/pkg-vinyl-cache/debian/` (vendored at `27c91305023b4c4dae09f903644774fb9dbd8fcb`, unmodified) was read in full. The plan's assessment — "useful ABI-provider generation, but debhelper 9-era conventions" — holds. Specifics:

**What is genuinely worth keeping.** The ABI provider generation. Upstream computes `vinyld-abi-<hash>` and `vinyld-vrt` by running `cpp` over the *built* source tree and appending the results to `debian/substvars` in an `override_dh_gencontrol`. That is the right shape: the virtual packages a runtime advertises are derived from the binary it ships, so a recipe cannot advertise an ABI its binary does not have. Carried forward, with the changes below.

**What had to change.**

- **`debian/compat` said `11` while `debian/control` build-depended on `debhelper (>= 9)`** — two different compat statements in one source package. Replaced with `debhelper-compat (= 13)` in `Build-Depends` and the `compat` file dropped, which is the only supported spelling now.
- **`debhelper (>= 9.20160709) | dh-systemd`** — `dh-systemd` has not existed as a separate package since stretch. Dropped.
- **`Standards-Version: 3.9.6`** (from 2014). Raised to `4.7.2`.
- **`dh $@ --parallel`** — `--parallel` has been the default since compat 10 and is now an error. Dropped.
- **`--libdir=/usr/lib`** — pre-multiarch. Replaced with `/usr/lib/$(DEB_HOST_MULTIARCH)`. This is what makes the installed `vmoddir` `/usr/lib/<triplet>/vinyl-cache/vmods`, matching what `registry/README.md` already documents for `install.vmoddir`.
- **`Replaces:`/`Conflicts:` against `libvarnishapi1`, `varnish-dbg`, `varnish-doc`, `libvarnish-dev`** — Vinyl Cache is a rename of a Varnish derivative, but these packages do not conflict on any file path in Debian 13, and declaring a conflict with a distribution's own Varnish packages is a support claim we have not tested. Dropped; revisit if a real file conflict is ever demonstrated.
- **`Provides: libvinylapi3`** as a plain provide — dropped, because we do not offer an independently versioned library (see the lint triage).
- **`dh_strip --keep-debug -Xvinyl -Xlibvmod`** — this deliberately left the daemon and every VMOD unstripped inside the runtime package, which is why upstream also needed `unstripped-binary-or-object` lintian overrides. Replaced with plain `dh_strip`, which produces proper `-dbgsym` packages. The overrides became unnecessary and were deleted rather than carried over.
- **`echo … >> debian/substvars`** with `dh_gencontrol -- -T debian/substvars` — a *shared* substvars file applied to every binary package in a multi-binary source package. Changed to append to `debian/vinyl-cache.substvars`, the per-package file `dh_gencontrol` already uses, so the ABI provides cannot leak into `vinyl-cache-dev`.
- **`override_dh_installdocs` copying `doc/sphinx/build/html`** — a Varnish 4.0/4.1 transition workaround. Removed; `make` in this tree installs no HTML.
- **`debian/changelog` with a literal `@VERSION@` and the note "this changelog is not in use"**, plus a `package-deb` script that ran `dch` to invent a version from `date +%Y%m%d%H%M%S`. Replaced with a generated changelog carrying the pinned snapshot version, the source commit, the submodule commit and the ABI, and an explicit EXPERIMENTAL statement.
- **`debian/vinyl-cache.postinst`** — carried a `change_group_for_varnishlog_user` migration for upgrades from Varnish 4.1.0-2, `dpkg-statoverride` bookkeeping, and a `varnishlog`-era comment. Rewritten to create the three system accounts and nothing else. **`postrm` was destructive on plain `remove`**: it deleted `/var/lib/vinyl-cache` and `/var/log/vinyl-cache` on `remove`, `failed-upgrade`, `abort-install`, `abort-upgrade` and `disappear`, which would discard a cache working directory during a *failed upgrade*. Narrowed to `purge` only.
- **`debian/source/format: 1.0`** → `3.0 (quilt)`.
- **`Section: web` for the dev package** → `libdevel`.
- **`WITHOUT_JEMALLOC_ARCH_LIST`** — kept, and the `Build-Depends` architecture qualification on `libjemalloc-dev` was made to match it. Upstream declared an unconditional `libjemalloc-dev` build dependency while passing `--without-jemalloc` on some architectures, so the buildroot and the configure line disagreed. On this arm64 run jemalloc is therefore correctly absent from the buildroot *and* explicitly disabled, rather than being silently auto-detected.
- **`DEB_BUILD_MAINT_OPTIONS=optimize=-lto`** — an Ubuntu 21.04-era workaround. Debian 13 does not enable LTO by default, so it was dropped in favour of `hardening=+all`.

**What upstream had that we deliberately still do not have.** No `debian/watch` (there is no upstream release URL to watch — the source is a pinned commit). No SysV init scripts. No `vinyl-cache-doc` package. No Arch/Alpine lanes.

## Modernisation beyond the upstream material

- Added `Rules-Requires-Root: no`.
- `dh_missing` runs at its compat-13 default (`--fail-missing`) and is not weakened. It caught two genuinely unpackaged files on the first run.
- Added `Documentation=` keys to both systemd units.
- Removed `usr/bin/vinylstat_help_gen` from the runtime package. It is a build-time code generator that Vinyl's own `Makefile.am` installs into `$(bindir)`; nothing at runtime calls it and it has no manual page.
- The service units are shipped **neither enabled nor started**. This is a deliberate deviation from the usual Debian expectation, recorded in `debian/rules`: these are experimental snapshot packages with no security-update commitment and the unit binds `:6081` on all interfaces, so an unattended start of an internet-facing daemon is not an acceptable side effect of installing a learning artifact. Revisit when the channel gains a security owner and a published response target.

## How the source input is pinned, and the trap in it

Vinyl 9 has no upstream release tarball, so the orig tarball is derived from an immutable commit. `build.sh` reproduces, byte for byte, the assembly procedure already in `libvmod-cachetag/scripts/release-source-archive.sh`: `git archive` the superproject, `git archive` each submodule at the commit the superproject tree pins, splice, and repack with GNU tar and `--sort=name --owner=0 --group=0 --numeric-owner --mtime=@<commit epoch>`.

That reproduced the digest the cachetag release run had already recorded, independently:

```text
canonical Vinyl source archive sha256: 2587f03289b3e16d36b4b688def4b78fb5af07a9aacc620a55e094a5c0f6ee15
OK: matches the digest recorded by the cachetag release script
```

**The trap.** `include/vcs_version.h` and `include/vmod_abi.h` are generated by `include/generate.py` from a Git checkout and are `.gitignore`d, so `git archive` omits them. `generate.py` falls back to the literal string `NOGIT` when there is no `.git` directory, which would have produced

```text
#define VMOD_ABI_Version "Vinyl Cache trunk NOGIT"
```

and handed every strict-ABI VMOD in the cohort a meaningless ABI token that still *looks* like a working dependency. `assemble-source.sh` therefore generates both headers into the export — which is exactly what an upstream `make dist` tarball contains, and `include/Makefile.am` leaves them alone when they are present and `.git` is absent — deriving `PACKAGE_STRING` from the exported `configure.ac` rather than assuming it, then asserting the result:

```text
PACKAGE_STRING from configure.ac: [Vinyl Cache trunk]
generated VMOD_ABI_Version: [Vinyl Cache trunk a90954814766d933a75d4c808c449cb9bc0ae3d3]
OK: generated strict VMOD ABI string matches the pinned value
```

The generated headers were also diffed against the ones the sibling development checkout's own build produced: byte-identical.

The cachetag orig tarball is the canonical release archive verbatim, digest `c7054e69…`, verified before use. No cachetag Git checkout is consumed by the package build.

## Defects found in the cachetag packaging scaffolding, and their fixes

Three, all real, all minimal. Two are code fixes in `libvmod-cachetag/packaging/debian/`; one is a documentation correction.

### 1. `dh_autoreconf` cannot run — build-stopping

```text
aclocal: error: couldn't open directory '/aclocal': No such file or directory
autoreconf: error: aclocal failed with exit status: 1
dh_autoreconf: error: autoreconf -f -i returned exit code 1
```

`Makefile.am` line 1 declares

```make
ACLOCAL_AMFLAGS = -I m4 -I ${VINYLAPI_DATAROOTDIR}/aclocal
```

and `VINYLAPI_DATAROOTDIR` is a configure-time substitution read from the installed `vinylapi.pc`. `dh_autoreconf` is part of the default `dh` sequence from compat 10 onwards and runs `aclocal` **before** `configure`, where that variable is empty, so the flag degenerates to `-I /aclocal`.

**Fix:** `dh $@ --without autoreconf` in `packaging/debian/rules`, with a comment explaining both why it fails and why it should not run at all. The second reason is the important one: this package is built from the tagged release archive, which already contains `configure`, `aclocal.m4`, `Makefile.in` and `config.h.in` generated by the release build (all four verified present in `libvmod-cachetag-1.0.0.tar.gz`). Regenerating them with whatever autotools the buildroot happens to carry would replace the build system the release archive was tested with — precisely what "the generated distribution archive must be the only source input consumed by native package jobs" exists to prevent. `dh_update_autotools_config` still runs and still refreshes `config.guess` and `config.sub`, which is the portability requirement `autoreconf` is normally invoked to satisfy.

This is a packaging-side fix by necessity: correcting `ACLOCAL_AMFLAGS` would mean editing `Makefile.am`, which is out of scope for this work.

### 2. `extended-description-line-too-long` — lint warning

`debian/control`'s long description embedded `vinyld-abi-@VINYL_STRICT_ABI@` inline. After substitution the 40-hex hash pushed the line past 80 characters. Not detectable before a real substitution, which is why it survived inspection-only validation.

**Fix:** reflowed so the virtual package name sits alone on its own indented line inside the description.

### 3. Missing lintian overrides for two knowingly-inapplicable tags

`initial-upload-closes-no-bugs` and `debian-watch-file-is-missing` both fire and both are inapplicable: the package is not in Debian and has no bug tracker, and the source archive is pinned by digest in the cohort manifest rather than tracked by a watch URL. The plan's Phase 3 acceptance criterion requires every remaining tag to be "either fixed or justified by a checked-in override that explains itself"; there were no overrides at all.

**Fix:** added `packaging/debian/libvmod-cachetag.lintian-overrides` and `packaging/debian/source/lintian-overrides`, each with a written justification.

### Also: a documentation correction, not a code fix

`packaging/README.md`'s token table said `@VINYL_VMODDIR@` had **no manifest field** and that the manifests lived under this repository's `release/`. Both statements are now stale: the registry moved to `vinyl-packaging/registry/` and `cachetag-target/v1` records `install.vmoddir` plus `install.vmoddir_source`. Corrected in place, since that section explicitly says it "is the place to fix any drift between the two". The resolved value on this target confirms the per-target argument: `/usr/lib/aarch64-linux-gnu/vinyl-cache/vmods`.

### Reported, deliberately NOT fixed and NOT suppressed

```text
W: libvmod-cachetag: wrong-manual-section 3 != 4 [usr/share/man/man3/vmod_cachetag.3.gz:30]
```

`src/vmod_cachetag.vcc` declares `$Module cachetag 4`, so the generated manual page's `.TH` line says section 4, while `src/Makefile.am` installs it as `vmod_cachetag.3`. Every VMOD shipped with Vinyl Cache declares section 3 (`$Module std 3`, `$Module purge 3`, …). This is a one-character upstream fix in the VCC, and both files are out of scope for this work. It is left visible rather than overridden, and `packaging/debian/libvmod-cachetag.lintian-overrides` carries a comment saying so — an override here would hide a real inconsistency instead of fixing it.

**Action for the cachetag repository:** change `$Module cachetag 4` to `$Module cachetag 3`.

### A stale claim in `packaging/README.md` worth flagging to the EL9 lane

The "Known gaps" section states that `LICENSE` is absent from the release archive because `Makefile.am`'s `EXTRA_DIST` does not list it, and that "the spec's `%license LICENSE` will fail the build until `LICENSE` is added to `EXTRA_DIST`". That is **no longer true**: `libvmod-cachetag-1.0.0/LICENSE` is present in the canonical `libvmod-cachetag-1.0.0.tar.gz`. The Debian recipe does not use it (it has a DEP-5 `debian/copyright`), so it did not block this lane, but the EL9 agent should not treat that gap as a live blocker. Left unedited because it is outside the token table this work was scoped to correct.

## Defects found in my own Vinyl recipe

Recorded because a diagnostic log should show the failures too.

1. **The `#` in a makefile is not a `#`.** `debian/rules` filtered `cpp` output with `grep -v '^#'`. GNU make strips comments at *read* time, so the line was truncated mid-quote and `/bin/sh` reported `Syntax error: Unterminated quoted string` — twice, once per `$(shell)` — leaving both ABI variables silently empty. The upstream recipe's `num=\#` idiom exists for exactly this reason and I had only applied it to the `#include` lines. The build did not produce a wrong package: the format assertions in `override_dh_gencontrol` would have refused an empty ABI, and in the event `dh_missing` failed first. Fixed by routing the `grep` pattern through `$(num)` as well, with a comment.
2. **`dh_missing --fail-missing` caught two unpackaged files**, `usr/share/doc/vinyl-cache/{builtin,example}.vcl`. Fixed by shipping them, and the redundant `debian/vinyl-cache.examples` (which would have installed `etc/example.vcl` a second time under a different path) was removed.
3. **`dpkg-deb -c | grep -q` under `set -o pipefail`.** `grep -q` exits on first match, `dpkg-deb` dies of SIGPIPE, and the pipeline reports failure even though the file *was* found — reported as `E: libvmod_cachetag.so is not in /usr/lib/aarch64-linux-gnu/vinyl-cache/vmods` when it demonstrably was. This is the same trap documented in `libvmod-cachetag/docs/20260724_2033_note_step-6-build-profiles.md` for `readelf | grep -q`; it recurs whenever a large producer feeds an early-exiting consumer. Fixed by reading the listing into a variable and matching with `case`.

## Build configuration and effective flags

Buildroot: `debian:trixie@sha256:fac46bff2e02f51425b6e33b0e1169f55dfb053d83511ca28aa50c09fd5ed7a4`.

Vinyl configure line, as run by `dh_auto_configure`:

```text
--localstatedir=/var/lib --libdir=/usr/lib/aarch64-linux-gnu --with-unwind --without-jemalloc
```

plus debhelper's own defaults (`--prefix=/usr --sysconfdir=/etc --includedir=… --mandir=… --runstatedir=/run --disable-dependency-tracking …`).

`DEB_BUILD_MAINT_OPTIONS = hardening=+all`. On this target that adds exactly one flag over the Debian default — `-Wl,-z,now` — because PIE is a *builtin* of Debian's gcc rather than an emitted flag. Verified before committing to it, which also removed the libtool `-pie` hazard the step-6 note warns about: no `-pie` ever reaches `LDFLAGS`.

```text
CFLAGS=-g -O2 -Werror=implicit-function-declaration -ffile-prefix-map=<srcdir>=.
       -fstack-protector-strong -fstack-clash-protection -Wformat
       -Werror=format-security -mbranch-protection=standard
CPPFLAGS=-Wdate-time -D_FORTIFY_SOURCE=2
LDFLAGS=-Wl,-z,relro -Wl,-z,now
```

Two differences from the harness's production profile in step 6 are worth recording: Debian 13's `dpkg-buildflags` selects `_FORTIFY_SOURCE=2`, not `3`, and it adds `-Werror=implicit-function-declaration` and `-ffile-prefix-map`. Vinyl's own `configure.ac` then appends `-DZ_PREFIX -pthread -Wall -Werror -Wno-error=unused-result`. The `-Werror` compiled clean against gcc 14.2.0, which was not a given.

`--disable-stack-protector` is nowhere near this build, and — per the step-6 finding — its *absence* is not what produces the hardening. Vinyl's `configure.ac` only feeds the stack protector into `DEVELOPER_CFLAGS`, which reaches `CFLAGS` only when `--enable-developer-warnings` is also given, so the hardening comes entirely from `dpkg-buildflags`. Both stages therefore verify the built ELF objects rather than trusting the configure line:

```text
vinyld                        libvmod_cachetag.so
PASS  stack-protector         PASS  stack-protector
PASS  relro-segment           PASS  relro-segment
PASS  bind-now                PASS  bind-now
PASS  pie                     PASS  pic
PASS  fortify-source          PASS  fortify-source
```

`SOURCE_DATE_EPOCH` is set per source package: `1781307021` for Vinyl (its commit date) and `1784926281` for cachetag (from the release archive metadata).

## lintian triage

`lintian -i -I --pedantic` over both `.changes` files, run inside the buildroot. Nothing is filtered; the only suppressions are per-tag overrides with written justifications.

**Final state: the Vinyl `.changes` emits no tags at all. One warning remains on cachetag, deliberately.**

| Tag | Package | Verdict |
| --- | --- | --- |
| `wrong-manual-section 3 != 4` | libvmod-cachetag | **Left visible.** Real upstream inconsistency in `src/vmod_cachetag.vcc`; out of scope to fix here and wrong to hide. See above. |
| `extended-description-line-too-long` | libvmod-cachetag | **Fixed** in `packaging/debian/control`. |
| `debian-changelog-line-too-long` ×3 | vinyl-cache, vinyl-cache-dev | **Fixed**: changelog template rewrapped so the 40-hex hash sits on its own line. |
| `systemd-service-file-missing-documentation-key` ×2 | vinyl-cache | **Fixed**: `Documentation=` added to both units. |
| `no-manual-page [usr/bin/vinylstat_help_gen]` | vinyl-cache | **Fixed by not shipping it** — it is a build-time generator, not a runtime interface. |
| `alien-tag versioned-provides`, `alien-tag no-upstream-changelog` (**E**) | vinyl-cache | **Fixed.** My first override file named two tags that do not exist in lintian 2.121; lintian reports an unknown override as an *error*. Both removed. Worth knowing: a well-meaning override can turn a clean package into a failing one. |
| `duplicate-override-context` | vinyl-cache | **Fixed** — a consequence of the two alien tags. |
| `package-name-doesnt-match-sonames libvinylapi3` | vinyl-cache | **Overridden, justified.** `libvinylapi` is versioned in lockstep with the daemon and a strict-ABI VMOD is bound to one exact build; a `libvinylapi3` package would advertise an independent SONAME compatibility promise this project explicitly does not make. |
| `no-symbols-control-file` | vinyl-cache | **Overridden, justified.** Same reason: a symbols file states a per-symbol backwards-compatibility promise for a library whose consumers must be rebuilt for every Vinyl revision. |
| `embedded-library` (zlib) | vinyl-cache | **Overridden, justified.** `vinyltest`/`vtest` embed `lib/libvgz` because the VTC suite must exercise Vinyl's own gzip path, not the system library's. |
| `shared-library-lacks-prerequisites [libvmod_purge.so]` | vinyl-cache | **Overridden, justified.** VMODs are `-module` plugins dlopened by vinyld, which supplies the cache symbols. Same shape as every Varnish-family VMOD. |
| `no-manual-page [usr/bin/vtest]`, `[usr/sbin/vinylreload]` | vinyl-cache | **Overridden, justified.** Third-party harness binary and packaging helper script; neither has an upstream page. |
| `typo-in-manual-page` ×6 | vinyl-cache | **Overridden, justified.** Upstream typos in generated Vinyl pages; this snapshot carries no downstream patches. |
| `manual-page-for-system-command [usr/sbin/vinyld]` (P) | vinyl-cache | **Overridden, justified.** Upstream installs `vinyld.1`; a packaging-side rename to section 8 would make the page disagree with its own `.TH` and with every upstream cross-reference to `vinyld(1)`. |
| `initial-upload-closes-no-bugs` ×3 | all | **Overridden, justified.** Not in Debian, no bug tracker. |
| `debian-watch-file-is-missing` ×2 | both sources | **Overridden, justified.** No upstream watch URL; the archive is pinned by digest, which is a stronger check than a watch file. |

One operational note: lintian prints `running with root privileges is not recommended!`. It runs as root because the container has no unprivileged user; this affects lintian's own file-permission introspection, not the tags above. CI should run it as a normal user.

## Installed-package smoke test

Run in a **fresh** `debian:trixie` container that has seen neither build tree. A local apt repository is generated with `dpkg-scanpackages` from the produced `.deb` files and added as `deb [trusted=yes] file:/repo ./`, so real package-manager dependency resolution is exercised rather than `dpkg -i`.

**16 assertions, 16 passed, 0 failed.**

| Step | Result |
| --- | --- |
| 1. Install the matching Vinyl runtime | `vinyl-cache 9.0.0~git20260613.a909548147-1 arm64` installed by apt, pulling `gcc`, `libc6-dev`, `libedit2`, `libunwind8`, `adduser`. |
| 2. Install cachetag through the package manager | Installed by apt. `apt-cache showpkg vinyld-abi-a9095481…` shows `Reverse Depends: libvmod-cachetag` and `Reverse Provides: vinyl-cache 9.0.0~git20260613.a909548147-1`, i.e. the virtual package is what connects them. Negative control: installing `vinyld-abi-0000…0000` is unresolvable. |
| 3. `.so` in the runtime's VMOD directory | `/usr/lib/aarch64-linux-gnu/vinyl-cache/vmods/libvmod_cachetag.so`, owned by `libvmod-cachetag` per `dpkg -S`, sitting alongside the Vinyl-shipped VMODs. |
| 4. Compile VCL with `import cachetag` | `vinyld -C -f smoke.vcl` succeeds. |
| 5. Start with Default storage | `storage.s0 = default`, `storage.Transient = default`; VCL `boot` active and warm. |
| 6. Fetch and cache a tagged object | `X-Cache: MISS`, `X-Tag-Objects: 1`, `X-Tag-Edges: 2` (two tags: `article:123`, `section:news`). |
| 7. Warm hit | `X-Cache: HIT`, identical body `backend-response-2`. |
| 8. Purge the tag | `PURGE` with `Cache-Tag-Purge: article:123` → `X-Purge-Result: -1`, the documented success return of `purge_header()`. |
| 9. Old object gone, fresh response served | `backend-response-2` → `backend-response-3`, `X-Cache: MISS`. |
| 10. Stop cleanly | `SIGTERM`, exits within the timeout. |
| 11. Uninstall and verify cleanup | `.so` and manual page removed with the package; `vinyl-cache` still `install ok installed` and `/usr/sbin/vinyld` intact; `purge` leaves no cachetag package. |

The backend is a `python3 http.server` handler returning an incrementing body plus `Cache-Control: max-age=3600` and `Cache-Tag: article:123, section:news`, so a stale-versus-fresh response is distinguishable by content rather than only by a header.

## The scaffolding's build-time assertions

Both ran and both passed inside `dpkg-buildpackage`:

- `override_dh_auto_configure` compares `pkg-config --define-variable=libdir=… --variable=vmoddir vinylapi` against the manifest value. Log: `vmoddir: /usr/lib/aarch64-linux-gnu/vinyl-cache/vmods` / `expected vmoddir: /usr/lib/aarch64-linux-gnu/vinyl-cache/vmods`.
- `override_dh_install` reads `VMOD_ABI_Version` from the **installed** `vinyl-cache-dev` package's `vmod_abi.h` and compares its trailing field with `@VINYL_STRICT_ABI@`. It is a silent `@`-prefixed recipe, so success prints nothing; the installed header was dumped into the log immediately before (`#define VMOD_ABI_Version "Vinyl Cache trunk a90954814766d933a75d4c808c449cb9bc0ae3d3"`), and a mismatch would have aborted the build.
- `override_dh_auto_test` ran the self-contained WAL unit test: `PASS: cachetag_wal_test`, `# TOTAL: 1 / # PASS: 1`.

`packaging/check-tokens.sh --substituted` runs on the generated tree before every build and passed; no `@TOKEN@` reaches `dpkg-buildpackage`.

## Values for the registry target manifest

The registry was not edited, per the task boundary. These are the values a `debian-13-arm64` target manifest needs. Note the id: this lane is arm64, so it does **not** fill `debian-13-amd64`.

```yaml
target:
  id: debian-13-arm64
  distro: debian
  distro_release: "13"
  distro_id: debian-13
  arch: arm64
  package_format: deb
  dist_tag: ""
package:
  revision: 1
  source_name: libvmod-cachetag
  binary_name: libvmod-cachetag
vinyl_packages:
  runtime_package: vinyl-cache
  dev_package: vinyl-cache-dev
  runtime_version: 9.0.0~git20260613.a909548147-1
  dev_version: 9.0.0~git20260613.a909548147-1
build:
  profile: production
  image_ref: debian:trixie
  image_digest: sha256:fac46bff2e02f51425b6e33b0e1169f55dfb053d83511ca28aa50c09fd5ed7a4
  compiler: gcc 14.2.0-19
  source_date_epoch: "1784926281"
  hardening_check: pass
install:
  vmoddir: /usr/lib/aarch64-linux-gnu/vinyl-cache/vmods
  vmoddir_source: pkg-config
tests:
  package_lint: pass
  installed_package_smoke: pass
  full_behavior_suite: pending
  upgrade_transactions: pending
```

Cohort-level values, all verified by this run:

- `vinyl.version: 9.0.0` (packaged as the snapshot `9.0.0~git20260613.a909548147`; Vinyl's own `AC_INIT` still says `trunk`)
- `vinyl.git_commit: a90954814766d933a75d4c808c449cb9bc0ae3d3`
- `vinyl.source_sha256: 2587f03289b3e16d36b4b688def4b78fb5af07a9aacc620a55e094a5c0f6ee15`
- `vinyl.vrt: "23.0"`
- `vinyl.strict_abi: a90954814766d933a75d4c808c449cb9bc0ae3d3`
- `vinyl.patches: []`
- `cachetag.version: 1.0.0`, `cachetag.source_sha256: c7054e69219ff3c54501d9c68857f2117944c4658db4cb08e2821b09b27821a2`, `cachetag.git_commit: 0d3c9fdb9e39e65f86b6af9bc6935ca016cff7f8`
- vtest2 submodule (audit only, not a digest input): `db5ccb4a078da40b3ec1ca3c18bf498bb1520888`

Two things a manifest author must decide rather than copy:

- **The cohort input-id is not minted here.** Deriving it needs `build_profile.revision`, which is a policy choice, and this build is a local process proof rather than the run that pins the cohort. The changelogs therefore carry the literal `unassigned-local-process-proof` in place of `@COHORT_ID@`; that string must not appear in a published artifact.
- **`cachetag.git_commit` describes a dev-mode archive.** `libvmod-cachetag-1.0.0.metadata.json` records `"mode": "dev"`, `"worktree_dirty": true` and `release_stamp: dev-build-from 0d3c9fd… +dirty`. The tarball digest is pinned and verified, but it was not built from a clean tagged tree, so the cohort cannot be promoted past `candidate` on this input.

Effective flags, resolved build dependencies (190 packages for the cachetag buildroot, full list in `dist/debian-13/logs/*-buildroot-packages.txt`) and `dpkg-buildflags` output are captured in `dist/debian-13/logs/`.

## Reproducibility: an unplanned but clean result

The lane was finally run once more from scratch as a single `recipes/debian-13/build.sh` invocation — every stage, fresh containers, `work/` wiped first — to prove the script drives the whole thing rather than only working as a sequence of hand-driven stages. It exited 0 and the smoke passed 16/16 again.

Comparing that run's `SHA256SUMS` against the previous one:

- **all five `.deb` payloads are bit-identical**, as are both `.dsc`, both `.debian.tar.xz` and both `.orig.tar.gz`;
- only `.buildinfo` and `.changes` differ, which is correct and expected: those files record the buildroot's exact package list and build metadata rather than package content.

So `SOURCE_DATE_EPOCH`, the deterministic tarball assembly and `-ffile-prefix-map` are together enough to make the payloads reproducible across independent container runs. This is a *same-host, same-image, same-day* result and is not yet the full reproducibility claim the plan asks for, but it is a much better starting point than an untested assumption, and it means a future rebuild comparison has a known-good baseline to diff against.

## The maintainer identity is a deliberate blocker

Every artifact carries:

```text
Maintainer: Vinyl Cache Packaging (unreleased snapshot) <packaging@vinyl-cache.example>
```

`.example` is a reserved TLD, so the address is undeliverable by construction and cannot be mistaken for a real contact. A package carrying a maintainer address implies a security contact, and implementation step 2 of the plan — deciding who owns security triage — has not happened. **These packages must not be published until a real owner replaces this.** It is stated as a single `build.sh` variable so the substitution is one edit.

## Deferred

All of these are CI work, and none of them is blocked by anything in this lane.

- **amd64.** The plan's Tier 1 first target. This lane is arm64 because the host is Apple Silicon. The recipes are architecture-independent apart from the three items named at the top; `build.sh` resolves the architecture from the buildroot rather than assuming it.
- **`sbuild` / `pbuilder` clean-room builds.** This lane installs build dependencies into a fresh container with `apt-get build-dep ./`, which does resolve `Build-Depends` from `debian/control` — so an *undeclared* dependency that happens to be in the base image would not be caught. `sbuild`'s minimal buildroot is what closes that gap, and the plan requires it before publication.
- **Mismatch-fixture and upgrade transaction tests.** `apt upgrade`, `apt full-upgrade`, and direct installation of a deliberately incompatible Vinyl package, each from a retained previous cohort. This needs a second Vinyl build with a different ABI, which is a CI-scale job. The negative control in smoke step 2 proves only that an unsatisfiable `vinyld-abi-` is unresolvable, not what a resolver does to an *installed* cachetag during a Vinyl upgrade — and that is the dangerous case the plan singles out.
- **The full Default-storage behaviour suite against the production-hardened package build.** Complementary to, not replaced by, the installed-package smoke.
- **Reproducibility beyond a same-host rebuild.** See the result below — a second full run reproduced every package payload bit for bit, but on the same host, same image and same day. The remaining questions are a different host, a different date, and a different build path; `-ffile-prefix-map` is present in `CFLAGS`, so the path case is expected to hold.
- **`Multi-Arch: same` on the cachetag package.** Now decidable — the Vinyl packages do install VMODs under a multiarch libdir — but it needs the co-installability question thought through rather than asserted, and `vinyl-cache` itself is not `Multi-Arch: same`.
- **Ubuntu.** Not a rebuild of the Debian artifact; it needs its own lane.
- **`vinyl-cache-doc`.** Vinyl builds Sphinx HTML but nothing installs it. `python3-sphinx` is nevertheless a hard `configure` requirement, so it stays in `Build-Depends`.
- **SELinux.** Not applicable to Debian; it is the EL9 lane's gate.
