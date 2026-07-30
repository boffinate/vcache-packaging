# Auto-prepared re-pin branch and pull request

Date: 2026-07-30

Status: implemented, unmerged. Authorized by the maintainer on 2026-07-30, having been recorded earlier the same day in the [publication-authority decision](20260730_1635_note_publication-authority-decision.md) as a desirable next increment that was deliberately not built.

## What was built

The next link in the chain the publication-authority decision defines: **detect → verify → notify → prepare**. When `tools/upstream_watch.py` observes a new stable tag on a pinned row, the scheduled trunk-early-warning workflow now opens a branch `auto-release/<vmod>-<tag>` and a pull request carrying the recorded re-pin — tag name, peeled commit, archive digest — for one-click human review.

It **publishes nothing**. It builds no package, merges nothing, writes no evidence file, and touches no release. The manual publication gate is untouched: what the job produces is a proposal built from an observation. The division of labour recorded in the decision note is unchanged — automation owns detection, notification and unattended trunk measurement; humans own re-pins and all publication. Preparing a diff for a human to accept or reject is not owning the re-pin.

New and changed pieces:

- `tools/repin_prepare.py` — the selftested core. `eligibility` classifies every candidate the watcher found; `plan` derives what one eligible candidate's re-pin would record; `apply` rewrites exactly those fields; `pr-body` renders the pull request; `issue-lookup` resolves the interlock's dedupe key. `tools/repin_prepare_selftest.py` carries its battery and runs under the `ci_matrix.py selftest` umbrella with the other three tools.
- `tools/upstream_watch.py` — one addition, `--report PATH`, writing the whole `upstream-watch-report/v1` document, plus a `repin_candidates` gate output. No existing output changed.
- `scripts/ci/prepare-repin.sh` — the part that cannot be selftested: `git`, `curl`, `gh`. Every decision it needs is answered by `repin_prepare.py`.
- `.github/workflows/trunk-early-warning.yml` — a `prepare-repin` job, `needs: [gate, notify]`, gated on `repin_candidates` being non-empty.

## Eligibility is machine-readable, and today it selects one row

The scope the maintainer set is patch-free pinned VMOD rows. That is expressed as a set of questions asked of the registry rather than as a list of VMOD ids, so a manifest or overlay change moves a row in or out of scope without anybody remembering to edit the tool. A candidate is refused if any of these hold, and **every** applicable reason is reported — not just the first:

| test | why | who it excludes today |
| --- | --- | --- |
| the row is an engine row | the Vinyl release pin has no single machine-readable home; it spans two lane pin files, a cohort manifest and the watcher's own constants ([release-automation plan §2.3](20260730_1414_plan_release-automation.md)) | `vinyl-release` |
| the overlay declares a non-empty `patches` list | a reviewed patch is pinned by digest against **one** tag. Moving the tag obliges a human to re-derive it, re-read it against the new tree and record a new `reviewed_against` — an obligation `SCOPE.md` places on a person and `vmod_recipe.py` hard-refuses to skip | `redis` |
| a cohort manifest records `<vmod>.version` | `release_tool.py validate` cross-checks that value against `vmods.<id>.package.upstream_version` in every target manifest of the cohort, so the pin and recorded evidence move together | `cachetag` |
| the manifest records no `archive_url`, or the overlay declares `method: derived-git-tag` | a derived archive has to be derived and re-digested with its own reproducibility record; there is nothing to fetch | `cachetag`, `redis` |
| the tag→version mapping or the archive-URL substitution is ambiguous | the version has to appear exactly once in the pinned tag, and the pinned version exactly once in the recorded URL, or a machine picking a reading is how a package ends up named after the wrong number | nobody today |
| anything structurally surprising — no manifest, a manifest that does not validate, a report whose pinned tag is not the registry's | the report and the registry disagreeing is a finding, not something to work around | nobody today |

So `dict` is the one row prepared automatically today, and that is the honest state rather than a limitation to design around. The mechanism generalises as rows gain published archives; nothing about it is dict-specific.

### The cohort-coupling refusal was discovered while building this, and it excludes cachetag

