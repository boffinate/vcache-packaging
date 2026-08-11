# Design

Status: Normative for the initial build. Contracts here (schemas, CLI surfaces, script interfaces, statuses) are what the components are written against. Change the contract here first, then the code.

This is a clean-room successor to `../vcache-packaging` (referred to as **v1** below). v1 remains readable as a reference; nothing in v1 is imported wholesale without being listed in the port map at the bottom.

## Repository layout

```
SCOPE.md  DESIGN.md  README.md
engines.yml              # hand-maintained: every engine version we test/package
vmods/<id>.yml           # hand-maintained: one file per selected VMOD
schemas/                 # generated: JSON Schema for the two catalog shapes (editors only)
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
  render-pages.yml       # dispatch: rebuild Pages from saved state only
  release.yml            # dispatch: build packages, create GitHub Release
.githooks/
  pre-commit             # validate + schema --check + selftest; enabled via core.hooksPath
work/                    # gitignored scratch (container mounts, results, artifacts)
```

## Catalog schemas

Parsed with `tools/yaml_subset.py`: mappings, block sequences, scalars-as-strings only — no flow `[a, b]` lists, no `|` block scalars, no anchors. Mapping keys may contain lowercase letters, digits, `_`, `-`, and `.` (series names like `vinyl-9.0` must be expressible as `by_series` keys). Multi-line prose (`description`) is a list of plain scalar lines. Unknown keys are validation errors. The inline examples below use flow-list shorthand for brevity only; the real files use block sequences throughout.

Both key sets of every mapping below live once, in `tools/matrix.py`'s `KEYS` table; the validator and the generated editor schemas both read it, so they cannot drift (decision 11).

### engines.yml

```yaml
schema: engines/1
targets:
  debian-13-amd64:
    image: debian:13
    format: deb
    runner: ubuntu-24.04
    platform: linux/amd64
    package_arch: amd64
  debian-13-arm64:
    image: debian:13
    format: deb
    runner: ubuntu-24.04-arm
    platform: linux/arm64
    package_arch: arm64
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
    targets: [debian-13-amd64, debian-13-arm64, el10-x86_64, el10-aarch64]
```

Rules: each top-level target is the complete execution contract: image, package format, native GitHub runner, Docker platform, and expected binary-package architecture. Engine rows only select its target ids. `kind: release` requires `tarball_url` + `sha256`; `kind: trunk` requires `git_url` + `branch` and forces `packages: "false"`. Trunk engines carry a self-named `series` (`vinyl-trunk`); the resolution rule never consults `series` for trunk engines. `packages: "true"` requires `kind: release` and `family: vinyl` (Varnish is matrix-only for now — reversible decision, see Decisions). Compat and package jobs run on every listed target.

Initial contents: `vinyl-9.0.1` (release, packages, both targets — pin from v1 `recipes/debian-13/pins.env`), `varnish-9.0.3` (release, matrix-only, debian target — pin from v1 `survey/harness/pins.env`), `vinyl-trunk` and `varnish-trunk` (trunk, matrix-only — git URLs/branches from v1 `tools/upstream_watch.py` constants).

### vmods/<id>.yml

```yaml
schema: vmod/1
id: dict
upstream:
  git: https://git.gnu.org.ua/vmod-dict.git
  homepage: https://...        # optional; matrix row link and package homepage
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
  families: [varnish]   # optional: engine families the build system supports; gates package mode only
tests: make-check       # optional: run upstream's own `make check` in compat mode; absent = no suite
engine_source: required # optional: configure needs the engine source tree (VINYLSRC); absent = prefix only
```

`package.modules` exists for multi-VMOD repositories (varnish-modules ships nine `.so` from one tree; upstream cannot build a subset, and Debian itself ships it as one package — so it is ONE catalog entry, one matrix row, one package). Module names must match `[a-z][a-z0-9_]*`. `tests: make-check` runs upstream's suite from upstream's tree — we still carry no tests; a VMOD whose suite needs fixtures we won't provide simply doesn't set it.

`package.families` (optional) lists the engine families — `vinyl` | `varnish`, the same vocabulary as engines.yml `family` — whose engines the VMOD's build system can configure against. It gates **package-mode expansion only**: a package cell is expanded only when the engine's family is listed; absent means no restriction. Compat mode ignores it entirely — the no-skip rule below stands, and a Varnish-flavoured VMOD still renders its honest red compat cell on vinyl columns (decision 13).

