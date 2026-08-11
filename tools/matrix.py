#!/usr/bin/env python3
"""The one host-safe CLI for the vcache-packaging-v2 catalog and matrix.

Subcommands (contract: DESIGN.md):

  validate   catalog well-formed; exit 1 with a clear message on any error
  expand     emit the GitHub Actions job matrix for a lane
  resolve    print the resolved ref+version for one (vmod, engine) pair
  env        sh-sourceable pins for the build scripts
  merge      fold cell result JSONs into the persistent state file
  render     pivot the state file into one self-contained HTML matrix page
  schema     write (or --check) the editor JSON Schemas under schemas/
  selftest   run tools/selftest.py

Catalog loading, the source-resolution rule, and every version string the
packaging layer uses live here; ``tools/recipe.py`` imports this module so
there is exactly one implementation of each.

Standard library only. Builds nothing, touches no network.
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml_subset  # noqa: E402

ENGINES_SCHEMA = "engines/1"
VMOD_SCHEMA = "vmod/1"
CELL_SCHEMA = "cell/1"
STATE_SCHEMA = "matrix-state/1"

FAMILIES = ("vinyl", "varnish")
KINDS = ("release", "trunk")
LANES = ("release", "trunk")
TARGET_FORMATS = ("deb", "rpm")
TARGET_PLATFORMS = ("linux/amd64", "linux/arm64")
# "engine" marks an engine's own build cell (row == engine id); the build
# scripts write it and the grid shows it on the shared "(engine)" display row.
MODES = ("compat", "package", "engine")
# GitHub Actions limits one job matrix to 256 configurations.  Keep shard
# matrices comfortably below that ceiling so adding a row never makes a
# workflow invalid, while still preserving one job per VMOD cell.
VMOD_SHARD_SIZE = 128
STATUSES = (
    "pass",
    "configure_failed",
    "build_failed",
    "load_failed",
    "test_failed",
    "package_failed",
    "install_failed",
    "infra_failed",
)
# The one legal value of a vmod manifest's optional top-level 'tests' key.
TESTS_VALUES = ("make-check",)
# The one legal value of a vmod manifest's optional top-level 'engine_source'
# key (decision 14): configure needs the engine source tree (VINYLSRC), which
# the build scripts then provision from the engine's own source pin.
ENGINE_SOURCE_VALUES = ("required",)
# VCL import names (package.modules entries). VMOD ids may contain hyphens
# (varnish-modules); module names may not.
MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Mapping keys the parser accepts; also the by_series key charset (DESIGN.md).
MAPPING_KEY_RE = re.compile(r"^[a-z0-9_.-]+$")

# The key sets of every catalog mapping, as (required, optional). One home:
# _load_engines/_load_vmods validate against this table and
# tools/jsonschema_gen.py emits the editor schemas from it, so the two cannot
# disagree about which keys exist (DESIGN.md decision 11).
KEYS = {
    "engines_doc": ({"schema", "targets", "engines"}, set()),
    "target": ({"image", "format", "runner", "platform", "package_arch"}, set()),
    "engine": ({"id", "family", "series", "kind", "source", "targets"}, {"packages"}),
    "engine_source_release": ({"tarball_url", "sha256"}, set()),
    "engine_source_trunk": ({"git_url", "branch"}, set()),
    "vmod_doc": ({"schema", "id", "upstream", "sources", "package"}, {"tests", "engine_source"}),
    "vmod_upstream": ({"git"}, {"homepage"}),
    "vmod_sources": ({"head", "default"}, {"by_series"}),
    "vmod_source_entry": ({"ref", "version"}, set()),
    "vmod_package": (
        {"summary", "description", "license"},
        {"build_deps", "build_target", "modules", "families", "promoted", "targets"},
    ),
    "vmod_build_deps": (set(), {"debian", "rpm"}),
}


class CatalogError(Exception):
    """The catalog or an input file is missing, unreadable, or invalid."""


def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_time(value: str) -> str:
    """Render a matrix timestamp for people while retaining its ISO value in HTML."""
    try:
        timestamp = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return value
    return f"{timestamp.day} {timestamp.strftime('%B %Y at %H:%M UTC')}"


# ---------------------------------------------------------------------------
# Catalog loading and validation
# ---------------------------------------------------------------------------


def _expect_keys(mapping: dict, required: set, allowed: set, ctx: str, errors: list) -> None:
    for key in sorted(required - set(mapping)):
        errors.append(f"{ctx}: missing required key {key!r}")
    for key in sorted(set(mapping) - allowed):
        errors.append(f"{ctx}: unknown key {key!r}")


def _expect(mapping: dict, kind: str, ctx: str, errors: list) -> None:
    """_expect_keys against the named entry of the KEYS table."""
    required, optional = KEYS[kind]
    _expect_keys(mapping, required, required | optional, ctx, errors)


def _str_value(mapping: dict, key: str, ctx: str, errors: list) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or value == "":
        if key in mapping:
            errors.append(f"{ctx}: {key!r} must be a non-empty string")
        return ""
    return value


def _str_list(value, ctx: str, errors: list) -> list:
    if not isinstance(value, list) or not value or not all(isinstance(i, str) and i for i in value):
        errors.append(f"{ctx}: must be a non-empty list of strings")
        return []
    return value


def _load_engines(path: Path, errors: list) -> tuple[list, dict]:
    if not path.is_file():
        errors.append(f"{path}: engine catalog not found (expected engines.yml at the repo root)")
        return [], {}
    try:
        doc = yaml_subset.parse_file(path)
    except yaml_subset.ManifestSyntaxError as exc:
        errors.append(str(exc))
        return [], {}
    ctx = str(path)
    _expect(doc, "engines_doc", ctx, errors)
    if doc.get("schema") != ENGINES_SCHEMA:
        errors.append(f"{ctx}: schema must be {ENGINES_SCHEMA!r}, got {doc.get('schema')!r}")
    known_targets = _load_targets(doc.get("targets"), ctx, errors)
    engines = doc.get("engines")
    if not isinstance(engines, list) or not engines:
        errors.append(f"{ctx}: 'engines' must be a non-empty list")
        return [], known_targets
    out = []
    seen = set()
    for i, engine in enumerate(engines):
        ectx = f"{ctx}: engines[{i}]"
        if not isinstance(engine, dict):
            errors.append(f"{ectx}: must be a mapping")
            continue
        _expect(engine, "engine", ectx, errors)
        eid = _str_value(engine, "id", ectx, errors)
        if eid:
            ectx = f"{ctx}: engine {eid!r}"
            if eid in seen:
                errors.append(f"{ectx}: duplicate id")
            seen.add(eid)
        family = _str_value(engine, "family", ectx, errors)
        if family and family not in FAMILIES:
            errors.append(f"{ectx}: family must be one of {FAMILIES}, got {family!r}")
        kind = _str_value(engine, "kind", ectx, errors)
        if kind and kind not in KINDS:
            errors.append(f"{ectx}: kind must be one of {KINDS}, got {kind!r}")
        _str_value(engine, "series", ectx, errors)
        packages = engine.get("packages", "false")
        if packages not in ("true", "false"):
            errors.append(f'{ectx}: packages must be "true" or "false", got {packages!r}')
            packages = "false"
        engine = dict(engine)
        engine["packages"] = packages
        if packages == "true":
            if kind != "release":
                errors.append(f'{ectx}: packages "true" requires kind release')
            if family != "vinyl":
                errors.append(f'{ectx}: packages "true" requires family vinyl')
        source = engine.get("source")
        if not isinstance(source, dict):
            errors.append(f"{ectx}: 'source' must be a mapping")
        elif kind == "release":
            _expect(source, "engine_source_release", f"{ectx}: source", errors)
            _str_value(source, "tarball_url", f"{ectx}: source", errors)
            _str_value(source, "sha256", f"{ectx}: source", errors)
        elif kind == "trunk":
            _expect(source, "engine_source_trunk", f"{ectx}: source", errors)
            _str_value(source, "git_url", f"{ectx}: source", errors)
            _str_value(source, "branch", f"{ectx}: source", errors)
        engine_target_ids = _str_list(engine.get("targets"), f"{ectx}: targets", errors)
        if len(engine_target_ids) != len(set(engine_target_ids)):
            errors.append(f"{ectx}: targets contains duplicates")
        for target in engine_target_ids:
            if target not in known_targets:
                errors.append(f"{ectx}: targets includes unknown target {target!r}")
        out.append(engine)
    return out, known_targets


def _load_targets(value, ctx: str, errors: list) -> dict:
    if not isinstance(value, dict) or not value:
        errors.append(f"{ctx}: 'targets' must be a non-empty mapping")
        return {}
    out = {}
    for target_id, target in value.items():
        tctx = f"{ctx}: targets[{target_id!r}]"
        if not isinstance(target_id, str) or not MAPPING_KEY_RE.match(target_id):
            errors.append(f"{tctx}: target id must match {MAPPING_KEY_RE.pattern!r}")
            continue
        if not isinstance(target, dict):
            errors.append(f"{tctx}: must be a mapping")
            continue
        _expect(target, "target", tctx, errors)
        target = dict(target)
        for key in KEYS["target"][0]:
            target[key] = _str_value(target, key, tctx, errors)
        if target["format"] and target["format"] not in TARGET_FORMATS:
            errors.append(f"{tctx}: format must be one of {TARGET_FORMATS}, got {target['format']!r}")
        if target["platform"] and target["platform"] not in TARGET_PLATFORMS:
            errors.append(f"{tctx}: platform must be one of {TARGET_PLATFORMS}, got {target['platform']!r}")
        out[target_id] = target
    return out


def _load_vmods(dirpath: Path, engines: list, targets: dict, errors: list) -> dict:
    if not dirpath.is_dir():
        errors.append(f"{dirpath}: vmods directory not found")
        return {}
    known_series = {e.get("series") for e in engines}
    vmods = {}
    for path in sorted(dirpath.glob("*.yml")):
        ctx = str(path)
        try:
            doc = yaml_subset.parse_file(path)
        except yaml_subset.ManifestSyntaxError as exc:
            errors.append(str(exc))
            continue
        _expect(doc, "vmod_doc", ctx, errors)
        if doc.get("schema") != VMOD_SCHEMA:
            errors.append(f"{ctx}: schema must be {VMOD_SCHEMA!r}, got {doc.get('schema')!r}")
        vid = _str_value(doc, "id", ctx, errors)
        if vid and vid != path.stem:
            errors.append(f"{ctx}: id {vid!r} does not match the filename stem {path.stem!r}")
        upstream = doc.get("upstream")
        if not isinstance(upstream, dict):
            errors.append(f"{ctx}: 'upstream' must be a mapping")
        else:
            _expect(upstream, "vmod_upstream", f"{ctx}: upstream", errors)
            _str_value(upstream, "git", f"{ctx}: upstream", errors)
        sources = doc.get("sources")
        if not isinstance(sources, dict):
            errors.append(f"{ctx}: 'sources' must be a mapping")
        else:
            _expect(sources, "vmod_sources", f"{ctx}: sources", errors)
            _str_value(sources, "head", f"{ctx}: sources", errors)
            default = sources.get("default")
            if not isinstance(default, dict):
                errors.append(f"{ctx}: sources.default must be a mapping")
            else:
                _check_source_entry(default, f"{ctx}: sources.default", errors)
            by_series = sources.get("by_series")
            if by_series is not None:
                if not isinstance(by_series, dict) or not by_series:
                    errors.append(f"{ctx}: sources.by_series must be a non-empty mapping")
                else:
                    for series, entry in by_series.items():
                        sctx = f"{ctx}: sources.by_series[{series!r}]"
                        if engines and series not in known_series:
                            errors.append(f"{sctx}: no engine declares this series")
                        if not isinstance(entry, dict):
                            errors.append(f"{sctx}: must be a mapping")
                        else:
                            _check_source_entry(entry, sctx, errors)
        if "tests" in doc and doc.get("tests") not in TESTS_VALUES:
            errors.append(f"{ctx}: tests must be one of {TESTS_VALUES}, got {doc.get('tests')!r}")
        if "engine_source" in doc and doc.get("engine_source") not in ENGINE_SOURCE_VALUES:
            errors.append(
                f"{ctx}: engine_source must be one of {ENGINE_SOURCE_VALUES}, got {doc.get('engine_source')!r}"
            )
        package = doc.get("package")
        if not isinstance(package, dict):
            errors.append(f"{ctx}: 'package' must be a mapping")
        else:
            _expect(package, "vmod_package", f"{ctx}: package", errors)
            _str_value(package, "summary", f"{ctx}: package", errors)
            _str_value(package, "license", f"{ctx}: package", errors)
            if "description" in package:
                _str_list(package.get("description"), f"{ctx}: package.description", errors)
            if "build_target" in package:
                _str_value(package, "build_target", f"{ctx}: package", errors)
            build_deps = package.get("build_deps")
            if build_deps is not None:
                if not isinstance(build_deps, dict):
                    errors.append(f"{ctx}: package.build_deps must be a mapping")
                else:
                    _expect(build_deps, "vmod_build_deps", f"{ctx}: package.build_deps", errors)
                    for eco in ("debian", "rpm"):
                        if eco in build_deps:
                            _str_list(build_deps[eco], f"{ctx}: package.build_deps.{eco}", errors)
            modules = package.get("modules")
            if modules is not None:
                names = _str_list(modules, f"{ctx}: package.modules", errors)
                for i, name in enumerate(names):
                    if not MODULE_NAME_RE.match(name):
                        errors.append(
                            f"{ctx}: package.modules[{i}]: {name!r} is not a valid module name"
                            " (must match [a-z][a-z0-9_]*)"
                        )
            elif vid and not MODULE_NAME_RE.match(vid):
                errors.append(
                    f"{ctx}: package.modules is required because id {vid!r} is not a valid"
                    " module name to default to"
                )
            families = package.get("families")
            if families is not None:
                # Absent means every family, so an explicit empty list is
                # almost certainly a mistake; _str_list rejects it.
                values = _str_list(families, f"{ctx}: package.families", errors)
                for i, family in enumerate(values):
                    if family not in FAMILIES:
                        errors.append(
                            f"{ctx}: package.families[{i}]: family must be one of {FAMILIES},"
                            f" got {family!r}"
                        )
                if len(values) != len(set(values)):
                    errors.append(f"{ctx}: package.families contains duplicates")
            promoted = package.get("promoted")
            if promoted is not None and promoted not in ("true", "false"):
                errors.append(
                    f'{ctx}: package.promoted must be "true" or "false", got {promoted!r}'
                )
            pkg_targets = package.get("targets")
            if pkg_targets is not None:
                # Absent means every target, so an explicit empty list is
                # almost certainly a mistake; _str_list rejects it.
                values = _str_list(pkg_targets, f"{ctx}: package.targets", errors)
                for i, tid in enumerate(values):
                    if tid not in targets:
                        errors.append(
                            f"{ctx}: package.targets[{i}]: unknown target {tid!r};"
                            f" known targets: {', '.join(sorted(targets))}"
                        )
                if len(values) != len(set(values)):
                    errors.append(f"{ctx}: package.targets contains duplicates")
        if vid:
            vmods[vid] = doc
    return vmods


def _check_source_entry(entry: dict, ctx: str, errors: list) -> None:
    _expect(entry, "vmod_source_entry", ctx, errors)
    _str_value(entry, "ref", ctx, errors)
    _str_value(entry, "version", ctx, errors)


def load_catalog(root) -> dict:
    root = Path(root)
    errors: list = []
    engines, targets = _load_engines(root / "engines.yml", errors)
    vmods = _load_vmods(root / "vmods", engines, targets, errors)
    if errors:
        raise CatalogError("\n".join(errors))
    return {"engines": engines, "targets": targets, "vmods": vmods}


def find_engine(catalog: dict, engine_id: str) -> dict:
    for engine in catalog["engines"]:
        if engine["id"] == engine_id:
            return engine
    known = ", ".join(e["id"] for e in catalog["engines"])
    raise CatalogError(f"unknown engine {engine_id!r}; known engines: {known}")


def find_target(catalog: dict, target_id: str) -> dict:
    target = catalog["targets"].get(target_id)
    if target is None:
        known = ", ".join(catalog["targets"])
        raise CatalogError(f"unknown target {target_id!r}; known targets: {known}")
    return target


def find_vmod(catalog: dict, vmod_id: str) -> dict:
    vmod = catalog["vmods"].get(vmod_id)
    if vmod is None:
        known = ", ".join(catalog["vmods"])
        raise CatalogError(f"unknown vmod {vmod_id!r}; known vmods: {known}")
    return vmod


# ---------------------------------------------------------------------------
# Versions and source resolution
# ---------------------------------------------------------------------------


def engine_version(engine: dict) -> str:
    """The engine's version string, derived from its id: family prefix stripped.

    ``vinyl-9.0.1`` -> ``9.0.1``; ``vinyl-trunk`` -> ``trunk``.
    """
    prefix = engine["family"] + "-"
    if engine["id"].startswith(prefix):
        return engine["id"][len(prefix):]
    return engine["id"]


def engine_package_version(engine: dict) -> dict:
    """The engine package's own version, as each format's dependency writes it.

    The engine packages are always revision 1 of their upstream version
    (there is exactly one packaging of each pinned engine release). The RPM
    string carries ``%{?dist}`` so a spec's exact-version Requires matches the
    dist-tagged release the engine spec produces in the same buildroot.
    """
    version = engine_version(engine)
    return {"deb": f"{version}-1", "rpm": f"{version}-1%{{?dist}}"}


def vmod_package_name(vmod_id: str) -> str:
    return f"vinyl-vmod-{vmod_id}"


def vmod_modules(vmod: dict) -> list:
    """The VCL import names the VMOD ships: ``package.modules``, defaulting to
    ``[<id>]`` when absent (DESIGN.md)."""
    return vmod["package"].get("modules") or [vmod["id"]]


def vmod_package_version(upstream_version: str, engine: dict) -> dict:
    """DESIGN.md naming: Debian ``<v>-1~vinyl<ev>``, RPM release ``1.vinyl<ev>``."""
    ev = engine_version(engine)
    return {
        "deb": f"{upstream_version}-1~vinyl{ev}",
        "rpm_version": upstream_version,
        "rpm_release": f"1.vinyl{ev}",
    }


def resolve_source(vmod: dict, engine: dict) -> dict:
    """The one source-resolution rule (DESIGN.md).

    Trunk engine -> ``sources.head`` (a branch; no package version, so
    ``version`` is empty). Release engine -> ``sources.by_series[series]``
    if present, else ``sources.default``.
    """
    if engine["kind"] == "trunk":
        return {"source": "head", "ref": vmod["sources"]["head"], "version": ""}
    by_series = vmod["sources"].get("by_series") or {}
    entry = by_series.get(engine["series"])
    if entry is not None:
        return {"source": "by_series", "ref": entry["ref"], "version": entry["version"]}
    default = vmod["sources"]["default"]
    return {"source": "default", "ref": default["ref"], "version": default["version"]}


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


def lane_engines(catalog: dict, lane: str) -> list:
    kind = "release" if lane == "release" else "trunk"
    return [e for e in catalog["engines"] if e["kind"] == kind]


def engine_targets(engine: dict, lane: str, mode: str) -> list:
    """The unique targets an engine must be built on for a lane and mode filter.

    Compat cells run on every listed target. Package cells also run on every
    listed target of a packages-"true" engine (release lane only).
    """
    targets = []
    if mode in ("compat", "all"):
        targets.extend(engine["targets"])
    if lane == "release" and engine["packages"] == "true" and mode in ("package", "all"):
        for target in engine["targets"]:
            if target not in targets:
                targets.append(target)
    return targets


def expand(catalog: dict, lane: str, mode: str = "all") -> dict:
    """Expand one lane into engine build pairs and VMOD cell rows.

    Returns ``{"engines": [{engine, target, runner}...], "vmods": [{row,
    engine, target, mode, runner}...], "rows": [...]}`` where ``rows`` is the full cell list
    including the engines' own build cells (mode ``engine``).
    """
    engine_pairs = []
    vmod_rows = []
    for engine in lane_engines(catalog, lane):
        for target in engine_targets(engine, lane, mode):
            runner = find_target(catalog, target)["runner"]
            engine_pairs.append({"engine": engine["id"], "target": target, "runner": runner})
        if mode in ("compat", "all"):
            for target in engine["targets"]:
                runner = find_target(catalog, target)["runner"]
                for vid in catalog["vmods"]:
                    vmod_rows.append({"row": vid, "engine": engine["id"], "target": target, "mode": "compat", "runner": runner})
        if lane == "release" and engine["packages"] == "true" and mode in ("package", "all"):
            for target in engine["targets"]:
                runner = find_target(catalog, target)["runner"]
                for vid, vmod in catalog["vmods"].items():
                    # package.families gates package cells only (DESIGN.md
                    # decision 13); absent means every family. Compat cells
                    # above are deliberately untouched: an incompatible
                    # pairing still renders its honest red.
                    families = vmod["package"].get("families")
                    if families is not None and engine["family"] not in families:
                        continue
                    # Promotion gate (decision 15): package cells exist only
                    # once the catalog says promoted: "true", so an unproven
                    # entry can never block the all-or-nothing release gate.
                    if vmod["package"].get("promoted") != "true":
                        continue
                    # package.targets restricts where packaging can work at
                    # all (e.g. an x86_64-only VMOD); absent = every target.
                    pkg_targets = vmod["package"].get("targets")
                    if pkg_targets is not None and target not in pkg_targets:
                        continue
                    vmod_rows.append({"row": vid, "engine": engine["id"], "target": target, "mode": "package", "runner": runner})
    engine_rows = [
        {"row": pair["engine"], "engine": pair["engine"], "target": pair["target"], "mode": "engine", "runner": pair["runner"]}
        for pair in engine_pairs
    ]
    return {"engines": engine_pairs, "vmods": vmod_rows, "rows": engine_rows + vmod_rows}


def shard_vmods(rows: list, size: int = VMOD_SHARD_SIZE) -> list:
    """Return reusable-workflow matrix rows holding bounded VMOD matrices.

    Each ``items`` value is JSON text because it crosses a workflow_call input
    boundary.  The caller matrices the shards; the called workflow expands the
    contained VMOD rows.  Both matrices therefore remain below GitHub's 256
    configuration limit.
    """
    if size < 1:
        raise ValueError("VMOD shard size must be positive")
    shard_count = (len(rows) + size - 1) // size
    return [
        {
            "shard": f"{index + 1}/{shard_count}",
            "items": json.dumps(rows[start:start + size], separators=(",", ":")),
        }
        for index, start in enumerate(range(0, len(rows), size))
    ]


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def target_format(catalog: dict, target_id: str) -> str:
    """Return a target's declared package format. Shared with recipe.py."""
    return find_target(catalog, target_id)["format"]


