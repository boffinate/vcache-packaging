# Step 8 Wave 1: upgrade transactions as a stage of the package row

Date: 2026-07-30

Status: **Implemented; code only, verified as far as the host-safe battery reaches.** The generalised scripts, the workflow wiring, the catalog and the tooling are complete and green on everything that can be run on a host: both selftest batteries, both validation modes, `bash -n` on every touched script, and a byte-identity diff of the `ci`, `release` and `trunk` ledgers against `main`. **Nothing was built and no container was started**, which is the workspace rule, so no transaction outcome in this wave is measured. Wave 2 measures; Wave 3 migrates and flips.

Branch: `step8-transactions-wiring`, off `main` at `8e82fac`.

Related:

- [Step 7 Wave 2 live proof](20260729_2256_report_step-7-wave-2-live-proof.md) — the run whose one deliberate verdict this wave exists to replace: `upgrade_transactions: not-applicable` for the generated VMODs, "until Step 8 wires them into the transactions matrix"
- [Failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md) — Phase 4, "move nightly transaction testing to the isolated reusable workflow", and the `failed_transactions` entry in its failure vocabulary
- [Step 9 Debian transactions](20260724_2300_note_step-9-debian-13-transactions.md) and [EL9 transactions](20260724_2342_note_step-9-el9-transactions.md) — the scenario tables this wave moves rather than rewrites

## The decision: a stage inside the rows, not a second graph

`nightly-transactions.yml` is a parallel workflow that rebuilds the engine and the VMOD from scratch so it has something to run transactions against. That was the right shape when there was one VMOD and one lane script. With three VMODs on two targets it is the wrong shape twice over: it would need a VMOD axis it does not have, and it would rebuild engine packages that the package rows have already built, verified by identity, and installed.

So transactions became a **tier-gated stage of the existing package-target rows** in `vmod-package.yml`: two steps, `mismatch fixture` then `upgrade-transaction matrix`, in both the `target` (upstream recipe, cachetag) and `target-generated` (dict, redis) jobs.

Three consequences, each of which is why this shape was chosen:

- **The row already has everything.** It has the verified engine packages, the built VMOD package, the cohort id read back out of the engine identity, and `VINYL_TRACK` set from its own matrix entry. A separate graph would have to re-establish all four and could get any of them wrong.
- **The failure vocabulary already fits.** A transaction failure is a failure *of that row*, on that target, against that engine — which is exactly what `failed_transactions` in the plan's list means and what the ledger reconciles. A separate workflow's failure belongs to nothing in particular.
- **The engine is built once.** Four engine rows serve every VMOD's nightly rows, the same way they serve the `ci` rows, instead of two more full engine builds per night.

The gate is `inputs.tier == 'nightly' && <previous stage succeeded>`, which is the idiom every other stage in those jobs already uses. **A skipped step's outcome is neither `failure` nor `success`**, and that single fact is the whole equivalence argument: every arm of both classification chains tests for one of those two values, so at tier `ci` the new arms are unreachable, nothing new runs, and nothing existing was reordered.

One deviation from the approved plan, with a reason. The plan put the two steps *before* the checksums. They are *after* them, because `mismatch-fixture.sh` verifies the baseline debs against `dist/debian-13/SHA256SUMS` before deriving a fixture from them, and `build.sh sums` is what writes that file. Placing the fixture first would mean either deleting that verification or fabricating the digest list, and the verification is the reason the fixture is evidence rather than a receipt. The EL9 half has no equivalent constraint — `stage_report` wrote `dist/el9/SHA256SUMS` back at the lint step — but both sit in the same block so a row has one transaction stage rather than two placed differently.

## The env-var contract

The scenario tables did not change. Debian's `s01`–`s16` and the EL9 scenario set are byte-identical, because they are about the **resolver**: the transactions act on the synthetic engine candidates, and what varies per VMOD is only what is asserted about the package installed alongside them.

