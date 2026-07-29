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

Not a defect — a gap found while preparing the evidence flip; the detail is below, after the equivalence measurement.

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

#### The EL9 buildroot package set (continued)

The registry's per-VMOD `build.build_dependencies` needs the buildroot the package was built in. Debian's falls out for free: dpkg writes `Installed-Build-Depends` into the `.buildinfo`, which the row already uploads. Mock resolves its buildroot itself and writes no such list, and `root.log` records only the packages each transaction *added* — 33 for this build, against the 351 cachetag's EL9 entry records. So `build-rpm.sh` now asks the chroot directly after the build, the same thing `recipes/el9/container/build.sh:76-77` does on its own lane, and writes `logs/buildroot-packages.tsv` into the artifact. Non-fatal by construction: a row that produced a good package must not fail on a bookkeeping step.

### Run 6 — `inject=none`, [30413513970](https://github.com/boffinate/vcache-packaging/actions/runs/30413513970), conclusion **`success`**

**The baseline gate is met. All 14 selected rows green.** The collector's reconciled ledger:

```json
"counts": { "expected": 14, "failed": 0, "missing": 0,
            "not_selected": 1, "passed": 14, "required_failed": 0 },
"ok": true
```

| Row group | Result |
| --- | --- |
| 4 engine rows | PASS |
| cachetag: invocation, source, 4 targets | PASS |
| dict: invocation, `source-generated` | PASS |
| dict: `target-generated` debian-13-amd64 | **PASS** |
| dict: `target-generated` el9-x86_64 | **PASS** |
| collector | 14 expected, 14 passed, 0 failed, 0 missing, 1 lane not selected for this tier |

For the first time, `vmod-dict` has verified binary packages on *both* selected targets, built from recipes generated in this repository out of the manifest and the reviewed overlay, with no upstream Debian or RPM files anywhere in the picture: clean pbuilder and Mock builds, payload allowlists, generated ABI and cohort dependencies, hardening asserted from the build log, `lintian --fail-on error,warning` and `rpmlint` both clean, runtime-pair-only install smoke, and upstream's own behaviour expectations passing against the packaged `.so`.

**Nine defects across six runs, none of them in a package.** B1 and B2 were lane omissions, B3 through B6 and B9 were defects in checks, B7a and B7c were defects in the generator and its parser, B7b was a reviewed upstream exception, and B8 was a defect in a test binding. Every `vmod-dict` package produced from run 2 onwards was correct; what kept failing was the machinery that inspects them. That is the shape a first live proof is supposed to have.

### Cachetag equivalence against `main` — confirmed on the green baseline

Repeated against run 30413513970, all four cachetag rows green.

| Target | Channel | Method | Verdict |
| --- | --- | --- | --- |
| debian-13-amd64 | `vinyl-release` | digests excluding `.buildinfo`/`.changes` | **11/11 identical** |
| debian-13-amd64 | `vinyl-trunk-pinned` | same | **11/11 identical** |
| el9-x86_64 | `vinyl-release` | normalized semantic comparison | **9/9 EQUIVALENT** |
| el9-x86_64 | `vinyl-trunk-pinned` | same | **9/9 EQUIVALENT** |

10 `.deb` packages byte-identical, 18 RPMs semantically equivalent with an empty diff in every section, and the package sets match exactly on all four rows. `Requires` checked directly on both channels and unchanged, so the A3 note's reasoning about the unconsumed `rpm_requires` change holds. **Equivalence: PASS.** This wave changed no file cachetag builds from, and the packages confirm it.

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

At the time this was written the conclusion drawn was that the upgrade dimension needed an engine that is simultaneously **newer** and **ABI-incompatible** — the `mismatch-fixture.sh` machinery only `nightly-transactions.yml` produces — and therefore that `upgrade_transactions` could not honestly be recorded.

**That conclusion was wrong, and the next section is why.** The upgrade under test is the *VMOD's*, not the engine's, and a second revision of dict costs one field in a scratch overlay. Left in place rather than rewritten, because the reasoning that produced a too-quick "cannot be done" is worth seeing next to what actually could be.

## The upgrade-transaction matrix, and the evidence flip

Ruling R-1: earn the evidence with a one-off rather than relax the gate. Done, and the ruling was right — "no upgrade exists to test" was too quick a conclusion. The missing ingredient was not a fixture engine at all: it was a **second revision of dict**, which the generator produces for free.

### Manufacturing revision 2

`package.revision` is a field in the reviewed overlay, and the generator renders it into `1.7-2` / `1.7-2.el9`. A scratch copy of the overlay with `revision: "2"` — the repository's declared revision stays 1, because revision 2 is a **fixture**, not a release artifact — regenerated both recipes, and both were then built by **the lane's own `container/build-deb.sh` and `container/build-rpm.sh`**, not by an improvised build:

```text
vmod-dict_1.7-2_amd64.deb          pbuilder, buildd chroot from the pinned snapshot
vmod-dict-1.7-2.el9.x86_64.rpm     Mock, alma+epel-9-x86_64, 214-package buildroot
```

Two local-only deviations, both recorded because neither is a lane change:

- the containers are started from the `debian:trixie` and `almalinux:9` **tags** with `--platform linux/amd64`, because `run.sh` cannot force a platform against a digest-pinned reference and this host is arm64;
- `mmdebstrap` needs a `/bin/sh` that can address file descriptor 10, and the emulated container's cannot (`Syntax error: Bad fd number`), so `/bin/sh` was pointed at bash for that one step. CI runs native and never meets this.

### The matrix

