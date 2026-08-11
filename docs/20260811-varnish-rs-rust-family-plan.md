# Plan: package Varnish and add the varnish-rs VMOD family

Status: proposed implementation plan. This replaces the earlier Cargo-first plan. `DESIGN.md` and `SCOPE.md` remain normative and must be amended before the contracts or code change.

## Outcome

Make Varnish Cache a package engine alongside Vinyl Cache, then add the maintained varnish-rs VMODs `reqwest`, `fileserver`, `rers`, and `fcgi` to the compatibility and package lanes.

The four VMODs build with Cargo against Varnish 9.0.3, produce engine-coherent `.deb` and `.rpm` packages, and pass the same fresh-container install-and-load check as an autotools VMOD. Each enters a Varnish release only after its own native-target package cells pass. The current `varnish-sys` `varnishapi` probe remains an honest Vinyl incompatibility: Vinyl compat cells are red and Vinyl package cells do not expand.

This work generalises only the assumptions that prevent the existing package pipeline from serving a second engine family or build family. It keeps the current catalog, package gate, stable per-engine/per-target releases, exact engine dependency, generic recipe generator, build scripts, statuses, and matrix page.

The package quality bar remains deliberately basic: prove that each package has the expected identity, architecture, dependency, payload, installation path, and load behaviour. Do not add reproducible-build promises, distribution replacement policy, upgrade-transaction testing, signing, service integration, or other distro-grade machinery.

## Principles

1. **Engine family and build family are independent.** `vinyl` versus `varnish` selects package identity, development package, pkg-config API, daemon, and VMOD installation directory. `autotools` versus `cargo` selects how a VMOD source tree is built and installed. Do not combine these axes into names such as `varnish-cargo`.
2. **Reuse the current packaging pipeline.** Cargo does not need a second recipe generator, package lane, release workflow, result shape, or verification path. Only the recipe's build/install instructions vary.
3. **Package the pinned engine.** A Varnish VMOD package is built against this repository's pinned Varnish package, not whatever Varnish version happens to be in the base image. The runtime dependency remains exact, so the package and matrix cell describe the same ABI pairing.
4. **One pin, one home.** The Rust toolchain version lives once in `engines.yml`. Engine and VMOD refs remain in their existing catalog locations. Generated environment values are the only shell interface.
5. **No upstream content is carried.** No patches, shims, vendored crates, copied test suites, per-VMOD scripts, or Cargo registry snapshots. A committed upstream `Cargo.lock` is required; after a locked fetch, Cargo performs no further dependency resolution or download.
6. **Promotion follows proof.** New VMODs begin unpromoted. Local package mode remains available for proof. Promotion is per VMOD and may be limited with `package.targets`.
7. **Prove the contract, not reproducibility.** Compare generated recipes, package metadata, normalized file manifests, and install/load results. Byte-identical packages are not a goal.

## Part I: Varnish packaging design

### Package contract

Record one engine-family package contract in `tools/matrix.py`, alongside the existing family vocabulary. It is code-owned schema behaviour, not repeated catalog data:

| Contract value | Vinyl | Varnish |
|---|---|---|
| Runtime package | `vinyl-cache` | `varnish` |
| Development package | `vinyl-cache-dev` / `vinyl-cache-devel` | `varnish-dev` / `varnish-devel` |
| VMOD package prefix | `vinyl-vmod-` | `varnish-vmod-` |
| API pkg-config name | `vinylapi` | `varnishapi` |
| Daemon | `vinyld` | `varnishd` |
| VMOD directory family component | `vinyl-cache` | `varnish` |
| Debian VMOD revision suffix | `~vinyl<engine-version>` | `~varnish<engine-version>` |
| RPM VMOD release suffix | `.vinyl<engine-version>` | `.varnish<engine-version>` |

The build must obtain the effective VMOD directory from the installed development package with `pkg-config --variable=vmoddir <api>`. Templates may use the conventional directory for package file lists, but the package build and fresh-container check must assert that it agrees with pkg-config. This catches a packaging layout error instead of publishing a package that installs where the daemon does not search.

