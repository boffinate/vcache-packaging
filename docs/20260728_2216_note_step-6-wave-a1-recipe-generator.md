# Step 6 Wave A1: the normalized package model and the recipe generator

Date: 2026-07-28

Status: Implemented (host-safe half only; nothing built yet)

Branch: `step6-second-vmod`

Related:

- [vmod-packager patterns and recipe generation](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md) — the plan this implements, Phase 0 remainder and Phase 1 in full
- [Step 5: selecting the second VMOD](20260728_2127_note_step-5-second-vmod-selection.md) — what was selected and on what evidence
- [Roadmap: outstanding packaging work](20260728_0916_roadmap_outstanding-packaging-work.md), Step 6
- [VMOD matrix failure isolation](20260728_0833_plan_vmod-matrix-failure-isolation.md) — Wave A2's half
- [`recipes/vmods/README.md`](../recipes/vmods/README.md) — the layout and how to run the generator

## What this wave is

Everything in Step 6 that can be done on the host without building anything: a normalized package model, a deterministic renderer, the reviewed data for `vmod-dict`, a deterministic source-archive derivation script, and the self-tests. Wave A2 owns the workflow wiring, `registry/vmods/dict.yml`, the `vmod-ci/v1` non-GitHub source fields, the VTC port, and every actual build.

Nothing here has produced a package. A rendered recipe is not a package and a package that compiles is not supportable; both gates are Wave A2's and Phase 2's.

## Phase 0: what was already done, and what was missing

| Plan item | State found | Action |
| --- | --- | --- |
| 1. Plan committed; inspected commit and licence recorded | Plan committed. Commit `252e0b0871eb9f7d6848ad92811288c821ca8cff` recorded in its Related block. Licence not recorded anywhere. | Cloned `xcir/vmod-packager` to scratch, confirmed `HEAD` is that commit, read `LICENSE`: **BSD-2-Clause, "Copyright (c) 2021, Shohei Tanaka"**. Added a paragraph to the plan's "Role of vmod-packager" recording the commit, the licence, and that nothing is vendored so no notice is owed today. |
| 2. README and AGENTS allow generated recipes | Done in Step 5 (`README.md` lines 18 and 22, `AGENTS.md` line 27 and the "If unsure" reading list). | None. |
| 3. Cross-reference from the matrix and Varnish downstream plans | The Varnish downstream plan referenced it in its body only. The matrix plan had **no** reference at all and no Related block. | Added a Related block to the matrix plan saying explicitly that the two are not separate projects and converge at Step 6; added the recipe-generation plan to the downstream plan's Related line. |
| 4. Varnish is vmod-packager's primary target; Vinyl support is reference material | Already documented, well, in the plan's "Correct interpretation of vmod-packager". | None. |

## The normalized model

`tools/vmod_recipe.py` builds one `vmod-recipe-model/v1` dictionary and renders both backends from it. Every field exists because a cachetag recipe fact needs a home; the table traces each one.

