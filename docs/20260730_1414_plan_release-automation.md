# Release-automation plan: unattended packaging of new upstream releases

Date: 2026-07-30

Status: **Plan for maintainer review. Nothing in this document is implemented, and nothing may be implemented until the maintainer has reviewed it and made the decisions it marks as open — in particular the publication-authority decision in §4, which this plan deliberately does not make.**

> **Amendment, 2026-07-30.** The §4 decision has been made: **manual gate** — automation never publishes anything; detection, pin-integrity verification, and loud notification are its whole job, and every publication step (GitHub release flip, and any future Step 9 repository promotion) stays a deliberate human action. The decision, its rationale, and everything it resolves are recorded in the [publication-authority decision note](20260730_1635_note_publication-authority-decision.md). Consequences inside this plan, without rewriting it: §2's soak delay is moot (the human gate is the soak) and signature verification is recorded as "unsigned is the norm", with the digest-pin and moved-tag machinery as the standing compensating control; Phase 0's machine-readable engine-pin home downgrades from prerequisite to friction reduction, since a human re-pin tolerates the four coordinated edit sites; Phase 3 narrows to "there is no automated publication to implement". Phase 1's detection hardening was implemented with that decision (watcher shape-gap fix, per-upstream stable-tag grammar, poisoned-tag rule, and issue-based notification), which is what makes a manual gate workable. The options below are retained as the record of what was considered.

This plan implements the maintainer decision recorded in [the maintainer-vision note](20260730_1334_note_maintainer-vision-and-automated-release-decision.md): a **new** upstream release tag — from the Vinyl Cache authors or a VMOD author — triggers automated packaging once detection is reliable, so that a security release reaches users while the maintainer is away for two weeks. It answers that note's five design questions in order. The companion [Step 9 managed-repository design](20260730_1415_plan_step-9-managed-repository-publication.md) is written authority-agnostic so that the single §4 decision resolves both documents consistently.

## Invariants this plan does not touch

These are restated because every phase below is designed inside them; a phase that would need to relax one is misdesigned.

- **The moved-tag rule is unchanged and load-bearing.** A pinned tag that no longer peels to its recorded commit is a loud failure and is never re-pinned automatically. Only *new* tags trigger automation; that distinction is what makes automated response defensible.
- **The full evidence pipeline is non-negotiable.** An automated release travels the same path as a deliberate one: re-pin with recorded commit and archive digest, full CI evidence, release-transactions measurement, completeness-gated draft assembly. There is no abbreviated "security fast path" that skips evidence.
- **Anything red stops that release and notifies. Nothing retries into a published artifact.** Automated retry may re-run a *failed, unpublished* step only if the maintainer later decides so; this plan proposes no retry at all (§6).
- **Transactions never run on a schedule.** The automated release's transactions run is event-triggered by the release in flight, which is a deliberate dispatch by machine proxy against one cohort — not a schedule.
- **Release artifacts are never rebuilt outside deliberate dispatches.** The automation initiates dispatches in response to an upstream release event; it never rebuilds an existing release's artifacts.
- **The trunk re-pin must not cross Vinyl commit `6d36364cc1`** until cachetag's header fix lands in the sibling `libvmod-cachetag` repository (surfaced here only; the fix belongs there). This constrains the trunk track and is independent of release automation, but any automation touching pins must know it exists.
- **No package renames.** The `vmod-*` / `libvmod-*` standardisation is a separate open maintainer decision; when made, it must update the EL9 per-scenario transaction pins in the same change. Automation must never "fix" a name.
- **The `step8-fixture` branch is never merged.**
- **`SCOPE.md` is not amended by this plan.** Per the 1334 note, `SCOPE.md` is amended when the plan's answers change authority or delivery promises; publication authority is deliberately left undecided here, so the amendment waits for that decision.

## 1. Detection reliability