def env_pairs(catalog: dict, engine_id: str, vmod_id: str = None, target_id: str = None) -> list:
    engine = find_engine(catalog, engine_id)
    ev = engine_version(engine)
    pairs = [
        ("ENGINE_ID", engine["id"]),
        ("ENGINE_FAMILY", engine["family"]),
        ("ENGINE_SERIES", engine["series"]),
        ("ENGINE_KIND", engine["kind"]),
        ("ENGINE_VERSION", ev),
        ("ENGINE_PACKAGES", engine["packages"]),
    ]
    source = engine["source"]
    if engine["kind"] == "release":
        pairs += [("ENGINE_TARBALL_URL", source["tarball_url"]), ("ENGINE_SHA256", source["sha256"])]
    else:
        pairs += [("ENGINE_GIT_URL", source["git_url"]), ("ENGINE_BRANCH", source["branch"])]
    if engine["packages"] == "true":
        pairs += [("ENGINE_DEB_VERSION", f"{ev}-1"), ("ENGINE_RPM_VERSION", ev), ("ENGINE_RPM_RELEASE", "1")]
    if target_id is not None:
        if target_id not in engine["targets"]:
            raise CatalogError(
                f"target {target_id!r} is not a target of engine {engine_id!r} (targets: {engine['targets']})"
            )
        target = find_target(catalog, target_id)
        pairs += [
            ("TARGET_ID", target_id),
            ("TARGET_IMAGE", target["image"]),
            ("TARGET_FORMAT", target["format"]),
            ("TARGET_PLATFORM", target["platform"]),
            ("TARGET_PACKAGE_ARCH", target["package_arch"]),
        ]
    if vmod_id is not None:
        vmod = find_vmod(catalog, vmod_id)
        package = vmod["package"]
        resolved = resolve_source(vmod, engine)
        # The manifest's extra build dependencies for the target's package
        # format, space-separated for the build scripts to install. Without
        # --target, the engine's first listed target decides the format (that
        # is the target compat cells run on), so the variable is always
        # present when --vmod is given, like VMOD_REF.
        fmt = target_format(catalog, target_id if target_id is not None else engine["targets"][0])
        build_deps = vmod["package"].get("build_deps") or {}
        deps = build_deps.get("debian" if fmt == "deb" else "rpm", [])
        pairs += [
            ("VMOD_ID", vmod["id"]),
            ("VMOD_GIT", vmod["upstream"]["git"]),
            ("VMOD_SOURCE", resolved["source"]),
            ("VMOD_REF", resolved["ref"]),
            ("VMOD_VERSION", resolved["version"]),
            ("VMOD_BUILD_DEPS", " ".join(deps)),
            ("VMOD_BUILD_TARGET", package.get("build_target", "all")),
            ("VMOD_MODULES", " ".join(vmod_modules(vmod))),
            ("VMOD_TESTS", vmod.get("tests", "")),
            ("VMOD_ENGINE_SOURCE", vmod.get("engine_source", "")),
            ("VMOD_PACKAGE_NAME", vmod_package_name(vmod["id"])),
        ]
        if resolved["version"]:
            pv = vmod_package_version(resolved["version"], engine)
            pairs += [
                ("VMOD_DEB_VERSION", pv["deb"]),
                ("VMOD_RPM_VERSION", pv["rpm_version"]),
                ("VMOD_RPM_RELEASE", pv["rpm_release"]),
            ]
    return pairs


