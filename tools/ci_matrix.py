#!/usr/bin/env python3
"""VMOD CI catalog, matrix expansion, and result reconciliation.

This is the tooling half of Phases 1 and 2 of
docs/20260728_0833_plan_vmod-matrix-failure-isolation.md. It has five jobs:

  * list the selected VMOD manifests without fetching or parsing their sources
    (``list-vmods``, ``check-catalog``);
  * expand the explicitly declared lanes of one VMOD for one workflow tier
    (``validate-vmod``, ``expand``);
  * derive the shared engine rows those lanes need, and describe and verify the
    artifact each one produces (``engine-matrix``, ``engine-metadata``,
    ``verify-engine-metadata``);
  * emit the expected engine / invocation / source / lane-row ledger
    (``ledger``);
  * validate, reconcile, and summarize machine-readable result records
    (``record``, ``summarize-vmod``, ``reconcile``).

The point of the split is failure isolation. Discovery derives a VMOD's id from
its checked-in file name, so a malformed manifest costs exactly one matrix copy
rather than the whole run; the collector rebuilds the expected ledger from the
checked-in manifests independently of what any job actually uploaded, so a row
that never ran is reported as missing execution evidence instead of silently
disappearing from the summary.

Standard library only, like the rest of tools/: it must run on the host and in
any buildroot with no install step. It builds and tests nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manifest as manifest_mod  # noqa: E402
import yaml_subset  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = "registry/vmods"
SCHEMA = "vmod-ci/v1"
RESULT_SCHEMA = "vmod-ci-result/v1"
ENGINE_SCHEMA = "engine-artifact/v1"

# Workflow tiers. `ci` and `release` exist today; `nightly` and `trunk` are
# declared so a manifest can name them, but their workflows still run their own
# graphs until Phase 4 of the plan migrates them.
TIERS = ["ci", "nightly", "release", "trunk"]

# The packaging adapters a manifest may name. `cachetag` is the audited
# upstream-owned recipe in the libvmod-cachetag repository; `autotools` is the
# default generated-recipe adapter, whose reviewed data lives in
# recipes/vmods/adapters/autotools/ and whose recipes tools/vmod_recipe.py
# renders. An adapter name here is a promise that checked-in, reviewed code and
# data exist for it -- never a free-text field.
ADAPTERS = ["autotools", "cachetag"]

LANE_KINDS = ["package", "source-harness"]

SOURCE_CHANNELS = ["release", "trunk"]

# The selected engine inputs. Together with the package lanes that name them,
# this table is the expected engine-row ledger the plan refers to: Phase 2
# builds the Vinyl runtime, development and debug packages once per
# (engine, target) row and every VMOD package row consumes the resulting
# artifact instead of rebuilding the engine inside itself.
#
# `vinyl_track` is the VINYL_TRACK value the lane pin files dispatch on, and is
# therefore the whole of what selects a Vinyl source: recipes/debian-13/pins.env
# and recipes/el9/cohort.env carry the rest. `builds_packages` marks the engine
# inputs that have a native package lane at all -- `vinyl-trunk-head` is a
# moving source used only by the source harness, so it never contributes an
# engine package row and can never produce a publishable artifact.
ENGINES = {
    "vinyl-release": {"vinyl_track": "release", "pinned": True, "builds_packages": True},
    "vinyl-trunk-pinned": {"vinyl_track": "trunk", "pinned": True, "builds_packages": True},
    "vinyl-trunk-head": {"vinyl_track": "trunk", "pinned": False, "builds_packages": False},
}

# The selected package targets, and the facts a workflow needs about them that
# are not in a VMOD manifest because they belong to the target, not the VMOD.
# The VMOD timeouts are the ones the pre-Phase-1 ci.yml carried on its Debian
# and EL9 jobs; `engine_timeout_minutes` is the same budget for the engine half,
# which is the slower half of each lane.
#
# These two tables deliberately stay in the tool rather than moving into
# `registry/`. `registry/targets/<cohort>/<target>.yml` already means something
# else -- the recorded per-cohort compatibility evidence for a built cachetag
# package -- and a second, unrelated "target" concept in the same tree would be
# actively misleading. What is recorded here is workflow shape: a runner label,
# a job timeout, and a package family. None of it is a compatibility claim, none
# of it is evidence, and none of it is a resolved build input; the resolved
# build inputs live in the lane pin files and reach the ledger through the
# engine artifact metadata below. Revisit if a target ever gains inputs of its
# own that the registry must record.
TARGETS = {
    "debian-13-amd64": {
        "family": "deb",
        "runner": "ubuntu-latest",
        "timeout_minutes": 35,
        "engine_timeout_minutes": 35,
    },
    "el9-x86_64": {
        "family": "rpm",
        "runner": "ubuntu-latest",
        "timeout_minutes": 30,
        "engine_timeout_minutes": 30,
    },
}

# What the nightly tier adds to a package row's timeout, and only to a package
# row's: an engine row builds the same engine at every tier.
#
# A nightly row runs the synthetic mismatch fixture and the whole
# upgrade-transaction matrix after everything a ci row does -- sixteen throwaway
# scenario containers on Debian, nineteen on EL9, each installing a cohort from a
# local repository and running one real package-manager transaction. The figure
# is nightly-transactions.yml's own 180-minute budget for the same work, minus
# the engine half a row no longer builds, which is the engine budget in the table
# above. Like every other timeout here it is a "something has hung" guard rather
# than a target, and the first migrated nightly run is what will measure the real
# cost.
#
# One number rather than a per-target column because the two matrices are the
# same size to within three containers, and a second column would be two things
# to keep true where the evidence supports one.
NIGHTLY_TRANSACTION_MINUTES = 110


def target_timeout_minutes(target: str, tier: str) -> int:
    """A package row's job timeout for one target and tier."""
    minutes = TARGETS[target]["timeout_minutes"]
    if tier == "nightly":
        minutes += NIGHTLY_TRANSACTION_MINUTES
    return minutes

# The failure vocabulary from the plan's "Failure reporting" section. Every
# result record must carry one of these; an unknown status is a hard error
# rather than a free-text field, so the collector can never be handed a
# classification it does not understand.
STATUSES = [
    "failed_manifest_validation",
    "failed_source_checkout",
    "failed_source_digest",
    "failed_source_archive",
    "blocked_by_vmod_source",
    "failed_engine_build",
    "blocked_by_engine_artifact",
    "failed_package_build",
    # Generated-recipe VMODs only. A rendering failure is not a build failure:
    # nothing was compiled, the inputs are wrong, and the fix is in the
    # manifest, the overlay, the adapter or the generator rather than in the
    # source. Classifying it as failed_package_build would send whoever reads
    # the summary to the wrong place. The plan also requires a missing
    # generated recipe or generation record to be an explicit classified
    # failure rather than a silently empty row.
    "failed_recipe_generation",
    "failed_abi_or_hardening",
    "failed_lint",
    "failed_install_or_smoke",
    "failed_behavior",
    "failed_transactions",
    "missing_result_record",
    "failed_infrastructure",
    "passed",
    "not_selected",
]

# Statuses that do not make a run red: a pass, and a row this tier never asked
# for. Everything else is a failure of its row.
OK_STATUSES = {"passed", "not_selected"}

# Deliberate failure injection, used to prove the isolation properties without
# editing any build script. Defaults to `none` and every workflow that threads
# it makes it reachable only from workflow_dispatch.
INJECTIONS = [
    "none",
    "manifest",
    "source_checkout",
    "source_digest",
    "debian_build",
    "el9_build",
    "suppress_result",
    # Phase 2, the plan's verification case 6: one engine row fails to build,
    # and one engine row builds but never publishes its artifact. Both must
    # block only the VMOD rows that name that exact engine and target.
    "engine_build",
    "suppress_engine_artifact",
    # Phase 3, the generated-recipe VMOD. `recipe_generation` corrupts the
    # rendered recipe with an unresolved token, which is the plan's
    # verification case 4 and the only injection that has no cachetag analogue.
    "recipe_generation",
    "dict_source",
    "dict_build",
    # Wave 1, the third VMOD. Same two shapes from redis's side, so a run can
    # show any of the three VMODs failing while the other two complete; the
    # build one is aimed at the RPM family because dict's is aimed at the
    # Debian one, and a third copy of the same family would demonstrate
    # nothing the second did not.
    "redis_source",
    "redis_build",
    # The patch capability's own gate, and the lane half of the plan's
    # verification case 10. The generator already refuses a declared patch that
    # is missing or whose digest moved -- that is covered by
    # vmod_recipe_selftest -- so this proves the LANE refuses a rendered recipe
    # whose patch has gone away afterwards. The two families refuse it for
    # different reasons and both are worth seeing: dpkg-source rejects a 3.0
    # (quilt) series naming a file that is not there, and build-rpm.sh compares
    # the spec's Patch lines against the files rendered beside it.
    "patch_omission",
]

