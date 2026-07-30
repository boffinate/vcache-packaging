# Step 8 Wave 3c: the change-gated trunk early-warning workflow

Date: 2026-07-30

Status: **Implemented; host-safe verification complete, nothing dispatched.** All four selftest batteries green, both validation modes green, all four ledgers byte-identical to `f1d0920`, every extracted `run:` block shell-parsed. **No workflow has been dispatched and the state branch does not exist yet** — creating it is the first live run's job.

Branch: `step8-wave3c-trunk-early-warning`, off `main` at `f1d0920`.

Related: [maintainer decisions, 2026-07-30](20260730_0826_note_step-8-maintainer-decisions.md); [Waves 3a and 3b](20260730_0846_note_step-8-wave-3a-3b-tier-rename-and-upstream-watch.md); [two-track policy](20260726_1235_note_two-track-release-and-trunk.md); [failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md) Phase 4.

## Two things called trunk, and only one of them is scheduled

This is the reconciliation the wave turns on, and it was the thing most likely to be got wrong.

**The pinned trunk snapshot** is a packaging input: `VINYL_TRACK=trunk` selects a fixed Vinyl commit out of the lane pin files, and the trunk-pinned package rows build cachetag against it. Those rows prove the packaging machinery — source assembly, buildroot, dpkg and rpm build, lint, smoke — against an input that **moves only when a human re-pins it**. Scheduling them would re-prove on Thursday exactly what the last pull request proved on Monday. They stay in `ci.yml`, event-driven, and this workflow does not touch them.

**Vinyl trunk HEAD** is unpinned and moves daily, and the strict VMOD ABI token *is* the Vinyl commit id — so a Vinyl core change breaks VMOD source before any cohort is minted. That is a tripwire, it has to run on a clock because nothing in this repository changes when Vinyl does, and it is the schedule's entire payload.

`trunk-early-warning.yml` runs the second and never the first.

## The workflow

| Job | When | What it does |
| --- | --- | --- |
| `gate` | always | fetches the state branch, runs `upstream_watch check --format github`, decides `run` |
| `structural-validation` | `run == true` | the four tooling gates, mirroring `ci.yml`'s |
| `discover-vmods` | `run == true` | every VMOD, by file name |
| `vmods` | `run == true` | `vmod-package.yml` at tier `trunk`, `fail-fast: false`, `max-parallel: 4` |
| `collect` | `!cancelled() && run == true` | `reconcile --tier trunk`, and exposes whether the evidence was **complete** |
| `advance-state` | `!cancelled() && run == true && complete == true` | writes the last-seen shas to the orphan branch. The one job with `contents: write` |

Triggers: cron `17 3 * * 1,4` — Monday and Thursday, off the hour — plus `workflow_dispatch` with a `force` boolean that skips the *decision* while keeping the *observation*.

**`discover-vmods` lists every VMOD, not only the changed ones, and that is not a missed optimisation.** The trunk ledger has a selected invocation row for each of the three, so the collector expects a record from each; dropping one from the matrix would make its invocation row `missing_result_record` and turn every gated run red. What the gate saves is the expensive half — a VMOD with no trunk lane expands to zero harness rows, and its invocation job is a manifest parse.

### The harness job

`vmod-package.yml` gained the `harness` job the tier input description has promised since Phase 1, gated on `harness_count != '0'` so it is inert at every other tier. Its content is `trunk-vmod-ci.yml`'s, migrated rather than rewritten: check the VMOD's default branch out, clone Vinyl trunk HEAD unpinned, build the harness image, run the VMOD's own documented harness.

What it gains is everything that makes it a **row** rather than a standalone job: a classified result record under the artifact name the ledger already expects (`result-cachetag-trunk-vinyl-trunk-head`), both resolved commits recorded as evidence, and a place in the VMOD's summary.

Three things worth stating:

- **`failed_source_harness` is a new status and deliberately not `failed_behavior`.** A harness row compiles a VMOD's own source against unpinned trunk HEAD, which nobody has agreed to support. A failure there is early-warning signal about a Vinyl core change and the first place to look is Vinyl; `failed_behavior` means "the packaged VMOD misbehaved against the engine it was built for", which is a statement about the package. Reusing it would blame the package for tomorrow's engine and send a reader to the wrong repository.
- **Both commits are recorded.** SCOPE.md requires a trunk job to record the commit it actually tested; for a harness row what was tested is the *pair*, so `source.commit` is the VMOD's and `source.engine_commit` is Vinyl's. The record command gained `--engine-commit` for it.
- **A strategy with no documented harness is refused, not guessed.** No generated-recipe VMOD declares a trunk source channel today, so the refusal is unreachable now and becomes a loud classified failure the moment one does. A harness command is a per-VMOD fact and belongs in the overlay; inheriting cachetag's would be a silent wrong answer.

The harness matrix entry gained the ref to check out. It was added to the **matrix** and to the **record**, and deliberately *not* to the ledger row: the ledger is reconciliation input keyed by row key, and widening its harness row would have moved every tier's ledger bytes for a value only the running job reads.

## The gate and state contract

**Fail open, in the direction of running.** A missing state branch, a fetch failure, an unparseable state file, a wrong-schema state file and an unreachable remote all count as changed, and the report says which. A freshness gate's dangerous failure is skipping work that should have run, because that failure is silent; running work that did not need to run costs runner minutes and announces itself.

**A moved pin is the opposite and stops everything.** If a pinned tag no longer peels to its recorded commit, the watcher exits non-zero, the gate job fails, and every downstream job is skipped. The recorded identity and what upstream publishes under that name have diverged, and nothing should be built from it until somebody has established which of the two moved.

**The state advances on a red run.** A harness row that failed against a Vinyl trunk commit is a **learned fact** about that commit. Re-testing a known-red sha every cadence re-pays the full cost — three quarters of an hour of building Vinyl — to learn the same thing again, and the next real movement is what should trigger the next run.

**The state does not advance on incomplete evidence.** If any expected row produced no result record at all, we do not know what happened at that sha, and recording it as seen would mean never finding out. `collect` exposes `complete` as a job output, computed from the reconciled ledger's `counts.missing`, rather than having `advance-state` re-parse the JSON and become a second reader that could disagree.

**The sha recorded is the one that was TESTED.** The gate observes a Vinyl HEAD and the harness rows clone one, and trunk moves between the two. The harness row proceeds with what it resolved, notes the drift in its record's detail, and `advance-state` reads `source.engine_commit` back out of the result record. If two harness rows disagree, trunk moved mid-run and no single commit was covered by every row, so the engine entry is left alone and the next run tests a coherent one.

**The state lives on an orphan branch, `ci-state/trunk-watch`.** Orphan because this is machine-written CI bookkeeping with no business in the history of the packaging tree: it shares no commit with `main`, nothing merges it, and deleting it is safe — the watcher fails open and the next run tests everything. It is created by the first live run, along with a README explaining itself. `advance-state` is the only job with `contents: write` and uses it for one file on one branch.

## The engine-reuse deferral

**No trunk-engine job, and no cross-run engine-artifact reuse, in this wave.** This is a deliberate deviation from the Wave 3 plan.

The VMOD's documented harness — `scripts/test-with-vinyl-cache.sh` in the sibling repository — **unconditionally builds Vinyl itself**. Until a reviewed opt-in flag exists *there* to accept a prebuilt prefix, an engine artifact built here would have no consumer, and producing one would mean building Vinyl twice per run rather than once.

It is also the smaller prize. The allowance saving is in the **change gate**, which skips whole runs; reuse only optimises the rarer "a VMOD moved but Vinyl trunk did not" case, where the engine could in principle be carried over. That arrives with the sibling-repository flag, as a future PR there followed by a small change here. The state schema already reserves `trunk_engine_run_id` for the run that produced a reusable artifact, and it stays unfilled until then.

## The flake, and the fix that came out of it

