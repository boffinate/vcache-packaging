# Step 9, Debian 13 lane: the synthetic mismatch fixture and the apt upgrade-transaction matrix

Date: 2026-07-24

Status: Complete for apt on Debian 13. Sixteen transactions run, each in its own fresh container, each with full apt output retained. Local arm64 process proof; the authoritative amd64 numbers come from CI, but nothing in these results is architecture-dependent — they are resolver outcomes, not code behaviour.

Implements the Debian half of the remaining work in implementation step 9 of [the accepted binary packaging and distribution plan](../../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md), specifically the "Upgrade transaction safety" section and the Phase 3 acceptance criterion that *every documented upgrade command has a tested, documented resolver outcome, and the supported path never silently removes an imported VMOD*.

The baseline cohort is the one built earlier today and described in [the steps 7–8 lane note](20260724_2231_note_step-7-8-debian-13-lane.md): `vinyl-cache 9.0.0~git20260613.a909548147-1` providing `vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3`, and `libvmod-cachetag 1.0.0-1` depending on exactly that virtual package.

## Headline result

The plan's expected baseline is confirmed, and it is confirmed in a more uncomfortable form than the plan anticipated.

`apt upgrade` holds an incompatible Vinyl back, as hypothesised. But **five separate commands will remove the imported VMOD**, and two of them are commands administrators type without thinking: `apt full-upgrade` and `apt install vinyl-cache=<version>`. Worse, apt's confirmation prompt for the removal is `Continue? [Y/n]` — capital Y, the default. An operator who skims the summary and presses Enter has silently deleted their VMOD. There is no second confirmation, no scary phrase to type, nothing that distinguishes this from an ordinary upgrade.

And the plan's stated known limitation is real, not theoretical: a candidate Vinyl package that advertises the *same* `vinyld-abi-<hash>` while carrying different content upgrades cleanly through every path tested, with no complaint from anything. The exact-ABI virtual provide is a correctness guard on the *build*, not on the *repository*. Cohort-aware promotion is not an optimisation; it is the only mechanism that makes the normal upgrade path coherent.

## The synthetic mismatch fixture

### Why a repack and not a second Vinyl build

The plan says: "Use the cryptographically pinned previous supported cohort as the normal mismatch fixture rather than building a second Vinyl on every run. Before a previous cohort exists, create one synthetic mismatched package fixture per release line and retain its source and digest."

There is no previous supported cohort — the Debian 13 lane produced the first one this morning. So the fixture had to be minted.

I did it by a scripted, deterministic metadata-level transformation of the retained baseline debs rather than by compiling a second Vinyl. The justification is short and I think decisive: **the resolver cannot see anything a second compile would change.** apt reads `Version`, `Depends`, `Provides`, `Conflicts`, `Breaks`. A recompile from a different commit would produce a different `vinyld-abi-<hash>` and a different version — which is precisely what the transformation sets, directly. Everything else a rebuild changes is invisible to the transaction under test. Against that, a rebuild costs about an hour per fixture, would still not be the real future security release, and would introduce a second uncontrolled variable into every scenario: if a transaction behaved oddly I would not know whether to blame the metadata or the new binary.

The repack keeps the payload byte-identical to the audited baseline, so any behaviour difference in a transaction is attributable to the metadata change alone. And the fixture is not a mock: it is a real, installable `.deb`, and every scenario below installs it for real through apt, from a real apt repository, with real `dpkg` maintainer scripts running.

The honest limitation, stated plainly: this fixture proves what the *package manager* does. It cannot prove what a genuinely incompatible `vinyld` binary would do to a loaded VMOD at runtime, because the payload is the same `vinyld`. That is a different test and it belongs with the runtime ABI work, not here. What this fixture is for — deciding which commands can delete a VMOD — it exercises completely.

### What the transformation does

`recipes/debian-13/container/make-mismatch.sh`, run inside the pinned `debian:trixie` buildroot:

1. `dpkg-deb -R` the baseline package;
2. add `usr/share/doc/<pkg>/SYNTHETIC-FIXTURE.txt`, a self-identifying marker recording the variant, the baseline version, the baseline ABI, the baseline deb filename, its SHA256, the generation timestamp and the generating script;
3. rewrite the control stanza: `Version`, the `vinyld-abi-<hash>` token in `Provides` (runtime package), the exact `vinyl-cache (= <version>)` relation in `Depends` (`-dev` package), `Installed-Size`, and a `SYNTHETIC PACKAGING FIXTURE` banner appended to the `Description`;
4. append the marker's digest to `DEBIAN/md5sums` so `dpkg --verify` stays clean;
5. pin the mtime of every file the script created or edited to `SOURCE_DATE_EPOCH`;
6. `dpkg-deb --build --root-owner-group`, with `SOURCE_DATE_EPOCH` exported.

`Installed-Size` is recomputed with `du -k -s` excluding `DEBIAN/`, the same way `dpkg-gencontrol` derives it. It comes out slightly above the baseline's (4248 vs 4153 KB) for a ~1.5 KB marker file, because `du` block accounting on the container's overlay filesystem differs from the filesystem the original build ran on. That is a measurement artefact of the field, not a payload difference.

Four independent safeguards stop a fixture being mistaken for a real build on an installed system: the impossible-looking commit tokens (`ffffffffffff`, `eeeeeeeeeeee`), a strict ABI hash of forty `f`s, the banner in the package `Description`, and the marker file on disk.

### Two variants, and why they are never in the same repository

| variant | version | advertises | simulates |
| --- | --- | --- | --- |
| `mismatch` | `9.0.0~git20260614.ffffffffffff-1` | `vinyld-abi-ffffffffffffffffffffffffffffffffffffffff` | an incompatible Vinyl security upgrade: the installed VMOD's exact-ABI dependency can no longer be satisfied |
| `sameabi` | `9.0.0~git20260615.eeeeeeeeeeee-1` | `vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3` (unchanged) | the plan's known limitation: same baked-in ABI string, different version, different payload |

Both sort above the baseline, asserted with `dpkg --compare-versions` before anything is built — a fixture that sorted *below* the baseline would turn every upgrade scenario into a silent no-op and the matrix would "pass" while testing nothing.

`sameabi` also sorts above `mismatch`, so the two are never published into the same repository. `transactions.sh` publishes exactly one candidate per scenario.

Both variants keep `vinyld-vrt (= 23.0)` unchanged. That is deliberate: it isolates the strict-ABI hash as the single variable under test. A real ABI break might or might not also move VRT, and if it did, VRT would mask the effect being measured.

### Digests

Retained under `dist/debian-13/mismatch/`, with `SHA256SUMS` and a `PROVENANCE` manifest recording the fixture sources and the transformation.

Fixture source (the retained baseline cohort, digests re-verified against `dist/debian-13/SHA256SUMS` at generation time, and the run aborts if they do not match):

```text
6d6c5250e421bef6e5fc452dec984ad8b6c4228f80afef77d6bc0bb3dce1db36  vinyl-cache_9.0.0~git20260613.a909548147-1_arm64.deb
45062e04d29a5adae6fc0fb34a3221a1e433d148f70e5fe16ede0e7ec3d96e49  vinyl-cache-dev_9.0.0~git20260613.a909548147-1_arm64.deb
1a7bcea972e34039dad2ba9e8f0934c588ca8ddf9eaa6a9bec90826ffd75e21f  libvmod-cachetag_1.0.0-1_arm64.deb
```

Fixtures produced:

```text
6a961f4eba8dded836ab3b42632cf9d70f1a123d9d80f41a03cfcde59b7c6160  vinyl-cache_9.0.0~git20260614.ffffffffffff-1_arm64.deb
7d1304a37a10ac1b9bd088decec03f9926aa2473250fc9a7affb8f9a09eca8aa  vinyl-cache-dev_9.0.0~git20260614.ffffffffffff-1_arm64.deb
9bd64ff18f534e1e7a4812867c7ab6a67d2f1477c21bcec42af582837c2eb88b  vinyl-cache_9.0.0~git20260615.eeeeeeeeeeee-1_arm64.deb
d1f4e9ba3a23d4ebbd99c93065e2f774bd0d566b3e0e05562777f3442f9d589d  vinyl-cache-dev_9.0.0~git20260615.eeeeeeeeeeee-1_arm64.deb
```

Regenerate with `recipes/debian-13/mismatch-fixture.sh`.

**These digests reproduce.** That was not true of the first version of the script, and the failure is worth recording because it would have quietly made the retained digest worthless. The marker file carried a `date -u` generation timestamp, and `dpkg-deb --build` stamps the deb's `ar` member headers with the current time unless `SOURCE_DATE_EPOCH` is set — so every run produced different digests for identical content. The plan asks for the fixture's digest to be retained; a digest that changes on every regeneration retains nothing. Both sources of drift are now pinned to a fixed `SOURCE_DATE_EPOCH` (1781307021, the Vinyl commit epoch the Debian 13 lane already uses), the marker records that epoch instead of "now", and two independent runs were verified to produce byte-identical debs.

## The harness

`recipes/debian-13/transactions.sh` drives the matrix; `recipes/debian-13/container/stage-transactions.sh` is one scenario. Every scenario is a throwaway container, so no outcome can contaminate the next — which matters more than it sounds, because a scenario that removes cachetag would otherwise make every subsequent scenario meaningless.

Each scenario, in order:

1. build a local apt repository containing **only the baseline cohort**;
2. install the baseline cohort through apt, so dpkg's own dependency state is what a real installation has;
3. assert the baseline compiles a VCL containing `import cachetag` — if the *baseline* cannot do this the scenario aborts, because then a post-transaction failure would prove nothing;
4. publish the synthetic candidate into that same repository and `apt update`, which is exactly what a security update landing in a stable repository looks like to a client;
5. optionally apply an incident-response measure (`apt-mark hold`, an apt pin);
6. run **one** transaction command, capturing full output and exit code;
7. record installed versions, whether `libvmod_cachetag.so` survived, and whether `vinyld -C -f` can still compile the probe VCL;
8. classify.

Scenario containers start from a derived image: the pinned `debian:trixie` digest, fully `dist-upgrade`d once, with the baseline cohort's own Debian dependencies already present. This is not a shortcut around the test. The relations under test are between `vinyl-cache`, `vinyl-cache-dev` and `libvmod-cachetag`; pre-resolving Debian's own packages keeps every `apt upgrade` output free of unrelated base-system churn that would otherwise make the logs ambiguous about what was actually kept back. It also avoids re-downloading a few hundred megabytes of Debian sixteen times.

Full apt output per scenario is in `dist/debian-13/logs/transactions/<scenario>.log`, machine-readable classifications in `<scenario>.result`, and the table in `SUMMARY.tsv`.

apt version under test: **apt 3.0.3**. This matters — apt 3.0 rewrote the transaction summary output, and the wording below is 3.0 wording.

## The transaction table

Baseline installed in every row: `vinyl-cache 9.0.0~git20260613.a909548147-1` + `libvmod-cachetag 1.0.0-1`.

