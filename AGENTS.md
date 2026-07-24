# Agent Runbook

This repository owns the Vinyl Cache **cohort registry** and **Vinyl packaging**. It does not contain the cachetag VMOD sources, and it must not be used to build or test that VMOD; see the sibling `../libvmod-cachetag` repository and its own `AGENTS.md` for that.

## Layout

- `registry/` — compatibility manifests (`cohorts/`, `targets/`, `distro-native/`) and their normative schema description in `registry/README.md`.
- `tools/` — Python 3 standard-library tooling that validates the manifests and generates native package version metadata. Entry point `tools/release_tool.py`.
- `upstream/` — vendored third-party material with a `PROVENANCE.md` recording source, commit, and audit verdict. Vendored content is not modified in place without recording why.
- `docs/` — design notes and session records.
- `../libvmod-cachetag` is the expected sibling cachetag checkout. The manifests cross-check `cachetag.version` against its `configure.ac`.
- `../vinyl-cache` is the expected sibling Vinyl Cache source checkout. It belongs to the wider workspace; do not edit it from here.

## Required Rules

- **Verification happens in containers, never on the host.** Package builds, installs, package-manager transactions, and VMOD load tests run in Docker/OrbStack containers or native buildroots (`sbuild`/`pbuilder`, Mock, `pkgctl`, Poudriere, `abuild rootbld`). A host-local build is never evidence that a package works.
- **Do not install host tools** with Homebrew, MacPorts, pip, cargo, or similar package managers unless the maintainer explicitly asks for that specific install. If something appears to need a missing host dependency, stop and read this runbook and the plan before installing anything.
- **The registry tooling is the exception, and only because it builds nothing.** `tools/*.py` is Python 3 standard library only — no PyYAML, no third-party dependency — precisely so it can be run on the host and inside any buildroot without an install step. Keep it that way: if a change to the tooling would need a dependency, change the design instead.
- **Do not edit `../vinyl-cache`, `../slash`, or any other workspace checkout** from this repository.
- **Generated and vendored content is marked as such.** Do not hand-edit a version string, package revision, or ABI hash into a packaging recipe; generate it from the manifests with `tools/release_tool.py metadata`. A recipe that disagrees with the registry is a bug in the recipe.
- **Keep a diagnostic log.** This is research-grade work: record what was tried, what failed, and what the measurements were, in `docs/`, not only in commit messages. Commit messages record what changed; notes record what was learned, including dead ends.
- Backwards compatibility is not required; there are no users of this project yet.
- Do not hard-wrap Markdown. Rely on the editor's soft wrapping.

## Documentation/note file naming

Use the structure `YYYYMMDD_HHMM_[type]_[description].md`, where `[type]` is `note`, `plan`, `report`, or another descriptive term, and `[description]` is a short hyphen-separated description. If it relates to a planned step, put that first, for example `step-7a` or `phase-2`.

## Common commands

Registry validation and metadata generation (host-safe, stdlib only):

```sh
python3 tools/release_tool.py validate
python3 tools/release_tool.py validate --require-releasable
python3 tools/release_tool.py selftest
python3 tools/release_tool.py metadata --cohort <cohort-id> --target debian-13-amd64
```

The cachetag checkout used for the `configure.ac` version cross-check defaults to `../libvmod-cachetag`. Override it with `--cachetag-src PATH` or `CACHETAG_SRC=PATH` when the checkout is elsewhere, such as inside a container.

## If unsure

Read the [binary packaging and distribution plan](../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md) and `registry/README.md` before running build or packaging commands. Where those conflict with a tempting shortcut, the documented container workflow wins.
