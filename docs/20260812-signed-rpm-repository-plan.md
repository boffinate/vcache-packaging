# Signed package repository plan

Status: revised proposal for a sibling repository; no repository publisher is implemented under this design. Date: 2026-08-12.

## Decision

Create a sibling repository, provisionally named `vcache-repository`, that turns green GitHub Releases from `vcache-packaging` into signed APT and DNF repositories on R2.

`vcache-packaging` remains the producer. It builds native `.deb` and `.rpm` packages, installs and load-tests them in target containers, applies the all-or-nothing release gate, assigns their package-set revision, writes a release manifest and `SHA256SUMS`, attests the final bytes, and publishes the green pair as a GitHub Release.

`vcache-repository` is the distributor. It downloads one completed GitHub Release, verifies its checksums, manifest, attestations and package metadata, signs what each repository format requires, generates repository metadata, publishes to R2, and tests installation through APT or DNF.

The distributor never consumes raw GitHub Actions artifacts. Its input is a public GitHub Release whose package set has already passed the producer's gate.

The public repositories are family-scoped current channels. Each root contains the newest published package set for one engine family and target. Package payload objects are immutable in both formats; signed indexes and client configuration are replaceable.

This boundary keeps GPG and R2 credentials out of the build repository and keeps signing, repository state, caching policy and client configuration out of the build workflows.

## What stays in `vcache-packaging`

- The engine and VMOD catalog.
- Source acquisition and build containers.
- Debian recipes and RPM spec templates.
- Package construction and version assignment.
- Package installation and VCL load checks.
- The per-`(engine, target)` completeness gate.
- Replaceable GitHub Releases containing unsigned packages, `release.json` and `SHA256SUMS`.
- Provenance attestations for the final release files.

Package construction stays here because it is part of the build proof. The recipes encode dependencies, engine/VMOD relationships, file ownership and installation paths, and the existing jobs prove that those packages install and load. Moving that work would make the distributor import the catalog and build rules, erasing the boundary and exposing the signing job to much more code.

The handoff unit is a native package, not a raw prefix, tarball or collection of binaries.

Direct GitHub Release downloads retain the existing contract: `.deb` and `.rpm` files are unsigned convenience artifacts accompanied by checksums and provenance. Signed repository clients use the sibling repository.

## What moves to `vcache-repository`

- Archive-key policy and the CI signing subkey.
- The R2 credential, endpoint, custom domain and cache rules.
- APT metadata generation and signing.
- RPM payload signing.
- RPM metadata generation and signing.
- Public key and client configuration publication.
- Repository update order and current-channel policy.
- Clean-client APT and DNF smoke tests.

The sibling contains no upstream pins, package recipes, VMOD catalog, compatibility matrix, source checkout or build scripts.

## Required producer contract changes

Amend `SCOPE.md` and `DESIGN.md` in `vcache-packaging` before writing sibling code.

`SCOPE.md` should continue to exclude signing and repository publication from this repository, while its Publication section names the external handoff: green GitHub Releases are the producer's final output and may be consumed by the sibling signing repository. Remove the suggestion that a managed service is the expected next step.

`DESIGN.md` must:

- replace decision 3 with the producer/distributor boundary;
- amend decision 13 so a replaceable GitHub Release may feed immutable repository package objects only through a package revision;
- add the release-asset interface defined below;
- make the package-set revision part of the engine and VMOD version contract; and
- state that attestations establish build provenance but are neither package signatures nor evidence that the contents are safe.

Signing keys and R2 settings must not appear in `vcache-packaging`.

## Package-set revision

Add one required positive decimal `package_revision` to every package-enabled release engine. It starts at `"1"` and applies to the engine package and every VMOD in that engine's release set.

Bump it whenever a rebuild intended for repository publication would change any package bytes without changing the upstream package version. This includes a packaging fix, a changed moving source ref, or another non-reproducible rebuild that differs from bytes already published under the same package identity.

The version rules become:

```text
Debian engine: <engine-version>-<package-revision>
Debian VMOD:   <upstream-version>-<package-revision>~<family-marker><engine-version>
RPM engine:    <engine-version>-<package-revision>%{?dist}
RPM VMOD:      <upstream-version>-<package-revision>.<family-marker><engine-version>%{?dist}
```