Four values, plus one path, all defaulted to `libvmod-cachetag`'s so that every existing caller is unchanged:

| Variable | Meaning | Default |
| --- | --- | --- |
| `VMOD_PACKAGE` | the native package name | `libvmod-cachetag` |
| `VMOD_VERSION` | its version in the family's own form; Debian only, since dnf resolves by name | `pins.env`'s `CACHETAG_DEBIAN_VERSION` |
| `VMOD_IMPORT` | the VCL import token | `cachetag` |
| `VMOD_SO` | the installed shared object; Debian only, where the scenario checks the file survived | `libvmod_cachetag.so` |
| `VMOD_PROBE_VCL` | the VCL `vinyld -C` compiles before and after each transaction, as a path **inside the container** | Debian `/stage/probe-cachetag.vcl`, EL9 `/recipes/smoke/smoke.vcl` |

And one on the host drivers:

| Variable | Meaning | Default |
| --- | --- | --- |
| `TXN_OUT_DIR` | the directory holding the baseline cohort packages, their `SHA256SUMS`, and where `mismatch/` and the scenario logs go | `dist/debian-13` / `dist/el9` |

Plus `DEB_HOST_ARCH` and `VINYL_VMODDIR`, which the Debian driver still reads from `work/target.txt` when they are unset and now accepts directly, because a staging directory assembled from an engine artifact has no build tree to have written one.

**The probe is a path, not composed text, and that is deliberate.** Both lanes already had a reviewed VCL to default to, and a path survives a `docker -e` where multi-line VCL does not. cachetag's Debian probe moved out of its heredoc into `recipes/debian-13/container/probe-cachetag.vcl` **byte for byte** — verified by diff against the heredoc it replaces — and the EL9 default is still the lane's own installed-package smoke VCL. An **explicitly empty** path is a caller's decision and selects a composed bare-`import` probe; that is what a generated VMOD passes, because compiling an import is what makes the engine load the shared object and its VCC-generated symbols, which is the whole of what the probe asks, and it is all a generated VMOD can promise without a hand-written VCL body of its own.

**`VMOD_IMPORT` is derived, never declared.** The engine resolves `import X` by loading `libvmod_X.so` from `vmod_path`, so the token *is* the object's stem. `vmod_recipe.py` derives it from `payload.vmod_object` into the model and out through `lane-env`, and refuses an object that is not `libvmod_<name>.so` — an overlay declaring one would be describing a payload no VCL could import. A second declaration could only ever disagree with the first, and the disagreement would surface as a probe VCL that fails to compile for a VMOD that is installed and working perfectly.

**One recorded-shape change.** The Debian result block's `cachetag=` key and `SUMMARY.tsv`'s `cachetag` column header are now `vmod`. Keeping the old name in a generalised script would have been a lie on a dict or redis row. The values, the column count and the outcome vocabulary are unchanged.

### How a generated row reaches the same scripts

`scripts/ci/vmod/transactions.sh`, four stages (`fixture-deb`, `matrix-deb`, `fixture-rpm`, `matrix-rpm`), mirroring `run.sh`'s shape. It does two things and no more:

1. **stages one package directory** at `lane/txn` in the layout the recipe scripts read — `*.deb` at the top level for Debian, `packages/*.rpm` for EL9, plus a `SHA256SUMS` written over exactly the files staged. A generated row has its package in `lane/out` and the engine's in `lane/engine`; there is no `dist/` tree to point at.
2. **reads the five VMOD values out of `vmod_recipe.py lane-env`** — the same model the recipe was rendered from, and the same command `run.sh` already uses for the verify stage. Nothing about any VMOD is named in a workflow expression.

The engine facts — baseline version, strict ABI, cohort id, and the derived synthetic candidate versions — are deliberately **not** passed. The recipe scripts read them from their own lane pin files, dispatching on `VINYL_TRACK`, which is the same reader `scripts/ci/engine-identity.sh` used when this row verified the engine artifact it is about to test against.

