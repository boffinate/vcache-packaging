# Signed package repository plan

Status: simplified proposal for a sibling repository; not yet implemented. Date: 2026-08-12.

## Decision

Create a sibling repository, provisionally named `vcache-repository`, which turns a green GitHub Release from `vcache-packaging` into signed APT and DNF repositories on Cloudflare R2.

`vcache-packaging` remains the producer. It builds native packages, installs and load-tests them, applies the release gate, writes `SHA256SUMS`, and publishes the unsigned packages as a GitHub Release.

`vcache-repository` is the distributor. It downloads one completed release, verifies it, signs what APT and RPM require, creates repository metadata, uploads it to R2, and tests installation through the public repository.

The distributor consumes GitHub Releases, not raw Actions artifacts. GitHub Releases remain the checksummed fallback if repository publication fails.

## Boundary

The following stay in `vcache-packaging`:

- the catalog and compatibility matrix;
- source acquisition, build containers and package recipes;
- native package construction and version assignment;
- package installation and VCL load checks;
- the per-`(engine, target)` release gate; and
- GitHub Releases containing the complete package set and `SHA256SUMS`.

Package construction stays with the build proof. Moving it would force the distributor to import the catalog, dependency rules and build logic, defeating the split.

The following belong only in `vcache-repository`:

- the archive signing key and R2 credentials;
- APT metadata generation and signing;
- RPM payload and metadata signing;
- repository layout and publication order;
- public key and client configuration; and
- clean-client APT and DNF smoke tests.

The sibling contains no source pins, VMOD catalog, compatibility matrix, package recipes or build scripts.

## Producer contract changes

Amend `SCOPE.md` and `DESIGN.md` in `vcache-packaging` before implementing the sibling.

`SCOPE.md` should continue to exclude package signing and repository publication from this repository, while naming green GitHub Releases as the handoff to the external distributor. Remove the suggestion that a managed repository service is the expected next step.

`DESIGN.md` must:

- replace decision 3 with this producer/distributor boundary;
- define the release-asset interface below;
- add the package revision to the version contract; and
- state that replacing a GitHub Release with changed package bytes requires a new package revision before repository publication.

No signing key, R2 setting or repository publisher belongs in `vcache-packaging`.

## Package revision

Add one quoted string `package_revision: "1"` to every package-enabled release engine. It must match `[1-9][0-9]*` and applies to the engine package and every VMOD in that engine's release set. Leading zeroes are invalid.

Bump it whenever a rebuild intended for repository publication could change package bytes without changing the upstream version. This includes packaging fixes, changed moving refs and non-reproducible rebuilds.

The version rules become:

```text
Debian engine: <engine-version>-<package-revision>
Debian VMOD:   <upstream-version>-1~<family-marker><engine-version>.<package-revision>
RPM engine:    <engine-version>-<package-revision>%{?dist}
RPM VMOD:      <upstream-version>-1.<family-marker><engine-version>.<package-revision>%{?dist}
```

These are final package versions. The existing RPM templates append `%{?dist}`, so the renderer supplies the `Release` value without that suffix and it appears exactly once.

Putting the package revision after the engine marker makes both required orderings monotonic: a same-engine revision bump sorts higher, and a newer engine at revision 1 sorts higher than an older engine at any revision. Producer self-tests must cover both comparisons in Debian and RPM version semantics.

One revision for the whole set preserves the exact engine dependencies and makes a revision bump an all-package rebuild. Every VMOD's exact engine dependency uses the matching engine package revision. There is no per-package counter and no counter in the sibling.

Package object keys are immutable. A collision with different package contents fails with a request to bump `package_revision`; the distributor never overwrites the object.

## Release handoff

The source tag remains:

```text
<engine-id>-<target-id>
```

The release must be non-draft and non-prerelease. Its assets are exactly the complete `.deb` or `.rpm` package set for the green pair plus one `SHA256SUMS`.

`SHA256SUMS` covers every package and not itself. Each line is a lowercase 64-character digest, two spaces, and a basename with no slash. Its filename set must exactly match the package assets.

