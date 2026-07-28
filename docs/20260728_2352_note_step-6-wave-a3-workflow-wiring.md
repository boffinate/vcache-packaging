# Step 6 Wave A3: wiring both recipe strategies into CI

Date: 2026-07-28

Status: Implemented

Branch: `step6-second-vmod`

Related:

- [Wave A2: CI integration](20260728_2334_note_step-6-wave-a2-ci-integration.md) — the schema, ledger, evidence model and lane scripts this wires up
- [Wave A1: the recipe generator](20260728_2216_note_step-6-wave-a1-recipe-generator.md)
- [VMOD matrix failure isolation](20260728_0833_plan_vmod-matrix-failure-isolation.md), Phase 3
- [vmod-packager patterns and recipe generation](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md), Phase 5

## Four defects, found by reading before writing

The wiring was specified by an agent that could not execute anything. Working entirely by reading, it found four real defects — three of them in code that had already passed a full self-test battery. That is worth recording on its own: three of the four are invisible to any test that does not model a two-VMOD run, and one was invisible to every test because the thing it broke had no test.

### D1 — the dict source injection was inert

`expand()` injects a source failure by rewriting the row's `ref` to `vmod-ci-injected-missing-ref`, and the workflow passes that row value down. But `source.sh` read the ref back out of the manifest through `ci_matrix.py source-facts`, so the rewritten value never reached the check it was supposed to fail. `inject=dict_source` would have produced a **green** run.

The cachetag path never had this problem because it has always taken its ref from the matrix row — `actions/checkout` is given `ref: ${{ matrix.ref }}`. The generated lane read from the manifest because that is where every other source fact correctly comes from, and the ref is the one that must not.

**Fix (ruling R-A):** `source.sh --ref REF` overrides the manifest's ref, and the workflow always passes `${{ matrix.ref }}`. Nothing else is overridden — the recorded commit, digest and version stay the manifest's — so an overridden ref that does not peel to the recorded commit fails the tag check, which *is* the injected failure rather than a special case bolted beside it.

### D2 — the manifest injection could not demonstrate what it claimed

Recorded as ruling R-B and corrected in the [A2 note](20260728_2334_note_step-6-wave-a2-ci-integration.md) in Superseded-assumptions style. Summary: the injection corrupted every manifest, which makes `valid_manifests()`' claim — one broken manifest costs its own invocation and the engine rows nothing else consumes — untestable, because no VMOD survives to be observed surviving. Now scoped to cachetag, with the scope read from `ci_matrix.py injection-scope` by all four jobs that rebuild the expected ledger.

### D3 — three injection expressions could not express "one VMOD"

`inputs.inject == 'debian_build'`, `inputs.inject == 'el9_build'` and the hardcoded `suppress_result` gate are workflow-level comparisons against a scalar input. They fire in *every* VMOD's invocation, because the reusable workflow receives the same `inject` input for all of them. After the second VMOD, `inject=debian_build` would have failed cachetag's Debian rows **and dict's**, which demonstrates a broken run rather than a contained one.

**Fix:** they read `matrix.inject_build` / `matrix.suppress_result`, per-row booleans the expansion already emits from `INJECTION_TARGET_VMOD`. This is the pattern the engine rows have used since Phase 2 (`matrix.inject_build`, `matrix.suppress_artifact`), so it is a return to an established convention rather than a new one.

### D4 — an `--inject-token` flag computed and never passed

Found by containerized `actionlint`, which runs shellcheck over `run:` bodies: `SC2034: inject appears unused`. The generate step built the flag from `matrix.inject_recipe` and then omitted it from the command line, so `inject=recipe_generation` would have produced a green run. Exactly the class of bug a linter exists for, and exactly why the linter runs in a container rather than being skipped for want of a host install.

## Job structure: per strategy, not per step

**Ruling R-C, confirmed.** `vmod-package.yml` now has four package jobs rather than two:

| Job | Strategy | Gate |
| --- | --- | --- |
| `source` | upstream-owned recipe | `needs.plan.outputs.recipe == 'upstream'` |
| `target` | upstream-owned recipe | `needs.plan.outputs.recipe == 'upstream'` |
| `source-generated` | generated recipe | `needs.plan.outputs.recipe == 'generated'` |
| `target-generated` | generated recipe | `needs.plan.outputs.recipe == 'generated'` |

**Why not gate steps inside one job.** Every skipped step reports `outcome: skipped`. The classification chains check for `failure` and for `!= 'success'`, and `skipped` is neither `failure` nor `success` — so a generated-recipe row running through the upstream job would fall through to `failed_infrastructure`, and the final `fail when this row failed` expression would fire unconditionally on a row that did nothing wrong. Splitting the jobs keeps each chain reasoning about steps that actually ran.