## `failed_transactions` acquires a producer

It has been in `STATUSES` since Phase 1, because the plan's failure list names it, with no job able to emit it — the same latent lie the verify-stage marker exists to avoid, and one the Step 7 Wave 2 audit called out for the four verification statuses.

Three arms per job now:

- the fixture failed → `failed_transactions`, "the synthetic mismatched engine candidates could not be built". Classified as a transaction failure rather than as infrastructure because the fixture exists only to be the candidate the scenarios resolve against: a row without one has no transaction evidence, which is precisely what the status says.
- the matrix failed → `failed_transactions`.
- **a nightly row reached its checksums and then ran no transactions at all** → `failed_transactions`. A tier-gated stage has exactly the shape of Wave B's three inert injections — a flag reaching a job nothing read, producing a green run that looked like a demonstration — and a nightly row must not report `passed` for evidence it does not have.

The self-tests read all three properties out of the workflow file rather than restating them: every transaction step is tier-gated, carries `continue-on-error: true` so its row still reaches its result record, and is read by a classification arm.

## The nightly ledger

All four package lanes claim `nightly`: cachetag on both `vinyl-release` and `vinyl-trunk-pinned`, dict and redis on `vinyl-release`. `cachetag.yml`'s comment used to justify nightly's absence with "claiming a nightly row here would put a row in the expected ledger that nothing produces". That stopped being true, so it now says what is actually still missing.

**The nightly ledger is the ci ledger.** Same 19 rows, 18 selected, same four shared engine rows, same artifact names — because the transaction matrix became a stage inside the rows rather than a second graph. `python3 tools/ci_matrix.py ledger --tier nightly`:

```text
engine          engine/vinyl-release/debian-13-amd64
engine          engine/vinyl-release/el9-x86_64
engine          engine/vinyl-trunk-pinned/debian-13-amd64
engine          engine/vinyl-trunk-pinned/el9-x86_64
invocation      vmod/cachetag
source          source/cachetag/release
package-target  target/cachetag/release/vinyl-release/debian-13-amd64
package-target  target/cachetag/release/vinyl-release/el9-x86_64
package-target  target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64
package-target  target/cachetag/release/vinyl-trunk-pinned/el9-x86_64
source-harness  harness/cachetag/trunk/vinyl-trunk-head        (not selected)
invocation      vmod/dict
source          source/dict/release
package-target  target/dict/release/vinyl-release/debian-13-amd64
package-target  target/dict/release/vinyl-release/el9-x86_64
invocation      vmod/redis
source          source/redis/release
package-target  target/redis/release/vinyl-release/debian-13-amd64
package-target  target/redis/release/vinyl-release/el9-x86_64
```

**Nothing reconciles `--tier nightly` yet.** `nightly-transactions.yml` still runs its own graph and is untouched by this wave; Wave 3 migrates it onto this manifest and consumes these rows. Until then the nightly ledger describes work no scheduled workflow has been pointed at, which is a different and much smaller problem than the one the old comment described: a producer that exists but is not called, rather than a row nothing could ever produce.

### The one tier-dependent value

Everything in `ci_matrix.py` was already tier-generic — `TIERS`, the lane schema, `vmod_rows`, `engine_rows`, `ledger`, `reconcile` — and that was verified rather than assumed. The exception is the package row's job timeout. A nightly row runs sixteen throwaway scenario containers on Debian and nineteen on EL9 after everything a `ci` row does, which the 35- and 30-minute budgets would kill. `NIGHTLY_TRANSACTION_MINUTES = 110` is added to a **package** row's budget at that tier and to nothing else: `nightly-transactions.yml`'s own 180-minute figure for the same work, minus the engine half a row no longer builds. One number rather than a per-target column, because the two matrices are the same size to within three containers and a second column would be two things to keep true where the evidence supports one. Like every other timeout in that table it is a "something has hung" guard, not a target, and the first migrated nightly run is what will measure the real cost.

