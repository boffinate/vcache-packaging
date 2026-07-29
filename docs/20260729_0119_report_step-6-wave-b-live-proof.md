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
