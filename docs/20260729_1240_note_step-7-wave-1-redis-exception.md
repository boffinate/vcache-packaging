# Step 7 Wave 1: libvmod-redis, the controlled exception

Date: 2026-07-29

Status: **Implemented; proven locally as far as an arm64 host allows.** The generator, the catalog, the lane wiring and the fixture contract are complete and green on the host-safe battery. The patch, the bootstrap, the build and the full 20-VTC behaviour suite were proven in `debian:13` and `almalinux:9` containers against a real Vinyl Cache 9.0.1. No package was built, because the lane images are digest-pinned to x86_64 and this host is arm64 — the same limitation the [Wave 0 note](20260729_1021_note_step-7-wave-0-lane-consolidation.md) records. Wave 2 is the live run.

Branch: `step7-redis-exception`, off `main` at `b0cccce`.

Related:

- [Wave 0: lane consolidation](20260729_1021_note_step-7-wave-0-lane-consolidation.md) — the fixture-contract seam this closes, and the rpmlint asymmetry it left standing with a reason
- [Roadmap Step 7](20260728_0916_roadmap_outstanding-packaging-work.md) — "a third VMOD only to exercise ONE useful variation"
- [Recipe-generation plan](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md) — Phase 3 and verification case 10

## The exception, and the thing that turned out not to be one

**The exception is the patch.** `libvmod-redis` 9.0-23.1 cannot configure against Vinyl Cache at all. The first thing `configure.ac` does after its program checks is `m4_ifndef([VARNISH_PREREQ], AC_MSG_ERROR([Need varnish.m4 -- see README.rst]))`, and Vinyl publishes `vinyl.m4` with `VINYL_` names. Nothing downstream of that runs. This is the first selected VMOD in that position: cachetag is ours, and `vmod-dict` builds unmodified on both engines.

**The dependencies are not the exception, and that is a finding worth recording.** The obvious reading of "a VMOD with four external libraries" is that it stresses the dependency model. It does not. `build_dependencies` has been a per-family list in the overlay schema since Wave A1, additive over the adapter's shared list, and a VMOD needing `libhiredis-dev`, `libev-dev`, `libssl-dev` and `autoconf-archive` rather than nothing is a **longer list, not a new capability**. Not one line of generator code changed to express them. The schema was already right, and the way we found out is that it took no work — which is the cheapest possible confirmation of a design decision and worth writing down rather than passing over.

Two smaller things did need work, and both were latent defects rather than new capabilities:

- **`bootstrap: autoreconf` had never had a consumer, and did not work.** dict ships a `make dist` archive; redis's derived archive is a git tree. Measured in a `debian:13` container against Vinyl Cache 9.0.1: `autoreconf` passes `Makefile.am`'s `ACLOCAL_AMFLAGS = -I m4 -I ${VINYLAPI_DATAROOTDIR}/aclocal` to a shell that has never heard of that variable, asks `aclocal` for `-I /aclocal`, and exits 1. Both backends now export `VINYLAPI_DATAROOTDIR` and `ACLOCAL_PATH` before bootstrapping. `bootstrap: none` renders nothing, so dict's `debian/rules` is byte-identical.
- **`_license_stanzas` rendered one stanza.** copyright-format 1.0 requires a `License:` paragraph for every short name any `Files:` paragraph uses. dict's two `Files:` paragraphs are both `GPL-3+`, so the single-stanza form was indistinguishable from the correct one. redis is under three licences and made the difference visible.

## The verified facts

Everything below was read from the upstream tree at the selected tag on 2026-07-29, or measured in a container on the same day. Nothing is taken from a survey verdict or from another distribution's package.