| Model field | Why it exists (cachetag fact it generalises) |
| --- | --- |
| `vmod.id`, `vmod.adapter`, `vmod.adapter_revision`, `vmod.overlay_revision` | The plan requires the adapter revision in the generation record and in the recipe. Cachetag has no equivalent because its recipe is hand-written and versioned with its source; a generated recipe needs to name the inputs that generated it. |
| `maintainer.name`, `maintainer.email` | `@MAINTAINER_NAME@` / `@MAINTAINER_EMAIL@` in cachetag's `control`, `changelog`, `copyright` and spec `%changelog`. Not in any data file: it is a project identity that already lives in the lane pin files, so it is a command-line input and duplicating it would create a second value to drift. |
| `upstream.name`, `contact`, `homepage`, `vcs_git`, `vcs_browser` | `Upstream-Name`, `Upstream-Contact` in `copyright`; `Homepage`, `Vcs-Git`, `Vcs-Browser` in `control`; `URL` in the spec. Cachetag hardcodes them because it packages itself. |
| `source.ref`, `commit`, `version`, `archive_sha256` | Cachetag's `CACHETAG_VERSION`, `CACHETAG_GIT_COMMIT`, `CACHETAG_SOURCE_SHA256` in the lane pin files, and `SCOPE.md`'s source policy. All four are mandatory: the generator refuses without them. |
| `source.archive_name`, `archive_url`, `directory` | `@SOURCE_URL@` and the spec's `%autosetup -n %{name}-%{version}`. Derived from a `stem` plus the version so a VMOD whose tarball stem differs from its package name is expressible. |
| `source.archive_method` | New. Cachetag has exactly one way to obtain source; third-party VMODs have two, and the plan forbids discovering one. Recorded, never guessed. |
| `source.source_date_epoch` | `CACHETAG_SOURCE_DATE_EPOCH`. The EL9 lane's 2026-07-28 bug — the cachetag package dated from the *Vinyl* commit — is exactly what happens when this is not a per-VMOD fact. |
| `source.clone_url`, `submodules` | New, for the derived-archive path. Cachetag has no submodules. |
| `package.debian_source_name`, `debian_binary_name`, `rpm_name`, `debian_section` | `Source:`, `Package:`, `Section:`, `Name:`. Separate fields because Debian's source and binary names need not agree and RPM's name need not match either. |
| `package.revision` | `registry/targets/*/package.revision`, and `registry/README.md`'s revision rules. Held in the overlay for now — see the open questions. |
| `package.summary`, `description` | `Description:` and `%description`. A list of lines, not a block scalar, because the manifest parser has no block scalars; the renderer produces Debian's continuation form and RPM's plain form from the same list. |
| `license.expression` | RPM `License:`, an SPDX expression. Cachetag: `MPL-2.0 AND BSD-2-Clause`. |
| `license.debian_short_name` | `debian/copyright`'s `License:` short name, a *different vocabulary* from SPDX. Conflating them gives a wrong field on one of the two targets. |
| `license.files` | RPM `%license`. Cachetag ships `LICENSE`. |
| `copyright.files[]`, `copyright.packaging` | `debian/copyright`'s `Files:` stanzas. Cachetag has three; the shape generalises directly. |
| `abi.mode` plus the `metadata.abi_expressions` output | `vinyld-abi-@VINYL_STRICT_ABI@`, `vinyld-vrt`, `vinyld-cohort-@COHORT_ID@` on Debian and the `vinyld(...)` capabilities on RPM. `mode` records what was *verified* of the VMOD, so an upstream later declaring `$ABI vrt` becomes a visible change. |
| `engine.*` | `@VINYL_PACKAGE_VERSION@`, `@VINYL_STRICT_ABI@`, `@VINYL_VRT@`, `@VINYL_VMODDIR@`, `@COHORT_ID@`, read from the cohort and target manifests. |
| `target.*` | `dist_tag`, arch, `distro_id` — the RPM release suffix and the release-asset filename need them. `debian_distribution` is the changelog suite, a lane fact, so it is a command-line input like the maintainer. |
| `build.bootstrap` | Cachetag's `dh $@ --without autoreconf` and the long comment explaining why. Generalised into a declared choice because a git-derived archive genuinely needs the other value. |
| `build.configure_args` | `--disable-static`, `--docdir=`. Adapter-shared plus overlay-specific. |
| `build.build_time_tests` | `dh_auto_test -- TESTS=cachetag_wal_test` and `%make_build check TESTS=...`. `none` is a first-class value. |
| `build.parallel_build` | New, and needed: `vmod-dict`'s `src/Makefile.am` generates `vmod_dict.man.rst` and `vcc_if.h` from one rule and builds `vmod_dict.3` from the former without declaring that edge. |
| `build.dependencies` | `Build-Depends:` / `BuildRequires:`. Adapter list, then bootstrap list when bootstrapping, then overlay list, then the engine dev package at its exact cohort version. Additive only. |
| `payload.vmod_object`, `man_pages`, `doc_files`, `license_files` | `%files`, the `.docs` file, and cachetag's `test -f %{buildroot}%{vinyl_vmoddir}/libvmod_cachetag.so` assertion. Explicit, never a glob. |
| `lintian_overrides.source`, `binary` | Cachetag's two override files. Present but empty for dict; the two overrides every generated package needs — no watch file, no bug to close — are in the templates. |
| `artifacts.*` | Contract item 7, and `metadata.py`'s existing filename generation for cachetag. |

### What the model deliberately does not carry

- **Native dependency strings.** The backends own them. The model carries facts; `token_values()` turns them into Debian relations and RPM tags.
- **Anything about how the source is fetched at build time.** Wave A2's workflow does that from the manifest and the overlay's `source` block.
- **A hook or a script path.** There is no shell anywhere in `recipes/vmods/`, on purpose.