| # | command | pre-step | candidate | exit | resolver outcome | cachetag survived? | `import cachetag` still compiles? | matches plan hypothesis? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s01 | `apt upgrade -y` | — | none | 0 | no-op | yes | yes | control |
| s02 | `apt upgrade -y` | — | mismatch | 0 | **holds Vinyl back** — `Not upgrading: vinyl-cache` | yes | yes | **yes** |
| s03 | `apt full-upgrade` | — | mismatch | 1 | **proposes removing the VMOD**, aborted only by the unanswered `Continue? [Y/n]` prompt | yes | yes | **yes — and worse than stated** |
| s04 | `apt full-upgrade -y` | — | mismatch | 0 | **removes libvmod-cachetag**, upgrades Vinyl | **no** | **no** | **yes** |
| s05 | `apt install vinyl-cache=<candidate>` | — | mismatch | 1 | proposes removing the VMOD, aborted at the prompt | yes | yes | not hypothesised |
| s06 | `apt install -y vinyl-cache=<candidate>` | — | mismatch | 0 | **removes libvmod-cachetag** | **no** | **no** | not hypothesised |
| s07 | `apt full-upgrade -y` | `apt-mark hold vinyl-cache` | mismatch | 0 | holds back — `Not upgrading: vinyl-cache` | yes | yes | incident response works |
| s08 | `apt full-upgrade -y` | apt pin, `Pin-Priority: -1` | mismatch | 0 | candidate invisible; `Upgrading: 0, Not Upgrading: 0` | yes | yes | incident response works |
| s09 | `apt-get upgrade -y` | — | mismatch | 0 | holds back — `The following packages have been kept back: vinyl-cache` | yes | yes | **yes** |
| s10 | `apt-get dist-upgrade -y` | — | mismatch | 0 | **removes libvmod-cachetag** | **no** | **no** | **yes** |
| s11 | `apt full-upgrade -y`, with `vinyl-cache-dev` installed | — | mismatch | 0 | upgrades both Vinyl packages, **removes libvmod-cachetag** | **no** | **no** | **yes** |
| s12 | `apt upgrade -y` | — | sameabi | 0 | **upgrades Vinyl cleanly**, VMOD untouched | yes | yes | **the known limitation, confirmed** |
| s13 | `apt full-upgrade -y` | — | sameabi | 0 | **upgrades Vinyl cleanly**, VMOD untouched | yes | yes | **the known limitation, confirmed** |
| s14 | `apt install -y vinyl-cache=<candidate>` | — | sameabi | 0 | **upgrades Vinyl cleanly**, VMOD untouched | yes | yes | **the known limitation, confirmed** |
| s15 | `apt install -y vinyl-cache=<candidate>` | `apt-mark hold vinyl-cache` | mismatch | 100 | **refused**: `Held packages were changed and -y was used without --allow-change-held-packages` | yes | yes | hold is the stronger measure |
| s16 | `apt install -y vinyl-cache=<candidate>` | apt pin, `Pin-Priority: -1` | mismatch | 0 | **removes libvmod-cachetag** — the pin does **not** stop an explicit versioned install | **no** | **no** | **pin is weaker than expected** |

The removal is not subtle when it happens. After s04:

```text
Message from VCC-compiler:
Could not find VMOD cachetag
('/tmp/probe.vcl' Line 3 Pos 8)
import cachetag;
-------########-

Running VCC-compiler failed, exited with 2
VCL compilation failed
```

Which is exactly the failure mode the plan predicted: the daemon keeps running on its already-mapped VMOD, and dies at the next restart or VCL reload.

## Commands that must carry a prominent warning

Five commands can remove an imported VMOD. All five must be flagged in the release notes and the upgrade documentation:

- **`apt full-upgrade`** — proposes the removal with a `[Y/n]` prompt whose default is yes;
- **`apt full-upgrade -y`** — removes it without asking;
- **`apt install vinyl-cache=<version>`** — same prompt, same default;
- **`apt install -y vinyl-cache=<version>`** — removes it without asking;
- **`apt-get dist-upgrade`** / **`apt-get dist-upgrade -y`** — removes it, and this is the one most likely to appear in somebody's automation.

Two commands are safe against an ABI-mismatched candidate and are the supported path:

- **`apt upgrade`** — reports `Not upgrading: vinyl-cache`;
- **`apt-get upgrade`** — reports `The following packages have been kept back: vinyl-cache`.

The wording of the warning matters. "Do not use `full-upgrade`" is useless advice on a machine that runs it for unrelated reasons. The warning should say what to look for: **if an apt transaction lists `libvmod-cachetag` (or any `libvmod-*` package) under `REMOVING:`, answer no.** That is a check an operator can actually apply, and it generalises to every VMOD, not just ours.

One thing this matrix does *not* cover: `unattended-upgrades`. It has its own resolver policy and is not simply `apt upgrade`. Its behaviour against a mismatched cohort is untested and must not be assumed from s02. Testing it is a prerequisite for any unattended-upgrade support claim, which the plan already defers until the cohort-aware repository exists.

## Incident-response procedure

Both measures work against the automatic upgrade path, but they are not interchangeable, and the difference only showed up because s15 and s16 were run.

**`apt-mark hold vinyl-cache`** is the stronger measure. It stops `apt full-upgrade -y` (s07) *and* refuses an explicit `apt install -y vinyl-cache=<version>` with exit 100 and a clear message naming `--allow-change-held-packages` (s15). It is visible to anyone who runs `apt-mark showhold`.

**An apt pin** at `Pin-Priority: -1` makes the candidate invisible to the upgrade calculation entirely — `apt-cache policy` shows the candidate at priority `-1` and apt reports `Upgrading: 0, Not Upgrading: 0`, so the operator is not even told a newer version exists (s08). But it does **not** stop an explicit versioned install: s16 removed the VMOD, exit 0, no warning. A pin controls *selection*, not *permission*.

So the documented procedure is:

```sh
# freeze the cohort while an incompatible Vinyl update is in the repository
apt-mark hold vinyl-cache vinyl-cache-dev

# confirm
apt-mark showhold

# ... wait for the matching cohort (Vinyl + every VMOD) to be promoted ...

apt-mark unhold vinyl-cache vinyl-cache-dev
apt upgrade
```

Use a pin only in addition to a hold, when you also want to suppress the "upgradable" noise, and never instead of one. Note that a hold must cover `vinyl-cache-dev` too on machines that have it: s11 shows the `-dev` package's exact-version dependency drags the runtime along.

## The same-ABI limitation: confirmed, and it matters

The plan flagged this as a known limitation and asked whether transaction testing shows it is material. It does.

The `sameabi` fixture is a higher-versioned Vinyl package with a genuinely different payload that advertises the identical `vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3`. Every path tested upgraded it cleanly: `apt upgrade` (s12), `apt full-upgrade` (s13), explicit versioned install (s14). The VMOD stayed installed, the `.so` stayed on disk, and `vinyld -C -f` compiled `import cachetag` without complaint.

Nothing objected, because from the resolver's point of view nothing was wrong. Both cachetag relations were satisfied:

- `vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3` — advertised by the candidate, unchanged;
- `vinyl-cache (>= 9.0.0~git20260613.a909548147)` — a lower bound, which any *newer* Vinyl satisfies by construction.

That second relation deserves attention. It is the only version constraint cachetag places on the runtime, and being a `>=`, it can never fail during an upgrade. All the actual protection comes from the ABI provide, and the ABI provide is a hash of the *upstream commit*, not of the built artefact. Two packages built from the same commit with different downstream patches — a distro security backport, a vendor patch, our own hotfix — advertise the same token and are interchangeable as far as apt is concerned.

The conclusion I draw is narrower than "add a cohort-qualified ABI provide". The provide is doing the job it was designed for and s02–s11 prove it: an actual upstream ABI change is caught. What it cannot do is police *provenance*, and no dependency relation on a single package can, because the guarantee being sought is about a set of packages moving together.

So:

- **cohort-aware repository promotion is load-bearing, not a nicety.** It is the only mechanism that keeps the normal path coherent. Publish Vinyl and every VMOD as one atomic set, or the guarantee does not exist.
- **the ABI provide should be qualified by the cohort, not just the upstream commit** — something like `vinyld-abi-<commit>-<cohort>`, so a repackaged or patched Vinyl from a different cohort does not silently satisfy an installed VMOD's dependency. This is cheap to add now and expensive to add after packages are published, because it is an incompatible metadata change. Cost: mismatched-cohort combinations become unresolvable rather than silently loadable, which is the intended direction, but it also means a distro-native Vinyl can never satisfy our VMOD — that is a real trade-off and it should be decided together with the distro-native support question in Phase 4, not unilaterally here.
- **a pinned or manually installed older cachetag remains a hole regardless.** s14 shows a direct versioned install sails through. Nothing in the package metadata can prevent an administrator from constructing an unsupported combination on purpose; the mitigation is that `vinyld` itself refuses to load a VMOD whose strict ABI string disagrees, which is a runtime guarantee that already exists and should be stated in the documentation as the real backstop.

## Implications for the repository and promotion design

1. **Promotion must be atomic across the cohort.** A repository state in which a new Vinyl is visible before the matching VMODs are is exactly the state s04 and s10 exploit. If the publishing tool cannot promote a set atomically, it must not promote at all.
2. **Never publish a Vinyl update into a repository unless every supported VMOD for that cohort is published in the same operation.** With the cohort complete, `apt upgrade` upgrades both packages together and the whole question disappears; the dangerous scenarios only exist because Vinyl moved alone.
3. **The repository is the safety mechanism; the ABI provide is the backstop.** The provide converts "silently broken" into "unresolvable", which is a large improvement, but it converts it into *removal* under half the commands tested. That is not a state to design for — it is a state to make unreachable by never publishing an incomplete cohort.
4. **Release notes need the `REMOVING:` warning, not a list of forbidden commands.** See above.
5. **`vinyl-cache-dev` must be part of the cohort's hold/promotion unit** (s11).
6. **This whole matrix should run in CI on every cohort promotion candidate**, against the *previous* promoted cohort rather than a synthetic fixture, as the plan intends once a previous cohort exists. The scripts take the baseline version, ABI and candidate version as constants at the top of `transactions.sh`; pointing them at a real previous cohort is a constant change, not a rewrite. The synthetic fixture then becomes what the plan says it is — a per-release-line artefact retained for provenance, not the routine input.

## What is not proven here

- **amd64.** Everything ran on arm64. Resolver behaviour is architecture-independent and the package relations are identical, so I would be surprised by a difference, but "would be surprised" is not evidence.
- **Runtime incompatibility.** The fixture's payload is the baseline `vinyld`, so no scenario demonstrates a genuinely incompatible daemon refusing a VMOD at load time. That claim rests on `$ABI strict` and belongs to a different test.
- **`unattended-upgrades`.** Untested, as noted above.
- **Downgrade and rollback.** The plan's transaction list mentions rollback for other package managers; no apt downgrade path was tested here. Worth adding when the repository design settles, since rollback to a previous cohort is the natural incident-response endgame after a hold.
- **A repository with both candidates present at once.** Deliberately excluded — one candidate per scenario — but a real repository accumulating multiple candidate versions is a state worth testing before promotion tooling is finalised.

## Reproducing

```sh
# fixtures (requires the baseline cohort from recipes/debian-13/build.sh)
vcache-packaging/recipes/debian-13/mismatch-fixture.sh

# the whole matrix
vcache-packaging/recipes/debian-13/transactions.sh

# one scenario, or a prefix
vcache-packaging/recipes/debian-13/transactions.sh s04
vcache-packaging/recipes/debian-13/transactions.sh --list
vcache-packaging/recipes/debian-13/transactions.sh --summary
```

Everything runs in containers. No host package is installed. The host only reads and writes `dist/debian-13/`.
