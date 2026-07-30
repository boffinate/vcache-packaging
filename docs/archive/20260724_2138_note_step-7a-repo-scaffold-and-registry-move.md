# Step 7a: repository scaffold, cohort registry move, and upstream vendoring

Date: 2026-07-24

Related plan: [Vinyl Cache and VMOD binary packaging and distribution plan](../../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md) (implementation-order step 7, "Update the audited `pkg-vinyl-cache` material into minimal Vinyl 9 Debian and RPM packages with strict ABI virtual provides")

Prior decision: the repository split was approved by the maintainer on 2026-07-24 and recorded in the cachetag repository's [packaging plan implementation session report](../../libvmod-cachetag/docs/20260724_2125_report_packaging-plan-implementation-session.md). This note records step **7a**, the preparation: the new repository exists, the registry has moved into it, and the upstream material is vendored and audited-in-place. Step 7 proper — modernising the recipes for Vinyl 9 — has deliberately not started.

## What exists now

```text
vcache-packaging/
  README.md              purpose, layout, the plan's support statement
  AGENTS.md              runbook; CLAUDE.md is a symlink to it, matching the sibling repos
  .gitignore
  registry/              the cohort registry, moved from libvmod-cachetag/release/
    README.md            normative schema description
    cohorts/vinyl-9.0.0-000000000000.yml
    targets/vinyl-9.0.0-000000000000/{debian-13-amd64,el9-x86_64}.yml
    distro-native/debian-13-amd64.yml
  tools/                 moved from libvmod-cachetag/scripts/release/
    yaml_subset.py manifest.py metadata.py release_tool.py selftest.py .gitignore
  upstream/
    PROVENANCE.md        source, commit, tree hash, audit verdict
    pkg-vinyl-cache/     vendored verbatim at 27c9130
  docs/
    this note
```

## Layout decision: `registry/` + `tools/`, not `release/` + `scripts/release/`

Keeping the original names would have been the smaller diff, and it was tempting for exactly that reason. Two things decided against it.

**`release/` still exists in libvmod-cachetag, meaning something else.** The registry moved out but `release/dist/` stayed: it is the gitignored output directory of `scripts/release-source-archive.sh`, and it belongs to the archive script, not to the manifests. Two sibling repositories each with a top-level `release/` — one meaning "compatibility manifests", the other "source tarball output" — is a trap that costs someone an hour eventually, and it costs nothing to avoid now while there are no users and no CI referencing either path.

**`scripts/release/` described a subdirectory of a project that releases something.** That was accurate in cachetag, where the tooling sat alongside the harness and archive scripts. Here the tooling is not a script directory attached to a larger build; it is one of the two things the repository contains. `tools/` at the top level says that, and leaves `packaging/` free for the Vinyl recipes that arrive in step 7 proper.

`registry/` also matches the vocabulary the plan already uses ("Maintain the cohort registry as a checked release checklist", "Add a generic VMOD registry ... when a second independently packaged VMOD joins"), so the directory name and the plan agree.

The Python module filenames were deliberately **not** renamed. `manifest.py`, `metadata.py`, `release_tool.py`, `selftest.py`, and `yaml_subset.py` keep their names so that the step-3 design note in the cachetag repository — which documents each module's role in detail and which Git history no longer connects to these files — still reads as a description of this code.

## History did not transfer

The registry was copied in and committed fresh; there is no shared history with `libvmod-cachetag`. A filter-branch or subtree split was considered and rejected: the registry is four template manifests and five Python modules, all created the same day in a single session, and its design rationale lives in prose notes rather than in commit messages. Rewriting history to preserve two days of it would have bought nothing.

What replaces it, deliberately, is a set of pointers: this note, the `registry/README.md` preamble, a location note at the top of the cachetag step-3 note, and the [cachetag-side record](../../libvmod-cachetag/docs/20260724_2138_note_step-7a-registry-moved.md) of what left that repository.

## Tooling adaptation: the configure.ac cross-check now spans two repositories

The one genuinely load-bearing change. The registry validates that each manifest's `cachetag.version` equals `AC_INIT` in cachetag's `configure.ac`. That check previously worked by accident of location:

