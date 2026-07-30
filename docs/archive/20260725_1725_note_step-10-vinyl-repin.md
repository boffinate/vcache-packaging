# Step 10: re-pin the packaged Vinyl source to a published upstream commit

Maintainer-approved 2026-07-25. Both packaging lanes now build from upstream `25761f8505817ac50df994270bfe75b60073e33e` instead of `a90954814766d933a75d4c808c449cb9bc0ae3d3`.

## Why

`docs/20260725_1655_note_step-10-ci-first-run-findings.md` recorded the blocker: `a909548147` exists on exactly one workstation. It is the tip of the local-only branch `perf/tag-vmod-baseline`, 17 commits ahead of `origin/main`, and a full anonymous clone of `code.vinyl-cache.org` does not contain the object at all. CI can never fetch it.

The second reason is the more important one, and it is not about CI. Those 17 additive commits are benchmark scaffolding, and they include an **in-tree `vmod_tag`** — a benchmark *subject*, not part of Vinyl:

```
doc/sphinx/reference/vmod_tag.rst
vmod/vmod_tag.c
vmod/vmod_tag.vcc
vmod/vmod_tag_index.c
vmod/vmod_tag_index.h
```

Every package the lanes had produced up to this point therefore shipped an experimental VMOD and its documentation inside a distribution package carrying the upstream name and the upstream ABI token. Packages must ship pure upstream Vinyl. That is the point of this change; the CI fetch failure is what made it visible.

The new pin is the merge-base of `perf/tag-vmod-baseline` with upstream `main` (`git merge-base` confirms `25761f8505` is an ancestor of `a909548147`), so nothing is invented: it is the last commit the benchmark branch and published upstream agree on.

## Every value that moved

| Where | Value | Old | New |
| --- | --- | --- | --- |
| `recipes/debian-13/build.sh` | `VINYL_GIT_COMMIT` | `a90954814766d933a75d4c808c449cb9bc0ae3d3` | `25761f8505817ac50df994270bfe75b60073e33e` |
| `recipes/debian-13/build.sh` | `VINYL_STRICT_ABI` | (derived from commit) | (derived from commit) |
| `recipes/debian-13/build.sh` | `VINYL_ABI_STRING` | `Vinyl Cache trunk a909548147…` | `Vinyl Cache trunk 25761f8505…` (derived) |
| `recipes/debian-13/build.sh` | `VINYL_SOURCE_SHA256` | `2587f03289b3e16d36b4b688def4b78fb5af07a9aacc620a55e094a5c0f6ee15` | `27568cc1cdf914b3a328fc633d90137b62134fc7d375ca16010656a26d53f507` |
| `recipes/debian-13/build.sh` | `VINYL_UPSTREAM_VERSION` | `9.0.0~git20260613.a909548147` | `9.0.0~git20260520.25761f8505` |
| `recipes/debian-13/build.sh` | `VINYL_SOURCE_DATE_EPOCH` | `1781307021` | `1779265093` |
| `recipes/debian-13/mismatch-fixture.sh` | `BASE_ABI`, `BASE_VERSION`, `FIXTURE_SOURCE_DATE_EPOCH` | `a909548147…`, `9.0.0~git20260613.a909548147-1`, `1781307021` | `25761f8505…`, `9.0.0~git20260520.25761f8505-1`, `1779265093` |
| `recipes/debian-13/transactions.sh` | `BASE_ABI`, `BASE_VERSION` | as above | as above |
| `recipes/el9/cohort.env` | `VINYL_GIT_COMMIT`, `VINYL_STRICT_ABI` | `a90954814766d933a75d4c808c449cb9bc0ae3d3` | `25761f8505817ac50df994270bfe75b60073e33e` |
| `recipes/el9/cohort.env` | `VINYL_VERSION` | `9.0.0~git20260613.a909548147` | `9.0.0~git20260520.25761f8505` |
| `recipes/el9/cohort.env` | `VINYL_SOURCE_DATE_EPOCH` | `1781652621` | `1779265093` |
| `scripts/ci/debian13/pinned.sh` | `VINYL_GIT_COMMIT`, `VINYL_UPSTREAM_VERSION`, `VINYL_SOURCE_DATE_EPOCH` | as above | as above |
| `scripts/ci/release-manifest.sh` | `vinyl_commit`, `vinyl_upstream_version` | as above | as above |
| `.github/workflows/{ci,nightly-transactions,release-draft}.yml` | `VINYL_GIT_COMMIT` | `a909548147…` | `25761f8505…` |
| `recipes/el9/README.md`, `recipes/el9/find-provides` | doc/comment examples | `a909548147…`, `9.0.0~git20260613.a909548147` | new values |
| `../libvmod-cachetag/packaging/README.md` | token-table `@VINYL_PACKAGE_VERSION@` / `@VINYL_STRICT_ABI@` examples | as above | as above |

