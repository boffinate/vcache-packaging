# Step 10: the cohort-qualified virtual provide

Date: 2026-07-25

Status: implemented on both lanes, built and smoke-tested locally on arm64, and demonstrated against the synthetic upgrade fixtures. These are process proofs, not publishable artifacts; the authoritative amd64/x86_64 builds are still CI work.

Implements gate decision 4 of [the step-10 gate decisions](20260725_1602_note_step-10-gate-decisions.md), which accepted the recommendation both step-9 transaction analyses arrived at independently: [Debian 13](20260724_2300_note_step-9-debian-13-transactions.md) (the discussion around the same-ABI limitation) and [EL9](20260724_2342_note_step-9-el9-transactions.md) (row 17, and conclusion 2).

## The problem, restated

`vinyld-abi-<hash>` / `vinyld(abi)` is derived from the upstream Vinyl source revision — Vinyl bakes `"<PACKAGE_STRING> <VCS_Version>"` into `include/vmod_abi.h`, and the trailing field is the token. It is an excellent guard against an *upstream ABI change* and both matrices proved that: every mismatched-ABI scenario either held the candidate back or refused outright.

It is not, and cannot be, a statement about provenance. Any package built from the same upstream revision advertises the identical token: a distribution security backport, a vendor respin, our own rebuild with a different patch series or build profile. Both lanes measured the consequence with a `sameabi` fixture, and both got the same answer — the candidate upgraded cleanly and the resolver never asked the question:

| lane | before | evidence |
| --- | --- | --- |
| Debian 13 | s12/s13/s14 all upgraded the candidate cleanly, VMOD untouched | step-9 note, "The same-ABI limitation: confirmed, and it matters" |
| EL9 | row 17 upgraded the whole cohort without noticing | step-9 note, "The same-ABI-string limitation: confirmed, and material" |

The cohort id can answer it, because it is a digest over the pinned source archive, the ordered patch series and the production build-profile revision (see [`registry/README.md`](../registry/README.md)). Two builds that differ in any of those get different cohort ids even when Vinyl's baked-in ABI string is unchanged.

## What was built

The Vinyl runtime package gains a **second virtual provide**, cohort-qualified, alongside the existing exact-ABI and VRT provides. The cachetag package depends on it in addition to the existing ABI and VRT dependencies.

```text
Debian:  Provides: vinyld-cohort-<COHORT_ID>          Depends: vinyld-cohort-<COHORT_ID>
RPM:     Provides: vinyld(cohort-<COHORT_ID>)%{?_isa} Requires: vinyld(cohort-<COHORT_ID>)%{?_isa}
```

Three shape decisions, each forced rather than chosen:

- **the cohort id lives in the provide *name*, not its version.** A cohort id contains hyphens (`vinyl-9.0.0-000000000000`), and RPM will not accept a hyphen in an EVR. `Provides: vinyld(cohort)%{?_isa} = vinyl-9.0.0-000000000000` is simply not expressible. Putting the id in the name works on both package managers, so both lanes use the same shape and the two recipes stay readable side by side. The cost is that the provide is unversioned and there is nothing to compare — which is correct, because the cohort id *is* the entire identity;
- **the value is asserted against the package-name charset at build time.** `^[a-z0-9][a-z0-9+.-]+$` on both lanes. Debian's `override_dh_gencontrol` refuses to write the substvar otherwise; EL9's `find-provides` refuses to emit the capability. A cohort id that needed quoting would produce a package nobody could depend on, and the failure would surface as an unresolvable dependency long after the build;
- **the distro-native lane gets nothing.** It has no cohort identity and no distribution package will ever advertise one, so emitting a cohort dependency there would make every distro-native artifact permanently unresolvable. Its equivalent guard remains the exact binary package version dependency. `tools/metadata.py` enforces this by construction and a self-test asserts it.

### Files changed

`vcache-packaging`:

| File | Change |
| --- | --- |
| `recipes/debian-13/vinyl/debian/rules` | `COHORT_ID = @COHORT_ID@`; non-empty and charset assertions; `vinyld:COHORT` substvar |
| `recipes/debian-13/vinyl/debian/control` | `${vinyld:COHORT}` in `Provides`; the virtual-package paragraph now explains all three and why the ABI provide alone is not enough |
| `recipes/debian-13/build.sh` | `COHORT_ID` added to the vinyl-debian `_subst` call and to the container environment |
| `recipes/debian-13/container/stage-vinyl.sh`, `stage-cachetag.sh`, `stage-smoke.sh` | assertions on the new provide/depends, plus a negative control (a foreign cohort id must not resolve) |
| `recipes/debian-13/mismatch-fixture.sh`, `container/make-mismatch.sh` | per-variant fixture cohort ids; the transformation now rewrites the cohort provide and *fails* if the baseline does not carry one |
| `recipes/el9/find-provides` | cohort id as a third positional argument, validated, emitted as `vinyld(cohort-<id>)$ABI_ISA` |
| `recipes/el9/vinyl-cache.spec.in` | `%global vinyl_cohort @COHORT_ID@`, spliced into `%{__find_provides}` |
| `recipes/el9/smoke/smoke.sh` | provide/require assertions plus the foreign-cohort negative control |
| `recipes/el9/mismatch/vinyl-cache-fixture.spec.in`, `mismatch/container.sh` | fixture cohort provide, and a check that the fixture never reuses the real cohort id |
| `recipes/el9/transactions.sh`, `transactions/scenario.sh` | two new scenarios, `same-abi-targeted-allowerasing` and `same-abi-install-allowerasing` |
| `tools/metadata.py`, `tools/selftest.py` | `cohort_provide` / `rpm_cohort_provide` in the generated metadata, appended to every per-ecosystem dependency list on the cohort lane and to none on the distro-native lane |
| `registry/README.md`, both lane READMEs | schema and lane documentation |

`libvmod-cachetag`:

| File | Change |
| --- | --- |
| `packaging/debian/control` | `vinyld-cohort-@COHORT_ID@` in `Depends`; description explains why the two dependencies are not redundant |
| `packaging/rpm/libvmod-cachetag.spec` | `Requires: vinyld(cohort-@COHORT_ID@)%{?_isa}` |
| `packaging/README.md` | `@COHORT_ID@` token row updated with its new uses and its charset constraint; the "what the Vinyl packages must provide" contract; the distro-native mapping note |

`check-tokens.sh` needed no change: `COHORT_ID` was already a declared token, and `--templates` passes.

**No registry manifest change was needed.** The registry already models the cohort id and already derives it from exactly the inputs the provide is meant to distinguish; nothing about the provide is a new manifest fact. What did change is the *generator*, because `tools/metadata.py` emits the dependency expressions that the recipes' contract is written against, and a generator that still emitted only `vinyld-abi-…` would have documented a contract the packages no longer honour.

## Evidence: metadata as built

Debian 13, arm64, `dist/debian-13/`:

```text
vinyl-cache      Provides: vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3,
                           vinyld-cohort-unassigned-local-process-proof,
                           vinyld-vrt (= 23.0)
libvmod-cachetag Depends:  libc6 (>= 2.38), vinyl-cache (>= 9.0.0~git20260613.a909548147),
                           vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3,
                           vinyld-vrt (= 23.0),
                           vinyld-cohort-unassigned-local-process-proof
```

EL9, aarch64, `dist/el9/`:

```text
vinyl-cache      Provides: vinyld(abi)(aarch-64) = a90954814766d933a75d4c808c449cb9bc0ae3d3
                           vinyld(cohort-vinyl-9.0.0-000000000000)(aarch-64)
                           vinyld(vrt)(aarch-64) = 23.0
libvmod-cachetag Requires: vinyld(abi)(aarch-64) = a90954814766d933a75d4c808c449cb9bc0ae3d3
                           vinyld(cohort-vinyl-9.0.0-000000000000)(aarch-64)
                           vinyld(vrt)(aarch-64) = 23.0
```

The two lanes carry different cohort ids because they always have: the Debian lane's `build.sh` uses the honest placeholder `unassigned-local-process-proof`, while the EL9 lane's `cohort.env` uses the registry template id `vinyl-9.0.0-000000000000`. That divergence predates this work and is harmless today — the lanes never share a repository — but it is now *visible in package metadata*, which makes it worth fixing when a real cohort id is minted. Both values are legal package-name components, which is the property the assertions check.