The watcher (`tools/upstream_watch.py`, as of `fd0a561`) carries the rows automation would consume: `vinyl-trunk` HEAD, the Vinyl release tag (`vinyl-cache-9.0.1`, moved-tag checked), per-VMOD pinned release tags with `newer_tags` surfacing, and watched trunk branches for cachetag, dict, and redis. Detection is `git ls-remote` only — no HTTP, no clone — which is the right footprint but also the whole of what it can see: a tag name and the object it peels to.

### 1.1 False-positive policy (must precede any automated action)

Today `newer_tags` is a human-facing surfacing signal; a human filters out nonsense by eye. Automation has no eye, so the filter must be explicit and written down before anything hangs off the signal:

- **Pre-releases and betas.** A candidate tag must parse as a *stable* version in the pinned tag's own scheme. Any tag carrying a pre-release marker (`-rc`, `-beta`, `-alpha`, `~`, `.dev`, or a suffix segment the pinned scheme has never used) is surfaced to the maintainer as today and never triggers automation. The stable-version grammar is per-upstream and lives with the pin (see §2.3): redis's `9.0-23.1` scheme is not Vinyl's `vinyl-cache-9.0.1` scheme, and a shared regex would be wrong for at least one of them.
- **Re-tagged betas / re-used names.** A tag name the watcher has *ever* observed peeling to a different commit is poisoned: even if it now looks like a stable release, it is a moved tag by history and gets the moved-tag treatment (loud failure surface, never automated). This requires the watcher state to retain observed tag→commit pairs rather than only the current pin (small state-schema addition).
- **Tags on unexpected branches.** `ls-remote` cannot answer ancestry. Two options: (a) accept the gap at detection time and rely on the pipeline — the tag's peeled commit is what gets built and evidenced, so a tag on a rogue branch still gets full evidence and, under every authority option except 4(a), a human or a candidate channel before stable; or (b) add one ancestry check in the automation's first CI job (which has a checkout anyway): the candidate tag's commit must be reachable from the upstream's release branch, else stop-and-notify. **Recommendation: (b)** — it is one `git merge-base --is-ancestor` in a job that already has the clone, and it converts a supply-chain-relevant anomaly into a loud stop instead of a well-evidenced build of the wrong thing.
- **Known heuristic gap, must be fixed:** `newer_tags` only compares same-shape tags, so a hypothetical `vinyl-cache-9.1` (two components) would never surface against `vinyl-cache-9.0.1` (three). The comparison must generalise to the upstream's version grammar rather than the pinned tag's exact component count. This is a watcher change with selftest coverage and is a Phase 1 exit criterion.

### 1.2 Observed is not tested

The watcher's advance-state job rewrites only the `vinyl-trunk` key from *tested* commits; the branch rows added in `fd0a561` advance from the gate's *observation*. Automation consuming watcher rows must treat every row as **observed, not tested** — an observation is a trigger to go and produce evidence, never a substitute for it. This is a stated contract in the automation code, with a selftest asserting no automated action reads an observation as a verdict.

### 1.3 Detection-adjacent facts to carry as validation items (not blockers)

- The first self-deciding scheduled `trunk-early-warning` run has not happened: the schedule (`17 3 * * 1,4`) landed 2026-07-30 08:33 UTC, after the Thursday slot; the first is expected Monday 2026-08-03 ~03:17 UTC. The gate's skip path is proven by dispatch (run 30528844197: gate success, downstream skipped); the run=true path is unexercised on schedule. Observing the first scheduled decision is a Phase 0 validation item.
- Watched-branch movement gates the early-warning run globally: dict/redis trunk movement sets run=true although neither has a harness lane, so a run triggered only by them builds Vinyl trunk HEAD (~45 min) to run zero dict/redis rows. **Known inefficiency, recorded, not scheduled work**: per-VMOD gating is a possible refinement and the maintainer decides whether it is worth it.
- dict/redis trunk harness lanes are **blocked upstream, not on packaging**: Vinyl trunk's `AC_INIT` emits literal `trunk`, so `vinylapi.pc` carries `Version: trunk`, breaking dict's `acvmod.m4` arithmetic and redis's `VINYL_PREREQ` numeric compare (recorded in `registry/vmods/dict.yml:53-58` and `registry/vmods/redis.yml:67-76`). When Vinyl trunk emits a numeric snapshot version these become one-line manifest edits; no packaging work is scheduled for them here.

