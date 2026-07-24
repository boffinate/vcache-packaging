# Step 9: upgrade-transaction safety verified on both lanes

Date: 2026-07-24

Related: [Debian 13 transactions note](20260724_2300_note_step-9-debian-13-transactions.md), [EL9 transactions note](20260724_2342_note_step-9-el9-transactions.md), plan section "Upgrade transaction safety" in libvmod-cachetag `docs/20260724_1526_plan_binary-packaging-and-distribution.md`.

## Outcome summary

Both lanes now have retained, reproducible synthetic mismatch fixtures (higher version + wrong ABI, and higher version + same ABI) and a fully executed transaction matrix — 16 apt scenarios, 17 dnf scenarios, one fresh container each, full resolver output retained under `dist/<lane>/mismatch/logs/`.

**Safe upgrade paths** (never remove an imported VMOD): `apt upgrade` / `apt-get upgrade` (hold Vinyl back); on EL9, plain `dnf upgrade` refuses the whole transaction under the default `best=True` (only `--nobest` produces the skip the plan hypothesised) — note the operational consequence that unrelated updates in the same run also fail until the cohort is coherent.

**Commands requiring prominent warnings** (verified to delete `libvmod-cachetag`, after which `vinyld -C` fails with "Could not find VMOD cachetag"):

- Debian: `apt full-upgrade` (prompt defaults to Yes), `apt-get dist-upgrade`, direct `apt install vinyl-cache=<version>`.
- EL9: `dnf install --allowerasing vinyl-cache-<version>`, `dnf upgrade --allowerasing <package>` (targeted). `--allowerasing` on a bare `dnf upgrade`/`distro-sync` did NOT remove cachetag, contradicting the plan's hypothesis in the safe direction.

**Incident-response procedures verified**: `apt-mark hold` refuses even a direct install (stronger than an apt `-1` pin, which still allows `apt install` to remove cachetag); `dnf versionlock` blocks the erasing install and must be released all-or-none (a partial release makes the resolver propose removing cachetag and devel); `dnf history undo last` restores the cohort while the previous repository generation remains reachable — a direct argument for the plan's retained-cohort requirement.

**Same-ABI-string limitation: confirmed material on both package managers.** A different-payload Vinyl advertising the identical ABI hash upgrades cleanly through every path with nothing objecting. Cohort-aware atomic promotion is therefore load-bearing, not an optimisation; `vinyld`'s own strict-ABI load check is the real backstop for a deliberately bad combination. Both lane notes recommend deciding on a cohort-qualified provide (e.g. `vinyld(cohort)` / a cohort-qualified virtual package) before publication rather than after — it trades against distro-native Vinyl support and belongs with the Phase 4 decisions.

## Plan-hypothesis corrections worth carrying forward

1. dnf on EL9 does not "normally skip" an unsatisfiable update: `best=True` makes it hard-fail the transaction. Documentation and unattended-upgrade guidance must reflect that a stale cohort blocks the whole `dnf upgrade` run.
2. `--allowerasing` alone is not the danger on EL9; naming a package alongside it is.
3. apt's removal prompt defaults to Yes; release notes should teach the actionable rule "if any `libvmod-*` appears under REMOVING, answer no" rather than a command blocklist.

## Remaining before step 10 (pre-release)

CI-deferred: amd64/x86_64 builds, sbuild/Mock clean-rooms, SELinux enforcing, signed repositories, `unattended-upgrades` behaviour, live-daemon VCL reload after removal. Maintainer decisions: EL9 libunwind/EPEL, real maintainer/security-contact identity, security-owner staffing, and now the cohort-qualified-provide decision. Step 10's internal draft and public experimental pre-release wait on those plus maintainer go-ahead.