Deliberately **not** moved:

- `VTEST2_GIT_COMMIT` / `VINYL_VTEST2_COMMIT` = `db5ccb4a078da40b3ec1ca3c18bf498bb1520888`. The `bin/vinyltest/vtest2` gitlink is byte-identical at both commits; verified with `git ls-tree` on each.
- `CACHETAG_SOURCE_SHA256` = `c7054e69219ff3c54501d9c68857f2117944c4658db4cb08e2821b09b27821a2`. The cachetag archive does not embed the Vinyl commit, and both lanes re-asserted the pinned digest against the on-disk archive and passed.
- `registry/` manifests. Every checked-in manifest is still a `template` carrying reserved placeholder identity (`000000000000`, forty zeroes for `strict_abi`). No registry manifest ever held the real commit, so there was nothing to re-pin. `validate` and `selftest` stay green.
- `tools/selftest.py`. It embeds `a909548147…` four times, but as a **self-consistent synthetic test vector** alongside `'1' * 40` and `'2' * 40` — input and expected output in the same file, not a lane pin. Left alone deliberately; the string is a grep false positive for anyone auditing this re-pin.
- Dated notes under `docs/` and `../libvmod-cachetag/docs/`. History stays true.

## A stale value the re-pin exposed

`recipes/el9/cohort.env` carried `VINYL_SOURCE_DATE_EPOCH=1781652621` under a comment reading "Commit author date of VINYL_GIT_COMMIT, 2026-06-13T00:30:21+01:00". That epoch is **2026-06-16T23:30:21Z** — four days off, and it disagreed with the Debian lane's `1781307021`, which *was* the old commit's author date. Nothing caught it because the two lanes never compare, and the value only ever fed changelog dates and `SOURCE_DATE_EPOCH`.

The old commit's author and committer dates were identical (`1781307021` both), so "author vs committer" was undecidable from the old value. The new commit's are not: author `1779265093` (2026-05-20T10:18:13+02:00), committer `1779285492` (2026-05-20T15:58:12+02:00). Both lanes now use the **author** date, `1779265093`, because the author date survives a rebase and because it is what the Debian lane's previous value actually was. Both comments now state the rule and each other's existence.

The version-string date is unaffected by the choice: both dates fall on 2026-05-20 in the commit's own timezone, so the snapshot is `git20260520` either way.

## Version ordering: the new snapshot sorts LOWER

The re-pin moves *backwards* in time (2026-06-13 → 2026-05-20), so `9.0.0~git20260520.25761f8505` sorts **below** `9.0.0~git20260613.a909548147`. This bit during the first full Debian run: `dist/debian-13/` still held the previous run's debs, the smoke stage's local apt repository offered both, and apt correctly installed the *old* higher-versioned runtime. The cachetag package had been built against the new ABI, so VCL compilation failed and 11 of 19 smoke steps failed. Clearing `dist/debian-13/` and re-running gave 19/19.

That is not a lane bug — apt did exactly the right thing — but two things follow:

- **Any re-pin that moves backwards in time needs a clean `dist/`.** A stale-artifact check in the smoke stage would turn this from a confusing failure into a named one. Not added here; recorded as a candidate.
- There is no upgrade path from the previously built process-proof packages to these. Irrelevant today (no users, and the cohort identity is still `unassigned-local-process-proof`), but a real release must not re-pin backwards without a package-revision bump.

The synthetic fixture ordering still holds, since the fixtures were always dated ahead of the baseline: `9.0.0~git20260520.25761f8505-1` < `9.0.0~git20260614.ffffffffffff-1` < `9.0.0~git20260615.eeeeeeeeeeee-1`.

## Permanent assertion added to both lanes

The absence of `vmod_tag` is the point of this change, so it is now asserted rather than merely observed.

