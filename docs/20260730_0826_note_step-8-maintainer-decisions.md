# Maintainer decisions, 2026-07-30: what the scheduled workflow is for

Date: 2026-07-30

Status: **Decision record.** Decision owner: the repository maintainer. Taken on 2026-07-30, after Step 8 Wave 2's live proofs and **before** Wave 3 is implemented, because it changes what Wave 3 builds.

Related: [Step 8 Wave 1](archive/20260730_0748_note_step-8-wave-1-transactions-wiring.md), [Step 8 Wave 2 live proof](20260730_0824_report_step-8-wave-2-live-proof.md), [failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md) Phase 4, [roadmap step 8](20260728_0916_roadmap_outstanding-packaging-work.md), [SCOPE.md](../SCOPE.md).

Wave 1 wired the upgrade-transaction matrix into the package rows as a tier-gated stage and left the migration of `nightly-transactions.yml` to Wave 3. These decisions reshape that migration. The stage itself is unaffected: what changes is **who dispatches it and how often**, not what it does.

## The decisions

### (a) The scheduled workflow is trunk early warning only, and it is change-gated

It runs **every few days rather than nightly**, and it **skips entirely when Vinyl trunk HEAD has not moved**. There is nothing to learn from rebuilding an unchanged engine against unchanged sources, and a schedule that reports green for work it did not do is worse than no schedule.

The gate is per-VMOD as well as per-engine: **when a VMOD's own upstream has changed, that VMOD runs against trunk even if trunk itself has not moved**, reusing the previous engine artifacts rather than rebuilding the engine. The two change signals are independent and either one selects work.

### (b) The trunk cadence runs build, verify and behaviour only

**No transaction matrix runs on any schedule.** The transaction matrix answers a question about a *published* upgrade path, and a trunk snapshot is not one. Running it on a cadence would spend the most expensive stage in the project on candidates nobody will ever be offered.

### (c) Release-channel rows are never rebuilt on a schedule

**Once release artifacts are built they are untouched until there is a new release.** Rebuilding them would produce packages that are not the packages the recorded evidence describes, which is the one thing the whole registry is arranged to prevent.

Release-channel transaction measurements are therefore **deliberate one-off dispatches**, not scheduled work. That is where the dict and redis `upgrade_transactions` verdicts will flip — Wave 3 — and it is why the flip is a dispatch plus a recording commit rather than something a nightly run does on its own.

### (d) Bleeding-edge trunk packages: considered and dropped

Publishing installable packages built from Vinyl trunk was considered and **rejected**. It requires a trunk snapshot versioning scheme that upgrades sanely, is distinguishable from a release, and does not poison a user's cohort — and the cost of getting that right was judged higher than the value. Trunk stays what it is: an early-warning lane that publishes nothing.

### (e) Accepted for consideration: per-cohort transactions instead of per-VMOD

A proposal to change what a transaction scenario tests. Today the matrix installs **one** VMOD alongside the engine and asks what the resolver does to it; per VMOD, per target, that is roughly a hundred containers or more for a release event, and it grows linearly with the fleet.

The proposal is to run **one scenario set per cohort with every selected VMOD installed at once**, asserting both coexistence and survival: after the transaction, did *all* of them survive, and can a VCL importing *all* of them still compile. That is roughly **33 containers per release event** instead of 100-plus, and it is arguably the stronger claim — a real machine has the whole cohort installed, not one VMOD.

**Accepted for consideration, not adopted.** It changes what the evidence means, so it needs its own design pass: per-VMOD `upgrade_transactions` verdicts would become a per-cohort fact recorded against each VMOD, and the failure attribution ("which VMOD did the resolver sacrifice") has to survive the consolidation.

### (f) Upstream freshness comes from live checks, not the survey JSON

The change signal for a VMOD must come from a **live check of that VMOD's own repository** — `git ls-remote`-style, the same mechanism the source stage already uses to confirm a recorded tag still peels to a recorded commit:

- **a new tag or release** surfaces a re-pin candidate **to the maintainer**. It is never auto-repinned: a moved pin is a deliberate act with evidence resets attached to it.
- **movement on a trunk branch** is a change-gate signal that selects that VMOD's trunk row.

The stale survey JSON is explicitly **not** the source of this. It is a point-in-time sweep, it goes out of date the moment it is written, and a freshness check reading it would report the state of the world on the day of the sweep.

### (g) The fleet ambition is roughly forty VMODs

Which is the reason (a) and (f) matter. At three VMODs a scheduled full sweep is affordable and change gating is a nicety. At forty it is the difference between a workflow that runs and one that is switched off; per-VMOD change gating is what keeps the cadence proportional to what actually moved.

### (h) Varnish-trunk early warning is anticipated, and gated on SCOPE.md

Adding a Varnish-trunk early-warning lane is anticipated but **requires a `SCOPE.md` amendment before any engine work begins**. `SCOPE.md` currently selects one engine and says so explicitly, including that no VMOD selection implies a Varnish lane. This decision does not amend it; it records that the maintainer expects to.

## The consequence for Wave 3

The isolated-graph migration of the scheduled workflow is no longer "invoke the same reusable workflow with `tier: nightly`". It needs an **engine-channel and change filter threaded through the tooling** — `expand`, `engine-matrix`, `ledger` and `reconcile` — so that:

- a run can select a subset of rows from a change signal rather than a whole tier, and
- the ledger it reconciles against is the ledger of **that** selection, so a skipped-by-design row reads as deliberately not selected rather than as missing execution evidence.

That last point is the substantive one. The reconciler's contract is that every expected row produces an outcome or is synthesized as a failure; a change gate that removes work without also removing it from the expected ledger would turn every gated run red. `not_selected` already exists in the vocabulary for exactly this shape, and the filter has to reach the ledger and not only the matrix.

None of it touches the transaction stage added in Wave 1. That stage is dispatched by tier and is inert at every other tier, which is the property these decisions rely on: (b) and (c) are enforced by simply never dispatching `nightly` from a schedule.

## What is not decided here

- Whether per-cohort transactions replace per-VMOD ones — (e) is accepted for consideration only.
- Which VMODs join the fleet, and in what order. Each addition remains an explicit `SCOPE.md` decision with its own evidence obligation.
- The exact cadence in days, and the shape of the change-signal store. Both are Wave 3 design.