Engine rows keep `engine_timeout_minutes` at every tier. They build the same engine whatever the tier asks for, they are shared by every consumer, and inflating their guard would delay every consumer's failure report.

## What this wave deliberately does NOT do

- **No `nightly-transactions.yml` migration.** It is not touched. Neither are `trunk-vmod-ci.yml` or `release-draft.yml`. Those move in Wave 3, and until they do the nightly transaction evidence still comes from the old graph.
- **No verdict flips.** Not one byte of recorded evidence changed. The dict and redis `upgrade_transactions: not-applicable` entries in `registry/targets/vinyl-9.0.1-ac4f719c16f4/*.yml` stay exactly as they are. **A flip happens only after the first green migrated nightly run, and the commit that flips it names that run id** — the same rule every recorded value in this repository already follows.
- **No measurement of any kind.** Nothing here was built, no container was started, no package was installed. Every claim above is about code and about tool output, and is labelled as such.
- **No change at tier `ci`.** Asserted mechanically: the `ci`, `release` and `trunk` ledgers are byte-identical to their output at `8e82fac`.

## What Wave 2/3 must prove live

1. **The transactions steps are provably inert at tier `ci`.** A full `ci` run on this branch, and its package artifacts compared against the previous green run's: same package digests, same `SHA256SUMS`, same row statuses. Not "the run went green" — the packages have to be the same packages.
2. **The generalised scripts with cachetag's defaults produce a scenario-outcome-identical `SUMMARY.tsv`.** Run the cachetag nightly rows and diff the outcome column against step 9's recorded matrix, scenario by scenario. The `cachetag` → `vmod` column rename means it is not a byte diff; the outcomes, the exit codes and the `WARNING-REQUIRED` set must be identical. Any difference is a defect in the parameterisation, not a finding about apt or dnf.
3. **dict and redis transactions are green on both targets.** Four rows that have never run. Two things are new to them specifically and are the first places to look: the composed bare-`import` probe, and whether the Debian scenario base image's pre-resolved dependency set covers a VMOD package the engine's own dependencies do not (it should not matter — apt installs from the local repository in each scenario — but it is untested).
4. **The verdict flip, with an identity cross-check.** Before any `upgrade_transactions` verdict moves from `not-applicable` to `pass`:
   - the run's engine identity resolves to cohort `vinyl-9.0.1-ac4f719c16f4`;
   - the VMOD package revisions are dict `1.7` revision 2 and redis `23.1` revision 1, the ones the recorded evidence describes;
   - the Debian artifact digests byte-match the artifacts already recorded in the target manifests.

   **Drift on any of the three is a stop, not a note.** A transaction verdict recorded against packages that are not the recorded packages is evidence about something the release does not contain.

## Verification performed on the host

| Check | Result |
| --- | --- |
| `python3 tools/release_tool.py selftest` | 160/160 |
| `python3 tools/ci_matrix.py selftest` (chains `vmod_recipe`) | 262/262 and 218/218 |
| `python3 tools/release_tool.py validate` | 10 manifests valid |
| `python3 tools/release_tool.py validate --require-releasable` | 10 manifests valid |
| `ledger --tier ci` / `--tier release` / `--tier trunk` vs `8e82fac` | **byte-identical, all three** |
| `ledger --tier nightly` | the 19 rows above |
| `bash -n` / `sh -n`, every touched script | clean |
| `probe-cachetag.vcl` vs the heredoc it replaces | **identical** |
| workflow YAML | re-read; no tabs, no trailing whitespace, step indentation consistent, six new step ids unique. **No parser was run**: PyYAML is not installed and installing it is forbidden. `actionlint` in a container is Wave 2's job. |

Nothing in this table is evidence that a transaction works. It is evidence that the code is internally consistent and that the `ci` tier did not move.
