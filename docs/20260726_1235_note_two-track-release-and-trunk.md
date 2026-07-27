# Two tracks: release and trunk

Decision record, 2026-07-26. This repository now maintains two Vinyl pin tracks side by side:

- **release** — built from the upstream release tarball. This is what the published packages should be built from and what most people should install.
- **trunk** — built from a pinned trunk snapshot. This is the early-warning machinery: it exists to find out which VMODs break when Vinyl core changes, and to catch `$ABI strict` churn before a release forces it on us.

Both tracks run through the same CI lanes. The release track is not "built outside CI": a release build is a CI build like any other, from a different set of pins. What distinguishes the trunk track is its *purpose* — surfacing problems introduced by Vinyl core development — not where it runs.

## What prompted this

The maintainer asked why we package 9.0.0 when upstream's website says the latest release is 9.0.1, released 2026-05-18 with a security fix.

The answer, verified in the `../vinyl-cache` checkout, is that we were never actually behind the fix:

- The pinned snapshot `9.0.0~git20260520.25761f8505` is a trunk commit dated 2026-05-20 — two days after the 9.0.1 release.
- 9.0.1 (tag `vinyl-cache-9.0.1`, commit `423648c4cb`) lives on the 9.0 release branch. Its security content is **VSV00019**: HTTP/2 request parsing compared pseudo-header names by prefix instead of exactly. The release-branch fix commits (`c9ddf6d28a` plus the `Tstreq`/`Tstrcmp` follow-ups `ff6290f1ba`, `26a2740b3c`, `42782bb715`) are backports of trunk commits `dfc27fb4e7`, `e67a7e55b0`, `84e2de41aa` — all ancestors of the pinned snapshot.
- `git cherry 25761f8505 vinyl-cache-9.0.1 vinyl-cache-9.0.0` confirms every substantive 9.0.0→9.0.1 commit has a trunk equivalent in the snapshot's history. The only commits with no equivalent are changelog edits ("changes: Mention VSV19", "Prepare for 9.0.1") and Forgejo workflow files — nothing that affects built binaries.

So the built packages are content-wise *ahead* of 9.0.1. But two real problems fall out of the episode:

1. **Optics.** Anyone auditing a package versioned `9.0.0~git…` will reasonably conclude it predates the 18 May security release, exactly as the maintainer did. Nothing in the package metadata says "contains the VSV00019 fix". A version string that *is* a release number answers that question by itself.
2. **No release lane existed.** The packaging had exactly one set of pins, and it pointed at a trunk snapshot. There was no way to build "the thing users should install" and "the thing that warns us about upstream churn" at the same time, because they were the same thing.

The two tracks fix both. Point 1 also carries a follow-up: the next trunk pre-release, if any, should state VSV-fix inclusion explicitly in its release notes rather than leaving it to be inferred from a commit date.

## The release track

Pinned to upstream release **9.0.1**, from the published tarball rather than a Git re-assembly. All values below were verified on 2026-07-26 by downloading the archive and inspecting it:

| Input | Value |
| --- | --- |
| Upstream tag | `vinyl-cache-9.0.1`, commit `423648c4cb6b225b3268ffc337354ea938f5efee`, tagged 2026-05-18 |
| Source archive | `https://vinyl-cache.org/downloads/vinyl-cache-9.0.1.tgz` |
| Archive sha256 | `2e8ec67cd213ea6864c763939d64912025557342fad2a5ffda6c7c5b59bdeb17` |
| Baked strict ABI | `VMOD_ABI_Version "Vinyl Cache 9.0.1 423648c4cb6b225b3268ffc337354ea938f5efee"` — the tarball ships `include/vcs_version.h` and `include/vmod_abi.h` pre-generated |
| SOURCE_DATE_EPOCH | `1779093527` (author date of `423648c4cb`, 2026-05-18T10:38:47+02:00, per the pins files' author-date rule) |
| Package versions | Debian `9.0.1-1`; RPM `9.0.1-1.el9` |
| vtest2 | vendored inside the tarball at `bin/vinyltest/vtest2/` — no separate pin |

Two consequences of building from the published tarball:

- **Provenance is upstream's bytes.** The trunk track has to assemble a canonical archive from Git inside the pinned buildroot, because upstream publishes no tarball for a trunk commit. For a release, upstream's own archive is the canonical artifact; our digest pin states which bytes, and anyone can check it against upstream independently of us.
- **The release lanes do not need the Vinyl Git checkout at all.** The source stage verifies the tarball digest and uses the archive verbatim as the Debian orig tarball / RPM Source0. The baked ABI string is asserted against the pin instead of being generated.

### Release cohort identity

Every digest input of the cohort identity is already known, so the id is derivable today. The canonical input blob (see `registry/README.md`):

```text
cachetag-cohort-input/v1
vinyl-source-sha256=2e8ec67cd213ea6864c763939d64912025557342fad2a5ffda6c7c5b59bdeb17
patch-count=0
build-profile=production
build-profile-revision=1
```

SHA-256 = `ac4f719c16f45b5c853e6fcb11a068fb395fff2a1f6a264a81ff4afe86c0e30c`, so the release cohort is **`vinyl-9.0.1-ac4f719c16f4`**. Both lanes' pins carry this id.

The registry manifest for it is deliberately **not** minted yet. The registry has no status for "pinned but never built": `candidate` requires placeholder-free target manifests, and a target manifest's recorded outputs (buildroot image digest, compiler, effective flags, vmoddir from `pkg-config`, artifact digests) only exist once the lanes have run. Minting happens at the first release-track build, exactly as `vinyl-9.0.0-4b7e68292979` was minted from its lanes' evidence. Until then the id in the pins is a derived value the mint must reproduce, not a registered identity.

## The trunk track

The existing pins, unchanged: commit `25761f8505` (2026-05-20), version `9.0.0~git20260520.25761f8505`, cohort `vinyl-9.0.0-4b7e68292979`. Its published pre-release keeps its identity.

Purpose, and what "trunk as CI" concretely means:

- **The pinned trunk lanes in `ci.yml`** prove, on every PR and push, that the full packaging machinery — cohort build, `$ABI strict` dependency generation, install/smoke — works against a fixed trunk snapshot. When we advance the pin, whatever Vinyl core changed since the last pin hits these lanes first.
- **The unpinned `trunk-vmod-ci.yml`** (new, draft) follows trunk HEAD on a weekly schedule and runs the documented cachetag source harness against it. This is the actual tripwire for Vinyl core changes: it catches VMOD compile/test breakage within a week of the commit that caused it, instead of at the next re-pin. It builds nothing installable and publishes nothing, so it is deliberately exempt from the everything-is-pinned rule — following HEAD is its job.
- Because Vinyl bakes the commit id into `VMOD_ABI_Version`, *every* trunk advance is a strict-ABI break by construction. The trunk track is what makes that churn routine — a rebuild inside a new cohort — rather than a surprise at release time.

### Trunk snapshot versioning, corrected for the next re-pin

The current convention `9.0.0~git<date>.<hash>` was chosen when no 9.0.x release existed; `~` sorts below everything, which gave a clean upgrade path to any future release. It now has a misleading side effect: the snapshot sorts below 9.0.0 and 9.0.1 while its *content* is ahead of both — the root of the optics problem above.

**Future trunk re-pins use `<latest release tag reachable from the snapshot commit>+git<commit date>.<10-char hash>`** — for example a re-pin today would be `9.0.1+git20260726.<hash>`. `+git` sorts above the release it extends and below the next one, so version order matches content order in both dpkg and rpm, and the string itself says "9.0.1 plus trunk commits". The current pin keeps its `~git` name: its cohort is minted and its pre-release is published, and renaming a published identity for cosmetics is exactly what this repository's rules forbid.

Re-pin procedure (unchanged in substance, now written down): pick the new trunk commit, update the trunk block of both pin files (commit, version, author-date epoch), let the lanes re-derive the assembled-archive digest, record it, and mint the new trunk cohort only if the snapshot is actually being published rather than merely exercised.

## Mechanics

Track selection is the `VINYL_TRACK` environment variable (`release` | `trunk`), dispatched inside each lane's pin file so every consumer — lane drivers, CI scripts, `release-manifest.sh` — resolves one consistent set of values from the single definition it already sourced:

- `recipes/debian-13/pins.env` and `recipes/el9/cohort.env` each carry shared values plus a `case $VINYL_TRACK` block; unknown tracks fail loudly.
- `VINYL_SOURCE_KIND` (`git` | `tarball`) tells the source stages which procedure the track uses. The tarball path verifies the pinned digest on the host (the download is the one host-side network step; the digest check is the authority), re-verifies it inside the container, asserts the baked ABI string, and skips the Git assembly entirely.
- `ci.yml` builds both tracks via a lane matrix; the release lanes skip the Vinyl checkout step. The cachetag source-archive job is track-independent (its digest is a function of the cachetag commit alone).
- **The default track is `trunk` for now.** The release-track source path is drafted but has never executed; until its first green CI run, defaulting local invocations to it would make `build.sh` fail for anyone who runs it. Flipping the default to `release` is the last item of the cutover below.

## What is deliberately not done now

- **No builds.** Maintainer instruction for this change. The release lanes and the tarball source path are drafted, marked as unexecuted, and validated by their first CI run.
- **No release cohort manifest** — minted at first build, above.
- **No registry schema change.** A cohort's track is derivable from `vinyl.version` (`~git`/`+git` suffix means trunk, a bare release version means release). An explicit `track` field, and a "pinned but unbuilt" status that would let a cohort be registered before its evidence exists, are noted in `registry/README.md` as future work rather than bolted on here.
- **No re-pin of the trunk track** — it would dirty every derived digest with no builds to re-derive them.

## Cutover checklist: first release build

1. Push the two-track changes; let `ci.yml` run. Trunk lanes must stay green (they are unchanged in behaviour). Release lanes exercise the tarball source path for the first time — treat failures there as bugs in the drafted path, not as pin errors, and fix the path.
2. When both release lanes are green: mint `vinyl-9.0.1-ac4f719c16f4` from the recorded evidence (`registry/cohorts/`, `registry/targets/vinyl-9.0.1-ac4f719c16f4/`), statuses `candidate`, and confirm `release_tool.py cohort-id` reproduces the id pinned above.
3. Run the transaction matrices against the release cohort (`nightly-transactions.yml` / the step-9 scripts) — the standing gap recorded for the first cohort applies here too.
4. Cut the release via `release-draft.yml` with `VINYL_TRACK=release`, release notes stating explicitly: *contains VSV00019 (HTTP/2 pseudo-header comparison), fixed upstream in 9.0.1*.
5. Flip the default track to `release` in both pin files and update their headers; the trunk lanes keep `VINYL_TRACK=trunk` explicitly from the CI matrix.
6. Update `README.md` status and, in the cachetag repository, `packaging/README.md`'s cohort examples to the release cohort.

## Evidence trail

- 9.0.1-vs-snapshot ancestry and `git cherry` output: session of 2026-07-26 against the `../vinyl-cache` checkout, tags `vinyl-cache-9.0.0` (`e92852d957`, 2026-03-16) and `vinyl-cache-9.0.1` (`423648c4cb`, 2026-05-18).
- Tarball digest and baked ABI: downloaded `vinyl-cache-9.0.1.tgz` (6,355,153 bytes, last-modified 2026-06-28 on the mirror), sha256 `2e8ec67c…`, `include/vmod_abi.h` as quoted above.
- Cohort input digest: `printf` of the blob above piped to `shasum -a 256`, independently of the registry tooling, matching the worked-vector procedure in `registry/README.md`.