Same steps on both targets. The three generated `vinyld` dependencies are asserted **declared and satisfied at every step**, not merely at the end — an upgrade that is only correct after a broken intermediate state is not a safe upgrade.

| Step | debian-13-amd64 | el9-x86_64 |
| --- | --- | --- |
| fresh install of revision 1 | OK | OK |
| vinyld deps declared and satisfied | OK | OK |
| upgrade 1 → 2, package manager exits 0 | OK | OK |
| revision 2 installed | OK | OK |
| **the engine did not move** | OK | OK |
| vinyld deps still declared and satisfied | OK | OK |
| the packaged object is in place and owned by the package | OK | OK |
| explicit downgrade 2 → 1 succeeds | OK | OK |
| vinyld deps hold across the downgrade | OK | OK |
| a later upgrade picks 2 again | OK | OK |
| an ABI-incompatible engine is refused under either revision | OK | OK |
| vinyld deps hold after the refusal | OK | OK |
| removal takes the VMOD and leaves the engine | OK | OK |
| the packaged object is gone from the VMOD directory | OK | OK |
| no broken packages / consistent rpm database afterwards | OK | OK |

**15 of 15 on each target.** Verification case 8's refusal rows from the earlier one-off are subsumed here and reproduced against both revisions.

Permanent wiring through `nightly-transactions.yml` remains **Step 8**; nothing in this repository's workflows runs the above, and `nightly-transactions.yml` is still cachetag-only.

### The flip

Both `vmods.dict` entries are now `evidence: recorded`. Everything comes from run 30413513970's own artifacts, except the transaction row, which comes from the matrix above:

| Field | Source |
| --- | --- |
| `build.configure_options` | the `./configure` line in the captured build log |
| `build.cflags` | the `libtool: compile:` line, minus the package's own `-I`/`-DHAVE_CONFIG_H`/`-DLOCALSTATEDIR` |
| `build.ldflags` | the `libtool: link:` line's `-Wl,` arguments |
| `build.source_date_epoch` | the overlay's recorded release-commit committer date |
| `build.hardening_check` | `pass`, from the stage that asserts the flag rather than the canary |
| `build.build_dependencies` | Debian: 178 from `Installed-Build-Depends`. EL9: 214 from `logs/buildroot-packages.tsv` |
| `artifacts` | filenames and digests from the row's own `SHA256SUMS` |
| `tests.package_lint` | `pass` |
| `tests.installed_package_smoke` | `pass` |
| `tests.full_behavior_suite` | `pass` — 2/2 VTCs, 0 skipped |
| `tests.upgrade_transactions` | **`pass`**, from the matrix above, with the method recorded in the file |

```text
python3 tools/release_tool.py validate --require-releasable
OK: 10 manifest(s) valid (releasable mode), cachetag version 1.0.1
exit 0
```

**The gate is green with zero errors**, having been 14 errors when Wave B began. The schema needed no `deferred` value and none was added.

## Failure-injection sequence

The baseline is the gate for everything below; it went green in run 30413513970 and the injections run against that same tree.

Expected results were stated before each dispatch, from the ledger rather than from memory. One expectation is worth stating up front because it is not what the brief anticipated:

**`INJECT_ENGINE_ROW` moved, ruling R-2.** The pre-dispatch check found the constant pointing at `("vinyl-trunk-pinned", "debian-13-amd64")`, which the ledger shows has exactly **one** consumer — `target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64` — because dict declares no trunk lane. Items 10a and 10b would have demonstrated nothing the per-VMOD injections do not already cover.

Read off the ledger:

| Engine row | Consumers |
| --- | --- |
| `engine/vinyl-trunk-pinned/debian-13-amd64` | 1: cachetag's trunk-pinned Debian row |
| **`engine/vinyl-release/debian-13-amd64`** | **2, one per VMOD**: `target/cachetag/release/vinyl-release/debian-13-amd64` and `target/dict/release/vinyl-release/debian-13-amd64` |

The constant is now `("vinyl-release", "debian-13-amd64")`. That makes items 10a/10b the shared-root-cause demonstration the matrix plan asks for — one cause blocking rows in two *different* VMODs, reported as that cause rather than as unrelated cancelled jobs — and the first live exercise of `target-generated`'s `blocked_by_engine_artifact` path, which the generated-recipe lane has never taken.

The isolation half of the case is unchanged and is now stronger, because the surviving set also spans both VMODs: three sibling engine rows, cachetag's four other rows, and dict's EL9 row.

A self-test now asserts the property against the **real** catalog rather than a fixture — the injected row has more than one consumer, those consumers sit in more than one VMOD, the row is selected for the `ci` tier, and at least three package rows on other engine rows survive to be observed. Nothing asserted any of that before, which is why a one-consumer row could sit in the constant unnoticed once a second VMOD arrived.

### Item 2 — `inject=dict_source`, [30414399323](https://github.com/boffinate/vcache-packaging/actions/runs/30414399323)

The dict side is decisive and matches the expectation exactly:

| Row | Expected | Observed |
| --- | --- | --- |
| `source/dict/release` | `failed_source_checkout` | **`failed_source_checkout`** |
| `target/dict/release/vinyl-release/debian-13-amd64` | `blocked_by_vmod_source` | **`blocked_by_vmod_source`** |
| `target/dict/release/vinyl-release/el9-x86_64` | `blocked_by_vmod_source` | **`blocked_by_vmod_source`** |
| `source/cachetag/release` | PASS | **PASS** |
| cachetag's 4 target rows | PASS | **PASS** |
| 4 engine rows | PASS | **PASS** |

