# Varnish Cache downstream VMOD packaging plan

Date: 2026-07-26

Refreshed: 2026-07-28 (roadmap Step 5)

Status: Draft

Support floor (decided 2026-07-26): nothing before Vinyl Cache 9.0 / Varnish Cache 9.0 is supported. The Varnish 6.0 LTS branch and every pre-9.0 Varnish branch are permanently out of scope; the first Varnish lane opens with the Varnish 9.0 branch.

When this lane opens: **Step 8 of the [outstanding-packaging-work roadmap](20260728_0916_roadmap_outstanding-packaging-work.md) at the earliest, and only by an explicit `SCOPE.md` decision then.** Step 5's second-VMOD selection is a Vinyl-lane selection and does not imply a second engine; see [`SCOPE.md`](../SCOPE.md) §"The package set" and [the Step 5 selection note](20260728_2127_note_step-5-second-vmod-selection.md). Everything below describes a lane that has not been authorized yet. What Step 5 did authorize is the factual content of this refresh: the engine package source, the ABI dependency model, and the record that the first downstream lane is Vinyl, not Varnish.

Related: [binary packaging and distribution plan](../../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md) (the Vinyl cohort plan), `registry/README.md`, `registry/distro-native/`.

## Superseded assumptions (verified 2026-07-28)

This plan was written on 2026-07-26 against `varnishcache/pkg-varnish-cache`, which turned out to be the previous generation of the packaging source. The body below has been corrected in place; this block preserves what the 2026-07-26 draft believed and why each belief changed, because a plan that silently rewrites its own history is useless as a diagnostic record. Every correction was verified live on 2026-07-28 against `github.com/varnish/pkg-varnish-cache` branch `9.0` and the published `packages.varnish-software.com` repositories.

| 2026-07-26 belief | 2026-07-28 finding | Why it changed |
| --- | --- | --- |
| Packaging source is `varnishcache/pkg-varnish-cache` | It is `varnish/pkg-varnish-cache` branch `9.0` (last push 2026-07-26) | The `varnishcache` org tops out at `varnish-8.0.0` / pkg branch `8.0`. The 9.x packaging moved with the project. |
| Upstream `.deb` provides both `varnishd-abi-<hash>` and `varnishd-vrt (= MAJOR.MINOR)` | The deb provides `varnishd-abi-<hash>` only | On the `9.0` branch `debian/rules` defines only `varnishd:ABI`. `debian/control` still substitutes `${varnishd:VRT}`, but nothing ever defines it, so no `varnishd-vrt` provide is emitted. A `$ABI vrt` dependency is **inexpressible** on the deb lane today. |
| Upstream RPM declares no ABI provides, so the lane needs an exact version pin | The RPM declares **both** `varnishd(abi)` and `varnishd(vrt)` | `redhat/find-provides` on the `9.0` branch injects both. Live EL9 `varnish 9.0.3-3.el9` carries them. The RPM lane is now the *stronger* of the two — the inverse of what this plan assumed. |
| The lane opens when upstream publishes 9.0 packages; resolve the repo name from varnish-cache.org | Packages already exist; varnish-cache.org now redirects to Vinyl | The lane is no longer blocked on upstream. It is blocked on our own scope decision (Step 8). |
| `varnishcache/varnish<NN>` packagecloud per-branch repos are the primary lane | Those repos do not carry 9.x; Varnish Software publishes **one multi-branch repo per distro** | Branch selection becomes an apt-pin / version-hold problem inside a single repo, not a repository-selection problem. |
| We would be the first downstream VMOD provider for Varnish 9 | Varnish Software already publishes 11 VMOD packages (+2 in-repo) using precisely the centrally-generated-recipe architecture we are proposing | Our lane must state what it adds and must not collide with their package names. See "What Varnish Software already publishes". |
| `$ABI vrt` saves patch-release rebuilds | True in principle, not expressible on the deb lane today | No `varnishd-vrt` provide exists to depend on; upstream's own VMOD packages pin `varnish (= ${binary:Version})` regardless. |
| Vinyl must converge *toward* the Varnish downstream model | Vinyl already satisfies the ABI-identity gate, and Varnish 9 is itself a Vinyl derivative | Varnish 9's libraries carry `LIBVINYLAPI_3.0`/`LIBVINYLAPI_3.1` symbol versions and varnish.org describes Varnish as "a Vinyl Cache distribution". Convergence is a naming-and-repository question, not an API question. |

