# Step 8 Wave 3e: release-draft on the isolated graph, and the publication gate

Date: 2026-07-30

Status: **Implemented; host-safe verification complete, nothing dispatched.** All batteries green, both validation modes green, all four ledgers byte-identical to `fead175`, every extracted `run:` block shell-parsed, and the generalised manifest script rendered end to end against a fixture assets tree. **No release draft has been assembled** — the first live dispatch is what proves the parts that cannot be proved offline.

Branch: `step8-wave3e-release-draft`, off `main` at `fead175`.

Related: [failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md) Phase 4 and its `release-draft.yml` section; [Wave 3d](20260730_1013_note_step-8-wave-3d-release-transactions.md); [Wave 2 live proof](20260730_0824_report_step-8-wave-2-live-proof.md); [the step-10 CI design note](20260725_1740_note_step-10-ci-design.md).

## The migration

`release-draft.yml` was the last workflow on the legacy graph, and it was the one that could least afford to stay there. Its shape was `registry-selftest` → one shared `cachetag-source-archive` → one hardcoded job per lane → `assemble-draft-release`, and **it could describe exactly one VMOD**. The release cohort has required three since 2026-07-29, so the workflow could not have assembled a correct draft at all: it would have published cachetag and silently omitted dict and redis.

The graph is now `ci.yml`'s, at `--tier release`:

| Job | Notes |
| --- | --- |
| `structural-validation` | keeps the cachetag tag verification and the `validate --require-releasable` hard gate, including its `allow_incomplete_evidence` downgrade and the step-summary listing, unchanged |
| `discover-vmods` | every VMOD |
| `discover-engines` | `--tier release` — **two** rows, both `vinyl-release` |
| `engine` | `ci.yml`'s engine job, **copied**, minus the two injection steps and the artifact suppression |
| `vmods` | `vmod-package.yml` at `tier: release` |
| `collect` | `reconcile --tier release`, publishing `release-reconciled-ledger` |
| `verify-release-set` | **new** — the publication completeness gate |
| `assemble-draft-release` | behind both gates; downloads by ledger pattern |

Copied rather than paraphrased. That is Wave 3d's lesson applied deliberately: the first draft of *that* wave's engine job was a paraphrase and got the stage commands subtly wrong, and no linter available here would have caught it.

The **fresh clean-room policy is unchanged** and now stated where it can be read: every dispatch rebuilds from the pinned inputs and reuses no artifact from any other run, so what lands in a draft is the output of one traceable run rather than a mix of whatever was still retained.

`assemble-draft-release` keeps its `environment: release-draft` and its `permissions: contents: write` confinement exactly as before — that is the one job in the repository that can write a release, and Wave 3e did not widen it. What changed is that it downloads `packages-*` and `vmod-source-*` **by the ledger's stable patterns** instead of three hardcoded artifact names, so a fourth VMOD needs no edit.

## The completeness gate

`ci_matrix.py verify-release-set --reconciled … --packages … --cohort … [--allow-incomplete]`.

The plan's rule is explicit: *"release assembly must not publish a partial required package set merely because the other nine succeeded."* Reconciliation already answers **did every expected row report**. This answers the different question — **is what they reported enough to publish** — and that needs two things reconciliation does not have: the artifacts themselves, and the cohort's own statement of what it must contain.

Four findings, each naming a different repair:

| Finding | Means |
| --- | --- |
| `row_failed` | a required VMOD's selected row did not pass |
| `missing_artifact` | a row passed but published no package artifact |
| `bad_checksums` | an artifact's `SHA256SUMS` is unparseable, lists nothing, names a file that is not there, or does not match the bytes beside it |
| `required_mismatch` | the VMODs this run built are not the VMODs `required_vmods` names — checked **both ways**, because one direction is an incomplete release and the other is a package nobody selected |

