# Agent Runbook

This repository is the clean-room v2 of the Vinyl Cache packaging + compatibility-matrix project. Three outputs, only: basic ("ondrej-grade") APT/RPM packages of Vinyl Cache + selected VMODs that are ABI-compatible with each other; a colourful VMOD × engine-version compatibility matrix where red is information, never an emergency; and trunk CI as early warning. `SCOPE.md` is normative for what belongs here; `DESIGN.md` is normative for every schema, CLI, script, and workflow contract, and carries the dated decisions log — change the contract there first, then the code.

`../vcache-packaging` is v1: a ~50,000-line predecessor that grew evidence ledgers, cohort registries, transaction matrices, re-pin automation, and carried patches/tests for third parties. It is a read-only reference (its container build commands and survey data are genuinely useful); its machinery is deliberately not ported. If a change here starts to resemble a v1 mechanism (evidence ledger, completeness gate, auto-re-pin, fleet surveillance, carried patch), stop — that is the failure mode this repo exists to avoid.

## Hard rules

- Verification happens in containers, never on the host. `tools/*.py` is Python 3 stdlib only (host-safe because it builds nothing) — keep it that way; if a tool change would need a dependency, change the design.
- Do not install host tools with Homebrew/pip/cargo/etc. unless the maintainer explicitly asks.
- Every version pin has exactly one machine-readable home: `engines.yml` or `vmods/<id>.yml`. Shell/CI gets values only via `python3 tools/matrix.py env`. Never hand-mirror a pin into a script, workflow, or second file — v1's worst recurring bug was four unguarded copies of the engine pin.
- We carry nothing on behalf of upstreams: no patches, no ported tests, no vendored source. A VMOD that fails against an engine is a red cell, which is a correct and useful result. If a patch is ever truly needed, fork the VMOD repo and point the catalog at the fork.
- A red cell never fails a CI job; only `infra_failed` (the harness itself broke) does. Build scripts exit 0 on classified failures.
- Generated recipes are outputs. Never hand-patch one; fix the generator or the catalog. The same goes for `schemas/*.schema.json` — fix `tools/jsonschema_gen.py` or `matrix.py`'s `KEYS` table, then `python3 tools/matrix.py schema`. Every catalog file's first line is a `# yaml-language-server:` modeline; keep it there (a selftest checks).
- Do not hard-wrap Markdown. Do not edit `../vinyl-cache`, `../slash`, or other workspace checkouts from here.
- Catalog YAML house style: block sequences only (no flow `[a, b]`), no `|` block scalars, `description` is a list of plain lines, quote anything that could look numeric. The parser (`tools/yaml_subset.py`) is strict on purpose.

## Commands

```sh
python3 tools/matrix.py validate        # catalog well-formed (engines + vmods)
python3 tools/matrix.py selftest        # all tooling tests, host-safe
python3 tools/matrix.py expand --lane release|trunk [--mode compat|package|all] --format github|json
python3 tools/matrix.py env --engine <id> [--vmod <id>] [--target <id>]   # the only pin source for shell
python3 tools/matrix.py merge --results-dir <dir> --state-file <f>
python3 tools/matrix.py render --state-file <f> --out index.html
python3 tools/matrix.py schema [--check]  # regenerate (or verify) the editor JSON Schemas
python3 tools/recipe.py generate --vmod <id> --engine <id> --target <id> --out <dir>
scripts/build-engine.sh <engine-id> <target> <workdir>
scripts/build-vmod.sh <vmod-id> <engine-id> <target> compat|package <workdir>
```

Local runs write under `work/` (gitignored). CI artifact/layout contracts (result filenames `<row>--<engine>--<target>--<mode>.json`, engine artifact re-rooted at `<workdir>/engine/`, packages under `<workdir>/packages/`) are in DESIGN.md.

## Container facts learned the hard way (2026-08-10 proof runs)

