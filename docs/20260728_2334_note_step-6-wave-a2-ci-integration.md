# Step 6 Wave A2: CI integration for the second VMOD

Date: 2026-07-28

Status: **Partially implemented.** The catalog, schema, ledger, classification, injection model and the host-safe half of the dict lane are done and verified. The workflow YAML, the EL9 Mock lane and the per-VMOD evidence schema are **not done** — see "What is not done" before reading anything else as complete.

Branch: `step6-second-vmod`

Related:

- [Wave A1 note](20260728_2216_note_step-6-wave-a1-recipe-generator.md) — the generator and the verified dict facts this builds on
- [VMOD matrix failure isolation](20260728_0833_plan_vmod-matrix-failure-isolation.md), Phase 3
- [vmod-packager patterns and recipe generation](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md), Phase 5
- [Step 5 selection](20260728_2127_note_step-5-second-vmod-selection.md), ruling 5

## What is done

### 1. dict is in the catalog

`registry/vmods/dict.yml`, moved out of the Wave A1 staging directory. `ci_matrix.py check-catalog` now discovers two VMODs and the `ci` tier ledger expects **14 rows**: four engine rows (unchanged — dict shares `vinyl-release`, so it adds none), cachetag's six, and dict's four (one invocation, one source, two targets).

### 2. The manifest says how the source is reached and which recipe is built

Two schema additions, both required, both replacing something that was previously implied.

**`source_host`, `repository`, `clone_url`.** Step 5's ruling 5 recorded that `REPOSITORY_RE` was GitHub-shaped and `vmod-package.yml` used `actions/checkout`, while every viable candidate except one is off GitHub. The regex happened to accept `git.gnu.org.ua/vmod-dict` as well as `owner/name`, so a non-GitHub upstream was representable *by accident*, by writing something into a field that means something else. Now `source_host` is `github` or `git`; a GitHub entry carries `repository` and may not carry `clone_url`; a git entry carries `clone_url` and may not carry `repository`. Both halves of the exclusivity are validated and tested, because either one alone would let an entry carry an address its declared host cannot be reached at.

**`recipe`.** `upstream` or `generated`, recorded rather than inferred from the adapter name. The recipe-generation plan is explicit that newly discovered upstream packaging must never silently displace a recorded strategy, since that would change package contents and recipe provenance without a manifest decision. cachetag is `upstream` — it keeps its audited recipe deliberately, as the oracle the templates are measured against.

**`sources.<channel>.archive_url`.** Optional in general, required when `recipe: generated`, and it sits beside `archive_sha256` because a URL and a digest are one statement about one set of bytes.

### 3. The non-GitHub checkout problem dissolved rather than being solved

The interesting result of this wave. dict's source stage needs **no clone at all**:

1. download the archive the manifest names;
2. assert its sha256 against the pin — these exact bytes;
3. `git ls-remote "$clone_url" "refs/tags/v1.7^{}"` and require the peeled commit to equal the recorded one;
4. unpack and cross-check the manifest's version against the archive's own `AC_INIT`.

Step 3 is not redundant with step 2. The digest says what we built; `ls-remote` says the human-meaningful release identity still points at the same place, which is what `SCOPE.md`'s source policy actually asks for and what catches a moved or re-tagged release even while the old archive is still served. One request, no working tree, no host-specific action.

So `vmod-package.yml` needs a non-`actions/checkout` path for dict, but a much smaller one than Step 5 anticipated: a shell step, not a second checkout mechanism.

### 4. `failed_recipe_generation`

Added to the status vocabulary. A rendering failure is not a build failure: nothing was compiled, the inputs are wrong, and the fix is in the manifest, the overlay, the adapter or the generator rather than in the source. Classifying it as `failed_package_build` would send whoever reads the summary to the wrong place. It also gives the plan's requirement — a missing generated recipe or generation record must be an explicit classified failure — something to be classified as.

### 5. Injection is per-VMOD, which is what makes two-VMOD isolation demonstrable

Before the second VMOD every injection implicitly hit every VMOD, because there was only one. With two, an injection that hit both would demonstrate a broken run rather than a contained one.

