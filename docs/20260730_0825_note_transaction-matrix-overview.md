# The upgrade-transaction matrix, in one page

Date: 2026-07-30

Status: Overview. Nothing here is new; it is the one place to look first, written because the detail is spread across a report, two lane notes and two scripts and there was nowhere to start.

## What it is for

**When a package manager is offered a newer Vinyl Cache that would break the VMODs already installed against the old one, we need per-command evidence of what it actually does: hold Vinyl back safely, refuse the transaction loudly, or quietly delete the VMOD and leave a daemon that can no longer compile its own VCL.**

That is the whole question. It is not a question about our packages compiling — the build and behaviour suites answer that — and it is not answerable by reading documentation, because the answer differs between `apt` and `apt-get`, between the prompt and `-y`, and between a whole-system upgrade and one that names a package.

## What it consists of

Two matrices, one per lane, each scenario in its own throwaway container so no outcome can contaminate the next:

- `recipes/debian-13/transactions.sh` — **16 apt scenarios**, `s01`–`s16`, unchanged since step 9.
- `recipes/el9/transactions.sh` — **19 dnf scenarios**. Seventeen at step 9; two same-ABI erasing routes were added on 2026-07-25 when the cohort-qualified provide turned a silent upgrade into a resolver conflict there was something to test.

Both install a real baseline cohort from a real local repository, publish a synthetic candidate into the same repository, run exactly one transaction, and record what survived — including whether `vinyld -C` can still compile a VCL that imports the VMOD.

Since 2026-07-30 the classification is also a *gate*: every scenario's outcome and package-manager exit code is pinned per VMOD in `recipes/<lane>/transactions/expected/<package>.tsv`, and the matrix fails on any difference — a changed outcome, an unpinned scenario, or a pinned scenario the run did not produce. The pins exist because a class-based gate let two VMODs diverge on the same scenario with no red anywhere (dict's silent EL9 erasure vs cachetag's refusal); a legitimate outcome change must now update the pin file in the same review.

## Why sixteen and not four

Roughly five questions, multiplied by the command forms administrators and unattended tooling actually run. Taking the Debian lane, where the numbering makes it easiest to see:

| Question | Scenarios |
| --- | --- |
| Does the ordinary upgrade hold an incompatible Vinyl back? | `s01` control (no candidate at all), `s02` `apt upgrade`, `s09` `apt-get upgrade` |
| Do the aggressive upgrades remove the VMOD? | `s03`/`s04` `apt full-upgrade` prompt and `-y`, `s10` `apt-get dist-upgrade`, `s11` the same with `vinyl-cache-dev` installed |
| Does installing the candidate directly remove it? | `s05`/`s06` `apt install vinyl-cache=<candidate>` prompt and `-y` |
| Do the countermeasures work — against both routes? | `s07`/`s15` `apt-mark hold` vs full-upgrade and vs direct install; `s08`/`s16` an apt pin vs the same two |
| Is a same-ABI-but-different-content candidate caught at all? | `s12`/`s13`/`s14` upgrade, full-upgrade and direct install against the `sameabi` fixture |

The multipliers are each there because something was measured and turned out to differ:

- **Two synthetic candidates.** `mismatch` advertises a different `vinyld-abi-<hash>` — the incompatible security upgrade. `sameabi` advertises the *identical* ABI token with different content and a different cohort id — the distro backport, the vendor respin, the rebuild with another patch series. They are never in the same repository at once.
- **Prompt and `-y` both.** apt's answer to "what would you do" and "do it" are not required to agree, and the removal prompt is `Continue? [Y/n]` — capital Y, the default. An operator who skims and presses Enter has deleted their VMOD.
- **`apt` and `apt-get` both.** Unattended tooling still calls `apt-get upgrade` and `apt-get dist-upgrade`, and they are not synonyms of the `apt` commands.
- **Each countermeasure against each route.** A defence that stops a full-upgrade and not a direct install is not a defence, and that asymmetry is exactly what was found.
- **With and without the development package.** `vinyl-cache-dev` is one more package the resolver can sacrifice to satisfy a transaction, so its presence can change the answer.

The EL9 matrix has the same shape with two extra axes, which is why it is longer: dnf's flag space (`--best`, which is the default, `--nobest`, `--skip-broken`, `--allowerasing`, whole-system versus a named package, `distro-sync`) and the **shape of the machine** — full cohort, runtime-plus-VMOD as a production cache server actually looks, and a no-VMOD control. It also carries the two recovery and freeze procedures, `dnf versionlock` and `dnf history undo`.