`engine_source` (optional; the one legal value is `required`) declares that the VMOD's configure demands the engine's *source tree* on top of the installed prefix — UPLEX's `VINYLSRC` convention for modules that reach into engine internals (pesi, tus, zipflow). When set, both build modes provision one inside the container before the VMOD build: release engines from `source.tarball_url` (sha256-verified; the dist archive carries every generated file except the VSC headers, which the step regenerates with the installed engine's own vsctool), trunk engines by a shallow clone of `source.git_url`/`branch`. The scripts export `VINYLSRC` and `VARNISHSRC` at the extracted tree and change nothing else; entries without the key see no new behaviour. For trunk engines the clone may sit a few commits past the one the engine artifact was built from — accepted skew at this quality bar; a real mismatch surfaces as an honest red cell (decision 14).

**Source resolution rule** (the one rule, used everywhere): for engine E, a VMOD builds `sources.head` if `E.kind == trunk`, else `sources.by_series[E.series]` if present, else `sources.default`. There is no "skip": every VMOD gets a compat cell for every engine column, and an incompatible pairing simply fails and renders red. Package mode is the one exception — it is gated by `package.families` (decision 13), because a package build that cannot succeed is a doomed product build, not information the way a compat red is.

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

The first four come from compat mode (autotools configure, make, a `vcl.load`-style check compiling a minimal VCL that imports every built `.so` against the built engine, then — only when the manifest says `tests: make-check` — upstream's own `make check`, retried once whole on failure to absorb known VTC load-flakes, `test_failed` with the failing test names in `detail` if it fails twice). `package_failed`/`install_failed` come from package mode (recipe build, then install-and-load in a fresh container; the installed load check compiles one VCL importing every name in `package.modules`). `infra_failed` means the harness itself broke and is the **only** status that fails a *build* job. The one other deliberately red job is `release.yml`'s gate (decision 13): it fails when any cell of a gated target is not `pass` — the build jobs beneath it still exit 0 and classify honestly.

## The matrix page

The page renders a separate matrix for every target. Rows are engines' own build row first, then one row per VMOD (catalog order). A VMOD row links to its explicitly configured `upstream.homepage`; leaving that optional field out leaves the label unlinked. Columns are the engines configured for that target, in `engines.yml` order (release engines, then trunk engines). Cell colour by worst status across that target's modes; green `pass`, red any `*_failed` except infra, grey `infra_failed` or no data. That single colour is the page's entire at-a-glance vocabulary — there are no per-mode badges or segments (tried and removed 2026-08-11: with decision 13's `families` gating, compat and package rarely disagree on a cell that exists at all, a packaging-only failure is the release gate's job to surface, and a second colour system answered a maintainer question at the cost of confusing the reader's one question, "does this work?"). Directly under the page title sits a one-line key for someone who stumbled onto the page, exactly: "Rows are modules, columns are engine versions. Green: works. Red: doesn't — usually upstream doesn't support that engine yet. Grey: not tested." The tooltip is the detail surface and speaks human: one plain sentence per mode ("compiles from source against this engine and loads", "the ready-to-install package (.deb/.rpm) built, installed and loaded", the harness-problem variant for infra) with its status, plus ref, commit, timestamps, link to the producing run. Light and dark both work. Rendering ports v1 `tools/status_page.py`'s bones (state-file merge, self-contained HTML, light/dark) with the new axes. State lives in `matrix-state.json` on orphan branch `ci-state/matrix`; Pages deploys from `matrix.yml` and `trunk.yml` on main only, shared concurrency group. `render-pages.yml` can be dispatched to rebuild Pages from that saved state without rebuilding an engine or VMOD.

## tools/matrix.py CLI contract

```
matrix.py validate                                      # catalog well-formed; exit 1 on error
matrix.py expand --lane release|trunk [--mode compat|package|all] --format github|json
matrix.py resolve --vmod ID --engine ID                 # print resolved ref+version JSON
matrix.py env --engine ID [--vmod ID] [--target ID]     # sh-sourceable pins for scripts
matrix.py merge --results-dir DIR --state-file FILE     # fold cell JSONs into state
matrix.py render --state-file FILE --out index.html
matrix.py schema [--out DIR] [--check]                  # write (or verify) schemas/*.schema.json
matrix.py selftest
```

`schema` writes `schemas/engines.schema.json` and `schemas/vmod.schema.json` from the `KEYS` table and the enum constants in `matrix.py`; `--check` regenerates in memory and exits 1 if the checked-in files differ. The schemas are **editor ergonomics, not a second validator** — see decision 11.

