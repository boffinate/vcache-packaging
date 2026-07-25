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