# Which VMOD each injection acts on. Before the second VMOD every injection
# implicitly acted on every VMOD, because there was only one; with two, an
# injection that hit both would prove nothing about isolation. Naming the
# target here rather than in a workflow expression keeps the tool, the
# workflow and the tests unable to disagree about which row is injected.
#
# The pairing is deliberate: cachetag and dict have a source failure, a
# package-build failure and a suppressed result each, so every case can be run
# from either side and the other VMOD's rows must complete regardless.
INJECTION_TARGET_VMOD = {
    "source_checkout": "cachetag",
    "source_digest": "cachetag",
    "debian_build": "cachetag",
    "el9_build": "cachetag",
    "suppress_result": "cachetag",
    # Scoped to cachetag since 2026-07-28. It was global -- every manifest
    # corrupted -- which made the plan's own claim untestable: valid_manifests()
    # promises that one broken manifest costs its own invocation and the engine
    # rows nothing else consumes, and nothing else. Corrupting both manifests
    # cannot demonstrate that, because there is no surviving VMOD to observe.
    # Scoping it makes the two-VMOD case the Phase 3 isolation demonstration it
    # was supposed to be: cachetag's ledger collapses to one
    # failed_manifest_validation row while dict's four rows run to completion.
    "manifest": "cachetag",
    "recipe_generation": "dict",
    "dict_source": "dict",
    "dict_build": "dict",
    "redis_source": "redis",
    "redis_build": "redis",
    # redis is the only VMOD with a declared patch, so this injection has
    # nowhere else it could act. If a second patched VMOD is ever selected the
    # choice becomes a real one and belongs here, not in a workflow expression.
    "patch_omission": "redis",
}

# The engine row both Phase 2 injections act on. It is a constant so the
# workflow condition, the documentation and the tests all name the same row;
# the workflow has to spell it out literally because YAML cannot call this
# module.
#
# `vinyl-release` on Debian, moved there 2026-07-29 from `vinyl-trunk-pinned`.
# The original choice predates the second VMOD and was made when every consumer
# row belonged to cachetag, so any engine row had exactly one consumer and the
# only property on show was that the three siblings survived.
#
# With more than one VMOD the rows are no longer interchangeable. Read off the
# ledger, as of Step 7 Wave 1 and its third VMOD:
#
#   engine/vinyl-trunk-pinned/debian-13-amd64  ->  1 consumer, cachetag's
#   engine/vinyl-release/debian-13-amd64       ->  3 consumers, ONE PER VMOD:
#                                                    target/cachetag/release/vinyl-release/debian-13-amd64
#                                                    target/dict/release/vinyl-release/debian-13-amd64
#                                                    target/redis/release/vinyl-release/debian-13-amd64
#
# Neither dict nor redis declares a `vinyl-trunk-pinned` lane -- Vinyl trunk
# emits no numeric version, and both of their build systems do arithmetic on it
# (acvmod.m4's modversion split for dict, VINYL_PREREQ for redis) -- which is
# why the trunk row has one consumer and will keep having one until that
# changes. The two exclusions have ONE root cause, which is worth knowing: the
# lane comes back for both VMODs at once, or for neither.
#
# Injecting the release row is therefore the only version of this case that
# demonstrates what the matrix plan asks for: one root cause blocking consumers
# in *different* VMODs, reported as that shared cause rather than as unrelated
# cancelled jobs. It is also the only live exercise of the generated-recipe
# lane's `blocked_by_engine_artifact` path, which the upstream-recipe lane has
# had since Phase 2 and `target-generated` has never once taken.
#
# The isolation property the original choice was after is unchanged and is now
# stronger, because the surviving set spans both VMODs too: three sibling engine
# rows, cachetag's four other rows and dict's EL9 row must all complete.
INJECT_ENGINE_ROW = ("vinyl-release", "debian-13-amd64")

ID_RE = r"^[a-z][a-z0-9-]*$"

# VMOD ids that would collide with a non-VMOD row kind in the artifact and row
# key namespaces. A VMOD literally called `engine` would mint
# `result-engine-<channel>-<engine>-<target>` artifact names alongside the
# engine rows' `result-engine-<engine>-<target>`, and the collector keys results
# by name. Reject the id rather than escaping the namespace: the cost is one
# forbidden word, and the alternative is a silent collision in the one place the
# whole reconciliation depends on being unambiguous.
RESERVED_VMOD_IDS = ["engine", "vmod"]

# Where a VMOD's source lives, and therefore how CI reaches it.
#
#   github  actions/checkout can fetch it; `repository` is an owner/name pair.
#   git     any other Git host; `clone_url` is an https:// URL and the lane
#           uses git directly.
#
# Until 2026-07-28 `repository` was the only field and its pattern happened to
# accept a `host/name` string as well as `owner/name`, so a non-GitHub upstream
# was representable only by accident and only by writing something into a field
# that means something else. Step 5's ruling 5 called that out: every viable
# second-VMOD candidate except one is off GitHub, so the distinction has to be
# in the schema rather than in a coincidence. REPOSITORY_RE is now what it
# always claimed to be -- a GitHub owner/name -- and a non-GitHub upstream says
# so and gives a clone URL.
SOURCE_HOSTS = ["github", "git"]
REPOSITORY_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$"
CLONE_URL_RE = r"^https://[A-Za-z0-9][A-Za-z0-9._-]*(?::[0-9]+)?/[A-Za-z0-9._/~-]+$"
ARCHIVE_URL_RE = r"^https://[^\s]+$"

# Which native recipe a VMOD is packaged from. Selected here, from trusted
# local data, and never discovered: the recipe-generation plan is explicit that
# newly found upstream packaging must not silently displace the recorded
# strategy, because that would change package contents and recipe provenance
# without a manifest decision.
#
#   upstream   the VMOD's own audited debian/ and rpm/ trees, substituted
#   generated  rendered by tools/vmod_recipe.py from the reviewed overlay
RECIPE_STRATEGIES = ["upstream", "generated"]
REF_RE = r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"
COMMIT_RE = r"^[0-9a-f]{40}$"
SHA256_RE = r"^[0-9a-f]{64}$"
# Two or more dot-separated numeric components. It was three exactly until
# 2026-07-28, which was a generalisation from cachetag's own versioning rather
# than a rule: vmod-dict releases as 1.7, and refusing to record that version
# would mean either declining a selected VMOD over a regex or writing a version
# its own AC_INIT does not agree with. Neither is acceptable, and the checks
# that matter -- the peeled commit, the archive digest, and the source
# cross-check against the VMOD's own configure.ac -- are unaffected.
VERSION_RE = r"^[0-9]+(?:\.[0-9]+)+$"


class CatalogError(Exception):
    """Raised when the catalog directory itself, not one manifest, is wrong."""


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


def _s(pattern: str, **kw) -> dict:
    node = {"type": "str", "pattern": pattern}
    node.update(kw)
    return node


def _enum(values, **kw) -> dict:
    node = {"type": "enum", "values": list(values)}
    node.update(kw)
    return node


def _map(fields: dict, **kw) -> dict:
    node = {"type": "map", "fields": fields}
    node.update(kw)
    return node


def _list(item: dict, min_len: int = 0, **kw) -> dict:
    node = {"type": "list", "item": item, "min_len": min_len}
    node.update(kw)
    return node


_SOURCE_FIELDS = {
    "ref": _s(REF_RE),
    "expected_commit": _s(COMMIT_RE, optional=True),
    "version": _s(VERSION_RE, optional=True),
    # Where the pinned archive is published, when upstream publishes one. Absent
    # means the lane derives the archive from the ref itself. It sits beside the
    # digest deliberately: URL and digest are one statement about one set of
    # bytes and splitting them across two files would let them drift.
    "archive_url": _s(ARCHIVE_URL_RE, optional=True),
    "archive_sha256": _s(SHA256_RE, optional=True),
    "publishable": _enum(["true", "false"]),
}

VMOD_SPEC = _map(
    {
        "schema": _enum([SCHEMA]),
        "id": _s(ID_RE),
        "source_host": _enum(SOURCE_HOSTS),
        "repository": _s(REPOSITORY_RE, optional=True),
        "clone_url": _s(CLONE_URL_RE, optional=True),
        "required": _enum(["true", "false"]),
        "adapter": _enum(ADAPTERS),
        "recipe": _enum(RECIPE_STRATEGIES),
        "sources": _map(
            {
                "release": _map(dict(_SOURCE_FIELDS), optional=True),
                "trunk": _map(dict(_SOURCE_FIELDS), optional=True),
            }
        ),
        "lanes": _list(
            _map(
                {
                    "kind": _enum(LANE_KINDS),
                    "source": _enum(SOURCE_CHANNELS),
                    "engine": _enum(sorted(ENGINES)),
                    "tiers": _list(_enum(TIERS), min_len=1),
                    "targets": _list(_s(r"^[a-z][a-z0-9._-]*$"), min_len=1, optional=True),
                }
            ),
            min_len=1,
        ),
    }
)


def load_vmod_manifest(path) -> dict:
    """Parse one VMOD manifest. Raises ManifestSyntaxError or OSError."""
    return yaml_subset.parse_file(path)


