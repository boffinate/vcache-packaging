# Publication authority: manual gate (maintainer decision, 2026-07-30)

Date: 2026-07-30

Decision owner: repository maintainer, decided in conversation on 2026-07-30. This note records the decision that the [release-automation plan](20260730_1414_plan_release-automation.md) §4 deliberately left open, and the amendments it forces: that plan's §2 and Phase 0 are re-scoped, the [Step 9 managed-repository design](20260730_1415_plan_step-9-managed-repository-publication.md) §6 authority mapping is resolved, and `SCOPE.md`'s cadence section now records the authority model the [1334 maintainer-vision note](20260730_1334_note_maintainer-vision-and-automated-release-decision.md) said it would record once decided.

## The decision

**Publication is a manual gate. Automation never publishes anything.** Its job is: detect upstream movement → verify pin integrity → notify loudly → (as a future increment) prepare the re-pin for one-click human review. Both halves of publication stay deliberate human actions: flipping a GitHub draft to a published release today, and promotion into any managed APT/RPM repository once Step 9 exists.

Against the plan's §4 option space this is closest to **option (d), taken one step further**: option (d) still had automation publishing the GitHub release; the decision keeps even that flip human. Everything upstream of publication — detection, pin-integrity verification, notification, and (per Phase 2, when built) the evidence pipeline to an unpublished draft — may run unattended.

## The boundary: releases, not trunk measurement

The manual gate applies to **releases only**. Continuous trunk compatibility checking — the scheduled, change-gated `trunk-early-warning.yml` run that builds Vinyl trunk HEAD and runs the declared VMOD harness lanes — is and must remain fully automatic, with no maintainer involvement; a red run notifies. The division of labour, stated once:

- **Automation owns**: detection, notification, and unattended trunk compatibility measurement.
- **Humans own**: re-pins and all publication.

Fleet watch rows (below) do not join the trunk build: unpackaged VMODs have no harness lanes to run, and fleet rows never gate. Fleet VMODs join trunk compatibility builds only as they become packaged, per the staged end state recorded below — not as part of this change.

## Rationale, in the maintainer's substance

Ondřej Surý's automated packaging pipelines work because his upstreams are professional projects publishing signed releases that automation can verify before acting. This project's upstreams are nothing like that: dozens of individuals and companies across scattered repositories — GitHub, GitLab, git.gnu.org.ua, code.uplex.de, sourcehut — with no signed artifacts to verify by default. Major upstream engine releases happen roughly twice a year, with infrequent patch releases. At that cadence a manual gate is sufficient, and anything more is overengineering.

## Consciously accepted trade-off

A release landing while the maintainer is away may wait until return, or be driven remotely. That is accepted on two conditions, both of which are mechanism obligations on the detection side:

1. **Detection must notify loudly.** A new re-pin candidate or a moved pin must reach the maintainer, not sit in a green run's logs. (Implemented alongside this note: the trunk-early-warning workflow now opens a GitHub issue per distinct upstream+tag from the watcher's findings, and a moved pin already fails the run red, which emails.)
2. **The deliberate dispatch path stays cheap enough to run from the GitHub UI anywhere.** The built pipeline already satisfies this: re-pin, CI proves it, one release-transactions dispatch and one release-draft dispatch produce the evidenced package set, and the draft→release flip is a button.

## What this resolves

### Release-automation plan §4 — decided

As above. The plan's four options remain in the plan as the record of what was considered; its §4 heading now carries a dated pointer here.

### Release-automation plan §2 — re-scoped

- **The soak delay is moot.** The soak existed to put a window between detection and unattended action; under a manual gate the human is the soak, on every release.
- **Signature verification: "unsigned" is the norm.** The upstreams generally do not sign. The compensating control is what already exists: the archive digest is pinned at detection, and a moved tag is a loud failure — now hardened by the poisoned-tag rule, under which a tag name ever observed peeling to a different commit than first recorded keeps moved-tag treatment permanently, even if it later returns to the original value. Recorded signing posture per current upstream, from repository records rather than a fresh survey: `vmod-dict` publishes PGP-signed tags and a signed release tarball (recorded in `registry/vmods/dict.yml` and the Wave A1 note); cachetag, redis, and Vinyl Cache publish unsigned (annotated or lightweight) tags. Verification is enabled per-upstream only if an upstream ever signs stably; until then the posture is recorded as unsigned rather than left accidental.

### Release-automation plan Phase 0 — downgraded

The single machine-readable engine-pin home (§2.3) drops from prerequisite to friction reduction. A human performing the re-pin tolerates the four coordinated edit sites (the two lane pin files, the cohort manifest, and the watcher constants — each cross-checked so a lone edit fails loudly); an automated re-pin was what could not. Still worth doing to make the deliberate path cheaper; no longer a gate on anything.

### Step 9 — unblocked

