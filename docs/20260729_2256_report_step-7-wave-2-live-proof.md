# Step 7 Wave 2: the live proof, and what the measurements corrected

Wave 1 ended with eight things only a live run could prove (docs/20260729_1240_note_step-7-wave-1-redis-exception.md, "What Wave 2 must prove live"). This is the record of proving them. All eight hold. Getting there took two failed dispatches, each of which found a real defect the local containers could not have found — which is the reason Wave 2 exists — and the wave closed two evidence gaps that only became visible when the recording was actually attempted.

## The run inventory

| Run | Dispatch | Outcome | What it established |
| --- | --- | --- | --- |
| 30452256824 | inject=none | red | the docdir defect: rpmbuild's unpackaged-file check on `dist_doc_DATA` |
| 30490103610 | inject=none | red | the RPM builds; the rpmlint measurement, which falsified Wave 1's filter; lintian passes on the Debian row |
| 30491861084 | inject=none | **green, reconcile green** | **the evidence run**: every criterion-1/2 fact and every recorded value below |
| 30491866212 | inject=patch_omission | red as designed | case 10's behaviour half |
| 30491871562 | inject=redis_source | red as designed | redis source isolation (`failed_source_archive` — see below) |
| 30491876719 | inject=redis_build | red as designed | redis EL9 isolation, exactly one row |
| 30491881593 | inject=engine_build | red as designed | the engine row blocks three consumers |

An injection run's reconciliation is red by construction — it reconciles against the uninjected expectation, and the value of the demonstration is that the red is exactly the injected shape and nothing else. Each of the four tables above was read row by row against its expectation; the shapes match.

## The three defects the live pass found

**The docdir defect (run 30452256824).** Upstream libvmod-redis declares `dist_doc_DATA`, so `make install` installs LICENSE and README.rst into `$(docdir)` — `/usr/share/doc/vmod-redis`, named after the upstream package — while the packaged documentation is the `%doc`/`%license` set taken from the source tree. rpmbuild's unpackaged-file check is a hard error, and it fired at the last moment of an otherwise clean build. Fixed in the RPM template, not the redis overlay: `rm -rf %{buildroot}%{_docdir}` after `%make_install`, because `dist_doc_DATA` is generic Autotools behaviour and the removal is a no-op for a VMOD that installs no docs. cachetag's lane never sees this template; `git diff main` over the cachetag lanes stayed empty all wave.

**The rpmlint filter matched nothing (run 30490103610).** Wave 1 wrote the `invalid-license` waiver for the full rendered expression on the recorded belief that "rpmlint reports the whole field, not a token from it" — and rejected reusing cachetag's per-token regex on exactly that ground. Measured live, the belief is false: rpmlint 1.11 splits the expression and reports each unrecognised token on its own line, three findings for this package. So the reviewed waiver failed to match the finding it was written to cover, and the row failed on it. The filter now enumerates the three reviewed tokens as an alternation, which keeps the failure direction Wave 1 wanted — a changed licence expression produces a token the regex has never heard of and the row fails for re-review.

**The description tripped the dictionary (same run).** `spelling-error` fired on "failover" and "unresolvable". The overlay's own rule is that those get fixed, not waived: the description now reads "fail-over", and the ABI paragraph says an incompatible upgrade fails to resolve rather than silently loading.

## The measurement rule, resolved

Wave 1 bound the filter to Wave 2's unfiltered informational pass: removed if `invalid-license` did not fire. It fired — once per token. The filter stays, rewritten to the shape the tool actually emits, and the resolution is recorded in the overlay beside the filter. The evidence run's unfiltered pass shows exactly the three covered warnings and nothing else; the gated pass is 0 errors, 0 warnings on both generated RPMs.

## The two evidence gaps the recording attempt exposed

**Fixture-package versions were never captured.** The evidence schema requires `tests.fixture_packages` as name and version from the run; the suite driver installed the declared packages with the package manager's output discarded. `vtc_install_packages` now writes `lane/logs/fixture-packages.tsv` (name, tab, version — the buildroot-packages.tsv register), fatally if it cannot. The driver stays generic and the derived-vocabulary assertion agrees. The recorded values: redis-server and redis-tools 5:8.0.2-3+deb13u2 on Debian, redis 6.2.22-1.el9_8.x86_64 on EL9.

**`recipe_sha256` had nowhere to land.** Case 10's evidence half — the recorded digest is what makes a substituted recipe impossible to hide — was asserted by two documents and satisfiable by neither, because the evidence schema had no field for it. `build.recipe_sha256` now exists: a 64-hex tree digest for a generated VMOD, `not-applicable` for cachetag, whose audited recipe is not generated. The recorded entries in all three cohorts carry it.