`expand` emits rows `{row, engine, target, mode, runner}` — the GitHub Actions job matrix. Package-mode VMOD rows are emitted only when the engine's family is listed in the VMOD's `package.families` (absent = all families; decision 13). `--format github` prints exactly two `key=<single-line-json>` lines for `$GITHUB_OUTPUT`: `engines=[...]` (unique engine×target pairs) and `vmods=[...]` (VMOD rows only, engine rows excluded). `env` is the **only** way shell/CI gets version strings and target metadata; nothing like v1's hand-mirrored `pins.env` exists. `merge` rule: newest `finished_at` per (row, engine, target, mode) wins; globs `*.json` recursively; a state file full of red cells is still a successful merge/render.

## tools/recipe.py

Renders a Debian source dir and an RPM spec from `packaging/` templates plus one VMOD's catalog entry and one engine's `env` values. Package naming (uniform, all VMODs including cachetag): Debian binary `vinyl-vmod-<id>`, RPM `vinyl-vmod-<id>`. Version: `<upstream_version>-1~vinyl<engine_version>` style Debian revision / `Release: 1.vinyl<engine_version>` RPM. ABI coherence is expressed as an exact-version dependency on the engine runtime package (`Depends: vinyl-cache (= <engine pkg version>)` / `Requires: vinyl-cache%{?_isa} = <ver>`). The RPM side is architecture-qualified: a VMOD is a dlopen()ed plugin that must match the daemon's architecture exactly, and without `%{?_isa}` a multilib host could satisfy an x86_64 VMOD with an i686 engine. Debian needs no equivalent — its dependency resolution is architecture-aware through the package architecture itself. `debian/copyright` is generated minimal: SPDX id + pointer to upstream license file. No lintian/rpmlint gating. The generated rules override `dh_auto_test` to a no-op and the spec carries no `%check`: upstream suites run only where the catalog opts in (`tests:`, compat lane) — otherwise dh's default `make check` inside every deb build would import per-suite flakes into package cells (pesi's ESI logexpect VTCs found this).

## Script contracts

```
scripts/build-engine.sh <engine-id> <target> <workdir>
scripts/build-vmod.sh   <vmod-id> <engine-id> <target> <mode> <workdir>
```

Both run everything inside the target registry's container image, pull pins and target metadata via `matrix.py env`, and always write a cell result JSON into `<workdir>/results/` — including on failure, classifying the failure honestly. The scripts select Docker's declared platform, reject a non-native host or container, and verify every finished `.deb`/`.rpm` records the target's declared architecture. Debian and Ubuntu share the generated Debian recipe templates and `build_deps.debian` catalog field; split them only when a genuine distro package difference demands it. Engine build produces, per target: a relocatable prefix tarball (for compat mode consumers) and, if `packages: "true"`, the engine .deb/.rpm set (adapted from v1's engine build + v1 `upstream/pkg-vinyl-cache` derivation, simplified — plain `dpkg-buildpackage`/`rpmbuild` in a container, no pbuilder/mock/sbuild). VMOD compat mode: untar engine prefix, autotools build against it, minimal-VCL load check. VMOD package mode: install engine packages, render recipe via `recipe.py`, build, then fresh-container install + load check. When the manifest sets `engine_source: required`, both modes run one extra provisioning step (shared `lib.sh` emitter) between engine setup and the VMOD build: fetch the engine source, regenerate VSC headers via the installed vsctool, export `VINYLSRC`/`VARNISHSRC`. The generated recipes are untouched — the exported environment reaches configure through dpkg-buildpackage and rpmbuild, the same ambient-environment passthrough the EL9 autoconf271 probe established for PATH (decision 14). Exit code 0 unless infra_failed.

## Workflows

