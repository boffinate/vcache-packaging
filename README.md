# vcache-packaging

Packaging and release-coordination repository for the Vinyl Cache package cohort.

The normative project boundary is [SCOPE.md](SCOPE.md). In short, this repository builds and tests a selected package set, publishes directly installable artifacts, and may publish them through narrowly focused managed APT and RPM repositories. It does not aim to become a distribution or build general-purpose package and source-archive infrastructure.

This repository owns the package-cohort work described by the historical [binary packaging and distribution plan](../libvmod-cachetag/devdocs/docs/20260724_1526_plan_binary-packaging-and-distribution.md), within the narrower boundary established by `SCOPE.md`:

- **the cohort registry** — the compatibility manifests that pin exactly which Vinyl source, patch set, build profile, and strict ABI a set of packages was built from, plus the tooling that validates them and generates every native package version string, artifact filename, and ABI dependency expression from them;
- **Vinyl Cache packaging** — the Debian, RPM, and other native recipes that build the `vinyl-cache` runtime, development, and debug packages themselves;
- **repository publication tooling** — the signed, cohort-aware APT/RPM repository staging, promotion, retention, and rollback integration that the selected package set needs, preferably through a managed service rather than infrastructure operated here.

## How VMOD recipes are owned

There are two cases, and which one applies depends on whether the project controls the VMOD's releases.

- **A VMOD whose releases this project controls keeps its own recipes.** `libvmod-cachetag` keeps its own `packaging/` recipes, source-archive script, configure-time ABI checks, and security policy, so that each cachetag release records the exact recipes used for its own artifacts.
- **A third-party VMOD gets Debian and RPM recipes generated here**, from reviewed per-VMOD adapter data held in this repository. We do not ask an upstream we do not control to carry packaging for us, and we do not fork it to add some.

Upstream-maintained packaging is used only where it already exists, is tied to the exact release source we selected, and independently meets this project's dependency, provenance, hardening, payload, and testing requirements. That is a high bar and most third-party VMODs will not clear it, usually because their packaging targets a different distribution's conventions or has drifted from their current release.

**The absence of upstream `debian/` or `rpm/` packaging is not a barrier to selecting a VMOD.** It is the normal case: the downstream provider absorbs it. Generated recipes are generated content under the rules in [AGENTS.md](AGENTS.md) — a generated recipe that disagrees with the manifest or adapter data is a generator bug, not something to hand-patch. See [`docs/20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md`](docs/20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md).

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
  vmods/             one file per selected VMOD: its source channels and CI lanes
recipes/vmods/       inputs the generated third-party VMOD recipes are rendered from
  templates/         the generic Debian and RPM recipes, as token templates
  adapters/          what is true of every VMOD built the same way
  overlays/          what is true of one VMOD: names, licence, payload, deps
  licenses/          reviewed debian/copyright licence stanzas
tools/               Python 3 stdlib tooling: validation and package-metadata generation
  vmod_recipe.py     renders a VMOD's Debian or RPM recipe from those inputs
upstream/            legacy audited packaging-recipe input, with provenance
  pkg-vinyl-cache/   audited upstream recipes; not a general source archive store
docs/                design notes and session records
SCOPE.md             normative project boundary
```

## Quick start

The tooling is pure Python 3 standard library. It never builds or tests anything, so it is safe to run on the host:

```sh
python3 tools/release_tool.py validate
python3 tools/release_tool.py validate --require-releasable
python3 tools/release_tool.py cohort-id --cohort vinyl-9.0.1-ac4f719c16f4
python3 tools/release_tool.py metadata --cohort <id> --target debian-13-amd64
python3 tools/release_tool.py selftest
```

Manifests record cachetag's version, which is cross-checked against `AC_INIT` in the cachetag checkout's `configure.ac`. That checkout defaults to the sibling `../libvmod-cachetag`; point the tooling elsewhere with `--cachetag-src PATH` or the `CACHETAG_SRC` environment variable.

Every package build, by contrast, happens in a container. See [AGENTS.md](AGENTS.md).

## Tracks

Since 2026-07-26 the packaging maintains two Vinyl pin tracks, selected by the `VINYL_TRACK` environment variable and defined in each lane's pin file (`recipes/debian-13/pins.env`, `recipes/el9/cohort.env`):

- **release** — built from the upstream release tarball (currently 9.0.1). This is what published packages are built from and what most people should install.
- **trunk** — built from a pinned trunk snapshot, plus a scheduled unpinned harness run against trunk HEAD. This is the early-warning machinery for Vinyl core changes: it finds VMOD breakage and `$ABI strict` churn before a release forces the issue.

Both tracks build in the same CI lanes. See [`docs/20260726_1235_note_two-track-release-and-trunk.md`](docs/20260726_1235_note_two-track-release-and-trunk.md) for the policy, the release-track pins and their verification, and the cutover checklist.

## Status

Early, but for the first time the registry describes a releasable cohort.

The release cohort `vinyl-9.0.1-ac4f719c16f4` — cachetag 1.0.1 against upstream Vinyl Cache 9.0.1 — was minted on 2026-07-28 with all six evidence classes recorded per target: clean-room builds, lint under hard gates, installed-package smoke, the full 52-VTC behaviour suite driven by the packaged `vinyltest` against the installed VMOD, upgrade-transaction matrices against the 9.0.1 baseline, and hardening inspection. `validate --require-releasable` passes, and the first fully green release-draft has been assembled from it. The default `VINYL_TRACK` is now `release`: what a plain build produces is what users install. The trunk cohort `vinyl-9.0.0-4b7e68292979` carries the same evidence on the pinned snapshot and remains the early-warning track, selected explicitly by the CI matrix and the scheduled nightly. `vinyl-9.0.0-000000000000` and the distro-native manifest remain as schema exemplars. The vendored `upstream/pkg-vinyl-cache` recipes are audit input, not a release-ready base.

What this is still not: signed, repository-published, or supported. There is no security SLA, no advisory feed, and no repository metadata — the packages are direct downloads from a GitHub Release, and publication beyond that is a maintainer decision the draft deliberately gates. See [`docs/20260726_0827_note_step-10-cohort-mint-and-pre-release.md`](docs/20260726_0827_note_step-10-cohort-mint-and-pre-release.md) for the original gate decision.

## Support statement

The durable statement this repository exists to make true is:

> A VMOD package is claimed compatible only with the exact Vinyl Cache or Varnish Cache package revision and target against which it was built, installed, and tested.
