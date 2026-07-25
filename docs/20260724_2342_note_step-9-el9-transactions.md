# EL9 step 9: the synthetic mismatch fixture and the dnf upgrade-transaction matrix

Date: 2026-07-24

Covers the EL9 half of plan step 9's remaining work: the synthetic mismatched-candidate package fixture, and the real dnf transaction matrix run against it. The plan section this answers is "Upgrade transaction safety", and the acceptance criterion it feeds is "every documented upgrade command has a tested, documented resolver outcome, and the supported path never silently removes an imported VMOD". Debian 13 is a separate lane, worked in parallel, in disjoint paths.

Everything ran in local containers on an Apple Silicon host: `almalinux:9` at `almalinux@sha256:d2515c769e7b73f95c4fde38c0a505336ff38f14990c0b7253b77060a049a743`, **aarch64**, `dnf 4.14.0`, `rpm 4.16.1.3-40.el9`. That is a process proof, not a release; authoritative x86_64 results come from CI. Nothing here is signed or published.

## What now exists

```text
recipes/el9/mismatch-fixture.sh              host driver for the fixture
recipes/el9/mismatch/container.sh            the respin, in-container
recipes/el9/mismatch/vinyl-cache-fixture.spec.in
recipes/el9/transactions.sh                  host driver for the matrix
recipes/el9/transactions/Dockerfile          the scenario base image
recipes/el9/transactions/prep.sh             local dnf repositories (createrepo_c)
recipes/el9/transactions/scenario.sh         one scenario, one fresh container
```

Artefacts under `dist/el9/mismatch/`: `packages/`, `specs/`, `repos/{baseline,candidate,sameabi}/`, `logs/txn-*.log` (full dnf output per scenario), `logs/summary.tsv`, `SHA256SUMS`, `PROVENANCE`. All of `dist/` is already ignored by the repository root `.gitignore` (`/dist/`) and again by `dist/el9/.gitignore` (`*`), verified with `git check-ignore`; no third ignore file was added.

## The fixture

### Technique, and why this one

The candidate is a **package-metadata-level respin**: the baseline cohort's own binary RPMs, unpacked into a buildroot and re-wrapped under new package metadata by `mismatch/vinyl-cache-fixture.spec.in`. No compiler runs.

The plan explicitly allows this, and it is the right shape for what is being tested. Every input the resolver reads is metadata — name, EVR, `Provides`, `Requires` — and a second Vinyl compile would change none of it. Three properties made the choice easy to defend:

- **provenance is two lines.** The fixture's only source of file content is `vinyl-cache-9.0.0~git20260613.a909548147-1.el9.aarch64.rpm` (sha256 `6148c451…08d1`) and its devel half (`b818c60b…be09`), both already digest-pinned in `dist/el9/SHA256SUMS`. Not a second build environment nobody recorded.
- **it stays honest in the direction that matters.** The candidate really installs, really runs, and really provides `libvinylapi.so.3()(64bit)` and the `LIBVINYLAPI_3.0`/`3.1` symbol versions, so a transaction that *should* succeed cannot fail for an unrelated reason. `sanity-candidate-installable` is in the matrix precisely to prove that, and it does: with no VMOD installed, `dnf upgrade` takes the candidate cleanly.
- **it is verified against the baseline rather than assumed.** `container.sh` fails the build unless the candidate's file list equals the baseline's plus `FIXTURE.txt`, the devel file lists match exactly, the soname provides survive, and the devel half is pinned to the candidate runtime EVR. Six checks per variant, twelve in all, every one passing.

What the respin cannot simulate is genuinely different compiled code. That is stated in the shipped `FIXTURE.txt`, and it matters for exactly one result below (the same-ABI case), where it is called out rather than glossed.

### Version choice

Baseline: `9.0.0~git20260613.a909548147-1.el9`, `vinyld(abi) = a90954814766d933a75d4c808c449cb9bc0ae3d3`.

| variant | version-release | `vinyld(abi)` |
| --- | --- | --- |
| `mismatch` | `9.0.0~git20260724.ffffffffffff-1.mismatchfixture.el9` | `ffffffffffffffffffffffffffffffffffffffff` |
| `sameabi` | `9.0.0~git20260724.eeeeeeeeeeee-1.sameabifixture.el9` | `a90954814766d933a75d4c808c449cb9bc0ae3d3` (unchanged) |

