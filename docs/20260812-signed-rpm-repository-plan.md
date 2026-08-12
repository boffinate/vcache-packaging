# Signed package repository plan

Status: proposed sibling repository; no repository publisher is implemented under this design. Date: 2026-08-12.

## Decision

Create a sibling repository, provisionally named `vcache-repository`, that turns green, checksummed GitHub Releases from `vcache-packaging` into signed APT and DNF repositories on R2.

`vcache-packaging` remains the producer. It builds `.deb` and `.rpm` files, installs and load-tests them in their target containers, applies the all-or-nothing release gate, writes `SHA256SUMS`, and publishes the green pair as a GitHub Release.

`vcache-repository` is the distributor. It downloads one completed GitHub Release, verifies the release manifest and package metadata, signs what each repository format requires, generates repository metadata, publishes to R2, and tests installation through APT or DNF.

The signing repository never consumes raw GitHub Actions artifacts. Its input is a public GitHub Release whose package set has already passed the producer's gate and whose bytes are covered by `SHA256SUMS`.

This boundary keeps GPG and R2 credentials out of the build repository and keeps signing, repository generation, caching policy and client configuration out of the build workflows.

## What stays in `vcache-packaging`

- The engine and VMOD catalog.
- Source acquisition and build containers.
- Debian recipes and RPM spec templates.
- Package construction.
- Package installation and VCL load checks.
- The per-`(engine, target)` completeness gate.
- Stable, replaceable GitHub Releases containing unsigned packages and `SHA256SUMS`.

Package construction stays here because it is part of the build proof. The package recipes encode target dependencies, exact engine/VMOD relationships, file ownership and installation paths, and the existing jobs prove that those packages install and load. Moving that work would make the signing repository import the catalog and build rules, which would erase the boundary and expose the signing job to much more code.

The handoff unit is therefore a native package, not a raw prefix, tarball or collection of binaries.

Direct GitHub Release downloads retain the existing contract: `.deb` and `.rpm` files are unsigned convenience artifacts accompanied by `SHA256SUMS`. Signed repository clients use the sibling repository.

## What moves to `vcache-repository`

- The GPG private key and expected full fingerprint.
- The R2 bucket credential, endpoint and public URL settings.
- APT metadata generation and signing.
- RPM payload signing.
- RPM metadata generation and signing.
- Public key and client configuration publication.
- Repository caching and update-order policy.
- Clean-client APT and DNF smoke tests.

The sibling contains no upstream pins, package recipes, VMOD catalog, compatibility matrix, source checkout or build scripts.

## Source release contract

The publisher accepts one manual input:

```text
source_tag = <engine-id>-<target-id>
```

The public source repository is fixed in the workflow. It is not an arbitrary URL supplied at dispatch time, and fetching it needs no cross-repository credential.

For the selected release, the publisher must:

1. Resolve one non-draft, non-prerelease GitHub Release and record its release ID and target commit in the workflow log.
2. List and download that release's assets by asset ID into a new temporary directory.
3. Require exactly one `SHA256SUMS` asset.
4. Parse `SHA256SUMS` strictly, reject non-package assets other than the manifest, and require its filename set to equal every downloaded package asset.
5. Verify every package before invoking any signing tool.
6. Require every package to have the format and architecture implied by the allowed target suffix.
7. Complete all local signing, index generation and verification before changing R2.

The publisher trusts the source repository's green-release contract and does not rerun its catalog or completeness gate. It independently verifies the bytes and basic package identity that cross the repository boundary.

Downloading by release and asset ID prevents a stable tag replacement from silently mixing two releases. If the source release is replaced during download, the run fails before R2 publication and can be dispatched again.

GitHub Actions also calculates and validates artifact digests when artifacts move between jobs, but that transport check is not the sibling repository's trust boundary. The checked `SHA256SUMS` attached to the completed source release is.

## Sibling repository shape

Keep the new repository small:

```text
README.md
SCOPE.md
DESIGN.md
targets.tsv
scripts/
  fetch-release.sh
  publish-apt.sh
  publish-rpm.sh
  smoke-apt.sh
  smoke-rpm.sh
tools/
  selftest.py
.github/workflows/
  publish.yml
```

`targets.tsv` contains only distribution facts needed to validate and route a release: allowed target ID, package format, package architecture, utility container and public repository root. It does not copy engine versions, VMOD names or package dependencies from the producer.

The initial allowed targets are:

```text
debian-13-amd64
debian-13-arm64
ubuntu-26.04-amd64
ubuntu-26.04-arm64
el10-x86_64
el10-aarch64
```

## Publication workflow

Start with one manually dispatched workflow. Releases happen a few times a year, so cross-repository dispatch credentials and trigger plumbing are not justified initially.

`publish.yml` must:

1. Run host-safe self-tests.
2. Fetch and verify the selected source release without signing credentials.
3. Enter a protected `production` environment for the format-specific publisher job.
4. Publish one target root under a non-cancelling R2 concurrency group.
5. Run a clean native-client smoke job after publication.

The production environment holds:

