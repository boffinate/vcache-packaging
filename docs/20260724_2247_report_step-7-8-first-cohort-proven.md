# Steps 7–8: first coordinated Vinyl/cachetag cohort proven on both first-milestone lanes

Date: 2026-07-24

Related plan: libvmod-cachetag `docs/20260724_1526_plan_binary-packaging-and-distribution.md` (implementation-order steps 7–9). Lane detail: [Debian 13 note](20260724_2231_note_step-7-8-debian-13-lane.md), [EL9 note](20260724_2240_note_step-7-8-el9-lane.md), [step-7a scaffold note](20260724_2138_note_step-7a-repo-scaffold-and-registry-move.md).

## What was proven

Both first-milestone lanes built the coordinated cohort end-to-end from the same pinned inputs — Vinyl commit `a90954814766d933a75d4c808c449cb9bc0ae3d3` (snapshot version `9.0.0~git20260613.a909548147`) and the canonical cachetag archive `libvmod-cachetag-1.0.0.tar.gz` (sha256 `c7054e69…`, dev-build-from cachetag `0d3c9fd`) — in clean containers, with the cachetag package built against the *installed* Vinyl development package, and passed the plan's 11-step installed-package smoke in fresh containers.

The exact-ABI dependency chain is load-bearing on both package managers: Debian's `vinyld-abi-<hash>` / `vinyld-vrt (= 23.0)` provides and EL9's `vinyld(abi)(aarch-64) = <hash>` / `vinyld(vrt) = 23.0` are what resolve the cachetag package, and both apt and dnf refuse it when the provide is absent or wrong. VRT is 23.0 on both lanes. Debian package payloads were bit-identical across two independent full runs.

## Caveats on what "proven" means

These are arm64/aarch64 process-proof builds on the development machine. The milestone's release targets (Debian 13 **amd64**, EL9 **x86_64**) are untested until CI exists, and container `dnf`/`apt` buildroots are fatter than sbuild/Mock clean-rooms, so undeclared build dependencies would not have been caught. Deferred to CI, per lane notes: sbuild/pbuilder and Mock clean-rooms, SELinux enforcing checks, `annocheck` against installed debuginfo, mismatch-fixture and upgrade-transaction matrices, the full behavior suite against the hardened package builds, and the x86_64/amd64 builds themselves.

## Decisions needing the maintainer

- **EL9 libunwind**: `libunwind.so.8` is in neither BaseOS nor AppStream, so the runtime package currently requires EPEL. Keep `--with-unwind` (production backtraces, matching the cachetag harness production profile) and accept the EPEL requirement, or build `--without-unwind` for EL9? Settle before publication.
- **Maintainer identity**: packages carry deliberately undeliverable placeholder addresses (`packaging@vinyl-cache.example` / `.invalid`) until plan step 2's security-owner decision is confirmed. Publication is blocked on a real contact.
- **Upstream nit fixed in cachetag** (commits `ad0df15`, `fa51946` on `packaging-plan-implementation`): man section 4→3 in the VCC, the `purge_header()` USAGE example, and the scaffolding defects found by the first real builds.

## Registry follow-up

The lane notes record everything a real target manifest needs (resolved build deps and supplying repos, effective flags, vmoddir per distro, artifact digests). The registry templates were deliberately not filled: the cohort inputs are still a dev snapshot of a mutable checkout, and the first real cohort id waits for a pinned Vinyl release input per the plan. When CI produces the first x86_64/amd64 builds from immutable inputs, fill `registry/targets/` from these recorded values.

## Next per the plan

Step 9 remainder (mismatch fixture, apt/dnf transaction matrix, SELinux/Mock/sbuild in CI), then step 10 (internal draft GitHub release → experimental pre-release with cohort manifests, checksums, and the test report). Publication additionally blocked on the step-2 maintainer confirmations above.