The brief for this increment assumed cachetag would be eligible (no overlay, therefore no patches) and that a cachetag re-pin would edit `registry/vmods/cachetag.yml` plus the `cachetag:` version block in `registry/cohorts/vinyl-9.0.1-ac4f719c16f4.yml`. Measured on the host: bumping that cohort block makes `release_tool.py validate` fail with two errors, because `registry/targets/vinyl-9.0.1-ac4f719c16f4/{debian-13-amd64,el9-x86_64}.yml` record `vmods.cachetag.package.upstream_version: 1.0.1` and `tools/manifest.py` cross-checks the two. Making that pass means editing recorded target evidence, which this automation must never do — so the coupled cachetag re-pin is refused, loudly, rather than prepared as a pull request that is red by construction.

Editing only `registry/vmods/cachetag.yml` and leaving the cohort alone does pass validate, and that was rejected: it would leave CI building 1.0.2 while the cohort claims 1.0.1's evidence, with no check anywhere that notices. A loud refusal beats a silent inconsistency.

The refusal is detected from the tree (does any cohort manifest carry a `<vmod>:` block with a `version`?), not hardcoded, so it disappears by itself if the coupling ever does.

## What the automated re-pin records, and what it will not touch

Exactly what a deliberate one records ([plan §2.1](20260730_1414_plan_release-automation.md)): the tag name, the tag's peeled commit as re-peeled by the job, and the sha256 of the archive as actually fetched.

Written: `registry/vmods/<id>.yml` `sources.<channel>.{ref,expected_commit,version,archive_url,archive_sha256}`, and — where the overlay carries them — `recipes/vmods/overlays/<id>/overlay.yml` `source.archive.{url,bytes}`. The overlay URL is not an independent statement: `vmod_recipe.py` refuses a recipe whose overlay URL is not the manifest's `archive_url`, so it is the same pin written twice and it moves with the pin. The byte count beside it is a measurement of the archive the job just fetched, recorded for the same reason the digest is.

Never written, and `repin_prepare.py` refuses the paths outright rather than merely avoiding them: anything under `registry/targets/`, `registry/cohorts/`, `registry/distro-native/`, or `*/transactions/expected/`. Those carry measured outcomes, and a measurement that was not measured is worse than a missing one. The pull request's checklist tells the human that they move with the release evidence.

The refusal normalizes the path before testing it, and the caller then opens the normalized spelling. A raw prefix test is only as strong as the spelling it is handed — `./registry/targets/x.yml`, `registry//targets/x.yml` and `registry/./targets/x.yml` all name the same file and none of them *starts with* `registry/targets/`, while `root / relative` resolves every one of them to the real evidence file. `..` and absolute paths are refused outright rather than normalized: neither has an innocent reading in a plan. Each of those spellings is in the selftest's forged-plan battery, so the claim "however a plan is worded" is tested rather than asserted.

Deliberately **not** written either, and on the checklist instead:

- the overlay's `revision`, which its own comment says to bump on any change to it, and `package.revision`, whose reset policy on an upstream version bump is a judgement (`registry/README.md`). Both are reviewed-data judgements; a bumped integer with no entry in the human-authored rationale block above it would be worse than an unbumped one.
- the prose. Only value lines are edited — deliberately, so the reviewed comments survive the edit — which means comments describing the old pin (its signature, its byte count, what was verified against which tree and when) are stale in the prepared diff and say so about the wrong release. This is visible and on the checklist rather than hidden by a round-trip through a serializer that would have deleted the reasoning and kept the values.

**A prepared pull request is therefore never mergeable as it stands, and that is the design.** "One-click review" means the maintainer opens one link and sees the whole proposal; it does not mean one click merges it.

## Observed is not tested

[Plan §1.2](20260730_1414_plan_release-automation.md) is the load-bearing contract of this increment: an observation is a trigger to produce evidence, never a substitute for it. Three mechanisms carry it:

1. The rendered pull-request body states it verbatim, and `repin_prepare_selftest.py` asserts the sentence survives. If it ever falls out, a machine-opened pull request starts reading like a claim that the pin works.
2. `apply` cannot write an evidence path, asserted by the same battery against a forged plan.
3. CI evidence is dispatched, not assumed — see below.

## CI evidence: dispatch, not `pull_request`

