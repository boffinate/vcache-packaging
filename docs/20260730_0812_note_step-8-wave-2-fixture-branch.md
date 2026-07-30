# Step 8 Wave 2: the ten-entry synthetic acceptance fixture

Date: 2026-07-30

Status: **Prepared, not dispatched.** The branch is complete and green on the host-safe battery. Nothing has been run in CI from it, nothing was built, no container was started.

Branch: `step8-fixture`. **Never merged to main.** Originally cut from `step8-transactions-wiring` at `59b0ddb` and rebased onto `main` at `31aacbc` after Waves 3a to 3c landed, so the rig always exercises the graph as it currently is — which is the only way a graph-shape acceptance test is worth anything.

Related:

- [Roadmap step 8](20260728_0916_roadmap_outstanding-packaging-work.md) — "Run the matrix plan's ten-entry synthetic acceptance fixture immediately before migration", and the risk it names: "Treating the ten-entry synthetic fixture as production-package selection would confuse a graph acceptance test with evidence of real package diversity."
- [Failure-isolation plan](20260728_0833_plan_vmod-matrix-failure-isolation.md) — "The strongest acceptance demonstration is a ten-entry fixture in which one VMOD fails at source verification and nine reach their final test stage."
- [Step 8 Wave 1](20260730_0748_note_step-8-wave-1-transactions-wiring.md) — the branch this one sits on top of.

## What the fixture is

Three VMODs are selected. Seven aliases make ten.

Each alias is a **clone of dict**: the same pinned upstream source (`v1.7`, commit `784584d2`, the published `vmod-dict-1.7.tar.gz`), the same reviewed overlay data, a distinct id and distinct package names. Cloning a real, verifiable source is the point. An alias pointing at a stub would demonstrate that a stub builds, and the acceptance criterion is that **nine entries reach their final test stage** — which means nine real package builds, nine real installed-package verifications, and nine real behaviour suites. Nothing weaker demonstrates the graph.

The ids are `fixture1` … `fixture7` and the packages are `vmod-fixture1` … `vmod-fixture7`. Nothing about them looks like a selected package, and `SCOPE.md` is unchanged: the fixture does not expand the production-selected VMOD set, because the branch it lives on never reaches main.

### What differs per alias, and what deliberately does not

Two catalog entries emitting the same package names would collide, so the id-bearing values differ: `id`, `revision`, `upstream.name`, `package.debian_source_name` / `debian_binary_name` / `rpm_name`, `package.revision`, and the lintian override's package prefix — lintian reports `mismatched-override` when the prefix is not the package the tag was emitted for, which the dict overlay's own comment already warned about.

Everything that names the **bytes** rather than the package stays vmod-dict's:

- `source.archive.stem` stays `vmod-dict`, because the published tarball really is `vmod-dict-1.7.tar.gz` unpacking to `vmod-dict-1.7/`, and the RPM spec's `%autosetup -n` is rendered from it. The Debian source name is a separate field, and `generate.sh` renames the unpacked tree to match it.
- `payload.vmod_object` stays `libvmod_dict.so` and the man page stays `vmod_dict.3`, because the built payload is vmod-dict's.
- `summary` and `description` stay word for word, so the reviewed rpmlint spelling waivers still cover exactly the text they were measured against. A reworded description could fail lint on seven rows and defeat the acceptance run for a reason with nothing to do with the graph.

**The consequence of that split has a production precedent, which is why it is safe.** An alias's upstream autoconf tarname (`vmod-dict`) differs from its binary package name (`vmod-fixture1`), so `dist_doc_DATA` stages `/usr/share/doc/vmod-dict/` alongside dh's `/usr/share/doc/vmod-fixture1/`. That is exactly the shape `libvmod-redis` already has in production — upstream's `AC_INIT` tarname is `vmod-redis`, its package is `libvmod-redis` — and its Debian row is green. The RPM template removes the whole docdir in `%install` for the same reason, and `pc_assert_deb_payload`'s allowlist excludes everything under `/usr/share/doc/` regardless of directory name.