The public source repository is fixed in the sibling workflow. The distributor resolves one release, records its release ID and target commit, and downloads every asset by asset ID into a new temporary directory. It then:

1. enforces the exact asset and checksum contract;
2. verifies every checksum;
3. matches the tag suffix to one allowed target;
4. inspects native package metadata and requires one format, the target architecture, one engine family and one package revision; and
5. completes validation before signing credentials are available or R2 is changed.

`SHA256SUMS` detects corruption and release mixing. It is not provenance: write access to the producer repository and release workflow is part of the trust boundary. V1 adds no second manifest, attestation API or cross-repository credential.

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

A fresh tree from one release replaces the index for that family and target. A Vinyl publication cannot unlist Varnish, and the reverse is also true. A newer engine version in the same family replaces the older version in the current channel.

Old package objects remain in R2 but are not promised to stay indexed. They are storage residue, not an archive, rollback service or ledger. Do not download and merge the previous repository tree.

`REPOSITORY_PUBLIC_URL` is the production custom-domain URL corresponding to the `vinyl-cache` prefix. The public key is always:

```text
vinyl-cache/vcache-archive-keyring.asc
```

## Sibling shape

Keep the sibling small:

```text
README.md
SCOPE.md
DESIGN.md
routes.tsv
keys/vcache-archive-keyring.asc
scripts/fetch-release.sh
scripts/publish-apt.sh
scripts/publish-rpm.sh
scripts/smoke-apt.sh
scripts/smoke-rpm.sh
tools/selftest.py
.github/workflows/publish.yml
```

`routes.tsv` is a distributor-owned allow-list. It contains only the target ID, format, package architecture, native container image and Docker platform. Package files remain authoritative for package identity, family, version and revision. Adding a target is deliberately a two-repository change: the producer learns to build it, and the distributor separately chooses to advertise it.

## Signing key

Use one dedicated archive key, not a maintainer's personal key. Keep its private key only as a secret in the protected publishing environment. Keep an encrypted offline backup and a revocation certificate outside GitHub.

The sibling checks in the public certificate and records its full primary fingerprint. Every run imports the private key into a temporary mode-0700 `GNUPGHOME`, requires exactly that primary fingerprint and signing capability, and removes the directory on exit.

The public key object is written only when absent. If it already exists, the publisher downloads it and requires the same bytes and fingerprint as the checked-in certificate.

Keeping the dedicated archive key in protected CI is an explicit simplicity trade-off. An offline-primary/online-subkey hierarchy would make recovery easier after CI compromise, but it would also introduce the key-lifecycle machinery deliberately omitted from v1.

V1 has no subkey lifecycle, automated key workflow, rollover or keyring package. Replacing or revoking the archive key is a manual trust-root migration and requires a new design decision and client instructions. This limitation is recorded before commissioning the key rather than hidden behind incomplete rotation machinery.

## Workflow and secrets

Start with one manually dispatched `publish.yml`. Releases happen a few times a year, so an automatic cross-repository trigger is not justified yet.

The workflow must:

1. run host-safe self-tests;
2. fetch and verify `source_tag` in a job with no signing or R2 secret;
3. pass the verified files through one internal workflow artifact;
4. enter a `main`-only protected environment that requires a reviewer before releasing secrets;
5. recheck the package checksums after the cross-job transfer;
6. build and verify the complete local repository tree;
7. upload under one repository-wide, non-cancelling concurrency group; and
8. run a clean native-client smoke job through the public URL.

Pin third-party actions to full commit SHAs. No pull-request job may enter the publishing environment.

The protected environment holds only:

```text
REPOSITORY_GPG_PRIVATE_KEY_B64
REPOSITORY_GPG_FINGERPRINT
REPOSITORY_PUBLIC_URL
R2_ACCOUNT_ID
R2_BUCKET
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
```

Use a dedicated bucket-scoped R2 credential. The publisher needs no delete permission. Fetch, self-test and smoke jobs receive neither private signing material nor R2 credentials.

## Immutable payload retries

