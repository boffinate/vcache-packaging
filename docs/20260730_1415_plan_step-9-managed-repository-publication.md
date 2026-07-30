# Step 9 design: managed APT/RPM repository publication

Date: 2026-07-30

Status: **Design only. This session implements nothing: no provider account, no upload, no workflow change. Maintainer review precedes any implementation.**

This begins roadmap §9 ([outstanding-work roadmap](20260728_0916_roadmap_outstanding-packaging-work.md) §9): add staging, promotion, retention, rollback, and repository-level transaction tests using a managed APT and RPM service. Exit gate, verbatim: promotion operates on a complete, verified package set; rollback and retention policies are tested; clients can install and upgrade through the published repositories.

`SCOPE.md`'s "Managed package repository boundary" section is normative over this design: provider features plus small project-specific scripts; **no** repository server, signing service, mirror, generalized promotion engine, or provider-independent framework. Every mechanism below is either a provider feature used as-is or a script that exists only because this project's cohort model needs it.

**Authority-agnostic by construction.** Publication authority — who or what flips a release into an installable channel — is deliberately undecided; the option space is §4 of the companion [release-automation plan](20260730_1414_plan_release-automation.md). This design works under every option, and §6 below maps each option onto the channel model so one maintainer decision resolves both documents.

> **Amendment, 2026-07-30.** The authority decision landed as **manual gate** ([publication-authority decision note](20260730_1635_note_publication-authority-decision.md)): automation detects, verifies pin integrity, and notifies; it publishes nothing and holds no provider token. That resolves §6's authority mapping to its option (d) row taken one step further — every channel verb (upload to candidate, promotion to stable, yank, retention pruning) is human-driven, and Step 9's channels serve deliberate releases only. Consequently §1 criterion 3 (channel-scoped tokens) drops from hard requirement to nice-to-have (still wanted, as least privilege), and Phase A no longer needs to evaluate providers "as if option (c)". This unblocks Step 9: nothing below waits on an authority model any more.

## 1. Provider evaluation criteria

`SCOPE.md` names Packagecloud as an example; it is a candidate to evaluate, not a commitment. Criteria, roughly in order of how quickly a "no" disqualifies:

1. **APT (Debian 13/amd64) and RPM (EL9/x86_64) from one provider**, so channel layout, tokens, and scripts exist once. Two providers is a fallback, not a preference.
2. **Distinct repositories usable as channels** (candidate/stable, §2) with client-visible separation — a user must be able to enable candidate without receiving it implicitly via stable.
3. **Token scoping.** Can a write token be limited to one repository/channel? This is a *hard* requirement under authority option (c) (automation writes candidate only) and a wanted one under all options (least privilege). A provider that only issues account-wide write tokens weakens option (c) to policy-only containment and that must be known before the authority decision.
4. **Signing and key custody.** Repository metadata signing (APT `Release`/InRelease, RPM repodata) is the provider's job — the project builds no signing service. Record whether keys are provider-managed or project-supplied, and what key rotation means for installed clients. Per-package RPM signing support is a plus, not a requirement, and any signing beyond provider features is out of scope.
5. **API completeness for the three verbs the scripts need:** upload, promote/copy between repositories, and yank/remove — each scriptable and returning distinguishable errors. Promotion mechanics matter: copy-then-verify-then-delete vs an atomic provider-side promote changes the §3 script's failure modes.
6. **Retention controls:** can old package versions be kept deliberately (for the tested rollback depth, §4) and pruned deliberately, rather than on provider-imposed limits? What happens at quota — silent eviction disqualifies.
7. **Pricing shape vs the fleet ambition:** priced per package version stored and per repository, evaluated at today's ~14 packages/cohort/target and at the recorded ~40-VMOD ambition. A price cliff at fleet scale is a finding, not necessarily a disqualifier.
8. **Operational basics:** availability history, deletion/export path if the project leaves (no provider-independence *framework*, but knowing the exit cost is due diligence), and whether install instructions for users are clean one-liners per target.

