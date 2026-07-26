# VMOD survey: first full dual-lane sweep

Date: 2026-07-26

Plan: [20260726_1858_plan_vmod-survey.md](20260726_1858_plan_vmod-survey.md). Tooling and harness: `survey/`. Full matrix: `survey/results/REPORT.md` (regenerable; results directories are gitignored, the data snapshots in `survey/data/` are committed).

## What ran

113 VMODs from the vinyl-cache.org directory (homepage repo commit `a67cfa490e`, 112 homepage registrations + 1 manual addition), each built and load-smoked in clean Debian 13 containers against both lanes:

- **varnish9** — varnishd 9.0.3, revision `0a625649cd40`, built from the pinned github.com/varnish/varnish release tarball.
- **vinyl9** — vinyld trunk `25761f8505` from the local `vinyl-cache`/`vinyl-cache-dev` 9.0.0~git20260520 packages (arm64 recipes build).

Both lanes carry naming shims in both directions (varnish↔vinyl pc/m4/tool names), so results measure API compatibility, not the project rename. Pass = bootstraps, configures, compiles, and every built module .so is accepted by the lane daemon's VCL compiler. No test suites were run.

## Headline

| verdict | count | meaning |
| --- | --- | --- |
| green | 35 | passes both Varnish 9 and Vinyl 9 |
| DIVERGENT | 7 | passes Varnish 9, fails Vinyl 9 |
| fails-both | 66 | needs 9.x modernisation regardless of fork |
| bundled | 5 | ships inside the daemon distribution; excluded |
| (dead) | 2 | repository gone (`authentication`) or empty result |

Per-lane pass counts: varnish9 42, vinyl9 35.

## The divergence set

Every one of the 7 fails at compile, and the log signatures enumerate the Vinyl fork's VMOD-facing API changes:

| vmod | source | vinyl9 failure signature |
| --- | --- | --- |
| xkey, saintmode, vsthrottle, var | varnish-modules | `cache/cache_varnishd.h: No such file or directory` (renamed to `cache_vinyld.h`) |
| basicauth | gnu.org.ua | empty version-guard macro (`operator '&&' has no right operand`) — version macros renamed |
| file | dridi | `VRT_synth_page` gone (vinyl offers `VRT_synth`) |
| gossip | martin | `OEV_INSERT`/`OEV_EXPIRE` gone — object event API replaced (vinyl's `OC_EF_*` flags; the surface cachetag subscribes to) |

Reading: **4 of 7 fail only on the `cache_varnishd.h` header rename** and would likely build with a one-line compat header or patch; the other three hit real API replacements (synth page helper, object event subscription, version macros). A Vinyl-shipped compatibility layer covering the header rename plus the build-name shims would flip most of varnish-modules to green — that quantifies the "should Vinyl ship a compat shim" question the plan raised.

No VMOD passes Vinyl and fails Varnish: the earlier `tbf` case was a parallel-make race in old vmodtool Makefile rules (fixed with a sequential retry), not reverse divergence — though `dns` (updated 2026-07-25) already *probes* the vinyl names first and needed the reverse shim to build on the varnish lane at all.

## Other findings with packaging consequences

- **10 configure runs demand a daemon source tree** (`Need VINYLSRC` / `VINYLSRC must be set`): `pesi`, `slash`, `tus`, `zipflow` and the rest of that uplex family. These build against daemon internals, not the installed dev package, so they can never be packaged downstream as currently written — directly relevant to the downstream plan's dev-package-surface gate.
- **The 66 fails-both are bimodal.** Roughly half are pre-2017 relics (VRT eras 3.x–5.x). The actively maintained failures have concrete, mostly small causes: `valkey` needs libvalkey (not in Debian 13), `variable` needs PCRE1 (removed from Debian 13), `brotli` builds a differently-named .so the harness doesn't yet smoke, `urlsort` has no autotools build, `ip2location` has a broken aclocal include path.
- **The directory data is stale but structurally sound**: 32 of 113 entries are marked inactive, only ~44 repos have 2026 commits, `authentication`'s repo is gone, and the claimed-version branch maps top out around 6.x–7.x. The registration JSON format works well as a machine-readable worklist source.

## Harness lessons (recorded for reruns)

Iterations needed to make the sweep honest, all now baked in: varnish 9 requires sphinx and libssl at build time; `ACLOCAL_AMFLAGS` referencing `${VARNISHAPI_DATAROOTDIR}` needs the variable exported; 13 repos vendor build machinery in git submodules; uplex configure scripts need autoconf-archive; old vmodtool Makefile rules race under `make -j`; and the naming shims must be symmetric and must strip vinyl's `m4_pattern_forbid` tripwires.

## Follow-ups

1. Decide the compat-shim product question with these numbers (4 VMODs green-able by one header alias; build-name shims already drafted in `survey/harness/`).
2. Report divergence findings upstream where cheap (varnish-modules `cache_varnishd.h` include, `file`'s `VRT_synth_page`).
3. Candidates for packaging lanes / directory refresh: the 35 green VMODs, starting with the actively maintained ones.
4. Harness v2 items: smoke non-`libvmod_*.so` module names (brotli), optional claimed-branch fallback sweeps, rerun cadence against vinyl trunk (the trunk-vmod-ci workflow is the natural home).
