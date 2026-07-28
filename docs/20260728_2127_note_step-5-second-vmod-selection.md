# Step 5: selecting the second VMOD

Date: 2026-07-28

Status: Decided

Decision owner: repository maintainer, selection delegated (see below)

Related:

- [Roadmap: outstanding packaging work](20260728_0916_roadmap_outstanding-packaging-work.md), Step 5
- [Varnish downstream VMOD packaging plan](20260726_0824_plan_varnish-downstream-vmod-packaging.md), refreshed in the same step
- [vmod-packager patterns and recipe generation](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md)
- [Third-party VMOD compatibility survey](20260726_1858_plan_vmod-survey.md) and its [first sweep report](20260726_2014_report_vmod-survey-first-sweep.md)
- [Survey rerun integrity](20260727_0830_note_survey-rerun-integrity.md)
- [`SCOPE.md`](../SCOPE.md) §"The package set"

## The delegation

`SCOPE.md` requires an explicit maintainer decision to add a package. For this one the maintainer delegated the choice itself:

> take its recommended second-VMOD and run with it. If it doesn't work out, try the next on the list. What matters is adding a second VMOD, not which VMOD it is.

So the decision recorded in `SCOPE.md` is a maintainer decision to add a second VMOD, with the identity of that VMOD selected by the coordinating agent under that delegation, from a researched shortlist. The delegation also carries fallback authority: if the selected VMOD does not work out in practice, moving to the next candidate on the recorded ordering does not need a fresh decision. It does need a `SCOPE.md` update and a note like this one, because the recorded selection must always match what is actually built.

The delegation is deliberately narrow. It covers *which* VMOD, not *how many* and not *which engine*. Adding a third VMOD, or adding Varnish as a second engine, remains an explicit maintainer decision.

## Selected: `vmod-dict`

| Field | Value |
| --- | --- |
| Upstream | `https://git.gnu.org.ua/vmod-dict.git` |
| Tag | `v1.7` (PGP-signed annotated tag) |
| Commit | `784584d272894a39cf995377618aad551a196424` |
| Version | `1.7` (`AC_INIT`) |
| Maintainer | Sergey Poznyakoff |
| Build system | Autotools (`AC_PREREQ` 2.71, `AM_INIT_AUTOMAKE` 1.16.5, `LT_INIT`) |
| Engine detection | `ACVMOD_VINYLAPI([6.0.0],[9.0.0])` via the `acvmod` submodule |
| Runtime dependencies | none beyond the engine |
| Build dependencies | pkg-config, python3 >= 3.5, rst2man, libtool |
| Upstream packaging | none (no `debian/`, no spec) |
| `$ABI` | undeclared, so strict by default |
| Survey verdict | pass on both the varnish9 and vinyl9 lanes |
| Last upstream commit | 2026-03-25, which is the `v1.7` tag |

What it does: loads a keyword-to-value dictionary file at `vcl_init` and exposes `dict.lookup()`, `dict.ci()`, `dict.collisions()`, and `dict.clear()`. It is a small, boring, self-contained VMOD, which is exactly the point — Step 6 is a tracer bullet for generated recipes, not a test of how much VMOD complexity the pipeline can absorb.

## Why this one: the five criteria

The survey produced 36 dual-compatible results, but those are branch-HEAD verdicts. Applying the project's own rules to them eliminates almost all of them. Five criteria, in the order they did the eliminating.

**1. A current release tag whose version the source agrees with.** `SCOPE.md` §"Source and release policy" requires release builds to identify source by a human-meaningful release tag or version, with the resolved commit and archive digest recorded as evidence. That is a hard filter and it removed most of the green set: `libvmod-curl`'s newest tag (1.0.4) sits 13 commits behind changes including Varnish 6.5 VSB adaptations, so the tag will not build on 9.x at all; `libvmod-uuid` v1.9 is 11 commits behind `vmod_priv` signature changes, and even Varnish Software pins a commit rather than that tag; `libvmod-dynamic` is 193 commits past v2.8.0, `libvmod-geoip2` 59 past v1.3.0; `kenshaw/libvmod-dns`, `hashids`, `dyncounters`, and `maxminddb` have no usable tags at all. `vmod-dict` v1.7 passes cleanly: the signed tag peels to `784584d2…`, `AC_INIT` says 1.7, `NEWS` says 1.7, and the peeled commit is the same commit the survey actually swept (`survey/data/triage.json`, `dict.head_commit`). Tag, version, commit, and evidence all agree without anybody deciding what to believe.

**2. Builds on both engines with no shim.** Exactly one family in the green set does this: the gnu.org.ua VMODs (`vmod-dict`, `vmod-remoteip`, `vmod-tbf`), whose `acvmod` macros probe `vinylapi` first, fall back to `varnishapi`, and derive `vmoddir`, the daemon name, and the test driver from whichever flavour they found. Every other candidate is engine-native in its `configure.ac`. This matters more than it looks: a candidate needing a naming shim would make the Step 6 tracer bullet a test of the shim rather than of recipe generation, and would drag Step 7's exception machinery forward into Step 6.

