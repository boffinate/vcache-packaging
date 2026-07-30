# Docs curation: what stayed current, what was archived, what was not deleted

Date: 2026-07-30

Maintainer request: delete outdated documents and decisions, archiving those whose ideas or provenance may matter later. Executed as a curation pass, not a purge: `AGENTS.md`'s diagnostic-log rule values the record of what was tried and what failed, and git history preserves everything anyway, so the axis optimised here is discoverability — `docs/` should read as what is true now plus the evidence trail, with the archive one level down.

## Semantics

- **Kept** — normative, active, cited by `SCOPE.md`/`AGENTS.md`/live code as current reading, a step closing/live-proof report (the evidence trail), or a record of a still-live constraint or decision.
- **Archived** (`docs/archive/`, filenames unchanged) — superseded or historical; retained for ideas and provenance.
- **Deleted** — nothing. No tracked file met the bar of zero residual insight *and* unreferenced. The bulky sweep outputs one might expect to delete (`survey/cache/`, `survey/results/`, `__pycache__/`) were never tracked in git; they are already excluded by `.gitignore` and were left untouched on disk.

All inbound references to archived files from kept files were updated to the `docs/archive/` path (verified by grep: zero dangling references repo-wide). References *between* two archived files were left as-is and still resolve. Known residual breakage, accepted rather than mass-editing history: relative links inside archived files that point at notes still in `docs/` (for example the archived step-6 wave notes linking the kept live-proof report, or archived step-8 wave notes linking the kept maintainer-decisions note) now need a `../` they do not have. `docs/archive/README.md` states this.

## Kept in place (31 + this note)

| File | Reason |
| --- | --- |
| `20260724_2247_report_step-7-8-first-cohort-proven.md` | Closing report for the first-cohort era; evidence trail entry point for the archived lane notes. |
| `20260724_2300_note_step-9-debian-13-transactions.md` | Transaction lane detail still cited as current reading by `recipes/debian-13/README.md` and `recipes/debian-13/vinyl/debian/rules`; the transaction overview points readers here. |
| `20260724_2342_note_step-9-el9-transactions.md` | Same as its Debian twin: cited by `recipes/el9/vinyl-cache.spec.in` and `recipes/el9/find-provides` (packaging payload sources this pass does not touch). |
| `20260724_2348_report_step-9-transaction-safety.md` | Closing report; still cited by the kept adjudication/root-cause notes. |
| `20260725_1602_note_step-10-gate-decisions.md` | Live maintainer decisions recorded nowhere else (Boffinate identity, no SECURITY.md, keep `--with-unwind`, cohort provide). |
| `20260725_1725_note_step-10-cohort-provide.md` | Design record of the live cohort-qualified-provide mechanism; cited by `recipes/debian-13/README.md` and the kept EL9 adjudication. |
| `20260726_0824_plan_varnish-downstream-vmod-packaging.md` | Cited by `SCOPE.md` as the technical groundwork for the anticipated (unauthorised) Varnish lane. |
| `20260726_1235_note_two-track-release-and-trunk.md` | Policy note cited by `AGENTS.md` Tracks, `ci.yml`, and `registry/README.md`. |
| `20260726_2014_report_vmod-survey-first-sweep.md` | The survey findings (36 dual-compatible, 7 divergent) remain the live candidate pool for future VMOD selection; cited by the roadmap and step-5 selection note. |
| `20260728_0833_plan_vmod-matrix-failure-isolation.md` | Cited as the design source by `tools/ci_matrix.py`, three workflows, and five CI scripts; still the CI architecture reference. |
| `20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md` | Named normative reading by `AGENTS.md` for any non-cachetag VMOD work. |
| `20260728_0916_roadmap_outstanding-packaging-work.md` | The roadmap; Step 9 is active. Dated status header added noting steps 1–8 complete; body unedited. |
| `20260728_1455_note_generated-release-content-upstream-references.md` | Live policy decision: generated-only release bodies and the upstream-ownership boundary for release notes. |
| `20260728_1856_report_step-3-failure-isolation-live-proof.md` | Step closing/live-proof report (evidence trail). |
| `20260728_2057_report_step-4-engine-split-live-proof.md` | Step closing/live-proof report. |
| `20260728_2127_note_step-5-second-vmod-selection.md` | Selection decision cited by `SCOPE.md` and `registry/vmods/dict.yml`. |
| `20260728_2216_note_step-6-wave-a1-recipe-generator.md` | `registry/vmods/dict.yml` and `recipes/vmods/overlays/dict/overlay.yml` say "read this before" — normative reading pointer from packaging inputs. |
| `20260729_0119_report_step-6-wave-b-live-proof.md` | Step closing/live-proof report. |
| `20260729_1240_note_step-7-wave-1-redis-exception.md` | Decision cited by `SCOPE.md`, `registry/vmods/redis.yml`, and the redis overlay; records the live reviewed-patch commitment. |
| `20260729_2256_report_step-7-wave-2-live-proof.md` | Step closing/live-proof report. |
| `20260730_0824_report_step-8-wave-2-live-proof.md` | Step report (evidence trail). |
| `20260730_0825_note_transaction-matrix-overview.md` | The declared one-page entry point for the live transaction matrix. |
| `20260730_0826_note_step-8-maintainer-decisions.md` | Decision provenance cited by `SCOPE.md`, `AGENTS.md`, tools, and workflows. |
| `20260730_1231_note_step-8-dict-el9-allowerasing-root-cause.md` | Live constraint (libsolv name-order collation) cited by `SCOPE.md`, `tools/ci_matrix.py`, both `transactions.sh`, and the expected TSV pins. |
| `20260730_1232_report_step-8-closing.md` | Step-8 closing report; the entry point that supersedes the archived wave notes. |
| `20260730_1300_note_step-8-dict-el9-adjudication.md` | Live per-scenario pins; cited by `recipes/el9/transactions/expected/vmod-dict.tsv`. |
| `20260730_1334_note_maintainer-vision-and-automated-release-decision.md` | Decision provenance cited by `SCOPE.md` and both active plans. |
| `20260730_1355_note_el9-sha256sums-fix-and-first-clean-draft.md` | Resolution record for the step-8 closing report's open SHA256SUMS item; current release-draft state. |
| `20260730_1414_plan_release-automation.md` | Active plan. |
| `20260730_1415_plan_step-9-managed-repository-publication.md` | Active plan (Step 9). |
| `20260730_1635_note_publication-authority-decision.md` | Decision provenance cited by `SCOPE.md`, `AGENTS.md`, `tools/upstream_watch.py`, `registry/fleet-watch.json`, and `trunk-early-warning.yml`. |

