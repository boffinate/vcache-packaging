# Note: Step 4, failure isolation Phase 2 — the engine split

Date: 2026-07-28

Status: Implemented on branch `step4-engine-split`, not pushed, not audited, and not yet proven live

Implements [Phase 2 of the VMOD matrix failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md) — its six numbered items and the "Shared engine package artifacts" section in full — which is step 4 of [the outstanding-work roadmap](20260728_0916_roadmap_outstanding-packaging-work.md). Companion to [the Phase 1 note](20260728_1704_note_step-3-failure-isolation-phase-1.md) and [its live-proof report](20260728_1856_report_step-3-failure-isolation-live-proof.md).

The live proof and the package-equivalence comparison are deliberately **not** done here, for the same reason Phase 1 deferred them: they need CI runs, and this branch has not been pushed. What they need is described at the end.

## What changed

| Piece | File |
| --- | --- |
| Engine rows, engine matrix, artifact metadata, `blocked_by_engine_artifact` | `tools/ci_matrix.py` |
| Its tests, including three engine-blocking fixtures and a metadata-rejection battery | `tools/ci_matrix_selftest.py` |
| The one reader of the lane pin files both sides of the identity comparison use | `scripts/ci/engine-identity.sh` (new) |
| Engine discovery, the engine matrix, and the artifact upload | `.github/workflows/ci.yml` |
| Engine artifact download, verification, installation; no engine build in a VMOD row | `.github/workflows/vmod-package.yml` |
| Scope selectors on the lane scripts | `recipes/debian-13/build.sh`, `recipes/debian-13/container/assemble-source.sh`, `recipes/el9/build.sh`, `scripts/ci/debian13/{debian-lane,container-pbuilder,assert-packages}.sh`, `scripts/ci/el9/{mock-build,container-mock}.sh` |
| Schema and command documentation | `registry/README.md`, `AGENTS.md` |

`nightly-transactions.yml`, `release-draft.yml` and `trunk-vmod-ci.yml` are untouched, as the roadmap requires. Every lane-script scope defaults to `all`, which is exactly the pre-split behaviour, so those three workflows keep running the coherent whole-cohort lane and their `CACHETAG_*` pins stay guarded by the existing selftest. They migrate in Phase 4.

## The graph

Before this change every VMOD package row built Vinyl and then cachetag inside itself, four times per run for one VMOD, and 4×N times for N VMODs:

```text
structural-validation -> discover-vmods -> vmods (reusable workflow)
                                              plan -> source -> target x4 -> summary
                                                                 |
                                                        each row builds Vinyl, then cachetag
```

After:

```text
structural-validation --+-> discover-vmods -----------------------------+
                        |                                               |
                        +-> discover-engines                            |
                                 |                                      |
                          engine x4 (fail-fast: false) -----------------+--> vmods (fail-fast: false)
                          builds Vinyl once per (engine, target)             plan -> source
                          uploads engine-<engine>-<target>                     -> target x4 -> summary
                                                                                     |
                                                                    downloads, verifies and installs
                                                                    its engine artifact; builds only the VMOD
                                 |                                      |
                                 +------------> collect, if: !cancelled() <-----+
```

`vmods` lists `engine` in `needs` but starts under `if: ${{ !cancelled() && needs.discover-vmods.result == 'success' }}`, so a failed engine row cannot skip a VMOD invocation. Each row decides from its own requested artifact whether it can proceed. Waiting for all engine rows before starting the VMOD workflows is accepted, as the plan permits; optimizing start latency is explicitly a later decision.

## Engine rows are derived, not listed

There is no engine manifest. `ci_matrix.py engine_rows` takes the union of `(engine, target)` pairs across every selected `package` lane of every VMOD manifest that parses, so two VMODs naming the same engine and target share one build and one artifact — which is the entire point of the split. A row is required when any of its consumers is required.

`discover-engines` does parse the VMOD manifests, which `discover-vmods` deliberately does not. That is not a reintroduced global gate: `valid_manifests` drops a manifest that fails to parse or validate, so a malformed entry costs its own invocation row and whatever engine rows only it asked for. Everything another VMOD asked for is unaffected, and a selftest asserts it.

`vinyl-trunk-head` is marked `builds_packages: false` and never produces an engine row. It is the moving source the trunk harness uses; giving it a package artifact would be exactly the "a trunk build becomes a package because it passed" mistake the plan forbids.

## Artifact naming and the metadata schema