def validate_vmod_manifest(data: dict, path: str, discovery_id: str = None) -> list:
    """Return a list of error strings ([] means valid)."""
    errors = manifest_mod.schema_errors(VMOD_SPEC, data, path)
    if errors:
        return errors

    problems: list = []
    stem = Path(path).stem
    if data["id"] != stem:
        problems.append(f"id {data['id']!r} must equal the file name stem {stem!r}")
    if data["id"] in RESERVED_VMOD_IDS:
        problems.append(
            f"id {data['id']!r} is reserved: it names a row kind, and a VMOD using it would "
            f"produce artifact names that collide with them ({RESERVED_VMOD_IDS})"
        )
    if discovery_id is not None and data["id"] != discovery_id:
        problems.append(
            f"id {data['id']!r} does not match the discovery id {discovery_id!r} this "
            "invocation was started for"
        )

    # Exactly one upstream address, and it must be the one the declared host
    # can actually be reached at. A GitHub entry carrying a clone URL, or a
    # non-GitHub entry carrying an owner/name, is the ambiguity this pair of
    # fields exists to remove.
    host = data["source_host"]
    if host == "github":
        if not data.get("repository"):
            problems.append("source_host: github requires repository as <owner>/<name>")
        if data.get("clone_url"):
            problems.append(
                "clone_url: not used with source_host: github; the workflow checks a "
                "GitHub repository out by owner/name"
            )
    else:
        if not data.get("clone_url"):
            problems.append(f"source_host: {host} requires clone_url")
        if data.get("repository"):
            problems.append(
                f"repository: an <owner>/<name> pair is meaningful only on GitHub; "
                f"source_host is {host}, so give clone_url instead"
            )

    sources = data["sources"]
    if not sources:
        problems.append("sources: at least one source channel is required")
    for channel, source in sorted(sources.items()):
        pinned = _source_is_pinned(source)
        if source["publishable"] == "true" and not pinned:
            problems.append(
                f"sources.{channel}: publishable requires expected_commit, version and "
                "archive_sha256; a moving ref can never be published"
            )
        # An absent archive_url means the lane DERIVES the archive from the ref,
        # which is what the field's own comment says and what
        # scripts/ci/vmod-source-archive.sh exists for. That was unreachable
        # until Step 7 Wave 1: libvmod-redis publishes no release archive at
        # all, so requiring the URL here would have refused a selected VMOD over
        # a field it cannot honestly fill in.
        #
        # Nothing is weakened by dropping the requirement, because the two
        # cases are still told apart, just in the file that can see both halves:
        # the overlay declares `source.archive.method`, and vmod_recipe.py
        # cross-checks it against the presence of archive_url here --
        # upstream-release requires both and requires them equal,
        # derived-git-tag refuses either. A manifest that says nothing about
        # where its source is still fails, and now it fails with the two
        # declarations named rather than with one field missing.

    seen_rows: dict = {}
    for index, lane in enumerate(data["lanes"]):
        where = f"lanes[{index}]"
        channel = lane["source"]
        source = sources.get(channel)
        if source is None:
            problems.append(f"{where}: source {channel!r} is not declared in sources")
            continue
        targets = lane.get("targets")
        if lane["kind"] == "package":
            if not targets:
                problems.append(f"{where}: a package lane must name at least one target")
            if not _source_is_pinned(source):
                problems.append(
                    f"{where}: package lanes need a pinned source; sources.{channel} has no "
                    "expected_commit, version and archive_sha256"
                )
        else:
            if targets:
                problems.append(
                    f"{where}: a {lane['kind']!r} lane produces no native package and must not "
                    "name package targets"
                )
        for target in targets or []:
            if target not in TARGETS:
                problems.append(
                    f"{where}: target {target!r} is not a selected package target "
                    f"({sorted(TARGETS)})"
                )
        for target in targets or [None]:
            key = (lane["kind"], channel, lane["engine"], target)
            if key in seen_rows:
                problems.append(
                    f"{where}: duplicates the row already declared in lanes[{seen_rows[key]}]"
                )
            else:
                seen_rows[key] = index

    return [f"{path}: {p}" for p in problems]


def _source_is_pinned(source: dict) -> bool:
    return all(source.get(key) for key in ("expected_commit", "version", "archive_sha256"))


# ---------------------------------------------------------------------------
# Discovery: file names only, never manifest contents
# ---------------------------------------------------------------------------

MANIFEST_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*\.yml$")


def catalog_dir(repo_root=None) -> Path:
    return (Path(repo_root) if repo_root else REPO_ROOT) / CATALOG_DIR


def discover(repo_root=None) -> list:
    """The trusted discovery list: [{'id', 'manifest'}], from file names alone.

    Nothing here parses a manifest. That is the point: one malformed VMOD entry
    must become one failed matrix copy, not a failure to discover the other
    nine.
    """
    directory = catalog_dir(repo_root)
    if not directory.is_dir():
        raise CatalogError(f"{CATALOG_DIR}: missing VMOD catalog directory")
    entries = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            raise CatalogError(f"{CATALOG_DIR}/{path.name}: the catalog is flat; no subdirectories")
        if not MANIFEST_NAME_RE.match(path.name):
            raise CatalogError(
                f"{CATALOG_DIR}/{path.name}: file name must be <vmod-id>.yml with a lower-case, "
                "hyphenated id"
            )
        entries.append({"id": path.stem, "manifest": f"{CATALOG_DIR}/{path.name}"})
    if not entries:
        raise CatalogError(f"{CATALOG_DIR}: no VMOD manifests found")
    ids = [entry["id"] for entry in entries]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise CatalogError(f"{CATALOG_DIR}: duplicate VMOD ids {duplicates}")
    return entries


# ---------------------------------------------------------------------------
# Row keys and artifact names
# ---------------------------------------------------------------------------


def engine_row_key(engine: str, target: str) -> str:
    return f"engine/{engine}/{target}"


def invocation_row_key(vmod: str) -> str:
    return f"vmod/{vmod}"


def source_row_key(vmod: str, channel: str) -> str:
    return f"source/{vmod}/{channel}"


def target_row_key(vmod: str, channel: str, engine: str, target: str) -> str:
    return f"target/{vmod}/{channel}/{engine}/{target}"


def harness_row_key(vmod: str, channel: str, engine: str) -> str:
    return f"harness/{vmod}/{channel}/{engine}"


def engine_artifact(engine: str, target: str) -> str:
    """The stable artifact address of one engine row.

    Derived from the logical row key alone: a consumer must be able to compute
    the name of the artifact it needs without knowing any commit or version
    that the engine job resolved at run time, and without reading an output of
    the aggregate engine matrix job.
    """
    return f"engine-{engine}-{target}"


def source_artifact(vmod: str, channel: str) -> str:
    return f"vmod-source-{vmod}-{channel}"


def packages_artifact(vmod: str, channel: str, engine: str, target: str) -> str:
    return f"packages-{vmod}-{channel}-{engine}-{target}"


def result_artifact(row: dict) -> str:
    kind = row["kind"]
    if kind == "engine":
        return f"result-engine-{row['engine']}-{row['target']}"
    if kind == "invocation":
        return f"result-{row['vmod']}-invocation"
    if kind == "source":
        return f"result-{row['vmod']}-source-{row['channel']}"
    if kind == "source-harness":
        return f"result-{row['vmod']}-{row['channel']}-{row['engine']}"
    return f"result-{row['vmod']}-{row['channel']}-{row['engine']}-{row['target']}"


def summary_artifact(vmod: str) -> str:
    return f"result-{vmod}-summary"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def _row(kind: str, vmod: str, required: bool, selected: bool, **fields) -> dict:
    row = {
        "kind": kind,
        "vmod": vmod,
        "required": required,
        "selected": selected,
        "channel": fields.get("channel", ""),
        "engine": fields.get("engine", ""),
        "target": fields.get("target", ""),
    }
    row.update({k: v for k, v in fields.items() if k not in row})
    if kind == "engine":
        # An engine row belongs to no VMOD: it is the shared dependency several
        # of them consume, so `vmod` is empty and the summary groups it apart.
        row["row_key"] = engine_row_key(row["engine"], row["target"])
        row["label"] = f"{row['engine']} / {row['target']}"
    elif kind == "invocation":
        row["row_key"] = invocation_row_key(vmod)
        row["label"] = "manifest validation"
    elif kind == "source":
        row["row_key"] = source_row_key(vmod, row["channel"])
        row["label"] = f"source {row['channel']}"
    elif kind == "source-harness":
        row["row_key"] = harness_row_key(vmod, row["channel"], row["engine"])
        row["label"] = f"{row['channel']} / {row['engine']} / source harness"
    else:
        row["row_key"] = target_row_key(vmod, row["channel"], row["engine"], row["target"])
        row["label"] = f"{row['channel']} / {row['engine']} / {row['target']}"
    row["result_artifact"] = result_artifact(row)
    return row


def engine_rows(datas: list, tier: str) -> list:
    """The shared engine package rows the selected package lanes need.

    The plan says the selected engine inputs provide the expected engine-row
    ledger, so the rows are derived rather than listed: one row per
    (engine, target) pair that at least one selected package lane consumes,
    across every VMOD manifest that parsed. Two VMODs naming the same engine and
    target share one row and one artifact, which is the point of the split.

    A row is required when any of its consumers is required. An engine row whose
    only consumers are optional cannot redden the run on its own, but its
    consumers still report `blocked_by_engine_artifact` when it fails.
    """
    wanted: dict = {}
    for data in datas:
        required = data["required"] == "true"
        for lane in data["lanes"]:
            if lane["kind"] != "package" or tier not in lane["tiers"]:
                continue
            for target in lane.get("targets") or []:
                if lane["engine"] not in ENGINES or target not in TARGETS:
                    continue
                key = (lane["engine"], target)
                wanted[key] = wanted.get(key, False) or required
    rows = []
    for engine, target in sorted(wanted):
        rows.append(
            _row(
                "engine",
                "",
                wanted[(engine, target)],
                True,
                engine=engine,
                target=target,
                vinyl_track=ENGINES[engine]["vinyl_track"],
                family=TARGETS[target]["family"],
                engine_artifact=engine_artifact(engine, target),
            )
        )
    return rows