## What was adopted from vmod-packager, and what was not

Inspected at `252e0b0871eb9f7d6848ad92811288c821ca8cff`, BSD-2-Clause, Copyright (c) 2021 Shohei Tanaka. Nothing vendored; no code copied, so no attribution notice is owed in any file today.

**Adopted, as a shape:**

- **Default adapter plus explicit escape hatches.** Its `sample-src/<vmod>_env.sh`, `_init.sh`, `_config.sh`, `_build.sh` family is exactly the right decomposition — declarative facts, dependency installation, bootstrap/configure, and a whole custom build. Our `adapters/autotools/adapter.yml` plus `overlays/<id>/overlay.yml` is the declarative half of that, and the `custom/` adapter directory the plan reserves is the other half, unbuilt until a selected VMOD needs it.
- **One lifecycle across two native backends.** Its `tplt/debian/` and `tplt/rpm/` render from the same variables. Ours render from the same model.
- **A catalogue of build peculiarities.** `libvmod-digest` needing mhash, `vmod-reqwest` needing Cargo, `slash` needing `VARNISHSRC` and `./bootstrap` — a useful onboarding checklist, and the reason `bootstrap` is a declared adapter knob rather than an assumption.

**Rejected, with the concrete artefact that made the call:**

| Rejected | What its templates actually do |
| --- | --- |
| Placeholder maintainers | `tplt/debian/tpl/control`: `Maintainer: %PFX%%VMOD% <example@localhost>`. Our generator refuses a maintainer containing `example` or `localhost`, and refuses an absent one. |
| Non-machine-readable licences | `tplt/rpm/tplt.spec`: `License: See original VMOD source license file.` and `tplt/debian/tpl/copyright`: `License: See original VMOD source license file.` Ours requires an SPDX expression, a Debian short name, *and* a reviewed stanza in `licenses/`, and refuses to render without all three. |
| Wall-clock dates | `%TIME%` in both changelog templates. Ours takes every date from the recorded source epoch and has no clock call in it. |
| Disabled debug packages | `tplt/rpm/tplt.spec` line 1: `%global debug_package %{nil}`. Ours never emits it and a self-test asserts its absence. |
| Broad payload globs | `tplt/rpm/tplt.spec`: `%{_libdir}/%SOFTNAME%/vmods/*.so`. Ours lists the exact object and the exact man page, and a self-test asserts no glob in `%files`. |
| Ignored dependency analysis | `tplt/debian/tpl/rules`: `dh_shlibdeps --dpkg-shlibdeps-params=--ignore-missing-info`, and an emptied `override_dh_usrlocal:`. Ours does neither. |
| Outdated policy declarations | `Standards-Version: 3.9.6`, `debhelper (>=9)`, `compat` file. Ours: `4.7.2`, `debhelper-compat (= 13)`, `Rules-Requires-Root: no`. |
| Version schemes that encode the engine | `%VRT%.%VER%` as the package version. Ours keeps the upstream version and the canonical package revision, as `registry/README.md` defines them. |
| Auto-executing discovered upstream files | Its driver runs `<vmod>_config.sh` from the source area. Ours executes nothing, and the packaging data contains no shell at all. |
| Engine-version ranges as ABI policy | Its generic RPM `Requires` names `varnish` by version. Ours generates the exact strict-ABI and cohort expressions from the engine row via `metadata.abi_expressions`. |

## `vmod-dict`: verified upstream facts

All verified 2026-07-28 against the tree at tag `v1.7`, peeled commit `784584d272894a39cf995377618aad551a196424`, cloned from `https://git.gnu.org.ua/vmod-dict.git`. Not from the survey, not from another distribution's package.