Artifacts are addressed from the stable logical row key alone, so a consumer computes the name it needs without knowing anything the engine job resolved at run time and without reading an aggregate matrix job's outputs:

```text
engine-vinyl-trunk-pinned-debian-13-amd64          the engine packages
result-engine-vinyl-trunk-pinned-debian-13-amd64   its result record and logs
```

The engine artifact contains:

```text
engine-metadata.json     engine-artifact/v1
engine-identity.env      scripts/ci/engine-identity.sh output, verbatim
packages/                the Vinyl runtime, development and debug packages
```

`engine-metadata.json`:

| Field | Notes |
| --- | --- |
| `schema` | `engine-artifact/v1` |
| `engine`, `target`, `family`, `vinyl_track` | the row this artifact was built for |
| `artifact`, `row_key` | recomputed and compared by the consumer, so a renamed artifact cannot pass |
| `identity` | every `key=value` line `engine-identity.sh` printed |
| `packages[]` | `name`, `bytes`, `sha256` per file |
| `packages_sha256` | one digest over the sorted name/digest pairs |

`identity` carries the resolved engine version, commit, ABI identity and build-input identity the plan asks for: `cohort_id`, `vinyl_track`, `vinyl_source_kind`, `vinyl_git_commit`, `vinyl_strict_abi`, `vinyl_abi_string`, `vinyl_upstream_version`, `vinyl_package_version`, `vinyl_source_sha256`, `vinyl_source_date_epoch`, `vinyl_source_url`, `build_image`, `maintainer`, plus `vinyl_vrt_expected` and `buildroot_snapshot` on Debian.