| Fact | Value | How it was established |
| --- | --- | --- |
| tag | `9.0-23.1`, annotated, **unsigned** | `git cat-file -t` → `tag`; no signature block |
| peeled commit | `b6ca669fc9af3399f3845d9d4930683b4e378aa8` | `git rev-parse 9.0-23.1^{commit}` |
| version | **23.1**, not 9.0 and not 9.0-23.1 | `AC_INIT([libvmod-redis], [23.1], [], [vmod-redis])` |
| release archives | **none at all** | the GitHub releases API returns an empty list |
| submodules | none | no gitlink in the tree, no `.gitmodules` |
| `$ABI` | `strict`, **explicitly declared** | `src/vmod_redis.vcc:9`, unlike dict's undeclared-and-defaulted |
| licences | three | `LICENSE` (2-clause), `src/crc16.c` (3-clause, endorsement clause), `src/sha1.c` (public domain) |
| VTC count | **20**, not 18 | `ls src/tests/*.vtc`; see below |
| autotools floor | `AC_PREREQ([2.68])`, `AM_INIT_AUTOMAKE([1.12 …])` | satisfied by EL9's autoconf 2.69 / automake 1.16.2, proven by bootstrapping there |

**The tag→version rule, recorded because it is a trap.** Upstream tags `<varnish-series>-<vmod-version>`. `9.0-23.1` means "release 23.1 of the VMOD, for the Varnish 9.0 series". Reading `9.0` out of the tag and calling it the version would produce a package whose version disagrees with its own `configure.ac`, and the source stage's `AC_INIT` cross-check would catch it — but only after a build had been attempted.

**The VTC count is 20, not the 18 the brief carried.** `ls src/tests/*.vtc` at the tag returns twenty files. The two the count missed are `standalone.template.vtc` and `clustered.template.vtc`; they are titled "Minimal test template", but `configure.ac` globs `tests/*.vtc` into `VMOD_TESTS`, so upstream's own `make check` runs them, and they assert real counter values against real servers. They are ported and run. `standalone.6000000.sentinels.vtc.disabled` is not, because the glob does not match it. All twenty pass on both distributions.

## The reviewed patch

`recipes/vmods/overlays/redis/patches/0001-build-against-vinyl-cache.patch`, sha256 `3e18e62688a7a6f148084d2930a79d80a3c9692716621ab9e124f8057392eb01`, 124 lines including a DEP-3 header. Four files, build system only. **No C source, no VCC, no test and no documentation is touched**, so the module's behaviour is upstream's and the ported VTCs are testing upstream's expectations rather than ours.

| File | Change |
| --- | --- |
| `autogen.sh` | ask `pkg-config` for `vinylapi`'s datarootdir |
| `Makefile.am` | `ACLOCAL_AMFLAGS` uses `${VINYLAPI_DATAROOTDIR}` — the exact line `vinyl.m4` documents |
| `configure.ac` | `VARNISH_PREREQ`/`VARNISH_VMODS`/the `m4_ifndef` guard become `VINYL_*`; the `PKG_CHECK_VAR([LIBVARNISHAPI_LIBDIR])` + `AC_SUBST([VARNISH_LIBRARY_PATH])` pair is **deleted** |
| `src/Makefile.am` | `VARNISHAPI_CFLAGS`/`LIBS` and `VARNISH_LIBRARY_PATH` become the `VINYL_` substitutions; the VTC log compiler drives `vinyltest` |

Two decisions inside it are worth stating.

**The `PKG_CHECK_VAR` pair is deleted rather than renamed.** `vinyl.m4`'s `_VINYL_PKG_CONFIG` already substitutes `VINYL_LIBRARY_PATH` as `$VINYLAPI_LIBDIR:$VINYLAPI_LIBDIR/vinyl-cache`. Renaming the local definition would create a second, divergent authority for the same value — the failure mode the whole registry is arranged to avoid.

**`VINYL_PREREQ([9.0], [9.1])` is kept, not dropped.** At the tag it is live code, not the commented-out line trunk carries. It does numeric comparison on `vinylapi.pc`'s `Version`, which is precisely why redis is `vinyl-release` only: Vinyl trunk's `AC_INIT` still says the literal string `trunk`. Dropping the macro would buy a trunk lane by deleting a real compatibility check to make a matrix row green.

