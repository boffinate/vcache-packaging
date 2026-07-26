# Step 10 — minting the first cohort, and the first pre-release

Date: 2026-07-26

Follows [the step-10 gate decisions](20260725_1602_note_step-10-gate-decisions.md), [the CI design](20260725_1740_note_step-10-ci-design.md), and the two re-pin notes of 2026-07-25. This note records the mint itself, the release run, and the things that were wrong or missing and are now written down rather than quietly carried.

## The mint

```text
cohort   vinyl-9.0.0-4b7e68292979
```

Derived, not chosen. The canonical input blob, reproducible with `python3 tools/release_tool.py cohort-id --cohort vinyl-9.0.0-4b7e68292979`:

```text
cachetag-cohort-input/v1
vinyl-source-sha256=27568cc1cdf914b3a328fc633d90137b62134fc7d375ca16010656a26d53f507
patch-count=0
build-profile=production
build-profile-revision=1
```

SHA-256 `4b7e68292979c0b1f254a6844d36c65c75f48649ae109a638511f23eb49ece6f`, so the input-id is its first 12 characters. The digest was computed twice, independently: once by hand with `hashlib` over a literally-typed blob, once by the tooling out of the manifest. They agree.

Three decisions the blob does not show:

- **`build_profile.revision: 1`.** The production build profile has had exactly one definition since `libvmod-cachetag` introduced it in `6df6c72` (plan step 6). The only later commit touching that script, `c4259a0`, added a `tar --exclude` and did not change a compiler or configure flag. So the profile has never been revised, and the first revision is 1.
- **`vinyl.source_url` is a Git URL.** The field means "where the pinned archive was fetched from", and there is nothing else truthful to put there: Vinyl publishes no release tarball for this revision. The archive is assembled from `https://code.vinyl-cache.org/vinyl-cache/vinyl-cache.git` at `vinyl.git_commit` by `recipes/debian-13/container/assemble-source.sh`, inside the pinned buildroot image so the tar implementation cannot move the digest.
- **`vinyl.version: 9.0.0`, not the snapshot string.** The cohort id embeds the upstream version, and `9.0.0~git20260520.25761f8505` would put a tilde in a Debian virtual package name and an RPM capability name. The package version keeps the snapshot form; the cohort id does not.

The package-name constraint that motivated that last point held: `vinyl-9.0.0-4b7e68292979` matches `^[a-z0-9][a-z0-9+.-]+$`, and both lanes assert it at build time rather than trusting it.

## What the mint replaced

Two different placeholders, one per lane: `unassigned-local-process-proof` in the Debian lane and the reserved template id `vinyl-9.0.0-000000000000` in the EL9 lane. Packages built from either advertised a cohort capability no manifest described.

A third copy turned up during the switch: `recipes/debian-13/mismatch-fixture.sh` carried its own hand-written baseline ABI, package version and cohort id. It now sources `pins.env`. That copy was not harmless — the fixture asserts the baseline advertises `BASE_COHORT` before rewriting it, so after the mint it would have failed against the very packages it derives from.

## Verification order, and why it is that order

The registry records outputs of a build, and the cohort id is an input to that build — it is baked into `vinyld-cohort-<id>` and into cachetag's dependency on it. So no earlier run's artifact digests describe the minted cohort, and the manifests were filled in two passes:

1. mint + lane switch, with `artifacts: []` and `build_dependencies: []`;
2. after [ci.yml run 30192249530](https://github.com/boffinate/vcache-packaging/actions/runs/30192249530) went green on the real id, record what that run produced.

The built Debian package confirms the switch took: `Depends: … vinyld-abi-25761f8505…, vinyld-vrt (= 23.0), vinyld-cohort-vinyl-9.0.0-4b7e68292979`, and the RPM advertises `vinyld(cohort-vinyl-9.0.0-4b7e68292979)(x86-64)`.

### Reproducibility, measured across runs

Comparing the two green runs before the mint (`30174495649` and `30175029127`), on identical inputs:

- **Debian: every `.deb`, `.dsc`, `.debian.tar.xz` and `.orig.tar.gz` is bit-identical.** Only `.buildinfo` and `.changes` differ, which is expected — they record the build environment and each other's checksums.
- **EL9: every RPM differs**, on identical inputs. RPM headers carry build-time state that `SOURCE_DATE_EPOCH` does not pin.

This is why the target manifests name the run their artifact digests come from. A Debian digest is a property of the inputs; an EL9 digest is a property of one run. It also means the digests recorded for a candidate are not the digests of what eventually gets published, unless the manifests are refreshed from the publishing run — which is the last step of promoting a cohort to `released`.

## Evidence gaps this pre-release ships with

`validate --require-releasable` does not pass, and the manifests were not edited to make it. Two verdicts are `pending` on both targets:

- **`full_behavior_suite`.** The complete 53-test suite (52 VTCs plus the WAL unit test) ran twice in the same CI run, on x86_64, from the byte-identical pinned source archive the packages are built from — but in the **diagnostic** profile, in the source harness, against the VMOD the harness builds rather than the one the package installs. The plan asks for the suite "against the production-hardened package build". No lane does that. Doing it needs `vinyltest` driven against the installed `vmoddir` with the VTCs unpacked from the release archive; `vinyltest` is shipped in the EL9 runtime package, so the missing piece is a harness, not a packaging change.
- **`upgrade_transactions`.** Produced by `nightly-transactions.yml`, which had never run before today.

Rather than either lying or blocking, the release workflow gained `allow_incomplete_evidence`: off by default, and when set it copies every failing check verbatim into `release-manifest.json` as `evidence_gaps` and into the run summary as warnings. A release can now be published that states precisely what it has not proved.

The gate itself moved into `scripts/ci/release-manifest.sh`, beside the assembly, so it cannot be left behind by a change to the workflow's early steps.

## Tooling changes the mint forced

- **`--require-releasable` had to stop meaning "every manifest here is releasable".** The templates are permanent — they are the schema exemplars and the self-tests read the checked-in ones by path — so that reading is unsatisfiable by construction, and the gate could only ever be a warning. It now means: every non-template manifest must be release-ready, at least one cohort must come through releasable, and naming a template with `--cohort` is an error.
- **Buildroot package names needed their own pattern.** Recording the EL9 buildroot exactly is that lane's entire reproducibility story, and RPM ships `perl-AutoLoader`, `hunspell-en-US`, `perl-Text-Tabs+Wrap`. The project's lower-case name pattern is for names this project chooses; `build_dependencies[].name` now accepts what a distribution resolves.

## Two facts recorded rather than corrected

Both are in the EL9 target manifest, both deliberately left alone this close to a release, both worth fixing next:

1. **The EL9 `SOURCE_DATE_EPOCH` is not the one the lane exports.** `container-mock.sh` exports `1779265093`; rpmbuild used `1779235200`. `redhat-rpm-config` derives `SOURCE_DATE_EPOCH` from the topmost `%changelog` entry and truncates to that day at midnight UTC, so the export is overridden. Worse, the changelog date is substituted from `VINYL_SOURCE_DATE_EPOCH`, so the cachetag package is dated to the *Vinyl* commit's day rather than to its own. The Debian lane uses the cachetag committer date, `1784997430`, as it should. Fixing it changes package bytes, which is why it waited.
2. **`package_lint: pass` is weaker on EL9 than on Debian.** The Debian lane fails on a non-zero `lintian` exit status. The EL9 lane runs `rpmlint` with `set +e` and only records the output, so the verdict rests on comparing this run's 5 errors and 22 warnings against the triage table in the [step-7-8 EL9 note](20260724_2240_note_step-7-8-el9-lane.md) — which they match exactly. An allowlist assertion would make it a check rather than a habit.

A third, now fixed: Mock's `build.log` was not kept, so the EL9 target manifest's `configure_options`, `cflags` and `ldflags` had no source in the run at all — the only alternative was restating what the distribution's macros are expected to expand to, which is the kind of hand-written value this repository's rules forbid. The lane now copies it into `dist/el9/logs/`.

## Release assembly

`release-draft.yml` had never run. Findings from driving it are below; the workflow changes it needed were made before the first dispatch, from reading it against the now-real registry:

- the source archive is built with `--release` from the `v1.0.0` tag. That mode refuses a dirty tree or a missing annotated tag, and it does **not** change the archive: `release_stamp` reaches only the metadata sidecar, so the pinned digest `a262ac7a…` must hold across the mode change, and the same assertion runs in both modes.
- `RELEASE-SHA256SUMS` used to list lane-prefixed paths (`debian-13/foo.deb`) while the workflow published the files flat, so the checksum file described names nobody could download. One script now assembles the upload directory and computes the checksums over it.
- release assets keep their **native** filenames. The registry also generates a distro-bearing name (`libvmod-cachetag-1.0.0-1-debian-13-amd64.deb`), and it is recorded in `release-manifest.json`, but renaming the `.deb` would contradict the `.changes` and `.buildinfo` published beside it, which reference the native name and its digest.

## Published

- libvmod-cachetag `v1.0.0`: annotated tag on `fcc369d23b199cc8e41086f28f2322256a8843d9`, the exact commit the pinned archive digest is a function of. No pin moved to make the tag possible.
- Release URLs and asset digests: see the release-assembly section above and the releases themselves.