**Recommendation for §1 overall:** detection is "reliable" — the decision's own precondition — when the shape-gap fix has landed with selftests, the stable-version grammar per upstream is recorded machine-readably, the poisoned-tag rule is implemented, and one full watcher cycle has been observed classifying a real new tag correctly (the next genuine upstream release serves as this observation; no synthetic tag is pushed to any upstream).

## 2. Pin provenance, soak, and signatures

### 2.1 What the automated re-pin records

At detection time the automation records, exactly as a deliberate re-pin does: the tag name, the tag's peeled commit, and the archive digest (fetched or deterministically derived per the manifest's existing source policy — redis's derived-archive path included). These land in the manifest/pin change that opens the automated release, so the evidence pipeline pins against detection-time reality; if the tag moves between detection and build, the existing moved-tag machinery fails loudly, which is precisely the desired race outcome.

### 2.2 Supply-chain posture: two open choices, presented with trade-offs

Automating the response to a new tag means a compromised upstream tag flows further without a human eye than it does today. Two mitigations are available; each is a posture choice for the maintainer, and they compose.

**Soak delay.** The automation waits N hours (say 24–48) between detection and acting, so that an upstream force-push retraction, a community alarm, or the maintainer (if reachable) can interrupt.
- *For:* a compromised tag that upstream itself notices and retracts within the window never enters the pipeline; the poisoned-tag rule (§1.1) then blocks the name forever.
- *Against:* it delays exactly the security releases the decision exists for, by a fixed cost on every release to mitigate a rare event; and during a genuine two-weeks-away window nobody is watching the soak anyway, so its protective value there reduces to "upstream retracts in time".
- **Recommendation: a short soak (24h) as the default, overridable to zero by an explicit maintainer dispatch input for a release the maintainer is watching in person.** The cost is one day on an unattended path that already includes multi-hour evidence runs; the benefit is real for the most likely compromise discovery route (upstream noticing).

**Upstream signature verification.** Verify the tag's GPG/SSH signature against a recorded upstream key before acting.
- *For:* the strongest available statement that the tag came from the upstream author rather than from an account or forge compromise.
- *Against:* it only works if the upstream actually signs tags and manages keys stably — this must be *surveyed per selected upstream* before the option is real; an unsigned-tags upstream makes the check vacuous, and a key rotation becomes a new loud-failure mode requiring maintainer intervention (which, unattended, means the security release stops — arguably correct, but it must be a chosen behaviour, not a surprise).
- **Recommendation: survey signing practice for the four current upstreams as Phase 1 work; enable verification per-upstream where signing is real and stable, recording the key fingerprint beside the pin. Where an upstream does not sign, record "unsigned" explicitly so the posture is visible rather than accidental.**

### 2.3 Prerequisite: one machine-readable home for the engine pin

The Vinyl engine release pin currently lives in **four places that must move together**: the release blocks of `recipes/debian-13/pins.env` and `recipes/el9/cohort.env`, `registry/cohorts/vinyl-9.0.1-ac4f719c16f4.yml`, and the watcher constants `VINYL_RELEASE_KEY`/`VINYL_RELEASE_TAG`/`VINYL_RELEASE_COMMIT` in `tools/upstream_watch.py` plus its selftest transcript. There is no machine-readable single source: `pins.env` is shell with a `case` dispatch, and the tag name appears only in a comment. A human re-pin holds the four in their head; an automated re-pin cannot.