**The two families are not symmetric, and the asymmetry matters.** The `rpm` branch emits neither `vinyl_vrt_expected` nor `buildroot_snapshot`, because `recipes/el9/cohort.env` defines neither: EL9 has no equivalent of `VINYL_VRT_EXPECTED` (the value is read back out of the mock-installed development package at build time, not asserted against a pin) and no equivalent of the `snapshot.debian.org` pin (Mock resolves from AlmaLinux's live mirrors, which is why that lane uses record-and-audit instead). The consequence is precise: **on EL9 the identity comparison does not pin the VRT expectation.** An engine artifact whose development package advertised a different VRT would pass verification, and the first thing that would notice is the `vinyld(vrt)` requirement on the built cachetag RPM. That makes `vinyld(vrt)` the sharpest single check in the live equivalence comparison, and it is the reason the audit put the EL9 ABI requirements first. Adding a VRT pin to `cohort.env` purely to feed this comparison would be inventing a pin to check itself against; the honest fix, if one is wanted later, is to record the VRT the engine job actually read back and compare *that*, which is a change to the engine job rather than to the pin file.

Build logs deliberately do **not** travel inside the engine artifact. A dependency artifact should deliver packages and metadata, not another row's logs; the logs go into the engine row's result artifact, which is uploaded `if: always()` and therefore survives a failure the engine artifact would not.

## How consumers verify, and why the check is load-bearing

Producer and consumer both run `scripts/ci/engine-identity.sh <deb|rpm>`, which sources the lane's own pin file for the row's `VINYL_TRACK` and prints `key=value` lines. The producer embeds that output; the consumer regenerates it from its own checkout and `ci_matrix.py verify-engine-metadata` requires the two to be equal, key for key, in both directions.

This discharges the deferred **D5** item from the Step 3 audit — "artifact metadata is not verified by consumers" — and it is the reason D5 could be deferred to here rather than fixed there. A VMOD source archive has a second, independent identity check: the target row checks the tag out again and asserts the peeled commit, so nothing unverified reaches a package even without reading the archive's metadata. A VMOD package row does not build the engine and has no equivalent, so for the engine artifact this comparison is the only check there is, and a mismatch fails the row.

The verifier rejects: a foreign schema; an artifact built for a different engine or target; a renamed artifact or row key; a `family` or `vinyl_track` that disagrees with the tables; any identity key present on one side and not the other, or differing; an empty required identity key; a `packages_sha256` that does not match the recorded list; a package recorded but not delivered; a package whose bytes or size moved; and an engine package delivered but never recorded. Round-tripped on the host: verifying a release-track artifact against trunk-track pins fails on eleven keys, including cohort, ABI, version and source digest.

Keeping the key list in the shell script rather than in a Python table is deliberate. A pin that gains or loses a name cannot then be silently dropped from the comparison by a table in the other language that nobody updated; `ci_matrix.py` compares whatever keys it is handed, and insists that `cohort_id`, `vinyl_track`, `vinyl_strict_abi` and `vinyl_package_version` are present and non-empty so the comparison cannot pass vacuously against two empty dictionaries. A test asserts the script emits each of those four and covers both pin files.

**Also verified for the VMOD source artifact?** No. D5's other half — target rows reading back `libvmod-cachetag-*.metadata.json` and comparing it with their own row's `ref`, `expected_commit` and `version` — is not done. The target row's independent tag-and-commit re-verification still stands, so nothing unverified reaches a package, but the plan's rule is "consumers verify the metadata", and for the source archive that is still satisfied by a different mechanism rather than by the stated one. Recorded here as an open item rather than quietly closed; see "What remains".

## The lane-script refactor, and why bytes cannot move

Every script gained a scope selector that **only skips work**. No command line changed, no flag was added or removed, no environment variable that reaches a build changed value, and no ordering that affects package bytes moved. Each scope reaches the byte-producing commands with identical arguments to the combined run.

| File | Change | Why packages cannot differ |
| --- | --- | --- |
| `recipes/debian-13/build.sh` | `_source_scope` (`all`/`engine`/`vmod`); `stage_source_engine`, `stage_source_vmod`; the Vinyl and cachetag input blocks factored into `_source_vinyl_input` and `_source_cachetag_input`; `substitute_recipes` factored into `substitute_vinyl_recipe` and `substitute_cachetag_recipe` | The two substitution halves never read the other's inputs: the Vinyl tree is substituted from `pins.env` and `target.txt`, the cachetag tree from `pins.env`, `target.txt` and the cachetag checkout's own template. The `_subst` calls, their token lists and their values are character-for-character unchanged. The two `DEB_HOST_ARCH`/`DEB_HOST_MULTIARCH` assignments that `substitute_recipes` made and never used are gone; the recipe trees carry no `@DEB_HOST_ARCH@` token (`debian/rules` derives it from `dpkg-architecture` inside the chroot), so nothing read them. Architecture resolution and `target.txt` still run in every scope. |
| `recipes/debian-13/container/assemble-source.sh` | `ASSEMBLE_SCOPE` guards the Vinyl derivation and each unpack block | Every `tar`, `gzip`, generated header and `touch -d` is inside a guard that either runs it with the same arguments or does not run it. Unknown scope is a hard error rather than a silent `all`. |
| `scripts/ci/debian13/debian-lane.sh` | optional scope argument, passed into the container as `PBUILDER_SCOPE` beside the existing `VINYL_TRACK` | A pass-through. Default `all`. |
| `scripts/ci/debian13/container-pbuilder.sh` | `PBUILDER_SCOPE` guards the Vinyl `build_one` and the cachetag half | `build_one` is one function called with the same four arguments and the same two extra pbuilder flags in every scope. The local repository the cachetag build resolves `vinyl-cache-dev` from is assembled by the **same** `cp` glob from the **same** `/out` directory whether this run built those `.deb` files or downloaded them, so pbuilder cannot tell the difference. The buildroot is the same pinned mmdebstrap tarball, unpacked and destroyed per package in all three scopes as before. The "Vinyl packages exist" assertions now also guard the `vmod` scope, where they mean "the engine artifact was delivered". |
| `scripts/ci/debian13/assert-packages.sh` | optional scope argument; `engine` stops after the Vinyl half | Reads packages and asserts; produces nothing. The VMOD row still runs the default `all` scope, so its evidence does not shrink — the engine assertions simply also run earlier, on the engine row. |
| `scripts/ci/el9/mock-build.sh` | optional scope argument; each source directory required only by the scope that reads it; the `deps source` container run is engine work and is skipped in `vmod`; `MOCK_SCOPE` passed into the privileged container | `deps` installs tools in the **outer** container; every RPM is built inside Mock's chroot, so skipping it cannot change a package. The unused source directory is stubbed out exactly as tarball-mode release runs have always stubbed `/vinyl-src`, keeping the container layout identical across scopes. |
| `scripts/ci/el9/container-mock.sh` | `MOCK_SCOPE` guards the Vinyl spec generation and build, and the cachetag half; `epoch_defines` hoisted out of the Vinyl branch so both builds still share it; `$srcdir` added to the existing `mkdir -p`; the local-repository source directory selected by scope | Every `mock` invocation is reached with identical arguments; the derived per-package configs, both epoch macros and both `SOURCE_DATE_EPOCH` exports are unchanged. `mock --init` still runs once before any build. The cachetag `--rebuild` never inherited anything from the Vinyl build even in `all` scope: as the script's own `--addrepo` comment records (measured, run 30167536066), every `mock --rebuild` begins with a chroot init that restores the root cache and discards whatever the preceding builds and `--install` left, which is why `--addrepo` exists at all. In `vmod` scope the repository is built from `/out/packages`, which in `all` scope holds exactly the same files the `$resultdir/vinyl` path copies there moments earlier. A new non-empty assertion turns "the engine artifact was not delivered" into a named failure instead of a `dnf builddep` error. |
| `recipes/el9/build.sh` | the Vinyl-checkout guard now applies only when there are container stages to run | A precondition check on a directory that `--smoke-only` and `--vtc-suite-only` never mount — they mount only `/recipes` and `/out`. Requiring it was always spurious; since the split it is also wrong, because a VMOD row has no Vinyl source to point at. Written as a nested `if` rather than an `&&` chain, which under `set -e` would have exited on the ordinary path. |

Two structural facts make the whole thing work without touching anything else. First, `substitute_cachetag_recipe` is the only part of the Debian source stage that reads the Vinyl **package** identity, and it reads it from `pins.env` (`VINYL_PACKAGE_VERSION`, `VINYL_STRICT_ABI`, `VINYL_VRT_EXPECTED`), never from the built engine — so the cachetag build tree is a function of the pins alone and is identical whether the engine was built here or elsewhere. Second, the engine packages are **installed into the lane's own output directory** by the consumer, so everything downstream of the build — the local repository, `assert-packages.sh`, lintian over `*.changes`, the installed-package smoke and VTC suite, `stage_report`'s `SHA256SUMS`, and `build.sh sums` — sees exactly the file set it saw before, in the same place.

Two consequences worth stating plainly, because they are visible in artifacts:

- **The VMOD row's `packages-*` artifact still contains the engine packages**, because they are installed into `dist/`. The equivalence comparison and `release-draft.yml`'s expectations are unaffected.
- **The engine build logs moved out of the VMOD row's `packages-*` artifact** into the engine row's `result-engine-*` artifact. That is a change in where evidence lives, not in what evidence exists. The Debian engine `.dsc`, `.changes`, `.buildinfo` and orig tarball do still travel with the packages, so `SHA256SUMS` is unchanged.
- **The standalone `build.sh subst` stage no longer assigns `DEB_HOST_ARCH` and `DEB_HOST_MULTIARCH`.** They were assigned from `target.txt` and never read: no recipe carries a `@DEB_HOST_ARCH@` token, and `debian/rules` derives both from `dpkg-architecture` inside the chroot. Only `VINYL_VMODDIR` is read, and both split halves still read it. Dead variables, but `subst` is a documented stage, so this is a behaviour delta in a documented interface and is recorded rather than left to be rediscovered.

Two further pieces of dead-but-deliberate surface, recorded so a later reader does not mistake either for an oversight:

- **`assert-packages.sh`'s `vmod` scope is never invoked.** CI's VMOD row runs the default `all` scope precisely so its evidence does not shrink, and no other caller passes `vmod`. It exists so the scope vocabulary is the same three words in every script that has one; the alternative is a script whose argument means something different from its neighbours'.
- **The `nightly`/`trunk` tiers still expand to rows no job consumes**, unchanged from Phase 1 and retired by Phase 4.

## Missing-engine classification

`blocked_by_engine_artifact` is produced from both directions.

**By the row itself.** A target row's engine download and its VMOD source download run independently, both `continue-on-error`, so a missing engine artifact cannot hide a broken VMOD source. The classification chain then puts them in a fixed order: the row's own source failure first (`blocked_by_vmod_source`), then a missing engine artifact, then a metadata mismatch, then an installation failure (`failed_infrastructure`). The row writes its classified record and fails explicitly; the `continue-on-error` never leaves it green.

**By the collector.** `reconcile` now resolves an engine-status map alongside the source-status map. A package row with no record of its own whose engine row is not `passed` is `blocked_by_engine_artifact`, naming the engine row key and its status — including when the engine row itself produced no record at all, which is what a suppressed artifact looks like from the collector's side.

The precedence — VMOD source before engine — is the same on both sides, deliberately. Where both causes apply the row reports its own VMOD source failure, which is specific to that row; the engine failure is not lost, because it is reported in full on the engine row, which is where a shared root cause belongs. A selftest asserts both halves of that.

Engine rows are in the ledger, so a required engine row that never reported is `missing_result_record` and reddens the run on its own account. The per-VMOD summary job downloads `result-engine-*` as well as its own records, so a blocked row in that summary names the engine row rather than reporting it as missing evidence; `summarize-vmod` never synthesizes an engine record and never writes one into the VMOD's merged result, because engine rows belong to the whole run and the caller's collector owns their verdict.

## Injection hooks

Two new values, both inert by default and both reachable only from `workflow_dispatch`, both acting on `vinyl-trunk-pinned` / `debian-13-amd64`:

| Value | Where it acts | Expected |
| --- | --- | --- |
| `engine_build` | an `exit 1` step in the engine job, before the build | that engine row `failed_engine_build`; its one consumer row `blocked_by_engine_artifact`; the other three engine rows and their three consumer rows pass |
| `suppress_engine_artifact` | the engine artifact upload is skipped; the row is otherwise unchanged | that engine row **`passed`**; its one consumer row `blocked_by_engine_artifact`; everything else passes |

`suppress_engine_artifact` is the purer of the two: the producing row is green and only the consumers that name it go red, which isolates the consumer-side classification from any question of whether the engine build works.

Which row is injected is computed by `ci_matrix.py` (`INJECT_ENGINE_ROW`) and delivered as a matrix field, not written as a workflow expression that could drift from the constant. The workflow condition reads `matrix.inject_build` and `matrix.suppress_artifact`; a selftest asserts each injection marks exactly one row and leaves the other three alone. **No build script is modified by either injection**, which is the property that keeps item 7-style live runs package-neutral.

Applying the Phase 1 lesson about injections that change what the ledger *should* contain: neither of the two new ones does. They change what a row *did*, so they need to reach only the engine job. `--inject` is threaded into `engine-matrix` in `discover-engines` so the marking is computed once, from the same constant the tests use.

**The pre-existing `manifest` injection, however, gained a fourth ledger builder, and the first draft of this branch missed it.** Phase 1's note recorded the rule — an injection that changes what the ledger *should* contain must reach every builder of it — and listed three builders: the `plan` job, the per-VMOD `summary` job, and ci.yml's `collect`. Deriving engine rows from the package lanes made `discover-engines` a fourth: it decides how many engine rows the run is supposed to have. Without the corruption applied there, `-f inject=manifest` would have derived four engine rows from a manifest every other job treats as unparseable, and the run would have ended with four stray `passed` engine records that no expected row claims — the same shape of wrong answer the Phase 1 audit caught, in a new place. With the step in place, `engine-matrix` sees no valid lane, emits `count=0`, the engine job's existing `!= '0'` guard skips it, and the summary collapses to the single `failed_manifest_validation` row the case exists to demonstrate. Verified on the host against a corrupted catalog copy.

The general rule now reads: **adding a job that computes what the run should contain means adding it to every such injection.** There are four.

## Deviations from the plan, and why

**`TARGETS` and `ENGINES` stayed in `ci_matrix.py`.** The Step 3 audit suggested moving them into the registry now that engine rows are first-class. They record workflow shape — a runner label, a job timeout, a package family, and the `VINYL_TRACK` value a pin file dispatches on — and none of it is a compatibility claim, recorded evidence, or a resolved build input. `registry/targets/<cohort>/<target>.yml` already means something else entirely: the per-cohort compatibility evidence for a built cachetag package. A second, unrelated "target" concept in the same tree would be actively misleading, and the resolved build inputs the registry would legitimately want are already reaching the ledger — through the engine artifact metadata, read out of the lane pin files. The tables carry a comment saying when to revisit: if a target ever gains inputs of its own that the registry must record. `ENGINES` did gain `builds_packages`, which is the one genuinely new fact.

**Engine rows are derived from the lanes rather than enumerated.** The plan says "the selected engine inputs provide the expected engine-row ledger". Enumerating `ENGINES × TARGETS` would build engines nothing consumes and would be exactly the blind Cartesian product the non-goals forbid. Deriving from the selected package lanes gives the same four rows today and stays correct when a second VMOD picks a different subset.

**Only selected engine rows appear in the ledger.** Lane rows carry `selected: false` and report `not_selected`; engine rows do not, because an unselected engine row would be a row nothing asked for rather than a lane a tier declined to run.

**`--vmod ""` for engine records.** `make_record` rejects a non-empty `--vmod` on an engine row: an engine row belongs to no VMOD, and the summary groups it apart under "Shared engine packages" rather than inventing an owner.

**The `vmod` scope of `assert-packages.sh` is unused.** It exists for symmetry and completeness of the interface; CI's VMOD row runs the default `all` scope precisely so its evidence does not shrink.

**The Debian buildroot is built twice per (engine, target) pair** — once in the engine job, once in the VMOD job. `make-chroot.sh` builds it from a pinned `snapshot.debian.org` timestamp, so it is reproducible rather than merely repeatable, and it is a buildroot rather than a package. Sharing it would mean another artifact and another verification for a 45–80 second step; not worth it before measurement says so.

## Verification performed

Host-safe only, per the runbook — no package was built and no container lane was run:

- `python3 tools/release_tool.py validate` — 10 manifests, passes.
- `python3 tools/release_tool.py validate --require-releasable` — passes.
- `python3 tools/release_tool.py --no-cachetag-cross-check validate` — passes.
- `python3 tools/release_tool.py selftest` — 111 pass, 0 fail, 0 skip.
- `python3 tools/ci_matrix.py selftest` — 147 pass, 0 fail (102 before the new engine tests, 96 on `main`; the pin-drift guard still passes unchanged).
- `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest` — clean across all five workflow files.
- `docker run --rm -v "$PWD":/mnt -w /mnt koalaman/shellcheck:latest` over the nine changed or new shell scripts: **the same two findings before and after the refactor**, both pre-existing (`SC2012`/`SC2035` on `build.sh`'s checksum glob, `SC2086` on `recipes/el9/build.sh`'s deliberate stage word-splitting). No new finding. `SC1091` (unfollowable source), `SC1007` (`CDPATH=` prefix) and `SC2140` are this repository's established idioms and were excluded on both sides of the comparison.
- `sh -n` / `bash -n` on every changed script.
- `scripts/ci/engine-identity.sh` run for all four (family, track) combinations on the host; every load-bearing pin resolves and the two tracks differ on all of them.
- Engine metadata round-tripped through the CLI: produced from a synthetic package set, verified successfully against matching pins, and rejected on eleven identity keys against the other track's pins.
- Simulated collector run with one engine row failing and its consumer uploading nothing: `10 expected, 8 passed, 2 failed`, the engine row `failed_engine_build`, its single consumer `blocked_by_engine_artifact` naming the engine row key, the other three engine rows and their consumers `passed`, exit 1. The per-VMOD `summarize-vmod` on the same records produced the same picture and synthesized exactly one record.
- `git diff main --stat` reviewed file by file; every `recipes/` and `scripts/` change is in the byte-neutrality table above.

## What remains

- **The live proof.** The plan's verification case 6 — "fail one engine artifact row; confirm only consumers of that exact engine/target are reported blocked and unrelated engine rows complete" — needs two dispatched runs, `-f inject=engine_build` and `-f inject=suppress_engine_artifact`, plus a baseline `-f inject=none`. Run them **one at a time**: the Step 3 live proof recorded that dispatching several `ci.yml` runs within a minute saturates the runner allowance and once slowed an EL9 row into its timeout.
- **The package-equivalence comparison**, which is the Step 4 exit gate's fourth condition. Same contract as Step 3: Debian package digests byte-identical excluding `.buildinfo` and `.changes`; EL9 normalized semantic comparison of NEVRA, payload, modes, ownership, symlinks, scripts, triggers, Provides/Requires/Conflicts/Obsoletes and the engine ABI dependency; identical installed-package smoke and behaviour results. Compare against the last green `main` run at this branch's base commit. The structural argument above says the bytes cannot have moved; Step 3 recorded that the structural argument turned out to be right and was still not evidence until it was measured.
- **The engine ABI dependency is the sharpest thing to check** in that comparison. `libvmod-cachetag`'s `vinyld(abi)`, `vinyld(cohort-…)` and `vinyld(vrt)` requirements are derived, on EL9, from values read back out of the mock-installed development package — which in `vmod` scope comes from the downloaded artifact. `vinyld(vrt)` is sharpest of the three, because the rpm identity branch does not pin the VRT expectation at all (see the metadata asymmetry above), so the identity comparison cannot have caught a change in it. If anything about the split moved a package byte, that is where it will show.
- **D5's other half**: VMOD source-archive metadata is still not read back and compared by the target rows. The engine half is done and is the half that had no alternative check. Closing the source half is a small change to the target job and is worth doing before Phase 3 multiplies the number of source artifacts.
- **Branch-protection check names** are unchanged from Step 3's note: the required status check should be `collect`, optionally with `structural-validation`. The engine rows are matrix children and must not be named individually.
- **The duplicated pins in the three unmigrated workflows** and their guard, which retires in Phase 4.
