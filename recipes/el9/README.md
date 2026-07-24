# EL9 lane

RPM recipes and a container-driven build for the coordinated Vinyl Cache 9 + cachetag package cohort on an EL9-compatible distribution.

## Status

Process proof, not a release. The lane builds end to end and its installed-package smoke test passes, on **aarch64**, from a **local Docker container on Apple Silicon**. Authoritative x86_64 release artifacts come from CI; nothing produced here is publishable, and the plan is explicit that a developer laptop is not a release build venue.

## Run it

```sh
recipes/el9/build.sh                        # everything, then the smoke test
recipes/el9/build.sh --stages "vinyl lint"  # a subset of the container stages
recipes/el9/build.sh --smoke-only           # re-run the smoke against existing packages
recipes/el9/build.sh --list-files           # tolerate unpackaged files, dump the buildroot

recipes/el9/mismatch-fixture.sh             # build the synthetic mismatched candidate
recipes/el9/mismatch-fixture.sh --check-reproducible
recipes/el9/transactions.sh                 # the whole upgrade-transaction matrix
recipes/el9/transactions.sh --no-prep upgrade distro-sync   # named scenarios only
```

Output lands in `dist/el9/` (git-ignored): `packages/` holds the RPMs and SRPMs, `logs/` the build, lint, hardening and smoke transcripts, and `SHA256SUMS` the digests.

Everything runs inside `almalinux:9`. The host contributes the Docker daemon, the pinned source checkouts, and nothing else — no package is installed on the host and nothing is compiled there.

## Layout

```text
build.sh                host driver: starts the containers, nothing more
cohort.env              the pinned inputs; the only place identity values live
container/build.sh      in-container stages: deps, source, vinyl, cachetag, report, lint
vinyl-cache.spec.in     the Vinyl Cache 9 spec template
find-provides           the ABI provider generator for the runtime package
systemd/                unit files, sysusers.d, tmpfiles.d, logrotate, vinylreload
smoke/                  the installed-package smoke test, its VCL and its backend
mismatch-fixture.sh     host driver for the synthetic mismatched-candidate fixture
mismatch/               the fixture spec template and its in-container builder
transactions.sh         host driver for the upgrade-transaction matrix
transactions/           the scenario base image, local repositories, and scenarios
```

## What the packages promise

The runtime package publishes the two capabilities every strict-ABI VMOD depends on, architecture-qualified:

```text
vinyld(abi)(aarch-64) = a90954814766d933a75d4c808c449cb9bc0ae3d3
vinyld(vrt)(aarch-64) = 23.0
```

The ABI token is the pinned Vinyl commit id, because Vinyl bakes `"<PACKAGE_STRING> <VCS_Version>"` into `include/vmod_abi.h`. `vinyl-cache-devel` requires the exact matching runtime, `%{version}-%{release}` and arch-qualified, and the cachetag package requires the exact `vinyld(abi)` above. That dependency is not decorative: the smoke test asserts that `dnf` refuses to install cachetag with no runtime present, naming `vinyld(abi)` as the unsatisfied capability.

## Two things worth knowing before changing anything here

**The source export needs its ABI headers injected.** `git archive` drops `.git`, and Vinyl's `include/generate.py` responds to a missing `.git` by writing the literal string `NOGIT` into `VCS_Version`. It does not fail. A build from a naive `git archive` export therefore produces a perfectly well-formed package advertising `vinyld(abi) = NOGIT`. `container/build.sh` synthesises `include/vcs_version.h` and `include/vmod_abi.h` from the pinned commit, exactly as `make dist` would have shipped them, and `%prep` refuses to proceed without them.

**The runtime package needs EPEL.** `vinyld` is built `--with-unwind` for production panic backtraces, and `libunwind.so.8` is in neither EL9 BaseOS nor AppStream. The alternative — `--without-unwind`, falling back to the glibc backtrace — is the thing to reconsider before this lane is published, but the flag is now explicit in the spec rather than a silent function of what happened to be in the buildroot.

## Upgrade transactions

`mismatch-fixture.sh` builds a synthetic mismatched candidate Vinyl pair by re-wrapping the baseline cohort's own payload under a higher, obviously synthetic version-release and a different `vinyld(abi)` hash. `transactions.sh` then runs the plan's dnf transaction list against it, one fresh container per scenario, and records what the resolver actually did.

Two commands, and only two, were found to remove an installed VMOD: `dnf install --allowerasing vinyl-cache-<version>` and `dnf upgrade --allowerasing <package>`. Whole-system `dnf upgrade`, `--best`, `--nobest`, `distro-sync` and even `distro-sync --allowerasing` never did. A plain `dnf upgrade` does not skip the update as one might expect — with the EL9 default `best=True` it fails the whole transaction. The full table, the same-ABI-string result, and the `versionlock` incident-response procedure are in the session note in `../../docs/`.

## Deferred

Mock clean-room builds, SELinux enforcing verification, x86_64, signed-repository behaviour, and a transaction test against a live daemon with the VMOD mapped are all CI work. See the session notes in `../../docs/`.