Three constraints drove it. It must sort strictly above the baseline or no upgrade would be proposed and the matrix would test nothing. It must be unmistakably synthetic on sight, because these files sit in a `dist/` directory next to real ones: the lane's snapshot convention is `9.0.0~git<date>.<12 hex of commit>`, and `ffffffffffff` is not a commit anyone will ever have. And the release field carries the word `fixture`, which also happens to sort above `1.el9` (`rpmvercmp` segments `1.mismatchfixture.el9` as `[1][mismatchfixture][el][9]` against `[1][el][9]`, and `mismatchfixture` > `el`). That last point is asserted with `rpmdev-vercmp` at build time rather than left to the reasoning above.

`vinyld(vrt)` is deliberately left at the baseline's `23.0` in both variants, so the only thing that can block a transaction is `vinyld(abi)`. An ABI hash of 40 `f`s is syntactically a valid strict-ABI token and semantically impossible, so a resolver that matches it is matching the string and nothing else.

### Digests

Fixture packages, `dist/el9/mismatch/SHA256SUMS`:

```text
58e281928d4e5c0f6df63be311b2e1d465c0ba3506da660d55bdf182ab7f4710  vinyl-cache-9.0.0~git20260724.ffffffffffff-1.mismatchfixture.el9.aarch64.rpm
6761f4291edcee38281f2e5732cffd73eaa7e519eb8a3b497618a79a52a2f829  vinyl-cache-devel-9.0.0~git20260724.ffffffffffff-1.mismatchfixture.el9.aarch64.rpm
f75913ba2fe2d95528b96e82939cc1faebcfa3025d8e7cdd54522b04e92483de  vinyl-cache-9.0.0~git20260724.eeeeeeeeeeee-1.sameabifixture.el9.aarch64.rpm
6390f110eea5301f7e37fbac0da710f3bfeb44f20fc12b07c61ca7b001e143e0  vinyl-cache-devel-9.0.0~git20260724.eeeeeeeeeeee-1.sameabifixture.el9.aarch64.rpm
```

These digests are **reproducible**: `mismatch-fixture.sh --check-reproducible` builds the fixture twice in two separate containers and fails unless the digests are identical. Getting there took two pins that are easy to lose silently, and both are worth knowing for the real lane:

- `SOURCE_DATE_EPOCH` alone does not fix `BUILDTIME` on EL9. rpm 4.16 ships `%use_source_date_epoch_as_buildtime` defaulting to `0`, so the header timestamp still comes from the wall clock unless that macro is set to `1`.
- `_buildhost` defaults to the container's hostname, which Docker randomises per run. Two identical builds in two containers differed by exactly that one header string until it was pinned to `vcache-packaging-fixture.invalid`.

A retained digest that a rebuild does not reproduce is a receipt, not a provenance record, so the check is a command rather than a comment.

### Trust model

Local repositories, unsigned, `gpgcheck=0`, the same model the lane already uses to install its own RPMs by path. This lane has no signing key. Whether signature checking changes any outcome below — dnf's behaviour on an unsigned candidate, `repo_gpgcheck`, key rotation — is **untested and is CI work**. It is recorded as a gap, not as a pass.

## The harness

`transactions.sh` runs one **fresh container per scenario** from a base image that is stock `almalinux:9` with everything except our packages already present: fully updated, EPEL enabled (the cohort runtime needs `libunwind.so.8`), the cohort's OS-level dependencies installed, and `python3-dnf-plugin-versionlock` available. Pre-installing that is a harness decision worth stating: on a half-updated container `dnf upgrade` proposes a hundred unrelated BaseOS updates and the one line about `vinyl-cache` is lost in the noise. With the image current, the transaction output is about the cohort. Nothing carries between scenarios.

