# Step 10: the cachetag archive digest pin was a dirty-tree artifact; re-pinned from a clean commit

Date: 2026-07-25

## What CI caught

The first real run of `ci.yml`'s source-archive job rebuilt the cachetag archive from a clean checkout and computed `a262ac7a74a1464d4c0a4cc6f072ea04a77ff660b25bf0befd32dc63c18fb329`; the pinned `CACHETAG_SOURCE_SHA256` was `c7054e69…`. The pinned archive's own metadata sidecar explained the mismatch: `worktree_dirty: true`, built from uncommitted state seven commits behind the branch head, against the old Vinyl pin. No commit anywhere could reproduce that digest — the pin was a laptop artifact of exactly the kind the plan's no-laptop-publishing rule exists to exclude, and the clean-room job caught it on its first opportunity.

## Evidence for the new pin

- Two independent amd64 GitHub runners produced byte-identical `a262ac7a…` (53/53 VTCs passing in the distcheck stage each time).
- The arm64 laptop harness (`scripts/release-source-archive.sh --vinyl-git ../vinyl-cache --vinyl-ref 25761f8505…`) produced the same `a262ac7a…` — cross-architecture and cross-toolchain-host determinism.
- The digest is a function of the cachetag commit alone: archive mtimes are pinned to the commit's committer date (metadata `source_date_epoch: 1784997430` = `fcc369d`'s committer date), so any new commit changes the digest. Consequently `CACHETAG_REF` in the workflows is now the exact commit `fcc369d23b199cc8e41086f28f2322256a8843d9`, not the moving branch — closing the CI design's open question 1. The future `v1.0.0` tag must point at this commit (or the pin set moves again, deliberately).

Values moved together in this change: `CACHETAG_SOURCE_SHA256`/`CACHETAG_SHA256` and new `CACHETAG_GIT_COMMIT` in `recipes/debian-13/build.sh` + `recipes/el9/cohort.env`, `CACHETAG_SOURCE_DATE_EPOCH` (1784926281 → 1784997430) in the Debian driver, the digest in `scripts/ci/release-manifest.sh`, and digest + `CACHETAG_REF` in all three workflow env blocks.

## Flake observations during the arm64 confirmation runs

Two archive-build attempts were needed; both failures were load-flake-class VTC count assertions, neither digest-relevant:

1. Attempt 1: `cachetag_pm00027.vtc` (`EXPECT resp.http.objects (5) == "4"`, volatile-object lifecycle count) failed in the distcheck stage. First recorded failure of this test; the same suite passed 4/4 on amd64 CI the same day. Watch item — if it recurs, adjudicate with a 10x-copy run per the flake policy before treating anything as a regression.
2. Attempt 2: distcheck passed (and produced the digest); the from-archive standalone-build proof then failed on `cachetag_pm00007.vtc`, the documented ~20–30%-under-load flake with an existing quarantine policy.

Per the flake policy, each was rerun once manually and investigated, not blanket-retried. The digest was confirmed despite attempt 2's late-stage flake because the archive and its digest are produced and verified before the from-archive proof runs; CI remains the gate that must pass all stages.
