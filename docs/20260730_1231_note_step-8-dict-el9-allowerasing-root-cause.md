# dict EL9 --allowerasing divergence: the root cause is libsolv name-order tie-breaking

Date: 2026-07-30

This note answers adjudication item 1 of [the divergence note](20260730_1139_note_step-8-dict-el9-allowerasing-divergence.md): reproduce the divergence in isolation and identify what flips libsolv's decision. It records cause only. **The verdict is still `pending`**: dict's EL9 `upgrade_transactions` entry has not been edited, and adjudication items 2 (per-scenario outcome pinning) and 3 (the verdict itself) remain with the maintainer.

## The cause

libsolv decides installed packages' update rules in solvable-ID order, which is alphabetical package-name order, and whether dnf refuses the transaction or silently erases the VMOD depends on which side of `vinyl-cache` the VMOD's package name sorts. `vmod-dict` sorts *after* `vinyl-cache`; `libvmod-cachetag` and `libvmod-redis` sort *before* it. That single fact flips `dnf --allowerasing upgrade` from refuse (exit 1) to silent erase (exit 0). It is not the debuginfo sets, not payload size, not any dependency difference — the `Requires:` stanzas were already known to be identical, and the bisect below eliminates everything else.

There is a second necessary condition: the VMOD's identical NEVRA must be visible in an enabled repository at upgrade time. With the VMOD installed but its RPM absent from the repo, **both** name orders erase cleanly. The repo copy is what creates the libsolv best rule that makes refusal possible at all.

## Method

Everything ran in x86_64 `almalinux:9` containers under OrbStack Rosetta emulation (`--platform linux/amd64`), on dnf 4.14.0 / libsolv 0.7.24-6.el9_8 — the same stack as the CI lane. Two layers of evidence:

**Real artifacts.** The dict, redis, and cachetag EL9 package artifacts from run 30532825959 were used unmodified, `%_isa`-qualified deps intact, with baseline repos rebuilt to the CI lane's `prep.sh` layout and a synthetic candidate engine (`vinyl-cache`/`-devel` `9.0.2~git…mismatchfixture`) replicating the mismatch fixture's provides. `dnf --assumeno --allowerasing upgrade` reproduced both CI outcomes exactly: dict plans `Removing dependent packages: vmod-dict` and the `-y` path executes it (exit 0, VMOD gone); redis gets `cannot install the best update candidate for package libvmod-redis-23.1-1.el9.x86_64 … requires vinyld(cohort-…)(x86-64), but none of the providers can be installed` (exit 1).

**Dummy-RPM bisect.** Minimal VMOD RPMs, byte-identical except `Name:`, carrying the same three `vinyld(...)%{?_isa}` Requires, no debuginfo, near-identical tiny size:

| Name | Sorts vs `vinyl-cache` | Outcome |
| --- | --- | --- |
| `libvmod-dummydict` | before | REFUSE |
| `aaa-dummydict` | before | REFUSE |
| `vinyl-cacaa-dummy` | immediately before | REFUSE |
| `vinyl-cache-aaaa` | immediately after | ERASE |
| `vmod-dummydict` | after | ERASE (verified `-y`: exit 0, removed) |
| `zzz-dummydict` | after | ERASE |

Both directions were demonstrated: renaming the eraser makes it refuse, renaming a refuser makes it erase. Adding or removing a `libvinylapi.so.3()(64bit)` requires changed nothing. The boundary is exactly the engine package's name.

Nothing was edited, committed, pushed, or dispatched by the investigation; it was container-only per the runbook. The harness (baseline-repo build, dummy specs, `--debugsolver` capture) re-runs any variant in seconds from a prebuilt local image, so the bisect is cheap to repeat or extend.

## The libsolv mechanism (0.7.24 `rules.c`)

dnf's `best=True` puts FORCEBEST on the update-all job; `--allowerasing` adds per-package allowuninstall jobs; the solver runs with `bestobeypolicy`. `solver_addbestrules` (rules.c:3888–3910) adds a conditional best rule `(-keep_literal | best_candidates)` for every package that is both best-tracked and allowuninstall — and the best candidate set is the repo copy of the VMOD. The solver's main loop then decides installed packages' update rules in solvable-ID order, and both the rpmdb `@System` repo and createrepo_c repos load name-sorted, so ID order equals alphabetical name order.

- **VMOD before `vinyl-cache`:** the solver tentatively keeps the VMOD first. Its best rule demands the repo copy be installable; the engine upgrade, forced later by `vinyl-cache`'s own best rules, makes `vinyld(cohort/abi)` unsatisfiable; the conflict involves the strong best rule, a problem is recorded, and dnf refuses. The `--debugsolver` `solver.result` for the refuse case contains both the recorded problem *and* the same erase transaction as a fallback — dnf refuses purely because the problem count is nonzero.
- **VMOD after `vinyl-cache`:** the engine upgrade is already decided by the time the VMOD is reached. The keep-VMOD update rule is weak under allowuninstall, so it is silently disabled; the VMOD is erased; its conditional best rule is satisfied *by the erasure*; no problem is ever recorded; exit 0.

## Eliminated variables

The divergence note's candidate variables are all dead. Debuginfo/debugsource sets are symmetric — all three VMODs ship both, all present in the baseline repos. Payload size (dict 62 KB installed vs redis 232 KB, cachetag 307 KB) is irrelevant per the dummy bisect. The Requires stanzas are identical except that redis and cachetag additionally carry `libvinylapi.so.3` ELF deps — proven irrelevant by the add/remove test. The only load-bearing difference is `Name:`.

## Implications

**This is not a spec bug.** Identical Requires produce both outcomes; the order-dependence is inherent to dnf4/libsolv 0.7.24. Which means cachetag's and redis's refusal is itself an accident of their names, not a guarantee — nothing in their packaging *asks* to be protected. And Step 9's finding that bare `--allowerasing` is not the danger on EL9 ([docs/20260724_2348](20260724_2348_report_step-9-transaction-safety.md)) was only ever true for package names sorting before the engine's; it was measured on cachetag and generalized past its evidence.

**It is packaging-fixable, and there are two honest options.**

1. Rename the RPM to `libvmod-dict` so it sorts before the engine. The Debian lane keeps `vmod-dict` — apt removes the VMOD for every package there, cachetag included, so deb naming is not load-bearing for this. A generator-level rule "EL9 binary names must sort before `vinyl-cache`" is enforceable, but it is silently load-bearing: nothing about a name says it is doing resolver work.
2. Ship `/etc/dnf/protected.d/<vmod>.conf`. That makes *any* removal error out, in both name orders — but it also blocks a deliberate `dnf remove` until the file is deleted, which is a semantics change to document, not a tie-break patch.

A `Suggests:` or any weak dependency would **not** help: the mechanism is rule decision order, not choice ranking, and no preference weight enters it.

**On adjudication item 2:** per-scenario outcome pinning would have caught this class immediately. The class-based gate scored both refuse and erase as acceptable outcomes, so two VMODs diverging on the same scenario produced no red anywhere. Resolver behaviour keyed on package name is invisible to any gate that does not compare VMODs against each other per scenario.

**Untested residual:** whether newer libsolv/dnf5 (EL10, Fedora) erases in both name orders. The conditional-best-rule code has moved since 0.7.24, and none of the above measurements say anything about it.
