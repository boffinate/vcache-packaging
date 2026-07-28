# Step 6 Wave A2: CI integration for the second VMOD

Date: 2026-07-28

Status: **Implemented.** The workflow wiring landed in Wave A3; see [the A3 note](20260728_2352_note_step-6-wave-a3-workflow-wiring.md) for the four defects it found and the per-strategy job structure. Everything in this note is current except the "manifest is deliberately global" ruling, which A3 superseded — corrected in place below.

Branch: `step6-second-vmod`

Related:

- [Wave A1 note](20260728_2216_note_step-6-wave-a1-recipe-generator.md) — the generator and the verified dict facts this builds on
- [VMOD matrix failure isolation](20260728_0833_plan_vmod-matrix-failure-isolation.md), Phase 3
- [vmod-packager patterns and recipe generation](20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md), Phase 5
- [Step 5 selection](20260728_2127_note_step-5-second-vmod-selection.md), ruling 5

## The three rulings, and what they changed

| Q | Ruling | What it produced |
| --- | --- | --- |
| Q1 evidence schema | Restructure with a schema bump; no legacy blocks, no agreement check; migrate cachetag's data in | `cachetag-target/v2`, below |
| Q2 duplicate pbuilder drivers | Stay separate this wave | Recorded below; merge is refactor-after-proof work |
| Q3 dict's stricter lint | Stays | Recorded below alongside `dh_missing` |

### Q2, recorded: the deliberate duplication

`scripts/ci/vmod/container/build-deb.sh` and `scripts/ci/debian13/container-pbuilder.sh` now duplicate the pbuilder configuration, the apt-resolver setting, the `D05update` hook and the local-repository publication; `build-rpm.sh` and `container-mock.sh` duplicate the Mock configuration and the `createrepo_c` step. Both duplications are commented as deliberate at the point of duplication.

They stay because this wave's equivalence contract is that cachetag's package bytes do not move, and merging would mean editing the exact code paths that produce them. Keeping the lanes apart turns that from a reasoned argument about scopes and branches into an empty diff. The merge is right eventually and belongs in a change whose only purpose is the merge, so the resulting package-byte diff has one cause and one thing to attribute it to.

### Q3, recorded: the gate asymmetries

Two places where dict is held to a *stricter* standard than cachetag:

- **lint.** dict's lanes run `lintian --fail-on error,warning` and `rpmlint` with the exit status propagated; cachetag runs lintian under a hard gate but with its reviewed override files, and rpmlint under a waiver file.
- **`dh_missing`.** Neither family runs `dh_missing --fail-missing` (the Wave A1 decision), but dict additionally carries a payload allowlist in `verify-deb.sh` that rejects anything installed outside the declared object, manual page and documentation.

The recipe-generation plan's "gates identical in strength regardless of recipe strategy" exists to stop a generated-recipe VMOD sneaking through *weaker* gates. Stricter is compliant with that intent, not a violation of it. Raising cachetag to match is future hardening: it should land for both families in one change, so any package-byte movement has a single cause. Recorded here so the difference is a decision with a date rather than something nobody noticed.

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
| ~~`manifest`~~ | ~~every VMOD~~ | **Superseded 2026-07-28 by ruling R-B; see below** |
| `engine_build`, `suppress_engine_artifact` | neither | they act in the caller's engine matrix |

The expansion carries per-row `inject_build`, `inject_recipe` and `suppress_result` booleans, so the workflow reads one flag instead of comparing ids and families in a YAML expression that could drift from the table.

#### Superseded: "`manifest` is deliberately global" (corrected 2026-07-28, later the same day)

**What this note said:** the `manifest` injection acts on every VMOD, deliberately, "because that case is about the ledger and not about isolation between VMODs."

**Why it was believed:** with one VMOD the distinction did not exist, and the case was written to prove that a manifest which does not parse produces one classified row rather than a set of invented lane rows. Corrupting every manifest tested that.

**What Wave A3 found, and ruling R-B:** the reasoning does not survive a second VMOD. `valid_manifests()` claims that a broken manifest costs its own invocation *and the engine rows nothing else consumes, and nothing more* — and that claim is untestable while every manifest is corrupted, because there is no surviving VMOD to observe surviving. The scoped two-VMOD case **is** the Phase 3 isolation demonstration this case was supposed to be.

`INJECTION_TARGET_VMOD["manifest"]` is now `"cachetag"`. Four jobs rebuild the expected ledger from their own fresh checkout — the reusable workflow's `plan` and `summary`, and `ci.yml`'s `discover-engines` and `collect` — and all four now read the scope from `ci_matrix.py injection-scope` rather than each hardcoding a comparison, because corrupting different files would build different ledgers and the run would report rows nobody asked for. A self-test builds the scoped ledger and asserts the result: cachetag collapses to one invalid invocation row, all four dict rows survive, and only the two engine rows dict still asks for remain expected.

`recipe_generation` corrupts the recipe **after** rendering, on purpose. The generator already refuses every token it can refuse, and `tools/vmod_recipe_selftest.py` covers that; this case proves the *lane* refuses a recipe that a build would otherwise consume literally, which is a different property.

