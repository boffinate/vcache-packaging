# Roadmap: outstanding packaging work

Date: 2026-07-28

Status: Proposed

> **Status update, 2026-07-30 (docs curation):** Steps 1–8 are complete — see the [step-8 closing report](20260730_1232_report_step-8-closing.md) for the final state and the per-step live-proof reports for the evidence trail. Step 9 (managed repository publication) is active under [its plan](20260730_1415_plan_step-9-managed-repository-publication.md), alongside the [release-automation plan](20260730_1414_plan_release-automation.md). The body below is unedited; wave-level notes it links to have moved to `docs/archive/`.

Decision owner: repository maintainer

## Context

The current baseline is not yet cleanly evidenced: the branch is ahead of remote, the cachetag re-pin changed source bytes and reset package evidence, and the release/trunk work has not run through authoritative GitHub CI. That must be settled before restructuring the workflows.

This roadmap orders the remaining work described by:

- [The cachetag nightly failure note](archive/20260728_0743_note-nightly-cachetag-ref-failure.md)
- [The cohort and pre-release note](archive/20260726_0827_note_step-10-cohort-mint-and-pre-release.md)
- [The release/trunk two-track note](20260726_1235_note_two-track-release-and-trunk.md)
- [The VMOD survey report](20260726_2014_report_vmod-survey-first-sweep.md)
- [The Varnish downstream packaging plan](20260726_0824_plan_varnish-downstream-vmod-packaging.md)
- [The VMOD matrix failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md)
- [The vmod-packager patterns and recipe-generation plan](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md)

## Recommendation

Use this order:

`baseline → package correctness → failure isolation → engine split → second VMOD → exceptions → workflow migration → publication`

Do not implement the matrix plan and recipe-generation plan as two independent projects. They converge at the second-VMOD vertical slice.

## Ordered work

### 1. Stabilize and land the existing baseline

- Review and commit the intended cachetag re-pin, `SCOPE.md`, two-track workflow, and associated documentation changes.
- Push the existing local commits and subsequent intended changes.
- Run the release and trunk tracks through GitHub Actions.
- Keep this change set free of matrix or recipe-generation refactoring.

This establishes a trustworthy before-state and closes the failure documented in the cachetag nightly failure note.

The release lanes have not executed before, so this step is a bring-up and debugging cycle rather than a push-and-watch formality. Failures in the drafted release-tarball path should initially be treated as path defects. Packages produced during this cycle are provisional: do not mint the release cohort or accept them as target evidence of record until the Step 2 correctness fixes have landed and the lanes have re-run on the corrected bytes.

Exit gate:

- Intended local work is committed and pushed.
- Release and trunk tracks complete with authoritative GitHub Actions workflow evidence, even though their package evidence remains provisional until Step 2.
- The cachetag re-pin is proven independently of later workflow restructuring.

### 2. Finish the one-VMOD package baseline

Before calling cachetag complete:

- Correct the EL9 `SOURCE_DATE_EPOCH` handling.
- Replace the weak `rpmlint` treatment with an explicit assertion or reviewed allowlist.
- Re-run the Debian and EL9 builds and smoke tests on the corrected bytes.
- Mint the Vinyl 9.0.1 release cohort from that evidence.
- Run the transaction matrices against the minted release cohort.
- Run the complete behavior suite against the installed production package, not merely a source-tree build.
- Run `release-draft` and confirm the resulting artifacts and checksums.
- Flip the default `VINYL_TRACK` to `release` in both pin files after the release lane, transactions, and release draft pass. Keep trunk jobs explicitly selecting `VINYL_TRACK=trunk`.
- Update the repository README status and the cachetag packaging README cohort examples to the release cohort.

The epoch fix changes package bytes. Finalize it before minting the release cohort or accepting target evidence so there is only one evidence-population cycle. If package revision 2 has not been published or adopted as valid evidence, it can be finalized with the epoch fix before its identity becomes authoritative. If revision 2 has already acquired a published or evidence-backed identity, increment the package revision and reset its evidence. Tightening the `rpmlint` gate alone does not require a revision change when it leaves package bytes unchanged.

Exit gate:

- All target evidence reset by the cachetag re-pin is populated and passing.
- The installed package passes the full behavior suite.
- The release cohort and draft artifacts are reproducible and internally consistent.
- Default local package builds select the release track, while trunk workflows continue to select trunk explicitly.
- Repository and cachetag packaging documentation identify the current release cohort and track behavior.

### 3. Implement failure isolation Phase 1

Implement the cachetag-only portion of the matrix failure-isolation plan:

- Separate structural validation from per-VMOD source validation.
- Add the reusable per-VMOD workflow.
- Preserve the existing package-building implementation inside it.
- Build the collector from an independent expected-row ledger.
- Prove missing-result, compiler-failure, blocked-row, and cancellation behavior through failure injection.

Package content must remain unchanged during this phase.

For unchanged inputs, package equivalence has target-specific definitions:

- Debian package sets must have byte-identical package digests, excluding `.buildinfo` and `.changes`, which legitimately record per-run build-environment state.
- EL9 package sets must pass a normalized semantic comparison of NEVRA, payload paths and content digests, file types and modes, ownership, symlink targets, scripts and triggers, Provides, Requires, Conflicts, Obsoletes, and the expected engine ABI dependency. Whole-RPM digests are not an equivalence requirement while RPM headers retain nondeterministic build state.
- Both targets must produce equivalent installed-package smoke and behavior results.

Exit gate:

- A deliberately broken cachetag row does not suppress unrelated target results.
- Every expected row is reconciled against an observed result.
- Successful package artifacts satisfy the target-specific equivalence contract against the pre-refactor baseline.

### 4. Implement failure isolation Phase 2

Split engine production from VMOD package production:

- Build shared Vinyl and Varnish engine packages once.
- Name artifacts using stable manifest keys.
- Put resolved version and commit identity inside artifact metadata.
- Make VMOD rows consume native engine artifacts.
- Verify cachetag output and behavior against the target-specific package-equivalence contract established in Step 3.
- Test missing-engine-artifact classification explicitly.

Exit gate:

- Cachetag no longer rebuilds its engine inside each VMOD target row.
- Consumers verify artifact metadata before building.
- An unavailable engine artifact produces the planned classified result.
- Cachetag packages and installed-package behavior satisfy the Step 3 equivalence contract after the engine split.

After the Phase 1 manifest schema is stable, normalized-model and deterministic-renderer work from recipe-generation Phase 1 may begin in parallel with Steps 4 and 5. This host-safe text-generation work must not enter production lanes or determine the general abstraction before the second VMOD is selected.

### 5. Refresh decisions before selecting another VMOD

- Update the Varnish downstream plan to remove its outdated Varnish 9 package-source assumption.
- Update README and AGENTS language so centrally generated recipes are an accepted option when upstream packaging is absent.
- Select one conventional Autotools VMOD from the survey's 36 dual-compatible results. It must have either a runnable test suite that can exercise the installed package or a meaningful package-level behavior smoke that can be defined before implementation; load-only verification is insufficient.
- Decide whether its first production lane is Varnish, Vinyl, or both.
- Record the explicit selection in `SCOPE.md`.

Exit gate:

- The engine package source, ABI dependency model, and first downstream lane are explicit.
- The second VMOD is selected by a maintainer decision.
- The selection is inside the normative project scope.
- The selected VMOD has an agreed behavior-verification path suitable for the Step 6 gate.

### 6. Build the second-VMOD tracer bullet

Combine three pieces of work:

- Phase 3 of the failure-isolation plan.
- Phases 1 and 2 of the recipe-generation plan.
- The relevant portion of the refreshed Varnish downstream plan.

The selected VMOD must receive generated Debian and RPM recipes, clean pbuilder and Mock builds, installed-package smoke and behavior tests, transaction evidence, and forced failure-isolation tests.

Exit gate:

- A second real VMOD is packaged without requiring upstream-maintained Debian or RPM files.
- Generated recipes are deterministic and validated.
- A failure in either VMOD does not hide the other VMOD's results.
- Both package families meet the same evidence policy as cachetag.

### 7. Prove one controlled exception

Add a third VMOD only if it exercises one useful variation, such as an extra dependency, bootstrap command, patch, or recipe override.

Do not add Meson, CMake, or general plugin machinery until a selected VMOD requires it. Do not implement broad Vinyl compatibility shims merely because the survey identified possible candidates.

Exit gate:

- The adapter and override model handles one real exception without weakening default validation.
- The exception remains visible in the manifest rather than becoming hidden generator behavior.

### 8. Migrate and scale

After two real VMODs and one controlled exception work:

- Run the matrix plan's ten-entry synthetic acceptance fixture immediately before migration. One entry must fail source verification, nine must reach their final test stage, the summary must reconcile all ten entries, and the workflow must fail without cancelling the nine independent entries.
- Migrate nightly, trunk, and release-draft workflows to the isolated graph.
- Integrate recurring survey and trunk compatibility checks.
- Add required-set completeness checks for publication.
- Add further VMODs one at a time, with an explicit `SCOPE.md` decision for each.
- Pursue survey divergence reports and compatibility work when they affect a selected candidate, rather than as a prerequisite for the packaging pipeline.

Exit gate:

- The ten-entry synthetic fixture demonstrates failure isolation and complete result reconciliation without expanding the production-selected VMOD set.
- All production workflows use the same result and evidence model.
- Publication cannot proceed with a missing required VMOD or target.
- Adding a VMOD is primarily a manifest, adapter, and verification task.

### 9. Add managed repository publication

Once package generation and multi-VMOD evidence are stable, add staging, promotion, retention, rollback, and repository-level transaction tests using a managed APT and RPM service.

Repository hosting is not part of the initial generalization.

Exit gate:

- Promotion operates on a complete, verified package set.
- Rollback and retention policies are tested.
- Clients can install and upgrade through the published repositories.

## Risks

- Refactoring before the re-pin baseline is green makes regressions difficult to attribute.
- Choosing the second VMOD before resolving the Varnish package source could validate the wrong dependency model.
- Treating the ten-entry synthetic fixture as production-package selection would confuse a graph acceptance test with evidence of real package diversity.
- Publishing repositories before completeness checks are reliable risks exposing partial or incompatible package sets.
- Generalizing build systems or compatibility shims ahead of selected-package demand would increase maintenance without proving production value.

## Next action

Finish and evidence the cachetag re-pin and release/trunk baseline before beginning matrix or recipe-generator implementation. Once that baseline is green, accept and begin Phase 1 of the failure-isolation plan.

## Assumptions

- High confidence: cachetag remains the sole production-selected VMOD until `SCOPE.md` is deliberately expanded.
- High confidence: the current re-pin and two-track work is intended to land.
- Medium confidence: the best second VMOD will be a conventional Autotools project; final selection depends on its current source and dependency state.
- No fixed delivery deadline is assumed. Progress is controlled by the exit gates above.