**The cost, recorded.** The engine download → `engine-identity.sh` → `verify-engine-metadata` → install block appears in both target jobs. That is the same bounded duplication the A2 note already accepted for the two `container-pbuilder.sh` implementations, for the same reason and with the same intended resolution: merge it in a change whose only purpose is the merge, after both strategies have been proven by a real run, so any package-byte movement has one cause.

**The strategy is selected, never discovered.** `recipe` comes from the manifest through `expand`, which the recipe-generation plan requires: newly found upstream packaging must not silently displace a recorded strategy, because that would change package contents and recipe provenance without a manifest decision.

## The cachetag-path edit list

`git diff -U0 main -- .github/workflows/vmod-package.yml` removes exactly **nine** lines. All nine are `if:` or `needs:` expressions. **No step body, no command, no pin, no `uses:` and no artifact path changed.**

| Line | Was | Is | Why |
| --- | --- | --- | --- |
| `plan` | `if: inputs.inject == 'manifest'` | `… && inputs.vmod_id == 'cachetag'` | R-B: the injection is scoped |
| `summary` | same | same | R-B: this job rebuilds the ledger and must see the same corruption |
| `source` job | `if: … source_count != '0'` | `… && recipe == 'upstream'` | strategy dispatch |
| `target` job | `if: … target_count != '0'` | `… && recipe == 'upstream'` | strategy dispatch |
| `deb_inject` | `inputs.inject == 'debian_build'` | `matrix.inject_build == 'true'` | D3: an expression cannot say "one VMOD" |
| `el9_inject` | `inputs.inject == 'el9_build'` | `matrix.inject_build == 'true'` | D3, same |
| `upload_result` (2 lines) | hardcoded family/engine test against `inputs.inject` | `matrix.suppress_result != 'true'` | D3, same; the row already knows |
| `summary` | `needs: [plan, source, target]` | `+ source-generated, target-generated` | the summary covers every row of this VMOD |

Additive-only elsewhere: `plan` forwards three outputs (`recipe`, `source_host`, `clone_url`) that `cmd_expand --format github` has printed since the second VMOD landed. The `outputs:` block previously forwarded only `repository`; adding three more changes nothing the job runs.

`git diff main -- recipes/debian-13/ recipes/el9/ scripts/ci/debian13/ scripts/ci/el9/ scripts/ci/lib/` is **empty**. `make-chroot.sh` is invoked by `target-generated` and not modified; it writes to `dist/debian-13/work/chroot/`, and the workflow copies the tarball into the generated lane's `chroot/`.

## `ci.yml`: nearly a no-op

Discovery already yields both VMODs from file names, and the collector already rebuilds the ledger from the catalog. Two changes:

- three dispatch options added (`dict_source`, `dict_build`, `recipe_generation`), with the choice list regrouped by which VMOD each case acts on;
- both manifest-corrupting steps (`discover-engines` and `collect`) now corrupt the single file `ci_matrix.py injection-scope` names, instead of looping over every manifest.

`nightly-transactions.yml` and `release-draft.yml` are untouched. `trunk-vmod-ci.yml` stays cachetag-hardcoded, correctly: dict has no trunk source channel and no `vinyl-trunk-*` lane.

**R1 finding, recorded:** `--require-releasable` appears in exactly one workflow, `release-draft.yml`, and nowhere in `ci.yml`. No scoping was needed. CI stays green while the release draft is correctly blocked by dict's pending evidence — which is the split the A2 evidence-schema work intended, confirmed rather than assumed.

## Verification

| Check | Result |
| --- | --- |
| `release_tool.py validate` | OK, 10 manifests |
| `release_tool.py --no-cachetag-cross-check validate` | OK |
| `release_tool.py validate --require-releasable` | **RED, expected** — dict's evidence is `pending` |
| `release_tool.py selftest` | 138/138 |
| `ci_matrix.py selftest` | **188/188** (was 179; 9 added for R-A/R-B) |
| `vmod_recipe.py selftest` | 125/125 |
| `ledger --tier ci` | **15 rows, 14 selected**, 4 engine rows. The unselected one is cachetag's `vinyl-trunk-head` source-harness lane, which no tier-`ci` workflow runs |
| containerized `actionlint`, all 5 workflows | clean — **and it caught D4** |
| containerized `shellcheck --severity=error`, all lane scripts | clean. Bare `shellcheck` exits 1 on this project's established `CDPATH= cd --` idiom (SC1007) and on `SC1091` for sourced files it was not given; both are excluded deliberately, not incidentally |
| cachetag lane diff vs `main` | empty |

