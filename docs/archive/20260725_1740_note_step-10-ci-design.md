# CI design for vcache-packaging (step 10)

Date: 2026-07-25

Status: draft, unreviewed, not yet copied into the repository. Written by a
read-only research agent; the two container/host constraints in the task
("do not run package builds", "do not push or call gh") mean nothing here has
been executed. Every script is a design artifact, not a verified one.

This note is the deliverable the orchestrator asked for: a CI design that
produces clean-room, publishable package artifacts for step 10 of the binary
packaging and distribution plan, checked in at
`libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md`
(hereafter "the plan"), honouring the maintainer's step-10 gate decisions in
`vcache-packaging/docs/20260725_1602_note_step-10-gate-decisions.md`
(hereafter "the gate note").

## 1. The load-bearing requirement: sbuild and Mock are not optional

The task brief floated the possibility that the existing pinned-digest Docker
lanes might already satisfy the plan's clean-room intent. They do not, and the
repository says so itself, twice, and the maintainer has since made the
requirement explicit. Three independent sources agree:

**The plan** (Phase 3, Debian and Ubuntu):

> Use: a Debian source package; modern debhelper; `dpkg-buildpackage`;
> **`sbuild` or `pbuilder` for clean builds**; `dh_shlibdeps` for ELF
> dependency generation; `lintian` for package checks.

**The plan** (Phase 3, RHEL-family RPM):

> Use: a proper `.spec`; a source RPM; `rpmbuild`; **Mock with AlmaLinux or
> Rocky Linux chroots**; `rpmlint`; native debuginfo generation.
>
> Mock creates a minimal RPM buildroot and helps expose undeclared
> `BuildRequires`.

**The repository's own recipes already record this as a known gap**, not a
design choice:

> `recipes/debian-13/README.md`, "Deferred": "`sbuild`/`pbuilder` clean-room
> builds. This lane installs build dependencies into a fresh container
> instead, which resolves `Build-Depends` from `debian/control` via
> `apt-get build-dep ./` but does not enforce a minimal buildroot the way
> `sbuild` does."

> `recipes/el9/README.md`, "Deferred": "Mock clean-room builds, SELinux
> enforcing verification, x86_64, signed-repository behaviour, and a
> transaction test against a live daemon with the VMOD mapped are all CI
> work."

> `recipes/el9/vinyl-cache.spec.in` header comment (this one names the exact
> invocation): "Intended build venue: Mock with an AlmaLinux or Rocky Linux 9
> chroot, i.e. `mock -r alma+epel-9-x86_64 --buildsrpm --spec
> libvmod-cachetag.spec --sources .` / `mock -r alma+epel-9-x86_64 --rebuild
> libvmod-cachetag-*.src.rpm`. Mock supplies the minimal buildroot that
> exposes undeclared `BuildRequires`; a host rpmbuild does not and must not be
> treated as a clean build. Mock needs privileges Docker on macOS cannot
> sensibly grant, so the local process proof uses rpmbuild inside a fresh
> almalinux:9 container instead, **and Mock stays a CI requirement.**"

**The gate note** then closes the question for step 10 specifically:

> "Step 10 proceeds as: CI clean-room builds first (**Debian 13 amd64 sbuild,
> EL9 x86_64 Mock** — laptop-built artifacts are never published), then the
> draft release, then a real experimental pre-release is allowed, on the
> understanding that test releases remain deletable."

So this is not a case where the plan's language is loose enough to accept the
pinned-digest Docker container as "close enough" — the repository's authors
already considered and explicitly rejected that reading, twice, in writing,
before this task started, and the maintainer's gate decision names the exact
tools. **This design uses `sbuild` for Debian 13 amd64 and `Mock` for EL9
x86_64 as the actual package-build step in CI.** What the existing container
lanes get right — the pinned-digest buildroot, the token substitution, the
ABI/hardening assertions, the lint pass, the fresh-container installed-package
smoke, the mismatch-fixture and transaction matrices — is reused unchanged,
because none of that logic depends on which tool ran `dpkg-buildpackage`.

## 2. What "reuse, don't duplicate" means concretely

Every one of the existing container scripts falls into one of two categories:

1. **Build the package** — `stage-vinyl.sh` and `stage-cachetag.sh`'s
   `dpkg-buildpackage -us -uc` line (Debian); `container/build.sh`'s
   `rpmbuild --rebuild ...` lines (EL9). This is the one piece CI must not
   reuse as-is, because it runs in the same persistent container across
   stages and resolves `Build-Depends`/`BuildRequires` from whatever is
   already installed rather than from a fresh minimal root, and it does not
   invoke `sbuild`/Mock at all.
