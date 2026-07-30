# Step 8 Wave 3d: the deliberate release-transactions dispatch

Date: 2026-07-30

Status: **Implemented; host-safe verification complete, nothing dispatched.** All batteries green, both validation modes green, all four ledgers byte-identical to `31aacbc`, every extracted `run:` block shell-parsed, and the cross-check script exercised on the host against the real recorded evidence in all three of its outcomes. **The one deliberate dispatch has not been made** — that is the maintainer's call, and it is what produces the evidence the verdicts wait on.

Branch: `step8-wave3d-release-transactions`, off `main` at `31aacbc`.

Related: [maintainer decisions, 2026-07-30](20260730_0826_note_step-8-maintainer-decisions.md) — decision (c), which this implements; [Wave 1](20260730_0748_note_step-8-wave-1-transactions-wiring.md), whose tier-gated stage this dispatches; [Wave 2 live proof](20260730_0824_report_step-8-wave-2-live-proof.md), whose equivalence contract the cross-check reuses; [the transaction-matrix overview](20260730_0825_note_transaction-matrix-overview.md); [Step 9 report](20260724_2348_report_step-9-transaction-safety.md), the comparison baseline.

## The workflow

`.github/workflows/release-transactions.yml`, `workflow_dispatch` only, one input: the cohort whose recorded packages the run must reproduce (default `vinyl-9.0.1-ac4f719c16f4`).

| Job | What it does |
| --- | --- |
| `structural-validation` | the four tooling gates, **plus `--require-releasable`** |
| `discover-vmods` | every VMOD |
| `discover-engines` | `engine-matrix --tier transactions` — **two** rows, both `vinyl-release` |
| `engine` | `ci.yml`'s engine job verbatim, minus the injections |
| `vmods` | `vmod-package.yml` at `tier: transactions` — the Wave 1 stages fire |
| `collect` | `reconcile --tier transactions` |
| `identity-crosscheck` | did this run build the recorded packages |

The graph is `ci.yml`'s. That is not laziness: the tier decides what each row **runs**, not which rows exist, so `transactions` selects exactly what `release` selects and the only difference from a `ci` run is the mismatch fixture and the transaction matrix inside each package row. A second, subtly different graph would be a second thing to keep true.

Two deliberate differences from `ci.yml`:

- **`--require-releasable` is a gate here.** This run exists to measure a cohort whose evidence is complete; a cohort carrying a `pending` VMOD has nothing to cross-check against.
- **No `inject` input.** The injections exist to prove failure isolation, which `ci.yml` and the ten-entry fixture already do. Here they could only produce a deliberately broken measurement of a real release cohort, which is the one thing this workflow must never produce.

### Why dispatch-only

The absent `cron` is the policy, not an omission. The matrix answers a question about a **published** upgrade path — what apt and dnf do when offered a newer Vinyl that would break the VMODs already installed against the old one — and neither a trunk snapshot nor a package rebuilt this morning is one. Release artifacts are untouched once built (decision c), so a schedule would either re-measure the same packages forever or rebuild the ones the evidence describes.

It is also the most expensive thing the project runs: six package rows, sixteen throwaway scenario containers per Debian row and nineteen per EL9 row, each installing a cohort from a local repository and running one real package-manager transaction. Affordable a few times a year; not affordable nightly. Both arguments land in the same place.

## The identity cross-check

`scripts/ci/verify-recorded-digests.sh`. Three assertions, each a **stop** rather than a note.

| # | Assertion | Mechanism |
| --- | --- | --- |
| 1 | the engine identity resolves to the dispatched cohort | `cohort_id` out of the run's own `engine-identity.env`, taken from the artifact the package rows already verified against their pin files |
| 2 | every recorded native package was rebuilt under the same name | a Debian filename is `<name>_<version>-<revision>_<arch>.deb` by construction, so "the recorded version and revision are what was built" and "a file with the recorded name exists" are the same statement — and the second needs no parsing |
| 3 | every in-scope Debian artifact byte-matches its recorded digest | SHA-256 over the built file against `registry/targets/vinyl-9.0.1-ac4f719c16f4/debian-13-amd64.yml` |

**EL9 is exempt from (3), measured rather than assumed.** An RPM header is signed over build-host and build-time material, so two builds of an identical payload produce different file digests; Wave 2's equivalence run `30520146411` confirmed it across all six EL9 packages. EL9 is covered by (1) and (2).

**`.buildinfo`, `.changes` and `_source.changes` are out of scope**, for the reason Wave 2 excluded them: they record the build environment and the upload rather than the package, and they carry a timestamp and the resolved buildroot package list by construction. The registry records them for dict and redis because the run that recorded those entries had them — a wider record, not a wider claim.

**A vacuity guard fails the run when nothing was compared.** A cross-check that checks nothing passes, and that is the failure mode a cross-check is most likely to develop.

### Where the recorded side comes from

A new read-only subcommand, `release_tool.py recorded-evidence --cohort … --target … [--vmod …] [--format json|sha256sums]`.

