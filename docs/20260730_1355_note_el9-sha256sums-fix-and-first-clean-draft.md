# The EL9 SHA256SUMS fix, and the first complete=true draft

Date: 2026-07-30

This note closes open item 3 of [the Step 8 closing report](20260730_1232_report_step-8-closing.md): the EL9 SHA256SUMS layout defect that Proof A (run 30536439592) surfaced on unmodified main, which made `complete=true` unreachable on every release-tier run and forced every draft through the `allow_incomplete_evidence` escape hatch.

## The defect

`recipes/el9/container/build.sh` (stage_report, formerly line 306) wrote `/out/SHA256SUMS` with bare filenames while the rpms it listed landed in `/out/packages/`. The release completeness gate (`_sha256sums_problems`, tools/ci_matrix.py) resolves every listed name against the checksum file's own directory — the `sha256sum -c` contract — so each of the nine listed rpms produced a `bad_checksums … listed but not in the artifact` finding on the cachetag/vinyl EL9 audited-recipe artifact. The selftests never saw it because the release-set fixture invented self-consistent artifact layouts; only the live dispatch could surface it.

## The fix (commit ccb8e79)

The checksum file moved beside the rpms it describes: `dist/el9/packages/SHA256SUMS`, bare names, written from inside the packages directory. Chosen over the alternative — keeping the file at the artifact root with `packages/`-prefixed names — for three reasons:

- Beside-the-files is what `sha256sum -c` and the gate both natively verify, and it is the layout the other two lanes already use: Debian's `dist/debian-13/SHA256SUMS` sits beside its debs, the generated lane's `lane/out/SHA256SUMS` beside its packages. EL9 was the odd one out.
- The only live readers of the old file both want bare names. `recipes/el9/mismatch/container.sh` cds into `/out/packages`, greps the file with a name-anchored pattern (`^<sha256>  (vinyl-cache|vinyl-cache-devel)-`) and pipes it straight to `sha256sum -c`; prefixed names would have broken both the anchor and the resolution. With the move, each reader needed only a path change.
- `scripts/ci/release-manifest.sh` computes release digests fresh from the package bytes and never reads a lane SHA256SUMS, and the engine artifact's digests live in `engine-metadata.json` (produced by `ci_matrix.py engine-metadata` from the Mock build), so neither is affected in either direction.

Everything touching the old location moved in the same commit: the mismatch fixture's baseline-digest verification and existence check, the artifact upload list in `vmod-package.yml` (`dist/el9/packages/SHA256SUMS`), and the generated rpm rows' transaction staging (`scripts/ci/vmod/transactions.sh`), which wrote the same root-level shape into `lane/txn/SHA256SUMS` and feeds the same fixture scripts — it now writes `lane/txn/packages/SHA256SUMS`. One sibling deliberately left as is: the mismatch fixture's own `dist/el9/mismatch/SHA256SUMS` still sits above `mismatch/packages/`; it is a provenance record compared by `diff` for reproducibility, nothing resolves its names `sha256sum -c` style, and the completeness gate never examines a transactions-tier artifact.

Three guards so the layout cannot drift apart again, all in `tools/ci_matrix_selftest.py`: the release-set fixture now mirrors the real per-lane layouts (generated rows under `out/`, audited Debian at the root, audited EL9 under `packages/`) instead of one invented shape; a verifier-level test pins that the broken root-level layout is rejected (`listed but not in the artifact`) and the identical digests verify once beside the rpms; and a static check holds `build.sh`, the upload list and both readers to the same location. Selftest totals after the change: ci_matrix 325, recipe generator 218, upstream watch 62, release_tool 172, all green.

## Run 30541746563: the first non-escape-hatch draft

Dispatched from main @ ccb8e79 with `track=release`, `allow_incomplete_evidence=false` — the first release-draft dispatch ever made without the escape hatch, possible because dict's EL9 verdict had just been adjudicated to pass (c90ac6f, [adjudication note](20260730_1300_note_step-8-dict-el9-adjudication.md)) and this fix removed the last structural blocker.

Conclusion success, every job green: structural-validation (including `validate --require-releasable`), both engines, all three VMODs' manifest/source/package/summary rows, reconcile, `is the required package set complete`, and `assemble the internal draft GitHub Release`. The gate reported required VMODs = built VMODs = cachetag, dict, redis; package rows 6; zero findings; "The set is complete." — `complete=true` for the first time. Draft `draft-20260730T125322Z-ccb8e79d9579` was created with 57 assets, and its `release-manifest.json` records `"evidence_gaps": []` — also a first; every prior draft carried recorded gaps.

The draft was a proof, not a release: it was deleted after evidence capture. Drafts create no git tag, and `ls-remote` confirms the only remote tag remains `cohort-vinyl-9.0.0-4b7e68292979`; the pre-existing `draft-20260728T130227Z-aaa14876510c` draft and the cohort pre-release are untouched.

## What this changes

Open item 3 of the closing report is closed: main can now produce `complete=true`, and a release-tier draft no longer needs `allow_incomplete_evidence`. Open item 4 (the roughly-20-versus-57 asset-count expectation) remains open; this run reproduced 57, consistent with Proof A's sidecar accounting. — Resolved 2026-07-30: the 20 is now scoped to the fixture tree and the live 57-asset arithmetic recorded in [the 3e note](archive/20260730_1107_note_step-8-wave-3e-release-draft.md) as amended.