# ---------------------------------------------------------------------------
# Cell results, state, merge
# ---------------------------------------------------------------------------

CELL_REQUIRED = ("schema", "row", "engine", "target", "mode", "status", "finished_at")
CELL_OPTIONAL = ("ref", "commit", "detail", "run_url")


def load_cell(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise CatalogError(f"{path}: a cell result must be a JSON object")
    if data.get("schema") != CELL_SCHEMA:
        raise CatalogError(f"{path}: schema must be {CELL_SCHEMA!r}, got {data.get('schema')!r}")
    for key in CELL_REQUIRED:
        if not isinstance(data.get(key), str) or data.get(key) == "":
            raise CatalogError(f"{path}: missing or empty required cell key {key!r}")
    if data["mode"] not in MODES:
        raise CatalogError(f"{path}: mode must be one of {MODES}, got {data['mode']!r}")
    if data["status"] not in STATUSES:
        raise CatalogError(f"{path}: status must be one of {STATUSES}, got {data['status']!r}")
    cell = {key: data[key] for key in CELL_REQUIRED}
    for key in CELL_OPTIONAL:
        value = data.get(key, "")
        cell[key] = value if isinstance(value, str) else ""
    return cell


def cell_key(cell: dict) -> str:
    return "/".join((cell["row"], cell["engine"], cell["target"], cell["mode"]))


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"schema": STATE_SCHEMA, "cells": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != STATE_SCHEMA:
        raise CatalogError(f"{path}: schema must be {STATE_SCHEMA!r}, got {data.get('schema')!r}")
    if not isinstance(data.get("cells"), dict):
        raise CatalogError(f"{path}: 'cells' must be an object")
    return data


def merge_cells(state: dict, cells: list) -> int:
    """Newest finished_at per (row, engine, target, mode) wins; ties go to the
    incoming cell. Returns how many cells were applied."""
    applied = 0
    for cell in cells:
        key = cell_key(cell)
        existing = state["cells"].get(key)
        if existing is None or cell["finished_at"] >= existing["finished_at"]:
            state["cells"][key] = cell
            applied += 1
    return applied


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

_BUCKET_RANK = {"MISSING": 0, "PASS": 1, "INFRA": 2, "FAIL": 3}
_SHORT_STATUS = {
    "pass": "pass",
    "configure_failed": "configure",
    "build_failed": "build",
    "load_failed": "load",
    "test_failed": "test",
    "package_failed": "package",
    "install_failed": "install",
    "infra_failed": "infra",
}
# Reader-facing sentence per (mode, bucket). The tooltip is the page's detail
# surface and speaks human: one such sentence per mode, with its raw status.
_MODE_SENTENCE = {
    ("engine", "PASS"): "This engine version built from source.",
    ("engine", "FAIL"): "This engine version failed to build.",
    ("engine", "INFRA"): "This engine version could not be tested (harness problem).",
    ("compat", "PASS"): "This module compiles from source against this engine and loads.",
    ("compat", "FAIL"): "This module fails to compile or load against this engine "
                        "(usually: upstream does not support this engine yet).",
    ("compat", "INFRA"): "This module could not be tested against this engine (harness problem).",
    ("package", "PASS"): "The ready-to-install package (.deb/.rpm) built, installed and loaded.",
    ("package", "FAIL"): "The ready-to-install package (.deb/.rpm) failed to build or install.",
    ("package", "INFRA"): "The ready-to-install package (.deb/.rpm) could not be tested (harness problem).",
}


def classify(status: str) -> str:
    if status == "pass":
        return "PASS"
    if status == "infra_failed":
        return "INFRA"
    return "FAIL"


def display_row(cell: dict) -> str:
    """An engine's own build cell renders on the shared '(engine)' row."""
    return "(engine)" if cell["mode"] == "engine" else cell["row"]


def matrix_targets(state: dict, catalog: dict = None) -> list:
    """Return targets in catalog order, followed by stale-state targets."""
    targets = []
    if catalog is not None:
        for engine in catalog["engines"]:
            for target in engine["targets"]:
                if target not in targets:
                    targets.append(target)
    return targets + sorted({c["target"] for c in state["cells"].values()} - set(targets))


def build_grid(state: dict, target: str, catalog: dict = None) -> dict:
    """Pivot one target's merged cells into one visual cell per row and engine.

    Axis order comes from the catalog when it is loadable (columns: release
    engines then trunk engines, in engines.yml order; rows: the engine build
    row, then VMODs in catalog order). Only engines configured for ``target``
    are shown. Anything present only in the state is appended sorted, so stale
    state still renders instead of erroring.
    """
    cells = [cell for cell in state["cells"].values() if cell["target"] == target]
    if catalog is not None:
        columns = [e["id"] for e in catalog["engines"] if e["kind"] == "release" and target in e["targets"]]
        columns += [e["id"] for e in catalog["engines"] if e["kind"] == "trunk" and target in e["targets"]]
        row_ids = ["(engine)"] + list(catalog["vmods"])
        row_urls = {row_id: vmod["upstream"].get("homepage", "") for row_id, vmod in catalog["vmods"].items()}
    else:
        columns = []
        row_ids = ["(engine)"]
        row_urls = {}
    columns += sorted({c["engine"] for c in cells} - set(columns))
    row_ids += sorted({display_row(c) for c in cells} - set(row_ids))

    groups: dict = {}
    for cell in cells:
        groups.setdefault((display_row(cell), cell["engine"]), []).append(cell)

    grid_cells: dict = {}
    counts = {"PASS": 0, "FAIL": 0, "INFRA": 0, "MISSING": 0}
    for row_id in row_ids:
        for col in columns:
            group = groups.get((row_id, col), [])
            if not group:
                counts["MISSING"] += 1
                continue
            # Worst bucket wins the colour; within the group, the worst-then-
            # newest cell supplies the label and link.
            top = max(group, key=lambda c: (_BUCKET_RANK[classify(c["status"])], c["finished_at"]))
            bucket = classify(top["status"])
            lines = []
            for cell in sorted(group, key=lambda c: (c["target"], c["mode"])):
                sentence = _MODE_SENTENCE[cell["mode"], classify(cell["status"])]
                line = f"{cell['mode']}: {sentence} ({cell['status']})"
                if cell.get("ref"):
                    line += f" [{cell['ref']}"
                    if cell.get("commit"):
                        line += f" @ {cell['commit'][:12]}"
                    line += "]"
                if cell.get("detail"):
                    line += f" — {cell['detail']}"
                line += f" ({cell['finished_at']})"
                lines.append(line)
            grid_cells[(row_id, col)] = {
                "bucket": bucket,
                "text": _SHORT_STATUS[top["status"]],
                "title": "\n".join(lines),
                "run_url": top.get("run_url", ""),
            }
            counts[bucket] += 1
    return {"target": target, "columns": columns, "rows": row_ids, "row_urls": row_urls,
            "cells": grid_cells, "counts": counts}


# Light palette on bare :root; dark redefined under prefers-color-scheme
# (guarded so an explicit light choice wins) and again under [data-theme=dark]
# so the toggle wins in both directions. Ported from v1 status_page.py.
_STYLE = """
:root{
  --bg:#ffffff; --surface:#ffffff; --surface-2:#f1f3f4; --ink:#202124;
  --muted:#5f6368; --line:#dadce0; --line-2:#c4c7c5;
  --pass:#1e8e3e; --fail:#d93025; --na:#bdc1c6;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#202124; --surface:#292a2d; --surface-2:#35363a; --ink:#e8eaed;
  --muted:#9aa0a6; --line:#3c4043; --line-2:#5f6368;
  --pass:#1e8e3e; --fail:#d93025; --na:#5f6368;
}}
:root[data-theme="dark"]{
  --bg:#202124; --surface:#292a2d; --surface-2:#35363a; --ink:#e8eaed;
  --muted:#9aa0a6; --line:#3c4043; --line-2:#5f6368;
  --pass:#1e8e3e; --fail:#d93025; --na:#5f6368;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.5}
a{color:inherit;text-decoration:none}
header.page{display:flex;flex-wrap:wrap;align-items:center;min-height:65px;gap:8px 20px;padding:8px clamp(16px,3vw,40px);
  border-bottom:1px solid var(--line);background:var(--surface)}
header.page h1{font-size:16px;line-height:1.15;margin:0;font-weight:700}
header.page h1 span{display:block}
header.page h1 .title-context{font-size:12px;font-weight:600;letter-spacing:.02em;color:var(--muted)}
header.page .gen{font-family:var(--mono);font-size:12px;color:var(--muted)}
.legend{display:flex;gap:14px;align-items:center;margin-left:auto;font-family:var(--mono);font-size:11.5px;
  color:var(--muted);flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:5px}
.swatch{width:13px;height:13px;display:inline-block}
.sw-pass{background:var(--pass)}.sw-fail{background:var(--fail)}.sw-infra{background:var(--na)}
.sw-missing{background:repeating-linear-gradient(45deg,var(--na),var(--na) 2px,transparent 2px,transparent 5px)}
main{padding:20px clamp(16px,3vw,40px) 60px}
.target{margin:0 0 8px;font-family:var(--mono);font-size:14px}
.target-matrix+.target-matrix{margin-top:30px}
.matrix-scroll{overflow-x:auto;border:1px solid var(--line-2);background:var(--surface);max-width:100%}
table.matrix{border-collapse:separate;border-spacing:0;font-family:var(--mono);font-size:12px}
th.corner,th.col{background:var(--surface-2);border-bottom:1px solid var(--line-2);padding:8px 12px;
  font-size:11px;color:var(--muted);text-align:left}
th.col{text-align:center;border-right:1px solid var(--line);min-width:110px}
td.rid{border-right:1px solid var(--line-2);border-bottom:1px solid var(--line);padding:4px 12px;
  white-space:nowrap;background:var(--surface)}
tr.engine-row td.rid{font-style:italic;font-weight:700}
td.rid a{text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}
td.cell{min-width:110px;height:28px;padding:0;text-align:center;border-bottom:1px solid var(--line);
  border-right:1px solid var(--line);font-size:11px;font-weight:700}
td.cell a,td.cell span.v{display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:inherit}
td.cell.PASS{background:var(--pass);color:#fff}
td.cell.FAIL{background:var(--fail);color:#fff}
td.cell.INFRA{background:var(--na);color:var(--bg)}
td.cell.MISSING{color:var(--na);
  background-image:repeating-linear-gradient(45deg,var(--surface-2),var(--surface-2) 3px,transparent 3px,transparent 7px)}
.matrix-key{margin:0 0 20px;max-width:75em;font-size:13px;color:var(--muted)}
.page-foot{font-family:var(--mono);font-size:11px;color:var(--muted);padding:16px clamp(16px,3vw,40px) 30px}
.theme-btn{font-family:var(--mono);border:1px solid var(--line-2);background:var(--surface);color:var(--ink);
  cursor:pointer;padding:5px 9px;font-size:12px}
""".strip()

_SCRIPT = """
(function(){
  var root=document.documentElement;
  var saved=localStorage.getItem('matrix-theme');
  if(saved==='light'||saved==='dark'){root.setAttribute('data-theme',saved);}
  var btn=document.getElementById('theme-toggle');
  if(!btn){return;}
  btn.addEventListener('click',function(){
    var current=root.getAttribute('data-theme');
    var next=current==='dark'?'light':(current==='light'?'':'dark');
    if(next===''){root.removeAttribute('data-theme');localStorage.removeItem('matrix-theme');}
    else{root.setAttribute('data-theme',next);localStorage.setItem('matrix-theme',next);}
  });
})();
""".strip()


def _esc(value) -> str:
    return _html.escape(str(value), quote=True)


def _cell_html(cell) -> str:
    if cell is None:
        return '<td class="cell MISSING" title="no result recorded"><span class="v">&middot;</span></td>'
    text = _esc(cell["text"])
    inner = (
        f'<a href="{_esc(cell["run_url"])}" target="_blank" rel="noopener">{text}</a>'
        if cell["run_url"]
        else f'<span class="v">{text}</span>'
    )
    return f'<td class="cell {cell["bucket"]}" title="{_esc(cell["title"])}">{inner}</td>'


def _grid_html(grid: dict) -> str:
    counts = grid["counts"]
    head_cols = "".join(f'<th class="col">{_esc(col)}</th>' for col in grid["columns"])
    body_rows = []
    for row_id in grid["rows"]:
        row_class = "engine-row" if row_id == "(engine)" else ""
        label = "engine build" if row_id == "(engine)" else row_id
        label = _esc(label)
        row_url = grid["row_urls"].get(row_id)
        if row_url:
            label = f'<a href="{_esc(row_url)}" target="_blank" rel="noopener">{label}</a>'
        tds = "".join(_cell_html(grid["cells"].get((row_id, col))) for col in grid["columns"])
        body_rows.append(f'<tr class="{row_class}"><td class="rid">{label}</td>{tds}</tr>')
    return f'''<section class="target-matrix">
  <h2 class="target">{_esc(grid["target"])} &middot; {counts["PASS"]} pass, {counts["FAIL"]} fail, {counts["INFRA"]} infra, {counts["MISSING"]} missing</h2>
  <div class="matrix-scroll">
    <table class="matrix">
      <thead><tr><th class="corner">VMOD \\ engine</th>{head_cols}</tr></thead>
      <tbody>{"".join(body_rows)}</tbody>
    </table>
  </div>
</section>'''


def render_html(grids: list, generated_at: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vinyl Cache and Varnish Cache VMOD compatibility matrix</title>
<style>{_STYLE}</style>
</head>
<body>
<header class="page">
  <h1><span>Vinyl Cache and Varnish Cache</span><span class="title-context">VMOD compatibility matrix</span></h1>
  <span class="gen">generated <time datetime="{_esc(generated_at)}">{_esc(human_time(generated_at))}</time></span>
  <div class="legend">
    <span><i class="swatch sw-pass"></i>pass</span>
    <span><i class="swatch sw-fail"></i>fail</span>
    <span><i class="swatch sw-infra"></i>infra</span>
    <span><i class="swatch sw-missing"></i>no data</span>
  </div>
  <button class="theme-btn" id="theme-toggle" type="button" aria-label="Toggle theme">&#9680; theme</button>
</header>
<main>
  <p class="matrix-key">Rows are modules, columns are engine versions. Green: works. Red: doesn't — usually upstream doesn't support that engine yet. Grey: not tested.</p>
  {"".join(_grid_html(grid) for grid in grids)}
</main>
<p class="page-foot">Generated by tools/matrix.py &mdash; do not edit. A red cell is information, not an
  emergency: it means that VMOD does not build or load against that engine. Cells link to the run that
  produced them; hover for detail.</p>
<script>{_SCRIPT}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_validate(args) -> int:
    catalog = load_catalog(args.root)
    print(f"ok: {len(catalog['engines'])} engine(s), {len(catalog['vmods'])} vmod(s)")
    return 0


def cmd_expand(args) -> int:
    catalog = load_catalog(args.root)
    if args.lane == "trunk" and args.mode == "package":
        raise CatalogError("the trunk lane has no package cells; use --mode compat or all")
    expansion = expand(catalog, args.lane, args.mode)
    if not expansion["engines"] or not expansion["vmods"]:
        raise CatalogError(f"lane {args.lane!r} expanded to an empty matrix; the catalog has no engines or vmods for it")
    if args.format == "github":
        # Three `key=<json>` lines, appended verbatim to $GITHUB_OUTPUT.
        # Engine rows are excluded from vmods= (they would become bogus VMOD
        # jobs). vmod_shards= is the outer matrix for the reusable VMOD
        # workflow; each item contains at most VMOD_SHARD_SIZE inner rows.
        print("engines=" + json.dumps(expansion["engines"], separators=(",", ":")))
        print("vmods=" + json.dumps(expansion["vmods"], separators=(",", ":")))
        print("vmod_shards=" + json.dumps(shard_vmods(expansion["vmods"]), separators=(",", ":")))
    else:
        print(json.dumps(expansion["rows"], indent=2))
    return 0


def cmd_resolve(args) -> int:
    catalog = load_catalog(args.root)
    engine = find_engine(catalog, args.engine)
    vmod = find_vmod(catalog, args.vmod)
    resolved = resolve_source(vmod, engine)
    out = {"vmod": vmod["id"], "engine": engine["id"], **resolved}
    print(json.dumps(out, indent=2))
    return 0


def cmd_env(args) -> int:
    catalog = load_catalog(args.root)
    for name, value in env_pairs(catalog, args.engine, args.vmod, args.target):
        print(f"{name}={sh_quote(value)}")
    return 0


def cmd_merge(args) -> int:
    results_dir = Path(args.results_dir)
    if not results_dir.is_dir():
        raise CatalogError(f"{results_dir}: results directory not found")
    files = sorted(results_dir.rglob("*.json"))
    cells = [load_cell(path) for path in files]
    state_path = Path(args.state_file)
    state = load_state(state_path)
    applied = merge_cells(state, cells)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"merged {applied} of {len(cells)} cell(s) into {state_path}; state holds {len(state['cells'])} cell(s)")
    return 0


def cmd_render(args) -> int:
    state = load_state(Path(args.state_file))
    try:
        catalog = load_catalog(args.root)
    except CatalogError as exc:
        print(f"warning: rendering without a catalog ({exc.args[0].splitlines()[0]})", file=sys.stderr)
        catalog = None
    grids = [build_grid(state, target, catalog) for target in matrix_targets(state, catalog)]
    generated_at = args.generated_at or now_iso()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(grids, generated_at), encoding="utf-8")
    shapes = ", ".join(f"{grid['target']}: {len(grid['rows'])} row(s) x {len(grid['columns'])} column(s)" for grid in grids)
    print(f"rendered {out_path}: {len(grids)} target matrix/matrices ({shapes})")
    return 0