The Wave 3a tier-rename run `30524142812` failed on attempt 1: the redis Debian row classified `failed_behavior` with `FAIL: vinyltest reported failures`, while the log showed all twenty VTCs passing — twenty separate `0 tests failed... 1 tests passed` lines. One upstream-runner invocation exited non-zero after its own tests were done, most likely Redis fixture teardown, and `scripts/ci/lib/vtc-suite.sh` latched the status without naming the case. Attempt 2 was green.

The suite runner now records each failing case's name and exit status on the per-case driver path and prints them with the failure, and the message carries the pass and skip counts so that "twenty passed and we still failed" reads as the finding it is rather than as a contradiction. On the `none` driver path a single `vinyltest` invocation covers the whole ledger and there is no per-case status to latch, so it says that rather than printing an empty list.

**Semantics are unchanged** — the same runs fail and the same runs pass; only the message differs. The cachetag lanes' own suites (`recipes/debian-13/container/stage-vtc-suite.sh`, `recipes/el9/vtc-suite/vtc-suite.sh`) are separate implementations that also run one invocation over their main set, so they have nothing to latch and were left alone. Noting them here because "fix the shared library" and "there are two other copies of a similar check" is exactly the kind of thing that gets rediscovered later.

This does not explain the flake. It makes the next one one glance instead of one log read.

## What the live bring-up must prove

1. **A forced dispatch runs everything and creates the state branch.** `workflow_dispatch` with `force: true` → gate reports `run=true` with the observed shas, all three VMOD invocations run, cachetag's harness row goes green against trunk HEAD, `collect` reconciles four expected rows with zero missing, and `advance-state` creates `ci-state/trunk-watch` with the **tested** Vinyl sha and cachetag's `main` sha.
2. **An immediate un-forced dispatch does nothing.** Run it again straight away with `force: false`: the gate must read the state it just wrote, report `run=false`, and every downstream job must skip. Expected cost about **one job-minute**, and the run must be green. This is the property the whole design is for, and it cannot be asserted offline — the state branch has to exist and be readable by the gate.
3. **A later real movement gates a full run.** Wait for Vinyl trunk or cachetag `main` to move — it will, `main` moved from `2c73ba1` to `0e23f632` while nobody was looking — and confirm the next scheduled run reports `run=true` naming what moved, and that the state advances to the new sha afterwards.

A fourth, whenever it happens naturally: a harness failure must classify `failed_source_harness`, the run must go red, and the state must **still** advance.

**The ten-entry fixture is re-run between 3c and 3d.** This wave changes the graph's shape — a new gate job, a new workflow, a harness job inside the reusable workflow — and `step8-fixture` is unmerged and reusable for exactly that.

## Verification performed on the host

| Check | Result |
| --- | --- |
| `ci_matrix.py selftest` | 274/274, chaining `vmod_recipe` 218/218 and `upstream_watch` 62/62 |
| `release_tool.py selftest` | 160/160 |
| `release_tool.py validate` / `--require-releasable` | both green |
| `ledger --tier ci` / `release` / `transactions` / `trunk` vs `f1d0920` | **byte-identical, all four** |
| `ledger --tier trunk` shape | 15 rows, 4 selected: three invocations plus `harness/cachetag/trunk/vinyl-trunk-head`, the only selected non-invocation row |
| `reconcile --tier trunk` over a synthetic complete-but-red run | expected 4, failed 1, **missing 0** → `complete=true`, so the state would advance. The designed behaviour, exercised rather than reasoned about |
| every `run:` block of both workflows, extracted and `bash -n` | 8 and 35 blocks, 0 syntax errors |
| `bash -n` / `sh -n` on `vtc-suite.sh` | clean |
| deleted workflows' script references | all thirteen scripts have between 5 and 26 other referrers; nothing orphaned, nothing else deleted |

`actionlint` is not installed and was not installed. The workflows were re-read instead, and the mechanical checks above (no tabs, no trailing whitespace, unique step ids within each job, every `run:` block shell-parsed) stand in for what it would have caught cheaply.
