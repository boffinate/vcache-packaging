# Note: Step 3, failure isolation Phase 1

Date: 2026-07-28

Status: Implemented on branch `step3-failure-isolation-phase1`, first-pass audit fixes applied, unexecuted in CI

Implements items 1–6 of [Phase 1 of the VMOD matrix failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md), which is step 3 of [the outstanding-work roadmap](20260728_0916_roadmap_outstanding-packaging-work.md). Item 7 — the live failure-injection runs — is deliberately not done here; the hooks it needs exist and are described below.

## What was built

| Piece | File |
| --- | --- |
| VMOD catalog entry for cachetag | `registry/vmods/cachetag.yml` |
| Catalog, matrix expansion, ledger, reconciliation | `tools/ci_matrix.py` |
| Its tests, including a six-entry isolation fixture | `tools/ci_matrix_selftest.py` |
| Structural / source-coupled validation split | `tools/manifest.py`, `tools/release_tool.py`, `tools/selftest.py` |
| Reusable per-VMOD workflow | `.github/workflows/vmod-package.yml` |
| Restructured top-level CI | `.github/workflows/ci.yml` |
| Schema and command documentation | `registry/README.md`, `AGENTS.md` |

`nightly-transactions.yml`, `release-draft.yml` and `trunk-vmod-ci.yml` are untouched, as the roadmap requires; they migrate in Phase 4. They still carry their own `CACHETAG_*` environment pins and their own copies of the source-archive job, which now duplicate the manifest rather than being the only record of it. That duplication is deliberate for one phase and is the first thing Phase 4 removes.

Nothing under `recipes/` or `scripts/` changed. `git diff main --stat` touches only workflows, tooling, the new manifest and documentation, so package bytes cannot have moved in this change set.

## The graph, before and after

Before, one shared job gated everything:

```text
registry-selftest (checks out cachetag)
        |
cachetag-source-archive
        |
   +----+----+
 Debian     EL9          -> checksums-summary (ordinary needs, skipped on failure)
```

After:

```text
structural-validation (no VMOD checkout)
        |
discover-vmods (file names only)
        |
vmods: matrix -> vmod-package.yml, fail-fast: false
        |            plan -> source -> target (fail-fast: false) -> summary (if: !cancelled())
        |
collect (if: !cancelled()): reconcile the expected ledger, then fail if a required row failed
```

## Design decisions and deviations from the plan

**The pins are `v1.0.1` / `a3897aac`, not the plan's `v1.0.0` / `368a01f`.** The plan document was written before the re-pin landed. The manifest carries what `.github/workflows/*.yml` and `recipes/debian-13/pins.env` actually carry today: ref `v1.0.1`, commit `a3897aaccf1d6996c00ee14b2c6e1ddac91ac982`, version `1.0.1`, archive sha256 `9aba3eff…9eac2`. The plan's example digest was not used.

**Lanes carry a `tiers` list, which the plan's schema sketch does not have.** `ci_matrix.py` is required to "expand the explicitly declared lanes for one VMOD and workflow tier", and without a tier membership on the lane that parameter has nothing to select on. The alternative — hard-coding "tier ci means all package lanes" in the tool — would move a policy decision out of the manifest into code, against the plan's insistence that the lane list is explicit. The tiers recorded are the ones that are true today: `ci` and `release` for the release-engine lane, `ci` for the trunk-pinned lane, `trunk` for the source-harness lane. **`nightly` appears nowhere**, because `nightly-transactions.yml` still runs its own graph; claiming a nightly row would put a row in the expected ledger that nothing produces, and the collector would rightly call it missing evidence.

**Block sequences, not the plan's `targets: [a, b]` flow sequences.** `tools/yaml_subset.py` accepts only the empty flow sequence, by design. Extending the parser to accept non-empty flow sequences would widen the manifest surface for cosmetics, so the manifest uses block sequences instead. No parser change was needed for anything in `vmod-ci/v1`.

**Booleans are the strings `true` / `false`.** The subset parser deliberately does no implicit typing, so `required`, `publishable` and friends are validated as a two-value enum. The files still read exactly like the plan's sketch.