- `ci.yml` — PR + push to main: `matrix.py validate` + `matrix.py schema --check` + `matrix.py selftest`. Fast, host-safe, no containers. Same three commands as the pre-commit hook, so a hook-skipping commit (`--no-verify`) is still caught.
- `matrix.yml` — workflow_dispatch + weekly: expand release lane → engine jobs (upload prefix/package artifacts) → VMOD jobs (fail-fast off, never red on cell failure) → merge → render → commit state to `ci-state/matrix` → deploy Pages. Main-branch runs only publish.
- `trunk.yml` — cron Mon/Thu + dispatch: same shape for trunk engines, compat mode only. **No change-gating, no issue filing, no re-pin PRs** — it just runs; the matrix page is the notification surface.
- `render-pages.yml` — workflow_dispatch only: render the saved `ci-state/matrix` state and deploy it to Pages. It rebuilds no engine or VMOD and does not alter state.
- `release.yml` — workflow_dispatch only: build vinyl release engine packages + every package-eligible VMOD (`package.families`, decision 13) on all package targets, then gate per (engine, target) pair: unless every expected cell — the engine's own build plus every eligible VMOD — is `pass`, that pair publishes nothing and the job fails. Green pairs (re)publish a GitHub release at the stable tag `<engine-id>-<target>` (delete + recreate with `--cleanup-tag`, so a re-dispatch after a fix replaces the release) with .deb/.rpm/SHA256SUMS and a body that states the all-or-nothing contract and lists the packages.

## Decisions (all reversible; no users yet)

1. **Package naming**: uniform `vinyl-vmod-<id>` for every VMOD on both package formats, including cachetag. Rationale: one code path, obvious provenance, no collision with distro `libvmod-*`. cachetag's own repo keeps its own audited `libvmod-cachetag` recipe for its own releases; this project does not consume it.
2. **Varnish is matrix-only**: we test compat against Varnish releases/trunk but package only against Vinyl. Flipping `packages` on a varnish engine later is the designed path.
3. **Publication**: GitHub Releases now; managed APT/RPM repo later as a thin publish script.
4. **No change-gating on trunk runs**: two scheduled runs a week, unconditional. Cost is a handful of container builds; the v1 watcher/gate machinery is not ported.
5. **No VMOD commit pins**: the hand-written ref is the pin. Moved-tag paranoia, archive digests per VMOD, and poisoned-tag tracking are not ported.
6. **Exact-version engine dependency** for VMOD packages (not ABI-hash ranges): honest and simple at this quality bar.
7. **Container images pinned by tag** (`debian:13`, `ubuntu:26.04`, `almalinux:10`), not digest; the cell result records what actually ran.
8. **cachetag uses the uniform generated recipe, not its own packaging** (2026-08-10). Its `packaging/` tree is a 14-token template whose dependency model needs `vinyld-abi-<hash>`/`vinyld-vrt`/`vinyld-cohort-<id>` engine Provides that v2's engine deliberately lacks; its distro-native variant is semantically what our generated recipe already emits. Its VTC suite runs anyway via `tests: make-check` — proven 52/53 against our vinyl-9.0.1 prefix (the one failure being the documented pm00007 load-flake, hence the retry-once policy). Whether v2's engine packages should grow ABI/cohort Provides was left open here; decision 10 closes it.
9. **varnish-modules is one catalog entry** (2026-08-10): one row, one package with all nine `.so`, matching both upstream's all-or-nothing build and Debian's own packaging of it. Expected red on vinyl columns at configure (`varnishapi.pc` absent) until upstream or a fork accommodates vinyl — that red is the matrix doing its job.
10. **No ABI/cohort Provides on the engine packages** (2026-08-10, closes the question left open in decision 8). The exact-version dependency of decision 6 stays as the only ABI coherence mechanism. Three findings settle it, the last two proven by container run (`work/abi-upgrade-proof/REPORT.md`):
    - v1's ABI token was a hash of the **engine source revision** (`vcache-packaging-old/tools/metadata.py:113`, the same hash vinyl bakes into `include/vmod_abi.h`). For a tagged release that is 1:1 with the version number, so `Depends: vinyld-abi-<hash>` and `Depends: vinyl-cache (= 9.0.1-1)` express an identical lockstep. v1 emitted both; v2 keeps the one that needs no engine-build → VMOD-recipe data channel. The Provides only start paying for themselves with multiple builds per source revision, third-party VMOD packagers, or a cohort model — none of which apply.
    - **Through apt the exact-version dep is sufficient.** Both-rebuilt upgrades in one transaction; engine-only leaves `vinyl-cache` *kept back*; `full-upgrade` removes the VMOD rather than mismatching it. No path loads ABI-mismatched code.
    - **Through bare `dpkg -i` nothing at the packaging layer helps** — and that includes the ABI Provides, which are equally a `Depends:` on the VMOD side. dpkg validates only the incoming package's own dependencies and never re-checks an already-configured reverse-dependency: installing a newer engine over an exact-pinned VMOD succeeds silently, exit 0, with `dpkg --audit` clean. This is dpkg semantics, not a weakness of decision 6, and it is why the supported install path for Release assets is `apt install ./*.deb`.

    The backstop for the `dpkg -i` path is the engine's own runtime check, not packaging: a `$ABI strict` VMOD embeds the engine's exact ABI marker and `lib/libvcc/vcc_vmod.c` refuses it with `Incompatible VMOD` at VCL compile time. Demonstrated against a byte-patched `.so` — a clean compile error, exit 2, no crash. Reopen this decision if v2 ever ships more than one engine build per source revision, or if someone else packages VMODs against our engine.
