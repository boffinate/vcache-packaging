# Step 6 Wave B: the live proof

Date: 2026-07-29

Status: In progress

Branch: `step6-second-vmod`

Related:

- [Wave A3: workflow wiring](20260728_2352_note_step-6-wave-a3-workflow-wiring.md) — the wiring under test, and the record of Wave B runs 1-3 with defects B1-B6
- [Wave A2: CI integration](20260728_2334_note_step-6-wave-a2-ci-integration.md) — the schema, ledger, evidence model and lane scripts
- [Wave A1: the recipe generator](20260728_2216_note_step-6-wave-a1-recipe-generator.md)
- [VMOD matrix failure isolation](20260728_0833_plan_vmod-matrix-failure-isolation.md), Phase 3
- [vmod-packager patterns and recipe generation](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md), the twelve verification cases
- [Roadmap](20260728_0916_roadmap_outstanding-packaging-work.md), Step 6 exit gate

Runs 1-3 are recorded in the A3 note, where the defects they found belong beside the wiring that produced them. This report starts at the B5/B6 fixes and carries the run-by-run evidence from run 4 onwards.

## B5 and B6: two defects in checks, not in packages

Both are the same shape as B3, and B3 is why they are being fixed together. Run 2's `verify-deb.sh` rejected the recipe's own lintian override file; B3 fixed that allowlist and nobody looked at its RPM twin, so B6 arrived one run later as the same omission in the other backend. The lesson the predecessor recorded — *"which is itself worth noting, because B3 was fixed on Debian only and the RPM twin went unexamined"* — is applied here by sweeping both allowlists side by side rather than patching the one that failed.

### B5: assert the flag, not the canary

**Ruling:** the stack-protector check moves from the binary to the build log. `relro`, `bind-now` and `pic` stay where they are.