**Method:** paper evaluation of Packagecloud plus at least one alternative (e.g. Cloudsmith) against these criteria first; then a throwaway trial of the leading candidate with a deletable test package on both formats, exercising upload, channel separation, promote, yank, and client install — the trial is disposable evidence for the evaluation note, not the start of production publication. Provider choice is a maintainer decision recorded in `SCOPE.md`'s managed-repository section when made.

## 2. Channel layout

Two channels per package format, named the way `SCOPE.md` already speaks: **candidate** and **stable**.

- **candidate** receives cohorts whose evidence set is complete (the same completeness gate as draft assembly — `complete=true`, no escape hatch on any publication path). It exists for pre-promotion install/upgrade testing (§4) and, under authority option (c), as the unattended landing zone.
- **stable** receives only whole cohorts promoted from candidate. Users are documented onto stable; candidate is documented as opt-in.
- No third channel. "Experimental" separation in `SCOPE.md`'s wording is satisfied by GitHub draft/pre-release artifacts, which already exist upstream of any repository; adding an experimental repo channel would publish what the drafts already hold and widen the surface for nothing.

**Cohort promotion is one tested unit.** The unit of publication and promotion is the cohort's complete package set for its targets — today: engine packages plus cachetag, dict, redis on `debian-13-amd64` and `el9-x86_64`, exactly the asset set the release manifest records. The promotion script takes a cohort id, verifies the candidate channel holds every package the cohort's `release-manifest.json` names (digest-checked against it), and promotes all or nothing. A partial promotion is not a smaller promotion; it is a refused one — the same shape as "partial evidence is a blocked release, not a partial one".

Mapping to provider mechanics (finalised per chosen provider): APT — one repo per channel, distribution `debian-13` (or trixie), component `main`; RPM — one repo per channel for `el9/x86_64`. Package-name collation caveats (the EL9 `--allowerasing` root cause) travel with the packages, not the channels; the channel layout adds no new naming decisions and, per standing constraint, no renames.

## 3. Promotion, and how a script stays "small"

The promotion script's whole job: (1) resolve cohort id → expected asset list + digests from the recorded release manifest; (2) verify candidate holds exactly those; (3) invoke the provider's promote/copy per package; (4) re-verify stable lists them; (5) write a promotion record (cohort id, channel, provider operation ids, date) into `docs/`/registry evidence. Rollback (§4) is the same script with source and destination judgment inverted plus yank. Anything more — queues, generalized channel graphs, multi-provider abstraction — is the scope warning `SCOPE.md` names.

## 4. Repository-level transaction tests, retention, and rollback

### 4.1 Extending the transactions tier