Package names intentionally distinguish this project's Varnish VMODs from distribution `libvmod-*` packages. The Varnish engine package uses the conventional `varnish` name. These are basic same-name packages, not a distro replacement policy: do not add `Conflicts`, `Replaces`, `Obsoletes`, upgrade semantics, or replacement tests without a demonstrated need. Release instructions continue to require `apt install` or `dnf install`, never raw `dpkg` or `rpm` installation.

### Varnish engine packages

Add minimal Varnish engine recipes parallel to the existing Vinyl engine recipes:

```text
packaging/engine/
  vinyl/
    debian/
    vinyl-cache.spec
  varnish/
    debian/
    varnish.spec
```

Move the current Vinyl files into the `vinyl/` directory without changing their rendered content. `build-engine.sh` selects the directory/spec by `ENGINE_FAMILY`. The runtime package name from the family contract also drives the Debian source/changelog name and the RPM source archive stem; those names must not remain as shell literals. Keep the small family-specific build-dependency lists in `build-engine.sh` rather than introducing another data model.

The Varnish recipes produce:

- Debian: `varnish` and `varnish-dev`;
- RPM: `varnish` and `varnish-devel`;
- the daemon, runtime libraries, bundled VMODs, headers, `varnishapi.pc`, VCL tooling, `vmodtool.py`, `vsctool.py`, and the files needed to compile and load third-party VMODs;
- the compiler and C development runtime dependencies needed when `varnishd` compiles VCL at run time;
- no systemd integration, signing, repository metadata, distro conffile policy, or other distribution-quality machinery.

Use the same package version contract as Vinyl: engine version plus repository release `1`. Configure conventional `/usr` paths and the target's native library directory. Preserve Varnish's own default state/configuration paths where the basic package needs them to run the existing compile-only load check; do not add a service lifecycle.

Before declaring the recipe complete, prove the exact installed file set from the pinned Varnish 9.0.3 tarball on one Debian and one EL10 target. Let that proof determine the Debian `.install` manifests and RPM `%files` lists. Do not copy a distribution recipe wholesale.

Before setting `varnish-9.0.3` to `packages: "true"`, run the fresh-container engine package check on every target already listed for that engine. Add each native EL10 target only after the same check passes there. `packages: "true"` becomes valid for any release engine family, while trunk engines remain non-package engines.

### VMOD recipe generalisation

Keep `tools/recipe.py` as the single recipe generator. Change its package-name, version, dependency, API, daemon, and VMOD-directory tokens to come from the selected engine-family contract.

Keep the common Debian templates unchanged except for replacing hard-coded Vinyl tokens:

- `control.in`;
- `changelog.in`;
- `copyright.in`;
- `source-format.in`.

The build protocol belongs in `debian/rules`. Maintain one small rules template per build family:

```text
packaging/debian/rules.autotools.in
packaging/debian/rules.cargo.in
```

For RPM, keep one common spec template and substitute build-family fragments for `%build` and `%install`:

```text
packaging/rpm/vmod.spec.in
packaging/rpm/build-autotools.inc
packaging/rpm/install-autotools.inc
packaging/rpm/build-cargo.inc
packaging/rpm/install-cargo.inc
```

`recipe.py` reads the selected fragments and supplies them through normal template tokens. If RPM macro expansion makes literal fragments less clear than two complete specs, use two specs instead; do not introduce a template language or conditional syntax. The deciding test is which form leaves less code and fewer escaping rules.

Existing autotools recipes must render equivalently apart from the intentional engine-derived tokens and file moves. Cargo and Varnish must not cause changes to a Vinyl/autotools package.

`build-vmod.sh` must also stop constructing its own VMOD package names. Use `VMOD_PACKAGE_NAME` for Debian collection and the RPM source directory/archive, and use exported family metadata to identify the engine runtime and development packages. This keeps recipe rendering, collection, and installation on the same naming contract.

### Package expansion, release, and installation

Keep the current package gates: engine `packages`, VMOD `package.families`, VMOD `package.promoted`, and VMOD `package.targets`. Removing the Vinyl-only validator rule allows a Varnish release engine, but it is not sufficient to enable one safely.