**Prerequisite work item (Phase 0):** give the engine release pin one machine-readable home — as the manifests already provide for VMODs — from which the pin files and watcher constants are generated or against which they are validated (`release_tool.py validate` cross-check, same pattern as the existing cachetag `configure.ac` cross-check). The design choice (generate vs validate) is small and left to implementation review; the requirement is that a single edited value moves the pin everywhere or the validation gate refuses. No automated engine re-pin is built before this exists.

## 3. VMOD ride-along policy (for an automated Vinyl release response)

When automation responds to a **new Vinyl release**, which VMOD versions ride along? Evidence is per exact combination under the cohort model (see the 1334 note's cohort-vs-matrix discussion): every VMOD in the new cohort needs its full evidence set against the new engine regardless of whether the VMOD itself changed, because the engine input change resets all families.

**Option A — current pins only.** The new cohort is {new Vinyl, VMOD versions exactly as currently pinned}.
- *Evidence cost:* the mandatory minimum — three (someday ~forty) families rebuilt and re-evidenced against the new engine. Nothing else varies.
- *Property:* single-variable change. If the cohort goes red, the engine bump is the only suspect. This matters most precisely when unattended: nobody is present to disentangle a two-variable failure.

**Option B — current pins plus newer detected VMOD releases.** The new cohort also lifts any VMOD whose watcher row shows a newer stable tag.
- *Evidence cost:* identical *per combination* (the same families are rebuilt either way), but each lifted VMOD adds its own re-pin provenance work (§2.1 per VMOD; for redis, patch re-derivation and re-review — a *human* obligation recorded in `SCOPE.md` that automation cannot discharge) and multiplies the failure-attribution space.
- *Property:* closer to the vision's "latest version of each VMOD", but a red cohort may be the engine, a VMOD bump, or an interaction, with no human present.

**Recommendation: Option A.** An automated Vinyl response ships current pins only. A newer VMOD release is its own detection event with its own automated response (a VMOD-only release against the current engine pin), so "latest of each" is still reached — as two single-variable releases instead of one two-variable release. The redis reviewed-patch obligation makes Option B additionally impossible to fully automate today: a VMOD bump that needs patch re-derivation must stop and notify in any case (§5), and under Option A that stop blocks only the VMOD's own release, never a Vinyl security release.

## 4. Publication authority — DECIDED 2026-07-30: manual gate

**Decided by the maintainer on 2026-07-30: manual gate — automation never publishes; see the [publication-authority decision note](20260730_1635_note_publication-authority-decision.md).** The outcome is closest to option (d) taken one step further: even the GitHub release flip stays human. The options below are kept as the record of what was considered and why.

Today the draft→published flip is a human action, and roadmap §9 ([outstanding-work roadmap](20260728_0916_roadmap_outstanding-packaging-work.md) §9) means "release happens" will ultimately mean **repository publication users can install from**, not just a GitHub draft flip. This is the single largest authority change in the automation and this plan presents options only. Each option states: what a compromised or buggy automation can reach, what the two-weeks-away security scenario delivers, what credentials the automation must hold, and how the option interacts with §9's staging/promotion model. The [Step 9 design](20260730_1415_plan_step-9-managed-repository-publication.md) §"Authority mapping" mirrors this section so one decision resolves both.

A cross-cutting modifier applies to every option and is itself part of the decision: authority can be scoped by blast radius — e.g. full automation for single-VMOD releases (one family changes) while engine releases (every family resets) keep a human step, or vice versa. It is noted once here rather than multiplying the option list.

### Option (a) — Full authority end to end

Automation carries the release from detection through draft to published GitHub release, and (once §9 exists) through candidate to the stable repository channel.

- *Compromised/buggy reach:* the maximum — a bad artifact reaches the stable installable channel and users' machines with no human anywhere in the path. The evidence pipeline gates on everything it measures, but it cannot measure "this upstream tag is malicious yet functional"; §2's posture choices are the only mitigation upstream of publication.
- *Two-weeks-away:* the security release fully lands for all users, GitHub and repository alike. This is the only option that completely delivers the motivating scenario.
- *Credentials held:* a GitHub token able to create **and publish** releases on the public repo, plus (post-§9) a provider token with write and promote rights on **stable**. These live in CI secrets; their theft equals publication authority.
- *§9 interaction:* automation drives promotion to stable directly; the candidate channel still exists but is a pipeline stage, not a human gate.

### Option (b) — Timed auto-publish with a notification/veto window

Automation assembles and holds; after a fixed window (say 72h) with notification to the maintainer, it publishes unless vetoed.

- *Compromised/buggy reach:* identical to (a), delayed by the window. The veto only functions if the maintainer is reachable.
- *Two-weeks-away:* the window elapses unwatched, so away-mode behaviour is exactly (a) with added latency — it slows the security release without adding away-mode protection. Its honest value is for the *near-but-busy* maintainer, not the absent one.
- *Credentials held:* same as (a); the window is policy, not a credential reduction.
- *§9 interaction:* the same timed flip applies to stable promotion; the candidate channel holds the release during the window, which does give observant users early access.

### Option (c) — Split authority: auto-publish to candidate, human promotes to stable

Automation carries the release through the full evidence pipeline and publishes to a **candidate** repository channel (and a GitHub pre-release); promotion of the cohort to **stable** is a human action.

- *Compromised/buggy reach:* the candidate channel only. Stable — what ordinary users track — is unreachable without the human promote. This is the smallest reach of any option that still publishes something installable unattended.
- *Two-weeks-away:* users tracking candidate (or instructed by the release notification to temporarily add it — an urgent-fix instruction the project can document once) get the security fix immediately; stable users get it on the maintainer's return. Partial delivery of the scenario, by design.
- *Credentials held:* a GitHub token for pre-release creation, plus a provider token scoped to the candidate channel only — *if the provider supports channel-scoped tokens*, which becomes a hard provider-evaluation criterion in Step 9. If the provider cannot scope tokens, this option's containment claim weakens to policy-only and that must be known before choosing it.
- *§9 interaction:* the most natural fit — this **is** §9's staging/promotion model with the authority boundary drawn on the promotion step, and cohort promotion as one tested unit is exactly the human's single action.

### Option (d) — Publish GitHub release only; repository publication stays human

Automation ends at a published GitHub release (direct-download artifacts); all repository channels, candidate included, wait for a human.

- *Compromised/buggy reach:* a published GitHub release — direct downloaders and anything scripted against GitHub releases; no APT/RPM repository is touched.
- *Two-weeks-away:* direct-download users get the fix; repository users get nothing until return. As §9 matures and repositories become the normal install path, this option **decays**: the population it serves shrinks to whoever still downloads by hand, so it under-delivers the motivating scenario progressively more over time.
- *Credentials held:* the smallest set — one GitHub token with release-publish on the repo; no provider credentials at all.
- *§9 interaction:* none, and that is its weakness: it defines "release happens" as the thing §9 exists to supersede.

**What this plan does with §4:** Phases 0–2 are identical under every option (everything up to a completeness-gated *draft*). Phase 3 is blocked on this decision and its `SCOPE.md` amendment. If the maintainer wants a provisional stance to unblock Step 9 provider evaluation, option (c) is the one whose provider requirements are a superset of the others' (channel-scoped tokens), so evaluating providers *as if* (c) keeps every option open.

## 5. Unattended failure behaviour

Every red stops **that release** and notifies; nothing retries into a published artifact. Concretely:

- **Notification channels:** (1) a GitHub issue on `boffinate/vcache-packaging`, created by the automation, titled with the upstream, tag, and failing stage, linking the run(s) and the release branch — the durable, resumable record; (2) GitHub's own workflow-failure notification email to the maintainer as the push channel. Recommendation: start with these two (both zero-infrastructure); add a second push channel (e.g. a mail step with its own credential) only if the maintainer finds GitHub's emails insufficient — another credential is another thing a compromise can use.
- **State left behind on a red:** the automated re-pin lives on a branch `auto-release/<upstream>-<tag>` — never pushed to `main` by automation while red. Whatever the pipeline produced stays where the failure left it: CI evidence artifacts on the runs, a draft release (if assembly was reached) left as an unpublished draft, the issue pointing at all of it. Nothing is deleted, nothing is retried, and no second automated attempt for the same tag starts while the issue is open (the open issue is the interlock).
- **Resume on return:** the maintainer reads the issue, inspects the branch and runs, and either fixes and re-dispatches *deliberately* (the branch re-enters the normal deliberate path; automation is done with that tag) or closes the release (close the issue, delete the branch and draft). Automation never resumes a stopped release.
- **A green unattended release also leaves a record:** the same issue (or a discussion entry), auto-closed, stating what was released and under which authority option — so the returning maintainer has one place listing everything that happened while away.
- **Interaction with the transaction-pin gate:** the pin-mismatch gate (commit `70e01dd`) has never run live, and its `deb_txn`/`el9_txn` steps carry `continue-on-error` — a pin mismatch surfaces through row classification and the reconciled ledger, not step failure. The automation's "anything red" detector must therefore read the **reconciled ledger verdicts**, not raw job conclusions. Validation item (Phase 0): confirm on the next *deliberate* release-transactions dispatch that a pin mismatch classifies as red in the ledger the automation would read. No dispatch is made for this plan; the confirmation rides the next deliberate one.

## Phases and exit criteria

Reviewable increments; each phase gets maintainer sign-off before the next begins. Phases 0–2 are authority-neutral; Phase 3 is blocked on the §4 decision.

**Phase 0 — Prerequisites and live validations (no automation behaviour yet).**
Work: single machine-readable engine-pin home (§2.3) with validate cross-checks; watcher tag→commit history state (§1.1); observe the first self-deciding scheduled trunk-early-warning run (expected 2026-08-03); confirm the transaction-pin gate's ledger-classification path on the next deliberate release-transactions dispatch (§5).
Exit: `release_tool.py validate` refuses a divergent engine pin anywhere in the four current locations; watcher selftests cover the history state; both live validations observed and recorded in a note.

**Phase 1 — Detection hardening.**
Work: `newer_tags` shape-gap fix; per-upstream stable-version grammar recorded beside each pin; poisoned-tag rule; upstream signing survey (§2.2).
Exit: watcher selftests cover shape generalisation, pre-release exclusion, and poisoned tags; a note records each upstream's signing posture; one real new upstream tag has been observed classified correctly (§1 recommendation).

**Phase 2 — Automated pipeline to draft (publishes nothing).**
Work: on a qualifying detection, automation opens `auto-release/<upstream>-<tag>` with the recorded re-pin (soak per §2.2 posture), dispatches the evidence chain (CI → release-transactions → completeness-gated draft assembly), ride-along per §3 recommendation, and implements §5's issue/interlock/notification behaviour ending at an **unpublished draft**.
Exit: one rehearsal release driven end to end on a real or test tag (test releases are acceptable if deletable), reaching a completeness-gated draft with zero human steps; one injected-failure rehearsal proving stop-notify-interlock; the returning-maintainer resume path walked once for real.

**Phase 3 — Publication authority (blocked on the §4 decision and its `SCOPE.md` amendment).**
Work: implement the chosen option; if (c), coordinate with Step 9's channel implementation.
Exit: defined when the option is chosen; at minimum, credentials scoped to exactly what the option requires, and one live release published under the chosen authority with its record (§5) complete.

## Explicitly out of this plan

Per-VMOD gating of the early-warning run (noted §1.3, maintainer's call); dict/redis trunk harness lanes (upstream-blocked, §1.3); the `vmod-*`/`libvmod-*` naming decision; any `SCOPE.md` amendment (waits for §4); any workflow dispatch, artifact rebuild, or implementation work in the session that authored this plan.
