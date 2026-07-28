# Plan: isolate failures across a ten-VMOD build and test matrix

Date: 2026-07-28

Status: Proposed

## Decision

Treat one VMOD as the primary CI failure boundary.

Each selected VMOD gets its own reusable-workflow invocation. That invocation owns the VMOD's source checkout or download, tag and commit verification, archive digest check, package builds, and tests. The top-level VMOD matrix uses `fail-fast: false`, so one invocation failing cannot cancel or skip the other VMOD invocations.

Within a VMOD invocation, target and compatibility rows also use `fail-fast: false`. A Debian failure must not cancel EL9, and a failure against one Vinyl or Varnish input must not cancel other explicitly selected inputs.

Failures remain failures. This plan does not use `continue-on-error` to make the workflow green. It lets all independent work finish, records every result, and leaves the overall workflow red when any required row fails.

## Current behavior and the cachetag source question

The current package matrix does not build both cachetag `v1.0.0` and cachetag `main`.

Remote refs verified on 2026-07-28:

```text
2c73ba1753919566d2a1e127c0caec8b7132b15e  refs/heads/main
1508d3949957f7a1cd10f5e086b333148ca2c9cc  refs/tags/v1.0.0
368a01f11d25256644154d02ec255db545154c1c  refs/tags/v1.0.0^{}
```

`.github/workflows/ci.yml`, `.github/workflows/nightly-transactions.yml`, and `.github/workflows/release-draft.yml` define:

```text
CACHETAG_REF=v1.0.0
CACHETAG_GIT_COMMIT=368a01f11d25256644154d02ec255db545154c1c
```

Every package job in those workflows checks out that tag and verifies that it peels to that commit. The `track: [trunk, release]` matrix axis in `ci.yml` selects the Vinyl input. Both Vinyl-track rows currently build cachetag `v1.0.0`; neither builds cachetag `main` at `2c73ba1`.

The separate weekly `trunk-vmod-ci.yml` workflow checks out cachetag's default branch, currently `main`, and tests it against Vinyl trunk HEAD. It builds no native packages and publishes nothing. Therefore both cachetag source lines receive some coverage today, but not in the same matrix and not with the same purpose:

- cachetag `v1.0.0` at `368a01f`: native package and release-cohort input;
- cachetag `main` at the commit resolved when the job starts, currently `2c73ba1`: source-level early-warning input.

Consequently, a change present only on cachetag `main` is not present in the package archive derived from `v1.0.0`. If such a change must enter released native packages, cachetag must deliberately move or cut a release and the packaging commit, archive digest, package revision, and evidence must be re-pinned together. The package workflow must not silently substitute `main` for the recorded release source.

The new design will make those source channels explicit. It will not create an accidental Cartesian product. Release-source package rows and trunk-source early-warning rows will be listed deliberately.

## Required outcome

With ten selected VMODs:

- a missing repository or ref for VMOD 1 must not stop VMODs 2–10;
- an archive digest mismatch for VMOD 1 must not stop VMODs 2–10;
- a package build failure for VMOD 1 on Debian must not stop VMODs 2–10 or VMOD 1's independent EL9 row;
- a smoke, lint, ABI, or transaction-test failure in one row must not cancel any other row;
- every attempted or blocked row must appear in the final summary;
- the workflow must finish red when a required row failed;
- release assembly must not publish a partial required package set merely because the other nine succeeded.

A genuinely shared dependency has a wider but still precise failure domain. If one Vinyl or Varnish runtime package fails to build, every VMOD row that requires that exact runtime artifact cannot complete, but unrelated engine versions and targets must continue and the summary must report the shared root cause rather than showing unrelated jobs as cancelled.

## Why the current graph fails this requirement

The current workflows have one `cachetag-source-archive` job followed by every Debian and EL9 package job:

```text
registry-selftest
        |
cachetag-source-archive
        |
   +----+----+
 Debian     EL9
```

The Debian and EL9 matrices correctly set `fail-fast: false`, but they never start when the shared cachetag source job fails. A checkout failure, source download failure, archive-build failure, or digest mismatch in that job therefore blocks the whole package matrix.