Object keys are scoped by format, family and target. APT payloads use the relative `pool/` path produced by the fresh `reprepro` tree:

```text
vinyl-cache/apt/<family>/<target>/<pool-relative-path>
```

RPM payloads use:

```text
vinyl-cache/rpm/<family>/<target>/Packages/<package-filename>
```

The package filename contains its version, revision and architecture. Collision checks use the complete object key, so packages cannot be reused across format, family or target roots.

For a Debian package object:

- upload it if absent;
- if present, download it and require its whole-file SHA-256 to equal `SHA256SUMS`, then reuse it; and
- otherwise fail and request a package-revision bump.

Signing changes an RPM's signature header, so its signed bytes cannot be compared directly with the unsigned release asset. For each RPM, record the NEVRA, `SHA256HEADER` and `PAYLOADDIGEST` before signing. If the object:

- is absent, sign a copy, verify it and upload it;
- is present, download it, verify its archive-key signature and require the same NEVRA, `SHA256HEADER` and `PAYLOADDIGEST`, then reuse those exact signed bytes; or
- differs, fail and request a package-revision bump.

An AlmaLinux 10 proof confirmed that `SHA256HEADER` and `PAYLOADDIGEST` remain unchanged when `rpmsign` adds the signature header. This makes retries idempotent without R2 object metadata or a publication ledger.

Never use `sync --delete` and never overwrite a package payload.

## APT publication

APT leaves `.deb` payloads unchanged and signs repository metadata.

For one verified Debian-family release, `publish-apt.sh` must:

1. apply the immutable payload rule;
2. build a fresh single-architecture `reprepro` tree for the family and target;
3. use `Codename: stable`, `Suite: stable`, `Components: main`, the package architecture and `SignWith` set to the archive fingerprint;
4. require and locally verify `Release`, `Release.gpg` and `InRelease`; and
5. upload packages before generated metadata, with `InRelease` last.

The README provides a deb822 source equivalent to:

```text
Types: deb
URIs: <REPOSITORY_PUBLIC_URL>/apt/<family>/<target>
Suites: stable
Components: main
Architectures: <package-architecture>
Signed-By: /etc/apt/keyrings/vcache-archive-keyring.asc
```

It gives the key URL, expected full fingerprint and exact installation commands. It never uses `apt-key`, `trusted=yes` or an insecure APT option.

## RPM publication

RPM publication signs copied package payloads and repository metadata. There is no unsigned mode.

For one verified EL10 release, `publish-rpm.sh` must:

1. apply the immutable RPM rule, signing absent objects with `rpmsign --addsign` in an AlmaLinux 10 utility container;
2. import the public key into a temporary RPM database and require `rpmkeys --checksig --verbose` to verify every RPM;
3. place the exact signed or reused bytes under a fresh `Packages/` directory;
4. run `createrepo_c` over the complete current package set;
5. create and locally verify an armored detached `repodata/repomd.xml.asc`; and
6. upload packages before metadata, then `repomd.xml`, its signature and the client file.

Publish one family-specific client file equivalent to:

```ini
[vcache-<family>]
name=Current <family> packages from vcache-packaging
baseurl=<REPOSITORY_PUBLIC_URL>/rpm/<family>/el10-$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=<REPOSITORY_PUBLIC_URL>/vcache-archive-keyring.asc
sslverify=1
```

Its object path is `vinyl-cache/rpm/<family>/vcache-<family>.repo`.

A disposable AlmaLinux 10 container reported DNF 4.20.0 on 2026-08-12, so the initial proof uses DNF4. The native smoke records `dnf --version`; a future DNF implementation change requires the signature checks to be reproved rather than assumed compatible.

## R2 publication

Production clients use a custom domain. The rate-limited `r2.dev` endpoint is for development only.

For v1, configure one Cloudflare cache-bypass rule for the entire `vinyl-cache/` prefix. Do not add extension-specific TTLs or try to optimise cache hit rates before the repository works reliably.

Build and verify a complete local tree before changing R2. Upload the public key if needed, package payloads next, ordinary generated metadata next, and signed top-level metadata last. There is no atomic multi-object swap, so clients must fail closed and retry a transient metadata/signature mismatch. Old signed metadata remains safe because referenced payload objects are immutable.