### The failing entry

**`fixture4` carries a deliberately wrong `archive_sha256`.** One hex digit moved from the real digest (`eb2a86a7…` → `fb2a86a7…`), still a well-formed SHA-256, so every structural check passes and only the lane's byte comparison can catch it.

It is a checked-in fact rather than an injection. The injection machinery exists to prove isolation without editing a build script; this case has to show the **ordinary** digest gate refusing **ordinary** wrong bytes, which is what an upstream archive silently changing would look like.

Expected classified shape for `fixture4`:

| Row | Status | Where it comes from |
| --- | --- | --- |
| `source/fixture4/release` | `failed_source_digest` | `source.sh` step 2 dies with "does not match the pinned"; `vmod-package.yml`'s `source-generated` job greps for exactly that phrase |
| `target/fixture4/release/vinyl-release/debian-13-amd64` | `blocked_by_vmod_source` | the source artifact was never published, so `steps.download.outcome != 'success'` |
| `target/fixture4/release/vinyl-release/el9-x86_64` | `blocked_by_vmod_source` | the same |

`fixture4` is `required: true`, so the run finishes **red** — which is the criterion, not a problem. Nothing about it may cancel the other nine entries.

## The ledger

`python3 tools/ci_matrix.py ledger --tier ci` — **47 rows, 46 selected, 4 engine rows**:

```text
engine          engine/vinyl-release/debian-13-amd64
engine          engine/vinyl-release/el9-x86_64
engine          engine/vinyl-trunk-pinned/debian-13-amd64
engine          engine/vinyl-trunk-pinned/el9-x86_64
invocation      vmod/cachetag
source          source/cachetag/release
package-target  target/cachetag/release/vinyl-release/debian-13-amd64
package-target  target/cachetag/release/vinyl-release/el9-x86_64
package-target  target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64
package-target  target/cachetag/release/vinyl-trunk-pinned/el9-x86_64
source-harness  harness/cachetag/trunk/vinyl-trunk-head          (not selected)
invocation      vmod/dict
source          source/dict/release
package-target  target/dict/release/vinyl-release/debian-13-amd64
package-target  target/dict/release/vinyl-release/el9-x86_64
invocation      vmod/fixture1        … source/fixture1/release + 2 package-target rows
invocation      vmod/fixture2        … source/fixture2/release + 2 package-target rows
invocation      vmod/fixture3        … source/fixture3/release + 2 package-target rows
invocation      vmod/fixture4        … source/fixture4/release + 2 package-target rows
invocation      vmod/fixture5        … source/fixture5/release + 2 package-target rows
invocation      vmod/fixture6        … source/fixture6/release + 2 package-target rows
invocation      vmod/fixture7        … source/fixture7/release + 2 package-target rows
invocation      vmod/redis
source          source/redis/release
package-target  target/redis/release/vinyl-release/debian-13-amd64
package-target  target/redis/release/vinyl-release/el9-x86_64
```

**Four engine rows, not eleven.** Every alias lane names `vinyl-release` on both targets, and `engine_rows` derives one row per `(engine, target)` pair that at least one selected lane consumes. Seven more consumers of `vinyl-release` add no engine work at all; the two `vinyl-trunk-pinned` rows still have exactly one consumer each, which is cachetag. That is the shared-engine property being demonstrated rather than asserted.

## What the dispatch must show

1. **`fixture4`'s source row is `failed_source_digest`**, and its two target rows are `blocked_by_vmod_source`. Not a generic archive failure, not a cancellation.
2. **Nine entries reach their final test stage.** cachetag's four package rows through checksums, and dict, redis and the six healthy aliases through the installed-package verification and behaviour suite. Twenty of the twenty-two package rows green.
3. **The collector reconciles all ten invocations.** Forty-six expected rows, forty-six outcomes, no `missing_result_record`.
4. **The run is red, and nothing was cancelled.** No entry may show `cancelled`; the failure is `fixture4`'s and stays `fixture4`'s.
5. **The transactions steps stayed inert.** This branch carries Wave 1, and the dispatch is at tier `ci`, so no transaction step may have run on any row.