The same revision across the set preserves the exact engine dependencies and makes a revision bump an all-package rebuild. There is no per-package counter and no counter in the sibling repository.

The sibling fails with a request to bump `package_revision` if an existing package object has the same package identity but a different producer SHA-256. It never overwrites that object.

## Release-asset interface

The handoff tag remains:

```text
<engine-id>-<target-id>
```

The release is non-draft and non-prerelease. Its target commit is the exact `vcache-packaging` commit used by the release workflow.

Its assets are exactly:

- the complete package set for the green `(engine, target)` pair;
- one `release.json`; and
- one `SHA256SUMS`.

`release.json` uses a versioned schema and contains only handoff facts:

```json
{
  "schema": "vcache-release/1",
  "engine": "vinyl-9.0.1",
  "family": "vinyl",
  "target": "el10-x86_64",
  "format": "rpm",
  "architecture": "x86_64",
  "package_revision": "1",
  "source_commit": "<40-hex commit>",
  "packages": ["<sorted package filename>"]
}
```

`SHA256SUMS` covers every package and `release.json`, but not itself. Each line is a lowercase 64-character digest, two spaces, and a basename with no slash. The filename set must match the package files plus `release.json` exactly.

The release workflow generates provenance attestations over every package and `release.json` after staging and before upload. It grants only the permissions needed by the attestation action, pins that action to a full commit SHA, and uses GitHub-hosted runners.

The stable release may still be deleted and recreated after a failed build is fixed. Its package revision must be bumped if the replacement changes bytes already admitted to the signed repository under the same package identity.

## Source release verification

The sibling workflow accepts one manual input:

```text
source_tag = <engine-id>-<target-id>
```

The public source repository is fixed in the workflow. It is not an arbitrary dispatch input, and fetching it needs no cross-repository credential.

For the selected release, the fetch job must:

1. Resolve one non-draft, non-prerelease GitHub Release and record its release ID and target commit.
2. List and download every asset by asset ID into a new temporary directory.
3. Re-resolve the tag after download and require the same release ID and target commit, failing if a replacement raced the fetch.
4. Enforce the exact asset interface above.
5. Parse `SHA256SUMS` strictly and verify every covered file.
6. Validate `release.json`, including its schema, source commit, family, target, format, architecture, revision and sorted package filename set; require the tag's engine and target to match it.
7. Verify the attestation for every package and `release.json` against the exact source repository, `.github/workflows/release.yml`, `refs/heads/main`, target commit and GitHub-hosted runner policy.
8. Inspect native package metadata and require it to agree with the manifest's format, architecture, revision and package filenames.
9. Complete all local validation before making signing credentials available or changing R2.

`SHA256SUMS` detects corruption and gives direct-download users a standard integrity check. It does not authenticate itself. The attestation binds each final file to the expected producer workflow and commit. Neither mechanism proves that the source or package is free of defects.

An attestation API or verification failure stops publication and can be retried. There is no unauthenticated fallback in the distributor.

## Archive model

Every advertised root is scoped by engine family and target:

```text
vinyl-cache/apt/vinyl/debian-13-amd64/
vinyl-cache/apt/vinyl/debian-13-arm64/
vinyl-cache/apt/vinyl/ubuntu-26.04-amd64/
vinyl-cache/apt/vinyl/ubuntu-26.04-arm64/
vinyl-cache/apt/varnish/<target>/

vinyl-cache/rpm/vinyl/el10-x86_64/
vinyl-cache/rpm/vinyl/el10-aarch64/
vinyl-cache/rpm/varnish/<target>/
```

A fresh tree generated from one release intentionally replaces the index for that family and target. A new Vinyl publication cannot unlist Varnish, and a new Varnish publication cannot unlist Vinyl. A newer engine version in the same family replaces the older version in the advertised current channel.

Old package objects remain in R2 but are not promised to remain indexed or installable. They are accepted storage residue, not a hidden archive, rollback service or publication ledger.

Do not download and merge the previous repository tree. A cumulative archive would add persistent state, retention promises and conflict handling that this project does not need.

`REPOSITORY_PUBLIC_URL` is the custom-domain URL corresponding to the `vinyl-cache` prefix. The shared public certificate is:

```text
vinyl-cache/vcache-archive-keyring.asc
```

