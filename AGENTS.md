# Agent Runbook

This repository owns the Vinyl Cache **cohort registry**, **Vinyl packaging**, and package-level compatibility/release coordination for the selected package set. It does not contain the cachetag VMOD sources; source-level cachetag changes and authoritative VMOD verification belong in the sibling `../libvmod-cachetag` repository under its own `AGENTS.md`.

## Scope

Read [SCOPE.md](SCOPE.md) before proposing architecture or expanding release machinery. It is normative over older plans and design notes.

This is a packaging and compatibility-testing project with a narrow publication surface, not a distribution platform. Managed APT and RPM repository integration for this project’s selected packages is in scope. Do not design or implement custom repository servers, signing services, mirrors, archival source storage, provider-independent promotion systems, or generalized packaging platforms unless the maintainer explicitly changes `SCOPE.md`.

## Layout

- `registry/` — compatibility manifests (`cohorts/`, `targets/`, `distro-native/`, `vmods/`) and their normative schema description in `registry/README.md`.
- `recipes/vmods/` — the reviewed inputs that third-party VMOD packaging recipes are *generated* from: `templates/` (the generic Debian and RPM recipes), `adapters/` (what is true of every VMOD built the same way), `overlays/<id>/` (what is true of one VMOD), `licenses/` (reviewed `debian/copyright` stanzas). Nothing in there is a recipe, and no generated recipe is committed. Its own `README.md` is normative for the layout. Cachetag is not generated: it keeps its audited recipe in its own repository, and that recipe is the policy reference for these templates.
- `tools/` — Python 3 standard-library tooling that validates the manifests and generates native package version metadata. Entry point `tools/release_tool.py`; `tools/vmod_recipe.py` renders the generated VMOD recipes, `tools/ci_matrix.py` owns the VMOD catalog and the CI ledger, and `tools/upstream_watch.py` answers whether any selected upstream has moved.
- `upstream/` — legacy audited packaging-recipe input with a `PROVENANCE.md` recording source, commit, and audit verdict. It is not a general store for upstream release archives. Vendored content is not modified in place without recording why.
- `docs/` — design notes and session records.
- `../libvmod-cachetag` is the expected sibling cachetag checkout. The manifests cross-check `cachetag.version` against its `configure.ac`.
- `../vinyl-cache` is the expected sibling Vinyl Cache source checkout. It belongs to the wider workspace; do not edit it from here.

## Required Rules

- **Verification happens in containers, never on the host.** Package builds, installs, package-manager transactions, and VMOD load tests run in Docker/OrbStack containers or native buildroots (`sbuild`/`pbuilder`, Mock, `pkgctl`, Poudriere, `abuild rootbld`). A host-local build is never evidence that a package works.
- **Do not install host tools** with Homebrew, MacPorts, pip, cargo, or similar package managers unless the maintainer explicitly asks for that specific install. If something appears to need a missing host dependency, stop and read this runbook and the plan before installing anything.
- **The registry tooling is the exception, and only because it builds nothing.** `tools/*.py` is Python 3 standard library only — no PyYAML, no third-party dependency — precisely so it can be run on the host and inside any buildroot without an install step. Keep it that way: if a change to the tooling would need a dependency, change the design instead.
- **Do not edit `../vinyl-cache`, `../slash`, or any other workspace checkout** from this repository.
- **Generated and vendored content is marked as such.** Do not hand-edit a version string, package revision, or ABI hash into a packaging recipe; generate it from the manifests with `tools/release_tool.py metadata`. A recipe that disagrees with the registry is a bug in the recipe.
- **Generated VMOD recipes fall under that same rule, in whole.** Third-party VMODs are packaged from Debian and RPM recipes generated in this repository out of the VMOD's manifest and its reviewed adapter data. The generated recipe is an output, not a source file: if it disagrees with the manifest or the adapter data, that is a defect in the generator or the adapter, and the fix goes there. Never hand-patch a generated recipe, not even to unblock a build — a hand patch is silently lost on the next generation and hides the real defect while it survives.
- **Absence of upstream packaging is expected, not a problem to work around.** Most third-party VMODs ship no `debian/` or `rpm/` directory, and that is not a reason to skip a VMOD, to vendor somebody's packaging, or to fork an upstream to add some. Absorbing packaging is the downstream provider's job. Upstream packaging is used only where it exists, is tied to the exact release source we selected, and meets this project's dependency, provenance, hardening, payload, and testing requirements.
- **Keep a diagnostic log.** This is research-grade work: record what was tried, what failed, and what the measurements were, in `docs/`, not only in commit messages. Commit messages record what changed; notes record what was learned, including dead ends.
- Backwards compatibility is not required; there are no users of this project yet.
- Do not hard-wrap Markdown. Rely on the editor's soft wrapping.

## Documentation/note file naming

Use the structure `YYYYMMDD_HHMM_[type]_[description].md`, where `[type]` is `note`, `plan`, `report`, or another descriptive term, and `[description]` is a short hyphen-separated description. If it relates to a planned step, put that first, for example `step-7a` or `phase-2`.

## Tracks

The packaging maintains two Vinyl pin tracks, selected with the `VINYL_TRACK` environment variable (`release` | `trunk`) and defined inside each lane's pin file (`recipes/debian-13/pins.env`, `recipes/el9/cohort.env`). `release` builds from the upstream release tarball and is what users install; `trunk` builds a **pinned trunk snapshot**. Since the 2026-07-28 cutover the default is `release`, and the trunk legs of `ci.yml`'s matrix select `trunk` explicitly. Policy: `docs/20260726_1235_note_two-track-release-and-trunk.md`.