There are three additional coupling points:

- `registry-selftest` checks out and validates cachetag specifically, so a cachetag source problem is currently treated as a global registry failure;
- summary and release-assembly jobs use ordinary successful `needs` semantics, so they are skipped after an upstream failure instead of reporting the partial result;
- current lane scripts build Vinyl and cachetag together, which prevents a built Vinyl package from being reused independently by several VMOD package jobs.

## Target workflow structure

The top-level package workflow will have four responsibilities:

1. discover the selected VMOD entries without resolving their remote sources;
2. build each selected Vinyl or Varnish runtime/development package once per engine and target row;
3. invoke one reusable workflow per VMOD with `fail-fast: false`;
4. run a failure-tolerant, cancellation-respecting collector that reports all expected results.

Conceptually:

```text
structural validation ----> VMOD discovery ----------------------+
                                                                  |
engine rows --fail-fast:false--> engine artifacts ----------------+--> VMOD invocation matrix --fail-fast:false
                                                                         |
                                             +---------------------------+---------------------------+
                                             |                           |                           |
                                          VMOD 1                      VMOD 2                      VMOD 10
                                      source + targets             source + targets             source + targets
                                             |                           |                           |
                                             +---------------------------+---------------------------+
                                                                         |
                                                           collector, if: !cancelled()
```

The collector reports partial success but does not erase failures. It runs after ordinary job failures but respects an intentional workflow cancellation. Publication is a separate gate over the collected evidence.

## VMOD catalog and source channels

Add one small checked-in manifest per selected VMOD under `registry/vmods/`. This is an explicit list of packages the maintainer has placed in scope, not repository discovery and not a general package-service registry.

The first cachetag entry should express the distinction that is currently hidden in workflow-wide environment variables:

```yaml
schema: vmod-ci/v1
id: cachetag
repository: boffinate/libvmod-cachetag
required: true
adapter: cachetag
sources:
  release:
    ref: v1.0.0
    expected_commit: 368a01f11d25256644154d02ec255db545154c1c
    version: 1.0.0
    archive_sha256: 23c378029c50072ca287d045208756a9acd0a648c261d2f0e2bca4fdbf7a1644
    publishable: true
  trunk:
    ref: main
    publishable: false
lanes:
  - kind: package
    source: release
    engine: vinyl-release
    targets: [debian-13-amd64, el9-x86_64]
  - kind: package
    source: release
    engine: vinyl-trunk-pinned
    targets: [debian-13-amd64, el9-x86_64]
  - kind: source-harness
    source: trunk
    engine: vinyl-trunk-head
```

The exact manifest schema should remain this small until a real second VMOD proves another field necessary.

Release source entries name a tag or version and record the expected peeled commit and archive digest. Trunk entries name a moving branch and record the resolved commit in run evidence. A trunk entry can never become a publishable package merely because its build passed.

The lane `kind` distinguishes native-package work from a source-level harness. Package lanes name package targets and consume engine package artifacts. Source-harness lanes run the VMOD's documented source test harness against the selected engine source, produce no native package, and do not overload the package-target vocabulary with a pseudo-target.

The lane list is explicit. Do not automatically multiply every VMOD source channel by every engine, release, distribution, and architecture. Each row must exist because it answers a compatibility or publication question the project has chosen to support.

## Reusable VMOD workflow

Add `.github/workflows/vmod-package.yml` with `workflow_call`. The top-level caller supplies only the VMOD manifest path and the workflow tier (`ci`, `nightly`, or `release`).

Each matrix copy of the caller invokes a complete reusable workflow for one VMOD. This containment is important: do not put all VMOD source jobs in one source matrix and then make all package jobs depend normally on that aggregate matrix job. In GitHub Actions, one failed child makes the aggregate dependency fail and ordinary downstream jobs are skipped, recreating the current bottleneck.

Keep this reusable workflow self-contained. The top-level caller already consumes one reusable-workflow nesting level, so `vmod-package.yml` should use ordinary jobs and actions rather than calling further reusable workflows. Matrix copies of the same reusable workflow do not consume additional unique-workflow slots.