## Sibling repository shape

Keep the sibling small:

```text
README.md
SCOPE.md
DESIGN.md
routes.tsv
keys/
  vcache-archive-keyring.asc
scripts/
  fetch-release.sh
  publish-key.sh
  publish-apt.sh
  publish-rpm.sh
  smoke-apt.sh
  smoke-rpm.sh
tools/
  selftest.py
.github/workflows/
  publish.yml
  publish-key.yml
```

The checked-in key is public. No private key file may exist in the checkout or an Actions artifact.

`routes.tsv` is a distributor-owned allow-list and routing table. It contains the allowed target ID, publisher/smoke container and APT suite/component where applicable. Format, architecture, engine, family, version and revision remain authoritative in the producer's manifest and package metadata. The sibling cross-checks any route implication against both.

Adding a target is deliberately a two-repository change: the producer learns how to build and test it; the distributor separately chooses to advertise and route it.

## Signing-key custody

Create one archive primary key offline and keep it offline. Store at least one encrypted offline backup and its revocation certificate separately. Put only a dedicated signing subkey in the protected GitHub environment.

The sibling records two full fingerprints:

```text
REPOSITORY_GPG_PRIMARY_FINGERPRINT
REPOSITORY_GPG_SIGNING_SUBKEY_FINGERPRINT
```

The private secret is:

```text
REPOSITORY_GPG_SIGNING_SUBKEY_B64
```

This secret is an armored or base64-encoded `gpg --export-secret-subkeys` bundle containing the primary public stub and exactly one secret signing subkey, not a bare subkey packet and not the secret primary key.

Every signing run must require the configured primary certificate and exactly the configured usable secret signing subkey, verify signing capability, and reject an expired or revoked key. The signer imports them into a temporary mode-0700 `GNUPGHOME` that is removed by a trap.

Set and document an expiry policy before commissioning the key. Record the renewal date somewhere maintained outside CI. Generate the revocation certificate at key creation, not after a loss.

Ordinary package publication never replaces the public key object. A maintainer uses the offline primary key to create, renew or revoke a subkey, exports the updated public certificate, verifies it offline, and commits only that public certificate to `keys/vcache-archive-keyring.asc`. `publish-key.yml` does not create or revoke keys. It verifies the checked-in primary and subkey fingerprints, requires the primary fingerprint in R2 to be absent or unchanged, and uploads the reviewed certificate to `vinyl-cache/vcache-archive-keyring.asc` through a protected key-maintenance environment. It receives the R2 credential and public fingerprints but never the private signing-subkey secret.

Publish the updated certificate before using a new signing subkey.

Every package publisher requires the R2 certificate to contain the configured primary and active signing subkey before changing any repository object. Initial repository commissioning therefore starts with `publish-key.yml`.

Primary-key replacement is a new trust root and requires explicit client action. It is not disguised as ordinary publication.

Without a keyring package, clients must refresh the public certificate before an ordinary signing-subkey renewal takes effect. The README documents that manual step and the renewal announcement gives clients time to complete it before the old subkey expires.

The compromise procedure is short and written before launch: stop publication, remove the CI secret, revoke the affected subkey offline, publish the updated certificate and an advisory, replace the CI subkey, bump package revisions for any packages that must be re-signed, and tell clients how to refresh the key. Primary-key loss or compromise requires a new key and manual client migration.

Revocation deliberately overrides availability. Metadata and RPMs signed by a revoked subkey may stop verifying; the maintainer removes them from current indexes and republishes revised package identities with the replacement subkey. The ordinary guarantee that stale metadata remains usable applies only while its signing subkey remains trusted.

This is a manual lifecycle, not automated rollover machinery or a keyring package.

## Publication workflow and secrets

Start with manually dispatched workflows. Releases happen a few times a year, so cross-repository dispatch credentials and trigger plumbing are not justified initially.

`publish.yml` must:

1. Run host-safe self-tests.
2. Fetch and verify the selected source release in a job with no signing or R2 secret.
3. Pass only the verified files and recorded release identity to the publisher job through one internal workflow artifact.
4. Enter a protected `production` environment restricted to `main` and requiring a reviewer before secrets are released.
5. Recheck the checksums, manifest, attestations and native package metadata after the cross-job transfer and before importing the signing subkey.
6. Build and verify the complete local repository tree.
7. Publish under one repository-wide, non-cancelling concurrency group shared with `publish-key.yml`.
8. Run a clean native-client smoke job after publication.