Before enabling Varnish packages, audit every currently promoted VMOD whose `package.families` is absent. Absence means both families, so each such VMOD must either pass package mode on every enabled Varnish target or declare `families: [vinyl]`. Assert the exact expected Varnish package rows in selftests so a future catalog edit cannot expand the release set accidentally.

The existing release expansion and all-or-nothing gate already group cells by `(engine, target)`. Retain that behaviour. A tag such as `varnish-9.0.3-debian-13-amd64` contains the matching Varnish engine packages and every promoted Varnish VMOD for that target, or it is not published. A failed Varnish pair must not block a green Vinyl pair, and vice versa.

Generalise `build-vmod.sh` package mode so it discovers the runtime and development engine package files through exported family metadata rather than filename literals. Its fresh container must:

1. install the matching engine runtime and development packages through apt or dnf;
2. install the newly built VMOD package;
3. verify the installed package architecture;
4. verify every declared module exists in the pkg-config VMOD directory;
5. compile a minimal VCL importing every declared module with the family daemon.

No new Varnish-specific workflow is needed. Harden the existing release workflow before Varnish publication:

- run stable publication only from `main`;
- serialize release publication with one non-cancelling concurrency group;
- before deleting or creating a release, require the runtime and development engine packages plus at least one correctly named package for every expected VMOD cell;
- treat a missing package artifact as a failed pair even when its result cell says `pass`.

### Varnish packaging implementation steps

1. Amend the normative documents first:
   - update `SCOPE.md` so basic packages may target Vinyl or Varnish and reconcile “red never blocks” with the release-only completeness gate;
   - add one “basic package quality bar” section to `DESIGN.md`: package identity, architecture, exact dependency, normalized payload, install, and load are required; reproducible binaries and distro-grade policy are not;
   - make release-engine packaging family-neutral and document the engine-family package contract;
   - generalise recipe, script, release, naming, version, and exact-dependency contracts;
   - replace decision 2, “Varnish is matrix-only”, with the dated Varnish packaging decision;
   - state that the release gate is per engine and target, not implicitly Vinyl-only;
   - update the Vinyl-only package description in `README.md`.
2. Add family-contract helpers and tests in `tools/matrix.py`:
   - runtime/development package names by format;
   - VMOD package name and version by engine;
   - API pkg-config name, daemon, and conventional VMOD directory;
   - engine source/changelog name and RPM source archive stem;
   - validation that `packages: "true"` requires a release engine but permits both families;
   - environment exports consumed by both build scripts.
3. Restructure the existing engine packaging files under `packaging/engine/vinyl/`. Before adding Varnish behaviour, compare the old and new rendered recipe text, package identities and dependencies, and normalized installed file manifests. Do not require byte-identical package archives.
4. Write the minimal Varnish Debian and RPM engine recipes from the pinned source tarball. Teach `build-engine.sh` to select the family recipe, source identity, archive stem, and small family dependency set.
5. Prove the Varnish engine recipe on one native Debian target and one native EL10 target: inspect package metadata and contents, install the runtime/development pair in a fresh container, and compile a VCL importing a bundled VMOD.
6. Generalise `tools/recipe.py`, the VMOD templates, and VMOD package collection to use the family contract. Render and inspect an existing autotools Varnish VMOD package before involving Cargo; this isolates engine-family defects from build-family defects.
7. Generalise `build-vmod.sh` engine-package discovery and fresh-container checks. Prove one Varnish/autotools VMOD package end to end on Debian and EL10.
8. Audit every currently promoted family-unrestricted VMOD. Prove it on every enabled Varnish target or restrict it to Vinyl, then assert the exact expanded Varnish package set in selftests.
9. Run the fresh-container engine package check on every existing Varnish target, add only proven EL10 targets, set `varnish-9.0.3` to `packages: "true"`, and verify simultaneous Vinyl and Varnish expansion/gating.
10. Harden `release.yml`: main-only publication, non-cancelling concurrency, and per-cell artifact completeness before stable release replacement. Add a missing-artifact regression test.
11. Run validation, generated-schema checks, selftests, and forced engine/package/install failures. Classified product failures remain result cells; only harness failures are `infra_failed`.