2. **Everything else** — assembling and substituting the pinned source
   (`assemble-source.sh`, `substitute_recipes`), lint (`stage-lint.sh`,
   `container/build.sh`'s `stage_lint`), the installed-package smoke
   (`stage-smoke.sh`, `smoke/smoke.sh`), the mismatch fixture and transaction
   matrices, the checksums stage. None of this cares how the `.deb`/`.rpm`
   files it consumes were produced; it only reads `dist/debian-13/*.deb` or
   `dist/el9/packages/*.rpm`.

The design therefore inserts CI-only scripts that replace category 1 and
leave category 2 completely untouched, provided the CI build populates
`dist/debian-13/` and `dist/el9/packages/` with the same file set the
existing stages would have produced (the same `.deb`/`.dsc`/`.changes`/
`.buildinfo`/`.tar.*` names for Debian; the same `.rpm`/`.src.rpm` names for
EL9). Everything downstream — lint, smoke, mismatch, transactions — is called
exactly as documented in `recipes/*/README.md`, with no forked copy.

Two deliberate exceptions, flagged rather than hidden, one per lane:

- **Debian**: `stage-vinyl.sh` and `stage-cachetag.sh` interleave the build
  command with the ABI/hardening assertions (`dpkg-deb -f`, `readelf`) in the
  same script. Those assertions are generic post-build checks, not part of
  "how to invoke dpkg-buildpackage", so `scripts/ci/debian13/assert-packages.sh`
  re-implements exactly that portion, attributed by comment to the file it
  mirrors.
- **EL9**: `container/build.sh`'s `stage_vinyl` and `stage_cachetag` interleave
  three things that are not separable by simply skipping a step: generating
  the substituted `.spec` from the `.spec.in` template, staging source files
  (`find-provides`, systemd units, the cachetag tarball) into the `SOURCES`
  directory, and the `rpmbuild -bs`/`--rebuild` calls themselves. Only the
  last of those three is being replaced by Mock; the first two — including,
  for cachetag, reading `vmoddir`/`vrt`/`abi` back out of the *installed*
  `vinyl-cache-devel` package before substituting its spec — have to be
  reproduced against Mock's install/chroot primitives instead of `dnf
  install`/`pkg-config` in an ad hoc container. `scripts/ci/el9/container-mock.sh`
  does this, attributed by comment to the exact lines of `container/build.sh`
  it mirrors. `stage_report` and `stage_lint`, by contrast, already operate
  purely on already-built RPMs and are called unmodified (§5).

This is the design's honest cost of "the maintainer's gate decision names a
specific tool that the existing recipes were never wired for": some of what
each recipe currently does is genuinely inseparable from *how* it built the
package, and has to be re-expressed once, not merely reused. §7 recommends
factoring both recipes so a future change to spec/control generation only
has to happen in one place.

## 3. Job graph

```text
registry-selftest            (~1 min, every PR/push)
  |
  v
cachetag-source-archive       (~20-60 min estimate, §6)
  |
  +----------------------+
  v                       v
debian-13 (sbuild)      el9 (Mock)
  |                       |
  v                       v
checksums-summary  (aggregates both lanes' SHA256SUMS into one job summary)
```

Separate workflows for the heavier, less-frequent tiers the plan's "Tiered CI
workflow" section describes:

```text
nightly-transactions.yml   (schedule: nightly, or workflow_dispatch)
  debian-13-full   build.sh (all stages) + mismatch-fixture.sh + transactions.sh
  el9-full         build.sh (all stages) + mismatch-fixture.sh + transactions.sh

release-draft.yml         (workflow_dispatch only)
  registry-selftest --require-releasable
  cachetag-source-archive
  debian-13   (build + lint + smoke, full)
  el9         (build + lint + smoke, full)
  assemble-draft-release   (release-manifest.json, aggregate SHA256SUMS,
                             `gh release create --draft`)
```

`ci.yml` (PR + push to `main`) intentionally does **not** run the mismatch
fixture or transaction matrix on every PR: each transaction scenario is a
throwaway-container apt/dnf run and the full matrices (16 Debian scenarios,
17 EL9 scenarios) are the plan's "merge to main and nightly" tier, not its
"every pull request" tier. `ci.yml` also does not assemble or publish a
release; that is `release-draft.yml`, gated to `workflow_dispatch` and a
protected environment, matching the gate note's "CI clean-room builds first,
then the draft release" sequencing and the plan's "keep release credentials
out of pull-request workflows."

## 4. sbuild design (Debian 13 amd64)

sbuild needs a chroot. The existing lane already pins the buildroot by
digest — `IMAGE_REF=debian:trixie`, `IMAGE_DIGEST=sha256:fac46bff...` in
`recipes/debian-13/build.sh` — so the design derives the sbuild chroot
**from that same pinned image** rather than introducing a second, unrelated
pin (a `debootstrap`/`mmdebstrap`-built chroot from a Debian mirror snapshot,
which is the more common sbuild setup but would pin the buildroot by mirror
timestamp instead of by the digest the rest of the lane already trusts):

1. `docker create --platform linux/amd64 "$IMAGE" true` against the pinned
   `debian:trixie@sha256:...` reference, then `docker export <cid>` into a
   plain directory. This is the same operation `assemble-source.sh` already
   depends on implicitly (running commands inside a container instantiated
   from that exact digest); exporting its filesystem is not a new trust
   boundary.
2. Use sbuild's **unshare** chroot backend (`--chroot-mode=unshare`) pointed
   at that directory. Unshare mode needs no `schroot` configuration file and
   no host-level chroot registration, which is what makes it usable inside an
   ephemeral GitHub-hosted runner without needing systemd/schroot state to
   persist; this is the same mechanism Debian's own Salsa CI pipeline uses
   for exactly this reason.
3. `sbuild` resolves `Build-Depends` via `apt` **inside the unshared chroot**
   at build time, against Debian's normal archive — a fresh, minimal
   resolution per build, which is precisely the "undeclared Build-Depends"
   gate the plan and the recipe's own deferred-work note ask for and that the
   current `apt-get build-dep -y ./` run in a shared, cumulative container
   cannot provide.
4. `libvmod-cachetag`'s `Build-Depends: vinyl-cache-dev (= ...)` (see
   `libvmod-cachetag/packaging/debian/control:10`) is not on any Debian
   mirror. sbuild's `--extra-package=PATH` flag installs an arbitrary local
   `.deb` into the chroot before resolving `Build-Depends`, which is the
   documented mechanism for exactly this "build against a package I just
   built" situation. The Vinyl lane runs first and its `.deb`s become the
   cachetag lane's `--extra-package` arguments.
5. Source packages are produced with `dpkg-buildpackage -S -us -uc -d` from
   the already-assembled source trees (`work/build/vinyl-cache-<uv>`,
   `work/build/libvmod-cachetag-<version>`, both produced unchanged by
   `recipes/debian-13/build.sh source`), then handed to
   `sbuild --dist=trixie --arch=amd64 --chroot-mode=unshare --chroot=<dir> ... *.dsc`.
6. `sbuild`'s output directory is copied verbatim into `dist/debian-13/`
   under the same names `stage-vinyl.sh`/`stage-cachetag.sh` already produce
   (`vinyl-cache_<ver>_amd64.deb`, etc.), so `stage_lint` and `stage_smoke`
   run unmodified.
7. `scripts/ci/debian13/assert-packages.sh` (the one intentional duplicate,
   §2) re-runs the `Provides: vinyld-abi-...`/`vinyld-vrt`, exact-Depends, and
   `readelf` hardening checks against the sbuild-produced `.deb`s.

## 5. Mock design (EL9 x86_64)

The RPM spec file already names the exact invocation to use
(`libvmod-cachetag.spec`'s header, quoted in §1): `mock -r
alma+epel-9-x86_64`. That config ships in `mock-core-configs` on Fedora/EL
hosts. GitHub's `ubuntu-latest` runner is Debian-family, so this design runs
the Mock stage inside a small **privileged** container built from a
Fedora/EL base that already has `mock` and `mock-core-configs` packaged
(`dnf install mock mock-core-configs`), rather than trying to get Mock's
bubblewrap/nspawn isolation working directly on the Ubuntu host. `--privileged`
is available to GitHub-hosted Linux runners; it is exactly the privilege the
same spec-file comment says "Docker on macOS cannot sensibly grant" but a
Linux CI runner can, which is why the comment calls this "a CI requirement"
rather than "impossible."

1. `mock -r alma+epel-9-x86_64 --init` once, to materialize the shared build
   root.
2. Vinyl: `--buildsrpm --spec vinyl-cache.spec --sources SOURCES/`, then
   `--rebuild <srpm>`. Same source assembly as the container lane
   (`container/build.sh`'s `stage_source`), reused unchanged — it is pure
   `git archive`/`tar` work, not a build.
3. Cachetag needs `vinyl-cache-devel = <version>` installed in the **same**
   root before its `Build-Depends` can resolve. Mock's `--install PATH...`
   installs local RPMs into an already-initialized root without tearing it
   down, which is the documented pattern for chained "build against what I
   just built" package sets: `mock -r alma+epel-9-x86_64 --no-clean --install
   vinyl-cache-<evr>.rpm vinyl-cache-devel-<evr>.rpm`.
4. The token substitution in `container/build.sh`'s `stage_cachetag` reads
   `vmoddir`/`vrt`/`abi` back out of the *installed* `vinyl-cache-devel`
   package before generating the spec (see `container/build.sh:186-199`).
   With Mock, that read happens with `mock -r alma+epel-9-x86_64 --chroot --
   pkg-config ...` against the just-installed root, rather than a plain `dnf
   install` in an ad hoc container. The `sed` substitution over the
   `.spec.in` template and the `packaging/check-tokens.sh --substituted`
   call are otherwise the same commands, but they have to run as part of
   `scripts/ci/el9/container-mock.sh` rather than inside `stage_vinyl`/
   `stage_cachetag`, because those two functions call `rpmbuild` in the same
   breath and this design does not want that call executing at all (see §2).
5. `--buildsrpm`/`--rebuild` for cachetag, same pattern as Vinyl.
6. Mock's `resultdir` is copied into `dist/el9/packages/` under the same
   names `container/build.sh` already produces, so `stage_report` and
   `stage_lint` run unmodified, and `smoke/smoke.sh` (a genuinely fresh,
   non-privileged container) is unaffected by any of this.

**Digest pinning does not transfer cleanly to Mock.** Mock's premade configs
resolve packages from AlmaLinux's live mirrors by `dnf`, not from a container
image reference, so there is no single digest to pin the way
`IMAGE_REF@IMAGE_DIGEST` pins the Docker lane. The plan's Reproducibility
section anticipates exactly this case and gives the fallback this design
uses instead: *"Pin buildroot repositories or record exact resolved package
versions."* `container/build.sh`'s `stage_deps` already writes
`dnf repoquery --installed --qf '%{name}-%{evr}.%{arch}\t%{from_repo}\n'` to
`logs/buildroot-packages.tsv`; the Mock wrapper does the equivalent inside
the mock root (`mock --chroot -- rpm -qa`) and the CI job uploads it as part
of the target manifest evidence. The registry schema already has a field for
this: `target.build.build_dependencies` ("exactly resolved buildroot
packages"), per `registry/README.md`'s target-manifest table.

## 6. Sibling checkouts and the source archive

### 6.1 `../vinyl-cache`

Not a GitHub repository — `debian/control`/`.dsc` files already in this repo
record its Vcs fields as `https://code.vinyl-cache.org/vinyl-cache/vinyl-cache.git`,
which `actions/checkout` cannot fetch directly (it authenticates against
`github.com`/GHES only). CI clones it with a plain `git`:

```sh
git clone https://code.vinyl-cache.org/vinyl-cache/vinyl-cache.git vinyl-cache
git -C vinyl-cache checkout a90954814766d933a75d4c808c449cb9bc0ae3d3
git -C vinyl-cache submodule update --init bin/vinyltest/vtest2
```

then asserts both commits resolve to exactly the pinned values
(`a90954814766d933a75d4c808c449cb9bc0ae3d3` for the superproject,
`db5ccb4a078da40b3ec1ca3c18bf498bb1520888` for `vtest2`) before anything
downstream runs — the same assertion `recipes/debian-13/build.sh`'s
`stage_source` already performs on a host checkout, reused verbatim. A shallow
`git fetch --depth 1 origin <sha>` is attempted first to bound clone time;
the design does not assume the server supports fetching an arbitrary
non-tip commit shallowly, so a full clone is the documented fallback if that
fails (**unverified in this draft** — I could not test network access to
`code.vinyl-cache.org` from this environment).

### 6.2 `../libvmod-cachetag`

`actions/checkout` against `boffinate/libvmod-cachetag`, `ref:
packaging-plan-implementation`, as the task specifies. **This is a real gap
against the plan's "pin all build inputs" requirement**, flagged rather than
quietly worked around: `recipes/debian-13/build.sh` and `recipes/el9/cohort.env`
pin Vinyl by exact commit (`VINYL_GIT_COMMIT`) and pin the *resulting cachetag
source archive* by digest (`CACHETAG_SOURCE_SHA256`), but nowhere in
`vcache-packaging` is the cachetag **git commit** that produces that archive
itself pinned — only its output digest is. `packaging-plan-implementation` is
a moving development branch, not a tag. Until `libvmod-cachetag` cuts an
annotated `v1.0.0` tag (which the plan's own Phase 0 versioning section
requires before a real release), CI checking out a moving branch is a
narrower but real version of the same problem the plan calls out for the
sibling Vinyl checkout ("do not derive it from a mutable sibling checkout").
See open question in §8.

### 6.3 The cachetag source archive itself

Per the task's instruction to check whether CI needs to build it: **yes,
unavoidably.** `CACHETAG_SOURCE_SHA256` in `recipes/debian-13/build.sh` and
`recipes/el9/cohort.env` names a specific archive
(`c7054e69219ff3c54501d9c68857f2117944c4658db4cb08e2821b09b27821a2`), but the
file that would carry that digest,
`libvmod-cachetag/release/dist/libvmod-cachetag-1.0.0.tar.gz`, is produced by
`scripts/release-source-archive.sh` into a directory whose own
`.gitignore` (written by the script itself) excludes everything in it. It is
not checked into either repository and cannot be assumed to exist on a fresh
CI runner. The `cachetag-source-archive` job therefore:

1. builds `docker/vinyl-cache-ubuntu-build.Dockerfile` from the
   `libvmod-cachetag` checkout (base `ubuntu:26.04`, **not currently pinned by
   digest in that Dockerfile** — see §8),
2. runs `scripts/release-source-archive.sh --vinyl-git ../vinyl-cache --vinyl-ref a90954814766d933a75d4c808c449cb9bc0ae3d3`,
   which performs the full Phase 1 sequence: build the pinned Vinyl, run
   `make distcheck` against it (the complete Default-storage behavioural
   suite, including the documented `pl00007` flake — see §6.4), repack the
   validated archive deterministically, and prove it rebuilds from itself in
   a second fresh container with the autotools toolchain disabled,
3. asserts the produced `libvmod-cachetag-1.0.0.tar.gz` digest equals the
   pinned `CACHETAG_SOURCE_SHA256` **and fails hard, non-negotiably, on any
   mismatch** — this is the literal text of the task's "never auto-update
   pins" constraint, applied to the one place in this pipeline where a freshly
   computed digest and a checked-in pinned digest are compared,
4. uploads the archive (plus its `.sha256` and `.metadata.json` sidecar) as a
   build artifact that the `debian-13` and `el9` jobs download into
   `libvmod-cachetag/release/dist/` before invoking the existing lane
   drivers, so the expensive archive-production step runs exactly once per CI
   run rather than once per lane.

### 6.4 The `pl00007` flake

The plan's "Test flake policy" section requires that this not become "either
a random hard release blocker or an ignored failure," and lists a specific
mitigation: run the case ten times and report the aggregate. `ci.yml`'s
`cachetag-source-archive` job runs the ordinary `distcheck`/`check` targets
and therefore can hit this flake like any other run; this design does **not**
add 10x-copy quarantine handling to that job, because doing so belongs to
`libvmod-cachetag`'s own CI (its VTC generation and the quarantine tooling
described in `docs/20260724_2008_note_step-5-pl00007-quarantine-policy.md`
live in that repository, not this one — `vcache-packaging`'s job only
consumes the archive `release-source-archive.sh` already validates). If this
flake fires inside `vcache-packaging`'s archive job before `libvmod-cachetag`
has quarantined it, the honest failure mode is: the job fails, is rerun once
manually, and the failure is investigated rather than silently retried by
workflow config — no blanket `retry:` step is added here, matching the
plan's explicit "do not use blanket job retries."

## 7. Artifact and checksum layout

Per lane, one artifact matching the existing `dist/<lane>/` layout:

```text
debian-13-packages/
  *.deb *.ddeb *.dsc *.changes *.buildinfo *.orig.tar.* *.debian.tar.*
  SHA256SUMS
  logs/{vinyl,cachetag,lint,smoke}.log
  logs/lintian-tags.txt
  work/target.txt              (resolved arch/multiarch/vmoddir, small, useful evidence)

el9-packages/
  packages/*.rpm packages/*.src.rpm
  SHA256SUMS
  logs/{buildroot-packages.tsv,buildroot-toolchain.txt,package-metadata.txt,
        hardening.txt,rpmlint.log,smoke.log}
```

`checksums-summary` (a small job with no build steps) downloads both
artifacts and writes a combined table to the job summary — one line per
artifact with lane, filename, and digest — so a reviewer can see both lanes'
evidence without opening either artifact. It does not re-derive the plan's
release-asset names (`libvmod-cachetag-1.0.0-1-debian-13-amd64.deb`, per
`registry/README.md`'s "Generated outputs" section) because those names are
generated by `tools/release_tool.py metadata` **from a `candidate` cohort
manifest**, and no such manifest exists yet (§8, first bullet). `ci.yml`
therefore reports native package filenames as built; `release-draft.yml`
is where release-asset naming would apply once a candidate cohort exists.

`release-draft.yml`'s `assemble-draft-release` job additionally synthesizes
`release-manifest.json` from the cohort/target manifests (once real) plus
CI-only facts (`workflow`, `run_id`, `run URL`) — this is exactly what
`registry/README.md`'s "Deliberately not here yet" section says is missing
and where it says that assembly belongs: *"assembled by the release workflow
from these manifests plus CI-only facts ... which cannot be checked in ahead
of the run."* Until a candidate cohort manifest exists, this step falls back
to writing the same fields from the literal pinned values in
`recipes/debian-13/build.sh`/`recipes/el9/cohort.env`, labelled
`"cohort_status": "unassigned-process-proof"` rather than a real cohort id, so
the draft release is still self-describing without inventing a cohort
identity CI has no authority to mint (see §8).

## 8. Deliberately out of scope, and why

Citing the plan directly for each:

- **Signing.** "Keep long-lived native repository signing keys offline, in an
  HSM, or in a narrowly scoped signing service" and "Do not expose repository
  signing keys to pull-request workflows." No signing key exists yet, and the
  gate note's step-10 sequencing ("CI clean-room builds first, then the
  draft release, then a real experimental pre-release") does not mention
  signing before the pre-release. GitHub artifact attestations are explicitly
  a **stable-channel** requirement ("For stable release candidates, generate
  artifact attestations...") and immutable releases likewise ("Enable
  immutable GitHub Releases before declaring the channel stable"), so neither
  is wired into `release-draft.yml`.
- **SBOMs.** "SBOMs, attestations, immutable publication ... may follow
  during hardening, but must be complete before a stable repository channel
  is announced" (Public learning pre-release section) — explicitly not
  required yet.
- **Repository metadata / apt-RPM-Arch-Alpine-FreeBSD repositories (Phase
  6).** "A published GitHub pre-release may precede this phase for manual
  learning." Out of scope for step 10 by the plan's own phase ordering.
- **The mismatch-fixture and transaction matrices on every PR.** These are
  the plan's "merge to main and nightly" tier, not its "every pull request"
  tier (`Tiered CI workflow` section); they run in `nightly-transactions.yml`
  instead, not in `ci.yml`.
- **arm64/aarch64, Ubuntu, EL10, Arch, FreeBSD, Alpine.** "The implementation
  should not enable this entire matrix on day one. Start with Debian 13 amd64
  and EL9 x86_64" (Phase 4). Untouched here.
- **Buddy/Slash packaging.** "The first package cohort is Default-only ...
  do not call Buddy an installed-package feature until an unpatched Slash
  package is built." No Slash package exists; out of scope by definition.
- **SELinux enforcing verification for the EL9 smoke test.** The plan asks
  for it explicitly (Phase 3, RHEL-family RPM section: "Run the
  installed-package test with SELinux enforcing"), and `recipes/el9/smoke/smoke.sh`
  already documents that this needs "a host that can run SELinux enforcing;
  Docker on macOS cannot." **This design could not satisfy it either**:
  GitHub-hosted `ubuntu-latest` runners run an Ubuntu kernel with AppArmor,
  not SELinux, and containers on them do not get an enforcing SELinux
  policy regardless of the base image inside the container. Satisfying this
  needs either a self-hosted EL9 VM/bare-metal runner or nested
  virtualization GitHub-hosted runners do not offer for this purpose. This
  is a genuine, not merely deferred, gap in this draft; see the open
  questions.
- **A `candidate` cohort manifest.** `registry/README.md`: "The first real
  cohort identifier is assigned only once the Vinyl source archive, the
  ordered downstream patch set, and the production build-profile revision are
  pinned" and "must not be derived from a mutable sibling checkout." All
  three inputs are, in fact, already pinned as literal values inside
  `recipes/debian-13/build.sh`/`recipes/el9/cohort.env` (not derived from the
  mutable checkout — they are hardcoded commit/digest/version strings). CI is
  deliberately **not** the thing that mints this manifest: authoring a cohort
  identity is a maintainer decision the tooling only validates, not a build
  output. See open questions.
- **Digest-pinning the `vinyl-cache-ubuntu-build` Dockerfile's base image.**
  `docker/vinyl-cache-ubuntu-build.Dockerfile:1` in `libvmod-cachetag` reads
  `FROM ubuntu:26.04`, unpinned. This repository cannot fix that (it lives in
  the sibling repo and this task is read-only against both), so
  `cachetag-source-archive` records the resolved image ID it actually built
  from (`docker image inspect --format '{{.Id}}'`, which
  `release-source-archive.sh` already captures into its metadata sidecar) as
  the audit trail, rather than silently treating an unpinned `FROM` as if it
  were pinned. Flagged for the maintainer to fix in `libvmod-cachetag`.
- **Pinning the EL9 lane's buildroot image by digest.** `recipes/el9/cohort.env`
  sets `EL9_IMAGE=almalinux:9` with no digest, unlike the Debian lane's
  `IMAGE_REF@IMAGE_DIGEST` pair. This asymmetry predates this design and this
  task cannot edit `cohort.env`; the workflow instead overrides `EL9_IMAGE`
  at the CI level to a `almalinux:9@sha256:...` value the workflow file
  documents how to resolve (`skopeo inspect docker://docker.io/library/almalinux:9`
  or `docker buildx imagetools inspect almalinux:9`), rather than inventing a
  digest this agent cannot verify from this environment. Mock's package
  resolution is unaffected by this pin either way (§5) — this only pins the
  *installation surface* the Mock-privileged wrapper container itself runs
  in, which is a smaller but real thing to pin.

## 9. Runner sizing

Estimates, not measurements — this design was not executed:

- **`cachetag-source-archive`**: the heaviest job. It builds
  `docker/vinyl-cache-ubuntu-build.Dockerfile` (a handful of `apt-get`
  packages on top of `ubuntu:26.04`, a few minutes), then a full Vinyl
  autotools build (`autogen.sh` + `configure` + `make -j$(nproc)`) and a full
  `make distcheck` of libvmod-cachetag's Default-storage VTC suite, then
  repeats a `configure`+`make` from the produced archive in a second
  container to prove it is self-contained. On a 2-core/7GB `ubuntu-latest`
  runner this is plausibly 20-60 minutes; there is no local timing data to
  cite (the workspace's own runbook forbids host-local builds as
  verification, so no comparable host timing exists either, only Docker/
  OrbStack runs, which are not necessarily representative of the GitHub
  runner's CPU class). Recommend an explicit `timeout-minutes: 90` so a hang
  fails loudly rather than burning the job's full 6-hour ceiling, and
  revisiting `ubuntu-latest-4-cores` (a paid larger runner) if repeated real
  runs show the 2-core default is a bottleneck rather than the network/apt
  steps. Disk is very unlikely to be the constraint: Vinyl and cachetag
  themselves are a small C codebase; the dominant consumers of the runner's
  ~14GB free disk are the built Docker image and its layer cache, not the
  build output.
- **`debian-13` (sbuild)**: exporting a `debian:trixie` rootfs (order of
  100-150MB) and building two smallish packages inside an unshare chroot.
  Expect single-digit minutes; `timeout-minutes: 30` as a guard.
  Bandwidth-bound on `apt-get build-dep`'s package resolution more than
  CPU-bound.
- **`el9` (Mock)**: `mock --init` resolving and downloading the AlmaLinux+EPEL
  base package set is the dominant cost (no persistent cache across CI runs
  unless a cache action is added for Mock's root cache directory, which this
  draft does not yet include — worth adding once real timings are known).
  Expect single-digit to low-double-digit minutes; `timeout-minutes: 30`.
- None of the three jobs is expected to need more than the default runner's
  disk; the concern flagged in the task brief (runner disk/time limits for
  "a full autotools build") is real for `cachetag-source-archive` specifically
  and probably not for the two packaging lanes, which consume an
  already-built archive rather than compiling Vinyl's full behavioural test
  suite again.

## 10. Open questions for the maintainer

1. **Should `vcache-packaging` record a pinned `libvmod-cachetag` git commit**
   (analogous to `VINYL_GIT_COMMIT`), rather than CI tracking a moving branch
   (`packaging-plan-implementation`)? This is the one sibling-checkout pin
   this design could not make exact from information available in this repo
   (§6.2). The cleanest fix is probably: once `libvmod-cachetag` tags
   `v1.0.0`, pin that tag/commit here the same way Vinyl's commit is pinned,
   and treat the pre-tag branch tracking as an accepted, temporary gap.
2. **Who mints the first `candidate` cohort manifest, and when?** All three
   digest inputs the cohort identity is computed over
   (`vinyl.source_sha256`, `patches: []`, `build_profile.revision`) are
   already pinned as literal values in the two lane drivers. Nothing in this
   design manufactures a cohort id from them, because `registry/README.md` is
   explicit that this is a maintainer-authored file, not a generated one, and
   because the plan forbids deriving the identifier from a mutable checkout
   (which is a different concern, but this design errs toward "do not let CI
   silently decide identity questions" as the safer reading). Until this
   exists, `release-draft.yml` cannot produce plan-conformant release-asset
   filenames (`libvmod-cachetag-1.0.0-1-debian-13-amd64.deb`) and falls back
   to native package filenames plus a `cohort_status` label explaining why.
3. **Is the EL9 SELinux-enforcing smoke test acceptable to leave unsatisfied
   for the public learning pre-release**, given GitHub-hosted Linux runners
   cannot provide an enforcing SELinux kernel? If not, this needs a
   self-hosted EL9 runner (VM or bare metal), which is a real infrastructure
   commitment beyond "write a workflow file" and should be a maintainer
   decision, not something this draft should quietly assume either way.
4. **Should `docker/vinyl-cache-ubuntu-build.Dockerfile`'s `FROM ubuntu:26.04`
   be pinned by digest** to satisfy "pin distro container images by digest"
   for the archive-production job the same way the two packaging lanes
   already are? This lives in `libvmod-cachetag`, not this repository, so
   this design can only flag it, not fix it.
5. **Is a moving `almalinux:9` tag acceptable for the Mock-runner container**
   (as opposed to the actual EL9 package-resolution surface, which is
   necessarily mirror-resolved rather than digest-pinned per §5)? This design
   proposes pinning it at the CI-workflow level (not in `cohort.env`, which
   this task cannot edit) but does not have a verified digest to put there
   (§8, last bullet) — needs a maintainer or a follow-up run to resolve and
   record one.
6. **Mock root caching across CI runs.** Not attempted in this draft. If
   `mock --init`'s package resolution turns out to dominate the EL9 job's
   wall time (§9), a `actions/cache` step keyed on the resolved
   `alma+epel-9-x86_64` package set (or a scheduled warm-cache job) would be
   the next thing to add, but doing so before there is real timing data would
   be premature optimization of a design nobody has run yet.
7. **`nightly-transactions.yml`'s trigger cadence and cost.** The Debian
   matrix alone is 16 scenarios, each a throwaway container running a full
   `apt`/`apt-get` transaction; EL9's is 17. Nightly is what the plan asks
   for ("Run on merge to the main branch and nightly"), but the maintainer
   should confirm that cadence is affordable on whatever GitHub Actions plan
   this repository runs under, since neither this design nor the source
   material puts a cost figure on it.

## 11. Files in this draft

```text
workflows/ci.yml                       registry-selftest, source-archive, both lanes, checksums summary
workflows/nightly-transactions.yml     mismatch-fixture.sh + transactions.sh, both lanes, scheduled
workflows/release-draft.yml            workflow_dispatch only; full rebuild + gh release create --draft

scripts/ci/lib/common.sh               shared sh helpers: pinned-value assertions, sha256, sibling checkout
scripts/ci/source-archive.sh           builds the vinyl-cache-ubuntu-build image, runs
                                        release-source-archive.sh, asserts the pinned digest
scripts/ci/debian13/make-chroot.sh     docker export of the pinned debian:trixie digest into an
                                        sbuild unshare chroot directory
scripts/ci/debian13/sbuild-vinyl.sh    dpkg-buildpackage -S, then sbuild, for the Vinyl source package
scripts/ci/debian13/sbuild-cachetag.sh   same, with --extra-package pointed at the Vinyl .debs just built
scripts/ci/debian13/assert-packages.sh   the one intentional duplicate (§2, §4.7): ABI/hardening
                                        assertions mirrored from stage-vinyl.sh/stage-cachetag.sh
scripts/ci/el9/mock-build.sh           mock --init / --buildsrpm / --rebuild / --install sequence for
                                        both Vinyl and cachetag, sharing one mock root
```

None of these have been executed. They are intentionally written at "a
careful engineer's first draft" fidelity rather than polished, because the
next real step is running them in an actual GitHub Actions job and fixing
whatever the first real failure turns out to be — which this task's
constraints (no builds, no `gh`, no network execution here) make impossible
to do from within this session.