## Archived to `docs/archive/` (27)

| File | Reason |
| --- | --- |
| `20260724_2138_note_step-7a-repo-scaffold-and-registry-move.md` | Historical scaffold/registry-move record incl. the `upstream/` vendoring verification; provenance, not current guidance. |
| `20260724_2231_note_step-7-8-debian-13-lane.md` | Lane bring-up implementation note; current truth is `recipes/debian-13/README.md`, entry point is the first-cohort report. |
| `20260724_2240_note_step-7-8-el9-lane.md` | Same for the EL9 lane; the waiver table it introduced lives in-tree in `rpmlint-waivers.rpmlintrc`. |
| `20260725_1655_note_step-10-ci-first-run-findings.md` | Diagnostic findings from a CI generation rebuilt several times since (steps 3, 4, 6–8). |
| `20260725_1725_note_step-10-vinyl-repin.md` | Historical re-pin event; superseded by the two-track cutover and the current registry pins. |
| `20260725_1740_note_step-10-ci-design.md` | Superseded CI design; ideas retained, but the failure-isolation plan and Step 8 restructures replaced it. |
| `20260725_1815_note_step-10-cachetag-archive-repin.md` | Self-declared superseded (by the nightly-ref-failure note); retained as evidence of the dirty-tree digest gotcha. |
| `20260726_0827_note_step-10-cohort-mint-and-pre-release.md` | Record of the first (pre-release-era) cohort mint; the current cohort story is in `README.md` and the registry. |
| `20260726_1858_plan_vmod-survey.md` | Implemented plan; `survey/README.md` is the current description of the harness. |
| `20260727_0830_note_survey-rerun-integrity.md` | Hardening record now embodied in the survey tooling itself. |
| `20260728_0743_note-nightly-cachetag-ref-failure.md` | Retired-workflow-era (nightly) incident; retained as provenance of the cachetag v1.0.0→v1.0.1 pin story. |
| `20260728_1002_note_trunk-head-header-rename.md` | Small closed incident (upstream header rename); step-8 closing cites it from the archive. |
| `20260728_1018_note_step-1-bringup-two-root-causes.md` | Wave note for completed step 1; unreferenced. |
| `20260728_1052_note_step-2-epoch-and-lint-gates.md` | Wave note for completed step 2; the epoch and lint gates it fixed are encoded in recipes/CI. |
| `20260728_1119_note_cachetag-1.0.1-repin.md` | Re-pin event note; `registry/vmods/cachetag.yml` is the authoritative pin record. |
| `20260728_1154_note_release-lane-sigterm-smoke.md` | Closed diagnostic (slow-stop vs never-stop); resolution encoded in the smoke scripts. |
| `20260728_1704_note_step-3-failure-isolation-phase-1.md` | Wave note; the step-3 live-proof report is the entry point. |
| `20260728_1936_note_step-4-engine-split.md` | Wave note; the step-4 live-proof report is the entry point. |
| `20260728_2334_note_step-6-wave-a2-ci-integration.md` | Wave note; the step-6 wave-b live-proof report is the entry point. |
| `20260728_2352_note_step-6-wave-a3-workflow-wiring.md` | Wave note; same entry point. |
| `20260729_1021_note_step-7-wave-0-lane-consolidation.md` | Wave note; the step-7 wave-2 live-proof report is the entry point. |
| `20260730_0748_note_step-8-wave-1-transactions-wiring.md` | Wave note; step-8 closing report is the entry point. |
| `20260730_0846_note_step-8-wave-3a-3b-tier-rename-and-upstream-watch.md` | Wave note; ditto. |
| `20260730_0948_note_step-8-wave-3c-trunk-early-warning.md` | Wave note; ditto — the workflow and `AGENTS.md` Tracks carry the current description. |
| `20260730_1013_note_step-8-wave-3d-release-transactions.md` | Wave note; ditto. |
| `20260730_1107_note_step-8-wave-3e-release-draft.md` | Wave note; ditto. |
| `20260730_1139_note_step-8-dict-el9-allowerasing-divergence.md` | Initial observation, fully superseded by the kept root-cause and adjudication notes. |

