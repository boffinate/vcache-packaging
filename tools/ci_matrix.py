#!/usr/bin/env python3
"""VMOD CI catalog, matrix expansion, and result reconciliation.

This is the tooling half of Phase 1 of
docs/20260728_0833_plan_vmod-matrix-failure-isolation.md. It has four jobs:

  * list the selected VMOD manifests without fetching or parsing their sources
    (``list-vmods``, ``check-catalog``);
  * expand the explicitly declared lanes of one VMOD for one workflow tier
    (``validate-vmod``, ``expand``);
  * emit the expected invocation / source / lane-row ledger (``ledger``);
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

# Workflow tiers. `ci` and `release` exist today; `nightly` and `trunk` are
# declared so a manifest can name them, but their workflows still run their own
# graphs until Phase 4 of the plan migrates them.
TIERS = ["ci", "nightly", "release", "trunk"]

ADAPTERS = ["cachetag"]

LANE_KINDS = ["package", "source-harness"]

SOURCE_CHANNELS = ["release", "trunk"]

# The selected engine inputs. This table is the expected engine-row ledger the
# plan refers to; in Phase 1 the engine is still built inside each VMOD package
# row, so all it contributes is the VINYL_TRACK value the existing lane scripts
# already select on. Phase 2 turns these into separately built artifacts.
ENGINES = {
    "vinyl-release": {"vinyl_track": "release", "pinned": True},
    "vinyl-trunk-pinned": {"vinyl_track": "trunk", "pinned": True},
    "vinyl-trunk-head": {"vinyl_track": "trunk", "pinned": False},
}

# The selected package targets, and the facts a workflow needs about them that
# are not in a VMOD manifest because they belong to the target, not the VMOD.
# The timeouts are the ones the pre-Phase-1 ci.yml carried on its Debian and
# EL9 jobs.
TARGETS = {
    "debian-13-amd64": {"family": "deb", "runner": "ubuntu-latest", "timeout_minutes": 35},
    "el9-x86_64": {"family": "rpm", "runner": "ubuntu-latest", "timeout_minutes": 30},
}

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
]

ID_RE = r"^[a-z][a-z0-9-]*$"
REPOSITORY_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$"
REF_RE = r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"
COMMIT_RE = r"^[0-9a-f]{40}$"
SHA256_RE = r"^[0-9a-f]{64}$"
VERSION_RE = r"^[0-9]+\.[0-9]+\.[0-9]+$"


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
    "archive_sha256": _s(SHA256_RE, optional=True),
    "publishable": _enum(["true", "false"]),
}

VMOD_SPEC = _map(
    {
        "schema": _enum([SCHEMA]),
        "id": _s(ID_RE),
        "repository": _s(REPOSITORY_RE),
        "required": _enum(["true", "false"]),
        "adapter": _enum(ADAPTERS),
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
    if discovery_id is not None and data["id"] != discovery_id:
        problems.append(
            f"id {data['id']!r} does not match the discovery id {discovery_id!r} this "
            "invocation was started for"
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


def invocation_row_key(vmod: str) -> str:
    return f"vmod/{vmod}"


def source_row_key(vmod: str, channel: str) -> str:
    return f"source/{vmod}/{channel}"


def target_row_key(vmod: str, channel: str, engine: str, target: str) -> str:
    return f"target/{vmod}/{channel}/{engine}/{target}"


def harness_row_key(vmod: str, channel: str, engine: str) -> str:
    return f"harness/{vmod}/{channel}/{engine}"


def source_artifact(vmod: str, channel: str) -> str:
    return f"vmod-source-{vmod}-{channel}"


def packages_artifact(vmod: str, channel: str, engine: str, target: str) -> str:
    return f"packages-{vmod}-{channel}-{engine}-{target}"


def result_artifact(row: dict) -> str:
    kind = row["kind"]
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
    if kind == "invocation":
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
                    packages_artifact=packages_artifact(vmod, channel, engine, target),
                    source_artifact=source_artifact(vmod, channel),
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


def ledger(tier: str, repo_root=None) -> dict:
    root = Path(repo_root) if repo_root else REPO_ROOT
    rows: list = []
    for entry in discover(root):
        path = root / entry["manifest"]
        try:
            data = load_vmod_manifest(path)
        except (yaml_subset.ManifestSyntaxError, OSError) as exc:
            rows.extend(invalid_manifest_rows(entry["id"], entry["manifest"], [str(exc)]))
            continue
        errors = validate_vmod_manifest(data, entry["manifest"], discovery_id=entry["id"])
        if errors:
            rows.extend(invalid_manifest_rows(entry["id"], entry["manifest"], errors))
            continue
        for row in vmod_rows(data, tier, entry["manifest"]):
            row["manifest_valid"] = True
            rows.append(row)
    return {"schema": "vmod-ci-ledger/v1", "tier": tier, "rows": rows}


# ---------------------------------------------------------------------------
# Matrix expansion
# ---------------------------------------------------------------------------


def expand(data: dict, tier: str, inject: str = "none") -> dict:
    """Matrices for one VMOD's reusable-workflow invocation."""
    vmod = data["id"]
    rows = vmod_rows(data, tier, "")
    sources = []
    for row in rows:
        if row["kind"] != "source" or not row["selected"]:
            continue
        entry = {
            "channel": row["channel"],
            "ref": row["ref"],
            "expected_commit": row["expected_commit"],
            "version": row["version"],
            "archive_sha256": row["archive_sha256"],
            "row_key": row["row_key"],
            "source_artifact": row["source_artifact"],
            "result_artifact": row["result_artifact"],
        }
        if inject == "source_checkout":
            # A ref that cannot exist: proves a checkout failure is confined to
            # this VMOD's rows. No build script is touched.
            entry["ref"] = "vmod-ci-injected-missing-ref"
        if inject == "source_digest":
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
                "timeout_minutes": TARGETS[row["target"]]["timeout_minutes"],
                "row_key": row["row_key"],
                "packages_artifact": row["packages_artifact"],
                "source_artifact": row["source_artifact"],
                "result_artifact": row["result_artifact"],
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
        "repository": data["repository"],
        "adapter": data["adapter"],
        "sources": {"include": sources},
        "targets": {"include": targets},
        "harnesses": {"include": harnesses},
        "source_count": len(sources),
        "target_count": len(targets),
        "harness_count": len(harnesses),
    }


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
    if kind == "invocation":
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
            upstream = source_status.get((row["vmod"], row["channel"]), "missing")
            if upstream is not None and upstream != "passed":
                status = "blocked_by_vmod_source"
                detail = f"source/{row['vmod']}/{row['channel']} is {upstream}"
            elif upstream is None:
                status = "blocked_by_vmod_source"
                detail = (
                    f"source/{row['vmod']}/{row['channel']} produced no result record either"
                )
            elif invocation_status.get(row["vmod"]) == "failed_manifest_validation":
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
    scoped = {
        "tier": expected["tier"],
        "rows": [row for row in expected["rows"] if row["vmod"] == vmod],
    }
    resolved = reconcile(scoped, observed)
    records = []
    for row in resolved["rows"]:
        if row["observed"] or not row["selected"]:
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
    else:
        out.append(
            f"**{counts['required_failed']} required row(s) failed or are missing.** "
            "The run is red."
        )
    out.append("")

    vmods = []
    for row in resolved["rows"]:
        if row["vmod"] not in vmods:
            vmods.append(row["vmod"])
    for vmod in vmods:
        rows = [r for r in resolved["rows"] if r["vmod"] == vmod]
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
        errors.extend(source_cross_check_errors(data, str(path), args.source_dir))
    if errors:
        for error in errors:
            print(f"ERROR    {error}", file=sys.stderr)
        return 1
    print(f"OK: {path} is a valid {SCHEMA} manifest for {data['id']}")
    if args.source_dir:
        print(f"OK: {data['adapter']} source cross-check against {args.source_dir}")
    return 0


