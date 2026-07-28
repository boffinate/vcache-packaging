# Project scope

Status: Normative

Last updated: 2026-07-28

## Purpose

`vcache-packaging` exists to build a particular set of native packages, test that those packages work with selected Vinyl Cache and Varnish Cache releases, and publish artifacts that people can install directly or through narrowly focused APT and RPM repositories.

This is a packaging and compatibility-testing project with a small publication surface. It is not a Linux or BSD distribution, a general package-building service, or a package-distribution infrastructure platform.

This document is the authority on project scope. Older plans and design notes remain useful historical records, but their broader proposals do not expand the boundary described here.

## The package set

The package set consists of:

- Vinyl Cache runtime, development, and debugging packages where they are needed to provide a coherent installable combination;
- selected independently packaged VMODs, currently `libvmod-cachetag`;
- additional cache-server or VMOD packages only after the maintainer explicitly adds them to the project.

A repository survey, compatibility experiment, or possible future integration does not by itself add a package to the supported set.

The project supports only the operating-system releases, architectures, Vinyl Cache releases, and Varnish Cache releases named in its current build and test matrix. There is no implied support for adjacent versions or other targets.

## In scope

The following work is in scope when it applies to the selected package set:

- native package recipes and packaging metadata;
- clean-room package builds in containers or native buildroots;
- checks that native package managers can install, upgrade, refuse incompatible combinations, and remove the packages correctly;
- VMOD compile, load, smoke, and behaviour tests against explicitly selected Vinyl Cache or Varnish Cache inputs;
- release lanes for packages intended for users and trunk lanes that provide early warning of upstream compatibility changes;
- exact source, package revision, ABI, build-target, artifact-digest, and test-result provenance;
- the cohort registry and its validation tooling, kept only as complex as needed to describe the combinations this project actually builds;
- deterministic source-archive derivation and digest verification when a package build requires an archive;
- assembling checksums, compatibility metadata, release notes, and directly downloadable native packages on GitHub Releases;
- publishing the selected package set through narrowly scoped, managed APT and RPM repository services;
- the minimum upload, signing, channel, promotion, retention, and rollback integration needed to make those managed repositories safe to install from;
- install and upgrade testing through the actual managed repositories once they exist;
- diagnosing and fixing failures in the above work.

Directly downloadable `.deb`, `.rpm`, or other native artifacts are the initial delivery mechanism. A future managed service such as Packagecloud may expose the same narrowly selected packages as APT and RPM repositories so users can install and upgrade them normally.

The project may own publication policy and automation for those repositories. It should use the managed service’s storage, indexing, signing, delivery, and repository-management capabilities instead of building replacements.

## What a package claim means

A successful package claim is deliberately narrow. It means that a recorded package revision passed the recorded tests against the exact cache-server source or package revision and target named in the evidence.

It does not mean that:

- the package works with every release in the same major or minor series;
- an artifact built against project-provided Vinyl Cache works with a distribution-provided Vinyl or Varnish package;
- a previously tested result remains valid after any source, patch, build-profile, toolchain, or package revision changes;
- the project promises indefinite artifact availability, unattended upgrades, security maintenance, or a support SLA.

When an input changes, affected package evidence must be rebuilt or reset to pending. The registry exists to prevent broader claims, not to turn the project into a distribution catalog.

## Source and release policy

Release builds should identify upstream source by a human-meaningful release tag or version and record the resolved commit and archive digest as evidence. A moved tag, changed digest, or missing source must fail loudly and require a deliberate re-pin and rebuild.

Trunk compatibility jobs may follow an upstream branch because detecting change is their purpose. They must record the commit they actually tested.

CI may derive a deterministic archive from a source checkout and pass it to later jobs using a GitHub Actions artifact. That artifact is a temporary build intermediate, not a permanent source archive or a durability promise.

The project does not normally vendor upstream source releases or preserve every source revision it has built. It does not operate a lookaside cache, source mirror, or archival store. If an upstream ref or release disappears, a failed build is an acceptable and useful signal; the normal repair is to select the intended current source, rebuild it, and move its provenance and package evidence together.

Repositories controlled by the maintainer may remain mutable while they are under active development. Enabling immutable-release enforcement is a separate maintainer decision, not a prerequisite imposed by this project.

## Managed package repository boundary

Managed APT and RPM publication is in scope when it remains an extension of the selected package set. Appropriate work includes:

- configuring the chosen managed repository service;
- uploading only the package artifacts and metadata this project produces;
- separating experimental, candidate, and released packages where that distinction is needed;
- promoting or withdrawing a cohort as one tested unit;
- retaining enough previous package revisions for the upgrade and rollback behavior the project explicitly tests;
- documenting installation commands for the supported targets;
- testing installation, upgrade, incompatibility refusal, and removal through the repository.

The project should prefer provider features and small project-specific scripts. It should not build a repository server, signing service, storage system, mirror network, generalized promotion engine, or provider-independent distribution framework.

## Out of scope

The following are out of scope unless the maintainer explicitly changes this document:

- building or operating our own package-repository server, signing service, mirror network, CDN, or artifact-storage platform;
- designing generic repository publication, promotion, retention, or rollback machinery beyond the needs and API of the selected managed service;
- publishing unrelated packages or offering repository hosting as a service to other projects;
- expanding to Arch, FreeBSD, Alpine, or other repository formats without an explicit package-target decision;
- permanent hosting or guaranteed retention of upstream source archives or every package artifact;
- vendoring source archives as an availability strategy;
- designing around GitHub outages, deleted upstream history, organization-access changes, or other hosting failures beyond detecting them and failing clearly;
- acting as Debian, Fedora, Red Hat, FreeBSD Ports, or another distribution project;
- getting packages accepted into official distribution repositories;
- providing security monitoring, an advisory feed, embargo handling, backport maintenance, response targets, or a security SLA;
- supporting every distribution, architecture, cache-server release, VMOD, or storage backend;
- building a generic packaging platform, dependency graph scheduler, reverse-dependency service, or reusable multi-project release system for hypothetical future packages;
- preserving old cohorts indefinitely or guaranteeing an upgrade path from every experimental artifact;
- changing upstream repository governance, tag policy, release immutability, or source-retention policy;
- claiming compatibility with any combination that the project has not built, installed, and tested explicitly.

## Decision rule for future work

A proposed change belongs here only when it directly does at least one of the following for a package or target already selected by the maintainer:

1. builds the package;
2. proves or records its compatibility;
3. verifies native installation or package-manager transactions;
4. assembles or publishes the package and its release evidence, either directly or through the selected managed repository service;
5. fixes a failure in one of those paths.

Even then, choose the smallest mechanism that satisfies the current package set. Possible usefulness for future repositories is not enough reason to build an abstraction or service now.

Proposals involving mirrors, lookaside caches, custom signing services, archival storage, generalized repository orchestration, provider-independent distribution abstractions, or support operations are scope warnings. Stop and compare them with this document before designing or implementing them. Managed-service channel, promotion, retention, and rollback work remains in scope only to the extent required by this project’s selected packages and tested release process.

## Changing the scope

The project can add another package, target, or stronger delivery promise. That requires an explicit maintainer decision and an update to this document describing the additional build, test, publication, and maintenance responsibility.

Scope must not expand implicitly because a historical plan mentioned an end state, because another distribution uses a particular system, or because infrastructure might be useful later.