**Engine ids and target facts live in `ci_matrix.py`, not in the manifest.** `ENGINES` maps `vinyl-release` / `vinyl-trunk-pinned` / `vinyl-trunk-head` onto the `VINYL_TRACK` value the existing lane scripts already select on, and `TARGETS` records each target's package family and the job timeout ci.yml already used. These belong to the target and the engine, not to a VMOD, and a second VMOD must not be able to redefine them. In Phase 2 `ENGINES` becomes the expected engine-row ledger, and that is the point at which to reconsider whether `TARGETS` should move into the registry as manifests rather than staying a table in the tool: a target that gains resolved build inputs of its own is registry data, while a table of package families and job timeouts is not.

**The `plan` job emits the matrices; the collector recomputes the ledger.** The plan warns against obtaining per-row artifact names from aggregate matrix-job outputs, so every artifact name is derived from the stable logical row key on both sides: producers compute it from the expanded row, and the collector computes the same name from the checked-in manifest without reading any job output. The collector never trusts a run's own idea of what it was supposed to do.

**Result-record precedence rather than a single writer.** A row writes its own record; the per-VMOD summary writes a merged `result.json` including synthesized rows for anything unreported. The loader dedups by row key with a fixed precedence — a row's own record always beats a synthesized one, regardless of load order — so both can be uploaded without either masking the other. Tested in both orders.

**Package artifact uploads became `if-no-files-found: warn`.** ci.yml used `error` on an `if: always()` upload, which is exactly the failure mode the plan's "Failure reporting" section warns about: a Mock build failure surfaced as an unrelated "no files found". The guaranteed evidence is now the classified result record, written *before* any upload and followed by an explicit failure step. Result-record uploads keep `if-no-files-found: error`, because that file is always written.

**The reusable workflow is still cachetag-shaped in two places.** It calls `ci_verify_cachetag_release_checkout` and checks out into a `libvmod-cachetag` path. Both are moved-unchanged code, and the plan puts generic-name removal in Phase 3, where a second real VMOD can prove which parts are genuinely generic. Renaming them now would be guessing.

**A source-row cross-check failure is classified `failed_manifest_validation`.** The vocabulary has no "manifest disagrees with the source" status, and that is what the failure is: the manifest's recorded version, or the registry's `cachetag.version`, disagreeing with the checked-out `configure.ac`. `failed_source_checkout` would be wrong — the checkout succeeded and resolved to the right commit.

## The validation split

The `configure.ac` cross-check is the only check in this tooling that reaches outside the registry, so the split is along exactly that line rather than being a workflow-step move:

- `manifest.validate_cohort(..., expected_version=None)` and `manifest.validate_registry_tree(..., cross_check_cachetag=False)` run every schema, cohort-identity-digest, target-wiring and placeholder check and skip only the version comparison.
- `release_tool.py --no-cachetag-cross-check` threads that through `validate`, `cohort-id`, `metadata` and `release-notes`, and says out loud that the cross-check was skipped and where it now runs.
- The default is unchanged. A missing or foreign checkout is still a hard error for local use and for `release-draft.yml`, which was not touched.
- The check itself moved into the cachetag source job: `ci_matrix.py validate-vmod --source-dir` compares the manifest's recorded version with `AC_INIT`, and the ordinary cross-checking `release_tool.py validate` compares every registry manifest's `cachetag.version` with the same checkout.
- `selftest.py` reports the source-coupled tests as `SKIP` with a reason when no checkout is present, rather than quietly shrinking in the global CI job. New `split:` tests cover both directions: structural validation passes with no checkout at all and still catches digest and schema errors; a version mismatch is still an error when a checkout is supplied.

## How the expected-ledger reconciliation works

1. `ci_matrix.py ledger --tier ci` walks `registry/vmods/*.yml`. For each entry it takes the id from the **file name** and then parses the manifest. A manifest that parses and validates contributes one invocation row, one source row per channel used by a package lane, and one row per lane target; lanes the tier does not select stay in the ledger marked `selected: false`. A manifest that does not parse or does not validate contributes **exactly one** invocation row, using the trusted discovery id, with `required: true` assumed because the flag that would say otherwise could not be read. No lane rows are invented from a manifest that failed validation.
2. `load_records` walks the downloaded artifacts for `*.json` files carrying `schema: vmod-ci-result/v1`, keyed by row key, with the precedence rule above.
3. `reconcile` resolves each expected row: an observed record keeps its status; an absent record is classified by why it is absent — `blocked_by_vmod_source` when that VMOD's source row for that channel is not `passed` (or produced no record itself), `failed_manifest_validation` for an invocation row whose manifest is broken, and otherwise `missing_result_record`. Unselected rows report `not_selected`.
4. Records with no expected row are listed separately, and a non-passing one fails the run: an unexpected failure is still a failure.
5. The run is red when any **required** row is not `passed`/`not_selected`. A non-required VMOD's failures are reported in full but do not fail the run; `required: true` on cachetag means Phase 1 behaves identically to before.