```python
REPO_ROOT = Path(__file__).resolve().parents[2]   # scripts/release/x.py -> repo root
text = (root / "configure.ac").read_text(...)
```

which silently meant "the repository I live in". After the move that assumption is false, and the failure mode would have been a `FileNotFoundError` with no explanation.

The cachetag checkout is now an explicit input, resolved in this order:

1. `--cachetag-src PATH` on any subcommand;
2. the `CACHETAG_SRC` environment variable;
3. the sibling `../libvmod-cachetag`, which is how this workspace is laid out.

`REPO_ROOT` became `parents[1]` for the shallower `tools/` layout, `release_dir()` became `registry_dir()`, and `validate_release_tree()` became `validate_registry_tree()` with a `cachetag_src` parameter threaded through.

Two decisions inside that change are worth recording:

- **A missing or foreign checkout is a hard error, not a skipped check.** It would have been easy to let validation pass when no cachetag checkout is present — convenient in a container that only has this repository. That would be wrong: the cross-check is the only thing tying a manifest to a real cachetag release, and a version lint that silently stops linting is worse than no lint. The error names both escape hatches. A `configure.ac` from some other project is rejected too, rather than yielding whatever version it happens to declare.
- **`validate` prints which checkout it used.** `OK: 4 manifest(s) valid (schema mode), cachetag version 1.0.0 from /path/to/libvmod-cachetag`. With three resolution paths, "which configure.ac did it actually read" is the first question anyone will ask of a surprising result.

## Verification

Pure-Python, standard library, host-run — the one thing in this workspace that is legitimately verified on the host, because it builds and tests nothing. No autotools, no Docker, no package build. System `python3` 3.14.6, no installs.

Self-tests grew from 79 to 86:

```text
$ python3 tools/release_tool.py selftest
...
PASS  cachetag-src: an explicit checkout path supplies the version
PASS  cachetag-src: defaults to the sibling libvmod-cachetag checkout
PASS  cachetag-src: a registry validates against its sibling checkout with no explicit path
PASS  cachetag-src: CACHETAG_SRC overrides the sibling default
PASS  cachetag-src: a missing checkout is an actionable error
PASS  cachetag-src: a foreign configure.ac is rejected
PASS  repo: checked-in registry/ manifests are schema-valid
PASS  repo: the cachetag version comes from the separate cachetag checkout
...
# TOTAL: 86
# PASS:  86
# FAIL:  0
```

The synthetic fixtures were restructured rather than merely repathed: `_write_workspace()` now builds a temporary directory containing a registry checkout *beside* a cachetag checkout, which is the shape the tooling really runs in. A pleasant consequence is that the sibling-default resolution is exercised by a real two-repository layout instead of being mocked.

Both validate modes behave exactly as they did before the move:

```text
$ python3 tools/release_tool.py validate
checked  registry/cohorts/vinyl-9.0.0-000000000000.yml
checked  registry/targets/vinyl-9.0.0-000000000000/debian-13-amd64.yml
checked  registry/targets/vinyl-9.0.0-000000000000/el9-x86_64.yml
checked  registry/distro-native/debian-13-amd64.yml

OK: 4 manifest(s) valid (schema mode), cachetag version 1.0.0 from /Users/peter/projects/open-source/vinyl-cache/libvmod-cachetag

$ python3 tools/release_tool.py validate --require-releasable
...
ERROR    registry/cohorts/vinyl-9.0.0-000000000000.yml: status is 'template'; a template manifest is never releasable
ERROR    registry/targets/vinyl-9.0.0-000000000000/debian-13-amd64.yml: status is 'template'; a template manifest is never releasable
ERROR    registry/targets/vinyl-9.0.0-000000000000/el9-x86_64.yml: status is 'template'; a template manifest is never releasable
ERROR    registry/distro-native/debian-13-amd64.yml: status is 'template'; a template manifest is never releasable

4 error(s) in 4 manifest(s)      (exit 1, as intended)
```

**Cohort identity is unchanged by the move**, which is the property that actually mattered. `cohort-id` still derives `018f1ab810ef` for the template's placeholder inputs — the same value recorded in the step-3 note — and both hand-computed SHA-256 vectors still pass. Nothing about the digest depends on where the files live, and now that is a tested statement rather than an expectation.