**3. A test suite that can be made to exercise the installed package.** Step 5's exit gate demands an agreed behaviour-verification path, and load-only verification is explicitly insufficient. `vmod-dict` has two Autotest groups (`tests/ci.at`, `tests/cs.at`) driven through `ACVMOD_AT_VTEST`. The generated VTC hardcodes `import dict from "$abs_top_builddir/src/.libs/libvmod_dict.so"` and a `${vmod_topsrc}/tests/num.dict` fixture path, so it is build-tree-bound as generated — but both bindings are mechanical to remove. The port is: plain `import dict;` resolved through `-p vmod_path=$VINYL_VMODDIR`, with `num.dict` staged into the container. Upstream's expected values become the oracle. This is the same shape as cachetag's existing `stage-vtc-suite.sh` path, so Step 6 reuses the harness rather than inventing one. Candidates were eliminated here too: `digest`, `geoip2`, and `gcrypt` bind their VTCs to `${vmod_topbuild}` in ways that are not mechanical to unpick.

**4. No dependencies to package.** `vmod-dict` needs nothing beyond the engine and standard build tools. Step 7 exists specifically to prove one controlled exception such as an extra dependency; pulling that into Step 6 would confuse the two. `libvmod-uuid` would have needed `libossp-uuid` from EPEL, and `libvmod-redis` needs hiredis and libev — both are Step 7 shapes, not Step 6 shapes.

**5. Credible provenance and maintenance.** Signed annotated tags, a maintained `NEWS`, a single identifiable maintainer, and a release three months old rather than three years. Provenance is not a tiebreaker here; it is the thing the registry exists to record, and a candidate whose releases cannot be pinned confidently is not usable regardless of its technical merit.

Everything eliminated is recorded above and in the runner-up ordering below rather than silently dropped, because the next person to pick a VMOD should not repeat the search.

## Runner-up ordering and fallback authority

If `vmod-dict` does not work out during Step 6, take these in order under the maintainer's fallback delegation:

1. **`vmod-tbf` 2.8** — token-bucket rate limiting, same gnu.org.ua family, so criteria 2 and 4 hold identically and it has a richer suite (six Autotest groups plus a standalone `.vtc`). Blocked today by criterion 1; see the ruling below. It also has a second, smaller problem: rate-limit assertions are time-dependent, which is a flake risk in CI containers.
2. **`vmod-querystring` 2.0.4** — technically the strongest suite of the lot (17 VTCs that already run against an installed package with the same `vmod_path` pattern cachetag uses), zero dependencies, `$ABI vrt`, and a genuinely published release tarball: `https://git.sr.ht/~dridi/vmod-querystring/refs/download/vmod-querystring-2.0.4/vmod-querystring-2.0.4.tar.gz`, 398,676 bytes, sha256 `965cd64edcb1c46dd88573b6e5da52b93cf21bbf0e482acff72f47d82bf866ed`, whose sha512 matches the one `varnish/pkg-varnish-cache@9.0`'s `pkg.env` records. Blocked by criterion 2: it is Varnish-native (`VARNISH_PREREQ`, `VARNISH_VMODS`, and an `m4_pattern_forbid(^_?VARNISH[A-Z_]+$)` in `vinyl-legacy.m4`), and its vinyl9 survey pass used a survey-local naming shim. Selecting it means either accepting a Step 7-class shim exception early, or opening a Varnish lane first — both larger decisions than a fallback should make on its own. If it comes to this, escalate rather than proceeding under the delegation.

Not on the fallback list, and why: `libvmod-selector` (gitlab.com/uplex) has the best Vinyl-native suite in the whole survey — 16 VTCs plus a C unit test exercising VSC counters through `VINYL_COUNTERS` — and is very actively maintained. It is disqualified by criterion 1 and the disqualification is structural: its newest tag is v2.6.0 from 2021, predating Vinyl entirely, and its branch head declares `AC_INIT([...],[trunk])`, so there is no human-meaningful release version to record. The whole uplex family shares this. Nothing short of an upstream release changes that, and asking upstream for one is out of scope.

## Rulings on the open questions

**1. Does the `vmod-tbf` version mismatch disqualify it? Yes, for now.** The `v2.8` signed tag (2026-03-22) peels to `42da01e18bd67d32bd891c87c166e967d47dee3c`, but the tree at that commit declares `AC_INIT` 2.7 and the newest `NEWS` entry is 2.7. There is no clean way to satisfy the version cross-check: recording 2.8 contradicts the source, and recording 2.7 contradicts the tag. `SCOPE.md`'s source policy exists precisely to make this kind of ambiguity fail loudly, and the correct response to a loud failure is not to pick whichever value makes the check pass. If tbf is later needed, the two acceptable routes are to record 2.7 with the discrepancy documented in the manifest as a deliberate, reviewed exception, or to ask upstream which is authoritative. Neither is expensive; neither is worth doing before it is needed.

