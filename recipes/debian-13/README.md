# Debian 13 (trixie) lane

Debian source-package recipes for **Vinyl Cache 9** plus the driver that builds the whole coordinated cohort — Vinyl runtime, Vinyl development package, debug symbols, and the `libvmod-cachetag` VMOD package — lints it, and runs the installed-package smoke test from the [binary packaging and distribution plan](../../../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md).

Only the *Vinyl* recipes live here. The cachetag recipes stay in `libvmod-cachetag/packaging/debian/`, because the plan requires each VMOD release to record the exact recipes used for its own artifacts. `build.sh` reads that template tree, substitutes its tokens, and builds it against the Vinyl packages produced in the same run.

## Layout

```text
build.sh              the whole lane; runs on the host, builds only in containers
mismatch-fixture.sh   the synthetic mismatched Vinyl candidate fixtures
transactions.sh       the upgrade-transaction matrix, one container per scenario
vinyl/debian/         the Vinyl Cache 9 Debian source package, debhelper compat 13
container/
  assemble-source.sh  canonical pinned Vinyl source archive and both orig tarballs
  stage-vinyl.sh      dpkg-buildpackage for Vinyl, plus ABI and hardening assertions
  stage-cachetag.sh   dpkg-buildpackage for cachetag against the INSTALLED dev package
  stage-lint.sh       lintian over every produced package
  stage-smoke.sh      the plan's 11-step installed-package scenario, fresh container
  make-mismatch.sh    repack a baseline deb into a synthetic candidate fixture
  stage-transactions.sh  one upgrade-transaction scenario, in one fresh container
```

Artifacts and logs go to `../../dist/debian-13/`, which is entirely gitignored.

## Running it

```sh
recipes/debian-13/build.sh              # all stages
recipes/debian-13/build.sh source       # just assemble the tarballs
recipes/debian-13/build.sh vinyl        # just the Vinyl packages
recipes/debian-13/build.sh cachetag lint smoke
```

Every stage runs in a fresh `debian:trixie` container pinned by digest. The host is used only to read the pinned Git checkouts with `git archive`, to substitute recipe tokens, and to move files. Nothing is installed on the host.

## Upgrade-transaction safety

`build.sh` proves the cohort installs. It does not prove what a package manager does when an *incompatible* Vinyl update appears in the repository — which is the question the plan's "Upgrade transaction safety" section asks, and the one that decides whether an upgrade can silently delete an imported VMOD.

```sh
recipes/debian-13/mismatch-fixture.sh   # synthetic candidates, retained with digests
recipes/debian-13/transactions.sh       # the whole matrix
recipes/debian-13/transactions.sh --list
recipes/debian-13/transactions.sh s04   # one scenario, or a prefix
recipes/debian-13/transactions.sh --summary
```

`mismatch-fixture.sh` mints two synthetic candidate package pairs from the retained baseline cohort by a scripted control-metadata transformation: a `mismatch` variant with a different `vinyld-abi-<hash>`, and a `sameabi` variant with the baseline's hash but a different version and payload. Both carry a fixture cohort id of their own, distinct from the baseline's, so `vinyld-cohort-<id>` moves in both. Both are versioned above the baseline, both are real installable debs, and both are retained under `dist/debian-13/mismatch/` with `SHA256SUMS` and a `PROVENANCE` manifest, as the plan requires.

`transactions.sh` then installs the baseline cohort through a local apt repository, publishes one candidate into it, and runs one transaction command — in a throwaway container per scenario, so an outcome cannot contaminate the next. It records the resolver outcome, whether the VMOD survived, and whether `vinyld` can still compile a VCL that imports it.

Results and the reasoning are in [`docs/20260724_2300_note_step-9-debian-13-transactions.md`](../../docs/20260724_2300_note_step-9-debian-13-transactions.md). The short version: `apt upgrade` and `apt-get upgrade` are safe; `apt full-upgrade`, `apt-get dist-upgrade` and a direct `apt install vinyl-cache=<version>` all remove `libvmod-cachetag`, and apt's confirmation prompt defaults to yes.

The matrix was re-run whole on 2026-07-25 after the cohort-qualified provide landed. The three `sameabi` scenarios changed and nothing else did: s12 now holds the candidate back instead of upgrading it, and s13/s14 now propose removing the VMOD instead of upgrading silently — the same trade the mismatched-ABI rows already showed. See [`docs/20260725_1725_note_step-10-cohort-provide.md`](../../docs/20260725_1725_note_step-10-cohort-provide.md).

## How the source input is pinned

Vinyl Cache 9 has no upstream release tarball, so the orig tarball is *derived*, deterministically, from an immutable commit:

1. `git archive` the pinned superproject commit, and each submodule at the commit the superproject tree pins it to (`bin/vinyltest/vtest2`; `configure.ac` hard-fails without it).
2. Splice the pieces and repack them with GNU tar, `--sort=name --owner=0 --group=0 --numeric-owner --mtime=@<commit epoch>`. This is byte-for-byte the same procedure as `libvmod-cachetag/scripts/release-source-archive.sh`, so the resulting `vinyl-source-<commit>.tar` has the digest that release run already recorded, and `build.sh` asserts it.
3. Generate `include/vcs_version.h` and `include/vmod_abi.h` into the export.

Step 3 is not optional and is easy to get wrong. Both headers are generated by `include/generate.py` from a Git checkout and are `.gitignore`d, so a bare `git archive` omits them — and `include/generate.py` falls back to the literal string `NOGIT` when there is no `.git`, which would bake `Vinyl Cache trunk NOGIT` into `VMOD_ABI_Version` and hand every strict-ABI VMOD a meaningless ABI token. Shipping the headers in the tarball is exactly what an upstream `make dist` release would do, and `include/Makefile.am` leaves them alone when they are present and `.git` is absent. `assemble-source.sh` derives `PACKAGE_STRING` from the exported `configure.ac` rather than assuming it, and then asserts the resulting ABI string against the pinned value.

## ABI provider generation

The runtime package advertises three virtual packages. The first two are **generated from the built tree** rather than written into the recipe; the third is an input of the build:

```text
Provides: vinyld-abi-<40 hex>, vinyld-vrt (= <major>.<minor>), vinyld-cohort-<cohort-id>
```

`debian/rules` reads `VMOD_ABI_Version` out of `include/vmod_abi.h` and `VRT_MAJOR_VERSION`/`VRT_MINOR_VERSION` out of `include/vrt.h`, with `cpp`, at make parse time, and refuses to continue unless the ABI is 40 lowercase hex characters and the VRT is `<major>.<minor>`. `build.sh` then re-reads the *built package's* metadata with `dpkg-deb -f` and asserts it matches the pinned cohort inputs. A recipe cannot advertise an ABI its binary does not have.

`vinyld-cohort-<cohort-id>` arrives as the `@COHORT_ID@` token that `build.sh` substitutes into the whole `vinyl/debian` tree, and `override_dh_gencontrol` refuses to emit it unless the substituted value is non-empty and matches `^[a-z0-9][a-z0-9+.-]+$` — the charset a Debian package name allows. A cohort id cannot be read out of the Vinyl sources, because a cohort is a set of packages built together rather than a property of one source tree.

It exists because the ABI provide is a hash of the upstream commit and therefore cannot answer the provenance question. Transaction scenarios s12 to s14 in the step-9 matrix showed a candidate carrying the baseline's `vinyld-abi-<hash>` from a different build upgrading cleanly through `apt upgrade`, `apt full-upgrade` and an explicit versioned install, with nothing to object to. With the cohort provide in the dependency set, those same three scenarios hold the candidate back.

`vinyl-cache-dev` depends on `vinyl-cache (= ${binary:Version})` — the exact matching runtime, not a version range.

## Hardening

`DEB_BUILD_MAINT_OPTIONS = hardening=+all`, so the build gets the distribution's normal policy plus `BIND_NOW`. Nothing here disables the stack protector, and that is a deliberate, load-bearing omission: Vinyl's `configure.ac` only feeds `-fstack-protector` into `DEVELOPER_CFLAGS`, which reaches `CFLAGS` **only** when `--enable-developer-warnings` is also passed, so a "production" configuration written the obvious way gets no stack protector at all while `configure` cheerfully reports it as enabled. See `libvmod-cachetag/docs/20260724_2033_note_step-6-build-profiles.md`. The hardening here therefore comes entirely from `dpkg-buildflags`, and both `stage-vinyl.sh` and `stage-cachetag.sh` verify the result by inspecting the built ELF objects, which is the only check neither `configure` nor a toolchain default can fool.

## Relationship to the vendored upstream material

`vinyl/debian/` is derived from `upstream/pkg-vinyl-cache/debian/` (vendored at commit `27c91305023b4c4dae09f903644774fb9dbd8fcb`, read-only). The audit findings and the list of what was modernised are in `../../docs/archive/20260724_2231_note_step-7-8-debian-13-lane.md`.

## Deferred

- `sbuild`/`pbuilder` clean-room builds. This lane installs build dependencies into a fresh container instead, which resolves `Build-Depends` from `debian/control` via `apt-get build-dep ./` but does not enforce a minimal buildroot the way `sbuild` does.
- amd64. The authoritative release artifacts are amd64 and come from CI; this lane is arm64 because the development host is Apple Silicon.
- Mismatch-fixture and apt upgrade/full-upgrade transaction tests. They need a second, deliberately incompatible Vinyl cohort, which is CI work.
- Reproducibility across a *different* host, date and build path. Two independent runs on this host already produced bit-identical `.deb`, `.dsc`, `.debian.tar.xz` and `.orig.tar.gz` payloads (only `.buildinfo` and `.changes` differ, as they should).
