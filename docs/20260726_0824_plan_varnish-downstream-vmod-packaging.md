# Varnish Cache downstream VMOD packaging plan

Date: 2026-07-26

Status: Draft

Support floor (decided 2026-07-26): nothing before Vinyl Cache 9.0 / Varnish Cache 9.0 is supported. The Varnish 6.0 LTS branch and every pre-9.0 Varnish branch are permanently out of scope; the first Varnish lane opens with the Varnish 9.0 branch.

Related: [binary packaging and distribution plan](../../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md) (the Vinyl cohort plan), `registry/README.md`, `registry/distro-native/`.

## Executive recommendation

For Varnish Cache, do not replicate the Vinyl cohort model. Become a **downstream VMOD provider**: rely on the Varnish project's own binary packages for `varnishd` and its development files, and build, test, and publish only VMOD packages against them.

This is the standard model for third-party Varnish modules (varnish-modules, vmod-uuid, Varnish Software's commercial VMODs all work this way), and it is strictly less responsibility than the Vinyl cohort:

- Upstream owns the daemon: builds, security updates, service integration, and the daemon package repository. We never become a security-update channel for an internet-facing daemon on the Varnish side.
- We own only the VMOD packages: their ABI dependency correctness, their rebuild latency after upstream releases, and our own repository signing.

The same model is the intended end state for Vinyl Cache. The existing Vinyl cohort plan remains in force **only because Vinyl publishes no packages today**; once the Vinyl project publishes runtime and development packages that meet the convergence gates in this plan, Vinyl lanes migrate to this downstream model and the cohort machinery is retired for those lanes.

## Why this works for Varnish: the ABI mechanism

These mechanics were verified against upstream's packaging repository ([pkg-varnish-cache](https://github.com/varnishcache/pkg-varnish-cache), `debian/control` and `debian/rules`) and distro package metadata on 2026-07-26.

Every compiled VMOD is stamped at build time by `vmodtool.py` with a `VMOD_ABI_Version` string, and `varnishd` checks it at `vcl.load`. A mismatch is a clean, immediate refusal to load the VCL — the failure mode is loud, not silent corruption. The stamp has two modes, chosen by the `$ABI` declaration in the `.vcc`:

- **`$ABI strict`** (default): the stamp is the exact Varnish version and git revision. The binary loads only against the identical varnishd build, including across patch releases.
- **`$ABI vrt`**: the stamp is `VRT_MAJOR_VERSION.VRT_MINOR_VERSION` only. The binary survives any varnishd rebuild that preserves the VRT major version — in practice, patch releases within a branch.

VRT major is bumped in essentially every six-monthly Varnish release (March 15 / September 15). So even `$ABI vrt` means one rebuild per release **branch**; it only saves the per-patch-release rebuilds. There is no build-once-run-everywhere option. The model is out-of-tree kernel modules with a small matrix: upstream supports only the latest branch plus the 6.0 LTS, and with the pre-9.0 support floor our matrix is smaller still — the supported 9.0+ branches only.

### How the dependency is expressed in packages

The virtual-provide names differ by package source, and a lane's dependency expression must match the varnish package source that lane targets:

| Varnish package source | ABI provides | Dependency expression for a VMOD package |
| --- | --- | --- |
| Upstream packagecloud `.deb` (pkg-varnish-cache) | `varnishd-abi-<git revision>` and `varnishd-vrt (= MAJOR.MINOR)` | `Depends: varnishd-vrt (= 19.0)` (vrt) or `Depends: varnishd-abi-<hash>` (strict) |
| Debian/Ubuntu archive `.deb` | `varnishabi-strict-<hash>` and a VRT provide (Debian's own naming, not upstream's) | resolve exact names from the target release's `varnish` package at implementation time |
| Fedora / EPEL / EL-family RPM | `varnishd(abi) = <hash>` and `varnishd(vrt) = <version>` | `Requires: varnishd(vrt) = 19.0` or `Requires: varnishd(abi) = <hash>` |
| Upstream packagecloud RPM (pkg-varnish-cache) | **none** — the upstream spec declares no ABI provides | exact `Requires: varnish = <version>-<release>` pin |

The upstream `varnish-dev`/`varnish-devel` package ships the full header set (including private `cache/cache.h`-level headers), `vmodtool.py`, and `varnishapi.pc` (which supplies `$VMODDIR`), so the build is fully driven by the installed development package. This is the development-surface property the Vinyl convergence gates below demand.

## Scope and first milestone

Prerequisite outside this plan: a VMOD that targets Varnish. Porting cachetag to Varnish is its own engineering effort (see the ABI-surface decision below); this plan defines how its packages are built and supported once it exists, and applies unchanged to any other VMOD we package for Varnish.

First milestone, mirroring the Vinyl plan's proving pair: one VMOD, built for the Varnish 9.0 branch from upstream's packagecloud repositories, on Debian 13 amd64 and EL9 x86_64, published as a GitHub pre-release with compatibility metadata and checksums. The lane opens when upstream publishes Varnish 9.0 packages; resolve the exact branch repository name from varnish-cache.org at implementation time rather than hardcoding it in tooling.

Supported varnish package sources, in order:

1. **Upstream packagecloud repositories** (`varnishcache/varnish<NN>`, `varnishcache/varnish60lts`) — the primary lane. Users configure two repositories: upstream's for varnishd, ours for VMODs.
2. **Distribution-provided varnish packages** (Debian/Ubuntu archive, EPEL) — a later, separate lane per the existing distro-native discipline: a VMOD built against upstream's varnish-dev is not interchangeable with one built against Debian's varnish, even at the same upstream version.

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
- the varnish branch, repository origin (packagecloud repo URL or distro archive), and the exact varnish binary/development package versions installed at build time;
- the VMOD's `$ABI` mode, the observed `VMOD_ABI_Version` stamp, VRT version, and (for strict) the varnishd ABI hash;
- which virtual provide or exact-version pin the package depends on (`abi_dependency_expression`), generated by `release_tool.py`, never hand-edited;
- build environment, artifact digests, and test results, as in the existing schemas.

No cohort identifier exists on this lane by design: the compatibility claim is "this VMOD package revision, against that varnish package revision." `release_tool.py metadata` grows a mode that emits the Depends/Requires expression from the manifest, keeping the no-hand-edited-ABI rule intact.

## Package mechanics

Same native-tooling discipline as the Vinyl plan (sbuild/pbuilder, Mock, lintian, rpmlint, dh_shlibdeps, clean containers, no nFPM/fpm), with these lane-specific rules:

- **Debian/Ubuntu**: `Build-Depends: varnish-dev` installed from the exact packagecloud branch repo pinned in the manifest. `Depends: ${shlibs:Depends}, ${misc:Depends}` plus the generated ABI expression (`varnishd-vrt (= X.Y)` or `varnishd-abi-<hash>`).
- **EL-family RPM**: decide per lane whether the target is upstream packagecloud RPMs (exact `varnish = V-R` pin, since upstream declares no ABI provides) or EPEL/distro RPMs (`varnishd(abi)`/`varnishd(vrt)`). Do not ship one RPM claiming both.
- Build in a container that has **only** the pinned varnish repo enabled, so the resolved varnish-dev version is exactly the manifest's, and record the resolved versions back into the manifest.
- Installed-package smoke per target: install upstream varnish + our VMOD, confirm the `.so` landed in `pkg-config --variable=vmoddir varnishapi`, compile a VCL importing it, run the behavioral smoke, uninstall cleanly.
- Upgrade transactions: smaller matrix than the cohort model but the same class of test. Verify that when upstream publishes a varnish our VMOD is not yet rebuilt for, `apt upgrade` holds varnish back rather than removing the VMOD, and document what `apt full-upgrade` / `dnf --allowerasing` do. Treat resolver behavior as a test result, not an assumption.

## Rebuild and response model

The recurring obligations — this is the entire steady-state cost of the lane:

| Upstream event | `$ABI vrt` VMOD | `$ABI strict` VMOD |
| --- | --- | --- |
| New six-monthly branch (Mar 15 / Sep 15) | new lane: build, test, publish | new lane: build, test, publish |
| Patch release within a branch | load-verification run only | rebuild, retest, republish |
| Security release within a branch | load-verification run, expedited | expedited rebuild, retest, republish |
| Branch leaves upstream support | retire lane per retention policy | retire lane per retention policy |

Automate detection: watch upstream release announcements and poll the packagecloud repository metadata for new varnish package versions per supported branch; a new version opens a rebuild task automatically. The security posture to document publicly: **upstream fixes varnishd; our obligation is bounded rebuild latency for strict-ABI VMODs, during which the package resolver holds the varnish upgrade back rather than breaking a running configuration.** State a target latency (e.g. rebuild within N days of an upstream security release) only when it can be staffed, exactly as the Vinyl plan requires for its SLA.

## Vinyl convergence path

The trigger: the Vinyl project begins publishing official binary packages. The gates before a Vinyl target migrates from the cohort model to this downstream model — all of them, per target:

1. Upstream publishes both runtime and development packages for the target, from a maintained repository with a stated support/security policy.
2. The development package exposes the full surface our VMODs need: private headers (`cache/cache.h` et al. for strict-ABI VMODs), `vinylapi.pc` with `vmoddir`, VMOD/VSC generation tools.
3. The runtime package carries usable ABI identity: a strict-ABI virtual provide (`vinyld-abi-<hash>` or equivalent) regenerated on every rebuild, or failing that we fall back to exact package-version pins as on the upstream-RPM lane.
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
- Fedora-style distro-native VMOD lanes before the upstream-packagecloud lane is proven.

## Risks

- **Strict-ABI operational load**: if the ported VMOD lands `$ABI strict`, every upstream patch release forces a republish across all lanes. Mitigation: make the public-API port a real goal; automate the rebuild pipeline before adding lanes.
- **Rebuild latency window**: users tracking upstream's repo can see a varnish update before our matching VMOD exists. The resolver hold-back keeps them safe but stale; document pin/hold procedures, publish the expected latency.
- **Upstream repo changes**: packagecloud repo naming, retention, or availability is outside our control; manifests must record the exact origin so a lane can be rebuilt against a mirror if upstream restructures.
- **Divergent provide names across package sources** (upstream deb vs Debian archive deb vs EPEL RPM vs upstream RPM): handled by generating the dependency expression from the manifest per lane, never sharing a hardcoded Depends across lanes.

## Implementation order

1. Decide the target ABI surface for the first Varnish VMOD port and record it (design-level decision, precedes packaging).
2. Add the `varnish-downstream/v1` schema to the registry and teach `release_tool.py` to validate it and emit per-lane ABI dependency expressions.
3. Stand up the containerized build lane: install upstream varnish + varnish-dev from a pinned packagecloud branch repo on Debian 13 amd64, build the VMOD package, run lint and installed-package smoke.
4. Repeat for EL9 x86_64, deciding and recording the RPM dependency strategy (upstream pin vs EPEL provides).
5. Add the upgrade-transaction tests (hold-back verification on both package managers).
6. Automate upstream release detection and rebuild task creation.
7. Publish the first GitHub pre-release for the proving pair, with manifests, checksums, and the support statement below.
8. Extend to subsequent 9.0+ branches and additional targets; fold the lanes into the same signed repository, staging/promotion, and retention machinery the Vinyl cohort channel builds, when that exists.
9. When Vinyl publishes packages: evaluate the convergence gates per target and begin migrating Vinyl lanes to the downstream model.

## Support statement

The downstream-lane analog of the cohort support statement, to be published alongside it:

> Our Varnish VMOD binaries are supported only with the exact Varnish package source, branch, and revision recorded in their compatibility manifest. Varnish Cache itself is supported by the Varnish project; we do not build, patch, or redistribute it.