### Varnish packaging acceptance criteria

- Vinyl engine and autotools VMOD rendered recipes, package identities/dependencies, and normalized installed file manifests remain equivalent except for intentional repository file relocation.
- The pinned Varnish release produces matching runtime/development packages on every enabled native target; they install in a fresh container and compile a VCL importing a bundled VMOD.
- A Varnish/autotools VMOD package has an exact dependency on the matching Varnish runtime, installs in the `varnishapi` VMOD directory, and loads in a fresh container.
- `matrix.py expand --lane release --mode package` emits the asserted Vinyl and Varnish engine/VMOD sets using the existing gates.
- The release workflow publishes from `main`, serializes stable replacement, verifies every expected package artifact, and publishes complete releases independently per `(engine, target)` pair.
- No package is built against an unpinned distribution Varnish installation.
- No reproducible-build, distro replacement, service lifecycle, signing, or upgrade-transaction promise is introduced.

## Part II: varnish-rs Cargo VMOD design

### Catalog contract

Add an optional top-level VMOD field:

```yaml
build: cargo
```

Legal values are `autotools` and `cargo`; absence means `autotools`. This preserves every existing manifest and makes the build protocol independent of `engine_source`, which still means an additional engine source tree is required.

Add `cargo-test` as the second legal `tests` value. It means `cargo test --release --locked --offline` in compat mode after the load check, retried once as a whole suite under the same policy as `make-check`. Package recipes continue to run no upstream tests. `make-check` is legal only for autotools and `cargo-test` only for Cargo; reject mismatched combinations during catalog validation. Set `cargo-test` only after the upstream locked suite has passed reliably in the target container.

For `build: cargo`:

- a committed, current upstream `Cargo.lock` is mandatory;
- `package.modules` is mandatory and declares the exact VCL import set;
- `package.artifacts` is mandatory and lists the corresponding Cargo output basenames in the same order as `package.modules`;
- `engine_source` remains independently legal if a future Cargo project needs it;
- `package.build_deps` continues to hold only module-specific native dependencies.

`package.artifacts` is legal only for Cargo. Its values must be distinct basenames ending in `.so`, with no slash, and its length must equal `package.modules`. The ordered lists are the complete mapping:

```yaml
modules:
  - rers
artifacts:
  - libvmod_rs_template.so
```

For each pair, validate exactly one declared artifact in the top level of Cargo's release directory and install it under the conventional `libvmod_<module>.so` name. Reject a missing or duplicate declared artifact and any other top-level `.so`. This handles upstream filenames that do not match their VCL import names without adding per-VMOD code.

Add one repository-wide Rust toolchain pin to `engines.yml`:

```yaml
toolchains:
  rust:
    version: "<exact version>"
    bootstrap: rustup
```

Select the lowest currently supported exact stable toolchain that satisfies the verified MSRV of the pinned varnish-rs releases. Do not assume `1.90` from the earlier plan: confirm `rust-version`, lockfile dependency requirements, and upstream CI before changing the normative document. All targets use this pin; a target override is out of scope until a real target difference appears.

`matrix.py env` exports `VMOD_BUILD`, the ordered module/artifact lists, `RUST_VERSION`, and `RUST_BOOTSTRAP` when applicable. Missing Rust metadata for a Cargo build is a catalog validation error. Autotools environment output remains unchanged where practical.

### Cargo execution contract

All Rust state is confined to gitignored task scratch mounted at `/work`:

```text
RUSTUP_HOME=/work/rustup
CARGO_HOME=/work/cargo
CARGO_TARGET_DIR=/work/cargo-target/<vmod>-<engine>-<target>-<mode>
```

This state is host-backed scratch, but nothing is installed into the host toolchain or normal user directories.

Inside the target container, the shared Cargo setup performs:

1. install the target's compiler, `pkg-config`, clang/libclang for bindgen, CA certificates, curl, and shared native development dependencies;
2. bootstrap the exact configured Rust toolchain with the minimal rustup profile;
3. export `RUSTUP_TOOLCHAIN=$RUST_VERSION`, put its `cargo` and `rustc` first on `PATH`, and fail unless both report the configured toolchain;
4. reject a missing `Cargo.lock` and run `cargo metadata --locked --offline --no-deps` as a network-free preflight;
5. run `cargo fetch --locked` with network access;
6. run every subsequent Cargo command with `--locked --offline`.

The fetch is the only Cargo dependency-download phase. The container remains networked for the existing OS and harness operations; this contract does not claim that arbitrary upstream build scripts or tests are network-isolated. Do not use `cargo vendor`, copy a registry, install host tools, or silently regenerate a lockfile.

For compat mode:

```text
cargo fetch --locked
cargo build --release --locked --offline
```

Export the same engine prefix paths used by autotools: `PKG_CONFIG_PATH`, `LD_LIBRARY_PATH`, and `PATH`. The `varnish-sys` probe therefore sees the pinned engine API rather than a distribution package.

Discover declared shared objects only in the top level of the configured Cargo release directory. Validate them with the ordered `package.modules`/`package.artifacts` mapping and use the validated set for the existing minimal-VCL load check. Do not recursively inspect Cargo's `deps/` directory.

Classification stays mechanical:

- missing/stale lockfile, metadata preflight failure, Cargo compilation failure, or artifact mismatch: `build_failed` in compat mode;
- Cargo fetch or Rust bootstrap failure: `infra_failed` after one retry, because these are network/tooling transfer steps rather than source compilation;
- VCL import failure: `load_failed`;
- opted-in Cargo suite failure twice: `test_failed`;
- recipe/build failure in package mode: `package_failed`;
- fresh-container installation or import failure: `install_failed`;
- container, mount, catalog environment, or harness failure: `infra_failed`.

Keep classification step-based. Add explicit shared-classifier entries for Cargo preflight, fetch, build, artifacts, and tests; do not parse Cargo diagnostics to manufacture a configure phase. A `varnish-sys` pkg-config probe occurs during Cargo compilation and is therefore `build_failed`.

### Cargo packaging contract

Cargo uses the package pipeline designed in Part I.

The harness prepares rustup, Cargo state, and the locked dependency cache before invoking `dpkg-buildpackage` or `rpmbuild`. The generated package recipe inherits `PATH`, `RUSTUP_HOME`, `CARGO_HOME`, and `CARGO_TARGET_DIR`; it does not download rustup or dependencies.

The Cargo Debian rules and RPM fragments do only this:

1. run `cargo build --release --locked --offline`;
2. validate the ordered module/artifact mapping;
3. copy each declared artifact into the engine-family VMOD directory as `libvmod_<module>.so`;
4. run no upstream test suite;
5. leave automatic shared-library dependency generation enabled.

Use one checked-in helper in `tools/` for artifact validation/install if expressing the exact same rules safely in Make, shell, and RPM would duplicate them. It must be a generic Cargo artifact helper driven by catalog-generated arguments, not a per-VMOD script. If the few shell lines remain clearer duplicated in the two recipe formats, do not add the helper.

The generated source recipe is supported inside this repository's prepared container harness; it is not required to bootstrap its own pinned Rust distribution. This is consistent with the project's basic-package quality bar and must be stated in `DESIGN.md`.

### Initial catalog entries

Add:

- `vmods/reqwest.yml`;
- `vmods/fileserver.yml`;
- `vmods/rers.yml`;
- `vmods/fcgi.yml`.

For each entry, verify from upstream rather than copying the earlier proposal:

- canonical repository and homepage;
- maintained release ref, version, and head branch;
- committed lockfile at both the release ref and head;
- varnish/varnish-sys version and effective Rust MSRV;
- SPDX licence;
- VCL import name and produced shared-object name;
- required native libraries on Debian/Ubuntu and EL10;
- whether the locked upstream test suite is suitable for `cargo-test`.

Set:

```yaml
build: cargo
package:
  families:
    - varnish
```