```text
REPOSITORY_GPG_PRIVATE_KEY_B64
REPOSITORY_GPG_FINGERPRINT
R2_ACCOUNT_ID
R2_BUCKET
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
APT_REPOSITORY_URL
RPM_REPOSITORY_URL
```

Only the publisher job receives the private key and R2 credential. Fetch, validation and smoke jobs do not.

Automatic dispatch from `vcache-packaging` may be added later if manual publication becomes a recurring nuisance. It would require a narrowly scoped credential that can dispatch the sibling workflow but cannot read the signing key or write R2. Do not add polling, a publication ledger or a state branch.

## APT route

APT publication keeps the simpler existing rule: `.deb` payloads remain unchanged and unsigned; the repository signs its metadata.

For one Debian-family source release, `publish-apt.sh` must:

1. Recheck the source `SHA256SUMS` and package metadata.
2. Import the archive key into a temporary mode-0700 `GNUPGHOME` and require exactly one primary certificate with the configured full fingerprint.
3. Build a fresh single-target `reprepro` tree using `SignWith`.
4. Require `Release`, `Release.gpg` and `InRelease` and verify them locally.
5. Export the public certificate.
6. Upload package objects before signed metadata without deleting old package objects.

Keep one root per existing target:

```text
vinyl-cache/apt/debian-13-amd64/
vinyl-cache/apt/debian-13-arm64/
vinyl-cache/apt/ubuntu-26.04-amd64/
vinyl-cache/apt/ubuntu-26.04-arm64/
```

The shared public key remains at:

```text
vinyl-cache/apt/vcache-archive-keyring.asc
```

## Signed RPM route

RPM publication signs both the copied package payloads and repository metadata. There is no unsigned repository mode.

For one EL10 source release, `publish-rpm.sh` must:

1. Recheck the source `SHA256SUMS` and RPM name, version, release and architecture metadata.
2. Copy the unsigned source RPMs into a disposable work tree; never mutate the downloaded evidence.
3. Import the archive key into a temporary mode-0700 `GNUPGHOME` and require exactly one primary certificate with the configured full fingerprint.
4. Configure EL10's RPM 4.19 through `_gpg_name` using the full fingerprint.
5. Run `rpmsign --addsign` on every copied RPM.
6. Import the exported public certificate into a temporary RPM database and require `rpmkeys --checksig --verbose` to report a valid signature and valid digests for every RPM.
7. Place the signed copies under a fresh `Packages/` directory.
8. Run `createrepo_c` over the complete target tree.
9. Create the armored detached signature `repodata/repomd.xml.asc` and verify it locally.
10. Generate the shared `vcache.repo` file and export the public certificate.
11. Upload through the R2 S3 endpoint only after every local check passes.

Use one root for each EL10 target:

```text
vinyl-cache/rpm/el10-x86_64/
vinyl-cache/rpm/el10-aarch64/
```

Publish the shared client files beside them:

```text
vinyl-cache/rpm/vcache.repo
vinyl-cache/rpm/vcache-archive-keyring.asc
```

`RPM_REPOSITORY_URL` is the public URL corresponding to `vinyl-cache/rpm`. The generated repository file is:

```ini
[vinyl-cache]
name=Vinyl Cache
baseurl=<RPM_REPOSITORY_URL>/el10-$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=<RPM_REPOSITORY_URL>/vcache-archive-keyring.asc
sslverify=1
```

An AlmaLinux 10 proof on 2026-08-12 used RPM 4.19.1.1, `rpmsign`, GnuPG 2 and `createrepo_c` 1.1.2 to sign and verify a real project RPM, generate and sign repository metadata, and complete DNF metadata verification with both checks enabled.

## Replaceable RPM objects

RPM signing changes the package bytes. Signing the same unsigned RPM twice with the same key produced different SHA-256 values in the EL10 proof, so the APT publisher's immutable-package check cannot be reused for signed RPM copies.

Keep the producer's stable, replaceable-release model:

- bypass the Cloudflare cache for the entire `vinyl-cache/rpm/` prefix;
- send `Cache-Control: no-store` on RPM packages, metadata, the key and `.repo` file;
- upload signed packages before metadata;
- serialize writers in the sibling repository; and
- never use `sync --delete` or remove old unindexed packages.

A client holding old metadata during the short package-first update window may receive a checksum error. It cannot install an unauthenticated package: the metadata checksum or package signature check fails closed. A retry after publication sees the completed tree. Do not claim atomic publication.

Snapshots, immutable version paths and RPM release counters stay out until observed failures justify them.

## RPM upload order

Build and verify the complete tree locally, then upload in this order:

1. The public key, after applying the no-rotation fingerprint and byte checks.
2. Every signed file under `Packages/`.
3. Every generated file under `repodata/` except `repomd.xml` and `repomd.xml.asc`.
4. `repodata/repomd.xml`.
5. `repodata/repomd.xml.asc`.
6. `vcache.repo` last.

The detached signature is the metadata commit point. Publishing `.repo` last prevents a first-time client from discovering the repository before signed metadata exists.