## What it proved

The findings are recorded in [the step-9 report](20260724_2348_report_step-9-transaction-safety.md); the short version:

- **`apt upgrade` and `apt-get upgrade` are safe** — they hold the incompatible Vinyl back.
- **Five Debian commands delete the VMOD**, two of which administrators type without thinking: `apt full-upgrade` and `apt install vinyl-cache=<version>`. Afterwards `vinyld -C` fails with "Could not find VMOD cachetag".
- **On EL9, plain `dnf upgrade` refuses the whole transaction** rather than skipping the one update, because `best=True` is the default. The operational consequence is that unrelated updates in the same run fail too, until the cohort is coherent again. The plan had hypothesised a silent skip; only `--nobest` produces one.
- **Whether a bare `--allowerasing` removes the VMOD on EL9 depends on package-name collation, not on the packaging** *(corrected 2026-07-30; the original finding read "`--allowerasing` alone is not the danger; naming a package alongside it is", which was only ever true of cachetag's name)*. On dnf 4 / libsolv 0.7.24, a bare `dnf upgrade --allowerasing` or `distro-sync --allowerasing` refuses (exit 1) for a VMOD whose package name sorts before `vinyl-cache` (`libvmod-cachetag`, `libvmod-redis`) and silently removes one that sorts after it (`vmod-dict`, exit 0) — libsolv decides update rules in name order, and the refusal is a tie-break accident, not a guarantee. The removal matches what the Debian lane's `apt full-upgrade` does to every VMOD. A targeted `dnf upgrade --allowerasing vinyl-cache` and `dnf install --allowerasing <candidate>` remove the VMOD in **both** name orders. See [the root-cause note](20260730_1231_note_step-8-dict-el9-allowerasing-root-cause.md); dnf 5 (EL10, Fedora) is untested.
- **`apt-mark hold` refuses even a direct install; an apt pin does not.** A `Pin-Priority: -1` still allows `apt install` to remove the VMOD, which makes hold the stronger procedure and the one to document.
- **`dnf versionlock` blocks the erasing install and must be released all-or-none.** Releasing the lock on `vinyl-cache` alone makes the resolver propose removing the VMOD again.
- **`dnf history undo last` restores the cohort**, provided the previous repository generation is still reachable — a direct argument for retaining previous cohorts rather than a nice-to-have.
- **The same-ABI candidate upgraded cleanly through every path, on both package managers, with nothing objecting.** The exact-ABI virtual provide is a guard on the *build*, not on the *repository*. This is the finding that made the cohort-qualified provides (`vinyld-cohort-<id>` on Debian, `vinyld(cohort-<id>)` on RPM) load-bearing rather than an optimisation, and the `sameabi` scenarios are now their regression test.

## Where to read more

| Document | What it holds |
| --- | --- |
| [Step 9 report](20260724_2348_report_step-9-transaction-safety.md) | the findings, the corrections to the plan's hypotheses, and the incident-response verdicts |
| [Debian lane note](20260724_2300_note_step-9-debian-13-transactions.md) | the apt matrix scenario by scenario, and why the fixture is a metadata repack rather than a second Vinyl build |
| [EL9 lane note](20260724_2342_note_step-9-el9-transactions.md) | the dnf matrix, the `best=True` behaviour, versionlock and history-undo |
| [Step 8 Wave 1 note](archive/20260730_0748_note_step-8-wave-1-transactions-wiring.md) | how the matrix became a tier-gated stage of a package row, and the env-var contract that generalised it past cachetag |
| [Root-cause note](20260730_1231_note_step-8-dict-el9-allowerasing-root-cause.md) | why dict's EL9 `--allowerasing` behaviour differs from cachetag's: libsolv name-order tie-breaking |
| `recipes/debian-13/transactions.sh`, `recipes/el9/transactions.sh` | the scenario tables themselves, which are the authority |
| `recipes/*/transactions/expected/<package>.tsv` | the pinned per-scenario outcomes each VMOD's matrix run is checked against |

## What is not covered

Recorded as gaps rather than implied by absence: signed repositories (both lanes use local unsigned repositories with `gpgcheck=0`, so whether signature checking changes any outcome is untested), `unattended-upgrades` behaviour, SELinux enforcing, and what a live daemon does when its VMOD is removed under it — every result here is a resolver outcome plus a `vinyld -C` compile, not a running-cache observation.