Repo-level tests extend the existing `transactions` tier and inherit its cadence rule unchanged: **deliberate dispatch only, never scheduled.** Today's transaction rows install from locally supplied artifacts; the repo-level rows point the container's package manager at the real hosted repositories instead — same scenarios, same per-scenario pinned outcomes, same reconciled-ledger classification (including the documented-removal style of pins, e.g. dict's adjudicated EL9 `--allowerasing` removal). New scenario axis: **channel**. Minimum set per target:

- fresh install of the cohort from **candidate**;
- fresh install from **stable** (post-promotion);
- upgrade from previous stable cohort to the new one via `apt upgrade` / `dnf upgrade` against **stable**;
- incompatibility refusal through the repo (the existing mismatch fixture published to candidate only, or version-pinned refusal — design detail per provider);
- removal.

These rows run against published repositories, so they are *post-publication* evidence by nature — they verify the channel, they do not gate the build. The completeness gate stays where it is (pre-publication); the repo rows verify that what the gate passed actually installs from where users will get it.

### 4.2 Retention policy, sized by what is tested

`SCOPE.md`: retain enough previous revisions for the upgrade and rollback behaviour the project explicitly tests — and nothing more is promised. Design: the tested rollback depth is **one cohort** — stable retains the current cohort and its predecessor; candidate retains only what is awaiting promotion plus the current stable (for upgrade-path testing). Older revisions are pruned deliberately by script, and the retention claim in user-facing docs says exactly this. Depth greater than one is a future maintainer decision priced when asked for.

### 4.3 Rollback test design

Rollback = the previous cohort becomes the installable set again. Two provider-dependent mechanics, tested whichever the provider supports best: re-promote the previous cohort's packages (they are still retained, §4.2) and yank the bad cohort's, or yank alone where the repo then re-exposes the previous versions. The test, per target, on a deliberate dispatch: install new stable cohort → execute rollback script → verify the package manager offers/downgrades to the previous cohort's exact versions (`apt policy` / `dnf --showduplicates` + a pinned downgrade transaction) → verify the yanked versions are no longer offered. Outcome pinned per scenario like every other transaction row.

### 4.4 Exit-gate evidence map

- *Promotion operates on a complete, verified package set* → §3's script refuses on any missing/mismatched asset (negative test: remove one package from candidate, promotion must refuse) plus one recorded successful whole-cohort promotion.
- *Rollback and retention policies are tested* → §4.3 rows green on both targets, plus a retention check that the predecessor cohort is still installable and the pre-predecessor is not.
- *Clients can install and upgrade through the published repositories* → §4.1 fresh-install and upgrade rows green on both targets against the real hosted channels, using the documented user install instructions verbatim.

## 5. Phases and exit criteria (design of the work, not a start on it)

**Phase A — Provider evaluation.** Paper evaluation + throwaway trial (§1). Exit: an evaluation note with the criteria table filled per candidate, a recommendation, and the trial evidence; maintainer picks the provider and records it in `SCOPE.md`.

**Phase B — Candidate channel live.** Upload scripts, candidate repos for both formats, first complete cohort uploaded; install-from-candidate transaction rows green. Exit: §4.1 candidate rows green on both targets; documented candidate install instructions verified verbatim.

**Phase C — Promotion and stable.** Promotion script (§3), stable channel, promotion of one cohort, install/upgrade-from-stable rows green, negative promotion test recorded. Exit: first two bullets of §4.4.

**Phase D — Retention and rollback.** Retention pruning script, rollback rehearsal (§4.3) on both targets. Exit: full §4.4; roadmap §9 exit gate satisfied and recorded in a closing note.

Phases B–D each end with a maintainer review; the authority decision (§6) determines who performs promotions from Phase C onward.

## 6. Authority mapping: one decision resolves both documents

The channel model above is deliberately the same shape under every publication-authority option from the [release-automation plan §4](20260730_1414_plan_release-automation.md); only *who drives which verb* changes:

- **Option (a) — full automation authority:** automation uploads to candidate *and* runs the §3 promotion to stable. Both channel tokens live in CI. The human gate disappears; candidate becomes a pipeline stage automation passes through (ideally with the §4.1 candidate rows as an automated gate between upload and promote).
- **Option (b) — timed auto-publish:** automation uploads to candidate immediately; the §3 promotion runs after the veto window. Token custody as (a). Candidate doubles as the veto-window holding area, which gives the window observable content.
- **Option (c) — split authority:** automation's token reaches **candidate only** (making §1 criterion 3, channel-scoped tokens, a hard provider requirement); the maintainer runs the §3 promotion to stable by hand. This is the mapping under which the §3 script *is* the human's single promotion action and the channel model and the authority boundary coincide exactly.
- **Option (d) — GitHub-only automation:** automation holds **no** provider token; the maintainer uploads to candidate and promotes. The repositories are entirely human-operated, and Step 9's channels serve deliberate releases only.

Because option (c) imposes the superset of provider requirements, Phase A evaluates providers *as if* (c) — that keeps every option open until the maintainer decides. Whatever the decision, it is recorded once (with its `SCOPE.md` amendment, per the [1334 note](20260730_1334_note_maintainer-vision-and-automated-release-decision.md)) and both this design and the automation plan proceed under it without structural change.