A pull request opened with `GITHUB_TOKEN` does not trigger `pull_request` workflows. That is GitHub's loop protection and it is not configurable, so a prepared pull request would otherwise sit with no checks at all and look, to a reader, exactly like a change nobody tested. An explicit `gh workflow run` is exempt from that restriction, so the job dispatches `ci.yml` against the branch (`ci.yml` already has `workflow_dispatch`) and links the run in a comment on the pull request.

No new credential. `GH_TOKEN` stays `${{ github.token }}`; this repository deliberately stores zero secrets, and the manual publication gate means automation holds no publication credential at all. The job's four permissions are each used for exactly one action: `contents: write` pushes the branch, `pull-requests: write` opens the pull request, `actions: write` dispatches the run, `issues: write` comments the result on the watcher's issue.

**That is what the code does, not what the token could do**, and the distinction matters. GitHub's scopes are coarse: `contents: write` can push to any branch including `main` and can create a *published* release, and `actions: write` can dispatch any workflow that accepts a dispatch. Nothing in the permission block prevents either. What constrains the behaviour is `scripts/ci/prepare-repin.sh` — which only ever pushes `auto-release/*` and only ever dispatches `ci.yml` — together with the selftests asserting it and `repin_prepare.py`'s refusal to write an evidence path. Those are review-time guarantees, not enforcement.

**Recommended, and not something this change can do for itself:** a branch ruleset protecting `main` (require a pull request, block direct pushes, including for the `github-actions` app) and a tag ruleset protecting the release tags. That turns "the automation never pushes to `main`" from an observation about the current script into something the forge enforces regardless of what any future edit to the script does. Until it exists, the manual publication gate rests on code review of this job.

**One operational prerequisite, also not a credential:** the repository setting "Allow GitHub Actions to create and approve pull requests" must be enabled. If it is not, the branch is still pushed and the failure is reported on the watcher's issue with that setting named as the likely cause.

## Interlock and failure behaviour

One preparation per upstream and tag. Three conditions each stop a second attempt, checked in this order:

1. **the watcher issue is CLOSED** — the maintainer has seen this candidate and handled or declined it; closing the issue is how a candidate is declined;
2. **the branch already exists on the remote**;
3. **a pull request from that branch is open**.

The watcher's per-tag issue remains the durable record, as the decision note requires; the branch and pull request hang off it.

Two more things the interlock is not. It is **not a lock**: it is a check of remote state, so two overlapping runs would both see no branch and no open pull request and both proceed. The job therefore carries a `concurrency` group (not `cancel-in-progress` — cancelling mid-candidate could leave a pushed branch with no pull request and no comment). And it does **not remember attempts**: a failure before the push creates no branch and no pull request, so nothing records that the attempt happened and the next scheduled run tries the same candidate again and comments again. That is stated plainly in the failure comment rather than papered over, and the remedy is the one that already exists — closing the watcher issue declines the candidate and stops the retries. No attempt-state tracking was built; it would be a fourth piece of state to keep true.

Failure behaviour follows [plan §5](20260730_1414_plan_release-automation.md): every candidate-level failure comments on that candidate's issue saying what failed, and the job ends red. Nothing is retried within a run, nothing is deleted. **An unnotified failure is the one forbidden outcome** — the whole detect-verify-notify chain exists because a finding sitting in a green run's log is a finding nobody has. Failures that stop a candidate before anything is pushed: the tag no longer exists; the tag now peels somewhere other than what the watcher observed (the moved-tag condition, never re-pinned automatically); the ancestry check fails; the archive cannot be downloaded; `apply` cannot find a recorded value where the plan said it would be; any host-safe gate fails on the edited tree; the pull-request description cannot be rendered; the branch cannot be committed.

Three cases deserve naming because the obvious implementation gets them wrong:

- **No watcher issue for the candidate** is an error, not a quiet case. This job runs after `notify`, which files one issue per upstream and tag from the same observation, so a missing one means notify did not do its work or the titles have drifted. The candidate stops; nothing is ever linked to `issues/0`. A preparation nobody was told about is precisely what this chain exists to prevent.
- **A failed CI dispatch** is an error even though the branch and pull request exist. A prepared pull request with no evidence run reads, to anybody who opens it, exactly like a change nobody tested. Both comments say plainly that CI was not started and has to be started by hand.
- **Every step inside the loop is guarded.** Under `set -eu` an unguarded failure aborts the whole script, leaving the remaining candidates unprocessed and nobody's issue commented — the same forbidden outcome by a different route.