`ci.yml`'s VMOD matrix is `max-parallel: 4`, a cost control rather than a failure control. Ten entries therefore queue in three waves and the run is roughly two and a half times the length of a three-entry run. That is expected; it is not evidence of anything and the value was deliberately not changed for the fixture.

## Verification performed on the host

| Check | Result |
| --- | --- |
| `python3 tools/ci_matrix.py check-catalog` | **10 VMOD manifests discovered** |
| `python3 tools/release_tool.py validate` | 10 manifests valid |
| `python3 tools/release_tool.py validate --require-releasable` | **fails, 98 errors** — see below |
| `python3 tools/ci_matrix.py selftest` | 262/262, and `vmod_recipe` 225/225 |
| `python3 tools/release_tool.py selftest` | 160/160 |
| `validate-vmod` on each of the seven aliases | all valid, including `fixture4` |
| `vmod_recipe.py names` + `generate`, `fixture1`, both targets | rendered, no unsubstituted token |
| dict's `recipe_sha256`, regenerated | `6f637e4b…`, unchanged from the recorded evidence |

**`--require-releasable` fails, and that is the gate working.** Fourteen `pending` entries — seven aliases on two targets — make `vinyl-9.0.1-ac4f719c16f4` unreleasable, and the errors name each one. `pending` is the honest state for a lane that has never run and plain `validate` accepts it; a release cannot proceed while it exists. This is precisely why the branch is never merged, and it is a second, independent mechanism preventing the fixture from reaching a release: even if somebody merged it, no cohort carrying it could be published.

### The generator proof

`fixture1` renders with the alias identity everywhere identity belongs and with vmod-dict's identity everywhere bytes belong:

```text
Name:      vmod-fixture1                       # RPM
Source0:   …/vmod-dict-1.7.tar.gz              # the real archive
%autosetup -n vmod-dict-1.7                    # the real unpack directory
%{vinyl_vmoddir}/libvmod_dict.so               # the real payload

Source: vmod-fixture1                          # debian/control
vmod-fixture1 (1.7-1) trixie; urgency=medium   # debian/changelog
vmod-fixture1: groff-message *cannot select font 'C'* [usr/share/man/man3/vmod_dict.3.gz:*]
vmod-fixture1_1.7.orig.tar.gz / _1.7-1.dsc     # source package filenames
```

`recipe_sha256` for `fixture1` is `1bdb00e3ca22044247562ead3cbad90c79dae5e40dd2e98df103a40ae3bc4cd2` (Debian) and `95f78c8c81cfd4d2b4ea60f1bd63ead62b61569cf8cdaaf59d2bd8c680723303` (EL9). dict's own Debian recipe still digests to `6f637e4bc4f09968b4e1662f30773de866d2df2698f8c97889480bd29f3be1e1`, the value its recorded evidence names — so the fixture perturbed nothing about the three selected VMODs.

## Two things that contradicted the design, and what was done

### A VMOD id cannot contain a hyphen, and two regexes disagree about it

The first attempt used `fixture-a` … `fixture-g`. `check-catalog` accepted them and `validate` then failed on **both** target manifests in the release cohort with `expected 'key: value' or 'key:', got 'fixture-a:'`.

The catalog's id space is `^[a-z][a-z0-9-]*$` — hyphens allowed, in `ci_matrix.ID_RE`, in `MANIFEST_NAME_RE` and in the target evidence map's own `key_pattern`. But the restricted parser's `KEY_RE` is `^[a-z][a-z0-9_]*$` — underscores allowed, hyphens not — and the evidence map is keyed by VMOD id. **The usable id space is therefore the intersection, `^[a-z][a-z0-9]*$`**, and it is narrower than any single regex in the tree states. A hyphenated VMOD id would pass `check-catalog` and then make every target manifest in its cohort unparseable.