Declare `package.modules` and the matching `package.artifacts` explicitly and leave `promoted` absent. Keep the existing shared recursive-submodule checkout rather than adding a new catalog switch. Do not add `cargo-test`, extra native build dependencies, or target restrictions without container evidence.

### Cargo implementation steps

1. Amend `DESIGN.md` with the build-family field, global toolchain pin, compatible build/test pairs, ordered module/artifact mapping, Cargo execution contract, lockfile rule, package-recipe boundary, step-based statuses, and dated decision. Update the repository layout for the selected rules/fragments.
2. Extend the catalog parser, `KEYS`, validators, schemas, schema generator, and `env` output for `toolchains`, `build`, `cargo-test`, and `package.artifacts`.
3. Add selftests for:
   - absence means autotools;
   - invalid build/test/toolchain values;
   - invalid build/test combinations;
   - Cargo requires global Rust metadata plus equal-length `package.modules` and `package.artifacts` lists with safe artifact basenames;
   - environment output contains the exact pin and ordered artifact mapping;
   - Cargo preflight/build/artifact failures map to `build_failed`, fetch/bootstrap failures to `infra_failed`, and Cargo suite failures to `test_failed`;
   - release/trunk and package expansion remain governed by the existing gates;
   - generated schema drift.
4. Refactor `build-vmod.sh` after common checkout and engine setup into two build functions: autotools and Cargo. Keep result emission, engine setup, package collection, architecture checks, submodule checkout, and load verification shared; do not add sourced build fragments unless the two functions become unwieldy.
5. Implement Cargo compat mode and prove one VMOD against the packaged/prefix Varnish 9.0.3 engine on one native Debian target. Force and inspect preflight, fetch, compilation, artifact, and load failures and confirm their result statuses and process exit codes.
6. Add the Cargo Debian rules and RPM fragments to the already-generalised recipe generator. Assert rendered Cargo recipes contain locked/offline commands, contain no autotools bootstrap/configure commands, and preserve the engine-derived exact dependency.
7. Prove the same VMOD's package mode end to end on one native Debian target and one native EL10 target: locked fetch, offline recipe build, expected package contents, correct architecture and dependencies, fresh install, and import.
8. Add the remaining three entries one at a time and run compat plus local package mode for every enabled native Varnish target. Fix only shared harness defects or catalog dependency declarations; upstream incompatibilities remain classified red cells.
9. Run every Cargo VMOD against Vinyl 9.0.1 and both engine trunks. The current `varnishapi`-only probe must yield normal red `build_failed` Vinyl cells, never `infra_failed`.
10. Promote each VMOD only on the targets where its Varnish package cell passed. Use `package.targets` for a demonstrated target limitation; do not batch-promote the family.
11. Run `python3 tools/matrix.py validate`, `schema`, `schema --check`, and `selftest`, then dispatch the matrix and release workflows. Confirm a Varnish release contains the engine pair plus the complete promoted Rust VMOD set or publishes nothing.

### Cargo and varnish-rs acceptance criteria

- Existing Vinyl/autotools catalogs, recipes, packages, and selftests behave unchanged when `build` is absent.
- The exact Rust toolchain version has one machine-readable home, is forced with `RUSTUP_TOOLCHAIN`, and is asserted inside every target container.
- Every Cargo source has a usable committed lockfile; Cargo resolution is locked and Cargo performs no dependency download after fetch.
- Every declared VCL import maps to one explicit Cargo artifact, which is installed under the conventional module filename; missing, duplicate, or undeclared top-level VMOD artifacts fail the cell.
- Each Rust VMOD builds and loads against pinned Varnish 9.0.3 in compat mode on every enabled native target.
- Each promoted Rust VMOD produces an engine-coherent Varnish `.deb` or `.rpm`, installs into the `varnishapi` VMOD directory, and imports in a fresh container.
- Varnish releases use the existing all-or-nothing per-engine/per-target gate and stable replaceable tags.
- Vinyl incompatibility appears as useful red compat cells without a shim, patch, skipped cell, or failed build job.
- No new workflow, evidence ledger, vendored source, per-VMOD script, or independent Cargo packaging subsystem is introduced.