Pin every third-party action to a full commit SHA. No pull-request job may enter the production environment.

Do not commission the repository on an account configuration that cannot enforce the main-only environment and approval gate. Use an equivalent external secret-release approval if GitHub cannot provide it.

The production environment holds only:

```text
REPOSITORY_GPG_SIGNING_SUBKEY_B64
REPOSITORY_GPG_PRIMARY_FINGERPRINT
REPOSITORY_GPG_SIGNING_SUBKEY_FINGERPRINT
REPOSITORY_PUBLIC_URL
R2_ACCOUNT_ID
R2_BUCKET
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
```

Use a dedicated bucket-scoped R2 credential with the least privilege Cloudflare supports. The publisher does not need to delete objects.

Only the package publisher receives the private signing subkey. Package publication and key maintenance receive R2 credentials through separate protected environments, and key maintenance receives only public-key fingerprints. Fetch, validation and smoke jobs receive neither signing nor R2 credentials.

Automatic dispatch from `vcache-packaging` may be added later if manual publication becomes a recurring nuisance. It would require a narrowly scoped dispatch credential but no access to the signing key or R2. Do not add polling, a publication ledger or a state branch.

## Immutable package objects

Package objects are immutable by object key in both formats. Mutable repository indexes are rebuilt from verified local bytes and uploaded after all referenced package objects exist.

For a Debian package object:

- if absent, upload it with its producer SHA-256 in object metadata;
- if present with the same producer SHA-256, leave it untouched; and
- if present with a different or missing producer SHA-256, fail and request a package-revision bump.

For an RPM package object:

- sign a copied source RPM only when the object does not exist;
- store its producer SHA-256, signed SHA-256, primary fingerprint and signing-subkey fingerprint as object metadata;
- if the source SHA-256 already exists at that key, download the first signed object, verify its stored digest and signature against the current public certificate, and use those exact bytes for `createrepo_c`; and
- if the source SHA-256 differs or the existing signature is no longer valid, fail and request a package-revision bump.

Reusing the first signed RPM makes a retry idempotent despite OpenPGP signature timestamps. A subkey change does not rewrite old RPMs that remain valid under the current public certificate.

Never use `sync --delete` and never overwrite a package payload.

## APT route

APT publication leaves `.deb` payloads unchanged and signs the repository metadata.

For one verified Debian-family release, `publish-apt.sh` must:

1. Enforce the immutable package-object rule.
2. Build a fresh single-architecture `reprepro` tree for the manifest's family and target.
3. Use `Codename: stable`, `Suite: stable`, `Components: main`, the manifest architecture and `SignWith` set to the active signing subkey.
4. Require `Release`, `Release.gpg` and `InRelease` and verify them locally against the checked-in public certificate.
5. Generate a deb822 client file for the exact family and target.
6. Upload package objects before repository metadata, with `InRelease` last and the client file after it.

The generated client file is equivalent to:

```text
Types: deb
URIs: <REPOSITORY_PUBLIC_URL>/apt/<family>/<target>
Suites: stable
Components: main
Architectures: <manifest-architecture>
Signed-By: /etc/apt/keyrings/vcache-archive-keyring.asc
```

Publish it as:

```text
vinyl-cache/apt/<family>/<target>/vcache-<family>.sources
```

The README gives the key URL, expected full primary fingerprint and exact installation commands. It never uses `apt-key`, `trusted=yes` or an insecure APT option.

The disposable integration fixture must prove the exact `SignWith` selector accepted by the pinned Debian 13 `reprepro`/GPGME combination and require the resulting `InRelease` issuer fingerprint to equal `REPOSITORY_GPG_SIGNING_SUBKEY_FINGERPRINT`.

## Signed RPM route

RPM publication signs copied package payloads and repository metadata. There is no unsigned repository mode.

For one verified EL10 release, `publish-rpm.sh` must:

1. Apply the immutable RPM rule above, signing only absent objects with `rpmsign --addsign` in an AlmaLinux 10 utility container.
2. Import the public certificate into a temporary RPM database and require `rpmkeys --checksig --verbose` to report a valid signature and digests for every RPM.
3. Place the exact signed or reused RPM bytes under a fresh `Packages/` directory.
4. Run `createrepo_c` over the complete current package set.
5. Create `repodata/repomd.xml.asc` as an armored detached signature and verify it locally.
6. Generate one family-specific `.repo` file.
7. Upload package objects before metadata, then upload `repomd.xml`, its detached signature and the `.repo` file in that order.

The generated repository file is equivalent to:

```ini
[vcache-<family>]
name=Current <family> packages from vcache-packaging
baseurl=<REPOSITORY_PUBLIC_URL>/rpm/<family>/el10-$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=<REPOSITORY_PUBLIC_URL>/vcache-archive-keyring.asc
sslverify=1
metadata_expire=3600
```

Publish it as:

```text
vinyl-cache/rpm/<family>/vcache-<family>.repo
```

AlmaLinux 10 currently ships DNF 4.20.0 and RPM 4.19.1.1. The smoke job records `dnf --version` so a future base-image change triggers a deliberate compatibility check rather than an assumption that DNF4 remains forever.

An AlmaLinux 10 proof on 2026-08-12 signed and verified a real project RPM, generated and signed repository metadata, and completed DNF metadata verification with both GPG checks enabled.

## R2 and cache policy

Production clients use a custom domain. The rate-limited `r2.dev` endpoint is for development only and is not an advertised repository URL.

Configure Cloudflare cache rules for both APT and RPM prefixes:

- package payloads under APT `pool/` and RPM `Packages/` are immutable and receive `Cache-Control: public,max-age=31536000,immutable`;
- signed indexes, compressed metadata, public keys, `.sources` and `.repo` files receive `Cache-Control: no-store` and match an explicit cache-bypass rule; and
- no Edge TTL rule may override those origin policies.

The explicit metadata bypass matters because compressed `.gz`, `.bz2` and `.zst` files are among Cloudflare's default cached extensions.

DNF's local metadata cache is separate from HTTP caching. `metadata_expire=3600` limits discovery delay for a new current release; immutable package objects ensure stale metadata still references valid bytes.

Build and verify a complete tree before changing R2. Upload packages first, ordinary generated metadata next, and the signed top-level metadata last. There is no atomic multi-object swap: a client can observe a transient `repomd.xml`/signature mismatch and must fail closed and retry. Stale signed metadata continues to reference immutable bytes.

## Client smoke tests

Repository smoke tests prove distribution, not builds. The producer has already proved that each package installs and its VMODs load before the source release exists.

APT smoke jobs use the target's native architecture, download the public key, require its full primary fingerprint, place it under `/etc/apt/keyrings`, install the generated deb822 source, run `apt-get update` without insecure flags, and install every package name derived from the verified `.deb` metadata.

DNF smoke jobs use clean native EL10 x86_64 and aarch64 containers, install the generated family `.repo`, assert both GPG checks, TLS verification and the metadata expiry, run `dnf makecache` without weakening overrides, and install every package name derived from the verified RPM metadata.

Do not duplicate catalog-aware VCL load tests in the sibling. Signing changes RPM headers, not payload files, and the source release could not exist without the producer's install/load proof.

## Failure tests

Host-safe tests check the release schema, strict asset/checksum matching, route allow-list, workflow permissions, secret isolation, key fingerprints, upload order, cache policy and absence of insecure options.

Disposable-container tests use a temporary archive key, one real `.deb` and one real `.rpm`. In addition to the successful path, they must prove at least:

- a changed source package at an existing package identity is rejected with a package-revision error;
- an identical RPM source reuses the first signed bytes;
- a tampered RPM cannot be installed;
- `repomd.xml.asc` signed by the wrong key makes DNF metadata refresh fail;
- a corrupted `InRelease` makes `apt-get update` fail;
- a missing attestation or wrong manifest commit, target, architecture, signer workflow, source ref or attestation identity fails before the signing job;
- an expired or revoked signing subkey cannot publish;
- stale DNF metadata remains usable after an ordinary revision update and refreshes to the new revision; and
- unsafe, duplicate or path-containing asset names are rejected.

These fixtures do not contact R2. Static forbidden-string checks may supplement them but do not replace behavioural failure tests.