12. **The RPM target is EL10, not EL9** (2026-08-11). `el9-x86_64` is replaced by `el10-x86_64` on `almalinux:10`; native ARM builds use the sibling `el10-aarch64` target. EL9's autotools are simply too old for part of the catalog: `dict`, `remoteip` and `tbf` (all three from git.gnu.org.ua) declare `autoconf >= 2.71` and `automake >= 1.16.5`, against EL9's 2.69 and 1.16.2. Autoconf has an escape hatch there — EL9 packages 2.71 as `autoconf271`, installed off-PATH under `/opt/rh/autoconf271/bin` — but automake has none: 1.16.2 is the only automake in appstream, CRB or EPEL. EL10 ships autoconf 2.71 and automake 1.16.5 in appstream, and the full engine and VMOD dependency sets install there unchanged. The other five VMODs need only automake 1.12 and were never affected.

    Three alternatives were considered and rejected:
    - **Building automake from source into the EL9 container.** This is the v1 failure mode in miniature: the harness would acquire, and thereafter own, a bespoke toolchain, per target, forever.
    - **Keeping EL9 with three red cells.** Red is normally information, but this red would have been a lie. Those VMODs build on EL9 perfectly well — they just cannot be re-`autoreconf`'d there. The cell would have read "dict does not work on EL9" when the truth was "our harness chose to clone git instead of using the release tarball".
    - **Upstream release tarballs for the three GNU VMODs.** They do publish them (`download.gnu.org.ua/release/vmod-{dict,tbf,remoteip}/`, at exactly the pinned versions), and a `make dist` tarball carries a pre-generated `configure` that needs no autotools at all — this is how a distro packager would really build them. It remains the correct fallback if EL10 ever falls behind the catalog again, but it costs the catalog a second source type, so it is not worth doing while a plain distro bump solves the same problem.

    Two consequences, both accepted. **RHEL/Alma/Rocky 9 get no packages**, despite EL9 being supported until 2032 and being the larger installed base today; there are no users yet, and adding it back alongside is a catalog edit if that stops being true. **`el10-x86_64` cannot be built on an arm64 macOS host at all**: EL10 requires an x86-64-v3 CPU and Rosetta does not emulate that level, so an emulated amd64 EL10 container dies immediately with `Fatal glibc error: CPU does not support x86-64-v3`. Local ARM proof uses the distinct `el10-aarch64` target; CI builds each published architecture on its matching native runner.

11. **Editor schemas are advisory; `validate` stays the authority** (2026-08-11). `schemas/*.schema.json` exist so `yaml-language-server` (Zed, VS Code/Cursor, Neovim `yamlls`, JetBrains, Helix) can red-underline a bad catalog *as it is typed* — the one thing a strict parser plus a CLI cannot do. Three properties keep this from becoming a second, drifting source of truth:
    - **Generated, never hand-written.** They are outputs, like the packaging recipes: fix the generator or the `KEYS` table, never the JSON. `matrix.py schema --check` fails CI and the pre-commit hook if the checked-in files do not match what the generator emits, so drift is caught the day it happens.
    - **Deliberately weaker than `validate`.** The language server parses real YAML, not our subset, so it accepts anchors, flow mappings, and tabs that `yaml_subset.py` rejects; and JSON Schema cannot express the cross-file or cross-record rules (`id` matching the filename stem, `by_series` keys naming a declared engine series, duplicate ids). Green in the editor therefore means "probably fine", never "valid". Nothing in CI or the build scripts reads these files.
    - **Structural only.** No catalog *data* is baked in — the `by_series` key pattern is a charset, not an enum of current engine series — so the schemas change only when the schema changes, not when a pin moves.

    Everything is typed `string` with `additionalProperties: false`, which mirrors the parser's no-coercion rule and makes the editor flag the house-style slips (`packages: true`, `version: 1.7`) that quoting exists to prevent.

    Behaviour proven in a container (`work/schema-proof/`, ajv draft-07 + a YAML 1.2 reader, the same pairing the language server uses): the real catalog validates clean, 14 negative cases are caught with the message naming the offending key, and the three documented limits above are demonstrated as *accepted* rather than assumed.