`INJECTION_TARGET_VMOD` in `ci_matrix.py` names the VMOD each case acts on, so the tool, the workflow and the tests cannot disagree. The pairing is deliberate — each VMOD has a source failure and a build failure, so every case can be run from either side:

| injection | acts on | proves |
| --- | --- | --- |
| `source_checkout`, `source_digest` | cachetag | dict's four rows complete while cachetag's source fails |
| `debian_build`, `el9_build` | cachetag | dict's rows complete while a cachetag target fails |
| `suppress_result` | cachetag | the collector synthesizes `missing_result_record` for one cachetag row only |
| `dict_source` | dict | cachetag's six rows complete while dict's source fails |
| `dict_build` | dict | cachetag's rows complete while dict's Debian row fails |
| `recipe_generation` | dict | an unresolved token in the *rendered* recipe fails one dict row and nothing else |
| `manifest` | every VMOD | deliberately global; that case is about the ledger, not about isolation |
| `engine_build`, `suppress_engine_artifact` | neither | they act in the caller's engine matrix |

The expansion carries per-row `inject_build`, `inject_recipe` and `suppress_result` booleans, so the workflow reads one flag instead of comparing ids and families in a YAML expression that could drift from the table.

`recipe_generation` corrupts the recipe **after** rendering, on purpose. The generator already refuses every token it can refuse, and `tools/vmod_recipe_selftest.py` covers that; this case proves the *lane* refuses a recipe that a build would otherwise consume literally, which is a different property.

### 6. The host-safe half of the dict lane

`scripts/ci/vmod/`, a lane of its own rather than a second scope threaded through `recipes/debian-13/` and `recipes/el9/`.

**Why separate, and the byte-neutrality argument.** cachetag's package bytes must not move in this wave. Threading dict through `container-pbuilder.sh` and `container-mock.sh` would have meant editing the exact code paths that produce those bytes, and the argument would then have been a reasoned one about scopes and branches. Keeping the lanes apart makes it a trivial one: **`git diff main -- recipes/ scripts/ci/debian13/ scripts/ci/el9/ scripts/ci/lib/ scripts/ci/source-archive.sh` touches nothing cachetag builds from.** The only files under `recipes/` this wave adds are `recipes/vmods/`, which cachetag does not read. The duplication between `scripts/ci/vmod/container/build-deb.sh` and `scripts/ci/debian13/container-pbuilder.sh` is real, bounded, and commented as deliberate; merging them belongs in a change whose only purpose is that, so the resulting package-byte diff has one cause.

Delivered and verified:

- `scripts/ci/vmod/source.sh` — the four checks above. Reads the manifest through `ci_matrix.py source-facts` rather than with `sed`: a second parser is a second thing that can disagree with the validator.
- `scripts/ci/vmod/generate.sh` — renders the recipe into the per-row work directory, refuses any surviving `@TOKEN@`, requires the generation record to exist, and lays out the source tree plus the Debian `.orig.tar.gz`. Dry-run on the host against the real archive: renders, asserts, lays out correctly.
- `scripts/ci/vmod/container/build-deb.sh` — pbuilder in the pinned `debian:trixie` container, engine `.debs` published as a local repository so the exact-version `Build-Depends` resolves.
- `scripts/ci/vmod/container/verify-deb.sh` — payload (including an allowlist that rejects anything outside the declared object, manual and documentation), generated ABI and cohort dependencies, hardening inspection, `lintian --fail-on error,warning` with an explicit expectation rather than `|| true`, runtime-pair-only install smoke, and the behaviour suite.

All shellcheck-clean, verified in a container (`koalaman/shellcheck:stable`).

### 7. The behaviour suite is ported

`recipes/vmods/overlays/dict/tests/dict_cs.vtc` and `dict_ci.vtc`, from upstream `tests/cs.at` and `tests/ci.at`. Exactly two bindings changed:

- `import dict from "$abs_top_builddir/src/.libs/libvmod_dict.so"` → plain `import dict;` resolved through `-p vmod_path`, so the subject is the packaged `.so`;
- `${vmod_topsrc}/tests/num.dict` → `${dictdir}/num.dict`, a macro the harness defines.

