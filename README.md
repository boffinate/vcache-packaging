# vcache-packaging v2

Basic, best-effort APT/RPM packages of pinned Vinyl Cache or Varnish Cache engines plus selected VMODs, for users who do not want to compile. They are convenience artifacts, not distro-quality replacements. The compatibility matrix shows which VMODs build and load against Vinyl Cache and Varnish Cache versions, including trunk, as early warning.

- What this project is and is not: [SCOPE.md](SCOPE.md)
- How it works, schemas and contracts: [DESIGN.md](DESIGN.md)

## Quick start

```sh
python3 tools/matrix.py validate      # catalog well-formed?
python3 tools/matrix.py selftest      # all tooling tests (stdlib only, host-safe)
python3 tools/matrix.py expand --lane release --mode compat --format json
git config core.hooksPath .githooks   # once per clone: run the above before each commit
```

## Editing the catalog

`engines.yml` and `vmods/*.yml` each carry a `# yaml-language-server: $schema=...` line pointing at a generated JSON Schema in `schemas/`. Any editor running [yaml-language-server](https://github.com/redhat-developer/yaml-language-server) — Zed, VS Code and Cursor (the *YAML* extension by Red Hat), Neovim's `yamlls`, JetBrains, Helix — then gives you key autocompletion, hover docs, and red underlines for unknown keys, missing required keys, bad enum values, and unquoted values that should be strings, as you type. No per-editor configuration is needed beyond installing the YAML support.

Those schemas are convenience, not truth. `matrix.py validate` is the authority: it enforces the strict YAML subset and the cross-file rules JSON Schema cannot express (see DESIGN.md decision 11). They are generated — never hand-edit `schemas/*.json`; change `tools/jsonschema_gen.py` or the `KEYS` table in `tools/matrix.py` and re-run `python3 tools/matrix.py schema`.

Container builds (never on the host):

```sh
scripts/build-engine.sh vinyl-9.0.1 debian-13-amd64 work/
scripts/build-vmod.sh dict vinyl-9.0.1 debian-13-amd64 compat work/
python3 tools/matrix.py merge --results-dir work/results --state-file work/state.json
python3 tools/matrix.py render --state-file work/state.json --out work/index.html
```

## Adding a VMOD

Copy an existing `vmods/<id>.yml` (keeping its modeline first line), edit it with the schema guiding you, run `matrix.py validate`, commit. That's the whole process. If it doesn't build against an engine, the matrix will show it red — that's a result, not a problem.