**2. Zero shim, or the better test suite? Zero shim.** `vmod-querystring` has the better suite by a wide margin and `libvmod-selector` has a better one still. Both were passed over for candidates whose suites need porting work. The reasoning: a shim is a permanent, load-bearing piece of this project's infrastructure that would be introduced to serve a tracer bullet, and it would then need its own verification, its own failure modes understood, and its own maintenance against two moving engines. Porting two Autotest groups to VTC is a one-time, bounded, reviewable task whose output is inspectable test files. Prefer the bounded one-time cost over the unbounded ongoing one. The survey's own history supports this: the survey harness's shim (`SHIM_API_VERSION` in `survey/harness/pins.env`) already masked a real defect once, as [the survey rerun integrity note](20260727_0830_note_survey-rerun-integrity.md) records.

**3. Varnish lane now, or later? Step 8 at the earliest, by explicit `SCOPE.md` decision then.** Vinyl engine artifacts already exist for both targets and are consumed by cachetag's rows; there is no Varnish anything in `registry/`. A Varnish lane means a new engine class, new schema, a new ABI-expression generator path, and a new transaction matrix — a project, not a tracer bullet. And the `acvmod` family is flavour-agnostic, so choosing Vinyl first costs nothing later: the same `vmod-dict` source builds on Varnish unmodified whenever that lane opens. The verified Varnish package source and ABI model are recorded now, in the [refreshed downstream plan](20260726_0824_plan_varnish-downstream-vmod-packaging.md), so the eventual decision starts from facts rather than from the 2026-07-26 assumptions, which were wrong in five separate places.

**4. Which engine lanes does `vmod-dict` get? `vinyl-release` only. `vinyl-trunk-pinned` is excluded, and the reason is a real defect, not caution.** Vinyl's trunk `AC_INIT` still says `trunk` — see `recipes/debian-13/pins.env` lines 154-157, which record exactly that in the snapshot-version convention comment — so the trunk build's `vinylapi.pc` carries `Version: trunk`. `acvmod.m4` splits the pkg-config modversion on `.` and does configure-time arithmetic on the major component, which fails outright on the literal string `trunk`. `vinyl-release` (9.0.1) is unaffected because it has a numeric version.

This did not show up in the survey because the survey harness masks it: `survey/harness/pins.env` rewrites every `.pc` file's `Version` to `SHIM_API_VERSION` on both lanes, deliberately, so that a version-string artifact of the trunk deb could not register as lane divergence. That was the right call for the survey's question and the wrong input for this one. It is worth noting as a general caution — the survey's green verdicts are verdicts about a shimmed environment, and any of them may hide something the production lane will not.

The fix is not ours to make. Making trunk work would mean either editing `../vinyl-cache` to emit a numeric snapshot version, which the workspace rules forbid from this repository, or teaching `acvmod` to tolerate non-numeric versions, which is an upstream change to somebody else's macro. Neither belongs in Step 6. Revisit when Vinyl trunk emits a numeric snapshot version; at that point adding the lane is a manifest edit.

**Recorded constraint for the future:** any VMOD whose configure does arithmetic on the engine's `.pc` version will fail against Vinyl trunk for as long as trunk's `AC_INIT` says `trunk`. This is not specific to `acvmod` — the pattern is common. Assume a trunk-pinned lane is unavailable for a new Autotools VMOD until proven otherwise, and prove it against the real pins rather than against the survey harness.

**5. How does a non-GitHub upstream fit the manifest? Scoped into Step 6.** `tools/ci_matrix.py`'s `REPOSITORY_RE` expects a GitHub `owner/name` shape and `vmod-package.yml` checks source out with `actions/checkout`. `vmod-dict` is on `git.gnu.org.ua`. This is not a reason to prefer a GitHub-hosted candidate: every viable candidate except `libvmod-redis` is off GitHub, so the schema needs this regardless of which one is selected, and choosing a worse VMOD to avoid a schema change would be the tail wagging the dog. Step 6 adds a host or clone-URL field to `vmod-ci/v1` and a non-`actions/checkout` path in the per-VMOD workflow. Recorded here so Step 6 does not discover it as a surprise.

## Source archive derivation

### Superseded (corrected 2026-07-28, later the same day)

**What this section said, and why it was believed:** "No upstream release tarball was located for `vmod-dict`, so the source archive is derived deterministically from the tag." The Step 5 research swept the repository, the tags, and the survey data, and found no published archive. It did not read `src/vmod_dict.vcc`.