- **Native vs emulated matters a lot on this arm64 host**: cachetag's 52-VTC suite took ~60 min under qemu/amd64 but minutes native. Engine prefix tarballs are arch-specific — never mix an amd64 prefix into a native arm64 run. The target registry selects native x64 and ARM GitHub runners, and the scripts reject a platform mismatch.
- Engine builds: vinyl-9.0.1 ~7 min, varnish-9.0.3 ~5 min (amd64/Rosetta). Varnish 9.0.3 configure requires libssl; vinyl does not.
- The engine prefix ships the daemon but not its shared-lib deps — compat containers must install the engine's library set (libedit, jemalloc, ncurses, pcre2, libunwind) or the load check dies on `libjemalloc.so.2`.
- vmod-dict keeps automake boilerplate in a git submodule (`acvmod`) — checkout must `submodule update --init --recursive`.
- vmodtool.py code generation races under parallel make — generated debian rules use `dh --no-parallel`; the compat harness retries make serially.
- The vinyl prefix ships `vinylapi.pc` + `VINYL_*` m4 macros only (no `varnishapi.pc`, no `VARNISH_*` aliases), so unpatched Varnish-flavoured VMODs (querystring, redis, selector, varnish-modules) fail at bootstrap/configure on vinyl columns. Expected red, noted in each manifest.
- cachetag: `make check` selects the right 52 storage-agnostic VTCs + 1 unit test automatically; never glob `src/vtc/*.vtc` (25+ Fellow-only VTCs need slash storage). `cachetag_pm00007.vtc` (and neighbours like pm00015) has a documented load-flake signature (`HTC eof` / backend connection-refused → 503-where-200) that fires MORE often on fast native machines — one verification invocation lost both make-check attempts to it while a fresh invocation passed 53/53 cleanly. The retry-once policy is not a guarantee; an occasional flake-red cachetag cell is a rerun candidate, not a regression, when the log shows that signature.
- Docker on this host can reuse a cached image tag's platform. The scripts pass the target's explicit `--platform`, reject a mismatched container architecture, and check package metadata, so a stale cached image now fails rather than silently producing the wrong artifact.
- varnish-modules: nine VMODs, one autotools tree, `-Werror`, no subset build possible. Tag `0.28.0` targets Varnish 9 (branch `9.0` ≈ same). Its 59-test `make check` passed in 24 s against varnish-9.0.3. Debian ships it as one package; so do we (one catalog entry, `package.modules` lists the nine import names). Upstream removed `cookie` (now bundled with the engine).
- cachetag's own `packaging/` tree is a 14-token template whose deps need `vinyld-abi-<hash>`/`vinyld-vrt`/`vinyld-cohort-<id>` engine Provides that v2's engine lacks — that's why decision 8 keeps the uniform generated recipe. Its `make dist` tarball is NOT byte-identical to v1's pinned deterministic archive.
- **dpkg does not enforce a `=` dep in the direction you want.** It validates only the incoming package's own dependencies, never an already-configured reverse-dependency: `dpkg -i` of a newer engine over an exact-pinned VMOD succeeds silently, exit 0, `dpkg --audit` clean, mismatched `.so` left loadable. apt/dnf get it right in every case (kept-back, or removal on `full-upgrade`). Hence `apt install ./*.deb` is the supported install path for Release assets. Proven in `work/abi-upgrade-proof/` (decision 10).
- The real backstop against loading an ABI-mismatched VMOD is the engine, not packaging: a `$ABI strict` VMOD embeds the engine's exact ABI marker (`"Vinyl Cache <ver> <git-hash>"`) and `lib/libvcc/vcc_vmod.c` refuses it with `Incompatible VMOD` at VCL compile, exit 2, no crash.

## EL/RPM container facts (2026-08-11, first real CI run)

- **Never `dnf install curl` on an EL base image.** Both almalinux:9 and :10 ship `curl-minimal`, which provides `/usr/bin/curl` and *conflicts* with the full `curl` package, so dnf refuses the whole transaction — `conflicts with curl provided by curl-7.76.1-40.el9.x86_64`. Ask for the file (`/usr/bin/curl`) instead: already satisfied, no-op, no conflict. This took out the entire el9 column of the first CI run.
- **EL10 requires an x86-64-v3 CPU and Rosetta does not emulate that level.** `docker run --platform linux/amd64 almalinux:10` dies instantly with `Fatal glibc error: CPU does not support x86-64-v3`, before any of our script runs. On this arm64 host use the native `el10-aarch64` target. GitHub's x64 runner builds `el10-x86_64`; target names always match the architecture of their artifacts.
- Autotools floors decide which EL generation we can target (decision 12). EL9: autoconf 2.69, automake 1.16.2, and no newer automake in appstream/CRB/EPEL (autoconf alone has an out — `autoconf271`, off-PATH under `/opt/rh/autoconf271/bin`). EL10: autoconf 2.71, automake 1.16.5, autoconf-archive 2023. Of the catalog, `dict`, `remoteip` and `tbf` need autoconf 2.71 + automake 1.16.5; the other five need only 1.12 and build anywhere.
- The three git.gnu.org.ua VMODs publish release tarballs at `download.gnu.org.ua/release/vmod-<id>/` at exactly our pinned versions. A `make dist` tarball carries a generated `configure` and needs no autotools at all — the escape hatch if EL10 ever falls behind the catalog (decision 12).
- `rpmbuild` **does** pass the ambient `PATH` through to its `%build` scriptlet — a `export PATH=...` before it in the same container script is honoured. (Established while testing the EL9 autoconf271 workaround, which is not in the tree; keep it in mind before reaching for a generator change.)
- The engine's own autotools are undemanding: vinyl-9.0.1 configures and builds fine on EL9's 2.69/1.16.2. It is the VMODs that force the newer floor.