### 6. Per-VMOD evidence is first-class: `cachetag-target/v2`

The blocking design question from Wave A1, now decided and implemented.

**The shape.** Target and buildroot facts stay at the top level — `target`, `vinyl_packages`, `buildroot` (image ref, image digest, compiler) and `install` — because every VMOD built for this cohort and target is built in the same container with the same compiler against the same engine packages and installs into the same directory. Recording any of those twice would create two copies that can disagree with no meaning attached to the disagreement. Everything per-VMOD — package names, package revision, upstream version, configure line, flags, `SOURCE_DATE_EPOCH`, hardening check, resolved build dependencies, artifacts, test results — moves into a `vmods:` map keyed by catalog id.

**No legacy shape, no agreement check.** cachetag's data was migrated into `vmods.cachetag` verbatim and the v1 blocks were deleted from the schema. The migration script rewrote the files textually rather than round-tripping them through a serialiser, so every comment survived — those comments are the evidence trail this registry exists to keep, and a serialiser would have dropped all of them silently.

**What the validator now enforces**, which is the whole point:

- every VMOD whose catalog lanes build a package for this cohort's engine and this target **has an entry** — otherwise "the release is complete" could be true with a required VMOD's results simply absent;
- **no entry exists** for a VMOD no lane builds here — that is either stale evidence or a lane somebody forgot to declare;
- `--require-releasable` holds **every** entry to the same policy, in a loop that does not know which VMOD is which and therefore cannot hold one to a weaker standard.

That is the Step 6 exit gate — "both package families meet the same evidence policy as cachetag" — expressed as a mechanical check.

**`pending` is a first-class state.** dict's entries record `evidence: pending` with a reason in words. Placeholders inside a pending entry are exempt from the placeholder policy, because a build that has not happened has no configure line to record; that exemption is safe precisely because `--require-releasable` rejects `pending` **by name**.

**Consequence, deliberate and worth stating loudly: `validate --require-releasable` is RED until Wave B.** The release cohort now carries a required VMOD whose evidence does not exist, so it is not releasable, and `release-draft.yml`'s hard gate will say so. That is the gate working. `validate` (schema mode) is green.

**Cohort manifests gained `engine`.** `registry/README.md` had listed an explicit track field under "deliberately not here yet" with the condition that it earns its validation rules "when a policy decision has to read it mechanically". This is that decision: the evidence map must contain exactly the VMODs whose lanes name this cohort's engine input. Deriving it from `vinyl.version` does not work — the trunk cohorts record a bare `9.0.0`, not a `~git` snapshot version — and inferring it from the shape of `source_url` would be a guess where a statement is available.

**`package.upstream_version` is per-VMOD.** cachetag builds 1.0.1 into this cohort and dict builds 1.7 into the same one. v1 could read the cohort's `cachetag.version` because cachetag was the only VMOD; with two, that field is one VMOD's version and nothing else. The validator cross-checks cachetag's recorded value against it so the two cannot drift.

#### Byte-neutrality evidence

`release_tool.py metadata` was captured for all six cohort/target pairs plus distro-native, in both `json` and `shell` formats, from the pre-migration branch tip (`8bf584f`) and again after. **Every existing value is byte-identical.** The complete diff, across all 13 files, is one added key:

```diff
   }
+  },
+  "vmod": "cachetag"
```

```diff
 CACHETAG_PACKAGE_FORMAT='deb'
+CACHETAG_VMOD='cachetag'
```

It is additive and names which entry was read. Every field `scripts/ci/release-manifest.sh` consumes — `CACHETAG_VERSION`, `CACHETAG_SOURCE_ARCHIVE`, `CACHETAG_ARTIFACTS_NATIVE_FILENAME`, `CACHETAG_ARTIFACTS_RELEASE_ASSET_FILENAME`, `CACHETAG_ABI_*` — is unchanged. A self-test asserts the invariant per cohort and target so it cannot silently regress.

### 7. The dict lane is complete on both targets

`scripts/ci/vmod/`, a lane of its own. See the byte-neutrality argument below for why it is separate.

| Stage | Script | What it does |
| --- | --- | --- |
| source | `source.sh` | download, assert sha256, `git ls-remote` tag-peel, `AC_INIT` cross-check |
| generate | `generate.sh` | render the recipe, refuse surviving tokens, require the generation record, lay out the source tree, stage the verify scripts and VTCs |
| build (deb) | `container/build-deb.sh` | pbuilder in the pinned `debian:trixie`, engine `.debs` as a local repository |
| build (rpm) | `container/build-rpm.sh` | Mock in the pinned `almalinux:9`, engine RPMs through `createrepo_c` |
| verify (deb) | `container/verify-deb.sh` | payload allowlist, ABI/cohort `Depends`, hardening, lintian, runtime-pair smoke, VTC suite |
| verify (rpm) | `container/verify-rpm.sh` | the same in RPM's vocabulary, plus an assertion that the plugin advertises no soname provide |
| driver | `run.sh` | one host entry point for all four containerised stages |

The verify stages mount **only** the lane directory — no repository checkout — because a container that has never seen the build tree is the whole point. That is why the generate stage stages the verification scripts and the ported VTCs into the lane.