**What Step 6 Wave A1 found:** the tarball exists. `src/vmod_dict.vcc`'s DOWNLOADS section names `https://download.gnu.org.ua/release/vmod-dict`, and that directory carries `vmod-dict-1.7.tar.gz` with a detached PGP signature, both published 2026-03-25 — the same day as the tag. 414,559 bytes, sha256 `eb2a86a780ba9628106dbe858d17ec4589ad6dcb70c6ad53decb5d32824e098c`, signature verified good against Sergey Poznyakoff's key as published on `puszcza.gnu.org.ua`. It is a complete `make dist` archive.

**Why the correction changes the decision rather than only the record:** the tag's tree carries no `configure`, so a derived archive must be bootstrapped, and `configure.ac` declares `AC_PREREQ([2.71])` with `AM_INIT_AUTOMAKE([1.16.5 …])`. AlmaLinux 9 ships autoconf 2.69 and automake 1.16.2. The derived archive therefore **cannot build on `el9-x86_64`**, one of the two selected targets, and the published tarball is not merely the nicer input but the only workable one.

The selected input is now the published tarball: `recipes/vmods/overlays/dict/overlay.yml` declares `source.archive.method: upstream-release`, and the manifest pins that digest. Full evidence, including the container runs, is in [the Wave A1 note](20260728_2216_note_step-6-wave-a1-recipe-generator.md).

**Status of the two bullets below: moot for `vmod-dict`.** The release tarball vendors the `acvmod` macros directly (`acvmod/acvmod.m4`, `gencl`, `testsuite.inc`, `top.am` are all inside it), so no submodule is fetched and no `git://` URL is ever contacted on the selected path. They are kept because they are correct about the *derived* path, which remains implemented and pinned in `scripts/ci/vmod-source-archive.sh` as the recorded fallback, and because the next tag-only VMOD will meet both.

### The derived path (retained, not selected)

Where an upstream publishes only a tag, the source archive is derived deterministically from it, as `SCOPE.md` permits and as the cachetag lane already does.

Two things such a derivation must handle, both of which `vmod-dict` exhibits:

- **The `acvmod` submodule.** `vmod-dict` carries its Autoconf macros as a submodule pinned at `4fba6604d1d1e586274376a20841be0966bf7df3`. The archive must include it, and the recorded digest must cover it, or the build will fail at `autoreconf` in a way that looks like a toolchain problem.
- **The submodule URL is `git://`.** `.gitmodules` declares a `git://` clone URL. That protocol is commonly blocked and is unauthenticated, so the checkout needs a `git config url."https://…".insteadOf "git://…"` override. Configure it explicitly rather than relying on any ambient host or runner configuration, so the build behaves the same everywhere.

Neither is a defect in the VMOD; both are the kind of thing that costs an hour if discovered during implementation and five minutes if written down first. Both were handled, and the derived archive reproduces at sha256 `499f48cbcf5a961633f053778403b95658f22abeb72849d3da13f9ca35c893e4`.

## Held for Step 7

`libvmod-redis` (`github.com/carlosabalde/libvmod-redis`) is the held candidate for Step 7's controlled exception. It has annotated `9.0-23.0` and `9.0-23.1` tags with HEAD only 12 cosmetic commits ahead, it is actively maintained, Varnish Software already packages `9.0-23.0`, and it is the one viable candidate that is on GitHub. It exercises the exception the roadmap asks for: extra dependencies (hiredis, libev) and a strict `$ABI`.

The caveat to settle before Step 7 commits to it: its 20 VTCs need live Redis or Valkey fixtures, some with TLS. So its behaviour verification needs either a Redis container sidecar in the harness or a defined minimal smoke that does not need one. Decide which before selecting it, not after — an exception candidate whose verification path is undefined would repeat the mistake Step 5's exit gate is designed to prevent.

## What Step 6 inherits

- Manifest: a `registry/vmods/dict.yml` on `vmod-ci/v1`, extended with the non-GitHub source fields.
- Lanes: `kind: package`, `source: release`, `engine: vinyl-release`, targets `debian-13-amd64` and `el9-x86_64`.
- Recipes: generated, per the [recipe-generation plan](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md). Upstream provides no *packaging*, which is the normal case; that is unaffected by the source-archive correction above.
- Behaviour gate: `tests/ci.at` and `tests/cs.at` ported to VTC, `import dict;` resolved through `-p vmod_path`, `num.dict` staged, run against the installed package in the existing fresh-container harness, upstream's expected values as the oracle.
- ~~Archive: derived from tag `v1.7` including the `acvmod` submodule, with the `git://` override configured.~~ **Corrected 2026-07-28:** the archive is upstream's published, signed release tarball, digest-pinned. See "Source archive derivation" above.
