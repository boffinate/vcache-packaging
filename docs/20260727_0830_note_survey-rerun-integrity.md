# Survey rerun-integrity hardening

Date: 2026-07-27. Follows the first sweep (`20260726_2014_report_vmod-survey-first-sweep.md`) and a code review of commit `8c6dd07`.

## Why

The first sweep's headline (36 green / 7 DIVERGENT) was trustworthy only because the divergent logs were hand-verified. The pipeline itself had four ways to publish wrong numbers on an unattended rerun (the planned trunk-cadence lane):

1. `report.py` treated every non-`pass` class as a real failure, so a docker hiccup or timeout on one lane would be machine-classified DIVERGENT, and the published "65 fails-both + 2 dead" split could not be produced by the tool at all (dead repos were machine-counted as fails-both; the doc was a manual re-slice whose arithmetic summed to 115, not 113).
2. Resume (`sweep.py` skip-existing) honoured artifact results forever, baking transient trouble into the matrix, and results carried no image provenance, so a resumed sweep spanning an image rebuild silently mixed daemon builds.
3. The surveyed repos were pinned only by "whatever HEAD the gitignored cache happened to hold"; deleting `cache/` re-surveyed different code with no error.
4. The vinyl lane's shim `varnishapi.pc` advertised the deb-derived 9.0.0 against the varnish lane's 9.0.3, so any `VARNISH_PREREQ` floor in between would manufacture a DIVERGENT verdict out of a version string.

## What changed

- **`tools/classes.py`** (new): every result class maps to a category — pass, fail, unbuildable, blocked, dead, artifact — shared by sweep and report.
- **Verdicts are computed from categories.** New verdicts: `needs-source-tree`, `blocked-deps`, `dead`, `incomplete`. DIVERGENT now requires a genuine `fail` on the vinyl lane. Artifacts (timeout, harness-error, copy-failed, pin-mismatch) become `incomplete`: rendered in their own section, and `report.py` exits non-zero while any exist or while a lane's results span more than one image — an unattended rerun fails loudly instead of publishing.
- **Resume retries artifacts.** `sweep.py` honours settled results only; artifact and clone-missing results re-run on the next invocation without `--force`.
- **Provenance stamps.** Every result (schema v2) records `image_id`, `head_commit`, `pinned_commit`; `LANE.json` (v2) keeps a `runs` history of image ids. `report.py` cross-checks swept commits against the triage pins.
- **Repos are pinned.** `triage.py` honours the recorded `head_commit` by default (full clones now — shallow clones cannot check out a moved-away pin; pre-existing shallow caches are deepened on demand), restores pins after a cache delete, and only moves them with the new `--repin`. `--only` now merges into the existing snapshot instead of truncating it. `sweep.py` refuses a cache tree on the wrong commit (`pin-mismatch`, an artifact, so it self-heals after re-triage).
- **One shim version surface.** `SHIM_API_VERSION` in `harness/pins.env` (tracks `VARNISH_VERSION`) is written into every pc file both lanes expose, including the vinyl lane's native `vinylapi.pc` (whose trunk deb says `Version: trunk`).
- **Classifier attribution tightened.** `classify()` now reads the failing stage's output and anchors `needs-source-tree` on the actual configure error (`Need VINYLSRC` / `VINYLSRC must be set`); `Unable to find required Varnish build environment` is api-detect.

## Corrections to the first-sweep report

Replaying all stored logs through the new classifier changes exactly two rows: `lang` and `queryfilter` (vinyl9) were misattributed `configure-failed-needs-source-tree` from a harmless `checking to see if VARNISHSRC set... no` line; both configure fine on the varnish9 lane and actually fail vinyl9 with `Unable to find required Varnish build environment` (api-detect). Both were re-swept. The first-sweep report's "10 configure runs demand a daemon source tree" is therefore **8** (the uplex family: `pesi`, `slash`, `tus`, `zipflow` × 2 lanes), and `lang`/`queryfilter` return to fails-both.

The machine-produced matrix now reads: **green 36, DIVERGENT 7, fails-both 58, needs-source-tree 4, blocked-deps 2, dead 1, bundled 5** (= 113). The headline 36/7 is unchanged.

## Verified

- `report.py` on the existing results: counts above, exit 0.
- Doctored a result to `timeout`: report renders it under Incomplete and exits 1; the next plain `sweep.py` invocation re-ran only that row and healed it.
- Doctored a triage pin: sweep refused with `pin-mismatch` without starting a container; a normal sweep against the real triage healed it.
- `triage` clone/`ensure_pin`/`advance`/unreachable-pin exercised against a local two-commit repo: all paths correct, unreachable pin raises.
- Full resweep of `lang`/`queryfilter` through the new harness path: v2 stamps present, `head_commit` == pin.

Not verified here: an image rebuild with the new `SHIM_API_VERSION` build-arg (no lane rebuild was needed; the next `build-images.sh` run exercises it — the shim scripts fail hard if the variable is missing).