## Evidence: smokes

Both lane drivers were run end to end, sequentially, on the local arm64 host.

- **Debian 13**: `recipes/debian-13/build.sh` — exit 0. `SMOKE SUMMARY: 19 passed, 0 failed` (was 16; the three new assertions are the runtime provide, the cachetag depends, and a negative control that a foreign `vinyld-cohort-…` does not resolve). lintian unchanged: `exit status 0`, the only tag still `W: libvmod-cachetag: wrong-manual-section 3 != 4`.
- **EL9**: `recipes/el9/build.sh` — exit 0. `ALL STEPS PASSED`, 18 `PASS` lines. rpmlint unchanged at 5 errors / 22 warnings, all pre-existing (`invalid-license BSD-2-Clause`, spelling, `no-documentation`).

The EL9 smoke's step 0c is the one worth quoting, because it shows the dependency is load-bearing rather than decorative. With no runtime installed, dnf now names *both* unsatisfied capabilities:

```text
- nothing provides vinyld(cohort-vinyl-9.0.0-000000000000)(aarch-64) needed by libvmod-cachetag-1.0.0-1.el9.aarch64
```

Both smokes also carry a new negative control: a capability naming a different cohort has no provider, and on Debian a foreign `vinyld-cohort-…` virtual package does not resolve.

## Evidence: the transaction outcome change

This is the point of the exercise, so it gets the whole section.

### Debian 13

Fixtures regenerated with per-variant cohort ids; the transformation log shows the rewrite explicitly:

```text
Provides: vinyld-abi-a909…e3d3, vinyld-cohort-unassigned-local-process-proof, vinyld-vrt (= 23.0)
       -> vinyld-abi-a909…e3d3, vinyld-cohort-sameabi-fixture-eeeeeeeeeeee, vinyld-vrt (= 23.0)
```

The whole 16-scenario matrix was then re-run (deviation from the brief, explained below). **Three rows changed and thirteen did not.** All three are the `sameabi` rows:

| # | command | before (2026-07-24) | after (2026-07-25) |
| --- | --- | --- | --- |
| s12 | `apt upgrade -y` | upgraded Vinyl cleanly, VMOD untouched | **HELD-BACK** — `Not upgrading: vinyl-cache`, `Upgrading: 0, … Not Upgrading: 1`, exit 0 |
| s13 | `apt full-upgrade -y` | upgraded Vinyl cleanly, VMOD untouched | **REMOVED-VMOD** — `REMOVING: libvmod-cachetag`, exit 0, VCL no longer compiles |
| s14 | `apt install -y vinyl-cache=<sameabi>` | upgraded Vinyl cleanly, VMOD untouched | **REMOVED-VMOD**, exit 0, VCL no longer compiles |

s12 is the result the change was made for. s13 and s14 are the honest other half: the cohort provide converts "silently accepted" into "a resolver conflict", and apt resolves a conflict under `full-upgrade` by removing the VMOD. That is precisely the trade the Debian step-9 note already called out for the mismatched-ABI case — *"the provide converts 'silently broken' into 'unresolvable', which is a large improvement, but it converts it into removal under half the commands tested"* — and it now applies to the same-ABI case too. It is not a new hazard class; s04, s06, s10, s11 and s16 already had it. It does mean the existing `REMOVING:` warning covers strictly more situations than before, which is the right direction.

### EL9

| # | command | before (2026-07-24) | after (2026-07-25) |
| --- | --- | --- | --- |
| 17 | `dnf upgrade`, same-ABI candidate | exit 0, upgraded the whole cohort without noticing | **exit 1, REFUSED the transaction, nothing changed** |

with dnf naming the capability that could not be satisfied:

```text
Error:
 Problem 1: package libvmod-cachetag-1.0.0-1.el9.aarch64 from @System requires
 vinyld(cohort-vinyl-9.0.0-000000000000)(aarch-64), but none of the providers can be installed
```