## Criterion by criterion

1. **Both redis packages build, both targets, consolidated lane** — run 30491861084, green through Mock and pbuilder.
2. **The full 20-VTC suite against the INSTALLED package** — "VTC-SUITE SUMMARY: 20/20 passed, 0 skipped" on both targets, packaged `.so` via `-p vmod_path`, against live Redis fixture services.
3. **rpmlint** — measured, resolved KEEP, filter rewritten to the measured shape; unfiltered pass carries exactly the three covered token warnings.
4. **lintian** — `--fail-on error,warning` exits 0 on the redis deb, confirmed live twice (30490103610, 30491861084) rather than assumed from the debian:13 spot-check.
5. **dict's revision-2 evidence re-recorded, `--require-releasable` green** — commit b4ce21d; "OK: 10 manifest(s) valid (releasable mode)", zero errors, no VMOD pending, none blamed.
6. **Case 10, both halves** — behaviour: run 30491866212 shows `failed_package_build` on both redis rows while cachetag's six and dict's four pass. Evidence: all four recorded `recipe_sha256` values cross-checked against the artifacts' generation records before recording; the branch renders what the rows built.
7. **The two isolation injections** — 30491871562 (source) and 30491876719 (EL9 build) each kill only redis rows; the other two VMODs' ten rows pass in both.
8. **Ledger 19/18; the engine row blocks three** — 19 rows, 18 selected, locally and in every reconciliation; run 30491881593 shows `engine/vinyl-release/debian-13-amd64` down and exactly three consumers `blocked_by_engine_artifact` — cachetag's, dict's and redis's Debian release rows.

## Recorded evidence, and one deliberate verdict

dict (revision 2) and redis (revision 1) are recorded in both target manifests from run 30491861084 alone; every value's comment names the run and the log or artifact file it came from. Build dependencies are the run's own lists (178/184 entries from the .buildinfo files, 214 from each Mock buildroot TSV); artifact digests are the run's SHA256SUMS, spot-reverified from the downloaded bytes.

`upgrade_transactions` is **not-applicable** on all four new entries, stated in their comments: the generated-VMOD lanes end at the verify container, and nightly-transactions.yml exercises only the engine cohort today. Wiring generated VMODs into the transactions matrix is Step 8 work; recording a verdict from packages that no longer exist — or were never exercised — would be a claim, not a measurement. This is a deliberate ruling for the maintainer to see, not a buried default.

## Corrections to prior records

- The Wave 1 note's digest table (its lines 223–228) is superseded twice over: first by the docdir removal (el9 only), then by the waiver-and-description fix (both redis targets). The values that built the evidence run and are recorded in the registry: redis debian `ca2865ecdfb94b1b4d56c28fb0063b35c9d009d4f5b87054f67aa5611cec4630`, redis el9 `eb3fbcf3bb8de3221d6ab8ce9b54cba56cd5f6fba28855895d973154be377dba`, dict debian `6f637e4bc4f09968b4e1662f30773de866d2df2698f8c97889480bd29f3be1e1` (unchanged since Wave 1), dict el9 `a655fa5cee88ad78d45b416c15a496992adfdf33e6a06c3db4bc028185b6dd66`.
- ci.yml promised `redis_source -> failed_source_checkout` by analogy with dict_source. redis derives its archive from the tag in one step, so a bad ref dies inside the derivation and classifies as `failed_source_archive`. The comment now says what was measured. The isolation criterion never named the status; it named the blast radius, which was correct.
- The RPM template changed without an `adapter.revision` bump, deliberately: the revision renders into the Debian changelog and rules headers, and moving byte-identical Debian recipes to a new revision would manufacture a package event out of nothing. The generation record carries the template digest as an input, so the change is traceable. If the audit wants the revision to mean "any input moved" rather than "the Debian output moved", that is a one-line convention change to make — but it should be made looking at it, not slid past.

## Open items, for Step 8 or the audit

1. Generated VMODs are not in the upgrade-transactions matrix (the not-applicable above). Step 8 should wire them in and flip the verdicts to measurements.
2. `registry/README.md`'s tests-field table still omits `fixture_packages` (pre-existing gap from G3), and the two 9.0.1 target manifests carry a duplicated header comment block. Both cosmetic, both real.
3. The evidence run's artifact digests are recorded from the CI run; cachetag's precedent note that release-published RPMs must come from the publishing run applies to redis and dict identically when Step 10's release machinery reaches them.
