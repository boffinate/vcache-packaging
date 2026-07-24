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

## Deferred

Mock clean-room builds, SELinux enforcing verification, x86_64, the mismatch fixture, and the dnf upgrade-transaction matrix are all CI work. See the session note in `../../docs/`.