Collector, from the run's reconciled ledger:

```json
"counts": { "expected": 14, "failed": 3, "missing": 0,
            "not_selected": 1, "passed": 11, "required_failed": 3 }
```

Three failures, no missing rows, and the two blocked rows name the artifact that was not there:

```text
source/dict/release                                   failed_source_checkout
    vmod-ci-injected-missing-ref did not resolve to 784584d272894a39cf9953…
target/dict/release/vinyl-release/debian-13-amd64     blocked_by_vmod_source
target/dict/release/vinyl-release/el9-x86_64          blocked_by_vmod_source
    source artifact vmod-source-dict-release was not available
```

This is D1's fix proven live. The Wave A3 note recorded that `expand()` injects a dict source failure by rewriting the row's `ref`, that `source.sh` used to read the ref back out of the manifest instead, and that `inject=dict_source` would therefore have produced a **green** run. It produces a classified red source row and two correctly blocked consumers, while cachetag's source and every engine row carry on — the two-way isolation property, from dict's side, in a real graph rather than in a fixture.

### Item 3 — `inject=recipe_generation`, [30415386761](https://github.com/boffinate/vcache-packaging/actions/runs/30415386761)

| Row | Expected | Observed |
| --- | --- | --- |
| `target/dict/release/vinyl-release/debian-13-amd64` | `failed_recipe_generation` | **`failed_recipe_generation`** — *"the native recipe could not be generated, or an unresolved token survived into it"* |
| `target/dict/release/vinyl-release/el9-x86_64` | PASS | **PASS** |
| `source/dict/release`, `vmod/dict` | PASS | **PASS** |
| cachetag's 6 rows | PASS | **PASS** |
| 4 engine rows | PASS | **PASS** |

Exactly one row, classified as a *generation* failure rather than a build failure, which is the whole reason `failed_recipe_generation` was added to the vocabulary: nothing was compiled, the inputs were wrong, and the reader is sent to the manifest, the overlay, the adapter or the generator rather than to a compiler log.

Two things are proven at once. D4 — the `--inject-token` flag that was computed and never passed, found by containerized actionlint — is fixed, because the case is no longer inert. And the *lane* refuses a recipe that a build would otherwise consume literally, which is a different property from the generator refusing to render one; `tools/vmod_recipe_selftest.py` covers the latter, and only a live run can cover the former.

Collector: `expected 14, passed 13, failed 1, missing 0, required_failed 1`. One row, no collateral.

The sibling EL9 row passing is the point of running this at all: a generation failure on one target does not cost the other target of the same VMOD, let alone the other VMOD.

### Item 4, first attempt — [30416252749](https://github.com/boffinate/vcache-packaging/actions/runs/30416252749): stopped at the structural gate, and the defect was mine

The run never reached the injection. `structural validation and tooling selftests` failed first:

```text
File "tools/selftest.py", line 1051, in test_per_vmod_evidence
    del pending_no_reason["vmods"]["dict"]["pending_reason"]
KeyError: 'pending_reason'
```

**B10 — a self-test that depended on the work not being finished.** `test_per_vmod_evidence` read the live release target and asserted *"cachetag's is recorded, dict's is pending with a reason"*, then reached into that live entry to strip its `pending_reason` and to check that `pending` blocks release. Every one of those only held while dict's evidence did not exist. The evidence flip removed the field, and the test did not fail — it raised `KeyError` and took the whole self-test process down.

Two separate faults, and the second is the one worth recording.

**The test.** A check that depends on a particular VMOD being mid-flight stops testing anything the moment the work lands, and here it did worse than stop: it crashed. The `pending` state is now **constructed** from the recorded data inside the test, so both pending checks keep working with every VMOD complete, and a new check asserts the live file is releasable as written — the exit gate's evidence clause, against real data rather than a constructed case.

**How it reached CI.** Locally I ran `release_tool.py selftest | grep -E '^# (TOTAL|FAIL)'`. A crash prints neither line, so the filter turned a traceback into silence and I read silence as a pass. The output of the battery I ran afterwards is missing its first block entirely, and I did not notice. **Filtering a self-test's output without checking its exit status is not running the self-test.** CI, which runs it under `set -e`, caught it on the first attempt — the gate did its job.

The reconciled ledger from the failed attempt is worth keeping for one reason: the **ledger shape was already right**. It shrank to **7 expected rows** with the two `vinyl-trunk-pinned` engine rows correctly absent rather than reported missing, and cachetag collapsed to a single `failed_manifest_validation`. Everything else read `missing_result_record` or `blocked_by_vmod_source` because no job downstream of the structural gate ran at all. Item 4 is therefore **not adjudicated** and is re-dispatched below.

### Item 4, second attempt — [30416382776](https://github.com/boffinate/vcache-packaging/actions/runs/30416382776): the ledger was right, one row timed out

**The shape of the case is proven.** The ledger did exactly what ruling R-B predicted:

```text
counts: expected=7 passed=5 failed=2 missing=0 not_selected=0 required_failed=2
  vmod/cachetag                                   failed_manifest_validation
  target/dict/release/vinyl-release/el9-x86_64    failed_infrastructure
```

| Expected | Observed |
| --- | --- |
| ledger shrinks to **7** rows | **7 rows** |
| cachetag collapses to one `failed_manifest_validation` | **yes**, and its four target rows and its source row were never created |
| the two `vinyl-trunk-pinned` engine rows are **not** reported missing | **absent from the ledger entirely**, and `missing=0` |
| the two `vinyl-release` engine rows PASS | **PASS** |
| all four dict rows PASS | invocation, source and the Debian target **PASS**; the EL9 target **timed out** |

