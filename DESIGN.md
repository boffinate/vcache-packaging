# Design

Status: Normative for the initial build. Contracts here (schemas, CLI surfaces, script interfaces, statuses) are what the components are written against. Change the contract here first, then the code.

This is a clean-room successor to `../vcache-packaging` (referred to as **v1** below). v1 remains readable as a reference; nothing in v1 is imported wholesale without being listed in the port map at the bottom.

## Repository layout

```
SCOPE.md  DESIGN.md  README.md
engines.yml              # hand-maintained: every engine version we test/package
vmods/<id>.yml           # hand-maintained: one file per selected VMOD
packaging/
  debian/                # one generic Debian recipe template set (*.in)
  rpm/                   # one generic RPM spec template (*.in)
tools/
  yaml_subset.py         # strict stdlib YAML-subset parser (ported from v1)
  matrix.py              # the one CLI: validate / expand / env / merge / render / selftest
  recipe.py              # renders debian/ + spec from templates + catalog (library + CLI)
  selftest.py            # tests for all of the above (run by matrix.py selftest)
scripts/
  build-engine.sh        # build one engine (id, target) in a container
  build-vmod.sh          # build one VMOD against one engine (compat and/or package mode)
  lib.sh                 # shared shell helpers (container run, result emission)
.github/workflows/
  ci.yml                 # PR/push: host-safe validation + selftests only
  matrix.yml             # dispatch + weekly: full release-engine matrix -> Pages
  trunk.yml              # schedule Mon/Thu: trunk columns -> Pages
  release.yml            # dispatch: build packages, create GitHub Release
work/                    # gitignored scratch (container mounts, results, artifacts)
```

## Catalog schemas

Parsed with `tools/yaml_subset.py`: mappings, block sequences, scalars-as-strings only — no flow `[a, b]` lists, no `|` block scalars, no anchors. Mapping keys may contain lowercase letters, digits, `_`, `-`, and `.` (series names like `vinyl-9.0` must be expressible as `by_series` keys). Multi-line prose (`description`) is a list of plain scalar lines. Unknown keys are validation errors. The inline examples below use flow-list shorthand for brevity only; the real files use block sequences throughout.

### engines.yml

```yaml
schema: engines/1
engines:
  - id: vinyl-9.0.1            # unique, becomes a matrix column
    family: vinyl              # vinyl | varnish
    series: vinyl-9.0          # what vmods/*.yml by_series keys refer to
    kind: release              # release | trunk
    source:
      tarball_url: https://...  # release: pinned tarball
      sha256: "..."             # release: tarball digest
      git_url: https://...      # trunk: repository
      branch: trunk             # trunk: branch to build HEAD of
    packages: "true"           # "true": we ship packages built against it
    targets: [debian-13-amd64, el9-x86_64]
```

Rules: `kind: release` requires `tarball_url` + `sha256`; `kind: trunk` requires `git_url` + `branch` and forces `packages: "false"`. Trunk engines carry a self-named `series` (`vinyl-trunk`); the resolution rule never consults `series` for trunk engines. `packages: "true"` requires `kind: release` and `family: vinyl` (Varnish is matrix-only for now — reversible decision, see Decisions). Compat columns are tested on the first listed target only; package engines build on every listed target.

Initial contents: `vinyl-9.0.1` (release, packages, both targets — pin from v1 `recipes/debian-13/pins.env`), `varnish-9.0.3` (release, matrix-only, debian target — pin from v1 `survey/harness/pins.env`), `vinyl-trunk` and `varnish-trunk` (trunk, matrix-only — git URLs/branches from v1 `tools/upstream_watch.py` constants).

### vmods/<id>.yml

```yaml
schema: vmod/1
id: dict
upstream:
  git: https://git.gnu.org.ua/vmod-dict.git
  homepage: https://...        # optional
sources:
  head: master                 # branch built for trunk-engine columns
  default:
    ref: v1.7                  # tag or branch built for release-engine columns
    version: "1.7"             # upstream version string for the package
  by_series:                   # optional overrides, keyed by engines.yml series
    varnish-10:
      ref: v2.0
      version: "2.0"
package:
  summary: "one line"
  description:          # list of lines, joined when rendered
    - "A few sentences."
  license: GPL-3.0-or-later    # SPDX, single expression; informational
  build_deps:
    debian: [python3-docutils]  # beyond the implied engine -dev + autotools set
    rpm: [python3-docutils]
  modules: [accept, bodyaccess]  # optional: VCL import names the package ships; default [<id>]
tests: make-check       # optional: run upstream's own `make check` in compat mode; absent = no suite
```

`package.modules` exists for multi-VMOD repositories (varnish-modules ships nine `.so` from one tree; upstream cannot build a subset, and Debian itself ships it as one package — so it is ONE catalog entry, one matrix row, one package). Module names must match `[a-z][a-z0-9_]*`. `tests: make-check` runs upstream's suite from upstream's tree — we still carry no tests; a VMOD whose suite needs fixtures we won't provide simply doesn't set it.

