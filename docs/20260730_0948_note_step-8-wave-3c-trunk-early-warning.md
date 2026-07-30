# Step 8 Wave 3c: the change-gated trunk early-warning workflow, and a generic source harness

Date: 2026-07-30

Status: **Implemented; host-safe verification complete, nothing dispatched.** All four selftest batteries green, both validation modes green, all four ledgers byte-identical to `f1d0920`, every extracted `run:` block shell-parsed. **No workflow has been dispatched and the state branch does not exist yet** — creating it is the first live run's job.

Branch: `step8-wave3c-trunk-early-warning`, off `main` at `f1d0920`.

Related: [maintainer decisions, 2026-07-30](20260730_0826_note_step-8-maintainer-decisions.md); [Waves 3a and 3b](20260730_0846_note_step-8-wave-3a-3b-tier-rename-and-upstream-watch.md); [two-track policy](20260726_1235_note_two-track-release-and-trunk.md); [the VMOD survey's first sweep](20260726_2014_report_vmod-survey-first-sweep.md), whose harness is the shape promoted here.

## The maintainer's challenge, and what it changed

The first cut of this wave migrated `trunk-vmod-ci.yml`'s job as it stood, which meant the harness step invoked **cachetag's own `scripts/test-with-vinyl-cache.sh`** from the sibling repository. The maintainer asked the obvious question — *why are we using cachetag's harness script at all?* — and it does not survive being asked.

Three reasons, in order of weight:

1. **It does not scale to the fleet.** The roadmap's ambition is ~40 VMODs. A bespoke harness invocation per VMOD is forty cross-repository contracts to keep working, each with its own name, arguments and assumptions, and forty ways for a VMOD's own refactor to break this repository's scheduled workflow.
2. **It creates cross-repository coupling in the wrong direction.** `../libvmod-cachetag`'s Docker harness is authoritative *for cachetag development in cachetag's repository*. This lane is the packaging project's own early-warning measurement, and it should no more depend on that script than the verify container does — and the verify container already runs cachetag's VTCs against the installed package **without invoking anything from cachetag's repository**. The precedent was in the tree.
3. **It wasted the engine build.** `test-with-vinyl-cache.sh` builds Vinyl itself, unconditionally. So would every other VMOD's equivalent. Forty VMODs, forty Vinyl builds, to answer one question about one Vinyl commit.

The generic harness replaces all of it, and it is not a new idea: it is **the survey sweep's proven shape promoted into the production lane**. `survey/harness/build-and-load.sh` already builds arbitrary third-party VMODs against an installed Vinyl development surface — sixty of them, across every autotools dialect in the wild — by discovering rather than declaring. This wave takes that and extends it with the suite run.

**The sibling-repository flag PR is cancelled, not deferred.** There is nothing left for it to unblock.

## Two things called trunk, and only one of them is scheduled

**The pinned trunk snapshot** is a packaging input: `VINYL_TRACK=trunk` selects a fixed Vinyl commit out of the lane pin files, and the trunk-pinned package rows build cachetag against it. Those rows move only when a human re-pins, so scheduling them would re-prove on Thursday exactly what the last pull request proved on Monday. They stay in `ci.yml`, event-driven.

**Vinyl trunk HEAD** is unpinned and moves daily, and the strict VMOD ABI token *is* the Vinyl commit id — so a Vinyl core change breaks VMOD source before any cohort is minted. That is a tripwire, it has to run on a clock because nothing in this repository changes when Vinyl does, and it is the schedule's entire payload.

## The workflow

| Job | When | What it does |
| --- | --- | --- |
| `gate` | always | fetches the state branch, runs `upstream_watch check --format github`, decides `run` |
| `structural-validation` | `run == true` | the four tooling gates |
| `discover-vmods` | `run == true` | every VMOD, by file name |
| `trunk-engine` | `run == true` | clones Vinyl trunk HEAD and builds it **once** into a prefix; uploads `engine-vinyl-trunk-head-prefix` |
| `vmods` | `run == true` | `vmod-package.yml` at tier `trunk`, `fail-fast: false` |
| `collect` | `!cancelled() && run == true` | `reconcile --tier trunk`, and exposes whether the evidence was **complete** |
| `advance-state` | `!cancelled() && run == true && complete == true` | writes the last-seen shas to the orphan branch. The one job with `contents: write` |

Triggers: cron `17 3 * * 1,4` plus `workflow_dispatch` with a `force` boolean that skips the *decision* while keeping the *observation*.

`vmods` depends on `trunk-engine` but **not with ordinary `needs` semantics**: a failed engine build must not skip the VMOD invocations, so every harness row starts and reports `blocked_by_engine_artifact` from its own missing download. That is the rule `ci.yml` already applies to its engine matrix, and it is what makes a broken engine one legible finding instead of a run that silently did nothing.

**`discover-vmods` lists every VMOD, not only the changed ones.** The trunk ledger has a selected invocation row for each of the three, so the collector expects a record from each; dropping one would make its invocation row `missing_result_record` and turn every gated run red. What the gate saves is the expensive half — a VMOD with no trunk lane expands to zero harness rows.

### The shared engine prefix

`scripts/ci/trunk/build-engine.sh`, in the pinned Debian container: bootstrap, `configure --prefix=/opt/vinyl-trunk`, make, make install, tar the installed prefix, write `trunk-engine-identity.env` (engine, resolved Vinyl commit, prefix, `vinylapi` version, vmoddir, run id, built-at). Uploaded as `engine-vinyl-trunk-head-prefix`, **retention 30 days, documented as a build intermediate and not a durability promise** — SCOPE.md is explicit that a CI-derived archive passed between jobs is exactly that.

The build dependencies are the union of two authorities in this repository and neither is guessed: the `Build-Depends` of the audited Vinyl packaging in `upstream/pkg-vinyl-cache/debian/control`, plus the autotools set a git checkout needs and a `make dist` tarball does not, plus `libunwind-dev` because the survey lane image has it and leaving it out silently builds a different daemon.

**The prefix path is load-bearing.** libtool bakes it into the `.la` files, pkg-config into `vinylapi.pc`, and `vmod_abi.h` into the ABI string, so the tarball works at exactly one absolute path. It is tarred rooted at `/` and unpacked at `/`, so the baked paths are right by construction rather than by a relocation step that would have to rewrite three things consistently. The harness verifies the path out of the identity file rather than assuming the two agree.

### The generic harness

`scripts/ci/vmod/container/source-harness.sh`. **The only per-VMOD input is `harness.tests`**, a glob out of the manifest. Everything else is autotools and is discovered:

- the engine surface: `PATH`, `PKG_CONFIG_PATH`, `LD_LIBRARY_PATH` from the prefix, plus `VINYLAPI_DATAROOTDIR` and its three other spellings and `ACLOCAL_PATH` — because many VMODs put `${VINYLAPI_DATAROOTDIR}/aclocal` in `ACLOCAL_AMFLAGS` and an unset variable becomes a bogus `-I /aclocal` that fails naming nothing. The survey sweep learned that across sixty repositories.
- bootstrap: the repository's own `bootstrap` or `autogen.sh` first, `autoreconf -f -i` as the fallback and as the second chance after a failed configure.
- the built module: `find . -path '*/.libs/libvmod_*.so'`, with every containing directory joined into `vmod_path` so a VMOD building more than one module has all of them reachable.
- the suite: `vinyltest -v -k -j1 -t 60 -p vmod_path=<discovered> -p debug=+vclrel` over the declared glob.

The container's four distinct exit codes — build failed, no module produced, glob matched nothing, suite failed — all classify as `failed_source_harness`. They are one finding, *this VMOD's source does not work against today's trunk*, and the log says which.

**`harness.tests` is required, not defaulted.** A glob that matched nothing would run a build, find no cases, and have to invent a verdict; and every default anyone would pick is one VMOD's layout imposed on the next. `GLOB_RE` forbids a leading slash and `..`, and the glob is expanded inside the copied source tree, so a manifest cannot point the harness outside the checkout it was given. A package lane declaring one is refused: its behaviour suite is the installed-package one.

**`failed_source_harness` is deliberately not `failed_behavior`.** A harness row compiles a VMOD's source against unpinned trunk HEAD, which nobody has agreed to support. `failed_behavior` means "the packaged VMOD misbehaved against the engine it was built for", a statement about the package; reusing it would blame the package for tomorrow's engine and send a reader to the wrong repository.

**Both commits are recorded.** SCOPE.md requires a trunk job to record the commit it actually tested; for a harness row what was tested is the *pair*, so `source.commit` is the VMOD's and `source.engine_commit` is Vinyl's, via the new `record --engine-commit`.

The harness matrix entry gained `ref` and `tests`. Both went to the **matrix** and the **record**, not to the ledger row: the ledger is reconciliation input keyed by row key, and widening its harness row would have moved every tier's ledger bytes for values only the running job reads.

## The gate and state contract

**Fail open, toward running.** A missing state branch, a fetch failure, an unparseable or wrong-schema state file, and an unreachable remote all count as changed, and the report says which. A freshness gate's dangerous failure is skipping work that should have run, because that failure is silent.

**A moved pin is the opposite and stops everything.** The watcher exits non-zero, the gate fails, every downstream job skips. The recorded identity and what upstream publishes under that name have diverged.

**The state advances on a red run.** A harness row that failed against a trunk commit is a **learned fact** about that commit; re-testing a known-red sha every cadence re-pays the full cost to learn it again.

**The state does not advance on incomplete evidence.** A row that produced nothing tells us nothing about that sha. `collect` exposes `complete` as a job output computed from `counts.missing`, rather than having `advance-state` re-parse the JSON and become a second reader that could disagree.

**The sha recorded is the one that was TESTED.** The gate observes a HEAD, `trunk-engine` clones one, and trunk moves between the two. Everything downstream uses the engine job's sha — the identity file carries it, the harness rows verify against it, and `advance-state` reads `source.engine_commit` back out of the result records. If two harness rows disagree, trunk moved mid-run and no single commit was covered by every row, so the engine entry is left alone.

**The state lives on the orphan branch `ci-state/trunk-watch`**, created by the first live run with a README explaining itself. Orphan because this is machine-written CI bookkeeping with no business in the packaging tree's history; deleting it is safe, because the watcher fails open.

## What stays out

**Cross-run reuse of the engine prefix, and the gate's `engine_artifact_missing` probe.** In-run sharing is what pays for itself — one build serving every row, which is the fleet economics. Carrying a prefix *between* runs is a pure optimisation of the rarer "a VMOD moved but Vinyl trunk did not" case, and it should be evaluated from real run costs rather than from a guess about them. The state schema reserves `trunk_engine_run_id` and leaves it unfilled.

## The flake, and the fix that came out of it

The Wave 3a tier-rename run `30524142812` failed on attempt 1: the redis Debian row classified `failed_behavior` with `FAIL: vinyltest reported failures`, while the log showed all twenty VTCs passing. One upstream-runner invocation exited non-zero after its own tests were done, most likely Redis fixture teardown, and `scripts/ci/lib/vtc-suite.sh` latched the status without naming the case. Attempt 2 was green.

The suite runner now records each failing case's name and exit status on the per-case driver path and prints them with the failure, and the message carries the pass and skip counts so that "twenty passed and we still failed" reads as the finding it is. On the `none` driver path a single invocation covers the whole ledger and there is nothing to latch, so it says that rather than printing an empty list.

**Semantics are unchanged** — the same runs fail and pass; only the message differs. The cachetag lanes' own suites (`recipes/debian-13/container/stage-vtc-suite.sh`, `recipes/el9/vtc-suite/vtc-suite.sh`) are separate implementations that also run one invocation over their main set, so they have nothing to latch and were left alone.

This does not explain the flake. It makes the next one one glance instead of one log read.

## What the live bring-up must prove

1. **A forced dispatch runs everything and creates the state branch.** `force: true` → gate reports `run=true`; `trunk-engine` builds Vinyl trunk HEAD once and publishes the prefix with its identity; cachetag's harness row unpacks it, builds cachetag from `main` and passes `src/vtc/*.vtc` against the freshly built `.so`; `collect` reconciles four expected rows with zero missing; `advance-state` creates `ci-state/trunk-watch` with the **tested** Vinyl sha.
2. **An immediate un-forced dispatch does nothing.** Run again straight away with `force: false`: the gate reads the state it just wrote, reports `run=false`, every downstream job skips. Expected cost about **one job-minute**, green. This cannot be asserted offline — the state branch has to exist and be readable.
3. **A later real movement gates a full run.** cachetag `main` moved from `2c73ba1` to `0e23f632` while nobody was looking; the next such movement must produce `run=true` naming what moved, and the state must advance afterwards.

A fourth, whenever it happens naturally: a harness failure must classify `failed_source_harness`, the run must go red, and the state must **still** advance.

**The ten-entry fixture is re-run between 3c and 3d.** This wave changes the graph's shape — a gate job, a new workflow, an engine job, a harness job inside the reusable workflow — and `step8-fixture` is unmerged and reusable for exactly that.

## Verification performed on the host

| Check | Result |
| --- | --- |
| `ci_matrix.py selftest` | 279/279, chaining `vmod_recipe` 218/218 and `upstream_watch` 62/62 |
| `release_tool.py selftest` | 160/160 |
| `release_tool.py validate` / `--require-releasable` | both green |
| `ledger --tier ci` / `release` / `transactions` / `trunk` vs `f1d0920` | **byte-identical, all four** |
| `ledger --tier trunk` shape | 15 rows, 4 selected: three invocations plus `harness/cachetag/trunk/vinyl-trunk-head`, the only selected non-invocation row |
| the harness job contains no VMOD-specific logic | asserted in the selftests against the **derived** vocabulary — every catalog id, overlay macro name, fixture extension and driver basename — so a fourth VMOD's id is forbidden the moment its manifest lands. The only literal occurrences of "cachetag" in the whole job are two comment lines citing the verify container as precedent |
| `reconcile --tier trunk` over a synthetic complete-but-red run | expected 4, failed 1, **missing 0** → `complete=true`; the state would advance |
| every `run:` block of both workflows, extracted and `bash -n` | 10 and 34 blocks, 0 syntax errors |
| `bash -n` on `build-engine.sh`, `source-harness.sh`, `vtc-suite.sh` | clean |
| deleted workflows' script references | all thirteen scripts have between 5 and 26 other referrers; nothing orphaned |

`actionlint` is not installed and was not installed. The workflows were re-read instead, with the mechanical checks above standing in for what it would have caught cheaply.