`metadata` **generates**: it computes the names and versions a build should produce, which is the same answer whatever the run did. The cross-check needs the opposite question — *does what this run built match what we published* — and no generator can answer it. So the recorded artifact digests needed a reader, and this is the smallest one: it copies the evidence out, per VMOD, and **compares nothing**. Whoever is checking supplies the observed side, so exactly one place knows how the evidence is shaped. `--format sha256sums` emits `<digest>  <filename>`, which is `sha256sum -c` format, so the output is usable beside the built files without a second parser.

Twelve selftests cover it, including that an unknown VMOD is an error rather than an empty report — silence would read to the cross-check as "nothing to check".

### Exercised on the host

The script was run against the real recorded evidence, in all three outcomes:

- **drift detected**: files planted under the recorded names with wrong content — 14 artifacts compared, 6 correctly skipped as out of scope, every one reported with both digests, exit non-zero;
- **EL9 path**: assertions 1 and 2 run, assertion 3 prints why it does not apply, exit zero;
- **missing files**: every recorded native package reported as recorded-but-not-built.

Assertion 1 resolved `vinyl-9.0.1-ac4f719c16f4` from the live pin files, and assertion 2 read back exactly the identities the brief names: **cachetag 1.0.1 revision 1, dict 1.7 revision 2, redis 23.1 revision 1**.

## What the one deliberate dispatch must prove

1. **dict and redis get their first-ever transaction measurement, on both targets.** Four rows that have never run a transaction matrix. Their registry entries currently say `upgrade_transactions: not-applicable`, and this is the run that replaces that.
2. **cachetag's matrices are scenario-outcome-equivalent to Step 9's.** The comparison is the run's `logs/transactions/SUMMARY.tsv` (Debian, in the `packages-cachetag-release-vinyl-release-debian-13-amd64` artifact) and `mismatch/logs/summary.tsv` (EL9, in `packages-cachetag-release-vinyl-release-el9-x86_64`), against the tables recorded in [the Step 9 report](20260724_2348_report_step-9-transaction-safety.md) and its two lane notes ([Debian](20260724_2300_note_step-9-debian-13-transactions.md), [EL9](20260724_2342_note_step-9-el9-transactions.md)). **Not a byte diff**: Wave 1 renamed the Debian summary's `cachetag` column to `vmod`. The outcome column, the exit codes and the `WARNING-REQUIRED` set must be identical, scenario by scenario. Any difference is a defect in Wave 1's parameterisation, not a finding about apt or dnf.
3. **The two named suspects from the Wave 1 note behave.** They are the only things genuinely new to dict and redis:
   - **the composed bare-`import` probe VCL.** cachetag's probe is its own reviewed file; dict and redis get a probe composed from `VMOD_IMPORT` alone. If `vinyld -C` cannot compile `import dict;` against the installed package, every dict scenario reports `vcl_compile=fail` and the classification collapses — and it would be a defect in the probe, not a transaction finding.
   - **the Debian scenario base image's dependency set.** It carries the *engine's* runtime dependencies. A VMOD package needing a distribution library the engine does not should still install, because apt resolves from the local repository in each scenario, but it is untested and it is where a first-run surprise would come from.
4. **The identity cross-check passes.** If it does not, nothing from the run may be recorded — see below.

## The verdict-flip procedure

The workflow **records nothing**. Recording evidence is a reviewed act, and a workflow that could record its own result would be able to record a result nobody looked at.

After a green dispatch:

1. confirm `identity-crosscheck` passed. **If it did not, stop.** Whatever the run measured, it did not measure the packages the registry describes, so nothing from it is evidence for this cohort. Establish what moved — an input, a pin, a package revision — before dispatching again, and **never** update a recorded digest to make the check pass.
2. read the six `SUMMARY.tsv` / `summary.tsv` tables and compare cachetag's against Step 9's, scenario by scenario.
3. write one commit that flips `upgrade_transactions` from `not-applicable` to the measured verdict for dict and redis in both `registry/targets/vinyl-9.0.1-ac4f719c16f4/*.yml`, **citing this run's id in the field comment**, exactly as every other recorded value in those files cites the run it came from.
4. if any scenario reports `needs_warning=YES` / `WARNING-REQUIRED` for a VMOD that Step 9 did not, that is a finding about the cohort and belongs in a note before the verdict is written, not after.

## Verification performed on the host

| Check | Result |
| --- | --- |
| `ci_matrix.py selftest` | 279/279, chaining `vmod_recipe` 218/218 and `upstream_watch` 62/62 |
| `release_tool.py selftest` | **172/172** (was 160; twelve new `recorded-evidence` checks) |
| `release_tool.py validate` / `--require-releasable` | both green |
| `ledger --tier ci` / `release` / `transactions` / `trunk` vs `31aacbc` | **byte-identical, all four** |
| `ledger --tier transactions` | 17 rows, 14 selected, **2 engine rows** — unchanged |
| `verify-recorded-digests.sh`, three outcomes | drift, EL9 exemption, missing files: all as designed |
| `bash -n` / `sh -n` on the new script | clean |
| every `run:` block of `release-transactions.yml`, extracted and `bash -n` | 12 blocks, 0 errors |

`actionlint` is not installed and was not installed. The workflow was re-read, and the engine job is `ci.yml`'s text rather than a paraphrase of it, which removes the class of error a linter would most likely have caught.