The step summary groups by VMOD, then by source channel, engine and target, and shows passing rows even when the run is red. The resolved ledger is also uploaded as `ci-reconciled-ledger`.

## Failure vocabulary implemented

All seventeen statuses from the plan exist as a closed set in `ci_matrix.py`; `record` rejects anything else rather than accepting free text. Which ones Phase 1 can actually produce:

| Status | Produced by |
| --- | --- |
| `failed_manifest_validation` | `plan` job; source-row cross-check |
| `failed_source_checkout` | VMOD checkout, or tag/commit verification |
| `failed_source_digest` | archive digest assertion (matched from the archive log) |
| `failed_source_archive` | any other archive-derivation failure |
| `blocked_by_vmod_source` | target row whose source artifact is unavailable; synthesized for unreported rows |
| `failed_engine_build` | pinned Vinyl checkout or build image unavailable |
| `failed_package_build` | `debian-lane.sh`, `mock-build.sh`, source assembly, injected build failure |
| `failed_abi_or_hardening` | `assert-packages.sh` |
| `failed_lint` | `build.sh lint`, `container/build.sh … lint` |
| `failed_install_or_smoke` | `build.sh smoke`, `--smoke-only` |
| `failed_behavior` | `build.sh vtc-suite`, `--vtc-suite-only` |
| `missing_result_record` | collector, when an expected row uploaded nothing and was not blocked |
| `failed_infrastructure` | buildroot creation, or a row that stopped without an explicit stage failure |
| `passed`, `not_selected` | normal outcomes |
| `blocked_by_engine_artifact` | **Phase 2 only** — no separate engine artifact exists yet |
| `failed_transactions` | **Phase 4 only** — the nightly workflow is unmigrated |

Classification uses per-step ids and checks for `outcome == 'failure'` explicitly, never "not success", so a stage skipped because an earlier stage failed is never mistaken for the cause. Every step-local `continue-on-error` is followed by an explicit `exit 1` step gated on the recorded status, so nothing is masked.

## Injection hooks for item 7

`ci.yml` gains a `workflow_dispatch` input `inject`, threaded into `vmod-package.yml`. It defaults to `none`, is only read when `github.event_name == 'workflow_dispatch'`, and is inert on pull requests and pushes. **No build script is modified by any injection**, which is the property that makes item 7 runnable from a branch without touching package content:

| Value | Where it acts | Expected classification |
| --- | --- | --- |
| `manifest` | overwrites the manifest in **every** job that rebuilds the ledger: the `plan` job, the per-VMOD `summary` job, and ci.yml's `collect` job | `failed_manifest_validation` on the invocation row, and one row only |
| `source_checkout` | `ci_matrix.py expand --inject` emits an unresolvable ref in the **sources** matrix only | `failed_source_checkout` on the source row, `blocked_by_vmod_source` on all four target rows |
| `source_digest` | `expand --inject` emits a wrong `archive_sha256` in the sources matrix | `failed_source_digest`, then blocked target rows |
| `debian_build` | an `exit 1` step immediately before `debian-lane.sh` | `failed_package_build` on the two Debian rows; both EL9 rows must still pass |
| `el9_build` | an `exit 1` step immediately before `mock-build.sh` | `failed_package_build` on the two EL9 rows; both Debian rows must still pass |
| `suppress_result` | skips the result upload for the `vinyl-release` Debian row | `missing_result_record` synthesized by the collector for exactly that row |

