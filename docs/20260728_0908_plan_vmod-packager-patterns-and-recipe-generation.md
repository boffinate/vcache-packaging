# Plan: adapt vmod-packager patterns into production VMOD recipe generation

Date: 2026-07-28

Status: Proposed

Related:

- [VMOD matrix failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md)
- [Varnish Cache downstream VMOD packaging plan](20260726_0824_plan_varnish-downstream-vmod-packaging.md)
- [Third-party VMOD compatibility survey](20260726_1858_plan_vmod-survey.md)
- [First survey report](20260726_2014_report_vmod-survey-first-sweep.md)
- [`SCOPE.md`](../SCOPE.md)
- [`xcir/vmod-packager`](https://github.com/xcir/vmod-packager), inspected at commit [`252e0b0871eb9f7d6848ad92811288c821ca8cff`](https://github.com/xcir/vmod-packager/commit/252e0b0871eb9f7d6848ad92811288c821ca8cff)

## Decision

Do not replace `vcache-packaging` with `vmod-packager` and do not make `vmod-packager` an external release-build dependency.

Adopt its strongest architectural pattern: most conventional VMODs can share a generated native-package recipe and a small adapter contract, while exceptional VMODs receive explicit, reviewed overrides or a custom build adapter.

`vcache-packaging` will own production-grade generic Debian and RPM recipe templates for selected VMODs that do not provide suitable upstream packaging. It will also own the reviewed per-VMOD adapter data needed to use those templates. Upstream-owned packaging remains usable when it exists, is tied to the selected release source, and satisfies this project's dependency, provenance, hardening, payload, and testing requirements.

Recipe generation is only one stage of `vcache-packaging`. The existing registry, exact source and engine identity, clean-room buildroots, ABI dependency generation, hardening and lint checks, installed-package behavior tests, package-manager transaction tests, failure isolation, evidence collection, and all-or-nothing release gate remain necessary.

## Correct interpretation of vmod-packager

`vmod-packager` is primarily a Varnish Cache VMOD packaging tool. Its generic RPM dependency generation names `varnish` because that is its normal target, not because the Varnish path is defective. Vinyl support is a newer adaptation layered onto the Varnish-oriented design.

Read the Vinyl changes as useful evidence about the kinds of adaptation a VMOD packager needs: engine naming, VMOD directory naming, engine source selection, recipe naming, and occasional source compatibility work. Do not assume that every generic Varnish recipe, dependency expression, versioning choice, or source transformation is therefore a finished Vinyl packaging policy.

The project descriptions also differ. `vmod-packager` deliberately creates packages for use in the operator's own environment. `vcache-packaging` creates selected packages with recorded compatibility claims and release evidence. The latter requires a stronger trust and verification boundary.

## Why this plan is needed

Most third-party VMOD repositories do not contain maintained Debian and RPM recipes. Requiring every selected VMOD to gain upstream packaging before this project can package it would make the selected package set dependent on unrelated upstream repository decisions and would duplicate essentially the same recipe work across many repositories.

The current rule that cachetag keeps its own recipes remains valid for cachetag because that repository is controlled with the package release. It should not become a universal prerequisite for third-party VMODs.

The survey reinforces the need for a layered generator:

- 36 surveyed VMODs built and loaded against both Varnish 9 and Vinyl 9, so a common conventional path should cover a meaningful set;
- some VMODs need additional distribution packages or different bootstrap/configure commands;
- Rust and other non-Autotools VMODs need a custom build path;
- ten surveyed VMODs require a daemon source tree rather than only the installed development package, which is a supportability distinction rather than merely another configure flag;
- many old VMODs fail for genuine API or maintenance reasons and must not be made publishable merely because a generic recipe can be emitted.

## What to learn and adopt

### 1. A default adapter plus explicit escape hatches

Provide a default `autotools` adapter for the common lifecycle:

1. unpack the verified VMOD release source;
2. install declared build dependencies in the clean buildroot;
3. use the installed engine development package to obtain include, pkg-config, tool, ABI, and VMOD-directory data;
4. run the selected bootstrap/configure path;
5. build and run the safe build-time test subset;
6. stage through `DESTDIR`;
7. select the permitted package payload;
8. generate the Debian or RPM source package;
9. rebuild it in the authoritative native buildroot.

Add narrow adapter capabilities only when a selected VMOD proves they are needed:

- extra build and runtime dependencies per target family;
- bootstrap command selection such as `autogen.sh`, `bootstrap`, or no regeneration for a release archive;
- configure arguments;
- safe build-time test selection;
- payload additions for manuals, VCC files, helper data, licenses, and documentation;
- a reviewed patch stack;
- a custom build adapter for a different build system such as Cargo.

Do not expose arbitrary unreviewed shell embedded in the VMOD manifest. Custom adapter scripts are checked-in code, reviewed like package recipes, named explicitly by the selected VMOD entry, and executed only inside the build container.

### 2. A common lifecycle across native package backends

Keep one normalized VMOD build description, then render native Debian and RPM recipes from it. Share facts, not hand-written native dependency strings:

- upstream name and version;
- source ref, commit, archive name, and digest;
- package summary and description;
- license expression and installed license files;
- build system and adapter revision;
- selected engine and ABI mode;
- intended payload;
- build-time test policy.

The backend still owns native policy. Debian and RPM dependency expressions, source-package structure, debug packages, file ownership, macros, and lint rules are not forced through a lowest-common-denominator template.

### 3. Per-VMOD configuration without modifying upstream

Allow a selected third-party VMOD to be packaged without adding files to its upstream repository.

Keep durable configuration in `vcache-packaging`, associated with the checked-in selected VMOD entry. If an upstream repository later accepts suitable packaging or a small packaging configuration directory, switching to it is an explicit source and recipe-provenance change, not automatic discovery.

Do not automatically execute `vmp_config` or similarly named files merely because an upstream checkout contains them. Upstream build files are source input; project-specific packaging policy and hooks remain explicitly selected inputs.

### 4. A catalogue of known build peculiarities

Use `vmod-packager`'s sample adapters and the existing survey results as research inputs when onboarding a VMOD. They already identify useful cases:

- additional library dependencies such as mhash, zlib, liburing, xxhash, and OpenSSL;
- alternate bootstrap/configure commands;
- daemon-source-tree requirements;
- custom Rust builds;
- payloads that contain more than the VMOD shared object.

Translate each relevant fact into the selected VMOD's reviewed adapter and prove it again in the project's target buildroots. Do not copy dependency lists uncritically across distribution releases or Varnish/Vinyl engines.

### 5. Multiple VMODs over shared engine inputs

Retain the useful separation between engine preparation and repeated VMOD builds. This aligns with the failure-isolation plan's shared engine artifacts.

The production package lane differs from `vmod-packager`: the VMOD build consumes installed runtime and development packages from the exact engine row, not an arbitrary installed source prefix. Source-harness lanes may use an engine source tree because compatibility exploration is their purpose, but they produce no publishable native package.

## What not to adopt

### Live and mutable input resolution

Do not use moving container tags, live branch pulls, unverified `curl | tar`, unpinned package installation, or an unrecorded current date as release inputs.

Every publishable row records and verifies:

- VMOD ref, peeled commit, source archive digest, and source date;
- generator revision and template digest;
- adapter revision and patch digests;
- engine package versions, artifact digests, ABI identity, and origin;
- target buildroot image or repository snapshot identity.

Trunk lanes may resolve a moving branch by design, but record the resolved commit and remain non-publishable.

### Broad source rewriting

Do not adopt a blanket Varnish-to-Vinyl textual substitution over the source tree.

When a selected VMOD needs compatibility changes, use a minimal reviewed patch stack. Record every patch digest and test the patched source against both intended and control lanes where applicable. A source transformation that changes build scripts, documentation, identifiers, or code accidentally is not acceptable evidence.

### Convenience package metadata

Do not publish generated recipes with placeholder maintainers, generic homepage or source URLs, unresolved or non-machine-readable licenses, outdated policy declarations, disabled debug packages, broad payload globs, or ignored dependency-analysis failures.

The generator must require the metadata needed for the selected package and fail closed when it is unavailable.

### Engine-version ranges as ABI policy

Do not infer VMOD compatibility from an engine version range alone.

For Varnish downstream lanes, generate the dependency expression appropriate to the exact upstream or distribution package source and the VMOD's `$ABI vrt` or `$ABI strict` declaration, as specified by the Varnish downstream plan.

For Vinyl cohort lanes, retain the exact VRT, strict ABI, and cohort dependency rules already measured by the transaction suites. A generated recipe must not weaken them.

### Direct container builds as release evidence

Do not treat `debuild` or `rpmbuild` in a general-purpose Docker image as the authoritative clean-room result.

The generator creates a native source package input. Debian packages are rebuilt through the documented pbuilder/sbuild-equivalent lane and EL packages through Mock. The project's Docker/OrbStack harness remains authoritative for local integration and the documented CI harness remains authoritative for release evidence.

### Build success as a support claim

A generated package that compiles is not yet selected, compatible, or publishable.

It must still pass payload checks, ABI inspection, hardening, lint, install/load smoke, selected behavior tests, and the applicable package-manager transaction tests. Source-tree-only VMODs remain survey or source-harness results until the selected engine's installed development package exposes a sufficient supported build surface.

## Proposed ownership and layout

Use the existing selected VMOD manifest as the entry point and add only the smallest adapter identity required by the second real VMOD:

```yaml
id: example
adapter: autotools
```

Do not add speculative manifest fields before a selected VMOD requires them. When fields are proven, keep source, lane, and publication policy in `registry/vmods/<id>.yml`, and keep package-generation implementation under a recipe tree such as:

```text
recipes/vmods/
  templates/
    debian/
    rpm/
  adapters/
    autotools/
    custom/
  overlays/
    <vmod-id>/
      adapter metadata
      reviewed patches
      custom scripts, only when required
```

Add a standard-library generator such as `tools/vmod_recipe.py`. It may run on the host because it only validates inputs and renders text; it must not compile, install, or inspect a built package on the host.

Generated native recipe trees live in a per-row work directory. They do not have to be committed, but the resulting Debian source package or source RPM must contain the exact generated recipe, and the result evidence must record its digest plus the generator, template, adapter, and manifest revisions.

## Generator contract

For a selected VMOD, engine row, and target, the generator must:

1. load the trusted local VMOD manifest, adapter, engine metadata, and target metadata;
2. reject missing source identity, license, maintainer, package description, payload policy, adapter revision, or ABI dependency input;
3. render the native source recipe deterministically;
4. derive changelog dates from recorded source or recipe epochs, never wall-clock time;
5. refuse unresolved template tokens;
6. emit a machine-readable generation record containing every input and output digest;
7. emit the expected binary and source package names without assuming the build succeeded;
8. leave compilation and native package generation to the target buildroot.

The same normalized inputs must produce byte-identical recipe output.

## Relationship to upstream packaging

Support two explicit strategies:

- `upstream`: use the selected release archive's own native recipe after auditing it and applying only recorded target substitutions;
- `generated`: render the project's native recipe from the generic template and selected adapter.

Never silently prefer newly discovered upstream packaging over the recorded strategy. That would change package contents and recipe provenance without a manifest decision.

Cachetag remains on its audited upstream-owned recipe initially. Do not rewrite it through the generic generator merely to exercise the abstraction. Use its package metadata and assertions as a policy reference and regression oracle.

## Role of vmod-packager

Use `vmod-packager` in three bounded roles:

1. **Reference implementation:** study the adapter lifecycle, distro dispatch, VMOD peculiarities, and Varnish-oriented defaults at a recorded commit.
2. **Onboarding research:** for a selected VMOD, compare its known hook and dependency requirements with the survey logs and upstream build documentation.
3. **Optional non-authoritative feasibility experiment:** a pinned containerized run may help determine whether the upstream source has a conventional build path, but its output is not release evidence and is never published by this project.

Prefer a small independent implementation of the required concepts over vendoring or forking the monolithic build driver. If non-trivial source is copied, retain the BSD-2-Clause copyright and license notice and record the imported commit and modifications.

Do not clone or execute an unpinned `vmod-packager` main branch during release CI.

## Implementation sequence

### Phase 0: record the design boundary

1. Add this plan and record the inspected `vmod-packager` commit and license.
2. Update `README.md` and `AGENTS.md` so upstream-owned recipes are an option, not a prerequisite for every selected third-party VMOD.
3. Cross-reference this recipe-generation plan from the VMOD matrix and Varnish downstream packaging plans.
4. Document that Varnish is `vmod-packager`'s primary target and that its Vinyl support is reference material, avoiding incorrect defect reports about intentional Varnish defaults.

### Phase 1: extract a normalized package model

1. Inventory the facts already generated for cachetag Debian and RPM recipes.
2. Define the smallest normalized model that can express those policy facts without attempting to regenerate cachetag.
3. Add deterministic rendering self-tests with small fixtures and unresolved-token failure cases.
4. Add attribution records for any `vmod-packager` source that is actually reused.

This phase renders text only and performs no package build.

### Phase 2: prove the default adapter on the second selected VMOD

1. Obtain the explicit maintainer decision and `SCOPE.md` update selecting the second VMOD.
2. Prefer a surveyed, actively maintained VMOD that passes the intended engine lane and uses a conventional installed-development-package build.
3. Record its release source, license, ABI mode, build system, dependencies, payload, and behavior smoke.
4. Compare upstream documentation, survey logs, and relevant `vmod-packager` adapter knowledge.
5. Generate Debian 13 and EL9 source recipes with the default adapter.
6. Build them through the documented Docker/OrbStack-backed pbuilder and Mock lanes.
7. Run package metadata, payload, hardening, lint, installed-package smoke, behavior, and transaction checks.
8. Record generator, template, adapter, source, engine, source-package, binary-package, and test-result digests.

Do not generalize further if this VMOD needs a custom adapter. Select a conventional proving VMOD first so the default path is tested independently of the escape hatch.

### Phase 3: prove one controlled exception

1. Select another in-scope VMOD that needs exactly one additional capability, such as an extra dependency, alternate bootstrap command, or small reviewed patch.
2. Add only that capability to the adapter contract.
3. Confirm the first generated VMOD's recipe output is unchanged.
4. Run the same authoritative package verification.

### Phase 4: prove a non-Autotools adapter only when selected

1. Add a custom adapter for Cargo, CMake, Meson, or another build system only after an explicitly selected VMOD requires it.
2. Keep the native recipe policy shared while isolating build-system commands and payload staging.
3. Require the same source, ABI, hardening, lint, smoke, behavior, transaction, and evidence gates.

### Phase 5: integrate with the isolated VMOD matrix

1. Make each VMOD reusable-workflow invocation select `upstream` or `generated` recipe strategy from trusted local data.
2. Include generator and adapter identity in stable artifact metadata and result records.
3. Keep VMOD adapter failures inside that VMOD's failure boundary.
4. Make missing generated recipes or generation records explicit classified failures.
5. Require release completeness across every selected generated and upstream-recipe VMOD.

## Verification and failure injection

The generator and workflow are not complete until these cases have been demonstrated:

1. A conventional VMOD with no upstream Debian or RPM files produces valid native source packages and verified binary packages on both selected targets.
2. Repeating generation from identical inputs produces byte-identical recipe trees and generation records.
3. A missing license, source digest, adapter, ABI identity, or payload declaration fails before native package build.
4. An unresolved template token fails generation.
5. An undeclared build dependency fails in the clean native buildroot rather than being hidden by a broad convenience image.
6. A package staged into the wrong VMOD directory fails before publication.
7. An unexpected installed file fails the payload allowlist.
8. An incompatible engine package cannot satisfy the generated ABI or cohort dependency.
9. A missing or failed generated-recipe result is reported by the global collector without cancelling other VMODs.
10. A reviewed patch changes the source and evidence digests and cannot be omitted or replaced silently.
11. An upstream-recipe VMOD and a generated-recipe VMOD coexist in the same release completeness gate.
12. A source-harness-only VMOD cannot accidentally produce a publishable package result.

## Acceptance criteria

The work is complete when:

- a selected conventional VMOD can be packaged without adding Debian or RPM files to its upstream repository;
- the default adapter is proven by authoritative Debian 13 and EL9 clean-room builds;
- generated recipes contain production maintainer, source, license, dependency, payload, debug, and hardening policy;
- native ABI dependencies are generated from the exact selected engine row rather than a broad version assumption;
- generator, template, adapter, patch, source, source-package, and binary-package identities are recorded in result evidence;
- generation is deterministic and rejects missing or unresolved inputs;
- upstream-owned and generated recipe strategies are explicit and cannot switch through discovery;
- custom behavior is reviewed checked-in code and remains scoped to the VMOD that requires it;
- build, lint, smoke, behavior, transaction, collection, and release-completeness gates remain identical in strength regardless of recipe strategy;
- no `vmod-packager` output is published directly as project release evidence;
- any reused `vmod-packager` code retains the BSD-2-Clause attribution required by its license.

## Non-goals

- Reimplement every option or distribution supported by `vmod-packager`.
- Add Arch, Ubuntu, CentOS Stream, old Varnish branches, or any other target not selected in `SCOPE.md`.
- Package every surveyed VMOD.
- Make a failing or abandoned VMOD publishable by papering over source incompatibility.
- Require third-party VMOD maintainers to accept this project's native packaging.
- Execute arbitrary packaging hooks discovered in upstream source.
- Build publishable VMOD packages against an unpackaged engine prefix.
- Replace Varnish or Vinyl ABI policy with a single cross-engine dependency rule.
- Vendor or fork all of `vmod-packager`.
- Treat a convenience Docker build as a substitute for pbuilder, Mock, installed-package tests, or transaction testing.
