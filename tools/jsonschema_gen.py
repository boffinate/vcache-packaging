#!/usr/bin/env python3
"""Generate the editor JSON Schemas for engines.yml and vmods/<id>.yml.

These schemas exist for one reason: so that `yaml-language-server` (Zed,
VS Code/Cursor via redhat.vscode-yaml, Neovim's yamlls, JetBrains, Helix) can
underline a bad catalog file *while it is being typed*. That is the one thing
a strict parser plus a CLI validator cannot do, because both only run after
the file is saved.

They are NOT a second validator, and DESIGN.md decision 11 is the contract:

* `tools/matrix.py validate` remains the authority. Nothing in CI, the build
  scripts, or the packaging layer reads these files.
* They are generated outputs, like the packaging recipes. Fix this generator
  or `matrix.py`'s KEYS table; never hand-edit the JSON. `matrix.py schema
  --check` fails if the checked-in files drift from what this emits.
* They are deliberately weaker than `validate`. The language server parses
  real YAML, not our subset, so it accepts anchors, flow mappings, and tabs
  that `yaml_subset.py` rejects; and JSON Schema cannot express the cross-file
  rules (`id` matching the filename stem, `by_series` naming a declared engine
  series, duplicate ids). Green in the editor means "probably fine".
* They are structural only. No catalog data is baked in, so they change when
  the schema changes, not when a pin moves.

Every value is typed `string` with `additionalProperties: false`. That mirrors
the parser's no-coercion rule and is what makes the editor flag the house-style
slips quoting exists to prevent: `packages: true` and `version: 1.7` parse as a
bool and a float in the language server's YAML 1.2 reader, so both light up as
"Incorrect type. Expected string."

Standard library only. Draft-07, which is the dialect yaml-language-server
supports most completely.

Usage: python3 tools/matrix.py schema [--out DIR] [--check]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matrix  # noqa: E402

__all__ = ["build_all", "render", "write", "check", "covered_kinds", "SCHEMA_FILES"]

# Every matrix.KEYS entry _object() was asked for during the last build_all().
# The selftest asserts this covers the whole table, so a mapping added to the
# catalog schema cannot be validated by the CLI but missing from the editor's.
_USED: set = set()

DRAFT = "http://json-schema.org/draft-07/schema#"

SCHEMA_FILES = {
    "engines.schema.json": "engines",
    "vmod.schema.json": "vmod",
}

# The line every catalog file carries so the schema is found with no per-editor
# configuration at all. Path is relative to the YAML file's own directory.
MODELINE = "# yaml-language-server: $schema={path}"


def _string(description: str, **extra) -> dict:
    """A plain string property. Everything in the catalog is a string."""
    out = {"type": "string", "minLength": 1, "description": description}
    out.update(extra)
    return out


def _string_list(description: str, items: dict = None) -> dict:
    return {
        "type": "array",
        "minItems": 1,
        "items": items or {"type": "string", "minLength": 1},
        "description": description,
    }


def _object(kind: str, properties: dict, description: str) -> dict:
    """An object whose required/allowed keys come from matrix.KEYS[kind].

    Reading the key sets from the same table the validator uses is the whole
    anti-drift mechanism: a key added to one is added to both, and any key
    named here but absent from the table (or vice versa) is a hard error at
    generation time rather than a silently permissive schema.
    """
    required, optional = matrix.KEYS[kind]
    _USED.add(kind)
    declared = set(properties)
    expected = required | optional
    if declared != expected:
        missing = sorted(expected - declared)
        extra = sorted(declared - expected)
        raise ValueError(
            f"jsonschema_gen: properties for {kind!r} disagree with matrix.KEYS: "
            f"missing {missing}, unexpected {extra}"
        )
    return {
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "required": sorted(required),
        "properties": properties,
    }


def _union_object(kinds: list, properties: dict, description: str) -> dict:
    """An object that is one of several KEYS variants, chosen by a sibling key.

    The engine `source` mapping is release-shaped or trunk-shaped depending on
    `kind`, so no key is unconditionally required and the allowed set is the
    union of both variants; an allOf if/then pins the right variant. The union
    still comes from the KEYS table, so adding a key to either variant widens
    this object automatically.
    """
    allowed = set()
    for kind in kinds:
        required, optional = matrix.KEYS[kind]
        _USED.add(kind)
        allowed |= required | optional
    declared = set(properties)
    if declared != allowed:
        raise ValueError(
            f"jsonschema_gen: properties for {kinds} disagree with matrix.KEYS: "
            f"missing {sorted(allowed - declared)}, unexpected {sorted(declared - allowed)}"
        )
    return {
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "properties": properties,
    }


def _source_entry(description: str) -> dict:
    return _object(
        "vmod_source_entry",
        {
            "ref": _string("Git ref to build: a tag, branch, or commit. This is the pin."),
            "commit": _string(
                "Expected full Git commit. Required by the validator for promoted package sources; "
                "the readable ref is checked out and must resolve to this immutable identity.",
                pattern=matrix.COMMIT_RE.pattern,
            ),
            "version": _string(
                "Upstream version for package naming, quoted so it stays a string "
                '(e.g. "1.7", not 1.7).'
            ),
        },
        description,
    )


def build_engines() -> dict:
    """Schema for engines.yml."""
    target = _object(
        "target",
        {
            "image": _string("Container image used to build this target."),
            "format": _string("Package format produced by this target.", enum=list(matrix.TARGET_FORMATS)),
            "runner": _string("Blacksmith runner label for serial or I/O-bound work on this target."),
            "build_runner": _string("Blacksmith runner label for CPU-parallel builds on this target."),
            "platform": _string("Native Docker platform required by this target.", enum=list(matrix.TARGET_PLATFORMS)),
            "package_arch": _string("Architecture reported by the finished package."),
        },
        "One target's complete build and package architecture contract.",
    )
    source = _union_object(
        ["engine_source_release", "engine_source_trunk"],
        {
            "tarball_url": _string("Release tarball URL (kind: release).", format="uri"),
            "sha256": _string("SHA-256 of the tarball, quoted (kind: release)."),
            "git_url": _string("Clone URL (kind: trunk).", format="uri"),
            "branch": _string("Branch built as it stands; no pin (kind: trunk)."),
        },
        "Where the engine source comes from. A release engine takes "
        "tarball_url + sha256; a trunk engine takes git_url + branch.",
    )
    rust_toolchain = _object(
        "rust_toolchain",
        {
            "version": _string("Exact Rust toolchain version, e.g. 1.90.0.", pattern=matrix.RUST_VERSION_RE.pattern),
            "bootstrap": _string("How the in-container Rust toolchain is installed.", enum=list(matrix.RUST_BOOTSTRAPS)),
        },
        "The global Rust toolchain contract used by Cargo VMODs.",
    )
    toolchains = _object(
        "toolchains",
        {"rust": rust_toolchain},
        "Global language toolchains. Required only when a VMOD uses that language.",
    )
    engine = _object(
        "engine",
        {
            "id": _string(
                "Engine id, '<family>-<version>'. The version half is derived from "
                "it, so 'vinyl-9.0.1' yields version 9.0.1."
            ),
            "family": _string("Upstream project.", enum=list(matrix.FAMILIES)),
            "series": _string(
                "Series this engine belongs to, matched by a VMOD's "
                "sources.by_series keys (e.g. 'vinyl-9.0')."
            ),
            "kind": _string(
                "'release' pins a tarball; 'trunk' builds a branch HEAD.",
                enum=list(matrix.KINDS),
            ),
            "source": source,
            "targets": _string_list("Target ids from the top-level targets registry."),
            "packages": _string(
                'Quoted "true" if this engine is packaged, not merely tested. '
                "Requires kind: release. Defaults to \"false\".",
                enum=["true", "false"],
            ),
            "package_revision": _string(
                'Quoted positive packaging revision for a packages "true" engine.',
                pattern=matrix.PACKAGE_REVISION_RE.pattern,
            ),
        },
        "One engine version we test and/or package against.",
    )
    # Cross-field rules JSON Schema *can* express, so the editor catches them
    # too. The validator enforces these independently; these are a mirror.
    engine["allOf"] = [
        {
            "if": {"properties": {"kind": {"const": "release"}}, "required": ["kind"]},
            "then": {
                "properties": {
                    "source": {
                        "required": sorted(matrix.KEYS["engine_source_release"][0]),
                        "not": {"anyOf": [{"required": ["git_url"]}, {"required": ["branch"]}]},
                    }
                }
            },
        },
        {
            "if": {"properties": {"kind": {"const": "trunk"}}, "required": ["kind"]},
            "then": {
                "properties": {
                    "source": {
                        "required": sorted(matrix.KEYS["engine_source_trunk"][0]),
                        "not": {"anyOf": [{"required": ["tarball_url"]}, {"required": ["sha256"]}]},
                    }
                }
            },
        },
        {
            "if": {"properties": {"packages": {"const": "true"}}, "required": ["packages"]},
            "then": {
                "properties": {"kind": {"const": "release"}},
                "required": ["package_revision"],
                "description": 'packages "true" requires kind: release and package_revision.',
            },
            "else": {"not": {"required": ["package_revision"]}},
        },
    ]
    return _document(
        "engines_doc",
        {
            "schema": {
                "const": matrix.ENGINES_SCHEMA,
                "description": f"Schema marker; must be {matrix.ENGINES_SCHEMA!r}.",
            },
            "targets": {
                "type": "object",
                "minProperties": 1,
                "propertyNames": {"pattern": matrix.MAPPING_KEY_RE.pattern},
                "patternProperties": {matrix.MAPPING_KEY_RE.pattern: target},
                "additionalProperties": False,
                "description": "Target registry: image, package format, standard and CPU-parallel Blacksmith runners, and architecture.",
            },
            "toolchains": toolchains,
            "engines": {
                "type": "array",
                "minItems": 1,
                "items": engine,
                "description": "Every engine version, in matrix column order.",
            },
        },
        title="vcache engines.yml",
        description=(
            "Hand-maintained engine catalog. Authority is 'python3 tools/matrix.py "
            "validate'; this schema is editor assistance only (DESIGN.md decision 11)."
        ),
    )


def build_vmod() -> dict:
    """Schema for vmods/<id>.yml."""
    sources = _object(
        "vmod_sources",
        {
            "head": _string("Branch used for the trunk lane's moving target."),
            "default": _source_entry("The ref/version used for any engine without a by_series override."),
            "by_series": {
                "type": "object",
                "minProperties": 1,
                "description": (
                    "Per-engine-series overrides, keyed by an engine's 'series' "
                    "value. The validator additionally checks each key names a "
                    "series some engine declares; this schema only checks shape."
                ),
                "additionalProperties": False,
                "patternProperties": {
                    matrix.MAPPING_KEY_RE.pattern: _source_entry("Override for this engine series."),
                },
            },
        },
        "How this VMOD's source ref is chosen per engine.",
    )
    package = _object(
        "vmod_package",
        {
            "summary": _string("One-line package summary (Debian Description / RPM Summary)."),
            "description": _string_list(
                "Long description as a list of plain lines, one per line of prose. "
                "No '|' block scalars: the parser rejects them."
            ),
            "license": _string("SPDX identifier, e.g. GPL-3.0-or-later, BSD-2-Clause."),
            "build_target": _string(
                "Make target or target arguments used to build the installable VMOD. "
                "Defaults to 'all'; use this when upstream's default also builds "
                "an auxiliary program requiring an unavailable compiled engine tree."
            ),
            "build_deps": _object(
                "vmod_build_deps",
                {
                    "debian": _string_list("Extra Build-Depends beyond the common set."),
                    "rpm": _string_list("Extra BuildRequires beyond the common set."),
                },
                "Extra build dependencies, per packaging ecosystem.",
            ),
            "modules": _string_list(
                "VCL import names this VMOD ships. Defaults to [<id>]; required "
                "when the id is not a legal module name (e.g. varnish-modules).",
                items={
                    "type": "string",
                    "pattern": matrix.MODULE_NAME_RE.pattern,
                    "description": "A VCL import name: lowercase, digits and underscores.",
                },
            ),
            "artifacts": _string_list(
                "Cargo release shared libraries, in the same order as modules. Required for build: cargo.",
                items={
                    "type": "string",
                    "pattern": matrix.ARTIFACT_BASENAME_RE.pattern,
                    "description": "A distinct basename-only release shared library ending in .so.",
                },
            ),
            "cargo_features": _string_list(
                "Cargo features required to build the VMOD. Legal only for build: cargo.",
                items={
                    "type": "string",
                    "pattern": matrix.CARGO_FEATURE_RE.pattern,
                    "description": "A Cargo feature enabled for every build and test invocation.",
                },
            ),
            "families": _string_list(
                "Engine families this VMOD's build system supports. Gates "
                "package-mode expansion only; absent means no restriction, and "
                "compat cells are unaffected (DESIGN.md decision 13).",
                items={
                    "type": "string",
                    "enum": list(matrix.FAMILIES),
                    "description": "An engine family, as engines.yml 'family' spells it.",
                },
            ),
            "promoted": _string(
                'Promotion gate (decision 15): package cells expand only when '
                'this is "true", so a new entry cannot block the release gate. '
                "Absent means not promoted; compat cells are unaffected.",
                enum=["true", "false"],
            ),
            "targets": _string_list(
                "Targets packaging can work on at all (e.g. an x86_64-only "
                "VMOD lists only x86_64 targets). Gates package-mode expansion "
                "only; absent means every target (decision 15).",
                items={
                    "type": "string",
                    "description": "A target id from engines.yml targets.",
                },
            ),
        },
        "What the generated .deb/.rpm says about itself.",
    )
    return _document(
        "vmod_doc",
        {
            "schema": {
                "const": matrix.VMOD_SCHEMA,
                "description": f"Schema marker; must be {matrix.VMOD_SCHEMA!r}.",
            },
            "id": _string(
                "VMOD id. Must equal the filename stem — the validator checks "
                "that; this schema cannot."
            ),
            "build": _string("Source build system. Defaults to autotools.", enum=list(matrix.BUILD_FAMILIES)),
            "upstream": _object(
                "vmod_upstream",
                {
                    "git": _string("Canonical clone URL.", format="uri"),
                    "homepage": _string("Project page, used in the package metadata.", format="uri"),
                },
                "Where this VMOD lives upstream.",
            ),
            "sources": sources,
            "package": package,
            "tests": _string(
                "Opt in to the selected build system's upstream suite in compat mode.",
                enum=list(matrix.TESTS_VALUES),
            ),
            "engine_source": _string(
                "Declare that configure needs the engine source tree "
                "(VINYLSRC); the build scripts provision it (decision 14).",
                enum=list(matrix.ENGINE_SOURCE_VALUES),
            ),
            "source_api_family": _string(
                "API family named by this Autotools source tree. When it differs from the selected engine, "
                "the build harness normalizes the explicit family spellings before bootstrap.",
                enum=list(matrix.SOURCE_API_FAMILIES),
            ),
        },
        title="vcache vmods/<id>.yml",
        description=(
            "One selected VMOD. Authority is 'python3 tools/matrix.py validate'; "
            "this schema is editor assistance only (DESIGN.md decision 11)."
        ),
    )


def _document(kind: str, properties: dict, title: str, description: str) -> dict:
    doc = _object(kind, properties, description)
    return {
        "$schema": DRAFT,
        "title": title,
        **doc,
    }


def build_all() -> dict:
    """Every schema file, as {filename: document}."""
    _USED.clear()
    builders = {"engines": build_engines, "vmod": build_vmod}
    return {name: builders[kind]() for name, kind in sorted(SCHEMA_FILES.items())}


def covered_kinds() -> set:
    """The matrix.KEYS entries the generated schemas actually describe."""
    build_all()
    return set(_USED)


def render(document: dict) -> str:
    """The exact bytes we write, so --check can compare strings."""
    return json.dumps(document, indent=2, sort_keys=False) + "\n"


def write(outdir: Path) -> list:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, document in build_all().items():
        path = outdir / name
        path.write_text(render(document), encoding="utf-8")
        written.append(path)
    return written


def check(outdir: Path) -> list:
    """Return a list of human-readable drift complaints; empty means clean."""
    problems = []
    for name, document in build_all().items():
        path = outdir / name
        want = render(document)
        if not path.is_file():
            problems.append(f"{path}: missing; run 'python3 tools/matrix.py schema'")
        elif path.read_text(encoding="utf-8") != want:
            problems.append(
                f"{path}: does not match the generator "
                "(these files are outputs — fix tools/jsonschema_gen.py or "
                "matrix.py KEYS, then run 'python3 tools/matrix.py schema')"
            )
    return problems
