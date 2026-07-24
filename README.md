# vinyl-packaging

Packaging and release-coordination repository for the Vinyl Cache package cohort.

This repository owns the parts of the [binary packaging and distribution plan](../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md) that are about the *cohort* rather than about any single VMOD:

- **the cohort registry** — the compatibility manifests that pin exactly which Vinyl source, patch set, build profile, and strict ABI a set of packages was built from, plus the tooling that validates them and generates every native package version string, artifact filename, and ABI dependency expression from them;
- **Vinyl Cache packaging** — the Debian, RPM, and other native recipes that build the `vinyl-cache` runtime, development, and debug packages themselves;
- **repository publication tooling** — the signed, cohort-aware apt/RPM repository staging, promotion, retention, and rollback machinery that a supported channel needs.

Individual VMODs are packaged in their own repositories. `libvmod-cachetag` keeps its own `packaging/` recipes, source-archive script, configure-time ABI checks, and security policy, so that each VMOD release records the exact recipes used for its own artifacts.

## Why the split

A cohort is a set of packages — one Vinyl runtime plus every strict-ABI VMOD built and tested against it. The registry that identifies a cohort therefore cannot belong to any one member of it: cachetag is the first VMOD in the cohort, not its owner. Keeping the registry here means a second VMOD can join without either VMOD repository depending on the other, and it means the Vinyl packaging work that produces the runtime lives next to the manifests that describe it.

The registry moved here from `libvmod-cachetag` on 2026-07-24 (maintainer-approved), at the point where implementation reached the Vinyl 9 packaging step. Git history did not transfer; see [`docs/20260724_2138_note_step-7a-repo-scaffold-and-registry-move.md`](docs/20260724_2138_note_step-7a-repo-scaffold-and-registry-move.md) and, in the cachetag repository, `docs/20260724_2017_note_step-3-release-manifests.md` for the registry's original design record.

## Layout

```text
registry/            the cohort registry: compatibility manifests
  README.md          normative schema description
  cohorts/           one file per coordinated project cohort
  targets/           one file per distro/arch build within a cohort
  distro-native/     builds against a distribution's own Vinyl packages
tools/               Python 3 stdlib tooling: validation and package-metadata generation
upstream/            vendored third-party source material, with provenance
  pkg-vinyl-cache/   the audited upstream packaging recipes (not yet modernised)
docs/                design notes and session records
```

## Quick start

The tooling is pure Python 3 standard library. It never builds or tests anything, so it is safe to run on the host:

```sh
python3 tools/release_tool.py validate
python3 tools/release_tool.py validate --require-releasable
python3 tools/release_tool.py cohort-id --cohort vinyl-9.0.0-000000000000
python3 tools/release_tool.py metadata --cohort <id> --target debian-13-amd64
python3 tools/release_tool.py selftest
```

Manifests record cachetag's version, which is cross-checked against `AC_INIT` in the cachetag checkout's `configure.ac`. That checkout defaults to the sibling `../libvmod-cachetag`; point the tooling elsewhere with `--cachetag-src PATH` or the `CACHETAG_SRC` environment variable.

Every package build, by contrast, happens in a container. See [AGENTS.md](AGENTS.md).

## Status

Early. The checked-in cohort is a `template` with placeholder identity values: the first real cohort identifier is assigned only once the Vinyl source archive, the ordered downstream patch set, and the production build-profile revision are pinned, and the plan forbids deriving it from a mutable sibling checkout. The vendored `upstream/pkg-vinyl-cache` recipes are audit input, not a release-ready base.

Nothing here is published, signed, or supported yet.

## Support statement

The durable statement this repository exists to make true, quoted from the plan:

> Official VMOD binaries are supported with the Vinyl packages from the same repository and release cohort. Distribution-provided Vinyl packages are supported only where a VMOD package has been built and tested specifically against that distribution package revision.