**The constraint has one root cause, shared with dict.** dict is excluded from trunk because `acvmod.m4` splits the modversion on `.` and does configure-time arithmetic on the major component; redis is excluded because `VINYL_PREREQ` compares numerically. Same cause, same fix, and the lane returns for both VMODs at once or for neither. `INJECT_ENGINE_ROW`'s comment now records that, because it is why `engine/vinyl-trunk-pinned/debian-13-amd64` still has exactly one consumer.

## The rulings, and how each is implemented

### R1 — patches are declared, digested, ordered and fail closed

`patches:` in the overlay is a list of `{file, sha256}` under `overlays/<id>/patches/`, applied in the written order. Rendered as `debian/patches/` plus a 3.0 (quilt) `series` for Debian, and `PatchN:` with `%autosetup -p1` for RPM — same patches, same order, one declaration.

Three properties, each a test:

- **missing file** → generation fails, naming the path;
- **digest moved** → generation fails, printing both digests and saying not to update the pin to make it pass;
- **content is inside `recipe_sha256`** → the patch is BOTH an input of the generation record (`inputs["patch:<name>"]`) and a rendered output. Changing one byte changes the recipe digest on both families, which is verification case 10's "cannot be omitted or silently replaced" stated as an assertion.

Patch bytes bypass the whitespace normaliser. A context line in a unified diff may legitimately end in a space, and normalising one would produce a patch that silently no longer applies.

**And the lane refuses one too.** `generate.sh --omit-patch` (injection `patch_omission`) deletes a rendered patch after generation. Debian refuses because `dpkg-source` cannot read a file the series names — measured: `dpkg-source: error: cannot read libvmod-redis-23.1/debian/patches/0001-build-against-vinyl-cache.patch`. EL9 refuses because `build-rpm.sh` now compares the spec's `Patch` lines against the files rendered beside it. Two families, two mechanisms, both fatal.

### R2 — what a bounded shim is, recorded

**A per-VMOD, reviewed, digested, manifest-visible patch is the bounded form of a shim, and it is allowed.** It is bounded on four axes: it belongs to one overlay, it is committed content rather than generated content, its bytes are pinned by digest, and it moves recorded release evidence when it changes.

**Blanket substitution and shared shim layers stay forbidden.** A pass that rewrote `VARNISH_` to `VINYL_` across any VMOD's tree would have handled this case with less typing and would be exactly the wrong thing: nobody would review what it did to the next VMOD, and nothing would record what it had done to this one.

**A capability shared by N VMODs is an adapter decision.** If a second VMOD needs the same rename, the answer is not a second copy of the patch: it is a deliberate decision about whether the adapter should express it, made once, with the revision bumped and every already-generated recipe confirmed unchanged. `recipes/vmods/README.md` now carries this in its "Patching a VMOD's source" section, and the recipe-generation plan carries it as a dated addition.

### R3 — the fixture contract

The overlay declares `behaviour:` — fixture packages per family, the fixture root inside the archive, the patterns beneath it, the `-D` macros, and the driver. `vmod_recipe.py lane-env` renders them; `run.sh` passes them through an `--env-file`, because three of the five are word lists and `$common_env` is expanded unquoted.

`scripts/ci/lib/vtc-suite.sh` gained two capabilities and **no branches**. A selftest asserts its code mentions no VMOD, no file extension, no macro name and no driver name.

- `vtc_stage_fixtures` takes a root and keeps each match's path relative to it. Necessary, not cosmetic: upstream's runner resolves its TLS certificates as `$ROOT/assets/…`, and a flattened copy would leave it looking for files that are not there.
- `vtc_run_suite` takes a driver. `none` is the old behaviour — the whole ledger through `vinyltest` in one go. Otherwise it is an executable staged out of the archive, given the test driver as its first argument and one VTC as its last, once per case. That shape is forced: upstream's runner decides which fixture topology to launch by matching the **last** argument against `standalone.*` or `clustered.*`, and it tears the servers down on exit.
- `vtc_install_packages`, so an empty declaration is a no-op rather than a branch in each caller.