The Step 9 design was authority-agnostic and waiting on this decision. Under the manual gate, promotion into repository channels is always a human action, so **channel-scoped provider tokens drop from hard evaluation criterion to nice-to-have** (still wanted, as least privilege). Phase A no longer needs to evaluate providers "as if option (c)"; automation holds no provider token at all.

### Recorded as a desirable next increment, not implemented

An auto-prepared re-pin branch/pull request — automation opening the recorded re-pin (tag, peeled commit, archive digest) for one-click human review — is the natural next step of the detect→verify→notify chain. It publishes nothing and is consistent with this decision; it is deliberately **not** built now.

> **Amendment, 2026-07-30.** The maintainer authorized this increment later the same day and it is now implemented, for patch-free pinned VMOD rows only. It still publishes nothing. Design, the machine-readable eligibility rule (and why the engine row, redis and cachetag are excluded), the dispatch-not-`pull_request` CI decision, the interlock semantics, and what remains deferred: [auto-prepared re-pin branch and pull request](20260730_1812_note_auto-prepared-repin-pr.md).

## Scope of detection widened alongside this decision: the active VMOD fleet

Decided in the same conversation: detection extends beyond the three packaged VMODs to the **active VMOD fleet** from the compatibility survey (`survey/`) — upstreams whose repositories were updated within the past two years per the survey's triage data (`head_date` in `survey/data/triage.json`). Anything older is assumed too old or incompatible and is deliberately not watched. The roster is materialized as a reviewable, maintainer-editable file (`registry/fleet-watch.json`), not derived at runtime.

Fleet rows have deliberately weaker semantics than the packaged rows, and this is the point of recording them here: **detection covers the active fleet; publication stays selective.**

- **Watch-only, non-gating.** A fleet row never sets the early-warning gate's `run=true`, never creates CI rows or harness expectations, and never triggers the Vinyl trunk build. Tags only; no fleet trunk branches enter the gate path.
- **No pin, so no moved-pin rule.** The state records the observed stable-tag set per upstream; a new stable tag since the last recorded state surfaces as an informational packaging candidate. The per-upstream stable-version grammar applies, so pre-release-shaped tags do not surface.
- **Digest notification, not per-tag issues.** Fleet candidates flow into a single rolling digest issue, clearly separated from the loud per-tag issues reserved for the packaged, pinned rows.
- **First observation seeds silently.** The first run against an unseeded upstream records its tag set without announcing decades of history as "new".

A fleet candidate becoming a packaged VMOD still requires the explicit maintainer selection decision `SCOPE.md` demands; the watcher only shortens the time between an upstream releasing and the maintainer knowing.

### The fleet end state, staged (maintainer policy, recorded here; only stage 1 is implemented now)

The maintainer's end state for the fleet: **building a package = it's watched = its movement triggers rebuild and test against Vinyl trunk, unattended.** The ~36 active fleet VMODs are all intended to become watched packages over time, joining one at a time under `SCOPE.md`'s selection rule. The staged path, on the record and cross-linked to the [release-automation plan](20260730_1414_plan_release-automation.md):

1. **Now** — fleet detection rows: watch-only, non-gating, exactly as described above. This is the only stage implemented with this note.
2. **Enabler** — cache the built Vinyl trunk HEAD prefix keyed by trunk commit, so a VMOD-only event does not cost a fresh ~45-minute engine build. The watcher state already reserves `trunk_engine_run_id` for exactly this; it stays unfilled until the caching lands.
3. **Per-VMOD lanes arrive with packaging.** Each VMOD's harness lane and manifest are created when that VMOD is packaged — its VTC test declaration is the one per-VMOD input the generic harness needs — not as a 36-lane big bang.
4. **Release events eventually test against the pinned Vinyl release as well as trunk.** A new VMOD release tag answers "could I package this today?"; a trunk commit answers "will it survive the next Vinyl?". Both measurements are wanted; only the trunk half exists today.

Throughout all stages the boundary above holds: trunk compatibility measurement is fully automatic; re-pins and publication are human.

## Deferred items from the plan's Phase 1, and why

- **"One real new upstream tag observed classified correctly"** (Phase 1 exit criterion) cannot be manufactured — no synthetic tag is pushed to any upstream. It rides the next genuine upstream release.
- **The signing survey** is satisfied from repository records (above) rather than a fresh sweep; under the manual gate its outcome could not change any mechanism anyway.

## Cross-references

- [Release-automation plan](20260730_1414_plan_release-automation.md) — §4 now marked decided; amendment block records the §2/Phase-0 re-scoping.
- [Step 9 managed-repository design](20260730_1415_plan_step-9-managed-repository-publication.md) — amendment resolves the §6 authority mapping.
- [Maintainer-vision note](20260730_1334_note_maintainer-vision-and-automated-release-decision.md) — design question 3 (publication authority) is the one answered here.
- `SCOPE.md` — cadence and measurement policy amended in the same change.