### Collector simulations

Hand-written result records reconciled against the real ledger. The point of each is what stays green, not what goes red.

| # | Scenario | Result |
| --- | --- | --- |
| 1 | all-green two-VMOD run | exit 0; 14 expected, 14 passed, 1 lane not selected for this tier |
| 2 | dict `recipe_generation` fails, cachetag green | exit 1; 13 passed, 1 failed. **All six cachetag rows PASS** |
| 3 | cachetag `debian_build` fails both engines, dict green | exit 1; 12 passed, 2 failed. **All four dict rows PASS** |
| 4 | dict `suppress_result` on the Debian row | exit 1; 1 row `missing_result_record`, synthesized by the collector |
| 5 | cachetag-scoped `manifest` injection | exit 1; ledger shrinks to **7** rows: cachetag collapses to one `failed_manifest_validation` row, **all four dict rows PASS**, and only the two engine rows dict still asks for are expected |

Simulation 5 is the one D2 made impossible. The trunk-pinned engine rows disappearing from the expected ledger is correct and is the property being tested: only cachetag's lanes consumed them, so with cachetag's manifest unparseable nobody asked for them, and the collector must not report them as missing.

## What Wave B must prove

Unchanged from the A2 note, now with the mechanism in place to prove it:

1. **Baseline both-VMOD run.** All 14 selected rows green.
2. **Two-way isolation.** `debian_build`, `el9_build`, `source_checkout`, `source_digest`, `suppress_result` and `manifest` fail only cachetag rows; `dict_source`, `dict_build` and `recipe_generation` fail only dict rows. Simulations 2, 3 and 5 model the reconciliation; Wave B proves the workflow produces those records.
3. **`recipe_generation`.** Exactly one dict row classified `failed_recipe_generation`, and no other row.
4. **Equivalence for cachetag against `main`.** Debian digests byte-identical excluding `.buildinfo` and `.changes`; EL9 by the Step 3 normalized semantic comparison.

   The reasoning has to be stated precisely, because "nothing changed" is not quite true. `metadata.py`'s `rpm_requires` **did** change for cachetag on every cohort — from the Debian virtual-package names to the arch-qualified `vinyld(abi)%{?_isa}`, `vinyld(vrt)%{?_isa}` and `vinyld(cohort-…)%{?_isa}` capabilities RPM actually provides. It is inert **because it is unconsumed**, not because it is unchanged: nothing on cachetag's build path reads `RPM_REQUIRES`. The EL9 lane substitutes tokens into cachetag's own audited spec, which spells those Requires itself; only the *generated* spec template renders them from this function. `scripts/ci/release-manifest.sh` reads `CACHETAG_ABI_DEB_DEPENDS`, `CACHETAG_ABI_COHORT_PROVIDE` and `CACHETAG_ABI_RPM_COHORT_PROVIDE`, all byte-identical.

   So: nine `if:`/`needs:` lines changed, and one generated value changed that no cachetag consumer reads. **If EL9 equivalence moves in Wave B, `Requires` is the first place to look** — that is the one place where a wrong belief about who reads what would show up.
5. **dict evidence populated.** Both `vmods.dict` entries move `pending` → `recorded`, after which `--require-releasable` goes green — the gate closing.
6. **Behaviour suites green on installed packages**, both VMODs, both targets. For dict: `dict_cs.vtc` and `dict_ci.vtc` against the packaged `.so` through `-p vmod_path`, `num.dict` from the digest-verified archive, upstream's expected values unmodified.
7. **The new gates fire.** dict's payload allowlist and its strict `lintian`/`rpmlint` expectations have never run. A first pass is itself evidence; a first failure is a finding about the templates or the overlay, never a reason to relax the gate.

## Open questions for the audit

1. **`target-generated` has one classification for six distinct checks.** `verify-deb.sh` covers payload, ABI, hardening, lint, install smoke and behaviour in one step, so all six classify as `failed_install_or_smoke`. The cachetag path separates `failed_abi_or_hardening`, `failed_lint`, `failed_install_or_smoke` and `failed_behavior` because it has four steps. Splitting the verify script into four container invocations would restore the distinction at the cost of four container starts per row; leaving it means the summary names the log to read rather than the stage that failed. Worth deciding before Wave B, since Wave B is what will make anybody read those classifications in anger.
2. **The engine block is duplicated across the two target jobs**, as recorded above. Confirm the intended merge point is after Wave B proves both strategies.
3. **`run.sh` reads `EL9_IMAGE` out of `recipes/el9/cohort.env` with `sed`.** Still the one place the new lane parses a pin file textually rather than through a tool, and still unresolved from the A2 note.
