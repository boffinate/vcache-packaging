# vcache-packaging v2

Basic APT/RPM packages of Vinyl Cache + selected VMODs, and a compatibility matrix showing which VMODs build and load against which Vinyl Cache / Varnish Cache versions — including trunk, as early warning.

- What this project is and is not: [SCOPE.md](SCOPE.md)
- How it works, schemas and contracts: [DESIGN.md](DESIGN.md)

## Quick start

```sh
python3 tools/matrix.py validate      # catalog well-formed?
python3 tools/matrix.py selftest      # all tooling tests (stdlib only, host-safe)
python3 tools/matrix.py expand --lane release --mode compat --format json
```

Container builds (never on the host):

```sh
scripts/build-engine.sh vinyl-9.0.1 debian-13-amd64 work/
scripts/build-vmod.sh dict vinyl-9.0.1 debian-13-amd64 compat work/
python3 tools/matrix.py merge --results-dir work/results --state-file work/state.json
python3 tools/matrix.py render --state-file work/state.json --out work/index.html
```

## Adding a VMOD

Write `vmods/<id>.yml` by hand (see DESIGN.md for the schema), run `matrix.py validate`, commit. That's the whole process. If it doesn't build against an engine, the matrix will show it red — that's a result, not a problem.