Two scenarios were **added** to the EL9 matrix rather than re-run, because before this change there was nothing for them to test: with no resolver conflict, an erasing transaction had nothing to erase.

| scenario | result |
| --- | --- |
| `same-abi-targeted-allowerasing` (`dnf upgrade --allowerasing vinyl-cache`) | exit 0, **UPGRADED VINYL AND REMOVED THE VMOD**, VCL fails |
| `same-abi-install-allowerasing` (`dnf install --allowerasing vinyl-cache-<sameabi>`) | exit 0, **UPGRADED VINYL AND REMOVED THE VMOD**, VCL fails |

So both lanes now behave identically on the same-ABI candidate, and identically to how they already behaved on a mismatched-ABI candidate: the supported path holds or refuses, and the erasing paths remove. The two EL9 removal shapes are the same two the step-9 note already required a prominent warning for — `--allowerasing` combined with a command that names a package — so **the published warning text needs no change**, only the observation that it now also applies to a candidate whose ABI string looks correct.

### Scope of the re-runs

The mismatched-ABI rows were expected not to change and did not: they were already blocked by the ABI dependency, which fails before the cohort dependency is ever consulted. The Debian matrix confirms this directly (13 unchanged rows). On EL9 only `upgrade` was re-run from the mismatch set, as a control; it still refuses, exit 1. The remaining EL9 mismatch rows were not re-run.

## Deviations from the brief

1. **The full 16-scenario Debian matrix was re-run**, where the brief asked only for the affected scenarios. Reason: `transactions.sh --summary` rebuilds `SUMMARY.tsv` from whatever `.result` files exist, so running four scenarios leaves a table in which three rows are from today and thirteen are from yesterday's fixtures, with nothing in the file saying so. A mixed-provenance evidence table is worse than either a stale one or a fresh one. Each scenario takes about six seconds and the base image was already built, so the whole matrix cost about 90 seconds. The EL9 matrix, whose scenarios take minutes each, was *not* re-run in full.
2. **Two scenarios were added to the EL9 matrix.** Not requested, but the Debian result made the question unavoidable: if the fix turns a silent upgrade into a removal on apt, does it do the same on dnf? It does, and leaving that unmeasured would have left the two lanes' documented conclusions asymmetric for no reason.
3. **Assertions were added to both smokes** rather than proving the metadata by ad-hoc container inspection. The brief allowed either; a checked assertion is evidence that keeps working.

## What is not proven

- **amd64 / x86_64.** Everything here is arm64/aarch64. The mechanism is a metadata relation, and the RPM side is `%{?_isa}`-qualified, so architecture-dependence would be surprising — but the assertions have not run there.
- **A real cohort id.** Both lanes still use placeholder identities. The charset assertions pass on both, and the derived form `vinyl-<upstream-version>-<input-id>` satisfies the charset by construction, but no package has yet carried an id that came out of `tools/release_tool.py cohort-id`.
- **The two lanes' placeholder ids differ**, as noted above. Harmless now, and a thing to fix when real ids are minted rather than to paper over with a third placeholder.
- **Whether the cohort dependency ever fires before the ABI dependency.** In every scenario tested where the cohort differed, the ABI either differed too (so the ABI relation failed first) or the cohort relation was the sole failure. A candidate with a *different* ABI and the *same* cohort is not a combination any fixture produces, and it is not a combination a correct build can produce either, so it was not tested.
- **`unattended-upgrades` and signed repositories** remain untested, exactly as the step-9 notes left them.

## Reproducing

```sh
python3 tools/release_tool.py selftest          # 94 checks, was 86

recipes/debian-13/build.sh                      # build + lint + installed-package smoke
recipes/debian-13/mismatch-fixture.sh           # fixtures, now with per-variant cohort ids
recipes/debian-13/transactions.sh               # the whole matrix
recipes/debian-13/transactions.sh s12 s13 s14   # just the rows that changed

recipes/el9/build.sh                            # build + smoke
recipes/el9/mismatch-fixture.sh                 # fixtures
recipes/el9/transactions.sh same-abi same-abi-targeted-allowerasing same-abi-install-allowerasing
```