Only cachetag's lanes consumed the trunk-pinned engine, so with cachetag's manifest unparseable nobody asked for those rows, and the collector correctly does not report them missing. That is the property D2 made untestable and R-B restored, now demonstrated live rather than in a simulation.

#### The one deviation, and why it is not the injection

`target/dict/release/vinyl-release/el9-x86_64` was cancelled at its 30-minute `timeout-minutes` while Mock was still in *build setup*, and classified `failed_infrastructure` — *"the row did not reach its checksums"*. Measured from the job logs' own timestamps:

| Phase | run 30413513970 (green) | run 30416382776 (cancelled) |
| --- | --- | --- |
| Mock initialises the root | 0.5 min | 2.6 min |
| `--buildsrpm` complete | 1.2 min | **14.5 min** |
| build phase complete | 1.5 min | never — axed at 30.2 min |
| **whole EL9 lane** | **2.1 min** | — |

Every Mock phase ran ten to twenty times slower than normal. The evidence that this is environmental rather than a property of the injection:

- the injection corrupts `registry/vmods/cachetag.yml`, and nothing on dict's EL9 path reads it;
- **dict's Debian row in the same run passed normally**, from the same corrupted checkout;
- the identical EL9 row passed in runs 30413513970 and 30415386761 at 2.1 and 2.4 minutes, from the same lane code;
- cachetag's four target jobs did not exist in this run, so the runner pool was *less* contended, not more.

`mock --buildsrpm` spends its time on chroot init and package-manager metadata, so twelve minutes for what normally takes forty seconds points at mirror or runner latency. Nothing in this repository changed to cause it.

One small positive falls out of it: a row killed by its own timeout classifies as `failed_infrastructure` rather than being misattributed to the package or to the injection, which is what that status is for. The 30-minute budget is not tight — the normal run has fourteen times the headroom.

Item 4 is therefore **re-dispatched** rather than adjudicated on this run. Deviations stop the line; an unexplained slow runner is not an expected classification.

### Item 4, third attempt — [30418133557](https://github.com/boffinate/vcache-packaging/actions/runs/30418133557): the same external condition, one step earlier

```text
counts: expected=7 passed=4 failed=3 missing=0 required_failed=3
  engine/vinyl-release/el9-x86_64                 failed_infrastructure
      the engine artifact could not be staged or described
  vmod/cachetag                                   failed_manifest_validation
  target/dict/release/vinyl-release/el9-x86_64    blocked_by_engine_artifact
      engine/vinyl-release/el9-x86_64 published no engine-vinyl-release-el9-x86_64
```

This time the **engine** row was killed by its 35-minute budget, cancelled inside `Start: installing minimal buildroot with dnf` — the same package-manager phase, one job earlier in the graph. The Debian half of the run was untouched again: `engine/vinyl-release/debian-13-amd64`, dict's invocation, its source row and its Debian target all passed.

#### The blocker: EL9 package-manager phases are running ten to twenty times slow

Two consecutive runs, two different EL9 jobs, both cancelled at their timeout inside a `dnf` phase:

| Run | Job killed | Phase it died in | Elapsed |
| --- | --- | --- | --- |
| 30416382776 | `target/dict/.../el9-x86_64` | Mock `build setup` for the SRPM | 30 min (budget 30) |
| 30418133557 | `engine/vinyl-release/el9-x86_64` | Mock `installing minimal buildroot with dnf` | 30 min (budget 35) |

Against the measured normal, from run 30413513970's own log timestamps: the whole EL9 VMOD lane — Mock init, `--buildsrpm`, `--rebuild`, buildroot capture and the fresh-container verification — takes **2.1 minutes**. In 30416382776 the `--buildsrpm` alone took **14.5**.

Everything points outside this repository:

- both failures are in `dnf` metadata or download phases, not in compilation or in any script this wave changed;
- **every Debian row in both runs passed normally**, from the same checkouts and the same runners;
- the identical EL9 rows passed at 2.1 and 2.4 minutes in runs 30413513970 and 30415386761 earlier the same night, from the same lane code;
- in 30416382776 cachetag's four target jobs did not exist at all, so the pool was *less* contended, not more.

Nothing in `vcache-packaging` changed between the green EL9 rows and the timed-out ones. The lane's timeouts are not tight — the normal case has roughly fourteen times the headroom — so raising them would be treating a symptom of an upstream mirror or runner condition, and would make every future genuine hang cost half an hour instead of thirty minutes. **The line is stopped here** until EL9 jobs complete normally again; the fourth attempt below names why.

#### Root cause, from the fourth attempt: the EPEL mirrors are down