## Client smoke tests

Repository smoke tests prove distribution, not builds. The producer has already proved that each package installs and its VMODs load before the source release exists.

APT smoke jobs must use the target's native architecture, import only the expected key through `signed-by`, run `apt-get update` without insecure flags, and install every package name derived from the downloaded `.deb` metadata.

DNF smoke jobs must use clean native EL10 x86_64 and aarch64 containers, install the generated `.repo`, assert `gpgcheck=1`, `repo_gpgcheck=1` and `sslverify=1`, run `dnf makecache` without weakening overrides, and install every package name derived from the source RPM metadata.

Do not duplicate catalog-aware VCL load tests in the sibling repository. Signing changes RPM headers, not payload files, and the source release could not exist without the producer's install/load proof.

## Effect on the current APT branch

The current `feat/signed-apt-r2-repository` branch proves that signed APT publication is feasible, but its publisher, R2 settings, signing secrets and repository smoke workflow are on the wrong side of the new boundary.

If this design is accepted:

1. Do not merge the branch as it stands.
2. Create `vcache-repository` and transplant only the format-specific publishing logic that remains useful.
3. Return `vcache-packaging` to its green GitHub Release boundary, retaining or strengthening the strict `SHA256SUMS` asset contract.
4. Keep repository user instructions and client smoke tests in the sibling repository.
5. Leave `SCOPE.md` in `vcache-packaging` focused on building, validating and publishing checksummed release assets; repository signing stays out of its scope.

The earlier in-repository APT/RPM plan is superseded by this document.

## Host-safe tests in the sibling

The sibling's self-tests must cover:

- exact source repository allow-listing;
- strict release-asset and `SHA256SUMS` matching;
- target format and architecture validation;
- no signing secret in fetch or smoke jobs;
- exact primary-key fingerprint checks;
- package signature creation and verification for RPM;
- `createrepo_c` only after RPM signing;
- local verification of `repomd.xml.asc`;
- `gpgcheck=1`, `repo_gpgcheck=1` and `sslverify=1` in `vcache.repo`;
- package-before-metadata upload order;
- cancellation and concurrency guards around R2 mutation; and
- absence of `--nogpgcheck`, `gpgcheck=0`, `repo_gpgcheck=0`, `trusted=yes`, `sync --delete` and private-key files in the checkout.

Add disposable-container integration fixtures using a temporary key, one real `.deb` and one real `.rpm`. They should exercise fetch verification, APT metadata signing, RPM payload signing, RPM metadata signing and package-manager metadata verification without contacting R2.

## Deliberately absent

- no raw Actions-artifact input;
- no source builds or package recipes in the signing repository;
- no unsigned APT metadata, RPM payload or RPM metadata path;
- no source-package repository;
- no distribution targets beyond the current Debian 13, Ubuntu 26.04 and EL10 pairs;
- no updateinfo, comps, modules, delta RPMs or mirror lists;
- no package-release counter or immutable snapshot path;
- no key rollover, second key or keyring package;
- no Worker, custom repository server or managed repository service;
- no deletion, retention, rollback, polling or publication ledger; and
- no automated cross-repository trigger until manual dispatch is demonstrably burdensome.

## Size expectation

The goal is a sibling repository of roughly 500–900 code lines across fetch, APT, RPM, smoke, workflow and self-tests. `vcache-packaging` should lose repository-specific code rather than gain it. If the sibling needs the producer's catalog, build scripts or package recipes, the boundary has failed and the design must be reconsidered.

## Definition of done

This split is complete when:

- `vcache-packaging` can produce a green, checksummed GitHub Release without any GPG or R2 credential;
- the sibling accepts only an allowed, completed source release and rejects any asset or checksum mismatch before signing;
- APT metadata is signed and verified for all four Debian-family targets;
- every published RPM and `repodata/repomd.xml` is signed and verified for both EL10 targets;
- the generated `.repo` enables package, metadata and TLS verification;
- clean native clients install every advertised package through APT or DNF;
- a failed repository publication leaves the source GitHub Release untouched and usable as the checksummed fallback; and
- neither repository contains a second copy of the other's build or distribution policy.

## References

- [Cloudflare's R2 APT/YUM walkthrough](https://blog.cloudflare.com/using-cloudflare-r2-as-an-apt-yum-repository/)
- [Cloudflare's current package publisher](https://raw.githubusercontent.com/cloudflare/cloudflared/master/release_pkgs.py)
- [GitHub workflow artifact digest validation](https://docs.github.com/en/actions/tutorials/store-and-share-data#validating-artifacts)
- [GitHub artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations)
- [GitHub environments and protected secrets](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [RPM `rpmsign` manual](https://rpm.org/docs/6.1.x/man/rpmsign.1)
- [RHEL 10 packaging and distributing software](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/packaging_and_distributing_software/)
- [DNF configuration reference](https://dnf.readthedocs.io/en/latest/conf_ref.html)
- [R2 consistency and custom-domain caching](https://developers.cloudflare.com/r2/reference/consistency/)