The reusable workflow contains:

### 1. Per-VMOD validation

- parse and validate only this VMOD's manifest;
- validate its adapter name and requested lanes;
- perform version cross-checks against this VMOD's source after checkout;
- emit a useful result if the manifest itself is invalid.

Global validation should continue to verify the cohort and engine schemas, the catalog directory, unique VMOD manifest filenames and paths, and tooling self-tests. Each reusable-workflow invocation validates that its manifest's declared id matches its discovery id. Global validation must not parse every detailed VMOD manifest or fetch and validate every VMOD source as one all-or-nothing gate.

### 2. Per-VMOD source job

- check out or download the selected source;
- for a release source, require the annotated tag, expected peeled commit, clean release state, and pinned archive digest;
- for a trunk source, resolve and record the branch HEAD without pretending it is immutable;
- derive the deterministic source archive where the VMOD's package adapter requires one;
- upload an artifact addressed by the stable VMOD id and source-channel key;
- record the requested ref, resolved commit, version, and digest in metadata inside the artifact;
- upload source logs and a result record under `if: always()`.

Failure here blocks target rows for this VMOD only. Other reusable-workflow invocations do not depend on it.

### 3. Per-VMOD target matrix

- construct only the compatibility rows listed for this VMOD and workflow tier;
- set `strategy.fail-fast: false`;
- download the exact engine artifact and VMOD source artifact named by the row;
- verify both artifacts again before use;
- build the VMOD native package against the installed runtime/development packages;
- run ABI, hardening, lint, install, load, smoke, behavior, and transaction checks appropriate to the tier;
- upload packages, logs, and one result record per row under `if: always()`.

A missing engine artifact must fail only the rows that name it. The download step must be allowed to reach the result-writing steps, for example by recording its step outcome and deferring the explicit failure. The row must then be reported as `blocked_by_engine_artifact`, with the engine row identity, rather than as a cancellation or an unclassified download error. This local use of `continue-on-error` does not make the row green: the row writes its classified result and then fails explicitly.

### 4. Per-VMOD summary

Run with `if: ${{ !cancelled() }}` and depend on the source and target jobs. Record:

- VMOD id and source channel;
- requested ref, resolved commit, version, and digest where available;
- every target and engine row;
- the stage and cause of each failure;
- artifact names for successful rows;
- whether the VMOD's required release set is complete.

This job should upload a machine-readable `result.json` even when the VMOD source job failed and every target row was skipped. It must derive the VMOD's expected source and lane rows from the manifest and synthesize a missing-row failure when an expected matrix copy produced no result record. If the manifest itself is missing or invalid, emit an invocation-level `failed_manifest_validation` result using the trusted discovery id and do not invent lane rows that could not be parsed.

## Shared engine package artifacts

Split Vinyl and Varnish package production from VMOD package production.

For each selected engine input and target:

- build the runtime, development, and debug packages once;
- verify their source and ABI identity;
- upload an artifact named from the stable engine input id and target, for example `engine-vinyl-trunk-pinned-debian-13-amd64`;
- place the resolved engine version, commit, ABI identity, package digests, and build input identity in metadata inside the artifact;
- set the engine matrix to `fail-fast: false`;
- always upload a result record and logs.

Refactor the current Debian and EL9 lane scripts so they can run an engine-package stage independently from a VMOD-package stage. The VMOD stage must consume installed engine packages, never an arbitrary build prefix.

The top-level VMOD caller may depend on the aggregate engine matrix only with an explicit `if: ${{ !cancelled() }}` condition. Every VMOD invocation should still start after an engine-row failure and decide from its own requested artifact whether it can proceed. This avoids GitHub's default behavior of skipping the entire downstream job because one engine matrix child failed while still allowing an intentional cancellation to stop queued work.

Waiting for all engine rows before starting VMOD workflows is acceptable initially. It preserves one engine build per row and keeps the graph understandable. Optimize start latency only if measurements later justify more complexity.

## Top-level matrix generation

Add a standard-library tool, for example `tools/ci_matrix.py`, with four small responsibilities:

- list selected VMOD manifest paths and ids without fetching their sources;
- expand the explicitly declared lanes for one VMOD and workflow tier;
- emit the expected engine, VMOD invocation, source, and lane-row ledger for later reconciliation;
- validate, reconcile, and summarize machine-readable result records.

Include a `selftest` command. Keep it dependency-free like the existing registry tooling.

The discovery job should output a matrix containing only trusted local identifiers and manifest paths. Derive the discovery id from the checked-in manifest filename or another source that does not require parsing the detailed manifest, then validate that it matches the manifest's declared id inside the reusable workflow. Parsing the detailed VMOD manifest happens inside that VMOD's reusable workflow, so one malformed VMOD entry becomes one failed matrix copy instead of preventing discovery of the other nine.

The discovery output is also the authoritative expected-invocation ledger. The selected engine inputs provide the expected engine-row ledger. For each VMOD manifest that parses successfully, the collector independently expands the expected source and lane rows for the selected tier. A malformed manifest remains one expected VMOD invocation with a manifest-validation failure rather than disappearing from the ledger.

The top-level invocation should resemble:

```yaml
vmods:
  needs: [discover-vmods, engine-packages]
  if: ${{ !cancelled() && needs.discover-vmods.result == 'success' }}
  strategy:
    fail-fast: false
    max-parallel: 4
    matrix: ${{ fromJSON(needs.discover-vmods.outputs.matrix) }}
  uses: ./.github/workflows/vmod-package.yml
  with:
    manifest: ${{ matrix.manifest }}
    tier: ci
```

`max-parallel` is a cost control, not a failure-control mechanism. All entries remain queued after a failure.

Do not add workflow-level concurrency settings that cancel in-progress matrix copies merely because another commit or scheduled run starts, unless that cancellation policy is separately intended and documented.

The required GitHub Actions mechanics are supported by the current official documentation:

- [a matrix job may call a reusable workflow](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-a-matrix-strategy-with-a-reusable-workflow);
- [`strategy.fail-fast: false` prevents a failed matrix child from cancelling the other children](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations#handling-failures);
- [a job whose `needs` dependency failed is skipped by default unless its condition explicitly permits it to continue](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds);
- [GitHub recommends `if: ${{ !cancelled() }}` rather than `always()` for failure-tolerant work that should still respect cancellation](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#always).

## Workflow-specific changes

### `ci.yml`

- keep a small global tooling and structural-schema self-test;
- remove the cachetag checkout from the global self-test;
- replace the single cachetag source job with the VMOD invocation matrix;
- split engine package production from VMOD package production;
- retain release and pinned-trunk Vinyl rows, but rename the axis from generic `track` to `engine_channel`;
- run the collector with `if: ${{ !cancelled() }}`;
- make the required check fail after collection if any required row failed.

### `nightly-transactions.yml`

- invoke the same per-VMOD reusable workflow with `tier: nightly`;
- run transaction suites inside each VMOD/engine/target row;
- use `fail-fast: false` at both VMOD and target levels;
- collect all nightly results after ordinary failures while respecting cancellation.

### `trunk-vmod-ci.yml`

- generalize the current single cachetag job into a VMOD matrix over explicitly configured trunk lanes;
- check out each VMOD's configured default branch and record its resolved commit;
- set `fail-fast: false`;
- keep this source-level and non-publishable unless a VMOD has an explicitly selected native-package trunk lane;
- for cachetag, this is the workflow that should pick up `main` at `2c73ba1` today.

### `release-draft.yml`

- perform fresh per-VMOD release builds through the reusable workflow;
- allow every VMOD build and test to finish;
- run collection and completeness checks under `if: ${{ !cancelled() }}`;
- do not create or update a draft release when any required VMOD, engine, target, artifact, or evidence record is missing;
- permit a deliberately partial experimental draft only through the existing explicit override, with every omission in its manifest and release notes;
- never publish trunk-source artifacts.

## Result and artifact naming

Artifact names must be globally unique within the workflow and predictable from the stable logical row key. Dependency consumers must never need a runtime-resolved commit or version merely to calculate the artifact name, and the design must not try to obtain per-row artifact names from aggregate matrix-job outputs.

Use the applicable stable components:

- VMOD id;
- VMOD source channel;
- engine input id;
- target id;
- artifact kind.

For example:

```text
vmod-source-cachetag-release
engine-vinyl-trunk-pinned-debian-13-amd64
packages-cachetag-release-vinyl-trunk-pinned-debian-13-amd64
result-cachetag-release-vinyl-trunk-pinned-debian-13-amd64
```

Every source, engine, package, and result artifact must contain machine-readable metadata recording the resolved source identities and content digests. Consumers verify that metadata against the manifest row before use. Resolved identity belongs in evidence even when it is not part of the artifact address.

Do not let two rows write a generic `SHA256SUMS` into the same directory. Preserve row-specific directories and merge only in the collector.

## Failure reporting

Every job that can fail should preserve its logs under `if: always()` when evidence preservation during cancellation is intentional. Evidence upload must not obscure the original failure: use a guaranteed result file or tolerate absent optional build outputs rather than turning “compiler failed” into an unrelated “no files found” error.

The final collector must reconcile the expected ledger with the result artifacts it actually finds. It must not summarize only present artifacts. An expected engine row, discovered VMOD, source row, or valid manifest lane with no corresponding result is a required failure even if GitHub never started the expected job or the runner disappeared before evidence upload. The collector should identify this as missing execution evidence rather than guessing that the package compiler failed. Complete reconciliation is guaranteed for non-cancelled workflow runs; an intentionally cancelled run is allowed to end without a final collector.

The final collector must distinguish:

- `failed_manifest_validation`;
- `failed_source_checkout`;
- `failed_source_digest`;
- `failed_source_archive`;
- `blocked_by_vmod_source`;
- `failed_engine_build`;
- `blocked_by_engine_artifact`;
- `failed_package_build`;
- `failed_abi_or_hardening`;
- `failed_lint`;
- `failed_install_or_smoke`;
- `failed_behavior`;
- `failed_transactions`;
- `missing_result_record`;
- `failed_infrastructure`;
- `passed`;
- `not_selected`.

The GitHub step summary should group results first by VMOD and then by source channel, engine input, and target. It must show successful rows even when the workflow is red.

## Implementation sequence

### Phase 1: prove failure isolation without changing package content

1. Add `tools/ci_matrix.py`, its self-tests, and a minimal `registry/vmods/cachetag.yml`.
2. Add the reusable per-VMOD workflow with cachetag as its only production entry.
3. Split registry tooling into source-independent structural validation and source-coupled VMOD validation. Global validation must not require a cachetag checkout; cachetag's `configure.ac` version cross-check moves to the per-VMOD path after checkout. Update `validate`, cohort-id, metadata, and self-test call paths deliberately rather than treating this as only a workflow-step move.
4. Move cachetag-specific remote validation out of the global registry gate.
5. Put the current source/archive and Debian/EL9 jobs inside the cachetag invocation without changing their commands or pins.
6. Add cancellation-respecting per-VMOD and global result collectors with expected-versus-observed reconciliation.
7. Confirm that deliberate cachetag source failures and deliberately absent result artifacts no longer prevent the collector from running or reporting the missing evidence.

This phase establishes the graph and reporting before introducing ten real VMODs.

### Phase 2: split shared engine packages from VMOD packages

1. Split Debian Vinyl package production from cachetag package production.
2. Split EL9 Vinyl package production from cachetag package production.
3. Upload one engine artifact per engine input and target.
4. Make the cachetag package rows consume those native engine packages.
5. Verify that package contents and ABI metadata remain equivalent to the current coherent lane.
6. Make missing engine artifacts fail only their consumer rows.

### Phase 3: generalize with the second real VMOD

1. Obtain the explicit maintainer decision that selects the second VMOD and update `SCOPE.md` with the added build, test, publication, and maintenance responsibility.
2. Add the second VMOD manifest and its smallest adapter.
3. Remove cachetag names from generic workflow helpers and result schemas where the second entry proves they are genuinely generic.
4. Demonstrate that a forced source, digest, build, and test failure in either VMOD does not cancel the other.
5. Only then add the remaining explicitly selected VMODs one at a time, with the same scope decision for each addition.

This follows `SCOPE.md`: build the abstraction because multiple real VMODs now need it, not because arbitrary future repositories might.

### Phase 4: migrate nightly, trunk, and release workflows

1. Move nightly transaction testing to the isolated reusable workflow.
2. Generalize trunk early-warning CI across configured VMOD main branches.
3. Move release-draft builds to the same isolation model.
4. Enforce required-set completeness at publication time while retaining complete partial-failure reports.

## Verification and failure-injection tests

Do not consider the migration complete based only on YAML review. Run controlled failure cases in a branch or manual workflow:

1. Configure one test VMOD with a nonexistent repository or ref. Confirm the other entries complete.
2. Give one VMOD a malformed manifest or nonexistent manifest path. Confirm it is classified `failed_manifest_validation` without preventing other VMOD invocations.
3. Give one VMOD a deliberately wrong archive digest. Confirm the other entries complete and the failed entry is classified `failed_source_digest`.
4. Inject a package-build failure into one VMOD's Debian row. Confirm its EL9 row and all rows for the other VMODs complete.
5. Inject a smoke or behavior-test failure after packages exist. Confirm artifacts and logs are retained and other rows complete.
6. Fail one engine artifact row. Confirm only consumers of that exact engine/target are reported blocked and unrelated engine rows complete.
7. Confirm the collector runs and displays every requested row in all six cases.
8. Suppress or remove one expected engine, VMOD, source, or target result artifact. Confirm the collector synthesizes `missing_result_record`, displays the affected expected row, and fails.
9. Cancel a run with queued matrix work. Confirm queued work and the job-level collectors respect cancellation rather than keeping the run alive.
10. Confirm the overall required CI check is red after collection.
11. Confirm release assembly refuses a partial required set.
12. Confirm an explicitly allowed experimental partial draft lists every missing VMOD and target.
13. Confirm cachetag release rows resolve `v1.0.0` to `368a01f`, while the trunk early-warning row records the current `main` commit independently.

The strongest acceptance demonstration is a ten-entry fixture in which one VMOD fails at source verification and nine reach their final test stage. The run must contain nine successful result artifacts, one classified failure record, a complete summary, and an overall failed conclusion without cancelled VMOD entries.

## Acceptance criteria

The work is complete when:

- one VMOD source failure cannot prevent another VMOD source or target job from starting;
- VMOD and target matrices both set `fail-fast: false`;
- downstream jobs that must observe a failed aggregate dependency use `if: ${{ !cancelled() }}` and handle missing row-specific artifacts locally;
- global validation performs no VMOD remote checkout or digest verification;
- Vinyl and Varnish packages are shared by exact engine/target identity rather than rebuilt implicitly inside one VMOD-specific lane;
- every dependency artifact is addressed by a stable logical row key and carries separately verified resolved-identity metadata;
- every expected engine row, VMOD invocation, source row, and valid manifest lane either produces a machine-readable outcome or is synthesized by the collector as missing execution evidence;
- summary jobs run after ordinary failures and respect intentional cancellation;
- any step-local `continue-on-error` used to reach result generation is followed by an explicit classified failure, so required failures are not masked;
- required release publication remains all-or-nothing by default;
- release and trunk VMOD source channels are explicit and cannot be confused with the Vinyl/Varnish engine channel;
- a live failure-injection run proves that one failing VMOD leaves the other nine building and testing.

## Non-goals

- Do not vendor VMOD source archives to achieve availability.
- Do not build a general repository-discovery or arbitrary third-party packaging service.
- Do not create a blind all-VMOD-by-all-engine Cartesian product.
- Do not make CI green when a required package failed.
- Do not publish partial stable cohorts.
- Do not replace managed APT/RPM publication services with custom distribution infrastructure.
- Do not guarantee that VMODs can be tested when their exact shared Vinyl or Varnish runtime/development artifact did not build.