def vmod_rows(data: dict, tier: str, manifest_path: str) -> list:
    """Expected rows for one valid VMOD manifest and one tier.

    Lanes the tier does not select stay in the ledger marked ``selected: false``
    so the summary can show what exists but was not asked for, which is the
    ``not_selected`` half of the vocabulary.
    """
    vmod = data["id"]
    required = data["required"] == "true"
    rows = [_row("invocation", vmod, required, True, manifest=manifest_path)]

    selected_channels: list = []
    lane_rows: list = []
    for lane in data["lanes"]:
        selected = tier in lane["tiers"]
        channel = lane["source"]
        engine = lane["engine"]
        if selected and channel not in selected_channels:
            selected_channels.append(channel)
        if lane["kind"] == "source-harness":
            lane_rows.append(
                _row("source-harness", vmod, required, selected, channel=channel, engine=engine)
            )
            continue
        source = data["sources"][channel]
        for target in lane["targets"]:
            lane_rows.append(
                _row(
                    "package-target",
                    vmod,
                    required,
                    selected,
                    channel=channel,
                    engine=engine,
                    target=target,
                    vinyl_track=ENGINES[engine]["vinyl_track"],
                    family=TARGETS[target]["family"],
                    # The row's own copy of the source identity. A package row
                    # checks the VMOD out itself in Phase 1, and must verify the
                    # same pinned ref and commit the source row did.
                    ref=source["ref"],
                    expected_commit=source.get("expected_commit", ""),
                    version=source.get("version", ""),
                    packages_artifact=packages_artifact(vmod, channel, engine, target),
                    source_artifact=source_artifact(vmod, channel),
                    # The shared engine artifact this row consumes, addressed by
                    # the engine row key rather than by anything the engine job
                    # resolved at run time.
                    engine_artifact=engine_artifact(engine, target),
                    engine_row_key=engine_row_key(engine, target),
                )
            )

    for channel in SOURCE_CHANNELS:
        if channel not in data["sources"]:
            continue
        used = any(
            lane["source"] == channel and lane["kind"] == "package" for lane in data["lanes"]
        )
        if not used:
            continue
        source = data["sources"][channel]
        rows.append(
            _row(
                "source",
                vmod,
                required,
                channel in selected_channels,
                channel=channel,
                ref=source["ref"],
                expected_commit=source.get("expected_commit", ""),
                version=source.get("version", ""),
                archive_url=source.get("archive_url", ""),
                archive_sha256=source.get("archive_sha256", ""),
                publishable=source["publishable"] == "true",
                source_artifact=source_artifact(vmod, channel),
            )
        )

    rows.extend(lane_rows)
    return rows


def invalid_manifest_rows(vmod: str, manifest_path: str, errors: list) -> list:
    """The ledger for a VMOD whose manifest could not be parsed or validated.

    Exactly one row, using the trusted discovery id. Inventing lane rows from a
    manifest that did not parse would report failures for work nobody ever
    asked for.
    """
    row = _row("invocation", vmod, True, True, manifest=manifest_path)
    row["manifest_valid"] = False
    row["errors"] = list(errors)
    return [row]


def valid_manifests(tier: str, repo_root=None) -> tuple:
    """(validated manifests, rows for the manifests that did not validate).

    One walk of the catalog, shared by the ledger and by the engine matrix, so
    both agree on which manifests contributed lanes. A manifest that does not
    parse contributes exactly one invocation row and no engine demand: inventing
    engine rows for a manifest nobody could read would build packages for lanes
    that were never declared.
    """
    root = Path(repo_root) if repo_root else REPO_ROOT
    datas: list = []
    broken: list = []
    for entry in discover(root):
        path = root / entry["manifest"]
        try:
            data = load_vmod_manifest(path)
        except (yaml_subset.ManifestSyntaxError, OSError) as exc:
            broken.extend(invalid_manifest_rows(entry["id"], entry["manifest"], [str(exc)]))
            continue
        errors = validate_vmod_manifest(data, entry["manifest"], discovery_id=entry["id"])
        if errors:
            broken.extend(invalid_manifest_rows(entry["id"], entry["manifest"], errors))
            continue
        datas.append((entry, data))
    return datas, broken


def ledger(tier: str, repo_root=None) -> dict:
    datas, broken = valid_manifests(tier, repo_root)
    # Engine rows first: they are the shared dependency, and the summary reads
    # top-down from "what did the whole run depend on" to "what did each VMOD
    # do with it".
    rows: list = [dict(row, manifest_valid=True) for row in engine_rows([d for _, d in datas], tier)]
    rows.extend(broken)
    for entry, data in datas:
        for row in vmod_rows(data, tier, entry["manifest"]):
            row["manifest_valid"] = True
            rows.append(row)
    return {"schema": "vmod-ci-ledger/v1", "tier": tier, "rows": rows}


# ---------------------------------------------------------------------------
# Matrix expansion
# ---------------------------------------------------------------------------


def injection_applies(inject: str, vmod: str) -> bool:
    """Does this injection act on this VMOD's rows?

    With one VMOD the question did not arise. With two it is the whole point:
    an injection that hit every VMOD would demonstrate a broken run rather than
    a contained one. `manifest` is the exception -- ci.yml corrupts every
    manifest for it, deliberately, because that case is about the ledger and
    not about isolation between VMODs.
    """
    if inject in ("none", "engine_build", "suppress_engine_artifact"):
        return False
    target = INJECTION_TARGET_VMOD.get(inject)
    return target is None or target == vmod


def injection_vmod(inject: str) -> str:
    """The VMOD an injection acts on, or "" when it acts on none of them.

    Exposed so a workflow can ask rather than hardcode a comparison. Every
    place that corrupts a manifest -- the plan job, this VMOD's summary job and
    the caller's collector and engine-discovery jobs -- has to apply the same
    corruption to the same file, or they rebuild different expected ledgers and
    the run reports rows nobody asked for.
    """
    if inject in ("none", "engine_build", "suppress_engine_artifact"):
        return ""
    return INJECTION_TARGET_VMOD.get(inject) or ""


def expand(data: dict, tier: str, inject: str = "none", repo_root=None) -> dict:
    """Matrices for one VMOD's reusable-workflow invocation."""
    vmod = data["id"]
    _refuse_inert_injection(vmod, inject, repo_root)
    rows = vmod_rows(data, tier, "")
    # An injection aimed at the other VMOD must leave every row here untouched,
    # which is exactly the property the two-VMOD isolation cases demonstrate.
    active = injection_applies(inject, vmod)
    sources = []
    for row in rows:
        if row["kind"] != "source" or not row["selected"]:
            continue
        entry = {
            "channel": row["channel"],
            "ref": row["ref"],
            "expected_commit": row["expected_commit"],
            "version": row["version"],
            "archive_url": row["archive_url"],
            "archive_sha256": row["archive_sha256"],
            "row_key": row["row_key"],
            "source_artifact": row["source_artifact"],
            "result_artifact": row["result_artifact"],
        }
        if active and inject in ("source_checkout", "dict_source", "redis_source"):
            # A ref that cannot exist: proves a source failure is confined to
            # this VMOD's rows. No build script is touched. `dict_source` is the
            # same case from the other side, so a run can show either VMOD's
            # source failing while the other completes.
            entry["ref"] = "vmod-ci-injected-missing-ref"
        if active and inject == "source_digest":
            entry["archive_sha256"] = "0" * 63 + "1"
        sources.append(entry)

    targets = []
    for row in rows:
        if row["kind"] != "package-target" or not row["selected"]:
            continue
        targets.append(
            {
                "channel": row["channel"],
                "engine": row["engine"],
                "vinyl_track": row["vinyl_track"],
                "target": row["target"],
                "family": row["family"],
                "timeout_minutes": target_timeout_minutes(row["target"], tier),
                "ref": row["ref"],
                "expected_commit": row["expected_commit"],
                "version": row["version"],
                "row_key": row["row_key"],
                "packages_artifact": row["packages_artifact"],
                "source_artifact": row["source_artifact"],
                "engine_artifact": row["engine_artifact"],
                "engine_row_key": row["engine_row_key"],
                "result_artifact": row["result_artifact"],
                # Per-row injection flags rather than a workflow expression
                # comparing ids and families. The workflow reads one boolean and
                # cannot drift from the table above about which row is injected.
                "inject_build": "true"
                if active
                and (
                    (inject == "debian_build" and row["family"] == "deb")
                    or (inject == "el9_build" and row["family"] == "rpm")
                    or (inject == "dict_build" and row["family"] == "deb")
                    or (inject == "redis_build" and row["family"] == "rpm")
                )
                else "false",
                "inject_recipe": "true"
                if active and inject == "recipe_generation" and row["family"] == "deb"
                else "false",
                # Both families, unlike inject_recipe: the two refusals have
                # different mechanisms and a run that showed only one would
                # leave the other untested.
                "inject_patch": "true"
                if active and inject == "patch_omission"
                else "false",
                "suppress_result": "true"
                if active
                and inject == "suppress_result"
                and row["family"] == "deb"
                and row["engine"] == "vinyl-release"
                else "false",
            }
        )

    harnesses = [
        {
            "channel": row["channel"],
            "engine": row["engine"],
            "row_key": row["row_key"],
            "result_artifact": row["result_artifact"],
        }
        for row in rows
        if row["kind"] == "source-harness" and row["selected"]
    ]

    return {
        "vmod": vmod,
        "required": data["required"] == "true",
        "source_host": data["source_host"],
        "repository": data.get("repository", ""),
        "clone_url": data.get("clone_url", ""),
        "adapter": data["adapter"],
        "recipe": data["recipe"],
        "sources": {"include": sources},
        "targets": {"include": targets},
        "harnesses": {"include": harnesses},
        "source_count": len(sources),
        "target_count": len(targets),
        "harness_count": len(harnesses),
    }


