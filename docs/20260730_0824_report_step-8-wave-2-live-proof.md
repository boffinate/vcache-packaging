# Step 8 Wave 2: the live proof of the transaction wiring and the ten-entry fixture

Date: 2026-07-30

Wave 1 ended with a list of things only a live run could prove ([the Wave 1 note](20260730_0748_note_step-8-wave-1-transactions-wiring.md), "What Wave 2/3 must prove live"). Two of them are proved here, and the roadmap's step-8 acceptance gate is met verbatim. Both runs came back the shape they were designed to come back; neither found a defect, which is worth stating plainly rather than implying by omission.

## The run inventory

| Run | Branch | Dispatch | Outcome | What it established |
| --- | --- | --- | --- | --- |
| 30520146411 | `step8-transactions-wiring` | inject=none | **green, all rows reconciled** | the transaction stage is inert at tier `ci`, measured against the recorded package bytes |
| 30521146916 | `step8-fixture` | inject=none | **red exactly as designed** | the roadmap's ten-entry acceptance fixture |

## Run 30520146411: the transaction stage is inert at tier `ci`

Wave 1's equivalence claim was that adding a tier-gated stage to the package rows changes nothing at tier `ci`, because a skipped step's outcome is neither `failure` nor `success` and every classification arm tests for one of those two. That is an argument. This is the measurement.

**The packages are the same packages.** Every Debian artifact in the equivalence contract's scope was compared against the digests recorded in the registry as the evidence of record, and every one is byte-identical:

| VMOD | Version | Files compared |
| --- | --- | --- |
| cachetag | 1.0.1-1 | `.deb`, `-dbgsym.deb`, `.dsc`, `.debian.tar.xz`, `.orig.tar.gz` |
| dict | 1.7-2 | the same five |
| redis | 23.1-1 | the same five |

**What the contract excludes, and why.** `.buildinfo` and `.changes` differ and are outside the comparison. They record the build environment and the upload metadata rather than the package, and they carry a timestamp and the resolved buildroot package list by construction; requiring them to match would be requiring two runs on two runners to have been the same runner.

**EL9 whole-RPM digests all differ.** This is the documented RPM header nondeterminism, not a finding: an RPM's header is signed over fields that include build-host and build-time material, so two builds of identical payload produce different file digests. It is the reason the EL9 half of the equivalence contract has never been a whole-file comparison, and nothing about this wave changed it.

**The skipped steps were confirmed skipped, not absent.** Sampled Debian and EL9 package jobs both show `mismatch fixture (nightly)` and `upgrade-transaction matrix (nightly)` present in the job with conclusion **skipped**. That is the distinction that matters: a step that is present and skipped proves the gate evaluated and declined, where a step that is simply not there would prove only that the YAML was not what we thought it was.

**The trunk-pinned cachetag rows are green and deliberately not digest-compared.** There is no release-cohort evidence of record for a trunk-pinned build to compare against — the trunk cohorts record their own builds — so comparing them would mean inventing a baseline. They are reported as green rows, and that is the whole of the claim.

**Verdict: Wave 1's inertness equivalence criterion holds.**

## Run 30521146916: the ten-entry acceptance fixture

Dispatched on `step8-fixture` with `inject=none` — no injection machinery involved anywhere. The failing entry is a checked-in wrong digest on `fixture4`, so what the run exercises is the **ordinary** source gate refusing **ordinary** wrong bytes.

Criterion by criterion, against [roadmap §8](20260728_0916_roadmap_outstanding-packaging-work.md)'s gate:

| Roadmap requirement | Measured |
| --- | --- |
| ten entries | discovery count **10**: cachetag, dict, redis, fixture1–fixture7 |
| one entry fails source verification | `source/fixture4/release` = **`failed_source_digest`**, "archive digest does not match fb2a86a7…", and `vmod-source-fixture4-release` is genuinely absent from the run's artifacts |
| nine reach their final test stage | every row of the other nine VMODs **PASS**: engines 4/4, cachetag's four package rows plus its trunk harness reported "skip — no lane for this tier", dict and redis two each, the six healthy aliases two each |
| the summary reconciles all ten | `reconciled.json`: expected **46**, passed **43**, failed **3**, **missing 0**, not_selected 1, **unexpected []**, 47 rows total |
| the workflow fails | red: four failing jobs — `fixture4`'s source, its two target rows, and the collector exiting 1 by design |
| without cancelling the nine | **zero cancelled jobs**, out of 80: 56 success, 20 skipped, 4 failure |

`fixture4`'s two package rows are `blocked_by_vmod_source` with the detail "source artifact vmod-source-fixture4-release was not available" — the classification the plan asks for, naming the cause, rather than an unclassified download error or a cancellation.

**The three failures are exactly the three expected failures.** 46 expected rows, 43 passed, 3 failed, nothing missing and nothing unexpected. A blast radius of one source row and its own two consumers is the isolation property the whole graph exists for, demonstrated at ten entries rather than at three.

### Run cost

18m09s wall, roughly 86 aggregate job-minutes across 80 jobs. `ci.yml`'s VMOD matrix is `max-parallel: 4`, a cost control rather than a failure control, so ten entries queue in three waves; the wall time is dominated by that rather than by any row. The figure is recorded because "what does a ten-VMOD matrix cost" is a real question for the roadmap's forty-VMOD ambition, and this is the only measurement of it that exists.

## The fixture branch stays

`step8-fixture` is **not merged and will not be**. It stays on the remote as a reusable acceptance rig: the next time the graph changes shape — the Wave 3 migration, an engine-channel filter, a new adapter — the ten-entry run can be re-dispatched from it without rebuilding the fixture.

Three independent mechanisms keep it out of a release, and they are described in [the fixture-branch note](20260730_0812_note_step-8-wave-2-fixture-branch.md), which lives on that branch: `SCOPE.md` still selects three VMODs, `--require-releasable` refuses the release cohort while the fourteen `pending` alias entries exist, and the two selftest modules carry a `NEVER MERGE` block naming seven ids that exist nowhere on main.

That note also records two findings from building the rig, both left open for the maintainer rather than worked around:

- **a VMOD id cannot contain a hyphen.** The catalog's regexes allow one and the restricted parser's key regex does not, and the per-target evidence map is keyed by VMOD id — so the usable id space is the intersection `^[a-z][a-z0-9]*$`, which no single regex in the tree states. A hyphenated id passes `check-catalog` and then makes every target manifest in its cohort unparseable.
- **the fixture adds one selected invocation row per alias at every tier**, because `vmod_rows` marks invocation rows selected unconditionally. No lane row of any alias is selected outside `ci`, which was the property that mattered; the invocation rows behave exactly as `vmod/dict` already does in the trunk ledger.

## What is now settled, and what is not

Settled by these two runs: transactions are a stage of the package row, they are provably inert at `ci`, and the isolated graph reconciles ten entries with one deliberate failure and no collateral damage.

Not settled, and deliberately so:

- **No verdict flipped.** dict's and redis's `upgrade_transactions` entries remain `not-applicable` in both release-cohort target manifests. Neither run executed a transaction matrix — 30520146411 skipped the stage by design and 30521146916 is a `ci`-tier fixture — so there is nothing measured to record. The flip is Wave 3 work and happens only from a run that actually ran the matrix, with the identity cross-check the Wave 1 note specifies.
- **The scheduled-workflow migration has been reshaped.** The maintainer's direction of 2026-07-30 changes what Wave 3 builds, before it is built: see [the maintainer decision record](20260730_0826_note_step-8-maintainer-decisions.md). In particular the transaction matrix does not run on any schedule, which retires the "nightly transactions" framing the Wave 1 note was written against; the tier-gated stage itself is unaffected, because what changes is who dispatches it, not what it does.