| Fact | Value | Where verified |
| --- | --- | --- |
| **Licence** | **GPL-3.0-or-later** (Debian short name `GPL-3+`) | `COPYING` is the GNU GPL v3, 674 lines. Every source header, `configure.ac` included, says "either version 3, or (at your option) any later version". `src/vmod_dict.vcc`'s COPYRIGHT section says "License GPLv3+". The `acvmod` submodule is the same. |
| Tag object | Annotated, peels to `784584d2…` | `git cat-file -t v1.7` → `tag`; `git rev-parse v1.7^{commit}` |
| Version | `1.7` | `AC_INIT([vmod-dict],[1.7],[gray@gnu.org])`; newest `NEWS` entry "Version 1.7, 2026-03-25" |
| Upstream contact | `Sergey Poznyakoff <gray@gnu.org>` | `AUTHORS`, `AC_INIT` |
| Source epoch | `1774429462` (2026-03-25T09:04:22Z) | committer date of `784584d2` |
| Submodule | `acvmod` at `4fba6604d1d1e586274376a20841be0966bf7df3`, `.gitmodules` URL `git://git.gnu.org.ua/acvmod.git` | `git ls-tree v1.7 acvmod`, `git show v1.7:.gitmodules` |
| Build tools | pkg-config, python3 ≥ 3.5, rst2man, libtool | `acvmod/acvmod.m4`: `PKG_PROG_PKG_CONFIG`, `AM_PATH_PYTHON([3.5])`, `AC_PATH_PROGS(RST2MAN, …)`; `configure.ac`: `LT_INIT` |
| Runtime dependencies | none beyond the engine | `src/Makefile.am`: `libvmod_dict_la_LIBADD=` is empty |
| Payload | `$(VMODDIR)/libvmod_dict.so`, `man3/vmod_dict.3` | `src/Makefile.am`: `vmod_LTLIBRARIES = libvmod_dict.la` with `-module -export-dynamic -avoid-version`; `dist_man_MANS = vmod_dict.3`; `$Module dict 3` in the VCC, so section and installed path agree — unlike cachetag, which has a real section mismatch its lintian overrides deliberately do not hide |
| Not installed | the `.vcc` and the generated `.rst` files | `EXTRA_DIST` only, no install rule |
| `$ABI` | undeclared, therefore strict | `src/vmod_dict.vcc` has no `$ABI` line |
| Engine detection | `ACVMOD_VINYLAPI([6.0.0],[9.0.0])` | `configure.ac`; probes `vinylapi` first, falls back to `varnishapi` |

Debian build dependencies rendered: `debhelper-compat (= 13)`, `pkgconf`, `python3`, `python3-docutils`, `vinyl-cache-dev (= 9.0.1-1)`.
RPM: `gcc`, `make`, `pkgconfig`, `python3`, `python3-docutils`, `vinyl-cache-devel = 9.0.1-1.el9`.

`python3` is not only for `rst2man`: `vcc_if.c` is a `nodist` source regenerated by the engine's `vmodtool.py` on every build.

## The source archive: a finding that changed the answer

The Step 5 research package recorded "No upstream tarball located", and the brief for this wave was written on that basis. It is wrong, and the correction matters.

`src/vmod_dict.vcc`'s DOWNLOADS section names `https://download.gnu.org.ua/release/vmod-dict`. That directory carries `vmod-dict-1.7.tar.gz` **and a detached PGP signature**, both published 2026-03-25 — the same day as the tag.

- `https://download.gnu.org.ua/release/vmod-dict/vmod-dict-1.7.tar.gz`
- **414,559 bytes, sha256 `eb2a86a780ba9628106dbe858d17ec4589ad6dcb70c6ad53decb5d32824e098c`**
- `.sig` alongside, 566 bytes

### Re-confirmed in a container after the Wave A1 audit

The audit could not verify the live upstream facts itself, so they were re-fetched from inside a `debian:13` container rather than from the host, 2026-07-28:

```text
414559 vmod-dict-1.7.tar.gz
eb2a86a780ba9628106dbe858d17ec4589ad6dcb70c6ad53decb5d32824e098c  vmod-dict-1.7.tar.gz
```

and the EL9 autotools versions re-read from `almalinux:9`:

```text
autoconf 2.69   automake 1.16.2   libtool 2.4.6
```

Both match what Wave A1 originally recorded. Nothing changed.

### The signature verifies

Verified once, in a `debian:13` container with GnuPG, against the key **upstream itself publishes** — `https://puszcza.gnu.org.ua/people/viewgpg.php?user_id=101`, the developer key page on the same project host that serves the release directory. Not a keyserver lookup, and not a guess from the key id in the signature.

```text
gpg: Signature made Wed Mar 25 09:06:01 2026 UTC
gpg:                using RSA key 4BE4E62655488EB92ABB468F79FFD94BFCE230B1
gpg: Good signature from "Sergey Poznyakoff <gray@gnu.org.ua>"
gpg:                 aka "Sergey Poznyakoff <gray@gnu.org>"
Primary key fingerprint: 4BE4 E626 5548 8EB9 2ABB  468F 79FF D94B FCE2 30B1
```

