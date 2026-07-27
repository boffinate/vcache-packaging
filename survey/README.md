# Third-party VMOD compatibility survey

Automated survey of the VMODs listed in the vinyl-cache.org directory: does each one build against, and load into, Varnish Cache 9 and Vinyl Cache 9? Design record: [`docs/20260726_1858_plan_vmod-survey.md`](../docs/20260726_1858_plan_vmod-survey.md).

A survey pass is not a support claim. No registry manifest is minted here; the results feed the decision of which VMODs get real packaging lanes per the downstream VMOD plan.

## Pipeline

All tools are Python 3 standard library and run on the host; every build and load happens inside the lane containers.

```sh
python3 tools/ingest.py     # directory JSONs + data/manual-additions.json -> data/worklist.json
python3 tools/triage.py     # clone repos, static signals -> data/triage.json
sh harness/build-images.sh  # build the two lane images (Docker required)
python3 tools/sweep.py      # dual-lane build+load sweep -> results/<lane>/<name>.json
python3 tools/report.py     # -> results/REPORT.md
```

The sweep is resumable (settled results are skipped; `--force` re-runs everything) and scopable (`--only name...`, `--lanes varnish9`). Harness artifacts — timeouts, docker errors, pin mismatches, missing clones — are always retried on the next invocation, so transient trouble cannot bake into the matrix.

The survey is pinned end to end: the directory ingest records the homepage commit, and triage records each repo's `head_commit`, which later runs honour — a rerun (even after deleting the gitignored `cache/`) checks out exactly the recorded commits, and the sweep refuses a cache tree on any other commit (`pin-mismatch`). Moving the survey to current upstream HEADs is explicit: `triage.py --repin`. Each result is stamped with the lane image id and the swept commit.

## Lanes

- **varnish9** — Varnish Software's Varnish Cache built from the pinned 9.0.3 release tarball (`github.com/varnish/varnish`).
- **vinyl9** — the `vinyl-cache` + `vinyl-cache-dev` .debs (local `dist/debian-13/` build when present, else the verified cohort pre-release assets).

Each lane carries a survey-local naming shim for the *other* project's development names (`harness/make-shim.sh` gives the vinyl lane `varnishapi.pc`/`varnish.m4`/`varnish-legacy.m4`; `harness/make-reverse-shim.sh` gives the varnish lane the vinyl names, since some maintained VMODs have already migrated). This makes the lanes name-agnostic so the sweep measures API compatibility, not the project rename. Every pc file both lanes expose advertises the one `SHIM_API_VERSION` from `harness/pins.env`, so a configure-time version floor (`VARNISH_PREREQ`/`VINYL_PREREQ`) resolves identically on both lanes instead of tripping over the vinyl trunk deb's version string. Header renames (`cache/cache_varnishd.h` → `cache_vinyld.h`) are deliberately not shimmed — that failure is real divergence data. Neither project ships these shims; whether Vinyl should is a product question the survey data informs.

Both images share an identical third-party dependency package list, so a missing library fails the same way on both lanes. Pins live in `harness/pins.env`.

## Reading results

Per VMOD and lane: `results/<lane>/<name>.json` (stage reached, failure class, per-module load results) with the full log alongside. `results/<lane>/LANE.json` records the exact daemon build the lane ran. The interesting cross-lane sets, rendered in `results/REPORT.md`:

- **green** — passes both; candidate for directory refresh and packaging lanes.
- **DIVERGENT** — passes Varnish 9, genuinely fails Vinyl 9: a fork API divergence to document, or a port candidate.
- **fails-both** — needs 9.x modernisation generally; upstream's problem first.
- **anomaly** — passes Vinyl, fails Varnish: treat as a harness bug until proven otherwise.
- **needs-source-tree** — configure demands a daemon source tree (`Need VINYLSRC`); can never build from dev packages as written.
- **blocked-deps** — a third-party dependency is missing from the lane images; says something about Debian 13, not 9.x.
- **dead** — the repository is gone.
- **incomplete** — a harness artifact (timeout, docker error, pin or image mismatch), never a VMOD result. `report.py` exits non-zero while any exist — the matrix is not publishable — and the next `sweep.py` invocation retries them.

The class→category mapping behind these verdicts lives in `tools/classes.py`.