**Source resolution rule** (the one rule, used everywhere): for engine E, a VMOD builds `sources.head` if `E.kind == trunk`, else `sources.by_series[E.series]` if present, else `sources.default`. There is no "skip": every VMOD gets a cell for every engine column, and an incompatible pairing simply fails and renders red.

No pinned commits or archive digests for VMODs. The ref is the pin; trunk cells record the commit they actually built in the cell result. (Engine release tarballs keep a sha256 because packages are built from them.)

## Cell results

One JSON file per (vmod|engine-itself, engine, target), written by the build scripts, merged by `matrix.py merge`:

```json
{"schema": "cell/1", "row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64",
 "mode": "compat", "ref": "v1.7", "commit": "<resolved sha or empty>",
 "status": "pass", "detail": "", "run_url": "", "finished_at": "2026-08-10T00:00:00Z"}
```

`row` is a VMOD id, or the engine's own id for the engine's build row. `mode` is `compat`, `package`, or `engine` (engine rows only). Result files are named `<row>--<engine>--<target>--<mode>.json` — globally unique, because CI flattens every job's results into one directory.

**Statuses** (the full vocabulary — keep it this small):
`pass`, `configure_failed`, `build_failed`, `load_failed`, `test_failed`, `package_failed`, `install_failed`, `infra_failed`.

