# Step 7 Wave 0: lane consolidation

Date: 2026-07-29

Status: **Implemented, pending the CI equivalence gate.** Every claim below is verified locally, in containers, against the artifacts of the last green `main` run. The authoritative equivalence proof is a baseline CI run whose cachetag artifacts must be byte-identical (Debian) and semantically equivalent (EL9) to [30437775658](https://github.com/boffinate/vcache-packaging/actions/runs/30437775658). Nothing here is pushed.

Branch: `step7-lane-consolidation`, off `main` at `d925b56`.

Related:

- [Wave B live proof](20260729_0119_report_step-6-wave-b-live-proof.md) — the ten defects, the allowlist sweep table, and the carry-forward items this wave closes
- [Wave A2: CI integration](20260728_2334_note_step-6-wave-a2-ci-integration.md) — the Q2 duplication ruling and the Q3 asymmetry ruling, both of which said "merge later, in a change whose only purpose is that"
- [vmod-packager patterns and recipe generation](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md) — "gates identical in strength regardless of recipe strategy"

## Why now, and what it cost not to do it earlier

Wave B needed ten defects to reach a green baseline. **Three of them — B3, B6 and B9 — were the same shape**: a lesson one backend's script had learned and the other had not. Two more, B2 and B4, were the same shape one level up: lessons `container-mock.sh` had recorded in prose and `build-rpm.sh` had to rediscover by failing in CI.

That was the measured price of the Q2 ruling, and the ruling was right: keeping the lanes apart made "cachetag's package bytes did not move" an empty diff rather than an argument. The price was paid to buy that proof. It has been bought. A third VMOD lane arrives in Wave 1, and paying it a third time would be a decision rather than an inheritance.

So this wave consolidates once, and the empty-diff era ends here **by design**. Every hunk in a cachetag-lane file is justified below.

## What was consolidated, and where

Four new files under `scripts/ci/lib/`, plus one moved there.

| File | One implementation of | Consumed by |
| --- | --- | --- |
| `lib/pbuilder.sh` | the Debian clean room: toolchain, apt resolver, `D05update` hook, base-tarball compression, local repository, `pbuilder_build_one` | `debian13/container-pbuilder.sh`, `vmod/container/build-deb.sh` |
| `lib/mock.sh` | the EL9 clean room: EPEL-before-mock, the unprivileged build user, the derived config, the epoch macros, `--addrepo`, the EXIT log trap | `el9/container-mock.sh`, `vmod/container/build-rpm.sh` |
| `lib/package-checks.sh` | the build-flag assertion, the ELF hardening inspection, the two payload allowlists | `debian13/assert-packages.sh`, `el9/container-mock.sh`, `vmod/container/verify-deb.sh`, `vmod/container/verify-rpm.sh` |
| `lib/vtc-suite.sh` | fixture staging and the installed-package behaviour suite | `vmod/container/verify-deb.sh`, `vmod/container/verify-rpm.sh` |
| `lib/check-build-flags.sh` | moved from `vmod/container/`; unchanged except its header | `lib/package-checks.sh`, the only caller |

**Delivery differs, the file does not.** The cachetag lanes reach `scripts/ci/lib/` through a repository checkout on the runner (`assert-packages.sh`) or a read-only mount (`/repo` for the Debian container, `/ci` for the EL9 one). The generated-recipe verify stages mount **only** their lane directory — deliberately, because a container that has never seen the build tree is the whole point — so `generate.sh` copies `package-checks.sh`, `vtc-suite.sh` and `check-build-flags.sh` into `lane/scripts/` beside the verify scripts. `PC_LIB_DIR` resolves from `BASH_SOURCE`, so `check-build-flags.sh` is found in either layout without a caller knowing which one it is in.

### The one-implementation inventory

Every lesson the brief names, and where it now lives exactly once:

| Lesson | Cost of learning it | Now in |
| --- | --- | --- |
| `epel-release` before `mock`; `mock` is not in AlmaLinux 9 | B2, one CI round trip | `mock.sh:mock_install_toolchain` |
| mock refuses to run as root; `/usr/bin/mock` is consolehelper and exits 6 | B4, one CI round trip | `mock.sh:mock_setup_build_user` |
| the build user takes the bind mount's uid so results land owned by the caller | — | `mock.sh:mock_setup_build_user` |
| `config_opts['root']` pinned to the STOCK config's name | rediscovered when writing `build-rpm.sh` | `mock.sh:mock_derived_config` |
| `SOURCE_DATE_EPOCH` through `config_opts['environment']` | the 1.0.0-1 evidence recorded a changelog-derived epoch | `mock.sh:mock_derived_config` |
| both epoch macros as `--define` on **every** invocation | measured; config macros alone are not the good form | `mock.sh:mock_epoch_defines` for both Mock drivers; **still duplicated in the local `rpmbuild` lane**, see below |
| `--addrepo`, not `mock --install` plus `--no-clean` | run 30167536066 | `mock.sh:mock_publish_localrepo` + a header note |
| EXIT trap copying `build.log` and `root.log` | run 30344401137's EL9 failures were undiagnosable | `mock.sh:mock_install_log_trap` |
| pbuilder's apt resolver (`aptitude` is absent from a buildd chroot) | first Debian CI run | `pbuilder.sh:pbuilder_configure` |
| the `D05update` hook (mmdebstrap ships cleaned apt lists) | first Debian CI run | `pbuilder.sh:pbuilder_configure` |
| `procps`, or every run logs `sysctl: command not found` | — | `pbuilder.sh:pbuilder_install_toolchain` |
| the engine `.debs` published as a local repository | — | `pbuilder.sh:pbuilder_publish_localrepo` |
| the build log must be written as the build runs, not copied after | the EL9 trap's reasoning, now applied to Debian too | `pbuilder.sh:pbuilder_build_one` |
| the payload allowlist and its three measured backend asymmetries | B3, B6 | `package-checks.sh:pc_assert_deb_payload` / `pc_assert_rpm_payload` |
| `\|\| true` on every allowlist filter | a latent abort on an all-allowed payload | the same two functions |
| assert the flag, not the canary | B5 | `package-checks.sh:pc_verify_build` |
| `dpkg-deb -c \| grep -q` is a SIGPIPE trap under `pipefail` | cost a run in the cachetag work | `pc_assert_deb_payload` |
| `debug=+vclrel`, and why 9.0.1 needs it | — | `vtc-suite.sh:vtc_run_suite` for the generated lane; **still in four cachetag-lane files**, see below |

### Two lessons this wave did NOT reduce to a single copy

Both are carry-forwards with a named reason, and both were miscounted in the first draft of this note. The counts below are from `grep -rl` over `scripts/` and `recipes/`, not from memory.

**`debug=+vclrel` is in FIVE files, not three.** The first draft said three and was wrong: it counted the VTC suites and missed the two smoke stages, which drive a daemon under the same 9.0.1 teardown condition and carry the same flag for the same reason.

| File | Lane | Role |
| --- | --- | --- |
| `scripts/ci/lib/vtc-suite.sh` | generated-recipe, both targets | the consolidated copy |
| `recipes/debian-13/container/stage-vtc-suite.sh` | cachetag Debian | full VTC suite |
| `recipes/debian-13/container/stage-smoke.sh` | cachetag Debian | installed-package smoke |
| `recipes/el9/vtc-suite/vtc-suite.sh` | cachetag EL9 | full VTC suite |
| `recipes/el9/smoke/smoke.sh` | cachetag EL9 | installed-package smoke |

All five carry the same reasoning and the same removal condition (a Vinyl containing `7de492b0e8`). The four cachetag copies run under three different mount contracts and drive fixed suites rather than declared ones, so folding them in means designing the fixture contract first — Wave 1's job. **The open question below needs this count, not the wrong one:** consolidating is four call sites to rewrite, not two.

**The two EL9 epoch macros are still in three places.** `mock.sh:mock_epoch_defines` is the consolidated copy for both Mock drivers, but the local whole-cohort lane keeps its own:

| File | Line | Why it was not folded in |
| --- | --- | --- |
| `scripts/ci/lib/mock.sh` | `mock_epoch_defines` | the consolidated copy, both Mock drivers |
| `recipes/el9/container/build.sh` | 39 | `rpmb()` wraps `rpmbuild`, not `mock`; it takes the macros as `--define` on a different tool, and the container mounts only `/recipes` |
| `recipes/el9/mismatch/container.sh` | 199 | the mismatch fixture's own `rpmbuild`, same mount problem |

Both are the *local* lane, which CI does not run, and both would need the `/recipes` mount widened the way `mock-build.sh`'s was. Listed here beside `debug=+vclrel` so the carry-forward is one list rather than two half-remembered ones.

## The three asymmetry settlements

All three change **checks**, not packages. Each reads a package or a log that already exists; none touches a file any recipe builds from. The byte-neutrality argument is the same for all three and is stated per item below anyway, because "it only reads things" is exactly the claim that deserves to be checked rather than assumed.

### (a) lintian: cachetag rises to `--fail-on error,warning`

`recipes/debian-13/container/stage-lint.sh` now runs `lintian -i -I --pedantic --no-tag-display-limit --fail-on error,warning`. Before this it ran lintian's default, which fails only on error-level tags — so a warning nobody had reviewed passed silently on the **audited** recipe while failing the generated one. The recipe-generation plan's "gates identical in strength regardless of recipe strategy" clause exists to stop a generated-recipe VMOD sneaking through weaker gates; it was being violated in the other direction.

**No overrides were added, because nothing fires.** Measured before the change, in a `debian:13` container with lintian 2.122.0, against the real packages of the green baseline:

| Channel | `.changes` files linted | Error tags | Warning tags | Gate |
| --- | --- | --- | --- | --- |
| `vinyl-release` | 4 (cachetag amd64 + source, vinyl amd64 + source) | 0 | 0 | exit 0 |
| `vinyl-trunk-pinned` | 4 | 0 | 0 | exit 0 |

What the informational pass does report is two `I:` tags (`unused-license-paragraph-in-dep5-copyright`, `file-references-package-build-path`) and three `P:` tags (prebuilt JavaScript and Sphinx output in Vinyl's own doc tree). `--fail-on error,warning` does not fail on those, and they stay visible, which is what info and pedantic levels are for.

The `W: wrong-manual-section 3 != 4` recorded in the [step-2 epoch and lint gates note](20260728_1052_note_step-2-epoch-and-lint-gates.md) — the finding that made the earlier "free today" judgement — **no longer fires**: cachetag 1.0.1's manual page is section 3. Had it still fired, this settlement would have needed one reviewed override; it did not.

**Byte neutrality:** lintian reads `.changes` files produced by an earlier stage and its exit status gates the lane. No recipe input changed, and the `.lintian-overrides` files are untouched.

### (b) payload: explicit allowlists on both families

The generated lane has had an explicit payload allowlist since Wave A2. The cachetag lane's whole payload check was *"the `.so` is present, and no `.la` or `.a`"* — so anything else `make install` happened to produce would have shipped unnoticed, on both targets.

Both cachetag packages now go through the same `pc_assert_deb_payload` / `pc_assert_rpm_payload` the generated lane calls:

- Debian, in `assert-packages.sh`, after the ABI assertions;
- EL9, in `container-mock.sh`, after the cachetag `--rebuild`.

The sweep table from the Wave B report was re-read against the **cachetag** payloads rather than dict's, and every recorded asymmetry survives with the same reason:

| Path class | Debian (`dpkg-deb -c`) | EL9 (`rpm -qpl`) | Verdict against cachetag's real payload |
| --- | --- | --- | --- |
| VMOD object | `/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods/libvmod_cachetag.so` | `/usr/lib64/vinyl-cache/vmods/libvmod_cachetag.so` | allowed on both |
| manual page | `man3/vmod_cachetag.3.gz` | `man3/vmod_cachetag.3.gz` | allowed on both |
| documentation | 5 files under `/usr/share/doc/libvmod-cachetag/` | 3 files under the same | allowed on both |
| licence text | `…/copyright`, covered by the doc rule | `/usr/share/licenses/…/LICENSE`, allowed explicitly | symmetric in effect; `%license` has no Debian twin |
| **lint override shipped by the recipe** | `/usr/share/lintian/overrides/libvmod-cachetag`, **present**, allowed by exact name | none shipped | **B3's lesson applies to cachetag too** — its recipe ships one, and a directory-shaped rule would have been the wrong fix |
| debuginfo build-id links | in the separate `-dbgsym` package this check never inspects | `/usr/lib/.build-id/**` in the **main** package | B6's rule needed on EL9, must not exist on Debian |
| directory entries | trailing `/`, filtered by shape | indistinguishable from files, matched by path | structural, handled on both sides |
| libtool archives / static libraries | rejected | rejected | symmetric |

Verified against the baseline's real packages in both distro userlands: cachetag's `.deb` and `.rpm` and dict's `.deb` and `.rpm` all pass the shared allowlist **unchanged**, and synthesized negatives (a stray `/etc` file on both; another binary's lintian override on Debian) are rejected.

Two things this settlement does **not** do, both deliberate:

- **The engine packages get no allowlist.** `vinyl-cache` is a daemon with a large and legitimately open-ended payload; an allowlist for it would be a different design decision, not this one. The sweep's subject is VMOD payloads.
- **`dh_missing --fail-missing` still runs on neither family.** That was the Wave A1 decision and this wave does not reopen it. The allowlist is a strictly stronger check for the property that matters here — it asserts what *is* in the package rather than what the build system *did not* install — and having both would be belt and braces on a package with four files.

**Byte neutrality:** both functions read a built package with `dpkg-deb -c` / `rpm -qpl` and compare strings.

### (c) hardening: the flag assertion on both cachetag targets, and the canary demoted

Wave B's B5 ruling moved the stack-protector verdict from the binary to the build log for the generated lane and recorded cachetag's as an unfixed asymmetry: *"cachetag asserts a symptom that happens to be present, dict asserts the cause."* Both cachetag targets now assert the cause.

| Property | Where observable | Treatment, all four rows |
| --- | --- | --- |
| `relro` (`GNU_RELRO`) | linked object | binary assertion, fatal |
| `bind-now` (`BIND_NOW`) | linked object | binary assertion, fatal |
| `pic` (ELF type `DYN`) | linked object | binary assertion, fatal |
| `-fstack-protector-strong` | compile line | **build-log assertion, fatal** |
| `-D_FORTIFY_SOURCE=2` | compile line | **build-log assertion, fatal — new to every lane** |
| `__stack_chk_fail` symbol | linked object, if the source has a canary-worthy function | corroborating; absence is never a failure |
| `__*_chk` symbols | linked object, if the source calls a fortifiable libc function | corroborating; absence is never a failure |

**`-D_FORTIFY_SOURCE=2` is a finding of this wave, not a request in the brief.** The old `fortify-source` check looked for `__*_chk` symbols, which appear only if some translation unit called a fortifiable libc function — the *identical* defect class B5 found in the canary check, sitting one line below it, unnoticed. Demoting the symbol check without asserting the flag would have been a straight weakening, so the flag joined the asserted list. Measured on all four green rows of run 30437775658 before being written down: every `libtool: compile:` line carries both flags (cachetag 7 lines on each of Debian and EL9, dict 2 on each).

**Where the Debian evidence comes from.** The cachetag lane captured no build log at all — `pbuilder`'s output went only to the job log, which the later stages cannot read. `pbuilder_build_one` now tees it into `dist/debian-13/logs/pbuilder-<package>.log`, written as the build runs so a *failing* build still leaves it behind, and published for free because the row already uploads `logs/**`.

**The engine keeps its symbol checks as hard failures, and says why.** `pc_verify_build` has two named forms and no optional argument: `log FILE` asserts the flags and demotes the symbols, `nolog REASON` keeps them fatal and prints the reason. `vinyld` gets `nolog`, for two reasons that both have to be fixed before it can change: a VMOD row *downloads* the engine and has no build log for it at all, and `libtool: compile:` selects libtool-built objects while `vinyld` is a program, so asserting flags from an engine log would be a statement about the convenience libraries dressed up as one about the daemon.

**The demotion is load-bearing, and here is the proof.** In both distro userlands, against the baseline's real objects: `pc_verify_build … log <real log>` passes on cachetag's `.so` and on dict's; `pc_verify_build … nolog` — the old, symbol-only check — passes on cachetag's and **fails on dict's**. That failing case is B5 restated as a test, and it is why "cachetag passes it too" was never evidence.

**Byte neutrality:** the flag assertion greps a log; the ELF inspection runs `readelf` over a package extracted into `/tmp`. The one new *write* anywhere is the tee'd log file, which lands in `logs/` and in the artifact, not in a package.

### The fourth asymmetry, found by the sweep and left standing

Reading the two lint gates side by side turned up one going the other way, which nothing had recorded.

**The cachetag EL9 lint gate is stricter than the generated lane's.** `recipes/el9/container/build.sh`'s `stage_lint` filters through the reviewed `rpmlint-waivers.rpmlintrc` and then asserts the summary line reads `0 errors, 0 warnings`; `verify-rpm.sh` propagates rpmlint's exit status, which is non-zero only for *errors*. On the green baseline, dict's row reported **`1 packages and 0 specfiles checked; 0 errors, 6 warnings`** and passed:

```text
vmod-dict.x86_64: W: summary-not-capitalized C dictionary look-up VMOD for Vinyl Cache
vmod-dict.x86_64: W: spelling-error %description -l en_US initialisation -> …
vmod-dict.x86_64: W: spelling-error %description -l en_US whitespace -> …
vmod-dict.x86_64: W: spelling-error %description -l en_US ci -> …
vmod-dict.x86_64: W: spelling-error %description -l en_US unresolvable -> …
vmod-dict.x86_64: W: invalid-license GPL-3.0-or-later
```

It is **not** closed in this wave, and the reason is the wave's own contract. Closing it needs one of two things:

1. **Fixing the findings.** `summary-not-capitalized` is a real one and the fix is one character in the overlay's `Summary`. But that moves dict's package bytes, and dict's artifact digests are *recorded release evidence* in `registry/targets/`; a byte change would silently invalidate them and `--require-releasable` would go on passing against a stale record. Wave 0's whole premise is that any byte movement has one attributable cause, and "a lint tidy-up" is not one.
2. **An rpmlint-override mechanism in the overlay** — the twin of the `lintian_overrides` list that already exists and that B7b/B7c built. There is none: the schema has no rpmlint field, the generator renders no `.rpmlintrc`, and the verify stage passes no `-f`. The last four warnings are dictionary entries and a tool that predates SPDX, exactly the class the cachetag waiver file already handles with written reasons.

Both are Wave 1 work, and (2) is the honest fix. Recorded here and in a comment at the check itself so the next reader finds a measured asymmetry rather than an oversight.

## Per-hunk justification, cachetag-lane files

`git diff main` over the files that produce cachetag's packages, hunk by hunk. Two classes only: **byte-neutral consolidation** (the same commands, from a shared function) and **check-strengthening with no byte effect**.

### `scripts/ci/debian13/container-pbuilder.sh`

| Hunk | Class | Justification |
| --- | --- | --- |
| header rewritten | comment | The sbuild-elimination history moved to `lib/pbuilder.sh`, where the decision now lives. No code. |
| `. /repo/scripts/ci/lib/pbuilder.sh` | consolidation | The `/repo` mount already existed for `pins.env`. |
| `PBUILDER_BASE_TGZ=/base.tgz` | consolidation | Same path, now named so the shared function can read it. |
| toolchain / config / hooks / base tgz blocks replaced by four calls | consolidation | Identical `apt-get` line, identical `/etc/pbuilderrc`, identical hook, identical `gzip -1 -c`. |
| `build_one` deleted; two `pbuilder_build_one` calls | consolidation | Same `dpkg-buildpackage -S -us -uc -d` in the same directory with the same epoch; same `pbuilder build` flags in the same order with the same `.dsc` last. **Verified by command trace, below.** |
| the build is tee'd into `logs/pbuilder-<package>.log` | check-strengthening | New file in `logs/`, which is uploaded, not packaged. It is the evidence settlement (c) asserts from. |
| `mkdir -p "$logdir"` | consolidation | The directory `build.sh` already creates; belt and braces for the CI row that starts mid-lane. |

### `scripts/ci/el9/container-mock.sh`

| Hunk | Class | Justification |
| --- | --- | --- |
| header: mount list and the shared-driver pointer | comment | — |
| `. /ci/lib/mock.sh`, `. /ci/lib/package-checks.sh` | consolidation | Reachable because `mock-build.sh` widened the mount by one directory. |
| `copy_mock_log` / `copy_mock_logs` / `trap` replaced by `mock_watch_logs` ×2 + `mock_install_log_trap` | consolidation | Same source files, same destination names, same tolerance of an absent log, same EXIT trap registered at the same point. |
| `dnf` installs replaced by `mock_install_toolchain cpio binutils` | consolidation + container tooling | `epel-release` still first and alone. `createrepo_c` moves from just-in-time to up-front, and `rpm-build`, `cpio` and `binutils` are added. All four are **container** packages; the buildroot is Mock's chroot and is untouched. |
| build-user block replaced by `mock_setup_build_user /out …` | consolidation, with one behaviour change | Same uid/gid derivation from the same `/out` mount, same group, same `usermod -aG mock`, same `mock_as`. **Changed:** the user is created only if the uid is free, and a root-owned mount warns and falls back to uid 1000 **unless `CI=true`, where it is fatal** (audit ruling D5). On a Linux runner `/out` carries the runner's uid, so neither branch is reachable in CI. Locally on macOS the old code stopped dead; the new one runs. |
| `-e "CI=${CI:-}"` added to the `docker run` | check-strengthening | Docker gives the container a fresh environment, so the D5 guard would be decorative without the forward. Empty when run from a workstation, which is exactly when the fallback is wanted. Same one-line addition in `vmod/run.sh`. |
| `mock_epoch_cfg` replaced by two `mock_derived_config` calls | consolidation | Byte-identical config file content, same `chmod 0644`, same root-name pinning. |
| `epoch_defines` → `mock_epoch_defines` | consolidation | Same two `--define` arguments, same array, passed to the same invocations. |
| `dnf install createrepo_c` + `find`/`createrepo_c`/`chown` replaced by `mock_publish_localrepo` | consolidation | Same `find … ! -name '*.src.rpm' -exec cp -p`, plus `-maxdepth 1` on a directory that is flat either way; same `createrepo_c`; same `chown`; same fatal-if-empty check with a shared message. |
| **new:** payload allowlist + `pc_verify_build` after the cachetag rebuild | check-strengthening | Settlements (b) and (c). Reads `/out/packages/…rpm` and `mockresult/cachetag/build.log`, extracts to `/tmp`. Placed here rather than in a fresh container because the EL9 cachetag row has no later stage that mounts `lib/`, and adding one would be a workflow change. |
| every `mock_as` invocation | **unchanged** | Verified by command trace, below. |

### `scripts/ci/el9/mock-build.sh`

| Hunk | Class | Justification |
| --- | --- | --- |
| `-v "$here:/ci:ro"` → `-v "$repo/scripts/ci:/ci:ro"`, and the script path gains `el9/` | consolidation | The mount widens by one directory level so `lib/` is reachable. A read-only mount of a checkout cannot reach a package. Mechanical and confined to two lines. |

### `scripts/ci/debian13/assert-packages.sh`

| Hunk | Class | Justification |
| --- | --- | --- |
| header rewritten | comment | The DESIGN.md "one deliberate duplication" note is obsolete for this file and names the two that remain. |
| `. …/lib/package-checks.sh`, `log_dir=` | consolidation | — |
| `hardening_check` / `inspect_hardening` deleted | consolidation | Replaced by `pc_verify_build`. |
| vinyld: `pc_verify_build … nolog "…"` | **no change in strength** | Same four assertions plus `fortify-source`, all still fatal. The `nolog` reason is printed. |
| cachetag payload: `pc_assert_deb_payload` replaces the two-line check | check-strengthening | Settlement (b). |
| cachetag hardening: `pc_verify_build … log …pbuilder-libvmod-cachetag.log` | check-strengthening | Settlement (c). relro/bind-now/pic unchanged and still fatal; two flags now asserted; two symbol checks demoted. |
| closing message mentions payload | comment | — |

### `recipes/debian-13/container/stage-lint.sh`

| Hunk | Class | Justification |
| --- | --- | --- |
| header records settlement (a) with its measurement | comment | — |
| `--fail-on error,warning` on the gating pass | check-strengthening | Settlement (a). Measured to fire on nothing. |
| the "0 = no error-level tag" message | comment | Now says error-level or warning-level. |

### Untouched, and why

- `recipes/debian-13/vinyl/debian/**`, `recipes/el9/vinyl-cache.spec.in`, `recipes/el9/find-provides`, `recipes/el9/systemd/**`, `pins.env`, `cohort.env` — nothing a package is built *from* was opened.
- `recipes/debian-13/container/stage-vinyl.sh` and `stage-cachetag.sh` still hold their own copy of the ELF hardening block. They belong to the local whole-cohort lane, which CI does not run. Folding them in needs a `dpkg-buildpackage` tee **and** a compile-line selector that covers the engine's non-libtool translation units; doing it without the second would demote their canary checks with nothing put in their place. Named carry-forward.
- `recipes/el9/container/build.sh`'s `stage_report` hardening block stays informational, by its own design (`set +e`, annocheck advisory). Its `stage_lint` is the stricter of the two rpmlint gates and is unchanged.
- `.github/workflows/**` — **no workflow file changed.** Every consolidation was arranged to fit the existing step sequence, which is why the EL9 checks live in `container-mock.sh` rather than in a new step.

## Wave 1 surface: what this prepares

Wave 1 adds `libvmod-redis` as a third VMOD on the generated-recipe lane. Two seams were designed for it here.

**The fixture contract.** `vtc-suite.sh` names no VMOD, no file extension and no vinyltest macro: `vtc_stage_fixtures` takes a whitespace-separated pattern list relative to the top of the release archive, and `vtc_run_suite` takes the macro name and the fixture directory. dict's two values (`tests/*.dict`, `dictdir`) are stated once, in `run.sh`, in a `case` **with no default** — so a third VMOD arriving before the overlay work fails loudly instead of silently running its suite against dict's fixtures. Wave 1 declares them in the overlay, renders them through `vmod_recipe.py lane-env` beside the package names, and deletes that block; no logic in the shared file moves, and there is no per-VMOD branch to add.

**The verify scripts.** `verify-deb.sh` and `verify-rpm.sh` are now lane wiring plus stage markers: the payload, hardening and behaviour stages are three shared calls. A third VMOD adds no line to either.

**And the reason to do this before the third lane rather than after.** The three B-defects this wave retires were each *"a lesson one backend's script had and the other did not"* — with two backends. A third lane multiplies the pairs to compare, and the sweep that closed B6 and B9 was already a manual side-by-side read of two files.

## Verification

### Host-safe battery

| Check | Result |
| --- | --- |
| `release_tool.py selftest` | **146/146**, exit 0 |
| `ci_matrix.py selftest` | **228/228**, exit 0, then **146/146** for the generator |
| `vmod_recipe.py selftest` | **146/146**, exit 0 |
| `release_tool.py validate` | OK, 10 manifests, cachetag 1.0.1 |
| `release_tool.py validate --require-releasable` | **OK, exit 0** — releasable stays green |
| `release_tool.py --no-cachetag-cross-check validate` | OK |
| `ci_matrix.py check-catalog` | OK, 2 VMODs |
| `ci_matrix.py ledger --tier ci` | **15 rows, 14 selected** — unchanged |
| containerized `actionlint`, all 5 workflows | clean, exit 0 |
| containerized `shellcheck --severity=error`, every `.sh` under `scripts/` and `recipes/` | clean, exit 0 |
| containerized `shellcheck --exclude=SC1007,SC1091`, every file this wave touched or added | clean apart from two pre-existing `SC2140` in `container-mock.sh`; the two cross-file array warnings carry `disable` directives with reasons |

Exit statuses were read, not filtered. B10's lesson: `selftest | grep '^# TOTAL'` turns a traceback into silence.

### Container fixture battery for the shared checks

The logic is shell that runs only inside a buildroot, so it is exercised as shell against **real captured artifacts** rather than round-tripped through a Python self-test — the same reasoning the Wave B report records for `check-build-flags.sh`. Inputs are the packages and logs of the green baseline run 30437775658, plus synthesized negatives. Run in both distro userlands so nothing depends on one `grep`, one `readelf` or one package tool.

`debian:13` — **19 cases, 19 pass**:

| Group | Cases |
| --- | --- |
| build-flag assertion, four real logs | cachetag Debian, cachetag EL9, dict Debian, dict EL9 — all pass |
| build-flag negatives | `-fstack-protector-strong` stripped; `-D_FORTIFY_SOURCE=2` stripped; no `libtool: compile:` lines; no log at all — all correctly fail |
| payload, real packages | cachetag `.deb`, dict `.deb` — pass |
| payload, synthesized | the allowed shape passes; a stray `/etc` file rejected; **another binary's lintian override rejected** (B3 stated as a test) |
| hardening | cachetag `.so` log form: pass. cachetag `.so` nolog form: pass. dict `.so` log form: pass. **dict `.so` nolog form: FAILS** — B5 as a test. Missing log in `log` form: fails. `nolog` with no reason: fails |

`almalinux:9` — **14 cases, 14 pass**: the same four logs and four negatives, both real RPMs through the allowlist, an `rpmbuild`-synthesized allowed shape and a stray-`/etc` negative, and the same three hardening cases including dict's `nolog` failure.

### The lintian settlement, measured before it was made

In a `debian:13` container with lintian 2.122.0, against the baseline's own `.changes` files and all the files they reference: **eight `.changes` across both engine channels, zero error-level tags, zero warning-level tags, `--fail-on error,warning` exits 0 on every one.** No override was needed and none was added.

### Command-trace equivalence: the byte-neutrality proof for the drivers

The strongest available local evidence that the consolidated drivers do not move a package byte is that they issue **the same commands with the same arguments**. Both container scripts were executed end to end under a stub `PATH` that records every `argv`, from a `main` worktree and from this branch, and the traces diffed.

`container-pbuilder.sh`, all three scopes:

| Scope | Recorded commands | Result |
| --- | --- | --- |
| `engine` | 5 | **identical** |
| `vmod` | 6 | **identical** |
| `all` | 8 | **identical** |

The only lines the branch adds anywhere are the two `tee [/out/logs/pbuilder-<package>.log]` invocations. Every `pbuilder build` line — basetgz, buildresult, override-config, distribution, components, mirror, architecture, hookdir, no-auto-cross, bindmounts, othermirror, `.dsc` — matches character for character, and so does every `dpkg-buildpackage -S` including its working directory.

`build-deb.sh`, the generated lane's consumer of the same library: `pbuilder build`, `dpkg-buildpackage` and `dpkg-scanpackages` **identical**; the one added line is `pbuilder --version`, the cachetag lane's version probe, now shared.

`container-mock.sh`, both scopes:

| Scope | `mock` invocations | Result |
| --- | --- | --- |
| `engine` | 4 | **identical** |
| `vmod` | 10 | **identical** |

The complete set of differences on the EL9 side is: the container-side `dnf` lines (`-q`, and `rpm-build`/`createrepo_c`/`cpio`/`binutils` installed up front), an `rpm -q` version print, and the new `rpm -qpl` / `rpm2cpio` / `cpio` reads of the settlement block. No `mock` argument moved.

**The EL9 trace has a stub boundary, and it matters where it is.** After `mock --install`, `container-mock.sh` reads four values back out of the mock-installed development package — `vmoddir`, `pkgincludedir`, the two VRT components and the ABI string — through `mock --chroot -- pkg-config` and `mock --chroot -- sed`, and then refuses to continue unless the ABI equals `VINYL_STRICT_ABI`. A recorder that stubs `mock` itself cannot answer those, so **it stops at the Vinyl ABI cross-check**, and everything past that point — cachetag's `--buildsrpm` and `--rebuild` — is then verified by source-level comparison rather than by the trace. The audit's independent reproduction, with a stricter recorder that also captured cwd, environment and tty, hit exactly that wall and adjudicated those two invocations that way.

The harness used here stubs `runuser` instead, one level *above* `mock`, and synthesises the four readbacks (returning the real `VINYL_STRICT_ABI` from `cohort.env` so the cross-check passes as it does in CI). That is why the table above reads ten `mock` invocations in `vmod` scope rather than eight: the last two are cachetag's `--buildsrpm` and `--rebuild`, recorded and identical. Both methods agree; they are recorded separately because the synthesised readbacks are an input this harness supplies and CI does not, and a reader should be able to tell which invocations were observed and which were reasoned about.

One incidental result worth recording: on this macOS Docker host, `main`'s `container-mock.sh` **cannot run at all** — it dies at `/out is owned by root; mock cannot run as root` — while the branch's proceeds. That is the local-debuggability change, demonstrated rather than argued.

### Generated-recipe determinism against the baseline

`generate.sh` was run on the host for both targets against the digest-verified dict release archive from the baseline run, and the rendered trees compared with the ones that run uploaded:

- `debian-13-amd64`: `recipe/debian/**` **identical**;
- `el9-x86_64`: the whole `recipe/` tree including `generation-record.json` **identical**.

So no recipe byte moved either, on the lane whose recipes are outputs.

Staging was checked in the same run: `lane/scripts/` contains `verify-deb.sh`, `verify-rpm.sh`, `package-checks.sh`, `vtc-suite.sh` and `check-build-flags.sh`, and `lane/tests/` the two ported VTCs.

### What did NOT run locally, and why

- **No package was rebuilt.** This host is arm64 and the lane images are digest-pinned, which is deliberate — `run.sh` documents why it will not pass `--platform` against a digest. A build under emulation would have proven that the scripts execute, and the trace harnesses prove that already: both container scripts ran end to end under `set -euo pipefail` with real `pins.env` / `cohort.env`, real substitutions, the real token check and the real digest check, and exited 0 (the one non-zero is the branch's new hardening check correctly rejecting a zero-byte stub `.so`).
- **No install or behaviour stage ran.** They need x86_64 packages on an x86_64 package manager.
- **Digest reproduction against CI was not attempted.** The Wave B report already measured that whole-RPM digests differ by `BUILDHOST` alone, and a local Debian build cannot reproduce a runner's `.buildinfo`. The equivalence proof is the CI gate, not a local build.

## The CI gate this is waiting on

Push, dispatch a baseline `inject=none` run, and require:

1. **All 14 selected rows green.** The ledger is unchanged at 15/14, so the shape is the same.
2. **cachetag Debian: byte-identical** to 30437775658, excluding `.buildinfo` and `.changes`, on both engine channels — 11 digest entries each, 10 `.deb` files in total.
3. **cachetag EL9: semantically equivalent** to 30437775658 under the Step 3 normalized comparison, on both channels — 9 RPMs each, empty diff in every section. Whole-RPM digests are expected to differ (`BUILDHOST`).
4. **The new checks are visible and passing** in the row logs: `BUILD-FLAG ASSERTION: PASS` with 7 compile lines on each cachetag row, `OK: payload contains only …` on both, and `lintian exit status: 0` with `--fail-on error,warning` in the Debian lint stage.

If (2) or (3) moves, the cause is in this change set and the per-hunk table above is the list of suspects — the tee'd log and the container-side `dnf` additions first, because they are the only hunks that write anything at all.

## Audit rulings, 2026-07-29

The audit reproduced the trace method with a stricter recorder (cwd, environment and tty added to the recorded `argv`) and it held; the `-D_FORTIFY_SOURCE=2` pairing and the `nolog` carve-out were both endorsed; the payload and lintian settlements reproduced exactly. Five items came back, and the four rulings among them are recorded here rather than left as open questions.

**D5 — the uid-1000 fallback is fatal in CI.** Implemented. `mock_setup_build_user` returns non-zero with two `E:` lines when the mount is root-owned and `CI=true`; the macOS warning path is unchanged otherwise. Both host drivers now forward `CI` into the container, because docker gives the container a fresh environment and the guard would otherwise have been decorative — a check that cannot fire is worse than no check, since it reads like coverage. This closes what was open question 5.

**The rpmlint asymmetry is Wave 1, and it is one change, not two.** Wave 1 builds `rpmlint_overrides` — the twin of the `lintian_overrides` list B7b and B7c already built — **and** fixes dict's `summary-not-capitalized` in the same change. That takes dict to package revision 2 **once**, with one attributable cause and one evidence re-record, rather than moving its bytes twice for two separately-reasoned tidy-ups. The four spelling findings and `invalid-license GPL-3.0-or-later` are the class the override mechanism exists for; the capitalisation is a real defect and gets fixed rather than waived. This closes what was open question 1.

**Open questions 2 and 3 are one carry-forward, not two.** The engine's compile-line selector and the two local-lane copies of the ELF block are the same piece of work: the copies cannot be folded into `package-checks.sh` without deciding what `pc_assert_build_flags` selects for `vinyld`'s non-libtool translation units, because folding them in without that would demote their canary checks with nothing put in their place. The audit's **preferred eventual direction is to retire the local whole-cohort lane's hardening stage in favour of `assert-packages.sh`** rather than to consolidate a stage CI never runs — but that is a scope decision about the local lane's purpose and is deliberately left for later, not settled here.

**The two miscounts.** `debug=+vclrel` is in five files and the EL9 epoch macros in three, both corrected above with the `grep -rl` evidence. The counts matter because open question 4's decision is sized by them.

## Open questions for the audit

1. **`debug=+vclrel` in five files.** It is a constant with a paragraph of reasoning, not logic, and the four cachetag copies live under three different mount contracts — two VTC suites and two smoke stages. Consolidating means the cachetag suites adopt the declared-fixture contract Wave 1 builds, and it is four call sites to rewrite rather than two. Wave 1, or later?
2. **The two EL9 epoch macros in the local `rpmbuild` lane** (`recipes/el9/container/build.sh:39`, `recipes/el9/mismatch/container.sh:199`). They apply the macros to `rpmbuild` rather than to `mock`, and their containers mount only `/recipes`. Widening that mount the way `mock-build.sh`'s was would fold them in. Worth it for a lane CI does not run, or does it go the same way as the ELF copies?
3. **The joint carry-forward** recorded above: the engine compile-line selector plus the local-lane ELF copies, with retiring the local lane's hardening stage as the preferred direction. Needs a decision about what the local whole-cohort lane is *for* before it can be sized.
