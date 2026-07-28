# Nightly package builds fail because the pinned cachetag commit was removed

Date: 2026-07-28

Run investigated: <https://github.com/boffinate/vcache-packaging/actions/runs/30333562372>

## Symptom

The scheduled nightly workflow completed after about 30 seconds. This was not a package-build timeout: no package build started.

The first job, `build and pin the cachetag source archive`, failed in its second `actions/checkout` step while checking out `boffinate/libvmod-cachetag` at the workflow's pinned `CACHETAG_REF`:

```text
fatal: remote error: upload-pack: not our ref fcc369d23b199cc8e41086f28f2322256a8843d9
```

`actions/checkout` tried the fetch three times. Its 11-second and 14-second retry delays account for almost all of the apparent 30-second runtime. The job has `timeout-minutes: 90`; GitHub concluded it as a failure, not a cancellation.

The Debian and EL9 jobs depend on the source-archive job, so GitHub skipped both after the checkout failure.

## Reproduction and evidence

The failure is reproducible without building anything:

```sh
git -C ../libvmod-cachetag cat-file -t fcc369d23b199cc8e41086f28f2322256a8843d9
git ls-remote https://github.com/boffinate/libvmod-cachetag.git
gh api repos/boffinate/libvmod-cachetag/commits/fcc369d23b199cc8e41086f28f2322256a8843d9
```

The old object is absent from the current local checkout and the GitHub commit API returns 422 for it. The remote currently exposes:

```text
368a01f11d25256644154d02ec255db545154c1c refs/heads/main
1508d3949957f7a1cd10f5e086b333148ca2c9cc refs/tags/v1.0.0
368a01f11d25256644154d02ec255db545154c1c refs/tags/v1.0.0^{}
```

The previous nightly, run `30243618558`, checked out `fcc369d…` successfully at 2026-07-27 07:42 BST and completed both package lanes. The new cachetag commit was made at 2026-07-27 08:40 BST with the message `Prepare cachetag for public release` and explicitly says the development history was cleaned before release. The annotated `v1.0.0` tag was then created at 08:41 BST and points to the new commit. This places removal of the old reachable history between the last successful nightly and the failing nightly.

The prior successful nightly artifact was still available. Its source archive has the expected pinned digest:

```text
a262ac7a74a1464d4c0a4cc6f072ea04a77ff660b25bf0befd32dc63c18fb329  libvmod-cachetag-1.0.0.tar.gz
```

Comparing the recovered archive with the current `v1.0.0` commit shows that the public-release rewrite was not commit-metadata-only. Distributed tracked files changed, including the licence identifier in C sources from BSD-2-Clause to MPL-2.0, and the commit timestamp also changed. Pointing `CACHETAG_REF` at `368a01f…` while retaining the old archive digest would therefore fail the source-archive digest gate even after checkout succeeds.

The `v1.0.0` Git tag currently exists, but `gh release view v1.0.0 --repo boffinate/libvmod-cachetag` reports that no GitHub Release exists. The lane source URLs therefore also need checking before treating a checkout-only change as a complete repair.

No package, VMOD, benchmark helper, or host-local autotools build was run during this investigation.

## Ranked hypotheses and outcome

1. The cachetag public-history rewrite removed the pinned object while `vcache-packaging` retained the old SHA. Confirmed.
2. The SHA was mistyped or drifted only in the nightly workflow. Rejected: the same old SHA is deliberately recorded in both build workflows, both lane pin files, and the minted cohort manifest.
3. GitHub or checkout authentication failed transiently. Rejected: the repository checkout and ref discovery succeeded, while the specific object is absent locally and through the GitHub API.
4. The job hit a 30-second timeout. Rejected: the configured timeout is 90 minutes and the timestamps match checkout retry backoff.

## Repair implications

This is a broken immutable-source contract, not a reason to relax checkout or digest validation. Two coherent repair directions exist:

1. Restore `fcc369d…` as a durable reachable ref in `libvmod-cachetag` if the already-built `a262ac7a…` source archive and existing cohort provenance are intended to remain authoritative.
2. Treat `368a01f…` as a new source input: build and verify a new deterministic archive through the documented Docker harness, then move the commit, digest, source-date epoch, workflow mirrors, lane pins, release-manifest input, and cohort provenance together.

Changing only `CACHETAG_REF` is unsafe because the old digest is demonstrably tied to different source content and metadata. Changing only the workflow copy would also leave the recipes and registry internally inconsistent.

## Resolution

The package workflows now build the named annotated tag `v1.0.0` and separately require it to peel to the recorded commit `368a01f11d25256644154d02ec255db545154c1c`. This makes the maintainable source input a release name while retaining a loud failure if the tag moves or ceases to be annotated. The deterministic archive digest remains the independent content gate.

The documented Docker harness rebuilt the tag twice from a clean temporary checkout against Vinyl commit `25761f8505817ac50df994270bfe75b60073e33e`. Both runs produced:

```text
23c378029c50072ca287d045208756a9acd0a648c261d2f0e2bca4fdbf7a1644  libvmod-cachetag-1.0.0.tar.gz
```

The metadata records source-date epoch `1785138016`, two identical canonical archive digests, and a passing fresh-container from-archive build. The fresh-container test passed all 53 tests.

The first archive attempt failed at `cachetag_pm00024.vtc` with 52 of 53 tests passing. In line with the existing flake policy it was rerun manually once, not hidden behind an automatic retry; `cachetag_pm00024` passed in both archive runs and the from-archive run on that attempt.

The separate cachetag investigation found a test setup race, not a production rollback failure. The 12th seed object exactly reaches the 64-bucket side table's automatic-growth threshold, so the resize worker can publish a 64-to-128 migration before the VTC asks its test hook to start the deliberate 16-bucket shrink. That hook correctly refuses to overlay an active migration and the VTC turns the refusal into the observed 500. The cachetag worktree now seeds 11 objects and refills 13 after starting the deliberate shrink, preserving the same 24-object rollback assertions without opening the competing automatic-growth window. Docker verification passed the VTC 20 of 20 times and then passed the complete 53-test suite. No production code, assertion, or retry policy changed.

The cachetag commit, archive digest, source-date epoch, workflow mirrors, Debian and EL9 pins, and candidate cohort provenance moved together. The native package revision moved from 1 to 2 because the source bytes changed without an upstream version change. Evidence from revision 1 was cleared from both candidate target manifests and remains pending until the revised clean-room lanes repopulate it. The cohort identifier remains `vinyl-9.0.0-4b7e68292979`: that identity is derived from the Vinyl source, patches, and build profile, not from cachetag.

This repair deliberately does not vendor cachetag source archives, switch package builds to consume-only, or enable immutable GitHub releases. Package CI continues to re-derive and digest-check the release archive as a reproducibility canary. Immutable release settings are deferred while the controlled repositories are still expected to change.
