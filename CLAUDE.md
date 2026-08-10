# Agent Runbook

This repository is the clean-room v2 of the Vinyl Cache packaging + compatibility-matrix project. Three outputs, only: basic ("ondrej-grade") APT/RPM packages of Vinyl Cache + selected VMODs that are ABI-compatible with each other; a colourful VMOD × engine-version compatibility matrix where red is information, never an emergency; and trunk CI as early warning. `SCOPE.md` is normative for what belongs here; `DESIGN.md` is normative for every schema, CLI, script, and workflow contract, and carries the dated decisions log — change the contract there first, then the code.

`../vcache-packaging` is v1: a ~50,000-line predecessor that grew evidence ledgers, cohort registries, transaction matrices, re-pin automation, and carried patches/tests for third parties. It is a read-only reference (its container build commands and survey data are genuinely useful); its machinery is deliberately not ported. If a change here starts to resemble a v1 mechanism (evidence ledger, completeness gate, auto-re-pin, fleet surveillance, carried patch), stop — that is the failure mode this repo exists to avoid.

## Hard rules

- Verification happens in containers, never on the host. `tools/*.py` is Python 3 stdlib only (host-safe because it builds nothing) — keep it that way; if a tool change would need a dependency, change the design.
- Do not install host tools with Homebrew/pip/cargo/etc. unless the maintainer explicitly asks.
- Every version pin has exactly one machine-readable home: `engines.yml` or `vmods/<id>.yml`. Shell/CI gets values only via `python3 tools/matrix.py env`. Never hand-mirror a pin into a script, workflow, or second file — v1's worst recurring bug was four unguarded copies of the engine pin.
- We carry nothing on behalf of upstreams: no patches, no ported tests, no vendored source. A VMOD that fails against an engine is a red cell, which is a correct and useful result. If a patch is ever truly needed, fork the VMOD repo and point the catalog at the fork.
- A red cell never fails a CI job; only `infra_failed` (the harness itself broke) does. Build scripts exit 0 on classified failures.
- Generated recipes are outputs. Never hand-patch one; fix the generator or the catalog.
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
python3 tools/recipe.py generate --vmod <id> --engine <id> --target <id> --out <dir>
scripts/build-engine.sh <engine-id> <target> <workdir>
scripts/build-vmod.sh <vmod-id> <engine-id> <target> compat|package <workdir>
```

Local runs write under `work/` (gitignored). CI artifact/layout contracts (result filenames `<row>--<engine>--<target>--<mode>.json`, engine artifact re-rooted at `<workdir>/engine/`, packages under `<workdir>/packages/`) are in DESIGN.md.

## Container facts learned the hard way (2026-08-10 proof runs)

- **Native vs emulated matters a lot on this arm64 host**: cachetag's 52-VTC suite took ~60 min under qemu/amd64 but minutes native. Engine prefix tarballs are arch-specific — never mix an amd64 prefix into a native arm64 run. CI (ubuntu-latest) is natively amd64.
- Engine builds: vinyl-9.0.1 ~7 min, varnish-9.0.3 ~5 min (amd64/Rosetta). Varnish 9.0.3 configure requires libssl; vinyl does not.
- The engine prefix ships the daemon but not its shared-lib deps — compat containers must install the engine's library set (libedit, jemalloc, ncurses, pcre2, libunwind) or the load check dies on `libjemalloc.so.2`.
- vmod-dict keeps automake boilerplate in a git submodule (`acvmod`) — checkout must `submodule update --init --recursive`.
- vmodtool.py code generation races under parallel make — generated debian rules use `dh --no-parallel`; the compat harness retries make serially.
- The vinyl prefix ships `vinylapi.pc` + `VINYL_*` m4 macros only (no `varnishapi.pc`, no `VARNISH_*` aliases), so unpatched Varnish-flavoured VMODs (querystring, redis, selector, varnish-modules) fail at bootstrap/configure on vinyl columns. Expected red, noted in each manifest.
- cachetag: `make check` selects the right 52 storage-agnostic VTCs + 1 unit test automatically; never glob `src/vtc/*.vtc` (25+ Fellow-only VTCs need slash storage). `cachetag_pm00007.vtc` (and neighbours like pm00015) has a documented load-flake signature (`HTC eof` / backend connection-refused → 503-where-200) that fires MORE often on fast native machines — one verification invocation lost both make-check attempts to it while a fresh invocation passed 53/53 cleanly. The retry-once policy is not a guarantee; an occasional flake-red cachetag cell is a rerun candidate, not a regression, when the log shows that signature.
- Docker on this host reuses a cached image tag's platform: a stale amd64 `debian:13` makes "native" runs silently Rosetta-emulated even with no `--platform` flag. `docker pull debian:13` refreshes the tag to arm64. Check the built artifacts (`_arm64.deb` vs `_amd64.deb`) when timing looks wrong.
- varnish-modules: nine VMODs, one autotools tree, `-Werror`, no subset build possible. Tag `0.28.0` targets Varnish 9 (branch `9.0` ≈ same). Its 59-test `make check` passed in 24 s against varnish-9.0.3. Debian ships it as one package; so do we (one catalog entry, `package.modules` lists the nine import names). Upstream removed `cookie` (now bundled with the engine).
- cachetag's own `packaging/` tree is a 14-token template whose deps need `vinyld-abi-<hash>`/`vinyld-vrt`/`vinyld-cohort-<id>` engine Provides that v2's engine lacks — that's why decision 8 keeps the uniform generated recipe. Its `make dist` tarball is NOT byte-identical to v1's pinned deterministic archive.
- **dpkg does not enforce a `=` dep in the direction you want.** It validates only the incoming package's own dependencies, never an already-configured reverse-dependency: `dpkg -i` of a newer engine over an exact-pinned VMOD succeeds silently, exit 0, `dpkg --audit` clean, mismatched `.so` left loadable. apt/dnf get it right in every case (kept-back, or removal on `full-upgrade`). Hence `apt install ./*.deb` is the supported install path for Release assets. Proven in `work/abi-upgrade-proof/` (decision 10).
- The real backstop against loading an ABI-mismatched VMOD is the engine, not packaging: a `$ABI strict` VMOD embeds the engine's exact ABI marker (`"Vinyl Cache <ver> <git-hash>"`) and `lib/libvcc/vcc_vmod.c` refuses it with `Incompatible VMOD` at VCL compile, exit 2, no crash.

## State as of 2026-08-10 (end of the build session)

Proven by real local container runs: vinyl-9.0.1 engine (prefix + debs, native arm64 build 97 s), varnish-9.0.3 engine, dict compat+package green, redis red-on-vinyl/green-on-varnish, cachetag green on vinyl with its full suite via `tests: make-check` (53/53 on a clean run), varnish-modules green on varnish (nine .so loaded, 59/59 make check) and honestly red on vinyl at configure, and the `test_failed` negative path end-to-end (a deliberately broken suite yields `test_failed` with the failing test name in detail, exit 0, after exactly one whole-suite retry). Evidence and logs live under `work/verify2*/`.

Not yet proven anywhere: the EL9/RPM container path (written + unit-tested only), all four workflows in actual GitHub CI (repo never pushed — pushing, enabling Pages, and the first `matrix.yml` dispatch are the next milestone), the trunk lane, `release.yml`, and package mode for any VMOD other than dict.

Settled since: the ABI/cohort Provides question is closed as *no change* (DESIGN.md decision 10), backed by a container proof of apt/dpkg upgrade behaviour in `work/abi-upgrade-proof/`. The generated VMOD RPM now arch-qualifies its engine dep (`Requires: vinyl-cache%{?_isa} = <ver>`) to match the engine spec's own `-devel` subpackage.

Open maintainer conversations, deliberately unsettled: a managed APT/RPM repo (Packagecloud-style) as a thin publish step — now understood as partly an ABI-safety decision, since a repo makes the solver rather than the user the enforcement point (SCOPE.md).
