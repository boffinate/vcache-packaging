# The trunk lane's VTC glob over-matched: Fellow-only tests are not runnable here

Date: 2026-07-30 (late evening; follows [the cron pre-flight note](20260730_2120_note_cron-preflight-and-state-branch-fix.md))

## What run 30583344213 was, and what it found

After cachetag v1.0.2 (the dual-header fix) was tagged and pushed to its repository, the maintainer directed a matrix rerun. Run 30583344213 was the packaging project's first run with real watcher findings, and its notification chain behaved correctly end to end: the gate fired on cachetag's moved trunk branch and new tag, `notify` opened the first real re-pin candidate issue (cachetag v1.0.2), `prepare-repin` executed live for the first time with a non-empty candidate list and correctly refused cachetag as ineligible, and `advance-state` — carrying the pre-flight fix — persisted state against the existing state branch.

The cachetag source harness row still failed, but differently and informatively: **the header fix worked** — cachetag compiled against Vinyl trunk HEAD 655c988a for the first time since the un-branding rename — and the failure moved to the VTC stage, which had **never executed before this run** (every earlier trunk run died at the compile). 24 `cachetag_p*` VTCs failed instantly with `Macro ${libvmod_slash} not found`.

## Root cause: the declared glob includes tests this harness cannot run, by design

The trunk lane declared `harness.tests: "src/vtc/*.vtc"`. cachetag's `src/vtc/` holds 77 VTCs in five families: `c` (16), `r` (7), `pm` (29) — the storage-agnostic suite its own standalone check target runs — plus `p` (24) and `x` (1), which are Fellow-backed persistence and SIGKILL tests importing the slash storage VMOD via `${libvmod_slash}`. The packaging harness builds Vinyl trunk and the VMOD under test, deliberately nothing else, so the Fellow-backed tests can never run here; upstream runs them through its own `test-fellow-with-vinyl-cache.sh` harness. The bare glob was recorded at Step 8 Wave 3c and its over-match stayed invisible behind the header break.

## Fix

- `registry/vmods/cachetag.yml` trunk lane now declares the storage-agnostic families explicitly: `src/vtc/cachetag_c*.vtc src/vtc/cachetag_r*.vtc src/vtc/cachetag_pm*.vtc`. Plain globs cannot say "p but not pm", so the set is positive and family-by-family, mirroring upstream's own standalone suite.
- `tools/ci_matrix.py` `GLOB_RE` widened to accept one or more space-separated relative glob words (each word keeps the old per-word rule: no leading slash). The harness script needed no change — `scripts/ci/vmod/container/source-harness.sh` already expands `$VMOD_TESTS` with shell word-splitting.
- `tools/ci_matrix_selftest.py`: one acceptance (multi-word set) and two refusals (absolute second word; doubled space) added beside the existing harness.tests checks.

The known coverage trade-off from Step 8 is unchanged in kind: trunk coverage runs the declared VTCs, not `make check`, so cachetag's C unit test and its Fellow-backed suites live outside this lane. What changed is that the declaration now states the runnable set truthfully instead of over-claiming and failing.

## Second finding, same first-execution class: the engine's bundled VMODs were unreachable

Forced run 30584237055 (dispatched after the fix above) selected exactly the 52 declared cases — and failed 7 of them (6 `pm`, 1 `r`), each dying in VCL compilation on `import vtc;`. Root cause in `scripts/ci/vmod/container/source-harness.sh`: it ran `vinyltest -p vmod_path=<built module dirs>`, and overriding `vmod_path` **replaces** vinyld's default search path, so the engine prefix's own bundled VMODs (`vtc` for barrier orchestration, `std`, ...) stopped being importable. The engine artifact does ship them (`lib/vinyl-cache/vmods`, `libvmod_vtc` included); the harness just hid them. cachetag's own repo harness never hits this because its `vmod_path` includes both the built module and the prefix. Fixed by appending `pkg-config --variable=vmoddir vinylapi` to the composed `vmod_path`, with a loud refusal if the prefix advertises none. Like the glob over-match, this was invisible until tonight because no run had ever reached the suite.

## Proof

Forced run 30584875328, with both fixes: `HARNESS SUMMARY: 52/52 passed, 0 failed, 0 skipped (vinyltest exit 0)`, and every job in the run green — gate, notify (deduplicated against the open cachetag v1.0.2 issue), prepare-repin (clean ineligibility refusal), trunk engine build, harness, reconcile, advance-state. The first fully green trunk-HEAD measurement since Vinyl's header rename, and the first ever whose VTC stage ran. The un-forced dispatch alongside (run 30584205513) proved the quiet path with standing findings: `run=false`, notify deduplicates, record-observations persists the tags map, whole run green at gate cost.

## Standing consequence for future VMODs

A VMOD's `harness.tests` must declare the subset of its suite runnable in a bare engine-plus-VMOD environment, not "all tests it ships". Any future VMOD whose repository carries tests requiring sibling VMODs, external services, or storage engines needs the same positive family selection — dict's ported-Autotest VTCs and redis's suite (which the packaging harness runs with its Redis servers) are unaffected.
