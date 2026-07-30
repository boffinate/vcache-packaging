# Report: Step 3 failure-isolation live proof and package equivalence

Date: 2026-07-28

Status: Complete. Item 7 of [the failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md) and the package-equivalence contract in [the roadmap](20260728_0916_roadmap_outstanding-packaging-work.md) Step 3.

Companion to [the Phase 1 implementation note](archive/20260728_1704_note_step-3-failure-isolation-phase-1.md), which describes what was built. This one records what happened when it ran.

Branch `step3-failure-isolation-phase1`, pushed to origin at `5211827`. Not merged, no PR opened.

## Runs

Every run is `ci.yml` on `step3-failure-isolation-phase1`, dispatched with `-f inject=<case>`.

| # | Case | Run | Conclusion | Verdict |
| --- | --- | --- | --- | --- |
| 1a | `none` (first attempt) | [30378977589](https://github.com/boffinate/vcache-packaging/actions/runs/30378977589) | cancelled | Environmental: one row hit its 30-minute job timeout. Reported correctly. See below. |
| 1b | `none` (re-run, alone) | [30382470092](https://github.com/boffinate/vcache-packaging/actions/runs/30382470092) | success | **PASS** — all six expected rows passed |
| 2 | `source_digest` | [30379027988](https://github.com/boffinate/vcache-packaging/actions/runs/30379027988) | failure | **PASS** |
| 3 | `manifest` | [30379014809](https://github.com/boffinate/vcache-packaging/actions/runs/30379014809) | failure | **PASS** |
| 4 | `debian_build` | [30380526327](https://github.com/boffinate/vcache-packaging/actions/runs/30380526327) | failure | **PASS** |
| 5 | `suppress_result` | [30380539280](https://github.com/boffinate/vcache-packaging/actions/runs/30380539280) | failure | **PASS** |
| 6 | cancellation | [30384194349](https://github.com/boffinate/vcache-packaging/actions/runs/30384194349) | cancelled | **PASS** |

Pre-refactor comparison baseline: [30366180075](https://github.com/boffinate/vcache-packaging/actions/runs/30366180075), the last green `main` run, at `6551fda` — the exact commit this branch is based on, so the only variable between the two runs is the workflow restructure.

## Observed versus expected, per case

### 1b. Baseline, `inject=none`

```text
ok: true  {"expected": 6, "passed": 6, "failed": 0, "missing": 0, "required_failed": 0, "not_selected": 1}
  vmod/cachetag                                            passed        observed
  source/cachetag/release                                  passed        observed
  target/cachetag/release/vinyl-release/debian-13-amd64    passed        observed
  target/cachetag/release/vinyl-release/el9-x86_64         passed        observed
  target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64  passed     observed
  target/cachetag/release/vinyl-trunk-pinned/el9-x86_64    passed        observed
  harness/cachetag/trunk/vinyl-trunk-head                  not_selected  (no ci lane)
```

No unexpected result records, no synthesized rows, and — case 7 — **no spurious `failed_infrastructure` anywhere**, which is the evidence that `if-no-files-found: warn` and the skipped-step outcomes are inert on a green row.

### 2. `source_digest`

Source row `failed_source_digest`, detail naming the injected digest. All four target rows `blocked_by_vmod_source` with `observed: true` — they are not synthesized. The job list confirms all four target jobs **started, ran, wrote their own record and then failed explicitly**; none was skipped or cancelled. Collector red, `required_failed: 5`.

### 3. `manifest`

```text
ok: false  {"expected": 1, "failed": 1, "required_failed": 1}
  vmod/cachetag  failed_manifest_validation  "registry/vmods/cachetag.yml did not validate as vmod-ci/v1"
```

Exactly one row. No lane rows. This is the case the pre-push audit caught as broken: before the fix the `summary` and `collect` jobs rebuilt the ledger from clean checkouts and produced four invented `blocked_by_vmod_source` lane rows. The event guard on the collector's injection step fired correctly on `workflow_dispatch`.

### 4. `debian_build`

| Row | Status |
| --- | --- |
| `vinyl-release/debian-13-amd64` | `failed_package_build` ("injected package-build failure") |
| `vinyl-trunk-pinned/debian-13-amd64` | `failed_package_build` |
| `vinyl-release/el9-x86_64` | `passed` |
| `vinyl-trunk-pinned/el9-x86_64` | `passed` |

`failed_package_build`, not `failed_infrastructure` — the elif ordering is intact. Both EL9 rows completed and passed while both Debian rows failed, which is the isolation property. All four package artifacts and both failed rows' logs were retained.

### 5. `suppress_result`

The suppressed row's **job is green** (`debian-13-amd64 (vinyl-release)` concluded `success`) and its `result-cachetag-release-vinyl-release-debian-13-amd64` artifact is absent from the run. The collector synthesized `missing_result_record` for that row and went red (`required_failed: 1`, `missing: 1`). A skipped upload is correctly not a failure of its own step, and the collector caught the gap independently — which is the whole point of reconciling against a ledger it computes itself.

### 6. Cancellation

Cancelled with `gh run cancel` about 90 seconds into the target phase, with all four target rows building.

- The workflow ended `cancelled`.
- The per-VMOD `summary` job and the global `collect` job were both **cancelled and did not run**, so the run ended without a final collector. This is what `if: ${{ !cancelled() }}` is for and what the plan explicitly permits.
- **The audit's specific concern did not materialise: no evidence was lost.** All four in-flight rows completed their `if: always()` chain inside the cancellation grace period — package upload, then classification, then result-record upload — and all four `result-*` artifacts plus all four `packages-*` artifacts are present on the cancelled run. Moving the package uploads ahead of the record step did not push the record upload out of the grace window.
- The cancelled rows recorded `failed_infrastructure` with "the … row did not reach its checksums / VTC suite", which is honest: they stopped before their final stage. `fail when this row failed` was skipped, because a cancelled job skips steps that are not `always()`/`!cancelled()`.

### 1a. The first baseline attempt, and why it is not a regression

The `el9-x86_64 (vinyl-trunk-pinned)` row hit its `timeout-minutes: 30` and was cancelled mid-Mock-build. Diagnosis:

- The step log shows the lane **progressing normally, just slowly**, through every expected stage in the expected order.
- The slow phases are all dnf/mock network work. `build dependencies` took 3m13s against 40s on `main`; the Vinyl Mock build did not start until 18 minutes into a step that starts it in 45 seconds on `main`.
- Its sibling `el9-x86_64 (vinyl-release)` row, in the same run on the same branch, finished in 7m16s — so nothing about the branch's EL9 path is inherently slower.
- The re-run of the identical case (1b), dispatched alone rather than alongside two other dispatched runs, completed that row in 7m33s against `main`'s 7m19s.
- The command set in the moved jobs is byte-identical to `main`'s, verified by diff.

Conclusion: a slow runner or slow package mirror under self-inflicted contention — three runs dispatched within a minute of each other, and a fourth queued for an hour behind them. Not a workflow regression. **No timeout was changed in response**; the budget that is adequate on `main` is adequate here, and raising it to accommodate one bad runner would only hide the next real hang.

The valuable part is what the machinery did with it: the timed-out row recorded **`failed_infrastructure` — "the EL9 row did not reach its VTC suite" — not `passed`**. The "row stopped without an explicit stage failure" guard catches a job timeout, the collector still ran (a cancelled *job* is not a cancelled *workflow*), reported the five passing rows and the one failed row, and failed the run.

## Package equivalence

Branch run 1b versus `main` run 30366180075, same commit content, same pins.

### Debian 13 amd64 — byte-identical package digests

Excluding `.buildinfo` and `.changes`, which legitimately record per-run build-environment state.

| Engine channel | Package | Verdict |
| --- | --- | --- |
| `vinyl-release` | `libvmod-cachetag_1.0.1-1_amd64.deb` | identical (`d488e49c…`) |
| `vinyl-release` | `libvmod-cachetag-dbgsym_1.0.1-1_amd64.deb` | identical (`052974bb…`) |
| `vinyl-release` | `vinyl-cache_9.0.1-1_amd64.deb` | identical (`c27b7eac…`) |
| `vinyl-release` | `vinyl-cache-dbgsym_9.0.1-1_amd64.deb` | identical (`a6f5b430…`) |
| `vinyl-release` | `vinyl-cache-dev_9.0.1-1_amd64.deb` | identical (`9152335b…`) |
| `vinyl-trunk-pinned` | `libvmod-cachetag_1.0.1-1_amd64.deb` | identical (`408f7101…`) |
| `vinyl-trunk-pinned` | `libvmod-cachetag-dbgsym_1.0.1-1_amd64.deb` | identical (`0b287d7e…`) |
| `vinyl-trunk-pinned` | `vinyl-cache_9.0.0~git20260520.25761f8505-1_amd64.deb` | identical (`b326ddf1…`) |
| `vinyl-trunk-pinned` | `vinyl-cache-dbgsym_9.0.0~git…_amd64.deb` | identical (`75937a12…`) |
| `vinyl-trunk-pinned` | `vinyl-cache-dev_9.0.0~git…_amd64.deb` | identical (`e0cb65ef…`) |

10 of 10 byte-identical. **Debian: PASS.**

### EL9 x86_64 — normalized semantic comparison

Compared inside the pinned `almalinux:9` container (no rpm tooling on the host), per package: NEVRA; payload path, size, content digest, mode, owner, group, config/doc flags, rdev and symlink target; file mtimes as a separate section; Provides; Requires; Conflicts; Obsoletes; scripts; triggers. Whole-RPM digests deliberately not compared.

All 18 RPMs — 9 per engine channel, `vinyl-cache`, `-devel`, `-debuginfo`, `-debugsource` and the SRPM, plus `libvmod-cachetag`, `-debuginfo`, `-debugsource` and its SRPM — compared **EQUIVALENT** with an empty diff across every section, in both channels. The package sets match exactly: nothing extra on either side.

The comparison is not vacuous: `libvmod-cachetag`'s engine ABI dependencies are inside the compared content and match exactly on both sides —

```text
vinyld(abi)(x86-64) = 423648c4cb6b225b3268ffc337354ea938f5efee
vinyld(cohort-vinyl-9.0.1-ac4f719c16f4)(x86-64)
vinyld(vrt)(x86-64) = 23.0
```

— and the compared dumps carry 11 payload entries for `libvmod-cachetag` and 111 for `vinyl-cache`. File mtimes matched too, so the `SOURCE_DATE_EPOCH` clamping is unchanged. **EL9: PASS.**

### Installed-package smoke and behaviour

Both targets ran their installed-package smoke and full installed-package VTC suite in run 1b and passed, as they did on `main`. Same stages, same commands, same result. **PASS.**

## What this does and does not prove

Proven live: one VMOD's source failure does not stop its own target rows from starting and reporting; a target failure on one distribution does not touch the other; a malformed manifest is one classified row and invents nothing; a missing result artifact is caught by the collector and not by the row that lost it; the collector runs after ordinary failures and does not run after an intentional cancellation; the required check is red whenever a required row is; package bytes did not move.

Not proven, because it needs machinery from later phases: `blocked_by_engine_artifact` (Phase 2's engine split), `failed_transactions` (Phase 4's nightly migration), release-completeness refusal (Phase 4's release-draft migration), and the ten-entry synthetic fixture, which the roadmap places immediately before the Step 8 migration rather than here.

Single-VMOD caveat: with one entry in the catalog, "one VMOD failing does not stop the others" is demonstrated by construction — reusable-workflow containment plus `fail-fast: false` — and by the multi-VMOD fixture in `ci_matrix.py selftest`, not by a live run with two real VMODs. That arrives with the second VMOD in Phase 3.

## Recommendation

**The Step 3 exit gate is met.** Its three conditions:

- *A deliberately broken cachetag row does not suppress unrelated target results* — cases 2, 4 and 5, live.
- *Every expected row is reconciled against an observed result* — every case, including the two where a row produced no record at all, and including a job timeout nobody planned.
- *Successful package artifacts satisfy the target-specific equivalence contract against the pre-refactor baseline* — 10/10 Debian byte-identical, 18/18 EL9 semantically equivalent, smoke and behaviour unchanged.

Before opening the PR, the repository's required status checks must be changed to the collector's `collect` job — "reconcile every expected row" — optionally with `structural-validation`. The old per-row check names (`registry-selftest`, `debian-13`, `el9`) no longer exist as jobs, and a required check whose job never reports leaves the PR waiting at "Expected" forever instead of failing.

One operational note for future live work: dispatching several `ci.yml` runs within a minute of each other saturates the runner allowance, queues jobs for up to an hour and, at least once, slowed an EL9 row enough to hit its timeout. Run the long injection cases one at a time.