Do not confuse that pinned snapshot with **Vinyl trunk HEAD**, which is a different thing with a different workflow. The pinned-snapshot package rows are event-driven and live in `ci.yml`: they move only when someone re-pins, so a schedule would re-prove what the last pull request already proved. Trunk HEAD is unpinned and is the actual early warning, run by `.github/workflows/trunk-early-warning.yml` — change-gated, Monday and Thursday, and it does nothing at all when neither Vinyl trunk nor a watched VMOD branch has moved. At tier `trunk` it builds Vinyl trunk HEAD once into a shared prefix, then builds each selected VMOD from its own source against that prefix and runs the VTCs its manifest declares. It builds no package and publishes nothing. The harness is generic — the only per-VMOD input is `lanes[].harness.tests` — and it invokes nothing from any VMOD's own repository: source-level verification of cachetag belongs to `../libvmod-cachetag` under its own runbook, and this lane is the packaging project's own early-warning measurement.

Two workflows retired with it in Step 8 Wave 3c: `nightly-transactions.yml`, because **no transaction matrix runs on a schedule** any more — the `transactions` tier is a deliberate dispatch against one release cohort's published packages — and `trunk-vmod-ci.yml`, whose job became the harness job inside `vmod-package.yml`. Policy: [the 2026-07-30 maintainer decisions](docs/20260730_0826_note_step-8-maintainer-decisions.md).

## Common commands

Registry validation and metadata generation (host-safe, stdlib only):

```sh
python3 tools/release_tool.py validate
python3 tools/release_tool.py validate --require-releasable
python3 tools/release_tool.py selftest
python3 tools/release_tool.py metadata --cohort <cohort-id> --target debian-13-amd64
```

The cachetag checkout used for the `configure.ac` version cross-check defaults to `../libvmod-cachetag`. Override it with `--cachetag-src PATH` or `CACHETAG_SRC=PATH` when the checkout is elsewhere, such as inside a container. `--no-cachetag-cross-check` runs the same validation without any VMOD source checkout; it exists for the global CI gate, and the cross-check itself moved into the per-VMOD CI invocation.

VMOD catalog, CI matrix expansion and result reconciliation (host-safe, stdlib only):

```sh
python3 tools/ci_matrix.py check-catalog
python3 tools/ci_matrix.py validate-vmod --manifest registry/vmods/cachetag.yml --id cachetag
python3 tools/ci_matrix.py expand --manifest registry/vmods/cachetag.yml --tier ci
python3 tools/ci_matrix.py engine-matrix --tier ci
python3 tools/ci_matrix.py ledger --tier ci
python3 tools/ci_matrix.py selftest
```

`ci_matrix.py selftest` also runs the recipe generator's and the upstream watcher's tests, so the CI structural-validation gate covers all three.

Live upstream freshness (host-safe: `git ls-remote` only, no HTTP, no install, builds nothing):

```sh
python3 tools/upstream_watch.py check --format text
python3 tools/upstream_watch.py check --state ci-state.json --format github
python3 tools/upstream_watch.py selftest
```

It answers three questions per VMOD — does the pinned tag still peel to the recorded commit (a moved tag is a loud failure, never a re-pin candidate), are there tags sorting above the pin (surfaced to the maintainer, never acted on), and has a watched trunk branch moved — plus Vinyl trunk HEAD. It also watches the Vinyl release tag itself under the same moved-tag rule, and the watched trunk branches cover cachetag, dict, and redis. It replaces the survey JSON as the freshness signal per the [2026-07-30 maintainer decisions](docs/20260730_0826_note_step-8-maintainer-decisions.md). Generated VMOD recipes (host-safe, stdlib only, builds nothing):

```sh
python3 tools/vmod_recipe.py generate --manifest registry/vmods/<id>.yml \
    --overlay recipes/vmods/overlays/<id>/overlay.yml \
    --cohort <cohort-id> --target debian-13-amd64 \
    --maintainer "$MAINTAINER_NAME <$MAINTAINER_EMAIL>" \
    --debian-distribution "$DEBIAN_DISTRIBUTION" --out <workdir>
python3 tools/vmod_recipe.py names --manifest ... --target el9-x86_64 ...
python3 tools/vmod_recipe.py selftest
```

The Vinyl engine packages are built once per engine input and target, published as `engine-<engine-id>-<target-id>`, and consumed by every VMOD package row after verifying the resolved identity recorded inside the artifact. See the shared-engine section of `registry/README.md` for the schema and the verification command; `scripts/ci/engine-identity.sh <deb|rpm>` is the one reader of the lane pin files that both sides of that comparison use.

## If unsure

Read `SCOPE.md`, then the historical [binary packaging and distribution plan](../libvmod-cachetag/devdocs/docs/20260724_1526_plan_binary-packaging-and-distribution.md) and `registry/README.md` before running build or packaging commands. For anything touching a VMOD other than cachetag, also read the [vmod-packager patterns and recipe-generation plan](docs/20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md), which defines the adapter contract and the generated-recipe model those VMODs use. `SCOPE.md` controls what work belongs in the project; the historical plan provides technical context only. Where the runbook conflicts with a tempting shortcut, the documented container workflow wins.