def _refuse_inert_injection(vmod: str, inject: str, repo_root=None) -> None:
    """An injection that cannot act is worse than an absent one (G4).

    Three of Wave B's ten defects were inert injections -- a flag that reached a
    job nothing read, producing a GREEN run that looked like a demonstration.
    `patch_omission` has the same shape available to it: aimed at a VMOD whose
    overlay declares no patches, it would delete nothing and the row would pass.

    The lane keeps its own guard as well, and that is deliberate rather than
    redundant. This one fails at expansion, before a runner is started, and
    names the manifest; the lane's fails inside generate.sh if the declaration
    changes between expansion and generation. Belt and braces is the correct
    posture for anti-inertness specifically, because the failure mode being
    guarded against is silence.
    """
    if inject != "patch_omission" or INJECTION_TARGET_VMOD.get(inject) != vmod:
        return
    overlay = (
        (Path(repo_root) if repo_root else REPO_ROOT)
        / "recipes"
        / "vmods"
        / "overlays"
        / vmod
        / "overlay.yml"
    )
    declared = []
    if overlay.is_file():
        try:
            declared = yaml_subset.parse_file(overlay).get("patches") or []
        except yaml_subset.ManifestSyntaxError:
            # The overlay has its own validator; a syntax error there must not
            # be reported here as an injection problem.
            return
    if not declared:
        raise CatalogError(
            f"inject=patch_omission targets {vmod!r}, whose overlay declares no patches. "
            "The injection would delete nothing and the run would go green, which is a "
            "demonstration of nothing. Point it at a VMOD with a declared patch, or "
            "remove the injection."
        )


def engine_matrix(tier: str, repo_root=None, inject: str = "none") -> dict:
    """The top-level engine matrix: one entry per shared engine package row.

    Tolerant of a malformed VMOD manifest by construction -- ``valid_manifests``
    drops it, so a broken entry costs its own invocation row and whatever engine
    rows only it asked for, never the engine rows another VMOD needs. That is
    the same containment rule discovery follows, applied to the shared half of
    the graph.
    """
    datas, _ = valid_manifests(tier, repo_root)
    entries = []
    for row in engine_rows([d for _, d in datas], tier):
        entries.append(
            {
                "engine": row["engine"],
                "target": row["target"],
                "family": row["family"],
                "vinyl_track": row["vinyl_track"],
                "timeout_minutes": TARGETS[row["target"]]["engine_timeout_minutes"],
                "row_key": row["row_key"],
                "engine_artifact": row["engine_artifact"],
                "result_artifact": row["result_artifact"],
                # Inert unless a human dispatched the caller with an injection.
                # Computed here rather than as a workflow expression so the tool
                # and the workflow cannot disagree about which row is injected.
                "inject_build": "true"
                if inject == "engine_build" and (row["engine"], row["target"]) == INJECT_ENGINE_ROW
                else "false",
                "suppress_artifact": "true"
                if inject == "suppress_engine_artifact"
                and (row["engine"], row["target"]) == INJECT_ENGINE_ROW
                else "false",
            }
        )
    return {"include": entries}


# ---------------------------------------------------------------------------
# Engine artifact metadata
# ---------------------------------------------------------------------------
#
# The plan requires that every dependency artifact carry machine-readable
# resolved-identity metadata and that consumers verify it against their own row
# before use. For a VMOD source archive the consumer has a second, independent
# way to know what it got -- it checks the tag out again and asserts the peeled
# commit -- but a VMOD package row does not build the engine and cannot
# re-derive it, so for engine artifacts this metadata is the only check there
# is. It is therefore not advisory: a mismatch fails the row.
#
# The identity values themselves are read out of the lane's own pin file by
# scripts/ci/engine-identity.sh, which both the producer and the consumer run
# from their own checkout. Keeping the key list in the shell script rather than
# duplicating it here means a pin that gains a name cannot be silently dropped
# from the comparison by a Python table nobody updated.

# Identity keys that must be present and non-empty in every engine artifact.
# Without this the comparison could pass vacuously -- two empty dictionaries are
# equal -- if engine-identity.sh ever emitted nothing.
REQUIRED_IDENTITY_KEYS = [
    "cohort_id",
    "vinyl_track",
    "vinyl_strict_abi",
    "vinyl_package_version",
]

# Which files in a lane output directory belong to the engine. Both lanes name
# every Vinyl artifact after the source package, so one prefix covers the
# runtime, development and debug packages and, on Debian, the source package,
# .changes and .buildinfo beside them.
ENGINE_FILE_PREFIX = "vinyl-cache"


class EngineMetadataError(Exception):
    """Raised when an engine artifact does not describe what the row asked for."""


def parse_identity(path) -> dict:
    """Parse scripts/ci/engine-identity.sh output: key=value, one per line."""
    identity: dict = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EngineMetadataError(f"{path}:{number}: not a key=value line: {line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise EngineMetadataError(f"{path}:{number}: empty key")
        if key in identity:
            raise EngineMetadataError(f"{path}:{number}: duplicate key {key!r}")
        identity[key] = value.strip()
    if not identity:
        raise EngineMetadataError(f"{path}: no identity values; the check would be vacuous")
    missing = [k for k in REQUIRED_IDENTITY_KEYS if not identity.get(k)]
    if missing:
        raise EngineMetadataError(f"{path}: missing or empty required identity keys {missing}")
    return identity


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def engine_package_files(packages_dir) -> list:
    """Every engine package file in a directory, sorted by name."""
    directory = Path(packages_dir)
    if not directory.is_dir():
        raise EngineMetadataError(f"{packages_dir}: no such directory")
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and p.name.startswith(ENGINE_FILE_PREFIX)),
        key=lambda p: p.name,
    )


def describe_packages(packages_dir) -> list:
    return [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256_file(path)}
        for path in engine_package_files(packages_dir)
    ]


