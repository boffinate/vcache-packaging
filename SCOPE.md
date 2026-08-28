# Project scope

Status: Normative. This document controls what work belongs in this repository.

## Purpose

This project does three things, and only these three things:

1. **Builds** of Vinyl Cache or Varnish Cache and selected ABI-compatible VMODs, delivered as basic APT and RPM packages. The quality bar is deliberately best-effort: *you don't want to compile from source, so here are packages we compiled, and they are compatible with each other*. They are convenience artifacts, not distribution-quality replacements.
2. **A compatibility matrix**: one colourful grid showing which VMODs build and load against which Vinyl Cache and Varnish Cache versions. A VMOD that fails is a red cell, and a red cell is a useful result, not an emergency. Red never blocks a matrix or build job.
3. **Trunk CI**: the same matrix computed against the trunk of Vinyl Cache, Varnish Cache, and each VMOD, as an early warning of upstream drift.

## Ground rules

- **Hand-maintained catalog.** Which engines exist, which versions are supported, and which VMOD ref maps to which engine series are declared by hand in YAML (`engines.yml`, `vmods/*.yml`). Automating that detection is explicitly out of scope for now; there are only a couple of engine releases per year.
- **We carry no VMOD changes on behalf of upstreams.** No VMOD patches, ported test suites, vendored source, or forked packaging. If a VMOD does not build against an engine, the matrix says so in red. If we ever need to patch a VMOD, we fork its repository and point the catalog at the fork. Engine packages carry the service assets described below because they must be usable after installation.
- **Red is information.** A build/test failure for a (VMOD, engine) cell is recorded and rendered; it never fails a matrix or build job. The release-only completeness gate is the exception: each package-enabled `(engine, target)` pair publishes only when its engine and every package-eligible VMOD cell pass. A failed Vinyl pair never blocks a Varnish pair, or the reverse.
- **Verification happens in containers**, never on the host. Host-safe tooling is Python 3 standard library only, so it runs anywhere without an install step.
- **The smallest mechanism wins.** No evidence ledgers, no per-artifact digest registries, no transaction matrices, no completeness machinery beyond the release-only pair gate, no auto-re-pin PRs, no fleet surveillance, no fault-injection harnesses. Every version pin has exactly one machine-readable home; anything a shell script needs is generated from it.
- **Promoted source identity is immutable.** Compatibility and trunk refs remain deliberately moving, but a source selection that can produce a published VMOD also names the full Git commit that its readable tag or branch must resolve to. This is a field on the existing catalog entry, not a second digest ledger.

## Out of scope

Distribution-quality packaging (copyright audits, lintian/rpmlint gates, hardening inspection), byte-equivalent or reproducible-build promises, distro replacement and upgrade-policy guarantees, package upgrade-transaction matrices, signing, custom repository servers, archival of upstream sources, security response, and any per-VMOD carried content. Service integration is deliberately narrow: the engine runtime packages install a default VCL, daemon account, systemd unit, and safe VCL reload helper derived from the official Varnish package. Broader service policy remains out of scope.

## Publication

Every green `(engine, target)` pair is published as a GitHub Release containing its complete package set and `SHA256SUMS`. That checksummed release is this repository's handoff to the external `vcache-repository` distributor; signing and repository publication stay out of scope here. It remains the fallback for direct installation with `apt install ./*.deb` / `dnf install ./*.rpm`, **not** `dpkg -i` / `rpm -i`: a VMOD package pins the engine by exact version, and only the solver enforces that pin. `dpkg -i` will happily install a newer engine over an ABI-pinned VMOD, silently and with exit 0 (see DESIGN.md decision 10).

An upstream-Varnish overlay is being evaluated separately. Its workflow may download the official signed Varnish cohort, build an additional VMOD against the installed upstream development package, and upload evidence artifacts. It does not publish stable packages or make the external repository part of this project's release contract. Promotion would require an explicit scope decision covering provider ownership, ABI dependencies, repository availability, upgrades and security-response latency.
