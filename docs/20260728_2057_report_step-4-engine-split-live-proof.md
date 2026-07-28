# Report: Step 4 engine-split live proof and package equivalence

Date: 2026-07-28

Status: Complete. The plan's verification case 6, and the package-equivalence contract in [the roadmap](20260728_0916_roadmap_outstanding-packaging-work.md) Step 4.

Companion to [the Phase 2 implementation note](20260728_1936_note_step-4-engine-split.md), which describes what was built. This one records what happened when it ran.

Branch `step4-engine-split`, pushed to origin at `79c91ed`. Not merged, no PR opened.

## Runs

Every run is `ci.yml` on `step4-engine-split`, dispatched with `-f inject=<case>`, one at a time.

| # | Case | Run | Conclusion | Verdict |
| --- | --- | --- | --- | --- |
| 1 | `none` | [30389367431](https://github.com/boffinate/vcache-packaging/actions/runs/30389367431) | success | **PASS** — all 10 expected rows passed |
| 2 | `engine_build` | [30391192683](https://github.com/boffinate/vcache-packaging/actions/runs/30391192683) | failure | **PASS** |
| 3 | `suppress_engine_artifact` | [30392799258](https://github.com/boffinate/vcache-packaging/actions/runs/30392799258) | failure | **PASS** |
| 4 | `manifest` (post-L1 fix) | [30394101478](https://github.com/boffinate/vcache-packaging/actions/runs/30394101478) | failure | **PASS** |

Equivalence baseline: [30385644917](https://github.com/boffinate/vcache-packaging/actions/runs/30385644917), the last green `main` run, at `8d8142e` — this branch's base commit, so the only variable between the two runs is the engine split.

No run needed a retry, and nothing hit a timeout. Dispatching strictly one at a time avoided the runner contention that cost Step 3 a run.

## Observed versus expected, per case

### 1. Baseline, `inject=none`

```text
ok: true  {"expected": 10, "passed": 10, "failed": 0, "missing": 0, "required_failed": 0, "not_selected": 1}
  engine/vinyl-release/debian-13-amd64                        passed  observed
  engine/vinyl-release/el9-x86_64                             passed  observed
  engine/vinyl-trunk-pinned/debian-13-amd64                   passed  observed
  engine/vinyl-trunk-pinned/el9-x86_64                        passed  observed
  vmod/cachetag                                               passed  observed
  source/cachetag/release                                     passed  observed
  target/cachetag/release/vinyl-release/debian-13-amd64       passed  observed
  target/cachetag/release/vinyl-release/el9-x86_64            passed  observed
  target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64  passed  observed
  target/cachetag/release/vinyl-trunk-pinned/el9-x86_64       passed  observed
  harness/cachetag/trunk/vinyl-trunk-head                     not_selected  (no ci lane)
```

Ten expected rows against Phase 1's six: the four engine rows are first-class, observed, and reconciled by a collector that computed them from the checked-in manifests rather than from anything the run reported. No synthesized rows, no unexpected records.

Each engine artifact carries what the plan asked for. `engine-vinyl-release-debian-13-amd64`, for instance:

```text
engine-metadata.json  engine-identity.env  packages/{9 files}
row_key         engine/vinyl-release/debian-13-amd64
packages_sha256 79a58910af08aa72…
identity        cohort_id=vinyl-9.0.1-ac4f719c16f4  vinyl_track=release
                vinyl_git_commit=423648c4…  vinyl_strict_abi=423648c4…
                vinyl_package_version=9.0.1-1  vinyl_source_sha256=2e8ec67c…
                vinyl_source_date_epoch=1779093527  vinyl_vrt_expected=23.0
                build_image=debian:trixie@sha256:fac46bff…
                buildroot_snapshot=20260701T000000Z
```

The nine files are exactly the nine Vinyl files the pre-split lane emitted into `dist/debian-13`, which is what the producer's suffix patterns had to achieve.

### 2. `engine_build`

```text
ok: false  {"expected": 10, "passed": 8, "failed": 2, "missing": 0, "required_failed": 2}
  engine/vinyl-trunk-pinned/debian-13-amd64                   failed_engine_build
      "injected engine-build failure"
  target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64  blocked_by_engine_artifact
      "engine/vinyl-trunk-pinned/debian-13-amd64 published no engine-vinyl-trunk-pinned-debian-13-amd64"
  — the other three engine rows and their three consumer rows: passed
```

Every property the case exists to prove:

- **The injection fired on exactly one row.** In the injected job, step 4 `injected engine-build failure` ran; in `engine vinyl-release (debian-13-amd64)` the same step is `skipped`. The `matrix.inject_build == 'true'` string-typed path had never executed before this run, and it selected precisely the row `ci_matrix.INJECT_ENGINE_ROW` names.
- **The blocked consumer genuinely started.** Its job ran 19:27:12–19:27:21, checked the VMOD source out, verified the tag, downloaded the VMOD source archive successfully, attempted the engine download, wrote its own classified record and failed explicitly at step 27. The ledger marks it `observed`, not synthesized: this is the row reporting itself, not the collector guessing.
- **The blocked row names the engine row identity**, not an unclassified download error.
- **Isolation held**: three engine rows and three consumer rows completed normally, including the EL9 row of the very same engine channel.
- The collector ran after the failures and reported the shared root cause. `required_failed: 2` — the engine row and its one consumer, which is the correct count: one root cause, one victim.

### 3. `suppress_engine_artifact`

The sharper case, and the one that isolates the consumer-side classification from any question of whether the engine build works.

```text
ok: false  {"expected": 10, "passed": 9, "failed": 1, "missing": 0, "required_failed": 1}
  engine/vinyl-trunk-pinned/debian-13-amd64                   passed   <- GREEN
  target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64  blocked_by_engine_artifact
  — everything else: passed
```

- **All four engine jobs concluded `success`**, including the suppressed one. Its steps show `stage and describe the engine artifact: success`, `upload the engine artifact: skipped`, `fail when this engine row failed: skipped`. It did all its work and published nothing.
- **The artifact is genuinely absent from the run.** The run carries `engine-vinyl-release-debian-13-amd64`, `engine-vinyl-release-el9-x86_64` and `engine-vinyl-trunk-pinned-el9-x86_64` — and no `engine-vinyl-trunk-pinned-debian-13-amd64`.
- **Exactly one consumer went red**, `observed`, naming the engine row.
- `required_failed: 1`. A green producer and a red consumer is exactly the asymmetry the plan's "a missing engine artifact must fail only the rows that name it" requires, and it cannot be explained by a build failure because there wasn't one.

### 4. `manifest`, after the L1 fix

```text
ok: false  {"expected": 1, "failed": 1, "required_failed": 1}
  vmod/cachetag  failed_manifest_validation  observed
      "registry/vmods/cachetag.yml did not validate as vmod-ci/v1"
unexpected records: 0
```

One row. Zero stray engine records. `discover-engines` logged `matrix={"include": []}` and `count=0`, the engine job's `needs.discover-engines.outputs.count != '0'` guard skipped it, and the source and target jobs skipped behind the failed `plan`.

This is the L1 defect the audit caught, fixed and then proven. Before the fix, `discover-engines` would have derived four engine rows from a manifest every other job treats as unparseable, four engine jobs would have built and passed, and the run would have ended with four `passed` records that no expected row claims — the collector reporting stray results for work the injected scenario says was never declared. The rule from Phase 1 now reads: **an injection that changes what the ledger should contain must reach every job that computes it, and there are four.**

## Package equivalence

Branch run 1 versus `main` run 30385644917, same commit content, same pins. 10 Debian packages and 18 EL9 RPMs on both sides — no package appeared or disappeared.

### Priority checks

The audit named four things to check first, because they are where the split could plausibly have moved bytes.

**1. EL9 cachetag engine ABI Requires.** These are derived, on EL9, from values read back out of the mock-installed development package — which in `vmod` scope comes from the downloaded artifact rather than from a build in the same container. `vinyld(vrt)` is the sharpest of the three, because the rpm identity branch pins no VRT expectation, so the metadata comparison could not have caught a change in it.

| Channel | Requires | main | branch |
| --- | --- | --- | --- |
| `vinyl-release` | `vinyld(abi)(x86-64)` | `423648c4cb6b225b3268ffc337354ea938f5efee` | identical |
| `vinyl-release` | `vinyld(cohort-…)(x86-64)` | `vinyld(cohort-vinyl-9.0.1-ac4f719c16f4)` | identical |
| `vinyl-release` | **`vinyld(vrt)(x86-64)`** | **`= 23.0`** | **identical** |
| `vinyl-trunk-pinned` | `vinyld(abi)(x86-64)` | `25761f8505817ac50df994270bfe75b60073e33e` | identical |
| `vinyl-trunk-pinned` | `vinyld(cohort-…)(x86-64)` | `vinyld(cohort-vinyl-9.0.0-4b7e68292979)` | identical |
| `vinyl-trunk-pinned` | **`vinyld(vrt)(x86-64)`** | **`= 23.0`** | **identical** |

**PASS**, including the unpinned VRT. The value the artifact metadata does not constrain came out the same anyway, which is what the sharp check was for.

**2. EL9 cachetag SRPM.** `mock --buildsrpm --sources "$srcdir"` sees a different directory in `vmod` scope (the cachetag tarball only) than in `all` scope (the Vinyl tarball, `find-provides` and seven systemd files as well). If `--sources` packaged the directory rather than the spec's declared sources, the SRPM would differ.

```text
libvmod-cachetag-1.0.1-1.el9.src.rpm contents, both channels, both sides:
    libvmod-cachetag-1.0.1.tar.gz
    libvmod-cachetag.spec
```

Identical on all four dumps, and the normalized comparison reports the SRPM `EQUIVALENT`. **PASS** — rpmbuild packages what the spec declares, so the extra files in `all` scope were always inert and their absence in `vmod` scope changes nothing.

The whole-SRPM sha256 does differ between the two runs, as do the binary RPM digests. That is not an equivalence requirement, and the cause is measured rather than assumed:

```text
libvmod-cachetag-1.0.1-1.el9.src.rpm     main BUILDHOST=791b7691ccc6  BUILDTIME=1785230737
                                       branch BUILDHOST=2d9388b8c503  BUILDTIME=1785230737
```

`BUILDTIME` is identical and correctly clamped to the cachetag `SOURCE_DATE_EPOCH`, so the epoch handling is intact. The only differing field is `BUILDHOST`, the container's random hostname, which both `container/build.sh` and `container-mock.sh` document as deliberately not pinned. Two `main` runs would differ the same way.

**3. Debian `SHA256SUMS` completeness.** The producer copies engine files by seven explicit suffix patterns rather than a `vinyl-cache*` glob, because `dist/debian-13` also holds the cached upstream `.tgz`, which is a build input rather than a package. Every Vinyl file the pre-split lane emitted had to come back.

| Channel | File list | Digests excluding `.buildinfo`/`.changes` |
| --- | --- | --- |
| `vinyl-release` | identical, 17 entries | all identical, 11 entries |
| `vinyl-trunk-pinned` | identical, 17 entries | all identical, 11 entries |

**PASS.** The `.buildinfo` and `.changes` digests differ, which the contract excludes as legitimately recording per-run build-environment state.

**4. mtimes through the artifact round-trip.** `cp -p` preserves them, and two independent checks confirm nothing downstream noticed. `dpkg-scanpackages`: the cachetag `.deb` built against the round-tripped local repository is byte-identical to main's, which it could not be if resolution had differed. `lintian`: it linted all four `.changes` files on both sides —

```text
lintian: libvmod-cachetag_1.0.1-1_amd64.changes
lintian: libvmod-cachetag_1.0.1-1_source.changes
lintian: vinyl-cache_9.0.1-1_amd64.changes
lintian: vinyl-cache_9.0.1-1_source.changes
lintian exit status: 0 (0 = no error-level tag)
```

— and lintian errors when a `.changes` references a file that is not beside it, so linting the Vinyl `.changes` successfully proves the round-tripped set is complete and coherent. Tag sets identical: 17 tags on `vinyl-release`, 11 on `vinyl-trunk-pinned`. **PASS.**

### Debian 13 amd64 — byte-identical package digests

Excluding `.buildinfo` and `.changes`.

| Engine channel | Package | Verdict |
| --- | --- | --- |
| `vinyl-release` | `libvmod-cachetag_1.0.1-1_amd64.deb` | identical (`d488e49c…`) |
| `vinyl-release` | `libvmod-cachetag-dbgsym_1.0.1-1_amd64.deb` | identical (`052974bb…`) |
| `vinyl-release` | `vinyl-cache_9.0.1-1_amd64.deb` | identical (`c27b7eac…`) |
| `vinyl-release` | `vinyl-cache-dbgsym_9.0.1-1_amd64.deb` | identical (`a6f5b430…`) |
| `vinyl-release` | `vinyl-cache-dev_9.0.1-1_amd64.deb` | identical (`9152335b…`) |
| `vinyl-trunk-pinned` | `libvmod-cachetag_1.0.1-1_amd64.deb` | identical (`408f7101…`) |
| `vinyl-trunk-pinned` | `libvmod-cachetag-dbgsym_1.0.1-1_amd64.deb` | identical (`0b287d7e…`) |
| `vinyl-trunk-pinned` | `vinyl-cache_9.0.0~git…-1_amd64.deb` | identical (`b326ddf1…`) |
| `vinyl-trunk-pinned` | `vinyl-cache-dbgsym_9.0.0~git…-1_amd64.deb` | identical (`75937a12…`) |
| `vinyl-trunk-pinned` | `vinyl-cache-dev_9.0.0~git…-1_amd64.deb` | identical (`e0cb65ef…`) |

10 of 10 byte-identical, and every digest also matches the value [the Step 3 report](20260728_1856_report_step-3-failure-isolation-live-proof.md) recorded — so the packages are unchanged across two consecutive refactors. **Debian: PASS.**

Worth stating separately: the three Vinyl packages were built by a *different job* than on `main`, in a container started by `debian-lane.sh engine` rather than `debian-lane.sh`, from source assembled by `build.sh source-engine` rather than `build.sh source`. They came out byte-identical.

### EL9 x86_64 — normalized semantic comparison

Compared inside the pinned `almalinux:9` container, per package: NEVRA; summary, license, group, URL; payload path, size, content digest, mode, owner, group, flags, rdev and symlink target; file mtimes as a separate section; Provides; Requires; Conflicts; Obsoletes; weak dependencies; scripts; triggers; changelog. Whole-RPM digests deliberately not compared.

All 18 RPMs — 9 per engine channel: `vinyl-cache`, `-devel`, `-debuginfo`, `-debugsource` and the SRPM, plus `libvmod-cachetag`, `-debuginfo`, `-debugsource` and its SRPM — compared **EQUIVALENT** with an empty diff across every section, in both channels. The package sets match exactly. **EL9: PASS.**

### Installed-package smoke and behaviour

| Row | Smoke | VTC suite |
| --- | --- | --- |
| `vinyl-release` / `debian-13-amd64` | 19 passed, 0 failed | 52/52 passed, 0 skipped, pm00007=clean |
| `vinyl-release` / `el9-x86_64` | ALL STEPS PASSED | 52/52 passed, 0 skipped, pm00007=clean |
| `vinyl-trunk-pinned` / `debian-13-amd64` | 19 passed, 0 failed | 52/52 passed, 0 skipped, pm00007=clean |
| `vinyl-trunk-pinned` / `el9-x86_64` | ALL STEPS PASSED | 52/52 passed, 0 skipped, pm00007=clean |

Identical to `main` on all four rows. **PASS.**

## An observation on latency

The VMOD invocation waits for the whole engine matrix, which the plan accepts. Measured on run 1: the run started 18:51:50 and the VMOD source job started 19:02:13 — about ten minutes of the run is the engine matrix, and the source job (which needs no engine artifact at all) sits behind it. The Debian buildroot is also built twice per pair, once in each half.

Neither is worth fixing yet. The plan says to optimize start latency only if measurements justify it, and these are the first measurements. Recorded so the next person has a number rather than an impression.

## What this does and does not prove

Proven live: the engine packages are built once per engine input and target and consumed as artifacts; consumers verify the recorded identity before building; a failed engine row blocks only the rows that name it, with the shared root cause named; a *successful* engine row that publishes nothing does the same, which separates the consumer-side classification from the build entirely; the collector reconciles engine rows as first-class and is red whenever a required one is; a malformed manifest still collapses to exactly one row now that the fourth ledger builder sees it; and package bytes did not move.

Not proven, because it needs later phases: `failed_transactions` (Phase 4's nightly migration), release-completeness refusal (Phase 4's release-draft migration), and the ten-entry synthetic fixture, which the roadmap places immediately before the Step 8 migration.

Single-VMOD caveat, unchanged from Step 3: with one entry in the catalog, "two VMODs share one engine artifact" is demonstrated by construction and by the multi-VMOD fixtures in `ci_matrix.py selftest`, not by a live run with two real VMODs. That arrives with the second VMOD in Phase 3.

## Recommendation

**The Step 4 exit gate is met.** Its four conditions:

- *Cachetag no longer rebuilds its engine inside each VMOD target row* — the engine matrix builds it four times per run instead of four times per VMOD; no target job contains a Vinyl build step, or a Vinyl checkout.
- *Consumers verify artifact metadata before building* — `verify-engine-metadata` runs before the build in every package row, and a row whose engine artifact is absent stops there with a classified result.
- *An unavailable engine artifact produces the planned classified result* — cases 2 and 3, live, from both a failed producer and a green one.
- *Cachetag packages and installed-package behaviour satisfy the Step 3 equivalence contract after the engine split* — 10/10 Debian byte-identical, 18/18 EL9 semantically equivalent, all four priority checks pass including the unpinned `vinyld(vrt)`, smoke and behaviour unchanged.

Before opening a PR, the repository's required status checks must be the collector's `collect` job, optionally with `structural-validation`. The engine rows are matrix children and must not be named individually — a required check whose job is skipped (as `engine` correctly is under `inject=manifest`) would leave a PR waiting at "Expected" forever.