**Signing key fingerprint: `4BE4E62655488EB92ABB468F79FFD94BFCE230B1`** (key id `79FFD94BFCE230B1`, RSA 4096, uids `Sergey Poznyakoff <gray@gnu.org.ua>` and `<gray@gnu.org>`). The signature was made 62 seconds after the tag object, which is consistent with one `make dist && gnupload` run.

This is a one-time provenance check, deliberately not a CI step. Making CI verify it would mean pinning the key as a recorded input and deciding what happens when it is rotated or expires — a new class of recorded identity that the [open questions](#open-questions-for-the-audit) still lists. **CI continues to rely on the pinned digest alone**, which is a check on the exact bytes rather than on a trust path, and the digest was itself derived from bytes whose signature we have now confirmed once.

It is a complete `make dist` archive: `configure` (header says "Generated by GNU Autoconf 2.71"), `Makefile.in`, `src/Makefile.in`, `tests/Makefile.in`, `aclocal.m4`, `config.h.in`, `build-aux/` (`missing`, `install-sh`, `compile`, `depcomp`, `config.guess`, `config.sub`, `ltmain.sh`), `m4/libtool*.m4`, the `acvmod` macros vendored in directly, the generated `src/vmod_dict.3`, `.rst` and `.man.rst`, and `tests/testsuite` plus `tests/package.m4`. `configure.ac` and `src/vmod_dict.c` are byte-identical to the tag's.

### Why this is not merely nicer

Deriving the archive from the tag produces a git tree with **no `configure`**, so the build must bootstrap. `configure.ac` declares `AC_PREREQ([2.71])` and `AM_INIT_AUTOMAKE([1.16.5 …])`. Verified in a container on 2026-07-28:

```text
docker run --rm almalinux:9 dnf info autoconf automake libtool python3-docutils
  autoconf 2.69   automake 1.16.2   libtool 2.4.6   python3-docutils 0.16
```

`autoconf` 2.69 fails `AC_PREREQ([2.71])` outright, and `automake` 1.16.2 fails `AM_INIT_AUTOMAKE([1.16.5])`. **The derived-from-tag archive is not buildable on `el9-x86_64`**, one of the two selected targets, without carrying newer autotools into the EL9 buildroot — which would be a reviewed exception, not a default. Debian 13 is fine (autoconf 2.72, automake 1.17), so the discrepancy would have appeared as a one-target failure in Wave A2 with a confusing cause.

### The decision, and the deviation from the brief

`overlays/dict/overlay.yml` declares `source.archive.method: upstream-release` and `dict.yml` pins the tarball digest. This deviates from the brief, which directed the derived-git-tag path. The reasons are the two above: the derived path cannot build on a selected target, and a signed upstream release archive is a stronger provenance statement than anything we derive. Both facts are container-verified rather than argued.

The derivation script is still delivered, tested, and pinned, because it is a general capability this project needs — most viable VMOD candidates publish tags and nothing else — and because it is the recorded fallback if `download.gnu.org.ua` is ever ruled an unacceptable source.

### The derived archive, pinned anyway

`scripts/ci/vmod-source-archive.sh`, run inside Linux containers:

```sh
sh scripts/ci/vmod-source-archive.sh \
    --url https://git.gnu.org.ua/vmod-dict.git \
    --tag v1.7 --commit 784584d272894a39cf995377618aad551a196424 \
    --stem vmod-dict --version 1.7 --epoch 1774429462 \
    --submodule acvmod=4fba6604d1d1e586274376a20841be0966bf7df3 \
    --out vmod-dict-1.7.tar.gz
```

**sha256 `499f48cbcf5a961633f053778403b95658f22abeb72849d3da13f9ca35c893e4`, 50,768 bytes.**

Reproduced identically three times: twice in one `debian:13` container, and once in `almalinux:9` with a different GNU tar and git, where it was asserted with `--sha256` and passed. The `git://` → `https://` `insteadOf` override worked in both (`Submodule 'acvmod' (git://git.gnu.org.ua/acvmod.git) registered`, then cloned over HTTPS).

Determinism measures, and why each is there:

- `--format=gnu`, because the pax default writes extended headers carrying `atime` and `ctime`;
- `--sort=name`, because member order is otherwise whatever `readdir(3)` returns;
- `--mtime=@<epoch>`, `--owner=0 --group=0 --numeric-owner`, `--mode=go-w`;
- `umask 022` before the checkout, because git creates files `0666 & ~umask` and the tar header records the result;
- `gzip -9n`, so no filename or timestamp lands in the gzip header;
- an explicit refusal to run without GNU tar. macOS bsdtar has neither `--sort` nor GNU's header layout, so a digest pinned on the host would not be the digest CI derives. **Do not run this on the host and trust the number.**

The script also refuses an undeclared submodule, so an upstream adding one has to become a recorded decision rather than silently changing what is compiled.

## Naming

`vmod-dict` on both targets: Debian source, Debian binary, and RPM name.

The rule is *use upstream's own name*, which is what cachetag already does — `libvmod-cachetag` is upstream's name too, not a downstream invention. Upstream's `AC_INIT` says `vmod-dict` and its release tarball is `vmod-dict-1.7.tar.gz`.

Non-duplication, against the list in the [refreshed downstream plan](20260726_0824_plan_varnish-downstream-vmod-packaging.md): Varnish Software publishes `varnish-modules`, `vmod-cfg`, `vmod-digest`, `vmod-fileserver`, `vmod-geoip2`, `vmod-jq`, `vmod-querystring`, `vmod-redis`, `vmod-reqwest`, `vmod-rers`, `vmod-uuid`, plus `varnish-otel` and `vmod-k8s-endpoint` in-repo. **Nothing named for dict.** The name is free.

Availability is a snapshot, not a guarantee. If Varnish Software later publishes a `vmod-dict`, the two packages would share a name while depending on different engines — theirs on `varnish (= <exact>)`, ours on `vinyld-abi-<hash>` and `vinyld-cohort-<id>`. They could never satisfy each other's dependencies, so the failure mode is an unresolvable transaction rather than a silently wrong install, but it would still be confusing in a machine with both repositories enabled. The escape, if it happens, is a `vinyl-` prefix on our side, which is a one-line overlay change plus a package revision — and it is only worth doing when it happens.

Not adopted: an RPM `Provides: vmod(dict)`. The downstream plan raised it as an option, from the convention `recipes/el9/find-provides` uses for the VMODs bundled *inside* the Vinyl package — `vmod(<name>)%{?_isa} = <version-release>`. That generator is scoped by `%global __find_provides` inside `vinyl-cache.spec.in` and does not run for a separately built VMOD package, so a generated spec would have to declare the provide by hand. Nothing consumes it today: no VCL-level dependency is expressed anywhere in this project, and adding a capability nothing requires is a name we would then have to keep. Left out until something needs it, and recorded so the omission is visible rather than accidental.

## Verification run

| Command | Result |
| --- | --- |
| `release_tool.py validate` | OK, 10 manifests, schema mode |
| `release_tool.py validate --require-releasable` | OK, 10 manifests, releasable mode |
| `release_tool.py --no-cachetag-cross-check validate` | OK, cross-check reported as skipped |
| `release_tool.py selftest` | 112/112 pass (was 111; one added asserting RPM requires do not use Debian names) |
| `ci_matrix.py selftest` | 151/151 pass, then 125/125 for the generator |
| `ci_matrix.py check-catalog` | OK, 1 VMOD (dict is staged, not catalogued yet) |
| `vmod_recipe.py selftest` | 125/125 pass (116 at first submission; 9 added by the audit fixes) |
| Archive derivation, `debian:13`, two consecutive runs | identical sha256 |
| Archive derivation, `almalinux:9`, `--sha256` assertion | passed against the `debian:13` digest |

No workflow file was touched, so containerised `actionlint` was not needed. `git diff --stat main -- .github/` is empty.

## What Wave A2 must consume

1. **Move `recipes/vmods/overlays/dict/dict.yml` to `registry/vmods/dict.yml`.** It already validates against `vmod-ci/v1` with discovery id `dict`; a self-test asserts that so it cannot drift while it waits. Moving it adds four rows to the expected ledger (two targets × the `ci` and `release` tiers), so the workflow that produces them must land in the same change.
2. **Add the non-GitHub source fields to `vmod-ci/v1`** and a non-`actions/checkout` path in `vmod-package.yml`. The clone URL to carry is `https://git.gnu.org.ua/vmod-dict.git`, already recorded as `source.clone_url` in the overlay. `repository: git.gnu.org.ua/vmod-dict` passes today's pattern by luck, not by design.
3. **Fetch the archive by digest**, not by clone: `https://download.gnu.org.ua/release/vmod-dict/vmod-dict-1.7.tar.gz`, sha256 `eb2a86a780ba9628106dbe858d17ec4589ad6dcb70c6ad53decb5d32824e098c`, 414,559 bytes. Verifying the detached `.sig` needs upstream's signing key recorded first; until then the digest pin is the check.
4. **Call the generator per row**, passing the maintainer and the changelog suite from the lane pin file:

   ```sh
   python3 tools/vmod_recipe.py generate \
       --manifest registry/vmods/dict.yml \
       --overlay  recipes/vmods/overlays/dict/overlay.yml \
       --cohort   "$COHORT_ID" --target "$TARGET_ID" \
       --maintainer "$MAINTAINER_NAME <$MAINTAINER_EMAIL>" \
       --debian-distribution "$DEBIAN_DISTRIBUTION" \
       --out "$work/recipe"
   ```

   Then copy `$work/recipe/debian/` (or the `.spec`) into the unpacked source and build. Record `generation-record.json`'s `recipe_sha256` in the result evidence.
5. **Expected artifact names**, emitted by `vmod_recipe.py names` and asserted by the self-tests:

   | Target | Binary | Source package files |
   | --- | --- | --- |
   | `debian-13-amd64` | `vmod-dict_1.7-1_amd64.deb` (+ `vmod-dict-dbgsym`) | `vmod-dict_1.7.orig.tar.gz`, `vmod-dict_1.7-1.debian.tar.xz`, `vmod-dict_1.7-1.dsc` |
   | `el9-x86_64` | `vmod-dict-1.7-1.el9.x86_64.rpm` (+ `-debuginfo`, `-debugsource`) | `vmod-dict-1.7-1.el9.src.rpm` |

   Release assets: `vmod-dict-1.7-1-debian-13-amd64.deb`, `vmod-dict-1.7-1-el9-x86_64.rpm`.
6. **Payload assertions to add to the lane scripts**, mirroring `stage-cachetag.sh`: `libvmod_dict.so` present in `$VINYL_VMODDIR`, no `.la` or `.a`, `Depends`/`Requires` carrying the strict ABI, VRT and cohort tokens, and the hardening inspection unchanged.
7. **The behaviour gate**, unchanged from Step 5: `tests/ci.at` and `tests/cs.at` ported to VTC with plain `import dict;` through `-p vmod_path`, `tests/num.dict` staged, upstream's expected values as the oracle. Both fixtures are in the release tarball at `tests/`.
8. **The dict RPM advertises no `vmod(dict)` capability**, and that is deliberate — see Naming above. `recipes/el9/find-provides` is scoped to `vinyl-cache.spec.in` and does not run for this package; the generated spec's `__provides_exclude_from` suppresses the plugin's soname provide and adds nothing in its place. Noted so an empty `Provides` is not reported as a defect.

## Deviations from the plan and the brief

1. **`upstream-release` instead of `derived-git-tag` for dict.** Reasons and evidence above. The derivation script is delivered and pinned regardless.
2. **`metadata.py`'s RPM requires were wrong and are fixed here.** It emitted the Debian virtual-package names on the RPM side. Nothing consumed them, so nothing was broken; a generated spec rendering from them would have depended on `vinyld-abi-<hash>` on a target whose provide is `vinyld(abi)%{?_isa}`. Fixed in the one authoritative place, as the plan requires, rather than worked around in the generator. Three `selftest.py` assertions updated, one added.
3. **Two `ci_matrix.py` schema changes.** `ADAPTERS` gains `autotools`; `VERSION_RE` accepts two or more numeric components rather than exactly three, because `vmod-dict` releases as `1.7`. Both are prerequisites, not scope creep: without either, dict's manifest cannot be recorded at all.
4. **`recipes/vmods/licenses/` is not in the plan's layout sketch.** It is one file per Debian short name. The alternative was licence text in each overlay, which would have meant the same GPL-3+ stanza copied per VMOD, and Debian's short names are a different vocabulary from SPDX so one field could not serve both.
5. **The generator self-tests run from `ci_matrix.py selftest`.** The brief forbids workflow edits this wave, and CI's structural-validation job invokes exactly `release_tool.py selftest` and `ci_matrix.py selftest`. Chaining them in is how the tests reach CI today. Wave A2 may give the generator its own step; nothing depends on the chaining.
6. **`package.revision` lives in the overlay.** `registry/targets/<cohort>/<target>.yml` is cachetag-shaped — it carries `cachetag.version` and `package.source_name: libvmod-cachetag` — so a per-VMOD revision has no home there yet. See the open questions.
7. **`--maintainer` and `--debian-distribution` are command-line inputs, not data files.** Both already exist in the lane pin files. Putting them in the overlay would create a second value to drift; the generator fails closed when they are absent, which is the property the contract asks for. `--maintainer` is required for `generate` only: `names` and `model` render nothing and answer a question a maintainer is irrelevant to, so refusing them would be refusing to answer rather than refusing to publish.

## Considered decisions the audit asked to have recorded

### `dh_missing` asymmetry between the two families: kept

The generated Debian recipe does not run `dh_missing --fail-missing`, while the RPM side gets EL9's `%files`-versus-buildroot check for free — anything installed but not listed fails the build. The asymmetry is real and it is deliberate.

Cachetag's audited recipe, which is this template's policy oracle, does not run `dh_missing` either. Matching the oracle is the point of the tracer bullet: a generated recipe that is *stricter* than the hand-written one it is being validated against would make any difference in output impossible to attribute. Payload strictness on the Debian side is delivered instead by the explicit assertion the template does carry — `override_dh_auto_install` fails when the VMOD object is not staged in the recorded `vmoddir` — plus the `.la`/`.a` deletions and the lane's own `dpkg-deb -c` payload assertions.

Raising both families to `dh_missing --fail-missing` is worth doing, and it is future hardening rather than tracer-bullet scope: it should land for cachetag and dict together, in a change whose only purpose is that, so the resulting package-byte diff has one cause. Recorded here so the gap is a decision with a date rather than something nobody noticed.

### Locale-sensitive date rendering: removed

`strftime`'s `%a` and `%b` follow `LC_TIME`, so the same recorded epoch would render `Wed` under `C` and `mer.` under `fr_FR.UTF-8` — recipe bytes depending on the environment the generator happened to run in, which defeats the determinism the contract requires. Both are now rendered from explicit English tables rather than by setting `LC_TIME` at import, because `setlocale` mutates process-global state for every other module in the interpreter and a lookup table does not. Self-tests assert that no `strftime(` or `setlocale(` call appears in the generator, and exercise every weekday and every month name.

## Open questions for the audit

1. **Is `download.gnu.org.ua` an acceptable release source?** Vinyl's own archive is pinned from `vinyl-cache.org/downloads` by digest, so the precedent exists, but this is somebody else's host. If the answer is no, flip `source.archive.method` to `derived-git-tag`, put digest `499f48cb…` in `dict.yml`, add `bootstrap: autoreconf` to the overlay — and then resolve the EL9 autoconf 2.69 problem, which is a real blocker with no cheap fix.
2. **Should the detached PGP signature be verified, and whose key?** The tarball ships one. Verifying it needs upstream's key recorded as a pinned input, which is a small but genuine new class of recorded identity.
3. **Where does a second VMOD's per-target evidence live?** `registry/targets/<cohort>/<target>.yml` records exactly one VMOD's build evidence, package revision and test results, and its schema names cachetag. With two VMODs the registry needs either a per-VMOD dimension in that path or a separate evidence manifest. This is a Step 6 question that Wave A1 has parked, not solved; `package.revision` sitting in the overlay is the placeholder.
4. **Should `vinyl-trunk-pinned` return for dict?** Excluded because Vinyl trunk's `AC_INIT` says `trunk` and `acvmod.m4` does arithmetic on the pkg-config modversion. Unchanged by this wave; recorded so it is not rediscovered.
5. **`parallel_build: "no"` for dict is a precaution, not a measurement.** The missing `vmod_dict.man.rst` prerequisite is real in `src/Makefile.am`, but nobody has observed the race. If Wave A2's builds are slow because of it, measure before relaxing it.
6. **The `custom/` adapter directory does not exist.** The plan's layout sketch shows it. Creating an empty directory to match a sketch would be exactly the speculative generalisation the plan warns against, so it arrives with the VMOD that needs it — Phase 4, at the earliest.