- `recipes/debian-13/container/stage-vinyl.sh`: after the existing Provides/Depends assertions, `dpkg-deb -c` over the runtime and dev debs must yield no path matching `vmod_tag|libvmod_tag`. Exits non-zero if any appears.
- `recipes/el9/smoke/smoke.sh`: alongside the cohort-capability negative control, `rpm -ql vinyl-cache` must yield no such path.

## Evidence

Registry tooling (host-safe, stdlib only):

```
python3 tools/release_tool.py selftest   -> # TOTAL: 94  # PASS: 94  # FAIL: 0
python3 tools/release_tool.py validate   -> OK: 4 manifest(s) valid (schema mode), cachetag version 1.0.0
```

Debian 13 lane, `recipes/debian-13/build.sh`, full run on a cleaned `dist/`:

```
canonical Vinyl source archive sha256: 27568cc1cdf914b3a328fc633d90137b62134fc7d375ca16010656a26d53f507
OK: canonical cachetag archive digest matches the pinned value
PACKAGE_STRING from configure.ac: [Vinyl Cache trunk]
generated VMOD_ABI_Version: [Vinyl Cache trunk 25761f8505817ac50df994270bfe75b60073e33e]
OK: generated strict VMOD ABI string matches the pinned value
Provides: vinyld-abi-25761f8505817ac50df994270bfe75b60073e33e, vinyld-cohort-unassigned-local-process-proof, vinyld-vrt (= 23.0)
--- upstream purity: no benchmark-scaffolding vmod_tag ---
OK: no vmod_tag file in the runtime or dev package
Depends: libc6 (>= 2.38), vinyl-cache (>= 9.0.0~git20260520.25761f8505), vinyld-abi-25761f8505817ac50df994270bfe75b60073e33e, vinyld-vrt (= 23.0), vinyld-cohort-unassigned-local-process-proof
===== SMOKE SUMMARY: 19 passed, 0 failed =====
```

EL9 lane, `recipes/el9/build.sh`, full run on a cleaned `dist/`:

```
vinyld(abi)(aarch-64) = 25761f8505817ac50df994270bfe75b60073e33e
vinyld(cohort-vinyl-9.0.0-000000000000)(aarch-64)
vinyld(vrt)(aarch-64) = 23.0                      [cachetag Requires]
PASS: the runtime package ships no vmod_tag file (pure upstream Vinyl)
===== SMOKE RESULT ===== ALL STEPS PASSED
```

Cross-lane agreement on the source archive: the EL9 `SOURCES/vinyl-cache-9.0.0~git20260520.25761f8505.tar.gz` digest is `34cd1e75906606bf4dad5e1fd020be7c075583fc5d8510deb6c7386a9eb2477b`, identical to the Debian `.orig.tar.gz`.

Independent package inspection, outside either lane's own assertions:

```
# Debian: runtime + dev debs
docker run --rm -v .../dist/debian-13:/p:ro debian:trixie \
  bash -c 'for f in /p/vinyl-cache_9*.deb /p/vinyl-cache-dev_*.deb; do dpkg-deb -c "$f"; done \
           | grep -Ec "vmod_tag|libvmod_tag"'   ->  0

# EL9: runtime + devel rpms
docker run --rm -v .../dist/el9/packages:/p:ro almalinux:9 \
  bash -c 'for f in /p/vinyl-cache-9*.rpm /p/vinyl-cache-devel*.rpm; do rpm -qlp "$f"; done \
           | grep -Ec "vmod_tag|libvmod_tag"'   ->  0
```

## Left undone

- `../libvmod-cachetag/release/dist/libvmod-cachetag-1.0.0.metadata.json` still records `"git_commit": "a909548147…"` and the old `source_sha256` in its `vinyl_input` block. That file is a gitignored build artifact, and the field is provenance for the Vinyl prefix the VMOD was *test-built* against, not an input to the cachetag archive — `CACHETAG_SOURCE_SHA256` is unchanged and both lanes re-verified it. Regenerating it means re-running the cachetag release-source lane, which is a separate job; `recipes/debian-13/build.sh` now says so in a comment where it used to cite that file as the digest's source.
- No stale-artifact guard added to the smoke stages (see the version-ordering section).
- CI has not been re-run against the new pin. The blocker it reported should now be gone — `25761f8505` is on `code.vinyl-cache.org` `main` — but that is unverified until the workflow runs.