**The checksum check finds `SHA256SUMS` anywhere under an artifact and resolves listed names relative to it**, which is what `sha256sum -c` does. That is not generality for its own sake: the two lanes upload different trees — the upstream-recipe rows put their checksum file at the artifact root, the generated rows under `out/` — and knowing where to look would have made one of them special. A checksum file that lists **nothing** is also a finding, because it would otherwise let an empty artifact pass silently.

**`allow_incomplete_evidence` makes it list rather than skip.** The check still runs, the report still says `complete: false`, every omission is still in the step summary and the JSON report, and the findings travel into `release-manifest.json`'s `evidence_gaps` and into the release body through `RELEASE_EVIDENCE_GAPS`. Only the exit status differs. A gate that went quiet under the experimental flag would publish something that looked whole, which is worse than not having the gate.

**The negative proof happens live, after merge.** Eight selftests cover every finding against a fixture built from the real ledger — including the one the live run cannot easily stage, an artifact simply absent from an otherwise green run — but "a suppressed artifact refuses assembly" as an end-to-end statement about the workflow needs a dispatch. That belongs in the Step 8 closing report.

## Generalising release-manifest.sh

Three decisions worth recording.

**Which VMODs the release describes: the cohort's `required_vmods`.** Not a list in the script, not the catalog, not what happened to be downloaded. `registry/README.md` makes `required_vmods` the cohort's own statement of what it must contain, cross-checked against the catalog in both directions, so it is the one authority that cannot disagree with what CI built. A fourth VMOD appears in the manifest and the body without an edit here.

**Names and versions: `release_tool.py metadata --vmod`.** It is the single generator of them, and the runbook is explicit that a recipe disagreeing with it is a bug in the recipe. It is *not* the authority for digests — `recorded-evidence` reports what a **previous** run produced, and this is a fresh clean-room build, so every digest in the manifest is computed from the bytes just built. `vmod_recipe.py names` was not used: it needs an overlay, so it cannot answer for cachetag at all, and `metadata --vmod` answers for all three from the registry alone.

**Pinned source digests: `ci_matrix.py source-facts`.** The VMOD manifest is the one place a source archive's identity is pinned, for all three VMODs, and this replaces the single `CACHETAG_SOURCE_SHA256` the cachetag-only script asserted against. The lane pin is still cross-checked against the manifest, because that assertion is what caught the four-copies problem on 2026-07-25 and is cheap to keep.

**The cachetag `configure.ac` cross-check stays cachetag-specific**, deliberately. It is the only thing tying a manifest to a real cachetag release, and cachetag is the only VMOD whose version this repository checks against a sibling source tree; the others are pinned by archive digest, which their own source rows verify.

Two smaller rules the rewrite made explicit:

- **Mandatory versus incidental assets.** Everything the registry *names* — each VMOD's native package on each target, and its source archive — is mandatory and its absence is a stop. The debug package, the source package, the `.changes` and the `.buildinfo` are *incidental*: published because they are useful, discovered rather than named, and their absence is not a failure (the RPM lane produces no `.buildinfo` at all).
- **Nothing else travels.** The generated lanes' artifacts also carry the verify scripts, the ported VTCs, the rendered recipe and the transaction logs. Those are evidence, they stay in the run's artifacts, and they are not release assets. The fixture run below confirms `scripts/` and `logs/` do not reach the upload directory.

`declare -A` was removed on the way through: bash 3.2 has no associative arrays and that is the bash on the maintainer's macOS host, so the script would have been verifiable only on a runner — which means verifiable only by dispatching it.

### The fixture run

A scratchpad assets tree mimicking the isolated graph's download layout (`packages/<artifact>/…` with the generated rows' checksum file under `out/`, `source/<artifact>/…`), plus a scratchpad copy of the repository whose three `archive_sha256` pins were rewritten to the fixture bytes' digests — because the real pinned digests are the real upstream bytes, which a fixture cannot have.

Run as-is against the real registry first, the pinned-digest assertion fired exactly as it should:

```text
E: libvmod-cachetag-1.0.1.tar.gz sha256 d3c0d5da… does not match the pinned 9aba3eff…
```

Against the scratch copy it rendered completely. `release-manifest.json` is now `vcache-packaging-release-manifest/v2` with a `vmods` array — one block per required VMOD, each with its upstream version, package revision, source archive and digest, ABI expressions, and both targets:

```json
"vmods": [
  { "vmod": "cachetag", "upstream_version": "1.0.1", "package_revision": "1",
    "source_archive": "libvmod-cachetag-1.0.1.tar.gz", …
    "targets": [ { "target": "debian-13-amd64", "filename": "libvmod-cachetag_1.0.1-1_amd64.deb", … },
                 { "target": "el9-x86_64",      "filename": "libvmod-cachetag-1.0.1-1.el9.x86_64.rpm", … } ] },
  { "vmod": "dict",  "upstream_version": "1.7",  "package_revision": "2", … },
  { "vmod": "redis", "upstream_version": "23.1", "package_revision": "1", … }
]
```

and the body carries a `## Package families` section with a table per family:

```markdown
### dict 1.7-2

| target | package | sha256 |
| --- | --- | --- |
| debian-13-amd64 | `vmod-dict_1.7-2_amd64.deb` | `a17bbaaa…` |
| el9-x86_64 | `vmod-dict-1.7-2.el9.x86_64.rpm` | `1bab2456…` |

Source: `vmod-dict-1.7.tar.gz` (sha256 `f871e232…`)
```

Twenty assets in the upload directory: six native packages, three debug packages, three `.dsc`, three `.debian.tar.xz`, three source archives, plus `RELEASE-SHA256SUMS` and `release-manifest.json`. No `scripts/`, no `logs/`.

## The fixture rig

`step8-fixture` was rebased onto post-3c `main` and is at **`c118541`**. The acceptance re-run **`30529847035`** reproduced the designed shape exactly against the migrated graph: four failures — `fixture4`'s source row and its two package rows, plus the collector exiting 1 by design — reconciliation naming exactly those, and **zero cancellations**. The rig stays unmerged and reusable; this wave changed the release graph, which is another shape change it can be re-pointed at.

## What remains for the Step 8 closing report

- **The live release-draft dispatch.** Three things need it: a complete set assembling three families into one draft, the `allow_incomplete_evidence` path listing omissions in a published body, and the negative proof — a suppressed package artifact refusing assembly.
- **The release-transactions dispatch `30532484094`**, running at the time of writing. Its outcome, and the dict/redis `upgrade_transactions` verdict flip it enables, are Wave 3d's business and belong in the closing report with the run id.
- **The first live `trunk-early-warning` gated skip.** Wave 3c proved the forced run (`30528344114`, red on the genuine `cache_vinyld.h` → `cache_int.h` signal) and the skip (`30528844197`, one job); what has not happened yet is a *scheduled* run deciding for itself.
- **Roadmap §8's remaining bullets**: recurring survey integration, and adding further VMODs one at a time with a `SCOPE.md` decision each.

## Verification performed on the host

| Check | Result |
| --- | --- |
| `ci_matrix.py selftest` | **287/287** (was 279; eight new release-set checks), chaining `vmod_recipe` 218/218 and `upstream_watch` 62/62 |
| `release_tool.py selftest` | 172/172 |
| `release_tool.py validate` / `--require-releasable` | both green |
| `ledger --tier ci` / `release` / `transactions` / `trunk` vs `fead175` | **byte-identical, all four** |
| `release-manifest.sh` against the fixture tree | renders three families, 20 assets, no evidence files published |
| `release-manifest.sh` against the real registry with fixture bytes | refuses on the pinned source digest, as designed |
| `bash -n` on `release-manifest.sh` and `verify-recorded-digests.sh` | clean |
| every `run:` block of `release-draft.yml`, extracted and `bash -n` | 15 blocks, 0 errors; zero `inject` references |

`actionlint` is not installed and was not installed.