Attempt four, [30419753356](https://github.com/boffinate/vcache-packaging/actions/runs/30419753356), stopped guessing. The EL9 engine row did not time out this time — it **failed outright**, with the reason in plain text:

```text
Errors during downloading metadata for repository 'epel':
  - Curl error (28): Timeout was reached for
    http://mirror.us.mirhosting.net/epel/9/Everything/x86_64/repodata/…-filelists.xml.xz
    [Failed to connect to mirror.us.mirhosting.net port 80: Connection timed out]
  - Curl error (28): … port 443: Connection timed out
  - Curl error (18): Transferred a partial file for
    https://mirror.fcix.net/epel/9/Everything/x86_64/repodata/…-filelists.xml.xz
    [transfer closed with 7338617 bytes remaining to read]
Error: Failed to download metadata for repo 'epel': Yum repo downloading error:
  … Cannot download, all mirrors were already tried without success
```

**EPEL is unreachable from GitHub's runners.** That explains all three failures as one condition:

| Attempt | Symptom | Same cause |
| --- | --- | --- |
| 30416382776 | dict's EL9 row killed at 30 min in Mock `build setup` | dnf retrying dead EPEL mirrors |
| 30418133557 | the EL9 **engine** row killed at 35 min in `installing minimal buildroot with dnf` | same, one job earlier |
| 30419753356 | the EL9 engine row **failed** with the curl errors above | same, now fatal rather than slow |

Every EL9 job in this repository needs EPEL, and needs it early: `epel-release` before `mock` and `mock-core-configs` (B2), before `rpmlint`, and for `libunwind.so.8`, which the runtime package requires because `vinyld` is built `--with-unwind` and it ships in neither BaseOS nor AppStream. There is no EL9 path that does not touch it.

The Debian lane is untouched throughout — it resolves against `snapshot.debian.org` at a pinned timestamp, which stayed healthy — which is why **every Debian row in all four attempts passed**.

Nothing in `vcache-packaging` caused this and nothing in it can fix it. Raising the timeouts would not help, because attempt four failed rather than timing out; pinning an EPEL mirror or vendoring its metadata would be a lookaside cache, which `SCOPE.md` places explicitly out of scope. The correct response is the one `SCOPE.md`'s source policy already states: *"a failed build is an acceptable and useful signal"*. **The line stops until EPEL is reachable again.**

#### One thing this bought, unintentionally

`target/dict/release/vinyl-release/el9-x86_64` reported **`blocked_by_engine_artifact`, naming the engine row that produced nothing**. That is `target-generated`'s engine-blocked path — the one ruling R-2 moved `INJECT_ENGINE_ROW` in order to exercise for the first time — taken live, from a real infrastructure failure rather than an injection. It classified correctly and named the shared cause, which is exactly what items 10a and 10b are meant to demonstrate deliberately. It does not substitute for those runs, because it says nothing about the *cachetag* consumer of the same engine row, but it is the first evidence that the path works on the generated lane.

### Item 4, fifth attempt — [30420921127](https://github.com/boffinate/vcache-packaging/actions/runs/30420921127): **adjudicated, exact**

```text
counts: expected=7 passed=6 failed=1 missing=0 not_selected=0 required_failed=1
  vmod/cachetag    failed_manifest_validation
      registry/vmods/cachetag.yml did not validate as vmod-ci/v1
```

**One failed row, and it is the injected one.**

| Expected | Observed |
| --- | --- |
| the ledger shrinks to **7** rows | **7** |
| cachetag collapses to one `failed_manifest_validation` | **exactly one**, and its source and four target rows were never created |
| all four dict rows PASS | **all four PASS** — invocation, source, debian-13-amd64 **and el9-x86_64** |
| the two `vinyl-trunk-pinned` engine rows are **not** reported missing | absent from the ledger, `missing=0` |
| the two `vinyl-release` engine rows PASS | **PASS** |

This is ruling R-B's claim demonstrated live: a manifest that does not parse costs **its own invocation and the engine rows nothing else consumes, and nothing more**. The trunk-pinned engine rows disappear from the expected set because only cachetag's lanes asked for them, and the collector does not report as missing something nobody requested. The Wave A3 note could only simulate this; it is now a real graph.

#### The EPEL condition is intermittent, not absolute

The run's EL9 jobs completed, but the logs show what they had to survive:

```text
[MIRROR] libunwind-1.8.0-4.el9.x86_64.rpm: Curl error (28): Timeout was reached for
  https://mirror.us.mirhosting.net/epel/9/Everything/x86_64/Packages/l/libunwind-1.8.0-4.el9.x86_64.rpm
  [Failed to connect to mirror.us.mirhosting.net port 443: Connection timed out]
```

`dnf` retried the dead mirror for every EPEL package and fell back to a working one, which is why attempts two and three ran into their timeouts and attempt four gave up on metadata entirely. `libunwind` is the package that matters: `vinyld` is built `--with-unwind` and `libunwind.so.8` ships in neither BaseOS nor AppStream, so no EL9 row can avoid EPEL.

**The condition is a flaky mirror in EPEL's rotation, not an outage**, and it costs an EL9 row roughly one attempt in two at present. Recorded so the next unexplained EL9 timeout is recognised rather than re-diagnosed: look for `[MIRROR] … Curl error (28)` in the job log before suspecting the lane.

### Item 5, first attempt — [30422290121](https://github.com/boffinate/vcache-packaging/actions/runs/30422290121): the injection was inert

`dict debian-13-amd64 (vinyl-release)` — the row `inject=dict_build` names — **passed**. It was supposed to fail.

#### B11 — `matrix.inject_build` reaches `target-generated` and nothing reads it

The Phase 2 injection point is a step that runs `exit 1` immediately before the build, so that no build script is ever modified by an injection. The upstream-recipe `target` job has had one per family since Phase 2 (`deb_inject` at line 548, `el9_inject` at 597), and ruling D3 rewired both to read the per-row `matrix.inject_build` boolean rather than comparing `inputs.inject`.

`target-generated` never had one. The expansion sets `inject_build: "true"` on dict's Debian row, the workflow passes it into the job, the job ignores it, the build succeeds, and the row passes. **`inject=dict_build` produced a green run.**

This is the third inert-injection defect of the wave, after D1 (the dict source injection whose rewritten `ref` never reached the check) and D4 (the `--inject-token` flag computed and never passed). All three share a cause: the generated-recipe path was built after the review pass that found the first two, and `inject_build` was never re-checked against the new jobs. The self-tests could not catch it — they assert the *expansion* emits the flag, which it does; what was missing was a consumer, and only a live run can show that a flag nobody reads produces a green row.

**Fix:** `target-generated` gains the same step, gated on `matrix.inject_build == 'true'` and placed after `generate` and before `build`, plus the matching `failed_package_build "injected package-build failure"` branch in its classification chain — ahead of the real build-failure branch, so an injected failure is never reported as a genuine one.

Item 5 is re-dispatched against the fix.

#### And a second EL9 row lost to the mirror

`engine/vinyl-release/el9-x86_64` was cancelled again in the same run, while `engine/vinyl-trunk-pinned/el9-x86_64` — the same build, the same image, a different job — completed. That is the per-job mirror lottery described above, and it is why this run has no reconciled ledger: the collector could not run.

### Item 5, second attempt — [30424259052](https://github.com/boffinate/vcache-packaging/actions/runs/30424259052): lost to the same row again

`engine/vinyl-release/el9-x86_64` cancelled at its budget for the **third consecutive run**, while `engine/vinyl-trunk-pinned/el9-x86_64` — same image, same script, same runner pool, different Vinyl source — passed in every one of them. No reconciled ledger, so item 5 is still unadjudicated and B11's fix is unverified live.

The concentration on one row looked like a lead. **It was not one.** Measured from the two runs where both EL9 engine rows completed, using the job logs' own timestamps:

| Run | `vinyl-release` el9 | `vinyl-trunk-pinned` el9 |
| --- | --- | --- |
| 30413513970 | 3.3 min | 3.5 min |
| 30415386761 | 3.5 min | 4.3 min |

The **trunk** row is the slower of the two, not the release row, and both sit about ten times inside their 35-minute budget. There is no margin difference for the mirror retries to push over, so the hypothesis that the release row is the marginal one is refuted rather than merely unproven.

Three consecutive losses on one of two equally-exposed rows is unremarkable at the observed per-job failure rate — one specific row losing three times running at roughly even odds is a one-in-eight coincidence, and one-in-eight things happen. **Recorded as mirror luck**, so nobody spends time hardening a row that has nothing wrong with it. The budgets are not the problem either: at 3.5 minutes normal against 35 allowed, a row that dies at its budget was not slightly slow, it was stuck.

### Item 5, third attempt — [30425966069](https://github.com/boffinate/vcache-packaging/actions/runs/30425966069): **adjudicated, exact**

EPEL was healthy: all four engine rows green, both EL9 rows included.

```text
counts: expected=14 passed=13 failed=1 missing=0 not_selected=1 required_failed=1
  target/dict/release/vinyl-release/debian-13-amd64   failed_package_build
      injected package-build failure
```

| Expected | Observed |
| --- | --- |
| dict's Debian row `failed_package_build` | **`failed_package_build`**, detail *"injected package-build failure"* |
| dict's EL9 row PASS | **PASS** |
| all six cachetag rows PASS | **PASS** |
| four engine rows PASS | **PASS** |

**B11's fix is verified live.** The detail string is the decisive part: it reads *"injected package-build failure"*, from the branch placed ahead of the real build-failure branch, so the row is attributed to the injection rather than to a genuine build fault. Two attempts earlier this same case produced a green run.

### Item 6 — `inject=debian_build`, [30427292013](https://github.com/boffinate/vcache-packaging/actions/runs/30427292013): **exact**

```text
counts: expected=14 passed=12 failed=2 missing=0 not_selected=1 required_failed=2
  target/cachetag/release/vinyl-release/debian-13-amd64        failed_package_build
  target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64   failed_package_build
      injected package-build failure
```

Cachetag's two Debian target rows red, its two EL9 rows and its source and invocation green, **all four dict rows green**, all four engine rows green.

This is the other direction of the two-way isolation property, and the half D3 broke: before that ruling, `inputs.inject == 'debian_build'` was a workflow-level comparison that fired in *every* VMOD's invocation, so this case would have failed dict's Debian row too and demonstrated a broken run rather than a contained one. The per-row `matrix.inject_build` boolean confines it to the VMOD the case names.

### Item 7 — `inject=el9_build`, [30429219759](https://github.com/boffinate/vcache-packaging/actions/runs/30429219759): **exact**

```text
counts: expected=14 passed=12 failed=2 missing=0 not_selected=1 required_failed=2
  target/cachetag/release/vinyl-release/el9-x86_64        failed_package_build
  target/cachetag/release/vinyl-trunk-pinned/el9-x86_64   failed_package_build
      injected package-build failure
```

The mirror image of item 6: cachetag's two EL9 rows red, its two Debian rows green, **all four dict rows green**, all four engine rows green. Together the two cases show the injection following the *target family* within one VMOD and never crossing into the other.

### Item 8a — `inject=source_checkout`, [30430481913](https://github.com/boffinate/vcache-packaging/actions/runs/30430481913): **exact**

```text
counts: expected=14 passed=9 failed=5 missing=0 not_selected=1 required_failed=5
  source/cachetag/release                                      failed_source_checkout
      checkout of boffinate/libvmod-cachetag@vmod-ci-injected-missing-ref failed
  target/cachetag/release/vinyl-release/debian-13-amd64        blocked_by_vmod_source
  target/cachetag/release/vinyl-release/el9-x86_64             blocked_by_vmod_source
  target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64   blocked_by_vmod_source
  target/cachetag/release/vinyl-trunk-pinned/el9-x86_64        blocked_by_vmod_source
      source artifact vmod-source-cachetag-release was not available
```

One source failure, four consumers blocked and each naming the artifact that was never produced, and **all four dict rows plus all four engine rows green**. `missing=0`: a blocked row is a classified result, not an absent one, which is the distinction the collector exists to make.

The largest blast radius any single injection has produced — five of fourteen rows — and it is still exactly the set that depends on the failed one.

### Item 8b — `inject=source_digest`, [30431584255](https://github.com/boffinate/vcache-packaging/actions/runs/30431584255): **exact**

```text
counts: expected=14 passed=9 failed=5 missing=0 not_selected=1 required_failed=5
  source/cachetag/release   failed_source_digest
      derived archive digest does not match 000000000000000000000000000000000000…
  … the same four cachetag targets, blocked_by_vmod_source
```

Identical blast radius to 8a and a **different source status**, which is the point of running both: the two failures are distinguishable at the source row — one says the ref did not resolve, the other says the bytes were not the recorded ones — while the consumer classification is the same, because from a consumer's position the two are the same event. All four dict rows and all four engine rows green.

### Item 9 — `inject=suppress_result`, [30432639448](https://github.com/boffinate/vcache-packaging/actions/runs/30432639448): **exact**

```text
counts: expected=14 passed=13 failed=1 missing=1 not_selected=1 required_failed=1
  target/cachetag/release/vinyl-release/debian-13-amd64   missing_result_record
      no result record was uploaded for this expected row
```

**`missing=1`** — the only run of the whole sequence with a non-zero missing count, and that is the case. The row's job *succeeded*: it built its packages and uploaded them, and then skipped uploading its result record. Nothing failed and nothing reported, and the run went red anyway because the collector rebuilds the expected ledger from the catalog and reconciles against it rather than trusting whatever records happen to arrive.

That is the property the whole collector design exists for: a row that silently produces no evidence is indistinguishable from a row that never ran, and both must be louder than a green run. All other thirteen rows passed, dict included.

### Item 10a — `inject=engine_build`, [30434296849](https://github.com/boffinate/vcache-packaging/actions/runs/30434296849): **exact, and the reason R-2 was worth doing**

```text
counts: expected=14 passed=11 failed=3 missing=0 not_selected=1 required_failed=3
  engine/vinyl-release/debian-13-amd64                    failed_engine_build
      injected engine-build failure
  target/cachetag/release/vinyl-release/debian-13-amd64   blocked_by_engine_artifact
      engine/vinyl-release/debian-13-amd64 published no engine-vinyl-release-debian-13-amd64
  target/dict/release/vinyl-release/debian-13-amd64       blocked_by_engine_artifact
      engine/vinyl-release/debian-13-amd64 published no engine-vinyl-release-debian-13-amd64
```

**One root cause, two consumers, one in each VMOD, both naming the cause.** This is the case the matrix plan asks the summary to report as a shared dependency failure rather than as unrelated broken jobs, and under the old constant it could not have shown it: `engine/vinyl-trunk-pinned/debian-13-amd64` has one consumer, so the run would have looked exactly like a single-VMOD build failure.

It is also **the first deliberate exercise of `target-generated`'s `blocked_by_engine_artifact` path**. The upstream-recipe lane has had that path since Phase 2; the generated-recipe lane had never taken it until an EPEL failure took it by accident earlier tonight, and never on purpose until now.

The three surviving engine rows and the remaining eight package rows all passed — including dict's EL9 row, which names a different engine row and is untouched.

### Remaining dispatches

The full expected result for each, stated from the ledger so the next run can be adjudicated without re-deriving it:

| # | `inject=` | Expected |
| --- | --- | --- |
| 10b | `suppress_engine_artifact` | same blocked set, from a *green* producer that published nothing |

Every one of those expectations was read back out of `ci_matrix.py expand` before dispatching, so the adjudication is against the tool rather than against memory:

```text
dict_build        inject_build on target/dict/release/vinyl-release/debian-13-amd64
debian_build      inject_build on cachetag's two debian-13-amd64 rows
el9_build         inject_build on cachetag's two el9-x86_64 rows
source_checkout   source/cachetag/release ref -> vmod-ci-injected-missing-ref
source_digest     source/cachetag/release archive_sha256 -> 0000...
suppress_result   suppress_result on target/cachetag/release/vinyl-release/debian-13-amd64 only
engine_build      inject_build on engine/vinyl-release/debian-13-amd64 (R-2)
suppress_engine_artifact
                  suppress_artifact on the same engine row
```

Dispatch discipline, learned the hard way in run 5: **one run at a time**. Two runs sharing the runner pool pushed a cachetag EL9 row past its 35-minute budget and GitHub cancelled it. There is no `concurrency:` group to serialise them.

## The ten defects, in one table

Wave B took ten defects to reach a green baseline and a green releasable gate. **Not one of them was a defect in a package.** Every `vmod-dict` package produced from run 2 onwards was correct; what kept failing was the machinery that builds, inspects and records them. That is the shape a first live proof should have, and it is the argument for running one.

| # | Where | What | Found by |
| --- | --- | --- | --- |
| B1 | Debian recipe template | `parallel_build: "no"` reached the spec and not `debian/rules`; a real `make` race | run 30405770446 |
| B2 | EL9 lane script | `mock` is in EPEL, not AlmaLinux; `epel-release` was not installed first | run 30405770446 |
| B3 | Debian payload allowlist | rejected the recipe's own `lintian-overrides` file | run 30407186693 |
| B4 | EL9 lane script | mock refuses to run as root; no `mockbuild` user | run 30407186693 |
| B5 | both hardening checks | asserted a canary *symbol*, which a source without canary-worthy buffers never emits | run 30409242057 |
| B6 | EL9 payload allowlist | rejected RPM's `/usr/lib/.build-id` debuginfo farm | run 30409242057 |
| B7a | recipe generator | changelog line carrying the 64-char digest exceeded lintian's 80 columns | run 30410876882 |
| B7b | upstream man page | `rst2man` selects font `C`; needed a reviewed override | run 30410876882 |
| B7c | `yaml_subset` parser | refused a *quoted* scalar containing `": "` — the fix its own diagnostic recommends | reached while fixing B7b |
| B8 | ported VTC bindings | `varnish v1` addressed a driver command `vinyltest` does not register | run 30410876882 |
| B9 | Debian uniqueness check | counted the tree the hardening stage had extracted; the RPM script had pruned it since it was written | run 30412067149 |
| B10 | `release_tool` self-test | asserted dict's evidence was `pending`, so recording it raised `KeyError` rather than failing | run 30416252749 |

Three of them — B3, B6 and B9 — are the same class: a lesson one backend's script had learned and the other had not. That class was closed by sweeping the two allowlists side by side after B6 and the two verify scripts side by side after B9, rather than by patching whichever one failed. It is the measured cost of the deliberate lane duplication the [Wave A2 Q2 ruling](20260728_2334_note_step-6-wave-a2-ci-integration.md) accepted, and it is higher than "some duplicated lines": every non-obvious thing the cachetag scripts had learned had to be rediscovered, sometimes by failing.


## Where Wave B stands

| Item | Run | Verdict |
| --- | --- | --- |
| 1 baseline | [30413513970](https://github.com/boffinate/vcache-packaging/actions/runs/30413513970) | **PASS** — 14/14, `success` |
| 2 `dict_source` | [30414399323](https://github.com/boffinate/vcache-packaging/actions/runs/30414399323) | **PASS** — exact |
| 3 `recipe_generation` | [30415386761](https://github.com/boffinate/vcache-packaging/actions/runs/30415386761) | **PASS** — exact |
| 4 `manifest` | [30420921127](https://github.com/boffinate/vcache-packaging/actions/runs/30420921127) (5th attempt) | **PASS** — exact; four earlier attempts lost EL9 rows to a flaky EPEL mirror |
| 5 `dict_build` | [30425966069](https://github.com/boffinate/vcache-packaging/actions/runs/30425966069) (3rd attempt) | **PASS** — exact; attempt 1 found **B11**, attempt 2 lost an EL9 row to the mirror |
| 6 `debian_build` | [30427292013](https://github.com/boffinate/vcache-packaging/actions/runs/30427292013) | **PASS** — exact |
| 7 `el9_build` | [30429219759](https://github.com/boffinate/vcache-packaging/actions/runs/30429219759) | **PASS** — exact |
| 8a `source_checkout` | [30430481913](https://github.com/boffinate/vcache-packaging/actions/runs/30430481913) | **PASS** — exact |
| 8b `source_digest` | [30431584255](https://github.com/boffinate/vcache-packaging/actions/runs/30431584255) | **PASS** — exact |
| 9 `suppress_result` | [30432639448](https://github.com/boffinate/vcache-packaging/actions/runs/30432639448) | **PASS** — exact, `missing=1` |
| 10a `engine_build` | [30434296849](https://github.com/boffinate/vcache-packaging/actions/runs/30434296849) | **PASS** — exact; both VMODs blocked by one cause |
| 10b | in progress | |
| 11 equivalence | run 30413513970 vs `main` 30397392846 | **PASS** — 10 `.deb` byte-identical, 18 RPMs equivalent |
| 12 case 8 | one-off containers | **PASS** — both targets refuse, both name the dependency |
| 13 evidence flip | run 30413513970 + the upgrade matrix | **PASS** — `--require-releasable` exits 0 |

Item 4's expectation is *partly* adjudicated and the part that matters most is settled: on all three attempts that got past the structural gate, the ledger shrank to **7 rows**, cachetag collapsed to exactly one `failed_manifest_validation`, the two `vinyl-trunk-pinned` engine rows were **absent rather than reported missing** (`missing=0` every time), and dict's invocation, source and Debian rows passed. Only the EL9 half is unproven, and it is unproven for a reason that has nothing to do with the injection.

## Step 6 exit gate

| Clause | Verdict |
| --- | --- |
| A second real VMOD is packaged without requiring upstream Debian or RPM files | **MET.** Run 30413513970, 14/14 green: `vmod-dict` 1.7 built from recipes generated here on both selected targets, upstream ships no packaging, none was vendored or forked |
| Generated recipes are deterministic and validated | **MET.** 146 generator self-tests; regeneration byte-identical; unresolved tokens refused by the generator and again by the lane, the latter proven live in item 3 |
| A failure in either VMOD does not hide the other VMOD's results | **PARTIALLY MET.** dict→cachetag proven twice (items 2 and 3, both exact). cachetag→dict proven on the Debian half in item 4's three attempts. The remaining seven cases are blocked on EPEL |
| Both package families meet the same evidence policy as cachetag | **MET.** Both `vmods.dict` entries are `recorded` against the same schema and the same `--require-releasable` policy as cachetag's, which exits 0 with no errors |

Three of four clauses are met. The fourth is met in one direction and blocked in the other by an upstream outage rather than by anything in this repository, which is the one thing a Step 6 exit gate should not be signed off around.