**dict migrated to the contract in the same change**, which is what makes "generic" a measurement. Its two hardcoded values moved from a `case` in `run.sh` into its overlay; its rendered recipe did not move, because the contract is lane data and is not rendered into any recipe.

**The skip rule falls out of the generic code.** `vtc_run_suite` asserts one `TEST … passed` line per ledger entry on both paths. On the driver path that is also what catches a driver deciding by itself not to run a case and exiting 0 — which upstream's runner does below a minimum fixture version. A skip nobody declared is indistinguishable from a test that never ran, so it fails the row.

### R4 — TLS on

`--enable-tls` is stated in the overlay rather than inherited, so a change of upstream default becomes a visible diff. It costs `libssl`/`libcrypto` and `libhiredis_ssl` at build time. `configure` reports `checking for TLS support... enabled` on both distributions, and `standalone.6000000.TLS.vtc` passes on both.

### R5 — `libvmod-redis` on both families

Upstream's own `redhat/vmod-redis.spec` calls it `vmod-redis`, and the Varnish ecosystem's convention is the `vmod-` prefix. **The deviation is deliberate and the reason is collision avoidance.** A user with a Varnish repository enabled alongside this one must not be offered two different packages under one name, built against two different engines, with only the `Requires` to tell them apart. `vmod-dict` keeps its name because nobody else publishes it; `redis` is a name several people publish.

### R6 — lanes

`vinyl-release` only, both targets. The trunk constraint is recorded above and in the manifest.

### R7 — the EL9 suite

**All 20 VTCs pass on AlmaLinux 9 with redis 6.2.20, no skips, TLS enabled.** Measured on 2026-07-29 in an `almalinux:9` container against a source-built Vinyl Cache 9.0.1. The concern was `enable-debug-command`, which upstream's runner only writes for redis ≥ 7.0.0, and which three VTCs need (`blocked-workers`, `command-execution-timeout`, `easy-command-timeout`). Redis 6.2 does not restrict `DEBUG` at all — the restriction and its opt-out both arrived in 7.0 — so the three cases run there without it.

Note the exact version: **AppStream ships redis 6.2.20**, not the 6.2.22 the brief carried. `repoquery` in an `almalinux:9` container, 2026-07-29.

### W0 — the rpmlint asymmetry and dict revision 2

One change, as the audit ruled. `rpmlint_overrides` in the overlay renders a reviewed `<rpm_name>.rpmlintrc` that `verify-rpm.sh` passes with `-f`; nothing declared renders no file and passes no `-f`. `verify-rpm.sh` runs an unfiltered informational pass first so every waived finding stays in the log, then the filtered gating pass, then asserts `0 errors, 0 warnings` rather than trusting an exit status that is non-zero only for errors.

dict's `summary-not-capitalized` is fixed rather than waived — it is a real finding. The four spelling findings and `invalid-license GPL-3.0-or-later` get filters with reasons: a `en_US` dictionary that predates this project's vocabulary, and a tool that predates SPDX.

dict goes to **package revision 2** and both targets record `evidence: pending` with the reason in words. `--require-releasable` is RED again, naming dict and redis. That is the gate working: it is what stops a recorded artifact digest outliving the bytes it described.

## The dict-output-unchanged proof, and why it is per commit

`DICT_PRE_PATCH_RECIPE_SHA256` pins dict's rendered recipe digests for both targets, and its comment carries both values:

| Stage | debian-13-amd64 | el9-x86_64 |
| --- | --- | --- |
| before the patch capability, and unchanged by it | `7e7055b8…4d1ba360` | `a64f74b8…1196e6dd` |
| after dict overlay revision 2 | `6f637e4b…9f3be1e1` | `a814b624…af141b65` |

The first row is the plan's Phase 3 requirement — "confirm the first generated VMOD's output unchanged" — and it held **as a byte comparison of the rendered tree**, not as an argument. It cost two deliberate choices to keep true:

- the RPM `Patch` block's explanatory comment is **rendered** rather than written into the template, so a patchless spec has no comment about patches in it;
- `@RPM_PATCHES@` sits on the line where the blank line between `Source0:` and `BuildRequires:` already was, so an empty render reproduces it exactly.

The generation *record* did move, and that is correct: it carries the generator's own source digest and the template digests, and both changed. The record describes the code that rendered the recipe. What must not move is the recipe.

Keeping the capability and dict's revision 2 in separate commits is what makes the claim checkable rather than asserted, and it is why they are separate commits.

## What the local containers proved

Two container images were built for this, each carrying a **source-built Vinyl Cache 9.0.1** from the release tarball the lane pins (`vinyl-cache-9.0.1.tgz`, sha256 `2e8ec67c…59bdeb17`). Source-level, not package-level: it proves the patch and the suite, and says nothing about a `.deb` or an `.rpm`.

| Check | debian:13 | almalinux:9 |
| --- | --- | --- |
| build-dep availability | libhiredis-dev 1.2.0, libev-dev 4.33, libssl-dev, autoconf-archive — all main | hiredis-devel 1.0.2 (EPEL), libev-devel 4.33 (CRB), openssl-devel 3.5.5, autoconf-archive 2019.01.06 (AppStream) |
| redis for the suite | 8.0.2 (`redis-server`, `redis-tools`) | 6.2.20 (`redis`) |
| patch applies at `--fuzz=0` | yes, 4 files | yes, 4 files |
| `autoreconf -fi` after the exports | OK | OK (autoconf 2.69, automake 1.16.2) |
| `configure` TLS | `enabled` | `enabled` |
| `make -j$(nproc)` | OK, no generator race | OK |
| **ported VTC suite** | **20/20 passed, 0 failed** | **20/20 passed, 0 failed** |

The lane code itself was exercised too, in containers, against the real inputs:

- **`source.sh`, derived path**: derived the archive from the tag, digest matched the pin, `ls-remote` confirmed the tag still peels to the recorded commit, `AC_INIT` cross-checked as 23.1. All four checks in one run.
- **archive determinism**: derived twice, byte-identical, 221100 bytes.
- **`generate.sh`**: both targets rendered; the Debian tree carries `debian/patches/` and its `series`; `lane/scripts/` carries the five staged scripts and `lane/tests/` the 20 VTCs.
- **`dpkg-source -b`** on the real generated tree: applied the patch, built `libvmod-redis_23.1-1.dsc` and the `.debian.tar.xz`.
- **`--omit-patch`**: `dpkg-source` refused, naming the missing file.
- **the fixture contract end to end**: `vtc_install_packages`, `vtc_stage_fixtures` (6 files staged from `src/tests/{runner.sh assets/*}`, `assets/` preserved as a subdirectory) and `vtc_run_suite` driving upstream's runner — the real `scripts/ci/lib/vtc-suite.sh`, with the real values from `lane-env`.

### What did NOT run locally, and why

- **No package was built.** The lane images are digest-pinned and this host is arm64; `run.sh` documents why it will not pass `--platform` against a digest. The Mock and pbuilder stages need x86_64.
- **No installed-package verification.** It needs x86_64 packages on an x86_64 package manager.
- **`rpmlint` on the redis RPM.** Its `License:` field is `BSD-2-Clause AND BSD-3-Clause AND LicenseRef-Public-Domain`, and rpmlint 1.11 predates SPDX. `invalid-license` is a live possibility, and `rpmlint_overrides.filters` is deliberately **empty** rather than pre-populated: a filter for a finding that does not fire produces its own complaint, and guessing at a waiver is exactly the habit the mechanism exists to replace.

## What Wave 2 must prove live