## State as of 2026-08-11 (first CI run + the EL10 move)

The first real `matrix.yml` dispatch ran on GitHub Actions and was green apart from the whole el9 column, which died on the `curl`/`curl-minimal` conflict above (one engine job failing, eight VMOD package jobs correctly recording `infra_failed` for the missing engine artifact). Fixing that exposed three further bugs that had been sitting behind it, none of them EL-generation-specific — the RPM path had simply never run far enough to reach them:

- **The engine RPM could not compile any VCL on a clean machine.** `%configure`'s hardening flags include `-specs=/usr/lib/rpm/redhat/redhat-hardened-cc1`, configure seeds `VCC_CC` from the command-line CFLAGS, and `VCC_CC` is baked into the daemon and re-run on the *user's* box at every VCL load. A VCL importing nothing at all failed with `cannot read spec file`. Fixed by setting `VCC_CC` explicitly in `packaging/engine/vinyl-cache.spec` (upstream's Linux/gcc shape minus the `-specs=` options) rather than depending on `redhat-rpm-config` at runtime (20 packages, 21 MB) or unhardening the daemon. Debian never had this: `dpkg-buildflags` emits no `-specs=`.
- **The generated VMOD spec built in parallel**, so the vmodtool.py `vcc_if` race fired (`FileNotFoundError: 'vcc_if.c.tmp2'`). `debian/rules` had guarded this with `dh --no-parallel` since day one; the spec had no equivalent. Fixed with `%global _smp_mflags -j1`, which covers `%make_build` and `%make_install` together.
- **The generated VMOD spec ran a bare `autoreconf -fi`**, while the compat harness has a bootstrap ladder (upstream `bootstrap` → `autogen.sh` → `autoreconf`). cachetag's `bootstrap` exports `VINYLAPI_DATAROOTDIR` from pkg-config and `mkdir -p m4` first; without it aclocal dies on `couldn't open directory '/aclocal'`. The spec now mirrors the ladder.

Proven by local container runs on el10 (arm64 — see the x86-64-v3 note above): engine `pass` including a bare-VCL compile check on a clean container, and package mode `pass` for dict, remoteip, tbf and cachetag. `querystring`, `redis`, `selector` and `varnish-modules` are red at bootstrap/configure, which is the documented expected red on vinyl columns (no `varnishapi.pc`), not a regression. Evidence under `work/el10-v2/`.

Still not proven anywhere: **any x86_64 EL artifact** (impossible on this host), the four workflows past the point the first run reached, the trunk lane, `release.yml`, and Pages publication.

Worth doing next, deliberately not done today: the cell `detail` field is close to useless on RPM failures — it scraped a stray C comment for one cell and `RPM build errors:` with no cause for others, so a red cell on the published matrix tells a reader nothing and forces a log dive. The classifier picks the wrong lines out of rpmbuild output.

## State as of 2026-08-10 (end of the build session)

Proven by real local container runs: vinyl-9.0.1 engine (prefix + debs, native arm64 build 97 s), varnish-9.0.3 engine, dict compat+package green, redis red-on-vinyl/green-on-varnish, cachetag green on vinyl with its full suite via `tests: make-check` (53/53 on a clean run), varnish-modules green on varnish (nine .so loaded, 59/59 make check) and honestly red on vinyl at configure, and the `test_failed` negative path end-to-end (a deliberately broken suite yields `test_failed` with the failing test name in detail, exit 0, after exactly one whole-suite retry). Evidence and logs live under `work/verify2*/`.

Not yet proven anywhere (as of that date): the EL9/RPM container path (written + unit-tested only), all four workflows in actual GitHub CI (repo never pushed — pushing, enabling Pages, and the first `matrix.yml` dispatch are the next milestone), the trunk lane, `release.yml`, and package mode for any VMOD other than dict.

Settled since: the ABI/cohort Provides question is closed as *no change* (DESIGN.md decision 10), backed by a container proof of apt/dpkg upgrade behaviour in `work/abi-upgrade-proof/`. The generated VMOD RPM now arch-qualifies its engine dep (`Requires: vinyl-cache%{?_isa} = <ver>`) to match the engine spec's own `-devel` subpackage.

Open maintainer conversations, deliberately unsettled: a managed APT/RPM repo (Packagecloud-style) as a thin publish step — now understood as partly an ABI-safety decision, since a repo makes the solver rather than the user the enforcement point (SCOPE.md).