Recorded reference pins from that verification, for whenever the lane opens: Debian `varnish 9.0.3-3~trixie`, EL9 `varnish 9.0.3-3.el9`, varnishd ABI hash `0a625649cd40af4b6c10be5e58a2e89a5e275baa`, VRT `23.1`. These are observations of a moving repository, not a commitment: re-resolve them at implementation time and fail loudly if they have moved.

## Executive recommendation

For Varnish Cache, do not replicate the Vinyl cohort model. Become a **downstream VMOD provider**: rely on the Varnish project's own binary packages for `varnishd` and its development files, and build, test, and publish only VMOD packages against them.

This is the standard model for third-party Varnish modules (varnish-modules, vmod-uuid, Varnish Software's commercial VMODs all work this way), and it is strictly less responsibility than the Vinyl cohort:

- Upstream owns the daemon: builds, security updates, service integration, and the daemon package repository. We never become a security-update channel for an internet-facing daemon on the Varnish side.
- We own only the VMOD packages: their ABI dependency correctness, their rebuild latency after upstream releases, and our own repository signing.

The same model is a *possible* end state for Vinyl Cache, not a settled one. The existing Vinyl cohort plan remains in force because Vinyl publishes no packages today; if the Vinyl project publishes runtime and development packages that meet the convergence gates below, migrating Vinyl lanes to this downstream model becomes an option worth evaluating per target. The 2026-07-28 refresh weakened this from an assumption to an option, because the cohort model currently expresses dependencies *more* precisely than the Varnish deb lane can — see "Vinyl convergence path".

## Why this works for Varnish: the ABI mechanism

These mechanics were verified against upstream's packaging repository ([`varnish/pkg-varnish-cache`](https://github.com/varnish/pkg-varnish-cache) branch `9.0` — `debian/control`, `debian/rules`, `redhat/find-provides`) and against the live published packages on `packages.varnish-software.com`, on 2026-07-28. The 2026-07-26 draft read the previous-generation `varnishcache/pkg-varnish-cache` repository, which tops out at the 8.0 branch; see "Superseded assumptions" above.

Every compiled VMOD is stamped at build time by `vmodtool.py` with a `VMOD_ABI_Version` string, and `varnishd` checks it at `vcl.load`. A mismatch is a clean, immediate refusal to load the VCL — the failure mode is loud, not silent corruption. The stamp has two modes, chosen by the `$ABI` declaration in the `.vcc`:

- **`$ABI strict`** (default): the stamp is the exact Varnish version and git revision. The binary loads only against the identical varnishd build, including across patch releases.
- **`$ABI vrt`**: the stamp is `VRT_MAJOR_VERSION.VRT_MINOR_VERSION` only. The binary survives any varnishd rebuild that preserves the VRT major version — in practice, patch releases within a branch.

VRT major is bumped in essentially every six-monthly Varnish release (March 15 / September 15). So even `$ABI vrt` means one rebuild per release **branch**; it only saves the per-patch-release rebuilds — where the package source expresses a VRT dependency at all, which the Varnish Software deb lane currently does not. There is no build-once-run-everywhere option. The model is out-of-tree kernel modules with a small matrix, and with the pre-9.0 support floor our matrix would be the supported 9.0+ branches only.

### How the dependency is expressed in packages

The virtual-provide names differ by package source, and a lane's dependency expression must match the varnish package source that lane targets. Verified live on 2026-07-28:

| Varnish package source | ABI provides | Dependency expression for a VMOD package |
| --- | --- | --- |
| Varnish Software `.deb` (`packages.varnish-software.com`, built from `varnish/pkg-varnish-cache@9.0`) | `varnishd-abi-<git revision>` **only** — plus `libvarnishapi1`. No `varnishd-vrt` provide exists: `debian/rules` defines only `varnishd:ABI`, while `debian/control` references an undefined `${varnishd:VRT}`. Live `varnish 9.0.3-3~trixie` provides `libvarnishapi1` and `varnishd-abi-0a625649cd40af4b6c10be5e58a2e89a5e275baa`. | `Depends: varnishd-abi-<hash>` (strict). A `$ABI vrt` VMOD **cannot** express its real compatibility here; it must fall back to `varnish (= <version>)` or to the same strict ABI provide. |
| Varnish Software RPM (`packages.varnish-software.com/varnish/el/...`, same repo, `redhat/find-provides`) | **both** `varnishd(abi)(x86-64) = <git revision>` and `varnishd(vrt)(x86-64) = <VRT major.minor>`. Live EL9 `varnish 9.0.3-3.el9` provides `varnishd(abi)(x86-64) = 0a625649cd40af4b6c10be5e58a2e89a5e275baa` and `varnishd(vrt)(x86-64) = 23.1`. | `Requires: varnishd(abi) = <hash>` (strict) or `Requires: varnishd(vrt) = 23.1` (vrt). Both are expressible. |
| Distribution archive packages (Debian/Ubuntu `varnish`, Fedora/EPEL `varnish`) | distro-specific naming, not upstream's; historically `varnishabi-strict-<hash>` on Debian | Resolve the exact provide names from the target release's own `varnish` package at implementation time. Do not carry a guess into tooling. |

Consequence worth stating plainly, because it inverts the 2026-07-26 draft: **the RPM lane is the stronger of the two.** It can express both ABI modes; the deb lane can express only strict. Any per-VMOD ABI-surface decision that assumes `$ABI vrt` buys reduced rebuild cadence is, on Debian, currently buying nothing.

The upstream `varnish-dev`/`varnish-devel` package ships the full header set (including private `cache/cache.h`-level headers), `vmodtool.py`, and `varnishapi.pc` (which supplies `$VMODDIR`), so the build is fully driven by the installed development package. This is the development-surface property the Vinyl convergence gates below demand — and which Vinyl already satisfies.

### A naming precedent worth noting

Varnish's own bundled and Varnish Software-published VMOD RPMs advertise a capability-style virtual provide of the shape `vmod(<name>)`. Resolve the exact published provide names from the target repository at implementation time rather than assuming any particular one. If this project ever needs a capability-style provide for a VMOD — as opposed to the ABI-identity provides above — that convention already exists in the ecosystem and is worth matching rather than inventing a parallel one. This is an observation, not a decision; nothing in the Vinyl lane emits such a provide today.

## Scope and first milestone

Two prerequisites, both outside this plan.

First, the scope decision. This lane is not authorized. It opens at roadmap Step 8 at the earliest and only through an explicit `SCOPE.md` amendment adding a second engine, with the additional build, test, publication, and maintenance responsibility that `SCOPE.md` §"Changing the scope" requires such an amendment to describe. Step 5 deliberately chose Vinyl as the first — and so far only — production lane for the second VMOD: Vinyl engine artifacts already exist for both targets, whereas a Varnish lane means a new engine class in the registry schema, a new ABI-expression generator path, and a new transaction-test matrix. That is not a tracer bullet.

Second, a VMOD that targets Varnish. Porting cachetag to Varnish is its own engineering effort (see the ABI-surface decision below); this plan defines how its packages are built and supported once it exists, and applies unchanged to any other VMOD we package for Varnish. Note that the VMOD selected at Step 5, `vmod-dict`, builds unmodified on both engines — its `acvmod` macros probe `vinylapi` first and fall back to `varnishapi` — so it is a viable first candidate for this lane too, without a port.

First milestone, mirroring the Vinyl plan's proving pair: one VMOD, built for the Varnish 9.0 branch from Varnish Software's published repositories, on Debian 13 amd64 and EL9 x86_64, published as a GitHub pre-release with compatibility metadata and checksums.

Supported varnish package sources, in order:

1. **Varnish Software's published repositories** — the primary lane, already live as of 2026-07-28. This is **one multi-branch repository per distribution**, not a repository per branch: `trixie main` carries varnish 8.0.0 through 9.0.3 side by side.
   - Debian/Ubuntu: `deb https://packages.varnish-software.com/varnish/$ID $VERSION_CODENAME main`, signing key `https://packages.varnish-software.com/varnish/varnish.pub.asc`. Verified: `dists/trixie/Release` dated 2026-07-14.
   - EL: `baseurl=https://packages.varnish-software.com/varnish/el/$releasever/$basearch`. Verified: `el/9/x86_64` repodata present.
   - **Consequence: selecting a branch is an apt-pin / version-hold problem, not a repository-selection problem.** A lane must record and actively hold an exact varnish version (`apt-mark hold` / an APT pin / a dnf `versionlock`), because a plain `apt upgrade` inside that one repository will otherwise walk the container forward to whatever the newest branch publishes. The build container must resolve to the manifest's exact version, and the resolved version must be recorded back into the manifest.
   - Reference pins observed 2026-07-28, to be re-resolved when the lane opens: `varnish 9.0.3-3~trixie`, `varnish 9.0.3-3.el9`, ABI `0a625649cd40af4b6c10be5e58a2e89a5e275baa`, VRT `23.1`.
2. **Distribution-provided varnish packages** (Debian/Ubuntu archive, EPEL) — a later, separate lane per the existing distro-native discipline: a VMOD built against Varnish Software's varnish-dev is not interchangeable with one built against Debian's varnish, even at the same upstream version.

## What Varnish Software already publishes

We would not be the first downstream VMOD provider for Varnish 9. Varnish Software already occupies that role, using almost exactly the architecture the [recipe-generation plan](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md) proposes: per-VMOD `debian/` and `redhat/` directories held centrally in `varnish/pkg-varnish-cache@9.0`, driven by a `pkg.env` of pinned upstream source URLs and sha512 digests, versioned `<varnish X.Y.Z>-<R>` so a VMOD's version tracks the daemon it was built against, with every VMOD package depending on `varnish (= <exact version>)`.

Published VMOD packages, verified live 2026-07-28: `varnish-modules`, `vmod-cfg`, `vmod-digest`, `vmod-fileserver`, `vmod-geoip2`, `vmod-jq`, `vmod-querystring`, `vmod-redis`, `vmod-reqwest`, `vmod-rers`, `vmod-uuid`. Two more have recipes in the repository but no published packages found: `varnish-otel`, `vmod-k8s-endpoint`.

Two rules follow.

- **Non-duplication.** If this lane opens, it must not publish a package under a name Varnish Software already publishes, and must not silently shadow one of their packages in a user's apt or dnf resolution. A user who has both repositories enabled must get a predictable answer. Where we want to package a VMOD they already ship, the options are: don't (theirs is fine and better-integrated), or ship under a distinct name with a distinct provide and document the choice. Pick one deliberately, per VMOD, and record it in the manifest.
- **State what we add.** Their model pins each VMOD to one exact daemon version and rebuilds the set together. What this project adds is not more VMOD packages for Varnish — it is per-package evidence: recorded source identity, clean-room build provenance, installed-package behavior suites, and transaction tests, all bound to an exact engine revision. If a proposed Varnish lane cannot state what evidence it adds beyond what Varnish Software already publishes, it should not open.

Their existence is also a useful validation signal: the centrally-generated-recipe architecture is not speculative, and the absence of upstream `debian/` or `rpm/` directories in a third-party VMOD is clearly not an obstacle to packaging it — it is the normal case that a downstream provider absorbs.

## ABI surface decision per VMOD

Record for every packaged VMOD whether it declares `$ABI vrt` or `$ABI strict`; the choice drives the dependency expression and the rebuild cadence.

- Target `$ABI vrt` where the VMOD can be expressed through the public VRT API (the way `vmod-purge` and `vmod-xkey` are). Rebuild once per branch; patch releases need only a load-verification run.
- Cachetag on Vinyl is `$ABI strict` and uses private APIs (`cache/cache.h`, object event subscription, `HSH_Kill`, `EXP_Reduce`, `struct objcore` fields). A Varnish port should attempt the public-API surface first, but plan for the likely outcome that it lands strict, at least initially. Strict means: rebuild and republish on every upstream patch release of every supported branch, including security releases, with the rebuild latency window documented below.

The port decision (vrt vs strict) is therefore not a packaging detail — it sets the ongoing operational cost of the whole lane, and should be an explicit design goal of the port.

## Registry integration

Extend the registry with a downstream lane rather than inventing a parallel structure. The existing `distro-native` schema already captures the essential shape — a compatibility claim bound to somebody else's exact package revision, with no cohort identity. Add:

```text
registry/varnish-downstream/<branch>/<target>.yml    e.g. varnish-downstream/90/debian-13-amd64.yml
```

Schema `varnish-downstream/v1`, modeled on `cachetag-distro-native/v1`, recording per lane:

- the VMOD version and source digest;
- the varnish branch, repository origin (the Varnish Software repository URL and suite, or the distro archive), and the exact varnish binary/development package versions installed at build time — the version matters more than the origin here, because one repository carries every branch;
- the VMOD's `$ABI` mode, the observed `VMOD_ABI_Version` stamp, VRT version, and (for strict) the varnishd ABI hash;
- which virtual provide or exact-version pin the package depends on (`abi_dependency_expression`), generated by `release_tool.py`, never hand-edited;
- build environment, artifact digests, and test results, as in the existing schemas.

No cohort identifier exists on this lane by design: the compatibility claim is "this VMOD package revision, against that varnish package revision." `release_tool.py metadata` grows a mode that emits the Depends/Requires expression from the manifest, keeping the no-hand-edited-ABI rule intact.

## Package mechanics

Same native-tooling discipline as the Vinyl plan (sbuild/pbuilder, Mock, lintian, rpmlint, dh_shlibdeps, clean containers, no nFPM/fpm), with these lane-specific rules:

- **Debian/Ubuntu**: `Build-Depends: varnish-dev` installed from the Varnish Software repository with the manifest's exact version pinned or held. `Depends: ${shlibs:Depends}, ${misc:Depends}` plus the generated ABI expression, which on this lane is `varnishd-abi-<hash>` — there is no `varnishd-vrt` provide to depend on, so a `$ABI vrt` VMOD gets either the strict provide or an exact `varnish (= <version>)` pin, and the manifest records which and why.
- **EL-family RPM**: decide per lane whether the target is Varnish Software RPMs (which do declare both `varnishd(abi)` and `varnishd(vrt)`) or EPEL/distro RPMs (different naming, resolve at implementation time). Do not ship one RPM claiming both sources' provides.
- Build in a container that has **only** the varnish repository enabled *and the manifest's version pinned within it*, so the resolved varnish-dev version is exactly the manifest's, and record the resolved versions back into the manifest. Enabling the repository is not sufficient: it is a multi-branch repository and will otherwise resolve to its newest branch.
- Installed-package smoke per target: install upstream varnish + our VMOD, confirm the `.so` landed in `pkg-config --variable=vmoddir varnishapi`, compile a VCL importing it, run the behavioral smoke, uninstall cleanly.
- Upgrade transactions: smaller matrix than the cohort model but the same class of test. Verify that when upstream publishes a varnish our VMOD is not yet rebuilt for, `apt upgrade` holds varnish back rather than removing the VMOD, and document what `apt full-upgrade` / `dnf --allowerasing` do. Treat resolver behavior as a test result, not an assumption.

## Rebuild and response model

The recurring obligations — this is the entire steady-state cost of the lane:

| Upstream event | `$ABI vrt` VMOD (RPM lane) | `$ABI vrt` VMOD (deb lane, today) | `$ABI strict` VMOD |
| --- | --- | --- | --- |
| New six-monthly branch (Mar 15 / Sep 15) | new lane: build, test, publish | new lane: build, test, publish | new lane: build, test, publish |
| Patch release within a branch | load-verification run only | rebuild, retest, republish | rebuild, retest, republish |
| Security release within a branch | load-verification run, expedited | expedited rebuild, retest, republish | expedited rebuild, retest, republish |
| Branch leaves upstream support | retire lane per retention policy | retire lane per retention policy | retire lane per retention policy |

The corrected middle column is the practical finding of the 2026-07-28 verification. The "`$ABI vrt` saves the per-patch-release rebuild" argument holds on the RPM lane, where `varnishd(vrt)` exists to depend on. **It does not hold on the Debian lane today**, because no `varnishd-vrt` provide is emitted; a deb-lane VMOD has nothing weaker than the strict ABI hash to depend on, and Varnish Software's own VMOD packages sidestep the question by pinning `varnish (= ${binary:Version})` unconditionally. So a `$ABI vrt` port reduces rebuild cadence on EL and not on Debian, and a lane that spans both targets pays the strict cadence regardless. Revisit if upstream ever defines `${varnishd:VRT}` — `debian/control` already references it, so the fix upstream is small.

Automate detection: watch upstream release announcements and poll the Varnish Software repository metadata for new varnish package versions; a new version opens a rebuild task automatically. Because it is one multi-branch repository, the poller must filter by branch itself rather than relying on the repository to scope it. The security posture to document publicly: **upstream fixes varnishd; our obligation is bounded rebuild latency for strict-ABI VMODs, during which the package resolver holds the varnish upgrade back rather than breaking a running configuration.** State a target latency (e.g. rebuild within N days of an upstream security release) only when it can be staffed, exactly as the Vinyl plan requires for its SLA.

## Vinyl convergence path

The 2026-07-26 draft had this relationship backwards, and the correction matters for how the whole plan reads.

Varnish 9 is a Vinyl Cache derivative, not a separate lineage that Vinyl might someday converge toward. Varnish 9's own libraries carry `LIBVINYLAPI_3.0` and `LIBVINYLAPI_3.1` symbol versions, and varnish.org describes Varnish as "a Vinyl Cache distribution". On the API surface there is nothing to converge: the two engines expose the same library, and the gnu.org.ua `acvmod` family of VMODs builds against either by probing `vinylapi` first and falling back to `varnishapi`, with no source change.

What differs is packaging identity and repository provenance — package names, provide names, `.pc` file names, `vmoddir` locations, and who publishes and supports the daemon. **Convergence is a naming-and-repository question, not an API question.** A "downstream Varnish lane" is therefore best understood as building the same VMODs against a differently-packaged build of substantially the same engine, published by somebody else.

Note also that gate 3 below — usable ABI identity on the runtime package — is a gate **Vinyl already satisfies and the Varnish deb lane does not**. Vinyl's runtime package carries `vinyld-abi-<hash>` and `vinyld-cohort-<id>` provides, generated from the manifests. The Varnish Software deb carries only `varnishd-abi-<hash>` and cannot express a VRT dependency at all. Migrating a Vinyl target to a downstream model would, on Debian, be a step *down* in expressible dependency precision. That is not a reason never to do it, but it is a reason the trigger below is not by itself sufficient.

The trigger: the Vinyl project begins publishing official binary packages. The gates before a Vinyl target migrates from the cohort model to this downstream model — all of them, per target:

1. Upstream publishes both runtime and development packages for the target, from a maintained repository with a stated support/security policy.
2. The development package exposes the full surface our VMODs need: private headers (`cache/cache.h` et al. for strict-ABI VMODs), `vinylapi.pc` with `vmoddir`, VMOD/VSC generation tools.
3. The runtime package carries usable ABI identity: a strict-ABI virtual provide (`vinyld-abi-<hash>` or equivalent) regenerated on every rebuild, or failing that we fall back to exact package-version pins. This project's own Vinyl packages already meet this gate.
4. Our VMOD builds against the installed upstream development package in a clean container and passes the installed-package behavior suite against the upstream runtime package.
5. Upgrade-transaction tests against the upstream repository show the resolver holds an incompatible Vinyl upgrade back rather than removing the VMOD.

When the gates pass for a target, that target's lane moves from `registry/cohorts/` + `registry/targets/` to a `vinyl-downstream` lane with the same schema as `varnish-downstream`; the cohort machinery is retained only for targets upstream doesn't cover and wound down when none remain. The registry is the constant across both models — the same manifests, tooling, and no-hand-edited-metadata rule describe a cohort build and a downstream build; only the lane and the identity scheme differ.

Until then, nothing in this plan changes the Vinyl cohort plan: for Vinyl we still build and serve the daemon, the development package, and the VMODs as one tested cohort, with the full security-channel responsibility that entails.

## Non-goals

- Building, patching, or redistributing varnishd or any Varnish core package.
- Mirroring upstream's varnish repositories.
- A portable `.so` or single VMOD binary claimed to work across Varnish branches.
- Getting VMOD packages into official distribution archives (possible later; not this plan).
- Supporting any Varnish branch before 9.0, including the 6.0 LTS, or branches upstream no longer supports.
- Fedora-style distro-native VMOD lanes before the Varnish Software repository lane is proven.
- Republishing a VMOD package that Varnish Software already publishes, without a deliberate, recorded reason and a distinct package name.

## Risks

- **The lane is not authorized.** Nothing here may be implemented before an explicit `SCOPE.md` amendment adding Varnish as a second engine, expected no earlier than roadmap Step 8. The largest risk to this plan is that it reads like a work item.
- **Strict-ABI operational load**: if the ported VMOD lands `$ABI strict` — or if it targets Debian at all, where `$ABI vrt` currently buys nothing — every upstream patch release forces a republish across all lanes. Mitigation: make the public-API port a real goal, prefer the RPM lane's stronger dependency expression, and automate the rebuild pipeline before adding lanes.
- **Rebuild latency window**: users tracking the Varnish Software repository can see a varnish update before our matching VMOD exists. The resolver hold-back keeps them safe but stale; document pin/hold procedures, publish the expected latency.
- **Multi-branch repository drift**: because one repository carries every branch, a build container that merely enables the repository will silently move to a newer branch between runs. The pin/hold is not a nicety; it is what makes the lane reproducible. Test that the pin holds, do not assume it.
- **Upstream repo changes**: repository naming, layout, retention, and availability are outside our control — and this plan has already been invalidated once by exactly that (the `varnishcache` → `varnish` packaging move). Manifests must record the exact origin, suite, and version so a lane can be rebuilt or re-pointed if upstream restructures again.
- **Divergent provide names across package sources** (Varnish Software deb vs Debian archive deb vs EPEL RPM vs Varnish Software RPM): handled by generating the dependency expression from the manifest per lane, never sharing a hardcoded Depends across lanes.
- **Package-name collision with Varnish Software's published VMODs**: an unqualified name reuse would make a user's resolution outcome depend on repository priority. Check their published set before naming anything.

## Implementation order

0. Obtain an explicit `SCOPE.md` decision adding Varnish as a supported engine, with the added responsibility described. Nothing below starts without it.
1. Decide the target ABI surface for the first Varnish VMOD port and record it (design-level decision, precedes packaging). Note that `vmod-dict`, selected at Step 5, needs no port.
2. Add the `varnish-downstream/v1` schema to the registry and teach `release_tool.py` to validate it and emit per-lane ABI dependency expressions.
3. Stand up the containerized build lane: install varnish + varnish-dev from the Varnish Software repository with the manifest's exact version pinned, on Debian 13 amd64; build the VMOD package, run lint and installed-package smoke.
4. Repeat for EL9 x86_64, deciding and recording the RPM dependency strategy (`varnishd(abi)` vs `varnishd(vrt)` vs exact pin; EPEL is a separate lane).
5. Add the upgrade-transaction tests (hold-back verification on both package managers).
6. Automate upstream release detection and rebuild task creation.
7. Publish the first GitHub pre-release for the proving pair, with manifests, checksums, and the support statement below.
8. Extend to subsequent 9.0+ branches and additional targets; fold the lanes into the same signed repository, staging/promotion, and retention machinery the Vinyl cohort channel builds, when that exists.
9. When Vinyl publishes packages: evaluate the convergence gates per target and begin migrating Vinyl lanes to the downstream model.

## Support statement

The downstream-lane analog of the cohort support statement, to be published alongside it:

> Our Varnish VMOD binaries are supported only with the exact Varnish package source, branch, and revision recorded in their compatibility manifest. Varnish Cache itself is supported by the Varnish project; we do not build, patch, or redistribute it.