1. **Both redis packages build**, on both targets, through the consolidated lane.
2. **The full 20-VTC suite passes against the INSTALLED package**, on both targets. The local runs were source-built; the subject has to be the packaged `.so` reached through `-p vmod_path`.
3. **rpmlint on redis.** If `invalid-license` fires, it gets a reviewed filter with its reason and redis goes to package revision 2. If anything else fires, it is a finding about the templates or the overlay first.
4. **lintian on redis.** Upstream's `dist_doc_DATA` installs `LICENSE` into the documentation directory, which raises `extra-license-file`. Checked in a `debian:13` container: severity **info**, so `--fail-on error,warning` does not fail on it. Confirm rather than assume.
5. **dict's revision-2 evidence, re-recorded**, and `--require-releasable` back to green.
6. **Verification case 10, both demonstrations.** `inject=patch_omission` must produce `failed_package_build` on both redis rows while cachetag's six and dict's four complete. And the recorded `recipe_sha256` for redis must be the value this branch renders — that is the evidence half, and it is what makes a substituted patch impossible to hide.
7. **The two new isolation injections**, `redis_source` and `redis_build`, each leaving the other two VMODs' ten rows green.
8. **The ledger at 19/18**, and `engine/vinyl-release/debian-13-amd64` blocking three consumers rather than two under `inject=engine_build`.

## Verification

| Check | Result |
| --- | --- |
| `vmod_recipe.py selftest` | **201/201**, exit 0 |
| `ci_matrix.py selftest` | **230/230**, exit 0, then 201/201 for the generator |
| `release_tool.py selftest` | **149/149**, exit 0 |
| `release_tool.py validate` | OK, 10 manifests |
| `release_tool.py validate --require-releasable` | **RED, and correctly so** — 28 errors, dict and redis both `pending`, cachetag blamed for none of them |
| `ci_matrix.py check-catalog` | OK, **3 VMODs** |
| `ci_matrix.py ledger --tier ci` | **19 rows, 18 selected** |
| containerised `actionlint`, all workflows | clean |
| containerised `shellcheck --severity=error`, every `.sh` under `scripts/` and `recipes/` | clean |
| `git diff main -- recipes/debian-13/ recipes/el9/ scripts/ci/debian13/ scripts/ci/el9/` | **empty** — the cachetag lane is untouched this wave |

Three `release_tool.py` self-tests had to move with this, and the reason is worth recording: they were pinned to a particular VMOD's mid-flight evidence state, which is a thing that changes in both directions. One had already taken the process down with a KeyError when Wave B recorded dict's evidence. They now assert the RULE — pending needs a reason, recorded must not carry one, releasability corresponds to the pending set, and no recorded VMOD is blamed for the block — so the next VMOD to enter scope breaks the release gate and not the self-test.

`scripts/ci/lib/` changed in exactly one file, `vtc-suite.sh`, and only for the fixture contract: a fixture root and a driver argument, plus `vtc_install_packages`. Its cachetag callers are `verify-deb.sh` and `verify-rpm.sh`, both of which changed with it; the cachetag lanes' own suites do not use this file (Wave 0's `debug=+vclrel` carry-forward, still five files, still open).

## Open questions for the audit

1. **The patch is pinned to one tag, and nothing schedules its re-review.** A version bump has to re-derive it, review it, and record a new digest. Nothing in the registry expresses "this patch was reviewed against commit X" other than the patch living beside a manifest that names X. Is a `reviewed_against` field worth its validation rules, or is the coupling adequate as it stands?
2. **`rpmlint_overrides.filters: []` on redis.** Left empty deliberately, so Wave 2 measures rather than guesses. If `invalid-license` fires it costs redis a package revision before it has ever been released. Would it be better to record the filter now on the strength of the cachetag lane already carrying the identical waiver for the identical reason?
3. **The behaviour suite now depends on a distribution package outside the selected set.** `redis-server` 8.0.2 on Debian and 6.2.20 on EL9 are different enough that "the suite passes" means two different things. Both pass today. Should the versions be recorded as evidence in `registry/targets/`, the way build dependencies are, so a distribution's redis moving becomes a visible change rather than a silent one?
4. **`--omit-patch` is the only injection that acts on exactly one VMOD by construction** rather than by table choice, because redis is the only patched VMOD. The table says so with a comment. Is that the right place for the constraint, or should the injection be refused at expansion time when the target VMOD declares no patches?