Nobody has hit it because `cachetag`, `dict` and `redis` are all alphanumeric. The fixture was renamed to `fixture1` … `fixture7` rather than widening `KEY_RE` or narrowing `ID_RE`: that is a production tooling decision, it is not what this branch is for, and a throwaway fixture is the worst possible reason to make it. **It is left open for the maintainer.**

### The other tiers' ledgers are not literally unchanged

The aliases claim tier `ci` only, and no alias **lane** row is selected at `nightly`, `release` or `trunk` — verified in all three. But each alias still contributes one **invocation** row that is selected, because `vmod_rows` marks the invocation row selected unconditionally, for every VMOD, at every tier.

That is not something the fixture chose. It is how the ledger already works: `vmod/dict` is a selected invocation row in the trunk ledger today even though dict has no trunk lane. The invocation row means "this manifest exists and must validate", which is true at every tier.

So at `nightly`, `release` and `trunk` the fixture adds exactly seven selected rows and zero unselected-to-selected transitions:

```text
selected added  : vmod/fixture1 … vmod/fixture7
selected removed: (none)
```

Every source and package-target row of every alias is present-but-unselected at those tiers, which is the `not_selected` half of the vocabulary doing its job. No release ledger gains a package row for a package nobody selected — which is the property that actually mattered.

## Rebased onto post-3c main, 2026-07-30

Rebased from `59b0ddb` onto `31aacbc`. One conflict, in `tools/ci_matrix_selftest.py`: the fixture's `_without_fixture` shim against the tier rename, where `test_nightly_is_the_transaction_tier` became `test_transactions_is_the_transaction_tier` and gained two assertions the older test did not have. Resolved by taking main's test wholesale and re-applying the subtraction to its row-key lists — the fixture must never win an argument with the production assertion, it may only exclude its own ids from it.

Three more assertions needed the same treatment, all of them new since the branch was cut and all of them deriving from the catalog rather than from a list:

- `harness: the trunk tier selects three invocations and one harness row` (Wave 3c);
- `tools/upstream_watch_selftest.py`'s watch-list, moved-pin and pinned-commit assertions (Wave 3b), which is a whole module the branch had never seen.

One of those is worth recording as behaviour rather than as a fix. The aliases clone **the same URL as vmod-dict**, so `upstream_watch` reports a moved dict tag as eight moved pins, not one. That is correct: a shared upstream moves every entry pinned to it at once, and it is the first demonstration that the watcher's fail-loud path scales past one entry per remote.

The aliases' `tiers` lists were re-verified after the rebase and are still `["ci"]` alone. They did **not** acquire the renamed `transactions` tier, which is the property that keeps every non-`ci` ledger free of fixture package rows.

## The branch is never merged

Three independent mechanisms would have to be defeated for the fixture to reach a release:

1. `SCOPE.md` selects three VMODs and is unchanged. Adding a package is an explicit maintainer decision.
2. `--require-releasable` refuses the release cohort while the fourteen `pending` entries exist.
3. `tools/ci_matrix_selftest.py`, `tools/selftest.py` and `tools/upstream_watch_selftest.py` each carry a **branch-local block** that subtracts the fixture ids from the assertions pinned to the production catalog. Each is marked NEVER MERGE, and each names seven ids that exist nowhere on main, so none can survive a merge quietly.

That block is load-bearing rather than cosmetic. `ci.yml`'s `structural-validation` job runs both selftests and `discover-vmods` needs it; without the subtraction those assertions fail — correctly, since the catalog is not the production catalog — the matrix never starts, and the acceptance run this branch exists for never happens. The assertions are not weakened: each still asserts the same property about the three selected VMODs.

When the acceptance run is recorded, the branch is deleted. Its evidence is the run id and this note.
