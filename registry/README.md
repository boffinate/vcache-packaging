# The Vinyl cohort registry

This directory holds the compatibility manifests defined by [Phase 0 of the binary packaging and distribution plan](../../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md). A manifest records the exact inputs that a cachetag package was, or will be, built from. Every native package version string, artifact filename, and ABI dependency expression is generated from these files rather than hand-edited into packaging recipes.

Nothing here builds or tests anything. The tooling is pure Python 3 standard library and is safe to run on the host; packages are built and tested only in containers and native buildroots, as [AGENTS.md](../AGENTS.md) requires, and the cachetag VMOD itself only through the Docker harness in its own repository.

The registry lives in `vcache-packaging` rather than in any VMOD repository because a cohort is a *set* of packages — one Vinyl runtime plus every strict-ABI VMOD built against it — and no single member of that set can own its identity. It moved here from `libvmod-cachetag` on 2026-07-24; Git history did not transfer.

## Layout

```text
registry/cohorts/<cohort-id>.yml              one coordinated project cohort
registry/targets/<cohort-id>/<target-id>.yml  one distro/arch build within that cohort
registry/distro-native/<target-id>.yml        a build against a distribution's own Vinyl packages
```

`<target-id>` is always `<distro-id>-<arch>`, for example `debian-13-amd64` or `el9-x86_64`.

Two deliberate deviations from the illustrative paths in the plan:

- Target files are named `debian-13-amd64.yml`, not `debian-13.yml`. The plan's Tier 1 matrix has two architectures per distro release and a single file cannot record two sets of resolved build dependencies, flags, and artifact digests.
- Distro-native files use the same `<distro-id>-<arch>.yml` convention, for the same reason.

## Status lifecycle

Every manifest carries a `status`:

| status | meaning |
| --- | --- |
| `template` | a schema exemplar with placeholder identity values. Never releasable. |
| `candidate` | real pinned inputs, build and test evidence still being collected. |
| `released` | real pinned inputs, complete evidence, published. |

A target manifest's status must equal its cohort's status.

### The template convention

The first concrete cohort identifier must not be derived yet: the plan forbids deriving it from the mutable sibling `../vinyl-cache` checkout, and it can only be assigned once the Vinyl source archive, the ordered downstream patch set, and the production build-profile revision are pinned. The checked-in manifests are therefore templates.

A placeholder is any of:

- `0` repeated 64 times, where a SHA-256 digest belongs;
- `0` repeated 40 times, where a Git commit or a Vinyl strict ABI string belongs;
- `sha256:` followed by 64 zeros, where a container image digest belongs;
- the literal token `PLACEHOLDER`, where free text belongs;
- any value containing `example.invalid`;
- the reserved cohort input-id `000000000000`.

The validator enforces both directions:

- a `template` manifest must use the reserved input-id `000000000000` and the placeholder `vinyl.source_sha256`, so a template can never masquerade as a real cohort, and it is rejected outright in `--require-releasable` mode;
- a `candidate` or `released` manifest must contain no placeholder anywhere, and its cohort input-id must equal the digest of its recorded inputs.

Placeholder values are exempt from the ordinary pattern checks so that a template can still be fully schema-checked.

## File format

The manifests are YAML-shaped, but they are read by a deliberately small strict parser ([`tools/yaml_subset.py`](../tools/yaml_subset.py)) rather than a full YAML implementation, because the release tooling must run with the Python standard library only and no `pip install` is permitted in this workspace.

Accepted:

- block mappings, `key: value` or `key:` followed by a block indented exactly two spaces;
- block sequences, `- scalar` or `- key: value` with continuation lines aligned under the first key;
- the empty flow sequence `[]`;
- plain scalars, and `"` or `'` quoted scalars containing neither their quote character nor a backslash;
- full-line `#` comments and completely empty lines.

Rejected, loudly, with file and line number: tabs, CRLF, anchors, aliases, tags, flow mappings, non-empty flow sequences, block scalars (`|`, `>`), document markers (`---`), duplicate keys, odd indentation, indentation jumps other than two spaces, trailing whitespace, comments that trail a value, and keys outside `lower_snake_case`.

**Every scalar is parsed as a string.** There is no implicit typing: `23.0` stays the string `"23.0"` and `no` stays the string `"no"`. Fields that must be integers are validated and converted by the schema layer. This is the point of the subset — these files exist to pin exact identities, and YAML's implicit typing is a poor fit for that.

Values containing `#`, `{`, `}`, `[`, `]`, `&`, `*`, `!`, `|`, `>`, `%`, `@`, a backtick, a quote character, or the sequence `": "` must be quoted, and quoting anything ambiguous is always safe.