def cmd_schema(args) -> int:
    """Write (or verify) the editor JSON Schemas. DESIGN.md decision 11."""
    import jsonschema_gen

    outdir = Path(args.out) if args.out else Path(args.root) / "schemas"
    if args.check:
        problems = jsonschema_gen.check(outdir)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
        print(f"ok: {outdir} matches the generator")
        return 0
    written = jsonschema_gen.write(outdir)
    print("wrote " + ", ".join(str(p) for p in written))
    return 0


def cmd_selftest(args) -> int:
    import selftest

    return selftest.main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matrix.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_root(p):
        p.add_argument("--root", default=default_root(), help="repo root holding engines.yml and vmods/")

    p = sub.add_parser("validate", help="check the catalog; exit 1 on any error")
    add_root(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("expand", help="emit the job matrix for a lane")
    p.add_argument("--lane", required=True, choices=LANES)
    p.add_argument("--mode", default="all", choices=("compat", "package", "all"))
    p.add_argument("--format", default="github", choices=("github", "json"))
    add_root(p)
    p.set_defaults(func=cmd_expand)

    p = sub.add_parser("resolve", help="print the resolved ref+version for one (vmod, engine) pair")
    p.add_argument("--vmod", required=True)
    p.add_argument("--engine", required=True)
    add_root(p)
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("env", help="print sh-sourceable pins for the build scripts")
    p.add_argument("--engine", required=True)
    p.add_argument("--vmod")
    p.add_argument("--target")
    add_root(p)
    p.set_defaults(func=cmd_env)

    p = sub.add_parser("merge", help="fold cell result JSONs into the state file")
    p.add_argument("--results-dir", required=True)
    p.add_argument("--state-file", required=True)
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("render", help="render the state file as a self-contained HTML matrix page")
    p.add_argument("--state-file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--generated-at", help="timestamp for the page header; defaults to now (tests pass this)")
    add_root(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("schema", help="write (or --check) the editor JSON Schemas")
    p.add_argument("--out", default=None, help="output directory (default: <root>/schemas)")
    p.add_argument("--check", action="store_true", help="verify the checked-in files match; exit 1 on drift")
    add_root(p)
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("selftest", help="run tools/selftest.py")
    p.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