Two local repositories are visible to each scenario: `vinyl-baseline` (today's built cohort) and `vinyl-candidate` (the fixture Vinyl pair, and nothing else). The candidate repository deliberately does **not** carry a rebuilt cachetag. That is the incoherent-repository shape the plan is worried about — a Vinyl update published without the cohort it belongs to — and building the coherent case into the harness would have assumed the conclusion.

Each scenario installs the baseline cohort, records state, is shown the candidate, runs the transaction, and records state again. "State" includes compiling `smoke/smoke.vcl`, which contains `import cachetag`, with `vinyld -C`. That compile is the test that matters: it is the difference between "a package is missing" and "this machine's VCL no longer loads".

Three starting shapes are used, because the shape changes what the resolver may do: the full cohort; **runtime + VMOD with no devel package**, which is the ordinary production shape; and runtime + devel with no VMOD, the control.

## The transaction matrix

All 17 scenarios, from `dist/el9/mismatch/logs/summary.tsv`. "Vinyl after" is `baseline` or `CANDIDATE`; full dnf output for each row is in `logs/txn-<scenario>.log`.

| # | command | exit | Vinyl after | cachetag | VCL compiles | outcome | class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `dnf upgrade` *(control: no VMOD installed)* | 0 | CANDIDATE | n/a | n/a | upgraded Vinyl; proves the fixture is installable and higher-versioned | control |
| 2 | `dnf upgrade` | 1 | baseline | present | yes | **refused** the whole transaction, nothing changed | safe |
| 3 | `dnf upgrade --best` | 1 | baseline | present | yes | refused, identical to row 2 | safe |
| 4 | `dnf upgrade --nobest` | 0 | baseline | present | yes | **skipped** the update, nothing changed | safe |
| 5 | `dnf upgrade --skip-broken` | 1 | baseline | present | yes | refused | safe |
| 6 | `dnf upgrade --allowerasing` | 1 | baseline | present | yes | **refused** — did not propose removing the VMOD | safe |
| 7 | `dnf upgrade` *(no devel installed)* | 1 | baseline | present | yes | refused | safe |
| 8 | `dnf upgrade --allowerasing` *(no devel installed)* | 1 | baseline | present | yes | refused | safe |
| 9 | `dnf upgrade --allowerasing --nobest` | 0 | baseline | present | yes | skipped the update, nothing changed | safe |
| 10 | `dnf upgrade --allowerasing vinyl-cache` | 0 | CANDIDATE | **ABSENT** | **no** | **upgraded Vinyl and removed the VMOD** | WARNING REQUIRED |
| 11 | `dnf distro-sync` | 1 | baseline | present | yes | refused | safe |
| 12 | `dnf distro-sync --allowerasing` | 1 | baseline | present | yes | refused | safe |
| 13 | `dnf install vinyl-cache-<candidate>` | 1 | baseline | present | yes | refused | safe |
| 14 | `dnf install --allowerasing vinyl-cache-<candidate>` | 0 | CANDIDATE | **ABSENT** | **no** | **upgraded Vinyl and removed the VMOD** | WARNING REQUIRED |
| 15 | `dnf versionlock add …` then row 14's command | 1 | baseline | present | yes | versionlock **blocked** the erasing transaction | safe |
| 16 | row 14's command, then `dnf history undo last` | 0 | baseline | present | yes | the removal was fully reverted | safe (recovery) |
| 17 | `dnf upgrade`, candidate keeps the **same** ABI string | 0 | CANDIDATE | present | yes | upgraded the whole cohort without noticing | see below |

### Hypotheses versus results

The plan offered "DNF normally skips an update with unsatisfied dependencies, while `--allowerasing` can permit removal", and asked for it to be verified rather than assumed. It was worth asking.

**"Skips the update" is wrong for the default configuration.** EL9 ships `best=True` in `/etc/dnf/dnf.conf` (recorded in every scenario log). A plain `dnf upgrade` therefore does not skip — it **errors out and does nothing at all**, exit 1, naming the dependency:

```text
Error:
 Problem: cannot install the best update candidate for package libvmod-cachetag-1.0.0-1.el9.aarch64
  - package libvmod-cachetag-1.0.0-1.el9.aarch64 from @System requires vinyld(abi)(aarch-64) = a90954814766d933a75d4c808c449cb9bc0ae3d3, but none of the providers can be installed
(try to add '--skip-broken' to skip uninstallable packages or '--nobest' to use not only best candidate packages)
```

The consequence is operationally significant and is not what "skips the update" implies: an unattended `dnf upgrade` on a host with cachetag installed **fails as a whole**, so unrelated security updates in the same transaction do not get applied either. `--nobest` (row 4) is the flag that produces the hypothesised skip behaviour, and it is explicit about it — `Skipping packages with conflicts:` … `Skip 2 Packages` … `Nothing to do.`, exit 0. `--skip-broken` (row 5), despite its name and despite dnf suggesting it, does **not** help for this conflict shape.

**`--allowerasing` on a whole-system upgrade does not remove the VMOD.** Rows 6, 8 and 12 all refuse rather than propose an erasure, and row 8 shows it is not the devel package getting in the way — the runtime-only shape behaves identically. The removal happens only when the resolver is given a **strong job**: a targeted `upgrade vinyl-cache` (row 10) or a direct `install` of the candidate (row 14), each with `--allowerasing`. Then dnf states plainly what it is about to do:

```text
Upgrading:
 vinyl-cache        aarch64  9.0.0~git20260724.ffffffffffff-1.mismatchfixture.el9  vinyl-candidate
Removing dependent packages:
 libvmod-cachetag   aarch64  1.0.0-1.el9                                           @vinyl-baseline
```

and afterwards the failure the plan predicted is exactly reproduced:

```text
Message from VCC-compiler:
Could not find VMOD cachetag
('smoke.vcl' Line 8 Pos 8)
```

The running daemon would have kept serving from its already-mapped VMOD; the next VCL reload or restart is where it dies.

**dnf actively advertises the dangerous route.** Row 4's own output says `(add '--best --allowerasing' to command line to force their upgrade)`. On this shape that advice does not even work — `--allowerasing` with the default `best=True` is row 6, which refuses. An operator following dnf's advice therefore gets a second failure and is one step from reaching for a targeted `install --allowerasing`, which is row 14. The documentation has to intercept that path, not just list the safe commands.

### The same-ABI-string limitation: confirmed, and material

Row 17. The `sameabi` candidate advertises `vinyld(abi) = a90954814766d933a75d4c808c449cb9bc0ae3d3` — the baseline's hash — at a higher version-release, with different package content. `dnf upgrade` resolved it in one step, no warnings, no prompts beyond the usual:

```text
Upgrading:
 vinyl-cache        aarch64  9.0.0~git20260724.eeeeeeeeeeee-1.sameabifixture.el9  vinyl-candidate
 vinyl-cache-devel  aarch64  9.0.0~git20260724.eeeeeeeeeeee-1.sameabifixture.el9  vinyl-candidate
Upgrade  2 Packages
```

cachetag stayed installed and the VCL still compiled. The exact-ABI dependency did not participate: it was satisfied by a string that a different package happened to carry.

Two honest caveats on how far this goes. The VMOD still loaded, but that proves nothing about a genuinely patched Vinyl — the fixture's payload *is* the baseline's binaries, so of course it loaded. And the point of the test is not that this fixture broke anything; it is that **the resolver never asked the question**. Any Vinyl package that copies the `vmod_abi.h` string — a downstream rebuild with patches, a vendor respin, a rebuilt cohort with a different build profile — is accepted as an ABI-identical drop-in with no metadata-level check available to contradict it.

The plan says to add a cohort-qualified ABI provide or an exact Vinyl binary package version dependency "if transaction testing shows this is material". It does. Row 17 is the whole risk in one transaction: the upgrade path that is *supposed* to be the safe one is precisely the one that cannot see the difference.

### Incident response: versionlock

Procedure, exercised in row 15 with `python3-dnf-plugin-versionlock`:

```sh
dnf install python3-dnf-plugin-versionlock
dnf versionlock add vinyl-cache vinyl-cache-devel libvmod-cachetag
dnf versionlock list
# to release, per package:
dnf versionlock delete vinyl-cache
```

The lock pins the currently installed EVR (`vinyl-cache-0:9.0.0~git20260613.a909548147-1.el9.*`) into `/etc/dnf/plugins/versionlock.list`. Under the lock, the erasing install that removes the VMOD in row 14 is filtered out entirely:

```text
All matches were filtered out by exclude filtering for argument: vinyl-cache-9.0.0~git20260724.ffffffffffff-1.mismatchfixture.el9
Error: Unable to find a match
```

and a plain `dnf upgrade` reports `Nothing to do.` with exit 0 — which is a real improvement on row 2's hard failure, because the rest of the system's updates can then proceed.

One detail found by releasing the lock again, and worth putting in the runbook: deleting the lock on `vinyl-cache` alone, while `vinyl-cache-devel` stays locked, makes the resolver propose removing **both** `libvmod-cachetag` and `vinyl-cache-devel`. A partially released lock is not a partially safe state. Release all three together or none.

**Recovery** (row 16): after a transaction that removed the VMOD, `dnf history undo last` restores the cohort completely — it downgrades `vinyl-cache` and `vinyl-cache-devel` and reinstalls `libvmod-cachetag`, after which the VCL compiles again. That is the incident-response instruction to publish alongside the warning, and it works because the baseline packages are still in a reachable repository. It does not work if the previous cohort has been dropped from the repository, which is a retention requirement, not a nice-to-have.

## Classification: commands that need a prominent warning

Per the plan's requirement, any command that can remove an imported VMOD gets a prominent warning in the release notes and upgrade documentation. On EL9 that is exactly two shapes:

- **`dnf install --allowerasing vinyl-cache-<version>`** — a direct install of a specific Vinyl version with erasure allowed;
- **`dnf upgrade --allowerasing <package>`** — a *targeted* upgrade with erasure allowed.

The common factor is not `--allowerasing` on its own: it is `--allowerasing` combined with a command that names a package. Whole-system `dnf upgrade`, `dnf upgrade --best`, `dnf upgrade --nobest`, `dnf distro-sync` and `dnf distro-sync --allowerasing` never removed the VMOD in any tested shape. The warning should say so in those terms, because a warning that reads "`--allowerasing` is dangerous" would be both over-broad (rows 6, 8, 12) and would fail to warn about the more likely route, which is `install`.

Also worth documenting as behaviour, though not a removal:

- a plain `dnf upgrade` **fails entirely** on a host with an ABI-mismatched Vinyl available, blocking unrelated updates in the same transaction. `--nobest` is the documented way to let the rest through;
- `--skip-broken` does not help, despite dnf recommending it.

## Implications for repository and promotion design

1. **Cohort-aware promotion is now load-bearing, not a nicety.** Every safe row in the matrix is safe because the resolver could not find a coherent transaction and gave up. That is a good failure mode, but the *supported* path must not rely on it: publishing a Vinyl update without its matching cachetag turns every affected host's `dnf upgrade` into a hard error until an operator intervenes. Promotion must move the whole cohort at once, and the previous cohort must stay reachable — `dnf history undo` and versionlock both depend on it.

2. **The ABI virtual provide is a correctness guard, not an identity.** Row 17 settles the plan's open question: an exact `vinyld(abi)` dependency cannot distinguish two Vinyl packages carrying the same baked-in string. The mitigation should be added rather than deferred. The cheapest option that fits the existing machinery is a second, cohort-qualified provide from the runtime package — for example `vinyld(cohort)(aarch-64) = <cohort id>` alongside the ABI provide, with cachetag depending on both — since the `find-provides` generator already emits the ABI pair and the cohort id already flows in from `cohort.env`. The alternative, an exact `Requires: vinyl-cache%{?_isa} = <EVR>` in the VMOD package, is stricter still but couples the VMOD to a package revision rather than to a compatibility claim, which will churn on packaging-only rebuilds.

3. **Unattended-upgrade claims stay unsupported until CI covers signing.** The whole matrix ran against unsigned local repositories. Nothing here says how dnf behaves when the candidate is signed by an unknown key, or when `repo_gpgcheck` is on, and a refusal at signature-check time could mask or change every outcome above. The plan's position — that a supported unattended-upgrade claim waits for the cohort-aware repository and these transaction tests — should be extended to include the signed-repository run.

4. **Cross-check against Debian.** The plan's expected baseline for apt (`apt upgrade` holds; `apt full-upgrade` may propose removal) is a different hypothesis from dnf's, and the dnf result already diverged from what the plan expected. The Debian 13 lane's results should be compared against this table before either target's upgrade documentation is written, since the release notes will need one shared warning section, not two contradictory ones.

## Gaps

- **x86_64 untested.** Everything here is aarch64. The ABI provides are `%{?_isa}`-qualified, so the mechanism is architecture-parameterised, but the matrix has not been run on x86_64.
- **Signed repositories untested**, as above.
- **A real second cohort is still absent.** This fixture is the plan's stopgap ("before a previous cohort exists, create one synthetic mismatched package fixture per release line"). When a second real cohort exists it should replace the `mismatch` variant as the natural fixture; the `sameabi` variant has no natural equivalent and should be kept as a synthetic regression test for the limitation in point 2 above.
- **No running-daemon test.** The matrix proves the VCL no longer compiles after a removal. It does not exercise a live `vinyld` that has the VMOD mapped, being asked to reload VCL after the package vanished underneath it. That is the failure users will actually experience, and it belongs in a CI job with systemd available.
