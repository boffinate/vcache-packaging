# Third-party VMOD compatibility survey

Date: 2026-07-26

Status: Implemented; first full sweep completed 2026-07-26 — see [20260726_2014_report_vmod-survey-first-sweep.md](20260726_2014_report_vmod-survey-first-sweep.md)

Related: [Varnish downstream VMOD packaging plan](20260726_0824_plan_varnish-downstream-vmod-packaging.md), `registry/README.md`, maintainer discussion of 2026-07-26.

## Goal

An automated, repeatable survey of the third-party VMODs listed in the vinyl-cache.org directory, answering per VMOD and per lane: does it build against, and load into, (a) Varnish Cache 9.0.x and (b) Vinyl Cache 9? The output is a classification matrix plus a Vinyl-divergence report (the set that passes on Varnish 9 but fails on Vinyl 9 — each such failure is a fork API divergence to document or a candidate port).

What the survey is **not**: a support claim. No registry manifest is minted for a surveyed VMOD; manifests exist only for VMODs we actually package and test per the downstream plan. The survey feeds the decision of which VMODs get lanes.

## Facts established 2026-07-26

- **Directory source of record**: the homepage repo on Forgejo (`https://code.vinyl-cache.org/vinyl-cache/homepage`), `R1/source/vmods/`, currently 113 `vmod_*.json` registration files at commit `a67cfa490e` (2026-06-28) plus `build.py` (the page generator) and `howto.rst` (the registration format). Anonymous access: raw file endpoints and the `contents` API work; tarball archive download returns 403; git clone works but only over dumb HTTP and is impractically slow. Ingestion therefore uses the contents API plus per-file raw fetches pinned to a branch-head commit id.
- **Registration format** (howto): required `name`, `date`, `desc`, `license`, `status`; optional `maintainer`, `support`, `product`, `inactive`; either a `github` object (`user`, `project`, `vcc_path`, `doc_path`, `branches` mapping claimed version → branch) or `repos`/`rev` for non-GitHub hosting. Not every VMOD in the wild is listed, and not every listed repo carries its own copy of the JSON — the worklist format must accept manual additions.
- **Varnish 9 availability — the project split, established from live sources**: the old community infrastructure renamed to Vinyl (varnish-cache.org now serves a "Sorry, we moved!" page pointing at vinyl-cache.org; the `varnishcache` GitHub org's newest tag is `varnish-8.0.0`; the varnishcache packagecloud repos stop at `varnish80`). Varnish Cache 9.x is Varnish Software's continuation, published at `github.com/varnish/varnish` and www.varnish.org: 9.0.3 released 2026-05-18, EOL 2027-03-16, alongside 8.0.2 and 6.0.18. The varnish lane builds varnishd from the pinned 9.0.3 release tarball (`https://github.com/varnish/varnish/releases/download/varnish-9.0.3/varnish-9.0.3.tar.gz`, sha256 `2aac11dd95329b0cea148d478168b3ccc6fe45fab38160c440159386403b69fd`); binary package lanes can follow later once we decide which of Varnish Software's package sources to pin. The downstream packaging plan's assumption of `varnishcache` packagecloud repos for 9.0 needs revisiting in light of the split.
- **Vinyl 9 availability**: real packages exist — `vinyl-cache` / `vinyl-cache-dev` .debs, trunk track `9.0.0~git20260520.25761f8505-1`, locally in `dist/debian-13/` (arm64) and on the `cohort-vinyl-9.0.0-4b7e68292979` GitHub pre-release (amd64). The dev package ships the full private header set, `vinylapi.pc`, `vmodtool.py`, and the `vinyl.m4`/`vinyl-legacy.m4` autoconf macros.
- **The rename gotcha**: `vinyl-cache-dev` provides **no** `varnishapi.pc`, no `VARNISH_*` autoconf macros, and `vinyl-legacy.m4` contains `m4_pattern_forbid([^_?VARNISH[A-Z_]+$])`. An unmodified third-party VMOD (`PKG_CHECK_MODULES([VARNISHAPI], [varnishapi])`, `VARNISH_PREREQ`, …) fails at autoconf/configure time on Vinyl regardless of actual API compatibility.

## Design decisions

1. **Two lanes, one prebuilt Docker image each**, both `debian:trixie`-based. The varnish lane image compiles and installs Varnish 9.0.3 from the pinned upstream tarball. The vinyl lane image installs the `vinyl-cache` + `vinyl-cache-dev` .debs (local `dist/debian-13/` output when present, else the GitHub pre-release assets, both sha256-verified). Per-VMOD runs are `docker run --rm` from the lane image: clean room per VMOD, image build cost paid once.
2. **Both lanes carry survey-local naming shims, in opposite directions** (decided during harness validation, 2026-07-26). The vinyl lane generates `varnishapi.pc`, `varnish.m4`, and `varnish-legacy.m4` over the `VINYL_*` surface; the varnish lane generates `vinylapi.pc`, `vinyl.m4`, and `vinyl-legacy.m4` over the `VARNISH_*` surface, because actively maintained VMODs (e.g. `dns`, updated 2026-07-25) have already migrated to the vinyl names and would otherwise fail on the varnish lane for naming reasons alone. The generated m4 files drop vinyl's `m4_pattern_forbid` tripwires, which exist to catch exactly the un-migrated names the shims satisfy. Header renames (`cache/cache_varnishd.h` → `cache_vinyld.h`) are deliberately **not** shimmed: an unmodified source tree that includes the old header genuinely does not build against Vinyl, and that is divergence data the survey exists to collect. Whether Vinyl should ship such a shim officially is a separate product question the survey data will inform — the shim files are a first draft of it.
3. **Build + load smoke only, no test suites.** The check per VMOD: bootstrap/configure/build against the installed dev surface, then compile a minimal VCL that imports each built `.so` (`varnishd -C` / `vinyld -C`). Third-party test suites are varnishtest-version-sensitive and flaky; running them is a per-VMOD tier-2 exercise for packaging candidates, not part of the sweep.
4. **Ref selection**: build the repository default branch first — active projects carry 9.x compatibility on main/master, and the claimed `branches` map tops out around 6.x–7.x for almost every entry. The claimed-latest branch is recorded and available as a fallback ref via `--ref`, but the sweep's headline number is "state of the default branch".
5. **Static triage before any container work**: `git ls-remote` + shallow clone per repo on the host (cloning is analysis, not verification; no build happens on the host), extracting last-commit date, `$ABI` declaration from `.vcc`, private-header includes, build system, and `varnishapi`/`VARNISH_*` usage. This predicts failure classes and orders the sweep; it makes no compatibility claim.
6. **Results are per-VMOD, per-lane JSON files** recording the stage reached (`clone`, `bootstrap`, `configure`, `build`, `load`), a failure-class signature extracted from the log, durations, and the exact daemon version/ABI identity of the lane. The sweep is resumable: an existing result file skips the VMOD unless `--force`. The rendered matrix (REPORT.md) records the lane identities; when a sweep concludes, the report is copied into `docs/` as a dated report note per repo convention.

## Failure classification

| Class | Signature examples |
| --- | --- |
| `clone-failed` | repo gone, auth-walled, default branch unfetchable |
| `no-build-system` / `unsupported-build-system` | no autotools; cmake/meson (recorded, not attempted in v1) |
| `bootstrap-failed` | autogen/aclocal era failures, missing m4 |
| `configure-failed-api-detect` | `PKG_CHECK_MODULES` / `VARNISH_PREREQ` version rejections |
| `configure-failed-other` | everything else at configure |
| `compile-failed` | missing VRT symbols, changed struct fields, removed private APIs |
| `link-failed` | unresolved symbols at link |
| `load-failed` | built but `-C` rejects the import (ABI stamp, metadata) |
| `pass` | built and loaded |

The interesting cross-lane sets: pass/pass (green), varnish-pass + vinyl-fail (**divergence set**), fail/fail (stale, upstream 9.x modernisation needed), vinyl-pass + varnish-fail (harness bug until proven otherwise).

## Layout

```text
survey/
  tools/ingest.py      directory JSONs + manual additions → data/worklist.json
  tools/triage.py      host-side static signals → data/triage.json
  tools/report.py      results → results/REPORT.md matrix
  data/manual-additions.json
  data/worklist.json   committed snapshot (regenerable)
  data/triage.json     committed snapshot (regenerable)
  harness/             lane Dockerfiles, pins, shim, sweep driver
  cache/               gitignored: fetched directory files, repo clones
  results/             gitignored: per-vmod logs + JSON, rendered REPORT.md
```

Tools follow the repository's stdlib-only rule and run on the host; every build and load happens inside the lane containers, per the workspace hard rules.

## Implementation order

1. Plan doc (this file), `survey/` scaffold, gitignore entries. — done 2026-07-26
2. `ingest.py` against the live directory; commit the worklist snapshot.
3. `triage.py` full run; commit the triage snapshot.
4. Lane images + shim + sweep driver; validate on a sample (expected-green modern VMOD, expected-red stale VMOD, one middle case).
5. Full dual-lane sweep; render and copy the report note into `docs/`.
6. Follow-ups the data will drive: manual additions beyond the directory, cmake/meson support, claimed-branch fallback sweeps, whether Vinyl ships a real compat shim, packaging-lane candidates per the downstream plan.