## Cohort manifest

`registry/cohorts/<cohort-id>.yml`. The file name stem must equal the `cohort` field.

| Field | Kind | Notes |
| --- | --- | --- |
| `schema` | fixed | `cachetag-cohort/v1` |
| `status` | policy | `template`, `candidate`, or `released` |
| `cohort` | derived identity | `vinyl-<upstream-version>-<input-id>`; validated against the digest below |
| `cachetag.version` | immutable input | must equal `AC_INIT` in the cachetag checkout's [`configure.ac`](../../libvmod-cachetag/configure.ac) |
| `cachetag.source_sha256` | immutable input | digest of the released `libvmod-cachetag-X.Y.Z.tar.gz` |
| `cachetag.git_commit` | immutable input | 40 hex characters |
| `vinyl.version` | immutable input | upstream Vinyl version; also the middle segment of the cohort id |
| `vinyl.source_url` | immutable input | where the pinned archive was fetched from |
| `vinyl.source_sha256` | **digest input** | canonical digest of the pinned Vinyl source archive |
| `vinyl.git_commit` | immutable input | recorded for audit; not a digest input |
| `vinyl.vrt` | immutable input | public VRT ABI, for example `23.0` |
| `vinyl.strict_abi` | immutable input | 40 hex characters, baked into the Vinyl build |
| `vinyl.patches` | **digest input** | ordered list of `{name, sha256}`; `[]` when unpatched |
| `build_profile.name` | **digest input** | `production` for any releasable cohort |
| `build_profile.revision` | **digest input** | integer starting at 1 |
| `required_vmods` | policy | every VMOD the cohort must contain; currently `cachetag` alone |
| `storage_support` | policy | `default`, and `buddy` only once unpatched Slash is packaged |
| `targets` | wiring | target ids; must match the files in `registry/targets/<cohort-id>/` exactly |
| `support.channel` | policy | `pre-release` or `stable` |
| `support.release_owner` | policy | named owner; filled in by the security-ownership step |
| `support.fellow` | policy | `excluded` for the first milestone |
| `support.buddy` | policy | `source-harness-only` until an unpatched Slash package joins a cohort |

`vinyl.git_commit` is recorded but is deliberately **not** a digest input: the source archive digest is the authoritative statement of what was compiled, and a commit id would make the identity depend on which VCS mirror produced the archive.

## Cohort identity

The cohort identifier is:

```text
vinyl-<upstream-version>-<input-id>
```

`<input-id>` is the **first 12 lowercase hexadecimal characters of the SHA-256 digest** of the canonical encoding of the compatibility inputs. The cohort identifier itself and every generated output field are excluded from the digest input.

### Canonical input encoding

The digest is taken over UTF-8 bytes consisting of these lines, in this order, each terminated by a single `\n` (including the last), with no other whitespace anywhere:

```text
cachetag-cohort-input/v1
vinyl-source-sha256=<64 lowercase hex characters>
patch-count=<decimal count, no leading zeros>
patch[<i>]-sha256=<64 lowercase hex characters>
build-profile=<build_profile.name>
build-profile-revision=<decimal revision, no leading zeros>
```

One `patch[<i>]-sha256=` line is emitted per patch, `<i>` counting from `0` in manifest order, immediately after the `patch-count` line. When `patches` is `[]`, no patch line is emitted and `patch-count=0`.

Notes on the encoding:

- The `cachetag-cohort-input/v1` magic line versions the encoding. Any future change to what feeds cohort identity must bump it, because the same inputs must never hash differently under one version string.
- `patch-count` is present so that a patch list cannot be confused with a differently split one by concatenation alone.
- Patch **order** is significant and patch **names** are not. Renaming a patch file does not change cohort identity; reordering the patch series does, because the applied result differs.
- The digest is over the pinned source archive digest, not the Git commit, so identity is a statement about bytes that were compiled.
- Only the first 12 hex characters are used, matching the plan. This is an identifier, not a security boundary; the full digests of every input remain in the manifest.

### Worked vector

This vector was computed with `shasum -a 256`, independently of the Python implementation, and is asserted by the self-tests:

```text
cachetag-cohort-input/v1
vinyl-source-sha256=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
patch-count=2
patch[0]-sha256=fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210
patch[1]-sha256=00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
build-profile=production
build-profile-revision=3
```

SHA-256 = `546d7171ef8e64724e444e69c06fd8e5cc319050324f9ad670fcbd45831ae50e`, so `input-id` = `546d7171ef8e` and the cohort identifier for Vinyl 9.0.0 is `vinyl-9.0.0-546d7171ef8e`.

