# The maintainer's packaging vision, its alignment with what is built, and the automated-release decision

Date: 2026-07-30

Decision owner: repository maintainer. This note records the vision verbatim, measures it against the built system and `SCOPE.md`, and records one new maintainer decision that amends the cadence policy recorded earlier today in [the Step 8 maintainer decisions](20260730_0826_note_step-8-maintainer-decisions.md) and in `SCOPE.md`.

## The vision, verbatim

> **Vinyl Cache packaging**
> - At a defined interval (configurable by me, could be every night or once a week) we:
>     - Check for new Vinyl Cache releases
>     - Check for new Vinyl Cache trunk commits
>     - Check for new VMOD releases
>     - Check for new VMOD trunk commits
> - If there are new VMOD releases:
>     - We build and package these for the currently supported Vinyl Cache releases
> - If there are new Vinyl Cache releases:
>     - We build and package each Vinyl Cache release
>     - We build and package the latest version of each VMOD for each new Vinyl Cache release
> - If there are new Vinyl Cache trunk commits:
>     - We test it builds
>     - We test the latest version of every VMOD builds
>     - We (maybe?) confirm they import cleanly, confirming ABI is compatible?
> - If there are new VMOD trunk commits:
>     - We test it builds with each supported **compatible** Vinyl Cache release
>         - Compatibility determined by ...???
>     - We test it imports with each compatible Vinyl Cache release, confirming ABI compatibility
> - We rely on the maintainers tagging a release (somehow??). We do not 'cut releases' - all we do is package the software for easier installation

## Alignment, item by item

**The interval checks are built, with two coverage gaps.** `trunk-early-warning.yml` runs on a schedule (Monday and Thursday; the interval is a one-line workflow edit) and `tools/upstream_watch.py` does the checking: Vinyl trunk HEAD, each VMOD's pinned release tag (a moved tag is a loud failure; tags sorting above the pin are surfaced as re-pin candidates), and watched VMOD trunk branches. Measured against the vision's four quadrants on 2026-07-30: the watcher does **not** watch for new Vinyl Cache *release* tags (only `vinyl-trunk` appears in its output), and only cachetag has a watched trunk branch — dict and redis carry release-tag rows only. Both are small additions.

**"New release → we build and package" was, until today, deliberately not automated.** The Step 8 decisions (c) and (f) made detection automated and action deliberate: newer tags are surfaced to the maintainer and never acted on automatically, and release artifacts come only from deliberate dispatches. The built pipeline makes the deliberate path cheap — re-pin, CI proves it, one release-transactions dispatch and one release-draft dispatch produce the evidenced package set — but a human initiates each step. The decision below changes this.

**The vision's version-matrix shape differs from the cohort model.** "Currently supported Vinyl Cache releases" (plural, × latest VMODs) implies a version matrix. What is built is the cohort model: one Vinyl release pin per track with a specific pinned VMOD set, evidence per exact combination. Several simultaneously supported Vinyl releases means several live cohorts; the registry can express that, but nothing runs that way today, and `SCOPE.md` claims only what the current matrix builds.

**Vinyl trunk testing is built, and stronger than the vision asks.** The trunk lane builds Vinyl trunk HEAD once, builds each selected VMOD's source against it, and runs the VTCs each manifest declares — behaviour evidence, not just a clean import, so the vision's "maybe confirm ABI?" is answered with yes-and-beyond. Caveat: only cachetag has a trunk harness lane today; dict and redis do not (a recorded open item of the Step 8 closing report).

**VMOD trunk testing is partially built, and the vision's open compatibility question has an architectural answer.** A moved VMOD trunk branch triggers a run against Vinyl trunk, not against supported releases; VMOD-trunk × Vinyl-release lanes do not exist. And in this project compatibility is never determined a priori — it is measured. A combination is compatible exactly when it was built, installed, and tested with recorded evidence, and no claim generalises beyond that (`SCOPE.md`, "What a package claim means"). That is the answer that belongs where the vision writes "Compatibility determined by ...???".

**"We rely on maintainers tagging; we do not cut releases" is fully aligned.** Release sources are identified by upstream tag; the tag must still peel to the recorded commit; the archive digest is pinned; the no-archive case is covered by deterministic derivation from the tag (redis). One refinement: the project does not cut *upstream* releases, but it does cut its own *packaging* releases — cohort drafts assembled by dispatch and published as a pre-release, today by a human.

## Decision: new upstream releases trigger automated packaging (2026-07-30)

The maintainer decided: **if new releases can be reliably detected, the packaging response is to be automated.** The motivating case is a security release — from the Vinyl Cache authors or a VMOD author — landing while the maintainer is away for two weeks; the release must happen anyway.

What this amends and what it keeps:

- It supersedes the "surfaced, never acted on automatically" half of Step 8 decision (f) for **new** release tags only. The **moved-tag** rule is unchanged and load-bearing: a tag that no longer peels to its recorded commit is a loud failure and is never re-pinned automatically — that distinction is precisely what makes automated response to *new* tags defensible.
- It does not create a schedule for building. The trigger is an upstream release event surfaced by the watcher; periodic rebuilds of unchanged inputs remain out of cadence policy.
- It does not weaken the evidence model. An automated release travels the same path as a deliberate one — re-pin, full CI evidence, release-transactions measurement, completeness-gated draft assembly — and anything red stops the line and surfaces to the maintainer; a partial or under-evidenced release is a blocked one, exactly as `SCOPE.md` already states.

Design questions the automation plan must answer (recorded, not decided):

1. **Detection reliability**, the decision's own precondition: the watcher must add Vinyl release-tag watching, and false-positive behaviour (pre-releases, re-tagged betas, tags on unexpected branches) must be defined before any automated action hangs off it.
2. **Pin provenance**: the automated re-pin records the tag's peeled commit and archive digest at detection time. Whether to add a soak delay or upstream signature verification before acting is a supply-chain posture choice — automating response to a new tag means a compromised upstream tag can flow further without a human eye, and the mitigations should be chosen deliberately.
3. **Publication authority**: today the draft-to-release flip is a human action. Unattended release means either granting the automation that flip or defining a timed auto-publish with a notification window. This is the single largest authority change and deserves its own recorded decision.
4. **VMOD version policy**: "latest version of each VMOD" in the vision versus the cohort model's pinned set — an automated Vinyl release response needs a rule for which VMOD versions ride along (current pins, or current pins plus any newer detected releases, each with its own evidence cost).
5. **Failure behaviour while unattended**: every red stops that release and notifies; nothing retries into a published artifact.

## Work items surfaced

- Add Vinyl Cache release-tag watching to `tools/upstream_watch.py`.
- Add watched trunk branches for dict and redis.
- Add trunk harness lanes for dict and redis (existing closing-report open item).
- Author the release-automation plan answering the five design questions above; amend `SCOPE.md` again when its answers change authority or delivery promises.