### The ancestry check

[Plan §1.1(b)](20260730_1414_plan_release-automation.md), implemented as recommended: `git ls-remote` cannot answer reachability, so the job — which is the first point in the chain with a clone — requires the candidate tag's peeled commit to be an ancestor of the upstream's default branch, via a filtered bare clone and `git merge-base --is-ancestor`. A failure stops that candidate and comments; it never becomes a pull request. A tag on a commit outside the published history is a supply-chain-relevant anomaly, and this converts it into a loud stop rather than a well-evidenced preparation of the wrong thing.

## Deferred, deliberately

- **Automatic re-pin of anything.** Nothing is merged, and no pin moves on `main` without a human.
- **The engine row.** Blocked on the single machine-readable engine-pin home (plan §2.3), which the publication-authority decision downgraded from prerequisite to friction reduction *for humans*. It remains a hard prerequisite for anything automated, which is why the engine row is refused here.
- **cachetag**, until the cohort/target coupling above is resolved or its re-pin is understood as an evidence move rather than a pin move.
- **redis**, permanently as far as this mechanism is concerned: the patch re-review is a human obligation and the derived archive needs its own reproducibility record.
- **Derived archives** generally. `plan` refuses them rather than carrying dead code for a path nothing can reach today.
- **Soak delay** — moot under the manual gate; the human is the soak.
- **Any publication step.** Unchanged and out of scope.

## Verification

Host-safe only, as the runbook requires — this repository's own stdlib selftests and validators, no build, no container, no network:

- `python3 tools/release_tool.py selftest`
- `python3 tools/ci_matrix.py selftest` (now umbrellas `repin_prepare_selftest` alongside the recipe generator's and the watcher's)
- `python3 tools/release_tool.py validate`
- `python3 tools/upstream_watch.py selftest`
- `sh -n` and `bash -n` on `scripts/ci/prepare-repin.sh`
- an end-to-end dry run of `eligibility` → `plan` → `apply` → `pr-body` against a synthetic report, applied to a throwaway copy of the tree, with the three host-safe gates re-run on the edited copy
- rehearsals of the script itself against throwaway checkouts with a stubbed `gh`, covering the loop, the interlock, the absent-issue error, the work-directory containment guard, and the tree-reset defect below

### The tree reset deletes untracked files, and it deleted the job's own input

Found in adversarial review of the first draft, and worth recording because the shape of the bug is more general than the instance. The script resets the working tree between candidates with `git checkout --force` and `git clean -fd`, so that one candidate's edits cannot leak into the next. `git clean -fd` deletes every **untracked** file under the checkout — and the workflow was downloading the watcher's report artifact to `watch-report/` inside the workspace. The first reset deleted it, so candidate one would have worked and every candidate after it would have failed with "unreadable watch report". With one eligible row today it would not have shown up at all.

Fixed at both ends, deliberately: the workflow now downloads to `${{ runner.temp }}/watch-report`, **and** the script copies the report into its own work directory before the first reset and reads the copy thereafter, **and** it refuses outright to run with a work directory inside the checkout. The rehearsal reproduces the original setup — report placed untracked inside the checkout — and confirms the run survives its own `git clean` deleting it.

The general rule, now stated in the script: nothing the job needs may live where the tree reset can reach it.

What has **not** been exercised, and cannot be from here: the live job. No branch has been pushed, no pull request opened, no workflow dispatched. The first real exercise rides the next genuine upstream release — the same condition the [publication-authority note](20260730_1635_note_publication-authority-decision.md) records for the plan's "one real new upstream tag observed classified correctly" criterion. No synthetic tag is pushed to any upstream to manufacture one.

## Cross-references

- [Publication-authority decision](20260730_1635_note_publication-authority-decision.md) — the "recorded as a desirable next increment, not implemented" paragraph now carries a pointer here.
- [Release-automation plan](20260730_1414_plan_release-automation.md) — §1.1(b) ancestry, §1.2 observed-is-not-tested, §2.1 what a re-pin records, §2.3 engine-pin home, §5 failure and interlock behaviour.
- `SCOPE.md` — unchanged. This increment publishes nothing and makes no new delivery promise, so it needs no amendment.