The first four come from compat mode (autotools configure, make, a `vcl.load`-style check compiling a minimal VCL that imports every built `.so` against the built engine, then — only when the manifest says `tests: make-check` — upstream's own `make check`, retried once whole on failure to absorb known VTC load-flakes, `test_failed` with the failing test names in `detail` if it fails twice). `package_failed`/`install_failed` come from package mode (recipe build, then install-and-load in a fresh container; the installed load check compiles one VCL importing every name in `package.modules`). `infra_failed` means the harness itself broke and is the **only** status that fails a CI job.

## The matrix page

Rows: engines' own build row first, then one row per VMOD (catalog order). Columns: engines in `engines.yml` order (release engines, then trunk engines). Cell colour by worst status across that cell's targets/modes; green `pass`, red any `*_failed` except infra, grey `infra_failed` or no data. Tooltip carries per-target/mode detail, ref, commit, timestamps, link to the producing run. Rendering ports v1 `tools/status_page.py`'s bones (state-file merge, self-contained HTML, light/dark) with the new axes. State lives in `matrix-state.json` on orphan branch `ci-state/matrix`; Pages deploys from `matrix.yml` and `trunk.yml` on main only, shared concurrency group.

## tools/matrix.py CLI contract

```
matrix.py validate                                      # catalog well-formed; exit 1 on error
matrix.py expand --lane release|trunk [--mode compat|package|all] --format github|json
matrix.py resolve --vmod ID --engine ID                 # print resolved ref+version JSON
matrix.py env --engine ID [--vmod ID] [--target ID]     # sh-sourceable pins for scripts
matrix.py merge --results-dir DIR --state-file FILE     # fold cell JSONs into state
matrix.py render --state-file FILE --out index.html
matrix.py selftest
```

`expand` emits rows `{row, engine, target, mode}` — the GitHub Actions job matrix. `--format github` prints exactly two `key=<single-line-json>` lines for `$GITHUB_OUTPUT`: `engines=[...]` (unique engine×target pairs) and `vmods=[...]` (VMOD rows only, engine rows excluded). `env` is the **only** way shell/CI gets version strings; nothing like v1's hand-mirrored `pins.env` exists. `merge` rule: newest `finished_at` per (row, engine, target, mode) wins; globs `*.json` recursively; a state file full of red cells is still a successful merge/render.

## tools/recipe.py

Renders a Debian source dir and an RPM spec from `packaging/` templates plus one VMOD's catalog entry and one engine's `env` values. Package naming (uniform, all VMODs including cachetag): Debian binary `vinyl-vmod-<id>`, RPM `vinyl-vmod-<id>`. Version: `<upstream_version>-1~vinyl<engine_version>` style Debian revision / `Release: 1.vinyl<engine_version>` RPM. ABI coherence is expressed as an exact-version dependency on the engine runtime package (`Depends: vinyl-cache (= <engine pkg version>)` / `Requires: vinyl-cache = <ver>`). `debian/copyright` is generated minimal: SPDX id + pointer to upstream license file. No lintian/rpmlint gating.

## Script contracts

```
scripts/build-engine.sh <engine-id> <target> <workdir>
scripts/build-vmod.sh   <vmod-id> <engine-id> <target> <mode> <workdir>
```

Both run everything inside containers (`debian:13` for debian-13-amd64 compat+package, `almalinux:9` for el9 package builds), pull pins via `matrix.py env`, and always write a cell result JSON into `<workdir>/results/` — including on failure, classifying the failure honestly. Engine build produces, per target: a relocatable prefix tarball (for compat mode consumers) and, if `packages: "true"`, the engine .deb/.rpm set (adapted from v1's engine build + v1 `upstream/pkg-vinyl-cache` derivation, simplified — plain `dpkg-buildpackage`/`rpmbuild` in a container, no pbuilder/mock/sbuild). VMOD compat mode: untar engine prefix, autotools build against it, minimal-VCL load check. VMOD package mode: install engine packages, render recipe via `recipe.py`, build, then fresh-container install + load check. Exit code 0 unless infra_failed.

## Workflows

- `ci.yml` — PR + push to main: `matrix.py validate` + `matrix.py selftest`. Fast, host-safe, no containers.
- `matrix.yml` — workflow_dispatch + weekly: expand release lane → engine jobs (upload prefix/package artifacts) → VMOD jobs (fail-fast off, never red on cell failure) → merge → render → commit state to `ci-state/matrix` → deploy Pages. Main-branch runs only publish.
- `trunk.yml` — cron Mon/Thu + dispatch: same shape for trunk engines, compat mode only. **No change-gating, no issue filing, no re-pin PRs** — it just runs; the matrix page is the notification surface.
- `release.yml` — workflow_dispatch only: build vinyl release engine packages + all VMOD packages on all package targets, collect green results, `gh release create` (not draft) with .deb/.rpm/SHA256SUMS and a body generated from the merged results. A VMOD whose package build fails is simply omitted from the release and listed as such in the body.

## Decisions (all reversible; no users yet)

1. **Package naming**: uniform `vinyl-vmod-<id>` for every VMOD on both package formats, including cachetag. Rationale: one code path, obvious provenance, no collision with distro `libvmod-*`. cachetag's own repo keeps its own audited `libvmod-cachetag` recipe for its own releases; this project does not consume it.
2. **Varnish is matrix-only**: we test compat against Varnish releases/trunk but package only against Vinyl. Flipping `packages` on a varnish engine later is the designed path.
3. **Publication**: GitHub Releases now; managed APT/RPM repo later as a thin publish script.
4. **No change-gating on trunk runs**: two scheduled runs a week, unconditional. Cost is a handful of container builds; the v1 watcher/gate machinery is not ported.
5. **No VMOD commit pins**: the hand-written ref is the pin. Moved-tag paranoia, archive digests per VMOD, and poisoned-tag tracking are not ported.
6. **Exact-version engine dependency** for VMOD packages (not ABI-hash ranges): honest and simple at this quality bar.
7. **Container images pinned by tag** (`debian:13`, `almalinux:9`), not digest; the cell result records what actually ran.
8. **cachetag uses the uniform generated recipe, not its own packaging** (2026-08-10). Its `packaging/` tree is a 14-token template whose dependency model needs `vinyld-abi-<hash>`/`vinyld-vrt`/`vinyld-cohort-<id>` engine Provides that v2's engine deliberately lacks; its distro-native variant is semantically what our generated recipe already emits. Its VTC suite runs anyway via `tests: make-check` — proven 52/53 against our vinyl-9.0.1 prefix (the one failure being the documented pm00007 load-flake, hence the retry-once policy). Whether v2's engine packages should grow ABI/cohort Provides is an open maintainer conversation, deliberately not settled here.
9. **varnish-modules is one catalog entry** (2026-08-10): one row, one package with all nine `.so`, matching both upstream's all-or-nothing build and Debian's own packaging of it. Expected red on vinyl columns at configure (`varnishapi.pc` absent) until upstream or a fork accommodates vinyl — that red is the matrix doing its job.

## Port map (the only v1/survey content that comes across)

| From (v1 repo / survey) | To | What survives |
|---|---|---|
| `tools/yaml_subset.py` | `tools/yaml_subset.py` | verbatim, minus anything unused |
| `tools/status_page.py` | render half of `tools/matrix.py` | state merge + HTML/CSS/JS bones; new axes |
| `registry/vmods/*.yml` (7 files) | `vmods/*.yml` | ids, upstream URLs, head branches, release refs/versions |
| `recipes/debian-13/pins.env`, `survey/harness/pins.env`, `tools/upstream_watch.py` constants | `engines.yml` | the four engine pins, transcribed once |
| `recipes/vmods/templates/` | `packaging/` | template idea, heavily slimmed |
| `scripts/ci/trunk/build-engine.sh`, `survey/harness/*` | `scripts/build-engine.sh` | engine configure/build/prefix commands |
| v1 engine package recipes (`recipes/debian-13/vinyl/`, `recipes/el9/*.spec.in`) | `scripts/build-engine.sh` + `packaging/` | debian/rules + spec content, simplified to plain dpkg-buildpackage/rpmbuild |
| `tools/metadata.py` ABI/version expressions | `tools/recipe.py` | the dependency-expression idea only |

Everything else in v1 — cohort/target registries, `ci_matrix.py`, `repin_prepare.py`, `upstream_watch.py`, fleet-watch, injections, transaction machinery, overlays' patches/tests/copyright stanzas, `upstream/` vendoring, 4,600 lines of workflow — is deliberately not ported.
