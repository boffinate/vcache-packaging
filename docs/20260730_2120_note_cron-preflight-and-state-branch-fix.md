# Cron pre-flight: actionlint, a full-path rehearsal, and the state-branch checkout defect

Date: 2026-07-30

Purpose: the first self-deciding scheduled `trunk-early-warning` run is due Monday 2026-08-03 ~03:17 UTC, and the workflow carries three jobs that had never executed (`notify`, `record-observations`, and today's `prepare-repin`), two of which hold write permissions. This note records the pre-flight the maintainer requested before that unattended run: a first-ever actionlint pass over every workflow, and a deliberate un-forced `workflow_dispatch` rehearsal. The rehearsal found one genuine workflow defect and one observation-loss condition, both fixed here; it also caught a real upstream compatibility break, which is the lane doing its job. The [release-automation plan](20260730_1414_plan_release-automation.md) §1.3 named observing the first scheduled decision a Phase 0 validation item; the dispatch half of that validation is now done, and the scheduled half still rides Monday.

## actionlint: first run ever, clean

actionlint 1.7.12 (run from its container image, per the no-host-installs rule), over all five workflows: **0 errors**, shellcheck over `run:` blocks included. This closes open item 9 of the [Step 8 closing report](20260730_1232_report_step-8-closing.md) — "actionlint has never been run in any wave" — with the note that it was re-run after the fixes below, also clean.

## The rehearsal: run 30577653264, un-forced dispatch at 3a48394

The gate decided `run=true` with `changed_vmods=dict redis` and `vinyl_changed=false`: the dict and redis watched trunk branches had moved against the recorded state; Vinyl trunk HEAD (655c988a) had not. `notify=false` — no re-pin candidates, no moved pins, no poison, no fleet candidates.

What this exercised, and what each part proved:

- **`notify` and `prepare-repin` skipped cleanly** on their gating conditions. First live validation of both, including the brand-new prepare job's `repin_candidates != ''` condition on a run where the list was empty.
- **The §1.3 inefficiency, live**: dict/redis branch movement triggered the full ~45-minute engine build to run zero dict/redis harness rows (both lanes are upstream-blocked on Vinyl trunk's non-numeric version). Known, recorded, still not scheduled work.
- **cachetag's source harness went red against Vinyl trunk HEAD**: `fatal error: cache/cache_vinyld.h: No such file or directory` at 655c988a. This is the header break the standing `6d36364cc1` constraint tracks — Vinyl trunk has crossed the header rename, and cachetag's source still includes the old path. The early-warning lane surfaced exactly the class of breakage it was built for. The fix belongs in the sibling `libvmod-cachetag` repository, not here. `reconcile every expected row` correctly went red with it.
- **`advance-state` failed on a defect of its own** — the reason this note exists, below.
- **`record-observations` skipped** because its condition was `advance-state.result == 'skipped'` and advance-state had *failed* — so the run's tag observations were persisted by nobody.

## The defect: state built at the workspace root collides with the state-branch checkout

`advance-state` built the merged state file at `$GITHUB_WORKSPACE/trunk-watch-state.json`, then switched the same workspace to `ci-state/trunk-watch` — a branch that tracks a file of exactly that name. Git refused: "The following untracked working tree files would be overwritten by checkout". The first-run orphan path (`git checkout --orphan`) performs no such overwrite check against the incoming tree, which is why every earlier execution survived: this was the first time the job met an *existing* state branch.

This is the same defect class the pre-merge review caught in `prepare-repin.sh` (an input placed inside the checkout, destroyed by a later git operation on that checkout), now observed live in a third place. The general rule stands: **nothing a job needs may live inside a checkout that a later step resets, cleans, or switches.**

Fixes, in this change:

1. `advance-state` builds the state at `$RUNNER_TEMP/state-to-commit.json` and copies it into the tree only after the branch switch. Nothing untracked with a tracked name exists at checkout time.
2. `record-observations` now also runs when advance-state **failed**, not only when it was skipped. A tag observation is a fact about the remote; it deserves recording regardless of what went wrong with the tested-refs half. The no-race argument is unchanged: `needs` orders the two jobs, and they still never both persist in one run.
3. `tools/repin_prepare_selftest.py` gains `test_the_state_jobs_survive_meeting_an_existing_state_branch`: text assertions that the build path is RUNNER_TEMP, that the copy happens after the switch, and that the observation-loss condition stays widened. Same honesty caveat as the existing workflow assertions: the live path is not exercisable from the host, so these guard the text, and the live proof is the re-dispatch below.

## Consequences for Monday, and the verifying re-dispatch

Run 30577653264 persisted nothing, so the state branch still records the pre-run world. A deliberate re-dispatch after this fix (a) proves the repaired advance-state against the existing state branch — the exact path that failed — and (b) records Vinyl trunk 655c988a as tested and the dict/redis branch shas as seen, so Monday's scheduled run makes its first self-decision from accurate state: quiet if nothing moves, and no re-payment of the 45-minute build to re-learn a known-red sha (advance-on-red is deliberate; the design comment in the job says why).

Expectation for that re-dispatch, recorded before it ran: cachetag's harness fails again at 655c988a (nothing changed upstream), `reconcile` red, run conclusion **failure** — and `advance-state` green with a state-branch push. A red run with an advanced state is the designed outcome, not a contradiction.

The cachetag header break itself is workspace work outside this repository and is not scheduled by this note.