`metadata` was spot-checked in both lanes after the move: `libvmod-cachetag-1.0.0-1.el9.x86_64.rpm` with its `vinyld-abi-…`/`vinyld-vrt = 23.0` requires for the cohort lane, and eval-safe shell output for the distro-native lane.

## Vendoring `pkg-vinyl-cache`

Cloned from `https://code.vinyl-cache.org/vinyl-cache/pkg-vinyl-cache`, checked out at `27c91305023b4c4dae09f903644774fb9dbd8fcb` — the commit the plan inspected — and vendored into `upstream/pkg-vinyl-cache/` without its `.git`. That commit is also the current tip of the default branch, so nothing has moved upstream since the plan's audit.

Vendored rather than submoduled so the audited bytes are in our history and the modernisation shows up as a reviewable diff. The verdict from the plan is repeated verbatim in `upstream/PROVENANCE.md`: **source material to audit and update for Vinyl 9, not a release-ready base** — debhelper 9-era Debian recipes, an Arch recipe declaring only x86_64, and an Alpine recipe still carrying version and checksum placeholders. Not one line of it has been modernised; that is step 7 proper.

Provenance is verified rather than asserted. The Git tree object for `upstream/pkg-vinyl-cache/` hashes to `aa31e4100541165c777c6bf5234648f9a8b3fb6e`, which is exactly upstream's tree at that commit, so content, file modes, and symlinks are provably identical:

```sh
git write-tree --prefix=upstream/pkg-vinyl-cache   # aa31e410...
```

## Traps hit, recorded so the next agent does not re-find them

1. **`find -type f` undercounts a vendored tree.** It reported 42 files where Git staged 56. The difference is 14 symlinks — upstream shares systemd and logrotate units across its `arch/`, `debian/`, `redhat/`, and `systemd/` directories. Nothing was wrong, but "the file counts disagree" is alarming enough to waste time on. Comparing tree hashes is both faster and stronger than counting anything, and is now what `PROVENANCE.md` records.
2. **A vendored `.gitignore` silently truncates the vendored tree.** `pkg-vinyl-cache/.gitignore` ignores `/sources`, its download directory, which upstream nonetheless tracks a `sources/.placeholder` in. Copying the tree in and running `git add` would have quietly dropped that file and produced a vendored copy that does not match upstream — with a `PROVENANCE.md` next to it claiming it does. Caught by `git status --porcelain --ignored`, fixed with `git add -f` on that one file, leaving upstream's `.gitignore` unmodified. **Check `--ignored` after vendoring anything**; a nested `.gitignore` outranks the repository root's patterns for its own subtree.
3. **The repository-root `.gitignore` nearly did the same thing.** A first draft ignored `sources/` and other build-output names unanchored, which would have hidden vendored content. The committed version anchors build directories to the root (`/build/`, `/dist/`) and carries an explicit `!upstream/**` with a comment saying why.

## What this unblocks, and what it does not

Unblocked: step 7 proper. The recipes to modernise, the registry that will describe their output, and the tooling that generates their version metadata are now in one repository, and cachetag no longer has to be checked out for a Vinyl package build to know which cohort it belongs to — beyond the one `configure.ac` version cross-check, which is explicit and overridable.

Not unblocked, and not attempted: any actual Vinyl 9 packaging. No recipe has been audited line by line, no ABI-provider generation has been reviewed against Vinyl 9's `VMOD_ABI_Version`, no package has been built, and no container has been run in this repository yet. The first real cohort identifier remains underivable until the Vinyl source archive, patch series, and production build-profile revision are pinned, so the checked-in cohort is still the reserved `vinyl-9.0.0-000000000000` template.

## Follow-up left for someone else

`libvmod-cachetag/acinclude.m4` documents its `--with-release-manifest` option with the now-stale path `release/cohorts/<cohort-id>.yml` and cites `release/README.md` for the restricted-YAML subset, in four header comments and one error message. Those are comments and a hint string, not behaviour — the option takes any path — but they point at a directory that no longer exists in that repository. Left untouched deliberately, as `acinclude.m4` was outside this task's blast radius; recorded in the cachetag-side note as a one-line fix for whoever next touches that file.