## Directories evaluated and deliberately not moved

- **`survey/`** — stays in place. Its freshness-signal role was replaced by `tools/upstream_watch.py` (2026-07-30 maintainer decisions), but the tracked content (pinned `data/*.json`, harness, tools, README) is the generation source of `registry/fleet-watch.json` ("generated_from: survey/data/triage.json") and the candidate pool for the ~40-VMOD fleet ambition; `registry/README.md`, `tools/upstream_watch.py`, and the publication-authority decision note all reference it. A dated status paragraph was added to `survey/README.md` recording the role change. The heavyweight sweep outputs (`cache/` 268M, `results/` 13M) are untracked/gitignored, so there was nothing to delete from the repository.
- **`upstream/`** — stays in place. Not dead: `recipes/debian-13/vinyl/debian/rules`, `recipes/el9/vinyl-cache.spec.in`, `recipes/el9/find-provides`, `recipes/debian-13/README.md`, and `scripts/ci/trunk/build-engine.sh` all cite `upstream/pkg-vinyl-cache/` as their audited derivation base, and `PROVENANCE.md` plus the verified tree hash (in the archived scaffold note) anchor that chain. Moving it would either break those references or force edits to packaging payload sources for zero gain.

## Reference fixes in kept/normative files

Every reference to an archived file outside `docs/archive/` was rewritten to the archive path: `README.md` (2), `AGENTS.md` layout line (description update), the roadmap and 16 other kept docs (link paths only), `recipes/debian-13/README.md`, `recipes/el9/rpmlint-waivers.rpmlintrc` (comment), `scripts/ci/debian13/make-chroot.sh` (comment), `.github/workflows/release-draft.yml` (comment), `survey/README.md`. No packaging payload source (`debian/rules`, `*.spec.in`, `find-provides`, overlays, manifests) was modified — docs cited by those files were kept in place instead.

## Validation

`python3 tools/release_tool.py validate` and `python3 tools/ci_matrix.py selftest` pass after the moves; repo-wide grep for each archived basename shows zero dangling references.