def packages_digest(packages: list) -> str:
    """One value over the whole package set: the content identity of the artifact.

    Sorted name + digest pairs, so it is independent of directory order and of
    the sizes, which are evidence rather than identity.
    """
    joined = "".join(
        f"{entry['name']}\0{entry['sha256']}\0" for entry in sorted(packages, key=lambda e: e["name"])
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def engine_metadata(engine: str, target: str, identity: dict, packages: list) -> dict:
    if engine not in ENGINES:
        raise EngineMetadataError(f"unknown engine {engine!r}; expected one of {sorted(ENGINES)}")
    if target not in TARGETS:
        raise EngineMetadataError(f"unknown target {target!r}; expected one of {sorted(TARGETS)}")
    if not ENGINES[engine]["builds_packages"]:
        raise EngineMetadataError(f"engine {engine!r} has no native package lane")
    if not packages:
        raise EngineMetadataError(
            f"no {ENGINE_FILE_PREFIX}* files were produced; an engine artifact with no engine "
            "packages in it would be verified successfully by every consumer"
        )
    return {
        "schema": ENGINE_SCHEMA,
        "engine": engine,
        "target": target,
        "family": TARGETS[target]["family"],
        "vinyl_track": ENGINES[engine]["vinyl_track"],
        "artifact": engine_artifact(engine, target),
        "row_key": engine_row_key(engine, target),
        "identity": dict(identity),
        "packages": list(packages),
        "packages_sha256": packages_digest(packages),
    }


def verify_engine_metadata(metadata: dict, engine: str, target: str, identity: dict, packages_dir) -> list:
    """Errors ([] means the artifact is what this row asked for)."""
    problems: list = []
    if not isinstance(metadata, dict):
        return ["engine metadata is not an object"]
    if metadata.get("schema") != ENGINE_SCHEMA:
        return [f"engine metadata schema {metadata.get('schema')!r} is not {ENGINE_SCHEMA!r}"]
    for field, expected in (
        ("engine", engine),
        ("target", target),
        ("artifact", engine_artifact(engine, target)),
        ("row_key", engine_row_key(engine, target)),
    ):
        if metadata.get(field) != expected:
            problems.append(
                f"{field} {metadata.get(field)!r} does not match the requested {expected!r}"
            )
    if target in TARGETS and metadata.get("family") != TARGETS[target]["family"]:
        problems.append(
            f"family {metadata.get('family')!r} does not match {TARGETS[target]['family']!r}"
        )
    if engine in ENGINES and metadata.get("vinyl_track") != ENGINES[engine]["vinyl_track"]:
        problems.append(
            f"vinyl_track {metadata.get('vinyl_track')!r} does not match "
            f"{ENGINES[engine]['vinyl_track']!r}"
        )

    recorded = metadata.get("identity")
    if not isinstance(recorded, dict) or not recorded:
        problems.append("engine metadata records no resolved identity")
    else:
        for key in sorted(set(recorded) | set(identity)):
            if recorded.get(key) != identity.get(key):
                problems.append(
                    f"identity {key}: artifact {recorded.get(key)!r} != this row's "
                    f"{identity.get(key)!r}"
                )
        for key in REQUIRED_IDENTITY_KEYS:
            if not recorded.get(key):
                problems.append(f"identity {key} is missing or empty in the artifact")

    entries = metadata.get("packages")
    if not isinstance(entries, list) or not entries:
        problems.append("engine metadata records no packages")
        return problems
    if metadata.get("packages_sha256") != packages_digest(entries):
        problems.append("packages_sha256 does not match the recorded package list")

    try:
        present = {path.name: path for path in engine_package_files(packages_dir)}
    except EngineMetadataError as exc:
        problems.append(str(exc))
        return problems
    for entry in sorted(entries, key=lambda e: e.get("name", "")):
        name = entry.get("name", "")
        path = present.pop(name, None)
        if path is None:
            problems.append(f"{name}: recorded in the artifact metadata but not delivered")
            continue
        got = _sha256_file(path)
        if got != entry.get("sha256"):
            problems.append(f"{name}: sha256 {got} != recorded {entry.get('sha256')}")
        size = path.stat().st_size
        if size != entry.get("bytes"):
            problems.append(f"{name}: {size} bytes != recorded {entry.get('bytes')}")
    for name in sorted(present):
        problems.append(f"{name}: delivered but not recorded in the artifact metadata")
    return problems


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


def make_record(
    kind: str,
    vmod: str,
    status: str,
    channel: str = "",
    engine: str = "",
    target: str = "",
    stage: str = "",
    detail: str = "",
    artifacts=None,
    source=None,
    synthesized: bool = False,
) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
    if kind == "engine":
        if vmod:
            raise ValueError("an engine row belongs to no VMOD; --vmod must be empty")
        row_key = engine_row_key(engine, target)
    elif kind == "invocation":
        row_key = invocation_row_key(vmod)
    elif kind == "source":
        row_key = source_row_key(vmod, channel)
    elif kind == "source-harness":
        row_key = harness_row_key(vmod, channel, engine)
    elif kind == "package-target":
        row_key = target_row_key(vmod, channel, engine, target)
    else:
        raise ValueError(f"unknown row kind {kind!r}")
    return {
        "schema": RESULT_SCHEMA,
        "row_key": row_key,
        "kind": kind,
        "vmod": vmod,
        "channel": channel,
        "engine": engine,
        "target": target,
        "status": status,
        "stage": stage,
        "detail": detail,
        "artifacts": list(artifacts or []),
        "source": dict(source or {}),
        "synthesized": bool(synthesized),
    }


def load_records(results_dir) -> dict:
    """Every result record under a directory tree, keyed by row key.

    A record written by the row itself always beats a record synthesized by a
    summary job, so a per-VMOD summary can safely report the whole VMOD without
    overwriting what a target row actually observed.
    """
    directory = Path(results_dir)
    found: dict = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "rows" in data:
            records = data["rows"]
        elif isinstance(data, list):
            records = data
        else:
            records = [data]
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("schema") != RESULT_SCHEMA or "row_key" not in record:
                continue
            key = record["row_key"]
            existing = found.get(key)
            if existing is None or (existing.get("synthesized") and not record.get("synthesized")):
                found[key] = record
    return found


def reconcile(expected: dict, observed: dict) -> dict:
    """Match the expected ledger against the observed records.

    Every expected row gets an outcome, including rows whose job never started:
    a required row with no evidence is a failure, not an absence.
    """
    rows: list = []
    source_status: dict = {}
    for row in expected["rows"]:
        if row["kind"] != "source":
            continue
        record = observed.get(row["row_key"])
        source_status[(row["vmod"], row["channel"])] = record["status"] if record else None

    engine_status: dict = {}
    for row in expected["rows"]:
        if row["kind"] != "engine":
            continue
        record = observed.get(row["row_key"])
        engine_status[row["row_key"]] = record["status"] if record else None

    invocation_status: dict = {}
    for row in expected["rows"]:
        if row["kind"] != "invocation":
            continue
        record = observed.get(row["row_key"])
        if record is not None:
            invocation_status[row["vmod"]] = record["status"]
        elif row.get("manifest_valid") is False:
            invocation_status[row["vmod"]] = "failed_manifest_validation"
        else:
            invocation_status[row["vmod"]] = None

    for row in expected["rows"]:
        resolved = dict(row)
        record = observed.get(row["row_key"])
        if not row["selected"]:
            resolved.update(
                {
                    "status": "not_selected",
                    "detail": "no lane for this tier",
                    "stage": "",
                    "observed": False,
                    "artifacts": [],
                }
            )
            rows.append(resolved)
            continue
        if record is not None:
            resolved.update(
                {
                    "status": record["status"],
                    "detail": record.get("detail", ""),
                    "stage": record.get("stage", ""),
                    "observed": not record.get("synthesized", False),
                    "artifacts": record.get("artifacts", []),
                    "source_identity": record.get("source", {}),
                }
            )
            rows.append(resolved)
            continue

        # No record at all. Say why, rather than guessing that the compiler
        # failed: a row blocked by its own VMOD's source failure is a different
        # fact from a row whose evidence never arrived.
        status = "missing_result_record"
        detail = "no result record was uploaded for this expected row"
        if row["kind"] == "invocation" and row.get("manifest_valid") is False:
            status = "failed_manifest_validation"
            detail = "; ".join(row.get("errors", [])) or "manifest failed validation"
        elif row["kind"] in ("package-target", "source-harness"):
            # Only a row that actually has an upstream source row can be
            # blocked by one. A source-harness lane on a moving channel derives
            # no archive and gets no source row (see vmod_rows), so its absence
            # of evidence is missing evidence, not a blockage by something that
            # was never expected to run.
            key = (row["vmod"], row["channel"])
            engine_key = row.get("engine_row_key")
            if key in source_status and source_status[key] != "passed":
                upstream = source_status[key]
                if upstream is None:
                    status = "blocked_by_vmod_source"
                    detail = (
                        f"source/{row['vmod']}/{row['channel']} produced no result record either"
                    )
                else:
                    status = "blocked_by_vmod_source"
                    detail = f"source/{row['vmod']}/{row['channel']} is {upstream}"
            elif engine_key in engine_status and engine_status[engine_key] != "passed":
                # The shared root cause, named by engine row identity rather
                # than reported as an unclassified download error. The row's own
                # VMOD source failure wins where both apply: that one is
                # specific to this row, and the engine failure is reported in
                # full on its own row regardless.
                upstream = engine_status[engine_key]
                status = "blocked_by_engine_artifact"
                detail = (
                    f"{engine_key} produced no result record either"
                    if upstream is None
                    else f"{engine_key} is {upstream}"
                )
            elif (
                key not in source_status
                and invocation_status.get(row["vmod"]) == "failed_manifest_validation"
            ):
                status = "blocked_by_vmod_source"
                detail = "the VMOD manifest failed validation"
        elif row["kind"] == "source" and invocation_status.get(row["vmod"]) not in (None, "passed"):
            status = "blocked_by_vmod_source"
            detail = f"vmod/{row['vmod']} is {invocation_status[row['vmod']]}"
        resolved.update(
            {
                "status": status,
                "detail": detail,
                "stage": "",
                "observed": False,
                "artifacts": [],
            }
        )
        rows.append(resolved)

    expected_keys = {row["row_key"] for row in expected["rows"]}
    unexpected = [record for key, record in sorted(observed.items()) if key not in expected_keys]

    failures = [r for r in rows if r["status"] not in OK_STATUSES]
    required_failures = [r for r in failures if r["required"]]
    bad_unexpected = [r for r in unexpected if r.get("status") not in OK_STATUSES]
    return {
        "tier": expected["tier"],
        "rows": rows,
        "unexpected": unexpected,
        "counts": {
            "expected": len([r for r in rows if r["selected"]]),
            "passed": len([r for r in rows if r["status"] == "passed"]),
            "failed": len(failures),
            "required_failed": len(required_failures),
            "not_selected": len([r for r in rows if r["status"] == "not_selected"]),
            "missing": len([r for r in rows if r["status"] == "missing_result_record"]),
        },
        "ok": not required_failures and not bad_unexpected,
    }


def synthesize_missing(expected: dict, observed: dict, vmod: str) -> list:
    """Records for one VMOD's expected rows that produced no record of their own.

    The per-VMOD summary job uploads these so the run carries an explicit
    outcome for every requested row even when the VMOD's source job failed and
    every target row was skipped.
    """
    # Engine rows are in scope for the reconciliation but never synthesized
    # here: the per-VMOD summary must be able to say "this row was blocked by
    # that engine row", but the engine row belongs to the whole run and its own
    # outcome is the caller's to record.
    scoped = {
        "tier": expected["tier"],
        "rows": [row for row in expected["rows"] if row["vmod"] == vmod or row["kind"] == "engine"],
    }
    resolved = reconcile(scoped, observed)
    records = []
    for row in resolved["rows"]:
        if row["observed"] or not row["selected"] or row["kind"] == "engine":
            continue
        records.append(
            make_record(
                kind=row["kind"],
                vmod=row["vmod"],
                status=row["status"],
                channel=row["channel"],
                engine=row["engine"],
                target=row["target"],
                stage="summary",
                detail=row["detail"],
                synthesized=True,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_STATUS_MARK = {
    "passed": "PASS",
    "not_selected": "skip",
    "missing_result_record": "MISSING",
}


def render_summary(resolved: dict) -> str:
    """Markdown grouped by VMOD, then source channel, engine and target.

    Successful rows stay visible when the run is red: the point of the
    collector is a complete picture, not a list of the things that broke.
    """
    counts = resolved["counts"]
    out = [f"## VMOD matrix results (tier: {resolved['tier']})", ""]
    out.append(
        "{expected} expected row(s): {passed} passed, {failed} failed, "
        "{missing} with no result record; {not_selected} lane(s) not selected for this "
        "tier.".format(**counts)
    )
    out.append("")
    if resolved["ok"]:
        out.append("Every required row produced a passing result.")
        # A green run with failures in it must say so in the same breath.
        # "Every required row passed" alone would read as "nothing failed".
        if counts["failed"]:
            out.append("")
            out.append(
                f"{counts['failed']} optional row(s) failed and did not redden the run."
            )
    else:
        out.append(
            f"**{counts['required_failed']} required row(s) failed or are missing.** "
            "The run is red."
        )
    out.append("")

    engines = [r for r in resolved["rows"] if r["kind"] == "engine"]
    if engines:
        out.append("### Shared engine packages")
        out.append("")
        out.append("| engine / target | status | evidence | detail |")
        out.append("| --- | --- | --- | --- |")
        for row in engines:
            mark = _STATUS_MARK.get(row["status"], row["status"])
            evidence = "row" if row["observed"] else "synthesized"
            detail = row.get("detail", "") or ", ".join(row.get("artifacts", []))
            out.append(
                "| {label} | {mark} | {evidence} | {detail} |".format(
                    label=row["label"],
                    mark=mark,
                    evidence=evidence,
                    detail=detail.replace("|", "\\|")[:300],
                )
            )
        out.append("")

    vmods = []
    for row in resolved["rows"]:
        if row["kind"] == "engine":
            continue
        if row["vmod"] not in vmods:
            vmods.append(row["vmod"])
    for vmod in vmods:
        rows = [r for r in resolved["rows"] if r["kind"] != "engine" and r["vmod"] == vmod]
        required = "required" if rows[0]["required"] else "optional"
        out.append(f"### {vmod} ({required})")
        out.append("")
        out.append("| row | status | evidence | detail |")
        out.append("| --- | --- | --- | --- |")
        for row in rows:
            mark = _STATUS_MARK.get(row["status"], row["status"])
            evidence = "row" if row["observed"] else ("-" if not row["selected"] else "synthesized")
            artifacts = ", ".join(row.get("artifacts", []))
            detail = row.get("detail", "") or artifacts
            out.append(
                "| {label} | {mark} | {evidence} | {detail} |".format(
                    label=row["label"],
                    mark=mark,
                    evidence=evidence,
                    detail=detail.replace("|", "\\|")[:300],
                )
            )
        out.append("")
        identities = [
            (r["label"], r.get("source_identity", {}))
            for r in rows
            if r["kind"] == "source" and r.get("source_identity")
        ]
        for label, identity in identities:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(identity.items()) if v)
            if parts:
                out.append(f"Resolved {label}: {parts}")
                out.append("")

    if resolved["unexpected"]:
        out.append("### Result records with no expected row")
        out.append("")
        for record in resolved["unexpected"]:
            out.append(f"- `{record.get('row_key')}` ({record.get('status')})")
        out.append("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list_vmods(args) -> int:
    entries = discover(args.repo_root)
    matrix = {"include": entries}
    if args.format == "github":
        print("matrix=" + json.dumps(matrix, sort_keys=True))
        print("count=" + str(len(entries)))
    else:
        print(json.dumps(matrix, indent=2, sort_keys=True))
    return 0


def cmd_check_catalog(args) -> int:
    entries = discover(args.repo_root)
    for entry in entries:
        print(f"catalog  {entry['manifest']} -> id {entry['id']}")
    print(f"\nOK: {len(entries)} VMOD manifest(s) discovered by file name")
    return 0


def cmd_validate_vmod(args) -> int:
    path = Path(args.manifest)
    try:
        data = load_vmod_manifest(path)
    except yaml_subset.ManifestSyntaxError as exc:
        print(f"ERROR    {exc}", file=sys.stderr)
        return 1
    errors = validate_vmod_manifest(data, str(path), discovery_id=args.id)
    if args.tier and args.tier not in TIERS:
        errors.append(f"{path}: unknown tier {args.tier!r}")
    if args.source_dir:
        errors.extend(
            source_cross_check_errors(data, str(path), args.source_dir, args.source_channel)
        )
    if errors:
        for error in errors:
            print(f"ERROR    {error}", file=sys.stderr)
        return 1
    print(f"OK: {path} is a valid {SCHEMA} manifest for {data['id']}")
    if args.source_dir:
        pinned = data["sources"].get(args.source_channel, {}).get("version")
        if pinned:
            print(
                f"OK: {data['adapter']} source cross-check, {args.source_channel} version "
                f"{pinned} against {args.source_dir}"
            )
        else:
            print(
                f"note: source channel {args.source_channel} records no version, so there is "
                "nothing to cross-check; its resolved commit is evidence, not a pin"
            )
    return 0


def source_cross_check_errors(data: dict, path: str, source_dir, channel: str = "release") -> list:
    """Adapter-specific checks that need the VMOD's source checked out.

    This is the half of validation that used to live in the global registry
    gate. It runs inside the VMOD's own invocation, after its checkout, so a
    cachetag source problem is a cachetag failure rather than a global registry
    failure.

    A moving channel records no version, and there is nothing to cross-check
    against: what it resolved to is evidence, not a pin.
    """
    if data["adapter"] != "cachetag":
        return [f"{path}: no source cross-check is implemented for adapter {data['adapter']!r}"]
    source = data["sources"].get(channel)
    if source is None:
        return [f"{path}: source channel {channel!r} is not declared"]
    version = source.get("version")
    if not version:
        return []
    try:
        found = manifest_mod.configure_ac_version(source_dir)
    except manifest_mod.ValidationError as exc:
        return [f"{path}: {exc}"]
    if found != version:
        return [
            f"{path}: sources.{channel}.version {version!r} does not match configure.ac "
            f"AC_INIT {found!r} in {source_dir}"
        ]
    return []


def cmd_expand(args) -> int:
    path = Path(args.manifest)
    data = load_vmod_manifest(path)
    errors = validate_vmod_manifest(data, str(path), discovery_id=args.id)
    if errors:
        for error in errors:
            print(f"ERROR    {error}", file=sys.stderr)
        return 1
    result = expand(data, args.tier, inject=args.inject)
    if args.format == "github":
        for key in ("sources", "targets", "harnesses"):
            print(f"{key}=" + json.dumps(result[key], sort_keys=True))
        for key in ("source_count", "target_count", "harness_count"):
            print(f"{key}={result[key]}")
        print(f"required={'true' if result['required'] else 'false'}")
        print(f"source_host={result['source_host']}")
        print(f"repository={result['repository']}")
        print(f"clone_url={result['clone_url']}")
        print(f"recipe={result['recipe']}")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def source_facts(data: dict, channel: str) -> dict:
    """The recorded source identity of one channel, flattened for a shell.

    A lane script needs these five values and must not parse the manifest with
    sed to get them: a second parser is a second thing that can disagree with
    the validator. Emitted through the tool that owns the schema instead.
    """
    source = data["sources"].get(channel)
    if source is None:
        raise CatalogError(f"sources.{channel}: not declared in {data['id']}")
    return {
        "VMOD_ID": data["id"],
        "VMOD_SOURCE_CHANNEL": channel,
        "VMOD_SOURCE_HOST": data["source_host"],
        "VMOD_CLONE_URL": data.get("clone_url")
        or "https://github.com/{}.git".format(data.get("repository", "")),
        "VMOD_RECIPE": data["recipe"],
        "VMOD_ADAPTER": data["adapter"],
        "VMOD_SOURCE_REF": source["ref"],
        "VMOD_SOURCE_COMMIT": source.get("expected_commit", ""),
        "VMOD_SOURCE_VERSION": source.get("version", ""),
        "VMOD_SOURCE_ARCHIVE_URL": source.get("archive_url", ""),
        "VMOD_SOURCE_ARCHIVE_SHA256": source.get("archive_sha256", ""),
        "VMOD_SOURCE_PUBLISHABLE": source["publishable"],
    }


def cmd_source_facts(args) -> int:
    path = Path(args.manifest)
    data = load_vmod_manifest(path)
    errors = validate_vmod_manifest(data, str(path), discovery_id=args.id)
    if errors:
        for error in errors:
            print(f"ERROR    {error}", file=sys.stderr)
        return 1
    facts = source_facts(data, args.channel)
    if args.format == "shell":
        for key, value in facts.items():
            escaped = str(value).replace("'", "'\\''")
            print(f"{key}='{escaped}'")
    else:
        print(json.dumps(facts, indent=2, sort_keys=True))
    return 0


def cmd_injection_scope(args) -> int:
    """Print the VMOD id an injection acts on, or nothing.

    Every job that corrupts a manifest asks this rather than hardcoding a
    comparison, so the tool, the reusable workflow and the caller's collector
    cannot disagree about which file to corrupt -- and a disagreement there
    would have each of them rebuild a different expected ledger.
    """
    print(injection_vmod(args.inject))
    return 0


def cmd_engine_matrix(args) -> int:
    matrix = engine_matrix(args.tier, args.repo_root, inject=args.inject)
    if args.format == "github":
        print("matrix=" + json.dumps(matrix, sort_keys=True))
        print("count=" + str(len(matrix["include"])))
    else:
        print(json.dumps(matrix, indent=2, sort_keys=True))
    return 0


def cmd_engine_metadata(args) -> int:
    identity = parse_identity(args.identity)
    packages = describe_packages(args.packages)
    data = engine_metadata(args.engine, args.target, identity, packages)
    text = json.dumps(data, indent=2, sort_keys=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def cmd_verify_engine_metadata(args) -> int:
    identity = parse_identity(args.identity)
    try:
        data = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR    {args.metadata}: {exc}", file=sys.stderr)
        return 1
    problems = verify_engine_metadata(data, args.engine, args.target, identity, args.packages)
    if problems:
        for problem in problems:
            print(f"ERROR    engine artifact {args.engine}/{args.target}: {problem}", file=sys.stderr)
        return 1
    print(
        f"OK: engine artifact {data['artifact']} matches this row: "
        f"{len(data['packages'])} package file(s), content {data['packages_sha256'][:12]}, "
        f"cohort {data['identity'].get('cohort_id')}, "
        f"vinyl {data['identity'].get('vinyl_package_version')}, "
        f"ABI {data['identity'].get('vinyl_strict_abi')}"
    )
    return 0


def cmd_ledger(args) -> int:
    data = ledger(args.tier, args.repo_root)
    text = json.dumps(data, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


def cmd_record(args) -> int:
    source = {}
    for key, value in (
        ("ref", args.ref),
        ("commit", args.commit),
        ("version", args.version),
        ("archive_sha256", args.digest),
    ):
        if value:
            source[key] = value
    try:
        record = make_record(
            kind=args.kind,
            vmod=args.vmod,
            status=args.status,
            channel=args.channel,
            engine=args.engine,
            target=args.target,
            stage=args.stage,
            detail=args.detail,
            artifacts=args.artifact,
            source=source,
        )
    except ValueError as exc:
        print(f"ERROR    {exc}", file=sys.stderr)
        return 1
    text = json.dumps(record, indent=2, sort_keys=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def cmd_summarize_vmod(args) -> int:
    entry_id = args.id
    manifest_path = args.manifest
    try:
        data = load_vmod_manifest(Path(manifest_path))
        errors = validate_vmod_manifest(data, manifest_path, discovery_id=entry_id)
    except (yaml_subset.ManifestSyntaxError, OSError) as exc:
        data, errors = None, [str(exc)]
    if data is None or errors:
        expected = {
            "schema": "vmod-ci-ledger/v1",
            "tier": args.tier,
            "rows": invalid_manifest_rows(entry_id, manifest_path, errors),
        }
    else:
        rows = vmod_rows(data, args.tier, manifest_path)
        for row in rows:
            row["manifest_valid"] = True
        # This VMOD's share of the engine rows. Only this invocation's lanes
        # are visible from here, which is enough: the summary needs them so a
        # blocked row can name the engine that blocked it, and the caller's
        # collector owns the engine rows' own outcomes.
        rows = [dict(r, manifest_valid=True) for r in engine_rows([data], args.tier)] + rows
        expected = {"schema": "vmod-ci-ledger/v1", "tier": args.tier, "rows": rows}

    observed = load_records(args.results)
    synthesized = synthesize_missing(expected, observed, entry_id)
    rows = [record for key, record in sorted(observed.items()) if record.get("vmod") == entry_id]
    rows.extend(synthesized)
    payload = {
        "schema": "vmod-ci-vmod-summary/v1",
        "vmod": entry_id,
        "tier": args.tier,
        "rows": rows,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    resolved = reconcile(expected, load_records_from(rows, observed))
    print(render_summary(resolved))
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(render_summary(resolved))
    # The per-VMOD summary never fails the invocation on a row failure: the row
    # itself already failed and the global collector owns the verdict. It fails
    # only when it could not do its own job.
    return 0


def load_records_from(rows: list, observed: dict) -> dict:
    merged = dict(observed)
    for record in rows:
        key = record.get("row_key")
        if key is None:
            continue
        existing = merged.get(key)
        if existing is None or (existing.get("synthesized") and not record.get("synthesized")):
            merged[key] = record
    return merged


def cmd_reconcile(args) -> int:
    expected = ledger(args.tier, args.repo_root)
    observed = load_records(args.results)
    resolved = reconcile(expected, observed)
    text = render_summary(resolved)
    sys.stdout.write(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    if args.json:
        Path(args.json).write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if resolved["ok"]:
        return 0
    print("", file=sys.stderr)
    for row in resolved["rows"]:
        if row["status"] in OK_STATUSES:
            continue
        scope = "required" if row["required"] else "optional"
        print(
            f"ERROR    {row['row_key']} ({scope}): {row['status']} {row['detail']}".rstrip(),
            file=sys.stderr,
        )
    return 1


def cmd_selftest(args) -> int:
    # Both VMOD-side tools' tests run here. The recipe generator is the second
    # half of the same catalog: it reads the manifests this tool validates and
    # renders the recipes those manifests' package lanes build. Running its
    # tests from here means the CI structural-validation gate covers them
    # without learning a third command, and a generator regression cannot land
    # green because nothing invoked it.
    import ci_matrix_selftest
    import vmod_recipe_selftest

    status = ci_matrix_selftest.main()
    print()
    return vmod_recipe_selftest.main() or status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci_matrix.py",
        description="VMOD CI catalog, matrix expansion and result reconciliation.",
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-vmods", help="the trusted discovery matrix, from file names")
    p_list.add_argument("--format", choices=["json", "github"], default="json")
    p_list.set_defaults(func=cmd_list_vmods)

    p_cat = sub.add_parser("check-catalog", help="structural checks on registry/vmods/")
    p_cat.set_defaults(func=cmd_check_catalog)

    p_val = sub.add_parser("validate-vmod", help="validate one VMOD manifest")
    p_val.add_argument("--manifest", required=True)
    p_val.add_argument("--id", help="the trusted discovery id this invocation was started for")
    p_val.add_argument("--tier", choices=TIERS)
    p_val.add_argument(
        "--source-dir",
        help="the VMOD's checked-out source, for the adapter's version cross-check",
    )
    p_val.add_argument(
        "--source-channel",
        default="release",
        choices=SOURCE_CHANNELS,
        help="which source channel --source-dir holds (default: release)",
    )
    p_val.set_defaults(func=cmd_validate_vmod)

    p_exp = sub.add_parser("expand", help="expand one VMOD's lanes for one tier")
    p_exp.add_argument("--manifest", required=True)
    p_exp.add_argument("--id")
    p_exp.add_argument("--tier", required=True, choices=TIERS)
    p_exp.add_argument("--inject", choices=INJECTIONS, default="none")
    p_exp.add_argument("--format", choices=["json", "github"], default="json")
    p_exp.set_defaults(func=cmd_expand)

    p_sf = sub.add_parser(
        "source-facts", help="one channel's recorded source identity, for a lane script"
    )
    p_sf.add_argument("--manifest", required=True)
    p_sf.add_argument("--id")
    p_sf.add_argument("--channel", default="release")
    p_sf.add_argument("--format", choices=["json", "shell"], default="shell")
    p_sf.set_defaults(func=cmd_source_facts)

    p_scope = sub.add_parser(
        "injection-scope", help="which VMOD an injection acts on (empty for none)"
    )
    p_scope.add_argument("--inject", choices=INJECTIONS, default="none")
    p_scope.set_defaults(func=cmd_injection_scope)

    p_eng = sub.add_parser(
        "engine-matrix", help="the shared engine package rows the selected lanes need"
    )
    p_eng.add_argument("--tier", required=True, choices=TIERS)
    p_eng.add_argument("--inject", choices=INJECTIONS, default="none")
    p_eng.add_argument("--format", choices=["json", "github"], default="json")
    p_eng.set_defaults(func=cmd_engine_matrix)

    p_emd = sub.add_parser(
        "engine-metadata", help="describe one engine artifact's resolved identity and contents"
    )
    p_emd.add_argument("--engine", required=True)
    p_emd.add_argument("--target", required=True)
    p_emd.add_argument(
        "--identity", required=True, help="scripts/ci/engine-identity.sh output for this row"
    )
    p_emd.add_argument("--packages", required=True, help="directory holding the engine packages")
    p_emd.add_argument("--out", required=True)
    p_emd.set_defaults(func=cmd_engine_metadata)

    p_evf = sub.add_parser(
        "verify-engine-metadata",
        help="check a downloaded engine artifact against the consuming row",
    )
    p_evf.add_argument("--metadata", required=True)
    p_evf.add_argument("--engine", required=True)
    p_evf.add_argument("--target", required=True)
    p_evf.add_argument("--identity", required=True)
    p_evf.add_argument("--packages", required=True)
    p_evf.set_defaults(func=cmd_verify_engine_metadata)

    p_led = sub.add_parser("ledger", help="the expected row ledger for a tier")
    p_led.add_argument("--tier", required=True, choices=TIERS)
    p_led.add_argument("--out")
    p_led.set_defaults(func=cmd_ledger)

    p_rec = sub.add_parser("record", help="write one machine-readable result record")
    p_rec.add_argument("--out", required=True)
    p_rec.add_argument(
        "--kind",
        required=True,
        choices=["engine", "invocation", "source", "package-target", "source-harness"],
    )
    p_rec.add_argument("--vmod", required=True, default="")
    p_rec.add_argument("--status", required=True, choices=STATUSES)
    p_rec.add_argument("--channel", default="")
    p_rec.add_argument("--engine", default="")
    p_rec.add_argument("--target", default="")
    p_rec.add_argument("--stage", default="")
    p_rec.add_argument("--detail", default="")
    p_rec.add_argument("--artifact", action="append", default=[])
    p_rec.add_argument("--ref", default="")
    p_rec.add_argument("--commit", default="")
    p_rec.add_argument("--version", default="")
    p_rec.add_argument("--digest", default="")
    p_rec.set_defaults(func=cmd_record)

    p_sum = sub.add_parser("summarize-vmod", help="merge one VMOD's records and synthesize misses")
    p_sum.add_argument("--manifest", required=True)
    p_sum.add_argument("--id", required=True)
    p_sum.add_argument("--tier", required=True, choices=TIERS)
    p_sum.add_argument("--results", required=True)
    p_sum.add_argument("--out", required=True)
    p_sum.add_argument("--summary", help="append the rendered summary to this file")
    p_sum.set_defaults(func=cmd_summarize_vmod)

    p_col = sub.add_parser("reconcile", help="reconcile every expected row against the records")
    p_col.add_argument("--tier", required=True, choices=TIERS)
    p_col.add_argument("--results", required=True)
    p_col.add_argument("--summary", help="append the rendered summary to this file")
    p_col.add_argument("--json", help="write the resolved ledger here")
    p_col.set_defaults(func=cmd_reconcile)

    p_self = sub.add_parser("selftest", help="run this tool's own tests")
    p_self.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except yaml_subset.ManifestSyntaxError as exc:
        print(f"ERROR    {exc}", file=sys.stderr)
        return 1
    except (
        CatalogError,
        EngineMetadataError,
        manifest_mod.ValidationError,
        FileNotFoundError,
        ValueError,
    ) as exc:
        print(f"ERROR    {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