The `manifest` case has to be applied in three places, and the first draft applied it in one. The `summary` and `collect` jobs each rebuild the expected ledger from their own fresh checkout — that independence is exactly why the collector can report a row that never ran — so a corruption confined to the `plan` job's checkout left both of them reconciling against the full, valid lane list and reporting `blocked_by_vmod_source` for four lanes that a broken manifest never declared. The injected run would have demonstrated the opposite of the property the case exists to prove. This is a general rule for any future injection that changes what the ledger *should* contain, as opposed to what a row *did*: it must reach every ledger builder.

Ref injection is applied to the **source** matrix only, never to the target rows' own refs. That is deliberate: with the true ref on the target rows, a `source_checkout` injection produces a clean `blocked_by_vmod_source` on the targets instead of four duplicate checkout failures, which is the more useful demonstration and the one the plan's case 1 describes.

The plan's remaining cases need work from later phases: case 6 (a failed engine artifact) needs Phase 2's engine split, and cases 11–12 (release completeness) need Phase 4's `release-draft.yml` migration. Case 9 (cancellation) and case 10 (the required check is red after collection) are testable now.

## What remains

- **Item 7, the live proof.** Every case in the table above needs a real dispatched run on this branch. Nothing here has executed in GitHub Actions; the graph is argued from the documentation and `actionlint`, which is exactly what the plan says is not sufficient.
- **The package-equivalence check.** The roadmap's Step 3 contract — byte-identical Debian package digests excluding `.buildinfo` and `.changes`, a normalized semantic RPM comparison, and equivalent installed-package smoke and behaviour results — cannot be evaluated on the host and has not been evaluated at all. The argument that it should hold is structural: no file under `recipes/` or `scripts/` changed, the build commands and their order are identical, and the pins are the same values read from the manifest instead of workflow environment variables. That argument is not evidence. A run of this branch and a run of `main` must be compared before Step 3's exit gate is claimed.
- **Artifact metadata is not verified by consumers.** The plan requires that every dependency artifact carry resolved-identity metadata and that consumers verify it against the manifest row before use. The source archive does carry its metadata — `release-source-archive.sh` writes `libvmod-cachetag-*.metadata.json` into the artifact — but the target rows do not read it back and compare it with their own row's `ref`, `expected_commit` and `version` before building. Today they re-verify the identity independently, by checking the tag out again and asserting the peeled commit, which is why nothing unverified reaches a package. It becomes load-bearing in Phase 2, when a target row consumes an engine artifact it did not build and no longer has a second, independent way to know what it got; the audit agreed to defer it there.
- **Branch-protection check names.** The job names changed (`registry-selftest` is now `structural-validation`; `debian-13` and `el9` are now matrix rows inside a reusable workflow). **Before opening the PR**, the required status checks should become the collector's `collect` job — that is the job that reconciles every expected row and is red whenever a required row is — optionally alongside `structural-validation`. The old per-row names must be dropped: a required check whose job no longer exists never reports, and the PR hangs at "Expected — waiting for status" forever rather than failing. This is repository configuration and is not visible from, or fixable in, the repository contents.
- **The duplicated pins in the three unmigrated workflows**, noted above.

## Fixes applied after the first-pass audit

The audit passed package neutrality, graph semantics and the tooling, and accepted every deviation above. Six things were changed before the live-proof push:

- **The `manifest` injection reached only one of three ledger builders**, described in the injection section above. The most interesting defect of the set: the injection would have run green-ish and been read as proof of a property it disproved.
- **An artifact upload could leave a record saying `passed` on a red job.** The source-archive upload had no `id` and no `continue-on-error`, so a failure there made the job red while the `if: always()` classification, which knew nothing about it, still wrote `passed` — and the explicit fail step was skipped by its implicit `success()` guard. Uploads that matter are now classified: the source archive and both package uploads run `continue-on-error` and classify as `failed_infrastructure`, the package uploads moved *ahead* of the record step so that a failure in them can be classified at all, and a lost result record fails its row explicitly rather than only turning up later as missing evidence.
- **A source-harness row with no source row was reported `blocked_by_vmod_source`.** `vmod_rows` deliberately emits no source row for a channel no package lane consumes, so the blocking lookup was naming a cause that was never expected to run. The blocking path is now taken only when the row genuinely has an upstream source row.
- **A green run containing optional failures said only "Every required row produced a passing result"**, which reads as "nothing failed". It now names the optional failures in the same sentence.
- **The `tier` input advertised `trunk`**, which expands to a source-harness row no job consumes in Phase 1. The description says so, and the unused `harness_count` output is documented as deliberate rather than forgotten.
- **The duplicated pins are now guarded.** `nightly-transactions.yml` and `release-draft.yml` keep their own `CACHETAG_*` values until Phase 4, and the lane pin files always had theirs. A selftest asserts each copy agrees with the manifest. The four files do not carry the same pins, and the table records what each one must carry:

  | File | Pins checked |
  | --- | --- |
  | `recipes/debian-13/pins.env` | `CACHETAG_VERSION`, `CACHETAG_GIT_COMMIT`, `CACHETAG_SOURCE_SHA256` |
  | `recipes/el9/cohort.env` | `CACHETAG_VERSION`, `CACHETAG_GIT_COMMIT`, **`CACHETAG_SHA256`** |
  | `.github/workflows/nightly-transactions.yml` | `CACHETAG_REF`, `CACHETAG_GIT_COMMIT`, `CACHETAG_SOURCE_SHA256` |
  | `.github/workflows/release-draft.yml` | `CACHETAG_REF`, `CACHETAG_GIT_COMMIT`, `CACHETAG_SOURCE_SHA256` |

  The two lanes disagree on a name for the archive digest — Debian's `CACHETAG_SOURCE_SHA256` is EL9's `CACHETAG_SHA256` — and the first version of this guard knew only the Debian name, so a `deadbeef` digest in `cohort.env` passed every assertion. Both names are mapped now. The anti-vacuity check is per expected pin rather than "at least one pin was found", so renaming a pin fails the guard instead of quietly reducing it to checking nothing, and the assignment parser tolerates `export `, indentation, quotes and trailing comments — a pin the guard cannot read is a pin the guard does not check. It retires when Phase 4 removes the duplication.

## Dead ends and rejected alternatives

- **Making the target rows depend on the source job with ordinary `needs` semantics.** That is the current bottleneck in miniature: the row is skipped and reports nothing. The target job instead starts under `!cancelled()`, lets the artifact download fail with `continue-on-error`, and classifies itself.
- **A single accumulated shell variable to track the failing stage.** A step that fails cannot then write to `$GITHUB_ENV`, so the accumulator would always miss the failure it exists to record. Per-step ids plus a first-`failure`-wins chain in the recording step is longer to read but is correct.
- **Putting the classification chain in a new `scripts/ci/` helper.** It would be shorter YAML, but it adds an untested shell script to the CI surface for no behavioural gain, and `scripts/` is where the build lane lives — the one place this phase must not touch.
- **Letting the per-VMOD summary fail on a row failure.** Then a red VMOD would produce two failures for the same fact and the collector's verdict would no longer be the single source of truth. The summary job fails only when it cannot do its own job.
- **Extending `yaml_subset.py` for flow sequences.** Considered for the plan's `targets: [a, b]` sketch, rejected: block sequences already parse, and widening the accepted YAML subset for cosmetics is the wrong trade in files whose purpose is exact identity.

## Verification performed

Host-safe only, per the runbook — no package was built and no container lane was run:

- `python3 tools/release_tool.py validate` — 10 manifests, passes.
- `python3 tools/release_tool.py validate --require-releasable` — passes.
- `python3 tools/release_tool.py --no-cachetag-cross-check validate` — passes, and says the cross-check was skipped.
- `python3 tools/release_tool.py selftest` — 111 pass, 0 fail; with `CACHETAG_SRC` pointing at nothing, 109 pass, 0 fail, 1 skip.
- `python3 tools/ci_matrix.py selftest` — 89 pass, 0 fail.
- `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest` — clean across all five workflow files.
- Simulated collector runs against hand-written result records: all-green, a lint failure plus one suppressed record, a failed source row, and a malformed manifest. Each produced the expected classification, summary grouping and exit status.
- Simulated the corrected `manifest` injection against a corrupted copy of the catalog: the collector's ledger is one invocation row, `failed_manifest_validation`, with no lane rows, and exits 1.
- Mutated `CACHETAG_REF` in `nightly-transactions.yml` and confirmed the new pin-drift guard fails with both values named, then restored it.
- `git diff main --stat`: no file under `recipes/` or `scripts/` is touched, and the set of build command lines in the moved jobs is identical to `main`'s.
