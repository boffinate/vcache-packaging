# dict diverges from cachetag and redis on EL9's --allowerasing scenarios

Date: 2026-07-30

Run: <https://github.com/boffinate/vcache-packaging/actions/runs/30532825959> (the deliberate release-transactions dispatch for cohort `vinyl-9.0.1-ac4f719c16f4`)

Decision owner: repository maintainer. Decision taken: dict's EL9 `upgrade_transactions` verdict is recorded `pending`, not `pass`, until this divergence is adjudicated.

## What was measured

Three EL9 scenarios — `upgrade-allowerasing`, `upgrade-allowerasing-runtime-only`, `distro-sync-allowerasing` — end in `UPGRADED VINYL AND REMOVED THE VMOD` with dnf exit 0 for `vmod-dict`, while the identical scenarios against `libvmod-cachetag` and `libvmod-redis` end in `REFUSED the transaction, nothing changed` with dnf exit 1. The divergence reproduced identically across all three scenarios in the run, so it is not a flake. The other 16 dict scenarios are outcome-identical to cachetag's row, and dict's Debian matrix is outcome-identical in full.

For cachetag and redis, dnf reports `cannot install the best update candidate ... requires vinyld(cohort-...), but none of the providers can be installed` and refuses. For dict, the same command produces a clean erasure plan (`Removing dependent packages: vmod-dict`) and executes it.

## Why it matters

Step 9 recorded, as a plan-hypothesis correction, that bare `--allowerasing` is **not** the danger on EL9 — only naming a package alongside it is (docs/20260724_2348_report_step-9-transaction-safety.md). That finding drives the operator guidance. For dict, the finding is false: a bare `dnf upgrade --allowerasing` silently removes the VMOD and exits 0. Recording `pass` would bake that behaviour difference into the evidence of record while the documentation still asserts the opposite.

## Why the row still passed in CI

The EL9 scenario harness classifies removal-with-warning-required as an accepted outcome class — the same class Debian's `apt full-upgrade -y` lands in for every VMOD, cachetag included. The gate asserts that outcomes are classified and warnings documented, not that nothing is ever removed. That policy is defensible, but it means this divergence produced no red anywhere; whether the EL9 expectations should pin per-scenario outcomes per VMOD (so a divergence *between VMODs* on the same scenario is loud) is part of the adjudication below.

## What is known about the cause

The generated RPM `Requires:` stanzas are identical across all three VMODs: `vinyld(abi)%{?_isa}`, `vinyld(vrt)%{?_isa}`, `vinyld(cohort-...)%{?_isa}`. The scenario containers see the same repositories. The visible variables are the package names (`vmod-dict` vs `libvmod-*`) and the package sets' payload sizes; the resolver difference is therefore in dnf/libsolv's solution search, not in anything the recipes declare differently. No hypothesis has been tested yet.

## Adjudication items

1. Reproduce the divergence in isolation (two-package repo, one scenario container per VMOD) and identify what flips libsolv's decision — candidate variables: package name ordering, the dbgsym/debuginfo subpackage set, payload weight in the erasure cost function.
2. Decide whether the EL9 expectation model should pin outcomes per scenario (divergence between VMODs on one scenario becomes red) or keep the class-based gate.
3. Then record dict's EL9 verdict: `pass` if the behaviour is adjudicated acceptable and the operator guidance is corrected to name dict's exception, `fail` if it is to be fixed in packaging or resolver configuration.

Until item 3, dict blocks `validate --require-releasable` for this cohort, which is the intended effect of `pending`: the cohort's release cannot claim uniform transaction safety while one VMOD's EL9 behaviour is unexplained.

## Evidence

- dict EL9: artifact `packages-dict-release-vinyl-release-el9-x86_64`, `txn/mismatch/logs/summary.tsv`, raw scenario logs beside it.
- redis EL9 (the refusing twin): artifact `packages-redis-release-vinyl-release-el9-x86_64`, same paths.
- cachetag EL9 baseline: artifact `packages-cachetag-release-vinyl-release-el9-x86_64`, `mismatch/logs/summary.tsv`.