The old check looked for a `__stack_chk_fail` reference in the built object. That reference exists only if some function had a stack-allocated buffer worth instrumenting, so its absence means *"no function needed one"*, not *"the flag was off"*. `vmod_dict.c` has no such function. Run [30409242057](https://github.com/boffinate/vcache-packaging/actions/runs/30409242057) therefore failed a package whose compile lines read:

```text
libtool: compile:  gcc … -ffile-prefix-map=/build/vmod-dict-1.7=. -fstack-protector-strong
  -fstack-clash-protection -Wformat -Werror=format-security -fcf-protection -c vmod_dict.c …
```

A verdict that depends on the shape of the source is not a verdict about the build. The distinction that makes the fix safe rather than a relaxation:

| Property | Where it is observable | Treatment |
| --- | --- | --- |
| `relro` (`GNU_RELRO`) | linked object | binary assertion, unchanged |
| `bind-now` (`BIND_NOW`) | linked object | binary assertion, unchanged |
| `pic` (`ELF type DYN`) | linked object | binary assertion, unchanged |
| `-fstack-protector-strong` | compile line | **build-log assertion, new** |
| `__stack_chk_fail` symbol | linked object, *if the source has a canary-worthy function* | **corroborating; absence is never a failure** |

Three link-level properties are source-independent and stay as they are. The fourth is a compile-time policy, so it is asserted from the compile lines.

**`scripts/ci/vmod/container/check-build-flags.sh`**, staged into the lane beside the two verify scripts, takes a log and a list of flags. It selects the lines prefixed `libtool: compile:`, requires at least one, and requires every one to carry every named flag.

`libtool: compile:` is the selector because it is libtool's echo of the real compiler invocation for one of the *package's own* translation units. Configure's `conftest.c` compiles never go through libtool, so nothing has to be excluded by name and no line the check should have seen can be missed. It is a property of the autotools adapter — every VMOD it packages builds a `vmod_LTLIBRARIES` — and a future adapter with another build system needs its own selector rather than a loosened one. Requiring at least one line is the other half: a log with no compile lines would otherwise pass vacuously, which is precisely what makes a flag assertion worthless.

**Both backends get the same assertion**, which is the point of the ruling:

- EL9 reads `/lane/logs/mock-build.log`, already copied into the lane by the EXIT trap the run-3 parity pass ported from `container-mock.sh:79-116`.
- Debian had no build log at all. `build-deb.sh` now `tee`s the pbuilder build into `/lane/logs/pbuilder-build.log`. Written as the build runs rather than copied afterwards, so a failing build still leaves its log behind — the same reasoning the EL9 trap records. It also arrives in the row's uploaded artifact for free, because the workflow already publishes `lane/logs/**`.

**Cachetag's own check is untouched.** It passes on symbols because cachetag's source has canary-worthy buffers, and this wave's contract is that nothing cachetag builds from moves. The difference is now a recorded asymmetry rather than an accident: cachetag asserts a symptom that happens to be present, dict asserts the cause. Raising cachetag to the stronger form belongs in a change whose only purpose is that, alongside the `lintian`/`dh_missing` asymmetries from Wave A2's Q3 ruling.

### B6, and the allowlist symmetry sweep

**Ruling:** allow `/usr/lib/.build-id/**` on the RPM side, and sweep both allowlists together so this class is closed rather than moved.

`/usr/lib/.build-id/**` is `redhat-rpm-config`'s debuginfo hard-link farm. `find-debuginfo` adds it to the **main** package of every debuginfo-enabled build, so it is in every EL9 package this lane will ever produce. The allowlist was written from the overlay's declared payload and forgot the packaging's own output, exactly as the Debian one had.

The sweep, both lists read side by side against the real run-3 payload listings:

| Path class | Debian (`dpkg-deb -c`) | EL9 (`rpm -qpl`) | Verdict |
| --- | --- | --- | --- |
| VMOD object | allowed | allowed | symmetric |
| manual page | `/usr/share/man/` | `/usr/share/man/` | symmetric |
| documentation | `/usr/share/doc/` | `/usr/share/doc/` | symmetric |
| licence text | inside `/usr/share/doc/<pkg>/copyright`, covered by the doc rule | `/usr/share/licenses/`, allowed explicitly | symmetric in effect; the extra RPM rule is `%license`, which Debian has no separate path for |
| lint override shipped by the recipe | `/usr/share/lintian/overrides/<binary>`, named exactly (B3) | none — no rpmlint configuration is shipped in the package | **no twin needed**, checked rather than assumed |
| debuginfo build-id links | in the separate `-dbgsym` binary package, which this check never inspects | in the **main** package | **B6**: RPM needs the rule, Debian must not have one |
| directory entries | filtered by the trailing `/` `dpkg-deb -c` prints | indistinguishable from files, so matched by path | structural difference, now handled on both sides |
| libtool archives / static libraries | rejected | rejected | symmetric |

Two things came out of the sweep beyond B6 itself.

The `-dbgsym` asymmetry is now a **comment in the Debian script** saying why there is deliberately no build-id rule there. An allowlist with an unexplained gap relative to its twin is indistinguishable from one with an oversight, which is how B6 survived B3.

And a latent defect in both, unrelated to either: the first `grep -v` in each pipeline was unguarded, so a payload consisting *only* of allowed paths would have made `grep` exit 1, `pipefail` propagate it, and `set -e` abort the script with a success-shaped payload. Every filter in both pipelines is now guarded with `|| true`. Nothing has ever hit it — every real package has documentation — but it is a check that fails on a correct package, which is the class both B5 and B6 belong to.

### Local verification

Host-safe, in containers, against **captured real build output from run 30409242057** rather than invented text. Two distro userlands (`debian:13` and AlmaLinux 9 derived images) so nothing depends on one `grep`.

`check-build-flags.sh`, seven cases:

| Case | Input | Expected | Result |
| --- | --- | --- | --- |
| EL9 real build log | run 3 `mock-build.log`, 394 lines | pass | PASS, 2 compile lines |
| Debian real build log | run 3 job log, 2488 lines | pass | PASS, 2 compile lines |
| Debian log with the flag stripped | same, `-fstack-protector-strong` removed | fail | FAIL, names both lines |
| log with the compile lines removed | EL9 log minus `libtool: compile:` | fail | FAIL, "proves nothing about the flags" |
| log absent | non-existent path | fail | FAIL |
| no flags given | usage error | fail | FAIL |
| two flags, one absent | real log, plus `-fno-such-flag` | fail | FAIL |

Both payload allowlists, exact pipelines from the two verify scripts, against the real run-3 listings and stray-file negatives: the real Debian payload and the real EL9 payload both pass; a stray `/etc` file is rejected on both; a lintian override for *another* binary is rejected on Debian; a stray `/usr/lib/other` is rejected on EL9; and an object-only listing passes on both, which is the `|| true` guard proving itself — under the old pipeline that input aborted the script.

`generate.sh` dry run stages `check-build-flags.sh` into the lane beside the two verify scripts.

**Why this is container fixtures and not a Python self-test.** The logic is in shell, in a script that runs only inside a buildroot. A `tools/*.py` self-test could only assert it by shelling out to `bash` with fixture files, which would put a shell dependency and a fixture corpus into tooling the runbook deliberately keeps stdlib-only and buildroot-portable. Exercising the real script against real captured build logs in the real distro userlands is the stronger evidence and the cheaper one.

## Pre-dispatch battery

| Check | Result |
| --- | --- |
| `release_tool.py selftest` | 142/142 |
| `ci_matrix.py selftest` | 224/224, then 132/132 for the generator |
| `vmod_recipe.py selftest` | 132/132 |
| `release_tool.py validate` | OK, 10 manifests, cachetag 1.0.1 |
| `release_tool.py validate --require-releasable` | RED, exit 1, **14 errors, every one naming `vmods.dict`** — dict's pending evidence and nothing else |
| `ci_matrix.py check-catalog` | OK, 2 VMODs |
| `ci_matrix.py ledger --tier ci` | 15 rows, **14 selected** |
| containerized `actionlint`, all 5 workflows | clean, exit 0 |
| containerized `shellcheck --severity=error`, all lane scripts | clean, exit 0 |
| containerized `shellcheck --exclude=SC1007,SC1091`, all lane scripts | clean, exit 0 |
| `git diff main -- recipes/debian-13/ recipes/el9/ scripts/ci/debian13/ scripts/ci/el9/ scripts/ci/lib/` | **empty** |

## Run evidence

Recorded per run: the dispatch, what was expected before it started, and what was observed.

### Run 4 — `inject=none`, [30410876882](https://github.com/boffinate/vcache-packaging/actions/runs/30410876882), conclusion `failure`

Stopped at the baseline gate per the no-silent-iteration rule; runs for items 2-13 were not dispatched.

**12 of 14 selected rows green, for the fourth run in a row, and the same 12.** B5 and B6 are confirmed fixed and both dict rows failed further in than they ever have.

| Row group | Result |
| --- | --- |
| 4 engine rows | PASS |
| cachetag: invocation, source, 4 targets | **all PASS** |
| dict: invocation, `source-generated` | PASS |
| dict: `target-generated` debian-13-amd64 | **FAIL**, `failed_lint` at stage 5 |
| dict: `target-generated` el9-x86_64 | **FAIL**, `failed_behavior` at stage 8 |
| collector | reconciled all 14 rows, reported exactly the two failures |

**B5 confirmed, in CI, exactly as designed.** The Debian row's stage 4 now reads:

```text
PASS  -fstack-protector-strong     present on all 2 compile lines
BUILD-FLAG ASSERTION: PASS
NOTE  stack-protector    no canary symbol: no function in this source needs one.
                         Not a failure -- the flag is asserted from the build log above.
PASS  relro-segment      GNU_RELRO present
PASS  bind-now           BIND_NOW set
PASS  pic                ELF type DYN
HARDENING INSPECTION: PASS
```

The Debian build log reached the fresh verify container, the two compile lines were found, and the row went on to lint. **B6 confirmed**: the EL9 row passed the payload check, and then passed the soname check, the same hardening block, **rpmlint** and the **installed-package smoke** — four gates that had never run — before failing in the behaviour suite. dict's package installs from a local repository against the runtime pair alone on EL9, which is the first time that has been true.

Three new defects, all in dict's lane, none touching cachetag.

#### B7a — the generated changelog can emit a line lintian rejects

```text
W: vmod-dict: debian-changelog-line-too-long [usr/share/doc/vmod-dict/changelog.Debian.gz:9]
```

Line 9 is `  * Source sha256: <64 hex>.` — 84 columns. `debian-changelog-line-too-long` fires above 80. The digest cannot be shortened; recording it is the point.

**Fix: the generator wraps.** `wrap_changelog()` re-flows bullet bodies to 80 columns with a four-space continuation indent, leaving the version header, the blank separators and the ` -- maintainer  date` trailer byte-for-byte alone. Long words are never broken, because half a digest is worse than a long line.

The wrap is in the generator rather than in the template on purpose: a pre-wrapped template would have fixed this one line and left a longer archive name, cohort id or maintainer string to reintroduce the tag silently. `test_changelog_lines_fit` asserts the property against the real templates and the real dict inputs — that no line exceeds 80, that the digest survives intact, that the trailer and header are untouched, that prose wraps on whitespace, that an over-long token is not split, and that wrapping is idempotent. Nothing had ever asserted anything about the rendered changelog's shape, which is why a generator could emit a lintian warning with a full self-test battery green.

**RPM is deliberately not changed.** The rendered `%changelog` has a 107-column line carrying the same digest and `rpmlint` passed it in this very run: RPM has no changelog width rule. Recorded so the asymmetry reads as measured rather than missed.

#### B7b — upstream's man page selects a font groff does not have

```text
W: vmod-dict: groff-message troff:<standard input>:48: warning: cannot select font 'C'
   [usr/share/man/man3/vmod_dict.3.gz:1]
```

`src/vmod_dict.3` is generated by `rst2man`, and docutils emits `.ft C` for literal blocks. groff's default family has `CR`, not `C`. The page is upstream's own generated content, shipped verbatim in the release archive.

**Fix: one reviewed override in the overlay**, with the context pinned. This is the mechanism the recipe-generation plan requires for reviewed, VMOD-scoped exceptions, and it is not a relaxation: `lintian --fail-on error,warning` still runs with its exit status propagated, and the override names one tag with one message on one file. The alternative — correcting the man page — means patching upstream source, and the autotools adapter deliberately has no patch capability in Step 6; verification case 10 was relocated to Step 7 for precisely that reason.

Verified in a `debian:13` container against the real run-4 package, repacked with the generated override file and the wrapped changelog: **lintian exits 0, with no unused or mismatched override**. A narrower form matching only the file path does *not* match (`mismatched-override`), which is why the message text is part of the context. Three candidate forms were tested rather than guessed at.

#### B7c — the parser could not express a lint override at all

Adding B7b's override hit this immediately:

```text
E: recipes/vmods/overlays/dict/overlay.yml:236: malformed mapping entry:
   '"vmod-dict: groff-message *cannot select font \'C\'* [...]"'
```

`_parse_scalar_block` rejected any scalar containing `": "`, quoted or not — while `_parse_scalar`, three functions away, rejects it only for *plain* scalars and tells the author to *quote the value*. So the parser refused the fix its own diagnostic recommended.

That made a whole class of reviewed data unrepresentable. Every lintian and rpmlint override is written `<package>: <tag> <context>`, so the `lintian_overrides` lists the overlay schema has carried since Wave A1 could be declared and never filled in. It survived because both lists were empty for both VMODs until now — the schema had a field nothing had ever used.

**Fix:** the guard stands down for a quoted scalar and stays for a plain one, so `key: value` mistyped at scalar position still gets its diagnostic. Self-tests both ways.

#### B8 — the ported VTCs address the wrong driver

```text
**   top   === varnish v1 -vcl+backend {
**** top   Autoload libvtest_ext_varnish.so failed: … cannot open shared object file
---- top   Unknown command: "varnish"
*    top   TEST /lane/tests/dict_ci.vtc FAILED
2 tests failed, 0 tests skipped, 0 tests passed
```

Upstream's `tests/cs.at` and `tests/ci.at` drive Varnish, so the ported VTCs say `varnish v1 -vcl+backend { … }`. The engine under test is Vinyl Cache, and its packaged `vinyltest` registers its server command as `vinyl`. `varnish` is not a command it knows, so vtest2 falls through to autoloading `libvtest_ext_varnish.so` — which is in neither `vinyl-cache` nor `vinyl-cache-devel`, verified against the packages from the green `main` run — and both tests die before issuing a single request. cachetag's suite passes because cachetag's VTCs were written for Vinyl and already say `vinyl`.

**Fix: `varnish v1` becomes `vinyl v1`, and the port's header now documents three bindings instead of two.** It belongs with the other two — `import dict from "…"` → `import dict;`, and `${vmod_topsrc}` → `${dictdir}` — because it binds the test to the driver under test rather than changing what is asserted. Every request URL and every expected value is still upstream's, character for character; the oracle is untouched.

This is the first time the suite has ever run. A behaviour suite that was written, reviewed and staged but never executed reached CI with a binding that could not work on this engine, which is exactly the failure Wave B exists to find and exactly why load-only verification was ruled insufficient at Step 5.

### Run 5 — `inject=none`, [30412067149](https://github.com/boffinate/vcache-packaging/actions/runs/30412067149), conclusion `failure`

**13 of 14, and dict's EL9 row is green end to end for the first time.**

| Row group | Result |
| --- | --- |
| 4 engine rows | PASS |
| cachetag: invocation, source, 4 targets | **all PASS** |
| dict: invocation, `source-generated` | PASS |
| dict: `target-generated` **el9-x86_64** | **PASS** — build, payload, soname, hardening, rpmlint, install smoke and both VTCs |
| dict: `target-generated` debian-13-amd64 | **FAIL**, `failed_install_or_smoke` at stage 6 |

**B7a, B7b, B7c and B8 all confirmed fixed.** The EL9 row went through every gate the lane has: `2/2 passed, 0 skipped` on `dict_cs.vtc` and `dict_ci.vtc`, against the packaged `.so` resolved through `-p vmod_path`, with `num.dict` extracted from the digest-verified release archive and every expected value upstream's. **vmod-dict has a verified binary package on EL9.**

The Debian row got past lint — so B7a and B7b are fixed there too — and then failed one stage later.

#### B9 — the uniqueness check counted the tree the hardening stage extracted

```text
FAIL: libvmod_dict.so is not uniquely at $VINYL_VMODDIR
(found: /usr/lib/x86_64-linux-gnu/vinyl-cache/vmods/libvmod_dict.so
        /tmp/x/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods/libvmod_dict.so)
```

The second copy is `dpkg-deb -x "$deb" /tmp/x`, extracted two stages earlier in the same container so the hardening checks could read the ELF. The check was right that there were two files and wrong about what the second one was.

`verify-rpm.sh` has pruned `/tmp/x` and `/repo` since it was written. `verify-deb.sh` never inherited the list. **Third instance of the same class after B3 and B6** — a lesson one backend's verify script had and the other did not — and the reason it took until run 5 to appear is that the Debian row had never before reached stage 6.

**Fix, and the sweep that goes with it.** The Debian `find` takes the RPM half's prune list, *and* both scripts now delete `/tmp/x` when the hardening stage finishes, so the uniqueness check does not depend on a prune list staying in step with an extraction path. Then the two scripts were diffed check by check, as the allowlists were after B6:

| Check family | verify-deb.sh | verify-rpm.sh | Verdict |
| --- | --- | --- | --- |
| package selection by exact name | `dpkg-deb` glob excluding dbgsym by name | `find` excluding debuginfo/debugsource/src | symmetric |
| generated ABI + cohort dependencies | `Depends` | arch-qualified `Requires` | symmetric |
| payload allowlist | swept after B6 | swept after B6 | symmetric |
| no libtool archive or static library | yes | yes | symmetric |
| no soname provide | — | stage 4 | **deliberate**: dpkg generates no provides for a plugin outside the linker path |
| hardening: flag from the build log | yes | yes | symmetric since B5 |
| hardening: relro / bind-now / pic | yes | yes | symmetric |
| lint, hard-gated with no `\|\| true` | `lintian --fail-on error,warning` | `rpmlint`, status propagated | symmetric in strength |
| runtime-pair-only install | `-dev` absence asserted | `-devel` absence asserted | symmetric |
| single installed object, right directory | prune list | prune list | **symmetric as of B9** |
| packaged test driver | `command -v vinyltest` | same | symmetric |
| behaviour suite | same invocation, same flags | same | symmetric |

What is left is package-manager vocabulary and one extra RPM stage that has no Debian meaning. The class is closed on the same basis as the allowlists: both files were read side by side, and every remaining difference is recorded with a reason.

#### Also landed with B9: the EL9 buildroot package set

Not a defect — a gap found while preparing the evidence flip. *(continued below)*

### Cachetag equivalence against `main` — first measurement

Compared against the latest green `main` run, [30397392846](https://github.com/boffinate/vcache-packaging/actions/runs/30397392846), using run 5's cachetag artifacts. Repeated against the confirming baseline below; recorded here because run 5 is where the measurement was first available.

**Debian 13 amd64 — byte-identical.** Excluding `.buildinfo` and `.changes`, as the roadmap's Step 3 contract requires:

| Engine channel | Digest entries compared | Verdict |
| --- | --- | --- |
| `vinyl-release` | 11 (5 `.deb`, `.dsc`, `.debian.tar.xz`, `.orig.tar.gz`, …) | **all identical** |
| `vinyl-trunk-pinned` | 11 | **all identical** |

10 `.deb` files across the two channels, byte for byte. **Debian: PASS.**

**EL9 x86_64 — normalized semantic comparison**, per package, inside an `almalinux:9` container: NEVRA; summary, licence, group, URL, sourcerpm, buildtime; payload path, size, content digest, mode, owner, group, flags, rdev and symlink target; payload mtimes; Provides; Requires; Conflicts; Obsoletes; weak dependencies; scripts; triggers; changelog. Whole-RPM digests deliberately not compared.

`vinyl-trunk-pinned`: **9 of 9 EQUIVALENT**, empty diff in every section.

The A3 note named `Requires` as the first place to look if EL9 moved, because `metadata.py`'s `rpm_requires` did change for cachetag on every cohort and the argument for its inertness was that nothing on cachetag's path reads it. Checked directly rather than inferred from the aggregate:

```text
main                                        branch
vinyld(abi)(x86-64) = 25761f8505…73e33e     identical
vinyld(cohort-vinyl-9.0.0-4b7e68292979)…    identical
vinyld(vrt)(x86-64) = 23.0                  identical
```

**The reasoning holds.** The EL9 lane substitutes tokens into cachetag's own audited spec, which spells those Requires itself; only the generated spec template renders them from the changed function.

Whole-RPM sha256 differs on all nine, which is expected and is not an equivalence requirement — the Step 4 report measured the cause as `BUILDHOST`, the container's random hostname, with `BUILDTIME` correctly clamped and identical.

#### Run 5's cachetag EL9 row was cancelled by its own timeout

`cachetag el9-x86_64 (vinyl-release)` ran past its 35-minute `timeout-minutes` budget and GitHub terminated it mid-`build.sh`. Nothing in the row failed; the run's `failure` conclusion is dict's Debian row.

The cause is dispatch discipline, not the lane. Run 6 was dispatched while run 5 was still executing, both runs competed for the same runner pool, and the row that would ordinarily finish inside 35 minutes did not. There is no `concurrency:` group in `ci.yml`, so nothing cancels a superseded run — the two simply share runners and both get slower. **Recorded as a caution: a second dispatch while a run is in flight can time out a row in the first one, and a timed-out row is indistinguishable at a glance from a failed one.** Run 5's other three cachetag rows were already green, which is why this cost nothing beyond the row itself.

#### The EL9 buildroot package set (continued) The registry's per-VMOD `build.build_dependencies` needs the buildroot the package was built in. Debian's falls out for free: dpkg writes `Installed-Build-Depends` into the `.buildinfo`, which the row already uploads. Mock resolves its buildroot itself and writes no such list, and `root.log` records only the packages each transaction *added* — 33 for this build, against the 351 cachetag's EL9 entry records. So `build-rpm.sh` now asks the chroot directly after the build, the same thing `recipes/el9/container/build.sh:76-77` does on its own lane, and writes `logs/buildroot-packages.tsv` into the artifact. Non-fatal by construction: a row that produced a good package must not fail on a bookkeeping step.

### Run 6 — `inject=none`, [30413513970](https://github.com/boffinate/vcache-packaging/actions/runs/30413513970)

**Both dict target rows PASS.** For the first time, `vmod-dict` has verified binary packages on *both* selected targets, built from recipes generated in this repository out of the manifest and the reviewed overlay, with no upstream Debian or RPM files anywhere in the picture.

| Row group | Result |
| --- | --- |
| 4 engine rows | PASS |
| dict: invocation, `source-generated` | PASS |
| dict: `target-generated` debian-13-amd64 | **PASS** |
| dict: `target-generated` el9-x86_64 | **PASS** |
| cachetag: invocation, source | PASS |
| cachetag: 4 target rows | see the table below |

## Verification case 8, and a one-off transaction matrix

The recipe-generation plan's case 8 — *an incompatible engine package cannot satisfy the generated ABI or cohort dependency* — run as a one-off in containers on both targets, per the brief. Permanent transaction-lane integration for dict is Step 8 work and is **not** attempted here; `nightly-transactions.yml` remains cachetag-only.

**The fixture.** Both package sets come from run 6 itself: dict's package and the `vinyl-release` engine in the baseline repository, and the **`vinyl-trunk-pinned` engine packages from the same run** as the incompatible candidate. They differ in exactly the way the dependencies are meant to catch:

```text
baseline  ABI 423648c4cb6b225b3268ffc337354ea938f5efee   cohort vinyl-9.0.1-ac4f719c16f4
candidate ABI 25761f8505817ac50df994270bfe75b60073e33e   cohort vinyl-9.0.0-4b7e68292979
```

Run in `--platform linux/amd64` containers from the `debian:trixie` and `almalinux:9` tags rather than through `run.sh`. `run.sh` cannot force a platform — the image reference is digest-pinned and Docker refuses `--platform` against a digest — so a one-off that must exercise an x86_64 package manager on an arm64 host uses the tag directly. This is a local investigation, not a lane; CI remains the evidence authority for everything the lane itself asserts.

### Debian 13 amd64 — apt refuses, and names both dependencies

```text
The following packages have unmet dependencies:
 vmod-dict : Depends: vinyld-abi-423648c4cb6b225b3268ffc337354ea938f5efee but it is not installable
             Depends: vinyld-cohort-vinyl-9.0.1-ac4f719c16f4 but it is not installable
E: Unable to correct problems, you have held broken packages.
E: The following information from --solver 3.0 may provide additional context:
   Unable to satisfy dependencies. Reached two conflicting decisions:
   1. vmod-dict:amd64=1.7-1 is selected for install
   2. vmod-dict:amd64 Depends vinyld-abi-423648c4cb6b225b3268ffc337354ea938f5efee
      but none of the choices are installable:
      [no choices]
```

`apt-get` exits 100 and installs nothing.

### EL9 x86_64 — dnf refuses, and names both dependencies

```text
Error:
 Problem: conflicting requests
  - nothing provides vinyld(abi)(x86-64) = 423648c4cb6b225b3268ffc337354ea938f5efee needed by vmod-dict-1.7-1.el9.x86_64
  - nothing provides vinyld(cohort-vinyl-9.0.1-ac4f719c16f4)(x86-64) needed by vmod-dict-1.7-1.el9.x86_64
```

`dnf` exits 1 and installs nothing.

**Case 8: PASS on both targets.** The generated ABI and cohort dependencies do what they were generated to do, and the error a user would actually see names the token that is missing rather than failing obscurely.

### The rest of the matrix, and what it does not prove

Nine assertions per target, all passing:

| Assertion | debian-13-amd64 | el9-x86_64 |
| --- | --- | --- |
| the two engines differ in ABI (the fixture is not vacuous) | OK | OK |
| the resolver refuses the incompatible pairing | OK, exit 100 | OK, exit 1 |
| the error names the unsatisfied `vinyld` dependency | OK, 2 of 3 token families | OK, 2 of 2 |
| nothing was installed by the refused transaction | OK | OK |
| the coherent cohort installs | OK | OK |
| the engine never moves while the VMOD stays | OK | OK |
| the surviving VMOD's dependencies are still provided | OK | OK |
| `dist-upgrade` / `--allowerasing`: VMOD removed or engine held | OK | OK |
| removal takes the VMOD and leaves the engine | OK | OK |

**What this is not: an upgrade-transaction matrix.** Stated plainly because it bears directly on the evidence flip. The only incompatible engine a `ci.yml` run produces is the trunk-pinned one, and `9.0.0~git20260520.25761f8505-1` sorts **below** `9.0.1-1` in both dpkg's and rpm's version comparison. So no resolver on either target ever considers the candidate an upgrade, and every row above that mentions "upgrade" is really testing that the resolver declines to *downgrade*. That is a genuine result — nothing mismatched, nothing broke — but it is not the property cachetag's thirteen-scenario matrix tests.

Proving the upgrade dimension needs an engine that is simultaneously **newer** and **ABI-incompatible**, which is exactly what `recipes/debian-13/mismatch-fixture.sh` and `recipes/el9/mismatch-fixture.sh` exist to build and what only `nightly-transactions.yml` produces. Building that for dict is the Step 8 integration the brief defers, so **`vmods.dict.tests.upgrade_transactions` cannot honestly be recorded as `pass` from this work.** See the evidence section below.