Every request URL and every expected value is upstream's, character for character — including `ci.at`'s mixed-case URLs (`/oNe`, `/twELve`), which are what make it a case-insensitivity test rather than a second copy of the other one. The `num.dict` fixture is **not copied into this repository**: `verify-deb.sh` extracts it from the digest-verified release archive, so there is one copy and the oracle cannot drift.

## What is not done

Stated plainly, because a partial wave reported as complete is worse than a partial wave.

| Deliverable | State |
| --- | --- |
| 2. `vmod-package.yml` two-strategy generality, generic renames | **Not started.** No workflow file was edited. |
| 3. EL9 Mock lane for dict (`build-rpm.sh`, `verify-rpm.sh`) | **Not written.** The Debian pair is the template for them. |
| 3. Host drivers `build-deb.sh` / `verify-deb.sh` (the `docker run` wrappers) | **Not written.** The container halves exist; the host halves are thin and follow `scripts/ci/debian13/debian-lane.sh`. |
| 5. Per-VMOD evidence schema in `registry/targets/` | **Not started.** See the open question below — the design is not obvious and it deserves a decision rather than a guess. |
| 6. Workflow injection wiring | Tool side done and tested; workflow side not wired. |
| Verification: containerized actionlint | Not run — no workflow changed, so there was nothing to lint. |

Nothing above is blocked; it is unfinished. The pieces that are done are the ones every later piece depends on, which is why they were done first.

## Verification run

| Command | Result |
| --- | --- |
| `release_tool.py validate` | OK, 10 manifests |
| `release_tool.py validate --require-releasable` | OK, 10 manifests |
| `release_tool.py selftest` | 112/112 |
| `ci_matrix.py selftest` | **179/179** (was 151; 28 added) then 125/125 for the generator |
| `vmod_recipe.py selftest` | 125/125 |
| `ci_matrix.py check-catalog` | OK, 2 VMODs |
| `ci_matrix.py ledger --tier ci` | 14 selected rows, asserted exactly by a self-test |
| shellcheck, `koalaman/shellcheck:stable` container, all new scripts | clean |
| `generate.sh` dry run against the real 1.7 archive | renders, refuses tokens, lays out the tree |
| `git diff main -- .github/` | empty |

The 28 new `ci_matrix` tests cover: both halves of the host/address exclusivity, the recorded recipe strategy, `archive_url` being required for a generated recipe, dict expanding to `vinyl-release` only, **per-VMOD injection isolation in both directions for all six targeted cases**, `failed_recipe_generation` being a non-OK status that a record can carry, `source-facts` output for both a git and a GitHub entry, and the exact 14-row ledger.

## Open questions for the audit

1. **Where does a second VMOD's per-target evidence live?** Carried over from Wave A1 and now the blocking design question for deliverable 5. `registry/targets/<cohort>/<target>.yml` records exactly one VMOD's build evidence, package revision, artifacts and test results, and its schema names `cachetag` in a top-level block. Three shapes are plausible — a `vmods:` list that becomes authoritative for all VMODs with the legacy blocks validated to agree with cachetag's entry; a per-VMOD evidence file under `registry/targets/<cohort>/<target>/<vmod>.yml`; or splitting shared target facts from per-VMOD evidence. The first keeps one file per target and one place to look but needs an agreement check to stop drift; the second is the least invasive to cachetag but multiplies files. The exit-gate clause is "both package families meet the same evidence policy as cachetag" and the schema has to make that *checkable*, so this should be decided rather than guessed.
2. **Should the two `container-pbuilder.sh` implementations be merged, and when?** They now duplicate the pbuilder configuration, the apt-resolver setting, the D hook and the local-repository publication. Merging them is right eventually; doing it in this wave would have put cachetag's package bytes at risk for no gain.
3. **`--fail-on error,warning` for `lintian` on the dict lane is stricter than the cachetag lane's treatment.** That is deliberate — a generated recipe has no excuse for a warning nobody reviewed — but it means the two families are not yet held to identical lint gates, which the exit gate arguably requires. Decide whether to raise cachetag or to record the asymmetry, in the same change that settles the `dh_missing` asymmetry from Wave A1.