def source_cross_check_errors(data: dict, path: str, source_dir) -> list:
    """Adapter-specific checks that need the VMOD's source checked out.

    This is the half of validation that used to live in the global registry
    gate. It runs inside the VMOD's own invocation, after its checkout, so a
    cachetag source problem is a cachetag failure rather than a global registry
    failure.
    """
    if data["adapter"] != "cachetag":
        return [f"{path}: no source cross-check is implemented for adapter {data['adapter']!r}"]
    release = data["sources"].get("release")
    if release is None:
        return [f"{path}: the cachetag adapter needs a release source to cross-check"]
    try:
        found = manifest_mod.configure_ac_version(source_dir)
    except manifest_mod.ValidationError as exc:
        return [f"{path}: {exc}"]
    if found != release["version"]:
        return [
            f"{path}: sources.release.version {release['version']!r} does not match configure.ac "
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
        print(f"repository={result['repository']}")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
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
    import ci_matrix_selftest

    return ci_matrix_selftest.main()


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
    p_val.set_defaults(func=cmd_validate_vmod)

    p_exp = sub.add_parser("expand", help="expand one VMOD's lanes for one tier")
    p_exp.add_argument("--manifest", required=True)
    p_exp.add_argument("--id")
    p_exp.add_argument("--tier", required=True, choices=TIERS)
    p_exp.add_argument("--inject", choices=INJECTIONS, default="none")
    p_exp.add_argument("--format", choices=["json", "github"], default="json")
    p_exp.set_defaults(func=cmd_expand)

    p_led = sub.add_parser("ledger", help="the expected row ledger for a tier")
    p_led.add_argument("--tier", required=True, choices=TIERS)
    p_led.add_argument("--out")
    p_led.set_defaults(func=cmd_ledger)

    p_rec = sub.add_parser("record", help="write one machine-readable result record")
    p_rec.add_argument("--out", required=True)
    p_rec.add_argument("--kind", required=True, choices=["invocation", "source", "package-target", "source-harness"])
    p_rec.add_argument("--vmod", required=True)
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
    except (CatalogError, manifest_mod.ValidationError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR    {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
