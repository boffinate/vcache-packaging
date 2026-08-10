# Project scope

Status: Normative. This document controls what work belongs in this repository.

## Purpose

This project does three things, and only these three things:

1. **Builds** of Vinyl Cache and a selected set of VMODs that are ABI-compatible with each other, delivered as basic APT and RPM packages. The quality bar is deliberately "ondrej-grade": *you don't want to compile from source, so here are packages we compiled, and they are compatible with each other*. They are not distribution-quality packages and do not try to be.
2. **A compatibility matrix**: one colourful grid showing which VMODs build and load against which Vinyl Cache and Varnish Cache versions. A VMOD that fails is a red cell, and a red cell is a useful result, not an emergency. Red never blocks anything.
3. **Trunk CI**: the same matrix computed against the trunk of Vinyl Cache, Varnish Cache, and each VMOD, as an early warning of upstream drift.

## Ground rules

- **Hand-maintained catalog.** Which engines exist, which versions are supported, and which VMOD ref maps to which engine series are declared by hand in YAML (`engines.yml`, `vmods/*.yml`). Automating that detection is explicitly out of scope for now; there are only a couple of engine releases per year.
- **We carry nothing on behalf of upstreams.** No patches, no ported test suites, no vendored source, no forked packaging. If a VMOD does not build against an engine, the matrix says so in red. If we ever need to patch a VMOD, we fork its repository and point the catalog at the fork.
- **Red is information.** A build/test failure for a (VMOD, engine) cell is recorded and rendered; it never fails the CI run. Only infrastructure errors (the harness itself broke) fail a run.
- **Verification happens in containers**, never on the host. Host-safe tooling is Python 3 standard library only, so it runs anywhere without an install step.
- **The smallest mechanism wins.** No evidence ledgers, no per-artifact digest registries, no transaction matrices, no completeness gates, no auto-re-pin PRs, no fleet surveillance, no fault-injection harnesses. Every version pin has exactly one machine-readable home; anything a shell script needs is generated from it.

## Out of scope

Distribution-quality packaging (copyright audits, lintian/rpmlint gates, hardening inspection), package upgrade-transaction matrices, custom repository servers or signing services, archival of upstream sources, security response, and any per-VMOD carried content. A proposal that reintroduces one of these is a scope change and needs this file amended first.

## Publication

Packages are published as GitHub Release assets with checksums. A managed APT/RPM repository service (e.g. Packagecloud) may later serve the same packages; wiring that up is in scope when the maintainer decides to, and remains a thin publish step, not a platform.
