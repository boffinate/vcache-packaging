# Step 8 Waves 3a and 3b: the tier vocabulary, and live upstream freshness

Date: 2026-07-30

Status: **Implemented; host-safe verification complete, nothing dispatched.** Both selftest batteries green, both validation modes green, the `ci`/`release`/`trunk` ledgers byte-identical to `a95a570`, and one live read-only `git ls-remote` sweep of all four watched remotes. No workflow was dispatched and no branch was merged.

Branch: `step8-wave3a-tier-rename`, off `main` at `a95a570`.

Related: [maintainer decisions, 2026-07-30](20260730_0826_note_step-8-maintainer-decisions.md) — the direction these two waves implement; [Wave 1](20260730_0748_note_step-8-wave-1-transactions-wiring.md); [Wave 2 live proof](20260730_0824_report_step-8-wave-2-live-proof.md); [failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md) Phase 4.

## Wave 3a: the tier vocabulary

| Tier | Trigger | Selected rows today | What a package row runs |
| --- | --- | --- | --- |
| `ci` | every push and pull request | 4 engine, 3 invocation, 3 source, 8 package-target — 18 selected of 19 | build, verify, behaviour |
| `release` | `release-draft.yml` | 2 engine, 3 invocation, 3 source, 6 package-target — 14 selected of 17 | build, verify, behaviour |
| `transactions` | **`workflow_dispatch` only**, deliberately, per release cohort. Its workflow arrives in Wave 3d | identical to `release`: 14 selected of 17 | build, verify, behaviour, **plus the mismatch fixture and the upgrade-transaction matrix** |
| `trunk` | the change-gated early-warning schedule, Wave 3c | 1 source-harness row (cachetag's `main` against Vinyl trunk HEAD) | the VMOD's own source harness; no package |

`transactions` and `release` select **exactly the same rows** and differ only in what each row runs. That is not a coincidence to be tidied away later — it is the design. Wave 1 made the transaction matrix a tier-gated stage *inside* the package row rather than a second graph, so "which rows" and "what those rows do" became two independent questions, and the tier answers the second one.

### Why the rename

`nightly` named a cadence the tier was never going to have. The 2026-07-30 decisions are explicit that **no transaction matrix runs on any schedule**: the matrix answers a question about a *published* upgrade path, and neither a trunk snapshot nor a rebuilt release candidate is one. A tier called `nightly` sitting in the vocabulary is a standing invitation for somebody to wire it to a schedule, and the name would have been the argument for doing it.

`transactions` says what it is. Its dispatch is deliberate, per release cohort, and it is where the dict and redis `upgrade_transactions` verdicts flip in Wave 3d.

### Why tiers and not a channel filter

The revised Wave 3 plan considered a separate engine-channel or change filter for scheduled selection. It was rejected: **tier membership is already the row-selection mechanism**, and every ledger builder in `ci_matrix.py` filters on it and nothing else. A second axis would have to be threaded through `expand`, `engine-matrix`, `ledger` and `reconcile`, and all four have to agree or a gated run reconciles against a ledger that describes different work and reports rows nobody asked for.

The scheduled trunk cadence therefore selects with the tier it already has — `trunk` — and the *change gate* decides whether to invoke the workflow at all, from outside it. That keeps the filter in one place, at the top, where it is one boolean rather than a predicate four builders have to implement identically.

### Why cachetag's trunk-pinned lane dropped the claim rather than renaming it

Three reasons, in order of weight:

1. **A trunk snapshot is not a published upgrade path.** A transaction verdict about one is a measurement of something nobody can install.
2. The trunk-pinned cohort `vinyl-9.0.0-4b7e68292979` already carries recorded cachetag transaction evidence from Step 9.
3. Keeping it would double the cost of every deliberate dispatch — four package rows instead of two per VMOD-target pair — for evidence nobody asked for.

Re-adding it at a future re-pin is a one-line manifest edit. That is why the reasoning is recorded in the lane comment rather than only here.

### The rename was one commit, on purpose

`TIERS`, the seven `if:` gates, the three bash classification arms and the three manifests moved together. A split would have produced the exact failure this project keeps finding: a tier literal nothing can request makes every gate false, the stage is **silently inert forever**, and the run is green because it measured nothing. Wave B's three inert injections were that shape; so was the `inject=dict_build` flag that reached a job nothing read.

A new selftest closes it permanently. `test_every_tier_literal_in_the_workflow_is_a_real_tier` reads every tier literal out of `vmod-package.yml` — both the `if:` expression form and the bash comparison form — and fails unless each is a member of `TIERS`. A future rename that touches one side and not the other cannot land.

## Wave 3b: `tools/upstream_watch.py`

Maintainer decision (f): the freshness signal comes from a **live** check of each VMOD's own repository, not from the survey JSON. The survey is a point-in-time sweep; it is out of date the moment it is written, and a checker reading it would report the state of the world on the day of the sweep.

### The contract

| Behaviour | Rule |
| --- | --- |
| **(a) the pinned tag** | must still peel to `expected_commit`. A moved or missing tag is a **loud failure** — nonzero exit, `::error` in github format, and output text that says not to update the manifest to make it pass. It is **never** a re-pin candidate. |
| **(b) newer tags** | tags sorting above the pin are **re-pin candidates**, surfaced as `::notice` and in the text report, **never acted on**. Computed statelessly against the manifest pin, so the state file never grows a "tags I already mentioned" list to go stale. |
| **(c) trunk branches** | a watched branch head compared against the last-seen sha in the state file. Movement is the **change-gate signal** — decision (a)'s "run that VMOD against trunk even if trunk itself has not moved". |
| **the engine** | Vinyl trunk HEAD, same comparison, the other half of the gate. |

**A re-pin candidate does not gate.** The lane still builds the pinned tag, so nothing about the run's inputs has moved; a new tag is information for a human, not work for a runner. Asserted in the tests, because it is the kind of thing that would otherwise get "helpfully" wired up later.

**Fail-open, deliberately.** A missing, unparseable or wrong-schema state file, and an unreachable remote, all count as changed and the gate says `run=true` — and the report says which, in words. A freshness gate's dangerous failure is skipping work silently; running work that was not needed costs runner minutes and announces itself.

### Stdlib ruling

`git ls-remote` through `subprocess` is the **whole** network surface. No HTTP, no GitHub API, no auth surface, no rate limit — and `registry/vmods/dict.yml` already documents ls-remote as its own verification mechanism, "cheaper than a full clone, needs no host-specific action, and checks the same thing". git is present on the host and on every runner and needs no install step, so the AGENTS.md stdlib rule holds.

Clone URLs come from `ci_matrix.source_facts`, which already owns the `repository` → `https://github.com/<repo>.git` derivation. A second copy here would be a second thing that can disagree with what the lane actually clones; the tests assert the three real URLs come out of the catalog.

### State

`upstream-watch-state/v1`, JSON, one last-seen sha per watched ref plus `trunk_engine_run_id` — reserved, and written by the Wave 3c workflow rather than by this tool, to record which run produced the trunk engine artifacts a later gated run may reuse.

The state file will live on an **orphan branch `ci-state/trunk-watch`**, created at the first live run of the Wave 3c workflow. An orphan branch because this is CI bookkeeping with no business in the history of the packaging tree. **It is not created now**, and the tool never writes to a branch: it reads a state file and writes a state file, and the workflow owns where those live.

### The live sweep

`python3 tools/upstream_watch.py check --format text`, on the host, 2026-07-30:

```text
vinyl-trunk            branch HEAD         655c988a2f079ee458bc64f55f4548862946fe3d ok CHANGED
cachetag/release       tag    v1.0.1       a3897aaccf1d6996c00ee14b2c6e1ddac91ac982 ok
cachetag/trunk         branch main         0e23f6326f7b7770c2422a23096c3b6d0917a6bc ok CHANGED
dict/release           tag    v1.7         784584d272894a39cf995377618aad551a196424 ok
redis/release          tag    9.0-23.1     b6ca669fc9af3399f3845d9d4930683b4e378aa8 ok

vinyl trunk changed : true
vinyl trunk HEAD    : 655c988a2f079ee458bc64f55f4548862946fe3d
changed VMODs       : cachetag
re-pin candidates   : (none)
run                 : true
```

**All three pinned tags still peel to their recorded commits.** No re-pin candidates: nothing in the fleet has published a newer release than the one pinned. The two `CHANGED` marks are the fail-open rule working — this run had no state file, so everything counts as changed, and the report says so.

One incidental finding worth recording: cachetag's `main` is at `0e23f632`, where the failure-isolation plan recorded it at `2c73ba1` on 2026-07-28. It moved, and nothing noticed until something looked. That is the gap this tool exists to close.

## What remains, Waves 3c to 3e

**3c — the scheduled trunk early-warning workflow.** A new `trunk-early-warning.yml` with a gate job that runs `upstream_watch check --format github`, consumes `run`/`vinyl_changed`/`changed_vmods`, and invokes `vmod-package.yml` at tier `trunk` only for what moved. It needs the `ci-state/trunk-watch` orphan branch created and written back, and a source-harness job in `vmod-package.yml`, which does not exist yet. **Engine-artifact reuse across runs is pending a sibling-repo harness flag** — reusing the previous run's trunk engine artifacts rather than rebuilding is what `trunk_engine_run_id` is reserved for, and it needs the harness to accept a prebuilt engine. `nightly-transactions.yml` and `trunk-vmod-ci.yml` retire here.

**3d — the deliberate transactions dispatch.** A `release-transactions.yml` on `workflow_dispatch` only, invoking `vmod-package.yml` at tier `transactions` against one named release cohort, with the **identity cross-check** the Wave 1 note specifies: the engine identity must resolve to the recorded cohort, the package revisions must be the recorded revisions, and the Debian artifact digests must byte-match the recorded artifacts. Drift on any of the three stops the run rather than annotating it. This is where dict's and redis's `upgrade_transactions: not-applicable` verdicts flip, in a commit that names the run id.

**Between 3c and 3d: re-run the ten-entry fixture.** `step8-fixture` is unmerged and reusable for exactly this. 3c changes the graph's shape — a new gate job, a new workflow, a source-harness job — and the fixture is the acceptance test for graph shape.

**3e — release-draft migration.** Move `release-draft.yml` onto the isolated graph and generalise `scripts/ci/release-manifest.sh`, which still assembles from a one-VMOD shape.

## Recorded recommendation: do NOT consolidate transactions per-cohort now

Maintainer decision (e) accepted the per-cohort consolidation proposal **for consideration**. The recommendation from implementing 3a is to **leave it**, for two reasons that only became clear once the tier stopped being a cadence.

**The cost case evaporated.** The proposal was priced against a nightly recurrence: roughly 100-plus containers *per night* collapsing to ~33. But decisions (b) and (c) mean the matrix now runs **once per release cohort, deliberately**. The saving is 100-plus containers per release event — a handful of times a year — against a redesign of what the evidence means. That is a different trade entirely, and it is no longer obviously worth making.

**Coexistence is a new claim, not a cheaper way to make the old one.** Today `upgrade_transactions: pass` on a VMOD means "the resolver's treatment of *this* VMOD under these commands was measured". A consolidated run would assert something strictly larger — that the whole cohort coexists and survives together — and that is a claim the maintainer should select deliberately, with its own design pass: how a per-cohort fact is recorded against each VMOD's evidence entry, and how failure attribution survives ("which VMOD did the resolver sacrifice" is the finding, and it is exactly what a single combined scenario makes harder to read).

Surfaced as follow-up, not built. If the fleet reaches the ~40 VMODs of decision (g) the arithmetic changes again, and it should be re-priced then against the release cadence rather than against a schedule that no longer exists.

## Verification performed on the host

| Check | Result |
| --- | --- |
| `ci_matrix.py selftest` | 267/267, chaining `vmod_recipe` 218/218 and `upstream_watch` 62/62 |
| `release_tool.py selftest` | 160/160 |
| `release_tool.py validate` / `--require-releasable` | both green, 10 manifests |
| `ledger --tier ci` / `--tier release` / `--tier trunk` vs `a95a570` | **byte-identical, all three** |
| `ledger --tier transactions` | 17 rows, 14 selected: 2 engine, 3 invocation, 3 source, 6 package-target; no trunk-pinned row |
| `ledger --tier nightly` | now an argparse error — `invalid choice: 'nightly'` |
| `upstream_watch.py check` live, all four remotes | every pinned tag peels; state round-trip verified (`run=false` on the second pass) |

No shell script was touched in either wave, so there was nothing to `bash -n`.
