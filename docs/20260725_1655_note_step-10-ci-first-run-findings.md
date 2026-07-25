# Step 10 CI: first-run findings

Date: 2026-07-25

Status: the CI workflows landed in dba2228 have now been executed for the first time. Run 1 ([30163781791](https://github.com/boffinate/vcache-packaging/actions/runs/30163781791)) got exactly one job green and stopped at a blocker that no amount of workflow fixing can clear. This note records what was measured, including the things that were wrong in the draft and are now fixed, so the next session does not rediscover them.

## Run 1 result

| job | result | wall time |
| --- | --- | --- |
| registry selftest and validate | success | 7s |
| build and pin the cachetag source archive | failure | 18s |
| Debian 13 amd64 (sbuild) | skipped (needs) | -- |
| EL9 x86_64 (Mock) | skipped (needs) | -- |
| combined checksum summary | skipped (needs) | -- |

The registry job is genuinely green: `tools/release_tool.py selftest` and `validate` both pass on a GitHub runner against a fresh `boffinate/libvmod-cachetag` checkout, so the `configure.ac` cross-check works with the sibling checkout laid out as `$GITHUB_WORKSPACE/libvmod-cachetag`.

## Blocker: the pinned Vinyl commit is not published

`ci_checkout_vinyl_cache` failed with `fatal: unable to read tree (a90954814766d933a75d4c808c449cb9bc0ae3d3)`. The cause is not a script bug:

- `git ls-remote https://code.vinyl-cache.org/vinyl-cache/vinyl-cache.git` advertises no ref that reaches `a909548147`.
- A full anonymous clone of that remote does not contain the object at all (`git cat-file -t` fails; the clone's HEAD is `655c988a2`, the tip of `main`).
- In the maintainer's local checkout the commit is 17 commits ahead of `origin/main`, on the local-only branch `perf/tag-vmod-baseline` (merge base `25761f8505`). It exists nowhere else, including the `peter/vinyl-cache.git` fork.

So every packaging lane is pinned to a Vinyl revision that only exists on one workstation. CI cannot fetch it, and re-pinning to something the remote does have would silently change the ABI token, the cohort identity, and both source-archive digests. This is a maintainer decision, not a CI fix: either publish the branch carrying `a909548147`, or re-pin deliberately and update `recipes/debian-13/build.sh`, `recipes/el9/cohort.env`, `scripts/ci/debian13/pinned.sh` and `.github/workflows/*.yml` in the same change.

Secondary measurement while diagnosing: `code.vinyl-cache.org` serves this repository over **dumb HTTP** (`/info/refs?service=git-upload-pack` answers `content-type: text/plain`). Any `git fetch --depth 1 <sha>` against it dies with "dumb http transport does not support shallow capabilities", so the draft's shallow-first optimisation could never have fired, and its failure hid the real error behind git's `unable to read tree` message. A full clone takes ~8s; the shallow path is now gone and the missing-object case names itself.

## Bugs found in the sbuild lane, and how they were verified

The Debian lane never ran in CI (its job was skipped), so these were verified out-of-band in a `debian:trixie` container with sbuild 0.89.3+deb13u4 -- building `hello` from a `.dsc` as an unprivileged user, ending in `Status: successful`. Each item below is a thing the draft got wrong, with the evidence:

1. **The unshare chroot is a tarball, not a directory.** `sbuild(1)`: "With the unshare chroot mode, if this option is a path, then it specifies the location of the chroot tarball directly." Given a directory path, `Sbuild::ChrootUnshare` treats it as a missing tarball and tries to `mmdebstrap` a live one -- which would have replaced the digest-pinned buildroot with an unpinned one without saying so. `make-chroot.sh` now writes `docker export` straight to a tarball.
2. **sbuild must not run under `sudo`.** `Sbuild::Utility::read_subuid_subgid` looks up the *invoking* user in `/etc/subuid`/`/etc/subgid` and aborts with "invalid idmap" when absent; a GitHub runner has an entry for the runner user and none for root. The workflow no longer wraps these steps in `sudo`; the scripts elevate only `apt-get` and refuse to run as root.
3. **`-us -uc` are not sbuild options.** sbuild parses `-s` as `--source`, which then conflicts with `--no-source`, and rejects `-u` outright: `E: Error parsing command-line options`. sbuild passes `-us -uc` to `dpkg-buildpackage` itself.
4. **sbuild builds from the `.dsc`, not the `*_source.changes`.** Handed the `.changes` that `dpkg-buildpackage -S` writes beside it, sbuild fails with `E: Failed to fetch source files` (exit 3).
5. **Host packages the draft did not install.** `debhelper` (the host-side `dpkg-buildpackage -S` runs `debian/rules clean`, and both recipes are dh-based: without it, `make: dh: No such file or directory`), `iproute2` (sbuild runs `ip link set lo up` in the build namespace; without it the build aborts), and `uidmap`. `apt-get install --no-install-recommends sbuild` pulls in none of them.

One symptom seen in the probe is an artefact of the probe, not a CI issue: `cannot bind-mount /dev/console as it does not exist outside the chroot`, because the probe ran sbuild inside a container. A runner VM has `/dev/console`.

## EL9 lane

Unexecuted; its job was skipped too. Two things were checked statically and hold: `EL9_IMAGE` in `ci.yml` matches the authoritative pin in `recipes/el9/cohort.env`, and the report/lint step's mounts (`recipes/el9:/recipes:ro`, `dist/el9:/out`, `-w /out`) match the contract `recipes/el9/container/build.sh` documents and `stage_report`/`stage_lint` actually use. The `sudo` was dropped from the Mock step for the same ownership reason as above: root-owned `dist/el9/` would break the later non-privileged `--smoke-only` and artifact steps.

## Not yet measured

The design's cost estimates remain guesses: the source-archive job (budgeted 90 minutes) died after 18 seconds, so nothing is known about a full Vinyl build plus `make distcheck` on a GitHub runner, and neither packaging lane has produced a package in CI. `CACHETAG_SOURCE_SHA256` (`c7054e69...`) is still unverified against a clean-room-produced archive -- the run that would test it never reached the archive step.

## Runs 2-5, after the Vinyl re-pin

The Vinyl blocker above was resolved by the maintainer re-pinning to published upstream `25761f8505` (ca16464). Three more runs followed. The archive job now works and the numbers below are the first real measurements this pipeline has ever produced.

| run | archive job | outcome |
| --- | --- | --- |
| [30165677234](https://github.com/boffinate/vcache-packaging/actions/runs/30165677234) (re-pin) | 6m17s | full Vinyl build + cachetag `distcheck`, 53/53 VTCs PASS, then failed cleaning up |
| [30165796047](https://github.com/boffinate/vcache-packaging/actions/runs/30165796047) | 6m15s | same failure, same cause (old release script) |
| [30165999231](https://github.com/boffinate/vcache-packaging/actions/runs/30165999231) | 6m16s | reached the archive digest gate and stopped there |

**The 90-minute timeout on that job is roughly 14x too generous.** A GitHub runner does the whole thing -- clone Vinyl, build the harness image, configure and build Vinyl, build cachetag, `make distcheck` over the 53-VTC Default-storage suite, rebuild from the produced archive in a fresh container and run the suite again -- in a bit over six minutes, consistently, three runs in a row. The Debian and EL9 lanes have still never executed: they are `needs:`-gated behind this job, so their 30-minute budgets remain guesses.

### Root-owned work directories (fixed, libvmod-cachetag fcc369d)

Runs 2 and 3 failed *after* a completely successful archive build, on `rm: cannot remove .../release/dist/work/run1/content-digests.txt: Permission denied`. Every container `release-source-archive.sh` starts runs as root, so what it writes into the bind-mounted work directory is root-owned on a Linux host and the host cannot delete it. macOS hides this: the Docker VM maps container-root writes back to the invoking user, which is why every local run of that script since it was written has been cleaning up files it only appeared to own. The same class of bug bit the Debian lane in this repository (61c7c67): `build.sh source` assembles `dist/debian-13/work/` inside a container, and sbuild has to run as an ordinary user, so the tree has to be handed over before `dpkg-buildpackage -S` writes to it.

This is worth generalising: **any host-side mutation of a directory a container has written to is a latent CI failure that macOS will not reproduce.**

## Stop condition: the pinned cachetag archive digest cannot be reproduced

Run 30165999231 produced `a262ac7a74a1464d4c0a4cc6f072ea04a77ff660b25bf0befd32dc63c18fb329`; `CACHETAG_SOURCE_SHA256` pins `c7054e69219ff3c54501d9c68857f2117944c4658db4cb08e2821b09b27821a2`. The pin was not touched. The archive's own metadata sidecar, still on the maintainer's disk at `libvmod-cachetag/release/dist/libvmod-cachetag-1.0.0.metadata.json`, says why:

```json
"release_stamp": "dev-build-from 0d3c9fdb9e39e65f86b6af9bc6935ca016cff7f8 +dirty",
"cachetag": { "git_commit": "0d3c9fdb...", "worktree_dirty": true },
"vinyl_input": { "git_commit": "a90954814766d933a75d4c808c449cb9bc0ae3d3" }
```

Three things follow, and the first is fatal on its own:

1. **The pinned archive was built from a dirty worktree.** It does not correspond to any committed state of `libvmod-cachetag`, so no build from any commit -- on any architecture -- can reproduce it.
2. **It is seven commits stale.** `0d3c9fdb..HEAD` includes `src/vmod_cachetag.vcc` and `acinclude.m4`, both of which are shipped in the archive (`src/` is 96 of its 199 members). The content legitimately changed.
3. **It was built against the old Vinyl pin** (`a909548147`, the one the packages must not ship).

So the mismatch is explained without invoking any architecture effect, and the open amd64-vs-arm64 determinism question is *not* answered by it: that question cannot even be tested until the pin is re-derived from a clean, committed tree. Note also that the archive does not embed the cachetag commit id -- `src/vmod_vcs_version.txt` is written for the compiled VMOD's `.vmod_vcs` ELF section and is neither in `EXTRA_DIST` nor shipped by `make dist` -- so a stable digest across commits that touch nothing shipped is still expected.

One thing the runs do settle: the amd64 archive is reproducible run to run. Runs [30165999231](https://github.com/boffinate/vcache-packaging/actions/runs/30165999231) and [30166321207](https://github.com/boffinate/vcache-packaging/actions/runs/30166321207), on two different ephemeral runners, both produced `a262ac7a74a1464d4c0a4cc6f072ea04a77ff660b25bf0befd32dc63c18fb329` from the same cachetag commit. What remains untested is whether an amd64 archive and an arm64 archive of the *same* clean commit agree.

The fix is a maintainer decision, and it is the same decision as DESIGN.md open question #1: pin `CACHETAG_REF` to an exact commit, produce the archive from that clean commit, and record the digest it computes. Pinning a digest while CI tracks a moving branch cannot work, because every commit that touches a shipped file invalidates the pin by design.