13. **Releases are all-or-nothing per target, at stable replaceable tags** (2026-08-11). `release.yml` stops omitting failed packages. For each (packaging engine, target) pair it now applies a gate: every expected cell — the engine's own build plus every package-eligible VMOD — must be `pass` (a missing result file counts as a failure), or that pair publishes nothing and the run goes red. A green pair (re)publishes a GitHub release at the stable tag `<engine-id>-<target>` (e.g. `vinyl-9.0.1-el10-x86_64`), delete-and-recreate with `--cleanup-tag`, so fixing a failure and re-dispatching *replaces* the release instead of accumulating dated tags. History is one release per engine version per target — GitHub Releases itself is the historic record a per-release matrix page would have duplicated.

    The user contract this buys (role-played from the installer's side before deciding): **a release existing means the full module set built.** An upgrade can never silently drop a module because its build happened to fail — under exact-version deps (decision 6) that scenario ends with apt removing the VMOD at the user's upgrade prompt and VCL failing to compile at their next restart, the worst possible place for a packaging failure to surface. The previous "omitted and listed in the body" behaviour was disclosure, not protection; nobody re-reads release notes mid-upgrade. The release body states the contract in one line so the guarantee is legible to the person it protects.

    Two supporting changes:
    - **`package.families` on the VMOD schema.** Four catalog VMODs (querystring, redis, selector, varnish-modules) are Varnish-flavoured and cannot package against vinyl unpatched (no `varnishapi.pc`; SCOPE forbids carrying patches). Under a gate their guaranteed-red package cells would make every vinyl release impossible — and they were never going to ship. They now declare `families: [varnish]`, and package mode expands only for listed families. This is a statement of build-system fact, not an expected-failures ledger: compat mode still tries every pairing and renders the honest red (the no-skip rule stands), and `matrix.yml --mode all` stops paying for eight doomed package builds a week as a side effect.
    - **A deliberate carve-out from "a red cell never fails a CI job":** the rule now reads "never fails a *build* job". The release gate job — the product lane, where incompleteness is a defect, unlike the information lane where red is content — fails on any non-pass and publishes nothing for that pair. Build scripts and every other workflow are untouched.

    Per-target independence is the point of the tag scheme: a Debian-only fix replaces only the Debian release, and an EL10 failure never holds Debian packages hostage. What the gate cannot do is repeal reality: if an engine bump permanently breaks a VMOD, releases for that target stay blocked until the catalog explicitly drops it — an accepted forcing function that converts build accidents into recorded catalog decisions. Rejected alternatives: keeping omit-and-list (fails the user contract above); a Releases page on the Pages site fed by per-release state files on `ci-state/matrix` (solved failure *visibility* but not release replacement, and added render/state machinery this decision makes unnecessary — a failed release is now a red workflow run and an untouched previous release).

14. **`engine_source: required` — a catalog flag, not carried scripts, provisions the engine source tree** (2026-08-11). Three uplex deep-integration VMODs (pesi, tus, zipflow) hard-require `VINYLSRC` pointing at an engine *source tree*; the installed prefix cannot satisfy them and SCOPE forbids patching it away. Container-proven mechanism (pesi vs vinyl-9.0.1, logs in the session scratchpad, recorded in `docs/20260811-vmod-candidate-survey.md`): extract the engine's release dist tarball — it carries every generated file except the VSC headers — regenerate those with the installed engine's own vsctool (`VSC_main.h` was the only missing file; one command), export `VINYLSRC`, and pesi builds and both its modules load. Configuring the engine tree in-tree was rejected: it drags in python3-sphinx and minutes of build for nothing a VMOD needs. Spelling: one optional catalog key gating one shared code path in `build-vmod.sh` (both modes; the generated recipes stay untouched because the exported environment reaches configure through dpkg-buildpackage/rpmbuild). The alternative — per-VMOD prep scripts — collapses into the same design, since the catalog must mark these VMODs either way for recipe/expansion purposes, and would add carried per-VMOD files that SCOPE rules out. Earmarked deliberately: if the catalog ever admits non-autotools families (varnish-rs Rust VMODs; cmake-built riscv/tinykvm), a shared prep/build script mechanism becomes the right spelling, and this flag should convert to one script referenced by the VMODs that need it.

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