## Tests

Host-safe tests cover strict asset/checksum parsing, the route allow-list, secret isolation, the protected environment, upload ordering and absence of insecure client options.

Disposable-container tests use a temporary archive key, one real `.deb` and one real `.rpm`. They prove:

- authenticated APT update and installation succeeds;
- authenticated DNF refresh and installation succeeds with both GPG checks enabled;
- changed contents at an existing package identity are rejected;
- corrupted `InRelease` is rejected by APT; and
- unsigned or tampered RPM payloads and `repomd.xml` are rejected by DNF.

The post-publication smoke job uses the target's native platform, verifies the downloaded public-key fingerprint, configures the signed repository without insecure options, and installs every package name derived from native package metadata.

Do not duplicate catalog-aware VCL load tests in the sibling. The source release cannot exist without the producer's install and load proof.

## Deliberately absent

- no raw Actions-artifact input;
- no build, package recipe or catalog import in the sibling;
- no extra release manifest or provenance-attestation machinery;
- no unsigned APT metadata, RPM payload or RPM metadata path;
- no cumulative archive, snapshots or historic-installability promise;
- no source-package repository;
- no updateinfo, comps, modules, delta RPMs or mirror list;
- no R2 object-metadata ledger or distributor-owned revision counter;
- no automated key maintenance, rollover or keyring package;
- no Worker, server or managed repository service;
- no delete, retention, rollback, polling or publication ledger;
- no fine-grained CDN cache policy; and
- no automatic cross-repository trigger until manual publication becomes burdensome.

## Size limit

The original `vcache-packaging` baseline at commit `a895af3` is 4,729 `cloc` code lines, so the requested 50% growth ceiling is 2,364 added code lines. The producer-side revision and contract work should add fewer than 200. Check the final producer diff against the baseline; do not add a LOC-counting workflow.

Target 400–700 nonblank code lines in the sibling across fetch, APT, RPM, smoke, workflow and tests. This is a review budget, not an excuse to omit authentication or fail-closed checks.

If the sibling needs an engine version, VMOD ID, dependency expression, source pin or package recipe, the boundary has failed.

## Definition of done

The split is complete when:

- the producer's scope, version and release-asset contracts have landed;
- `vcache-packaging` publishes a complete checksummed GitHub Release without GPG or R2 credentials and remains below the 50% growth ceiling;
- the sibling rejects a wrong asset set, checksum, target, architecture, family or revision before signing;
- publishing one family cannot unlist another family;
- changed contents at an existing package identity fail with a package-revision error;
- an unchanged retry reuses existing `.deb` and signed `.rpm` bytes without an object-metadata ledger;
- every APT root has locally verified signed metadata;
- every RPM and `repomd.xml` has a locally verified signature;
- clean native clients install every advertised package with signature checking enabled;
- corrupt packages and metadata fail closed;
- the dedicated archive key, offline backup, revocation certificate and protected environment exist before publication;
- a failed distributor run leaves the source GitHub Release and previous signed repository usable; and
- no engine version string, VMOD ID or dependency expression exists in the sibling checkout.

## References

- [Cloudflare's R2 APT/YUM walkthrough](https://blog.cloudflare.com/using-cloudflare-r2-as-an-apt-yum-repository/)
- [Cloudflare's package publisher](https://raw.githubusercontent.com/cloudflare/cloudflared/master/release_pkgs.py)
- [GitHub environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [RPM `rpmsign` manual](https://rpm.org/docs/6.1.x/man/rpmsign.1)
- [RHEL 10 packaging and distributing software](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/packaging_and_distributing_software/)
- [Debian deb822 source configuration](https://manpages.debian.org/trixie/apt/sources.list.5.en.html)
- [R2 public buckets and custom domains](https://developers.cloudflare.com/r2/buckets/public-buckets/)
- [R2 consistency and custom-domain caching](https://developers.cloudflare.com/r2/reference/consistency/)