EL9's Mock configuration sets `%source_date_epoch_from_changelog 0` and `%clamp_mtime_to_source_date_epoch 1`, because on EL9 an exported `SOURCE_DATE_EPOCH` does not otherwise reach the build — the same lesson `recipes/el9/container/build.sh` already records.

### 8. The host-safe half of the dict lane

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

Nothing from the Wave A2 brief. The workflow wiring, the one item outstanding when this note was first written, landed in Wave A3.

## Verification run

| Command | Result |
| --- | --- |
| `release_tool.py validate` | OK, 10 manifests |
| `release_tool.py validate --require-releasable` | **RED, deliberately** — dict's evidence is `pending` on both release targets. See the evidence-schema section. |
| `release_tool.py --no-cachetag-cross-check validate` | OK |
| `release_tool.py selftest` | **138/138** (was 112; 26 added) |
| `ci_matrix.py selftest` | **179/179** (was 151; 28 added), then 125/125 for the generator |
| `vmod_recipe.py selftest` | **125/125** |
| `ci_matrix.py check-catalog` | OK, 2 VMODs |
| `ci_matrix.py ledger --tier ci` | **14 selected rows**, asserted exactly by a self-test; dict adds **no** engine row |
| containerized `actionlint` (`rhysd/actionlint`), all 5 workflow files | clean, exit 0 |
| containerized `shellcheck` (`koalaman/shellcheck:stable`), all 9 new scripts | clean, exit 0 |
| `generate.sh` dry run, both targets | renders, refuses tokens, lays out the tree, stages scripts and VTCs |
| `generate.sh --inject-token` | refused, non-zero |
| `metadata` byte-neutrality, 13 files, pre- vs post-migration | one added key, everything else identical |
| `git diff main -- .github/` | empty |
| `git diff main -- recipes/debian-13/ recipes/el9/ scripts/ci/debian13/ scripts/ci/el9/` | empty |

## What Wave B must prove

1. **Baseline both-VMOD run.** All 14 ledger rows green on the `ci` tier: four engine rows, cachetag's six, dict's four.
2. **Two-way isolation.** `inject=debian_build` and `inject=source_checkout` fail cachetag rows while all four dict rows complete; `inject=dict_build` and `inject=dict_source` fail dict rows while all six cachetag rows complete. The tool-level property is already asserted by self-tests; Wave B proves it in a real graph.
3. **`recipe_generation`.** `inject=recipe_generation` classifies exactly one dict row as `failed_recipe_generation`, and no other row.
4. **Equivalence for cachetag against `main`.** Debian package digests byte-identical excluding `.buildinfo` and `.changes`; EL9 packages pass the normalized semantic comparison from the Step 3 contract. This wave changed no file cachetag builds from, so any difference is a finding about the workflow.
5. **dict evidence populated.** The two `vmods.dict` entries move from `pending` to `recorded` with real configure lines, flags, epoch, resolved build dependencies, artifact digests and test results — after which `validate --require-releasable` goes green again, which is the gate closing.
6. **Behaviour suites green on installed packages, both VMODs, both targets.** For dict specifically: `dict_cs.vtc` and `dict_ci.vtc` passing against the packaged `.so` resolved through `-p vmod_path`, with `num.dict` extracted from the digest-verified release archive, and upstream's expected values unmodified.
7. **Payload and lint gates fire.** The dict payload allowlist and the strict lintian/rpmlint expectations are new and have never run; a first run that passes them is itself evidence, and a first run that fails them is a finding about the templates or the overlay, never a reason to relax the gate.

## Open questions for the audit

1. **Where does a second VMOD's per-target evidence live?** Carried over from Wave A1 and now the blocking design question for deliverable 5. `registry/targets/<cohort>/<target>.yml` records exactly one VMOD's build evidence, package revision, artifacts and test results, and its schema names `cachetag` in a top-level block. Three shapes are plausible — a `vmods:` list that becomes authoritative for all VMODs with the legacy blocks validated to agree with cachetag's entry; a per-VMOD evidence file under `registry/targets/<cohort>/<target>/<vmod>.yml`; or splitting shared target facts from per-VMOD evidence. The first keeps one file per target and one place to look but needs an agreement check to stop drift; the second is the least invasive to cachetag but multiplies files. The exit-gate clause is "both package families meet the same evidence policy as cachetag" and the schema has to make that *checkable*, so this should be decided rather than guessed.
2. **Should the two `container-pbuilder.sh` implementations be merged, and when?** They now duplicate the pbuilder configuration, the apt-resolver setting, the D hook and the local-repository publication. Merging them is right eventually; doing it in this wave would have put cachetag's package bytes at risk for no gain.
3. **`--fail-on error,warning` for `lintian` on the dict lane is stricter than the cachetag lane's treatment.** That is deliberate — a generated recipe has no excuse for a warning nobody reviewed — but it means the two families are not yet held to identical lint gates, which the exit gate arguably requires. Decide whether to raise cachetag or to record the asymmetry, in the same change that settles the `dh_missing` asymmetry from Wave A1.