## Effect on the current APT branch

The `feat/signed-apt-r2-repository` branch proves that signed APT publication is feasible, but its publisher, R2 settings, signing secret and client smoke workflow are on the wrong side of the boundary.

If this design is accepted:

1. Do not merge that branch as it stands.
2. Create `vcache-repository` and adapt only the useful format-specific publication logic.
3. Keep `vcache-packaging` at the green GitHub Release boundary, adding only the package revision, manifest, checksums, attestations and normative contract changes described here.
4. Keep repository user instructions and client smoke tests in the sibling.
5. Retain GitHub Releases as the checksummed fallback when R2 publication fails.

The earlier in-repository APT/RPM plan is superseded by this document.

## Deliberately absent

- no raw Actions-artifact input;
- no source builds, package recipes or catalog import in the sibling;
- no unsigned APT metadata, RPM payload or RPM metadata path;
- no cumulative archive, snapshot path or historic-installability promise;
- no source-package repository;
- no distribution targets beyond the current Debian 13, Ubuntu 26.04 and EL10 pairs;
- no updateinfo, comps, modules, delta RPMs or mirror lists;
- no per-package counter or distributor-owned revision ledger;
- no automated key rollover or keyring package;
- no Worker, custom repository server or managed repository service;
- no deletion, retention, rollback, polling or publication ledger; and
- no automated cross-repository trigger until manual dispatch is demonstrably burdensome.

## Size expectation

The sibling should remain roughly 650–1,100 code lines across fetch, key maintenance, APT, RPM, smoke, workflow and tests. `vcache-packaging` gains only the revision, manifest and attestation handoff and should lose all repository-specific code from the prototype branch.

If the sibling needs engine versions, VMOD IDs, dependency expressions, build scripts or package recipes, the boundary has failed and the design must be reconsidered.

## Definition of done

This split is complete when:

- the producer's normative scope, version and release-asset contracts have landed;
- `vcache-packaging` produces a checksummed and attested green GitHub Release without GPG or R2 credentials;
- the sibling rejects a wrong source repository, workflow, ref, commit, target, architecture, revision, asset set, checksum or attestation before signing;
- publishing one family cannot remove the other family from its advertised roots;
- publishing revision N+1 leaves stale revision N metadata able to fetch its immutable package bytes unless an emergency key revocation intentionally invalidated them;
- a retry of an unchanged RPM release reuses the first signed RPM bytes;
- changed bytes at an existing package identity fail with a package-revision error;
- the offline primary, signing subkey, backup, revocation certificate, expiry policy and compromise procedure exist before the public key is commissioned;
- each published APT root has locally verified signed metadata and a working deb822 client file;
- each published RPM and `repomd.xml` has a locally verified signature and a family-specific `.repo` with both GPG checks enabled;
- clean native clients install every package advertised by each published root;
- tampered packages and metadata fail closed in container tests;
- the production custom domain and cache rules match the immutable-payload/mutable-metadata policy;
- a failed distributor run leaves the source GitHub Release and previously published repository usable; and
- no engine version string, VMOD ID or dependency expression exists in the sibling checkout.

## References

- [Cloudflare's R2 APT/YUM walkthrough](https://blog.cloudflare.com/using-cloudflare-r2-as-an-apt-yum-repository/)
- [Cloudflare's current package publisher](https://raw.githubusercontent.com/cloudflare/cloudflared/master/release_pkgs.py)
- [GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
- [`gh attestation verify`](https://cli.github.com/manual/gh_attestation_verify)
- [GitHub environments and protected secrets](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [RPM `rpmsign` manual](https://rpm.org/docs/6.1.x/man/rpmsign.1)
- [RHEL 10 packaging and distributing software](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/packaging_and_distributing_software/)
- [DNF configuration reference](https://dnf.readthedocs.io/en/latest/conf_ref.html)
- [Debian deb822 source configuration](https://manpages.debian.org/trixie/apt/sources.list.5.en.html)
- [R2 public buckets and custom domains](https://developers.cloudflare.com/r2/buckets/public-buckets/)
- [R2 consistency and custom-domain caching](https://developers.cloudflare.com/r2/reference/consistency/)
- [Cloudflare default cache behaviour](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/)