Any change to the Vinyl source archive, the patch series, or the production build-profile revision produces a different cohort identity, even when Vinyl advertises an unchanged `strict_abi` string. That is the property the plan requires: the baked-in ABI string is a dependency identifier, not a rebuild decision.

### The cohort identifier is also a package-name component

Since 2026-07-25 the cohort id is not only a manifest key. The Vinyl runtime package advertises a second, cohort-qualified virtual provide alongside the exact-ABI one — `vinyld-cohort-<cohort-id>` on Debian, the unversioned `vinyld(cohort-<cohort-id>)%{?_isa}` on RPM — and every cohort VMOD depends on it. The step-9 transaction matrices in [`docs/`](../docs/) measured why: on both apt and dnf, a *different* package advertising the baseline's `vinyld-abi-<hash>` upgraded cleanly, because that token is derived from the upstream source revision and says nothing about the patch series, build profile or respin that produced the binary.

Two consequences for this identifier:

- **it must be usable inside a package name**, `^[a-z0-9][a-z0-9+.-]+$`. The derived form `vinyl-<upstream-version>-<input-id>` satisfies this by construction, and both lanes assert it at build time rather than trusting that;
- **it goes in the provide *name*, never its version**, because a cohort id contains hyphens and RPM will not accept one in an EVR. The Debian side uses the same shape for symmetry rather than because it has to.

The distro-native lane has no cohort identity and therefore emits no cohort dependency at all; its equivalent guard is the exact binary package version dependency described below.

## Target manifest

`registry/targets/<cohort-id>/<target-id>.yml`. The file name stem must equal `target.id`.

| Field | Kind | Notes |
| --- | --- | --- |
| `schema` | fixed | `cachetag-target/v1` |
| `status` | policy | must equal the cohort status |
| `lane` | fixed | `cohort` |
| `cohort` | wiring | must equal the owning cohort id |
| `target.id` | identity | must equal `<distro_id>-<arch>` |
| `target.distro` | identity | `debian`, `el`, `arch`, `freebsd`, `alpine`, ... |
| `target.distro_release` | identity | `13`, `9`, ... |
| `target.distro_id` | identity | `<distro><release>` or `<distro>-<release>`; both `el9` and `debian-13` are valid |
| `target.arch` | identity | the native architecture name for the package format: `amd64` for deb, `x86_64` for rpm |
| `target.package_format` | identity | `deb`, `rpm`, `arch`, `freebsd`, `apk` |
| `target.dist_tag` | identity | RPM dist tag such as `el9`; must be `""` for every other format |
| `package.revision` | policy | canonical package revision, integer starting at 1 (see below) |
| `package.source_name` / `package.binary_name` | fixed | `libvmod-cachetag` |
| `vinyl_packages.*` | recorded input | the cohort Vinyl runtime/dev package names and versions this build consumed |
| `build.profile` | policy | `production` for any releasable target |
| `build.image_ref` / `build.image_digest` | recorded input | buildroot identity; the digest pins it |
| `build.compiler` | recorded output | resolved compiler and version |
| `build.configure_options` | recorded output | the effective configure command line |
| `build.cflags` / `build.ldflags` | recorded output | the effective flags, so hardening policy is auditable |
| `build.source_date_epoch` | recorded input | from the cachetag release commit |
| `build.hardening_check` | evidence | `pending`, `pass`, `fail`, `not-applicable` |
| `build.build_dependencies` | recorded output | exactly resolved buildroot packages; `[]` until recorded |
| `install.vmoddir` | recorded output | the installed VMOD directory, fully resolved for this distro and architecture (see below) |
| `install.vmoddir_source` | evidence | `pkg-config` when read from `vinylapi.pc`, `recorded` when written by hand |
| `artifacts` | recorded output | `{filename, sha256}` per produced artifact; a releasable target needs at least one |
| `tests.*` | evidence | `package_lint`, `installed_package_smoke`, `full_behavior_suite`, `upgrade_transactions` |

`--require-releasable` additionally demands `build.profile: production`, `build.hardening_check` of `pass` or `not-applicable`, every `tests.*` entry `pass` or `not-applicable`, a non-empty `artifacts` list, and `install.vmoddir_source: pkg-config`.

### The installed VMOD directory

`install.vmoddir` is the directory the packaged `libvmod_cachetag.so` is installed into. It is per target because it differs by distribution and architecture: `/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods` on Debian 13 amd64, `/usr/lib64/vinyl-cache/vmods` on EL9 x86_64.

It must be the value reported by the installed Vinyl development package:

```sh
pkg-config --variable=vmoddir vinylapi
```

fully expanded, with no `${libdir}`-style pkg-config variable left in it. The validator enforces that the value is an absolute path with no unexpanded variable, no empty segment, and no trailing slash, and it specifically rejects anything under `/tmp/vinyl-prefix` or `/vinyl-build` — those are the Docker test harness's prefixes, and a package built against them would have taken its VMOD directory from the harness rather than from the installed development package.

`install.vmoddir_source` records how the value was obtained. A releasable target requires `pkg-config`; `recorded` is for a manifest written before the package build has run, such as the checked-in templates.

Packaging recipes consume the directory as the `@VINYL_VMODDIR@` substitution token, which the metadata generator emits (see below). Recipes must not hardcode the path or re-derive it themselves.

## Distro-native manifest

`registry/distro-native/<target-id>.yml` uses `cachetag-distro-native/v1`. It has **no cohort identity**: its compatibility claim is bound to one exact distribution Vinyl binary package revision. It replaces the cohort's `vinyl_packages` block with `distro_vinyl`:

| Field | Notes |
| --- | --- |
| `distro_vinyl.repository_origin` | which distribution repository supplied the packages |
| `distro_vinyl.upstream_version` | the Vinyl upstream version the distro packaged |
| `distro_vinyl.source_package_version` / `binary_package_version` | the exact distro revisions the VMOD was built and tested against |
| `distro_vinyl.runtime_package` / `dev_package` | the distro package names |
| `distro_vinyl.vrt` / `strict_abi` | as advertised by the distro build |
| `distro_vinyl.exposes_abi_provide` | `yes`, `no`, or `unknown` |
| `distro_vinyl.patches` | downstream patches where the distribution publishes them |

When `exposes_abi_provide` is not `yes`, the generated dependency falls back to the exact binary package version, for example `vinyl-cache (= 9.0.0-3)`, as the plan requires. This deliberately blocks a Vinyl upgrade until the VMOD has been rebuilt.

It also carries its own `cachetag.version`, which is still checked against the cachetag checkout's `configure.ac`.

## Package revision rules

Manifests store one canonical `package.revision`, an integer starting at **1**. The tooling maps it onto each ecosystem's convention:

| Ecosystem | Fields | Revision 1 | Revision 3 |
| --- | --- | --- | --- |
| Debian | `Version` | `1.0.0-1` | `1.0.0-3` |
| RPM | `Version` / `Release` | `1.0.0` / `1.el9` | `1.0.0` / `3.el9` |
| Arch | `pkgver` / `pkgrel` | `1.0.0` / `1` | `1.0.0` / `3` |
| FreeBSD | `PORTVERSION` / `PORTREVISION` | `1.0.0` / `0` | `1.0.0` / `2` |
| Alpine | `pkgver` / `pkgrel` | `1.0.0` / `0` | `1.0.0` / `2` |

FreeBSD `PORTREVISION` and Alpine `pkgrel` are zero-based by convention, so they are `revision - 1`. The FreeBSD package version therefore omits the suffix entirely at revision 1 (`1.0.0`) and reads `1.0.0_2` at revision 3.

When to increment which number:

- **cachetag upstream version** (`configure.ac`, and therefore every manifest): only when cachetag's own source changes. Requires a new tag.
- **package revision**: when the same cachetag source is rebuilt against a different Vinyl source, patch set, production build profile, Vinyl package revision, or strict ABI, or for a packaging-only fix. The revision is per target, so one target can be rebuilt without disturbing another.
- **cohort input-id**: automatically, whenever a digest input changes. A new cohort id means a new cohort directory and a rebuild of every required VMOD; it does not by itself force the cachetag upstream version to move.

A cohort rebuild for an ABI change is therefore a routine package-revision bump inside a new cohort, with no upstream version churn.

## Generated outputs

These values are **never stored** in a manifest. They are computed by the tooling, so a packaging recipe cannot drift from the manifest:

- Debian `Version`; RPM `Version` and `Release`; Arch `pkgver`/`pkgrel`; FreeBSD `PORTVERSION`/`PORTREVISION` and package version; Alpine `pkgver`/`pkgrel`;
- the source archive name `libvmod-cachetag-X.Y.Z.tar.gz`;
- the native artifact filename, for example `libvmod-cachetag_1.0.0-1_amd64.deb` or `libvmod-cachetag-1.0.0-1.el9.x86_64.rpm`;
- the source package filenames (`.orig.tar.gz`, `.debian.tar.xz`, `.dsc`, `.src.rpm`);
- the release asset filename, which always carries distro and arch: `libvmod-cachetag-1.0.0-1-debian-13-amd64.deb`;
- the ABI dependency expressions: `vinyld-abi-<strict-abi>`, `vinyld-vrt = <vrt>`, the cohort-qualified pair `vinyld-cohort-<cohort-id>` (Debian) and `vinyld(cohort-<cohort-id>)` (RPM), the Debian `Depends` form `vinyld-abi-<hash>, vinyld-vrt (= 23.0), vinyld-cohort-<cohort-id>`, and the RPM `Requires` list;
- the recipe substitution tokens, currently `@VINYL_VMODDIR@`, in a `substitutions` block. Unlike the values above these are copied from the manifest rather than computed, but they are emitted here so that a recipe has exactly one place to read them from. The block is the extension point for further tokens.

Note on artifact naming: the native `.deb` filename is whatever `dpkg-buildpackage` produces from the package name, version, and architecture, so the distro release cannot appear in it. The distro-bearing name is the **release asset** name used for GitHub Release uploads. A distro suffix inside the Debian version itself (`1.0.0-1~deb13`) is deliberately **not** used yet; it becomes necessary only when one repository component serves more than one Debian or Ubuntu release, which the first milestone does not do.

## Tooling

[`tools/`](../tools/) — Python 3 standard library only.

```sh
# validate every manifest: schema, cachetag version against configure.ac,
# cohort id format and digest, target wiring, placeholder policy
python3 tools/release_tool.py validate

# additionally require release readiness (rejects templates, pending tests,
# missing artifact digests)
python3 tools/release_tool.py validate --require-releasable

# show the canonical input blob, its digest, and the derived cohort id
python3 tools/release_tool.py cohort-id --cohort vinyl-9.0.0-000000000000

# generated native package metadata for one target
python3 tools/release_tool.py metadata --cohort <id> --target debian-13-amd64
python3 tools/release_tool.py metadata --cohort <id> --target el9-x86_64 --format shell
python3 tools/release_tool.py metadata --distro-native debian-13-amd64

# the tooling's own tests
python3 tools/release_tool.py selftest
```

### The cachetag checkout

`cachetag.version` is validated against `AC_INIT` in a `libvmod-cachetag` checkout, which is a different repository. Every subcommand therefore accepts `--cachetag-src PATH`; without it the tooling uses `$CACHETAG_SRC`, and failing that the sibling `../libvmod-cachetag`. `validate` prints which checkout it used, and a missing or foreign checkout is a hard error rather than a skipped check — the cross-check is the only thing tying a manifest to a real cachetag release, so it must not degrade quietly:

```sh
python3 tools/release_tool.py --cachetag-src ../libvmod-cachetag validate
CACHETAG_SRC=/src/libvmod-cachetag python3 tools/release_tool.py validate
```

`--format shell` emits `CACHETAG_*` assignments that are safe to `.` from a POSIX shell. Lists become `<name>_COUNT` plus `<name>_0`, `<name>_1`, ... so values containing spaces, such as `vinyld-vrt = 23.0`, survive intact. Characters that cannot appear in a shell identifier are folded to underscores, so the `@VINYL_VMODDIR@` token is exported as `CACHETAG_SUBSTITUTIONS__VINYL_VMODDIR_`; the plain `CACHETAG_INSTALL_VMODDIR` carries the same value and is the more readable name to use.

Generating metadata from a `template` manifest requires `--allow-template` and is only useful for inspecting the shape of the output.

Module map:

| File | Role |
| --- | --- |
| `yaml_subset.py` | the strict restricted-YAML parser |
| `manifest.py` | schema, cohort-identity digest, validation; the executable copy of this document |
| `metadata.py` | native package version, filename, and ABI string generation |
| `release_tool.py` | command line entry point |
| `selftest.py` | tests, including the hand-computed digest vectors |

## Deliberately not here yet

- **The first real cohort identifier.** Assigned only when the Vinyl source archive, patch series, and production build-profile revision are pinned, per the plan.
- **`debian/changelog` and RPM `%changelog` generation.** The plan lists them under the same Phase 0 bullet; they belong with the packaging recipes — cachetag's in its own repository, Vinyl's in this one — and they need release-note text that no manifest field holds.
- **A VMOD registry.** `required_vmods` is a flat list because cachetag is the only independently packaged VMOD. Generic reverse-dependency scheduling arrives with the second one.
- **`release-manifest.json` emission.** The per-release artifact described in the plan's release artifact contract is assembled by the release workflow from these manifests plus CI-only facts (workflow URL, run id), which cannot be checked in ahead of the run.
