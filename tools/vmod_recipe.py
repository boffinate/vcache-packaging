#!/usr/bin/env python3
"""Render native Debian and RPM recipes for a selected VMOD, deterministically.

This is Phase 1 of docs/20260728_0908_plan_vmod-packager-patterns-and-recipe-
generation.md: the normalized package model and the renderer that turns it into
a native source recipe. It exists because most third-party VMODs ship no
``debian/`` or ``rpm/`` directory, and absorbing packaging is the downstream
provider's job -- ours -- rather than something to ask an upstream for or to
fork a repository to add.

Four inputs, all trusted and local:

  * the VMOD catalog manifest (``vmod-ci/v1``), which owns source identity:
    ref, peeled commit, version, archive digest, and the lanes;
  * the packaging adapter (``vmod-adapter/v1``), which owns what is true of
    every VMOD built the same way;
  * the per-VMOD overlay (``vmod-recipe-overlay/v1``), which owns what is true
    of one VMOD: names, licence, description, dependencies, payload;
  * the cohort and target manifests, which own the engine row -- VRT, strict
    ABI, cohort id, VMOD directory, and the exact engine package versions.

The ABI dependency expressions are NOT written here. They come from
``metadata.abi_expressions``, the same function that generates cachetag's, so
there is one implementation of that policy and a generated recipe cannot weaken
it by drifting.

What this tool must never do, from the plan's generator contract: compile,
install, inspect a built package, read a clock, or emit a recipe with an input
missing or a token unresolved. It renders text and writes a generation record.
Everything that builds runs in the target buildroot.

Standard library only, like the rest of tools/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_matrix  # noqa: E402
import manifest as manifest_mod  # noqa: E402
import metadata as metadata_mod  # noqa: E402
import yaml_subset  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

OVERLAY_SCHEMA = "vmod-recipe-overlay/v1"
ADAPTER_SCHEMA = "vmod-adapter/v1"
MODEL_SCHEMA = "vmod-recipe-model/v1"
RECORD_SCHEMA = "vmod-recipe-generation/v1"

RECIPE_ROOT = "recipes/vmods"

# The complete token vocabulary. A template using a token that is not here is a
# typo; a token here that no renderer supplies is a bug. Both fail closed, which
# is the same two-sided discipline libvmod-cachetag/packaging/check-tokens.sh
# applies to the hand-written recipes.
TOKEN_RE = re.compile(r"@([A-Z][A-Z0-9_]*)@")

ARCHIVE_METHODS = ["upstream-release", "derived-git-tag"]
BOOTSTRAPS = ["none", "autoreconf"]
ABI_MODES = ["strict", "vrt"]
YES_NO = ["yes", "no"]


class GeneratorError(Exception):
    """A refusal: an input is missing, contradictory, or unresolved."""


# ---------------------------------------------------------------------------
# Schemas
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


FREE_TEXT_RE = r"^[^\x00-\x1f]*$"
ID_RE = r"^[a-z][a-z0-9-]*$"
PKG_NAME_RE = r"^[a-z0-9][a-z0-9+.-]+$"
DEP_RE = r"^[A-Za-z0-9][A-Za-z0-9 ()=<>~+.:_-]*$"
PATH_RE = r"^[A-Za-z0-9][A-Za-z0-9._/+-]*$"
URL_RE = r"^https://[^\s]+$"
COMMIT_RE = r"^[0-9a-f]{40}$"
DIGITS_RE = r"^[0-9]+$"
REVISION_RE = r"^[1-9][0-9]*$"
SPDX_RE = r"^[A-Za-z0-9 ()+.-]+$"
SO_RE = r"^lib[a-z0-9_]+\.so$"

ADAPTER_SPEC = _map(
    {
        "schema": _enum([ADAPTER_SCHEMA]),
        "adapter": _s(ID_RE),
        "revision": _s(REVISION_RE),
        "defaults": _map(
            {
                "bootstrap": _enum(BOOTSTRAPS),
                "configure_args": _list(_s(FREE_TEXT_RE)),
                "build_time_tests": _s(FREE_TEXT_RE),
                "parallel_build": _enum(YES_NO),
            }
        ),
        "build_dependencies": _map(
            {"debian": _list(_s(DEP_RE)), "rpm": _list(_s(DEP_RE))}
        ),
        "bootstrap_dependencies": _map(
            {"debian": _list(_s(DEP_RE)), "rpm": _list(_s(DEP_RE))}
        ),
    }
)

OVERLAY_SPEC = _map(
    {
        "schema": _enum([OVERLAY_SCHEMA]),
        "id": _s(ID_RE),
        "adapter": _s(ID_RE),
        "revision": _s(REVISION_RE),
        "upstream": _map(
            {
                "name": _s(PKG_NAME_RE),
                "contact": _s(FREE_TEXT_RE),
                "homepage": _s(URL_RE),
                "vcs_git": _s(FREE_TEXT_RE),
                "vcs_browser": _s(FREE_TEXT_RE),
            }
        ),
        "source": _map(
            {
                "clone_url": _s(URL_RE),
                "archive": _map(
                    {
                        "method": _enum(ARCHIVE_METHODS),
                        "url": _s(URL_RE),
                        "bytes": _s(DIGITS_RE),
                        "stem": _s(PKG_NAME_RE),
                        "source_date_epoch": _s(DIGITS_RE),
                    }
                ),
                "submodules": _list(
                    _map({"path": _s(PATH_RE), "commit": _s(COMMIT_RE), "url": _s(URL_RE)})
                ),
            }
        ),
        "package": _map(
            {
                "debian_source_name": _s(PKG_NAME_RE),
                "debian_binary_name": _s(PKG_NAME_RE),
                "rpm_name": _s(PKG_NAME_RE),
                "debian_section": _s(r"^[a-z][a-z/-]*$"),
                "revision": _s(REVISION_RE),
                "summary": _s(FREE_TEXT_RE),
                "description": _list(_s(FREE_TEXT_RE), min_len=1),
            }
        ),
        "license": _map(
            {
                "expression": _s(SPDX_RE),
                "debian_short_name": _s(r"^[A-Za-z0-9.+-]+$"),
                "files": _list(_s(PATH_RE), min_len=1),
            }
        ),
        "copyright": _map(
            {
                "files": _list(
                    _map(
                        {
                            "pattern": _s(r"^[A-Za-z0-9*._/-]+$"),
                            "holder": _s(FREE_TEXT_RE),
                            "license": _s(r"^[A-Za-z0-9.+-]+$"),
                        }
                    ),
                    min_len=1,
                ),
                "packaging": _s(FREE_TEXT_RE),
            }
        ),
        "abi": _map({"mode": _enum(ABI_MODES)}),
        "build": _map(
            {
                "bootstrap": _enum(BOOTSTRAPS, optional=True),
                "parallel_build": _enum(YES_NO, optional=True),
                "build_time_tests": _s(FREE_TEXT_RE, optional=True),
                "configure_args": _list(_s(FREE_TEXT_RE), optional=True),
            }
        ),
        "build_dependencies": _map(
            {"debian": _list(_s(DEP_RE)), "rpm": _list(_s(DEP_RE))}
        ),
        "payload": _map(
            {
                "vmod_object": _s(SO_RE),
                "man_pages": _list(_s(r"^man[0-9]/[A-Za-z0-9._-]+$"), min_len=1),
                "doc_files": _list(_s(PATH_RE)),
            }
        ),
        "lintian_overrides": _map({"source": _list(_s(FREE_TEXT_RE)), "binary": _list(_s(FREE_TEXT_RE))}),
    }
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load(path, spec, schema_name: str) -> dict:
    path = Path(path)
    try:
        data = yaml_subset.parse_file(path)
    except OSError as exc:
        raise GeneratorError(f"{path}: cannot be read ({exc})") from None
    errors = manifest_mod.schema_errors(spec, data, str(path))
    if errors:
        raise GeneratorError(
            f"{schema_name} validation failed:\n  " + "\n  ".join(errors)
        )
    return data


def load_adapter(path) -> dict:
    return _load(path, ADAPTER_SPEC, ADAPTER_SCHEMA)


def load_overlay(path) -> dict:
    return _load(path, OVERLAY_SPEC, OVERLAY_SCHEMA)


def load_vmod_manifest(path, discovery_id: str = None) -> dict:
    """Load and validate a vmod-ci/v1 manifest through ci_matrix's own schema.

    Deliberately not a second implementation: the catalog schema has one owner,
    and a generator that accepted a manifest the catalog would reject would be
    generating recipes for a VMOD that cannot enter CI.
    """
    path = Path(path)
    try:
        data = ci_matrix.load_vmod_manifest(path)
    except OSError as exc:
        raise GeneratorError(f"{path}: cannot be read ({exc})") from None
    errors = ci_matrix.validate_vmod_manifest(data, str(path), discovery_id)
    if errors:
        raise GeneratorError("VMOD manifest validation failed:\n  " + "\n  ".join(errors))
    return data


# ---------------------------------------------------------------------------
# The normalized model
# ---------------------------------------------------------------------------

_REQUIRED_SOURCE_FIELDS = ("ref", "expected_commit", "version", "archive_sha256")


def build_model(
    *,
    vmod_manifest: dict,
    overlay: dict,
    adapter: dict,
    cohort: dict,
    target: dict,
    maintainer: str,
    channel: str = "release",
    debian_distribution: str = None,
    require_maintainer: bool = True,
) -> dict:
    """The one normalized VMOD build description both backends render from.

    Native policy stays with the backend: this returns shared facts, not
    hand-written Debian or RPM dependency strings.

    ``require_maintainer`` is False only for the inspection subcommands, which
    print names and the model and render nothing. Refusing an absent maintainer
    is a refusal to emit a recipe, not a refusal to answer what a package would
    be called; the rendering path always requires one.
    """
    vmod_id = vmod_manifest["id"]
    if overlay["id"] != vmod_id:
        raise GeneratorError(
            f"overlay id {overlay['id']!r} does not match manifest id {vmod_id!r}"
        )
    if overlay["adapter"] != vmod_manifest["adapter"]:
        raise GeneratorError(
            f"overlay adapter {overlay['adapter']!r} does not match manifest adapter "
            f"{vmod_manifest['adapter']!r}"
        )
    if adapter["adapter"] != overlay["adapter"]:
        raise GeneratorError(
            f"adapter file declares {adapter['adapter']!r}, overlay names {overlay['adapter']!r}"
        )

    # 1. Source identity. Every field is required: a publishable package must be
    #    traceable to a ref, a peeled commit, a version, and archive bytes.
    source = vmod_manifest["sources"].get(channel)
    if source is None:
        raise GeneratorError(f"sources.{channel}: not declared in the manifest")
    missing = [f for f in _REQUIRED_SOURCE_FIELDS if not source.get(f)]
    if missing:
        raise GeneratorError(
            f"sources.{channel}: missing source identity {missing}; a generated recipe "
            "must name the exact bytes it was built from"
        )
    if source["publishable"] != "true":
        raise GeneratorError(
            f"sources.{channel}: publishable is {source['publishable']!r}; a native recipe "
            "is only generated for a channel that may become a package"
        )

    maintainer_name, maintainer_email = _split_maintainer(maintainer, require_maintainer)

    # 2. The engine row. The cohort and target manifests are the authority; the
    #    ABI expressions come from metadata.py so this tool holds no copy of
    #    that policy.
    tgt = target["target"]
    if target["lane"] != "cohort":
        raise GeneratorError(
            f"target {tgt['id']}: lane {target['lane']!r}; the generated-recipe path is "
            "defined for cohort-lane targets only"
        )
    if target["cohort"] != cohort["cohort"]:
        raise GeneratorError(
            f"target {tgt['id']} belongs to cohort {target['cohort']!r}, not "
            f"{cohort['cohort']!r}"
        )
    # The ABI dependency input, spelled out rather than left to a KeyError. A
    # missing strict ABI or VRT must fail as a refusal with a reason, because
    # "generate a recipe whose ABI dependency cannot be formed" is exactly the
    # case the plan's verification list requires to fail before any build.
    vinyl = cohort.get("vinyl") or {}
    for field in ("vrt", "strict_abi", "version"):
        if not vinyl.get(field):
            raise GeneratorError(
                f"cohort {cohort.get('cohort')!r}: vinyl.{field} is missing; the ABI "
                "dependency expression cannot be generated without it"
            )
    engine_packages = target.get("vinyl_packages") or {}
    for field in ("runtime_name", "runtime_version", "dev_name", "dev_version"):
        if not engine_packages.get(field):
            raise GeneratorError(
                f"target {tgt['id']}: vinyl_packages.{field} is missing; the generated "
                "recipe must pin the exact engine package it was built against"
            )
    abi = metadata_mod.abi_expressions(
        vrt=vinyl["vrt"], strict_abi=vinyl["strict_abi"], cohort_id=cohort["cohort"]
    )
    vmoddir = target["install"]["vmoddir"]
    if not vmoddir.startswith("/"):
        raise GeneratorError(f"target {tgt['id']}: install.vmoddir is not an absolute path")

    fmt = tgt["package_format"]
    if fmt not in ("deb", "rpm"):
        raise GeneratorError(
            f"target {tgt['id']}: package format {fmt!r} has no generated recipe backend"
        )
    if fmt == "deb" and not debian_distribution:
        raise GeneratorError(
            "a deb target needs --debian-distribution (the suite the changelog names); "
            "the lane pin file's DEBIAN_DISTRIBUTION is the value to pass"
        )

    # 3. Build flow: adapter defaults, narrowed by the overlay where the overlay
    #    declares an override. Dependencies are additive only -- an overlay
    #    removing a shared dependency would be a claim about the lifecycle, not
    #    about one VMOD.
    defaults = adapter["defaults"]
    ov_build = overlay["build"]
    bootstrap = ov_build.get("bootstrap", defaults["bootstrap"])
    configure_args = list(defaults["configure_args"]) + list(
        ov_build.get("configure_args") or []
    )
    build_time_tests = ov_build.get("build_time_tests", defaults["build_time_tests"])
    parallel_build = ov_build.get("parallel_build", defaults["parallel_build"])

    family = "debian" if fmt == "deb" else "rpm"
    deps = list(adapter["build_dependencies"][family])
    if bootstrap != "none":
        deps += list(adapter["bootstrap_dependencies"][family])
    deps += list(overlay["build_dependencies"][family])
    # The engine development package at the exact cohort version. Not a policy
    # this file owns: the cohort model requires it and the buildroot must not be
    # able to satisfy the build with a different Vinyl revision.
    if fmt == "deb":
        deps.append(f"{engine_packages['dev_name']} (= {engine_packages['dev_version']})")
    else:
        deps.append(f"{engine_packages['dev_name']} = {engine_packages['dev_version']}")
    deps = _dedupe(deps)

    # 4. Names and expected artifacts, without assuming any build succeeded.
    version = source["version"]
    revision = int(overlay["package"]["revision"])
    versions = metadata_mod.package_versions(version, revision, tgt["dist_tag"])
    stem = overlay["source"]["archive"]["stem"]
    archive_name = f"{stem}-{version}.tar.gz"
    names = _expected_names(overlay, tgt, versions, version, revision, archive_name)

    payload = overlay["payload"]
    licence = overlay["license"]
    package = overlay["package"]

    if not package["summary"].strip():
        raise GeneratorError("package.summary is empty; a package description is required")
    if not any(line.strip() for line in package["description"]):
        raise GeneratorError("package.description is empty; a package description is required")

    return {
        "schema": MODEL_SCHEMA,
        "vmod": {
            "id": vmod_id,
            "adapter": adapter["adapter"],
            "adapter_revision": adapter["revision"],
            "overlay_revision": overlay["revision"],
        },
        "maintainer": {"name": maintainer_name, "email": maintainer_email},
        "upstream": dict(overlay["upstream"]),
        "source": {
            "channel": channel,
            "ref": source["ref"],
            "commit": source["expected_commit"],
            "version": version,
            "archive_sha256": source["archive_sha256"],
            "archive_name": archive_name,
            "archive_url": overlay["source"]["archive"]["url"],
            "archive_method": overlay["source"]["archive"]["method"],
            "archive_bytes": overlay["source"]["archive"]["bytes"],
            "directory": f"{stem}-{version}",
            "source_date_epoch": overlay["source"]["archive"]["source_date_epoch"],
            "clone_url": overlay["source"]["clone_url"],
            "submodules": [dict(s) for s in overlay["source"]["submodules"]],
        },
        "package": {
            "debian_source_name": package["debian_source_name"],
            "debian_binary_name": package["debian_binary_name"],
            "rpm_name": package["rpm_name"],
            "debian_section": package["debian_section"],
            "revision": revision,
            "summary": package["summary"],
            "description": list(package["description"]),
            "versions": versions,
        },
        "license": {
            "expression": licence["expression"],
            "debian_short_name": licence["debian_short_name"],
            "files": list(licence["files"]),
        },
        "copyright": {
            "files": [dict(f) for f in overlay["copyright"]["files"]],
            "packaging": overlay["copyright"]["packaging"],
        },
        "abi": {"mode": overlay["abi"]["mode"], **abi},
        "engine": {
            "cohort": cohort["cohort"],
            "vinyl_version": vinyl["version"],
            "vrt": vinyl["vrt"],
            "strict_abi": vinyl["strict_abi"],
            "runtime_package": engine_packages["runtime_name"],
            "runtime_version": engine_packages["runtime_version"],
            "dev_package": engine_packages["dev_name"],
            "dev_version": engine_packages["dev_version"],
            "vmoddir": vmoddir,
        },
        "target": {
            "id": tgt["id"],
            "distro": tgt["distro"],
            "distro_id": tgt["distro_id"],
            "distro_release": tgt["distro_release"],
            "arch": tgt["arch"],
            "package_format": fmt,
            "dist_tag": tgt["dist_tag"],
            "debian_distribution": debian_distribution or "",
        },
        "build": {
            "bootstrap": bootstrap,
            "configure_args": configure_args,
            "build_time_tests": build_time_tests,
            "parallel_build": parallel_build,
            "dependencies": deps,
        },
        "payload": {
            "vmod_object": payload["vmod_object"],
            "man_pages": list(payload["man_pages"]),
            "doc_files": list(payload["doc_files"]),
            "license_files": list(licence["files"]),
        },
        "lintian_overrides": {
            "source": list(overlay["lintian_overrides"]["source"]),
            "binary": list(overlay["lintian_overrides"]["binary"]),
        },
        "artifacts": names,
    }


def _split_maintainer(maintainer: str, required: bool = True):
    if not maintainer and not required:
        return "", ""
    if not maintainer:
        raise GeneratorError(
            "no maintainer given. A generated recipe must not carry a placeholder "
            'maintainer; pass --maintainer "Name <email>"'
        )
    match = re.match(r"^\s*(.+?)\s*<([^<>@\s]+@[^<>@\s]+)>\s*$", maintainer)
    if match is None:
        raise GeneratorError(
            f"--maintainer {maintainer!r} is not in 'Name <email>' form"
        )
    name, email = match.group(1), match.group(2)
    if "example" in email or "localhost" in email:
        raise GeneratorError(
            f"--maintainer email {email!r} looks like a placeholder; a published package "
            "needs a real contact"
        )
    return name, email


def _dedupe(items: list) -> list:
    """Order-preserving de-duplication. Order is part of the rendered bytes."""
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _expected_names(overlay, tgt, versions, version, revision, archive_name) -> dict:
    """Expected source and binary package names, computed without a build."""
    fmt = tgt["package_format"]
    arch = tgt["arch"]
    if fmt == "deb":
        src = overlay["package"]["debian_source_name"]
        binary = overlay["package"]["debian_binary_name"]
        deb_version = versions["debian"]["version"]
        return {
            "source_package_name": src,
            "binary_package_names": [binary, f"{binary}-dbgsym"],
            "native_filenames": [f"{binary}_{deb_version}_{arch}.deb"],
            "source_package_filenames": [
                f"{src}_{version}.orig.tar.gz",
                f"{src}_{deb_version}.debian.tar.xz",
                f"{src}_{deb_version}.dsc",
            ],
            "release_asset_filenames": [
                f"{binary}-{version}-{revision}-{tgt['distro_id']}-{arch}.deb"
            ],
            "upstream_archive": archive_name,
        }
    name = overlay["package"]["rpm_name"]
    release = versions["rpm"]["release"]
    return {
        "source_package_name": name,
        "binary_package_names": [name, f"{name}-debuginfo", f"{name}-debugsource"],
        "native_filenames": [f"{name}-{version}-{release}.{arch}.rpm"],
        "source_package_filenames": [f"{name}-{version}-{release}.src.rpm"],
        "release_asset_filenames": [
            f"{name}-{version}-{revision}-{tgt['distro_id']}-{arch}.rpm"
        ],
        "upstream_archive": archive_name,
    }


# ---------------------------------------------------------------------------
# Dates: from recorded epochs, never from a clock
# ---------------------------------------------------------------------------


# Weekday and month abbreviations, spelled out rather than taken from
# strftime's %a and %b. Both are LC_TIME-sensitive: the same epoch renders as
# "Wed" under C and as "mer." under fr_FR.UTF-8, which would make the recipe
# bytes depend on the environment the generator happened to run in. Debian's
# changelog format and RPM's %changelog both require the English abbreviations,
# so there is nothing to localise even in principle.
#
# Setting LC_TIME to C at import would also work, but it mutates process-global
# state for every other module in the interpreter. A table cannot.
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def debian_date(epoch: str) -> str:
    """RFC 5322 date for debian/changelog, in UTC, from a recorded epoch."""
    t = time.gmtime(int(epoch))
    return "{}, {:02d} {} {:04d} {:02d}:{:02d}:{:02d} +0000".format(
        _WEEKDAYS[t.tm_wday],
        t.tm_mday,
        _MONTHS[t.tm_mon - 1],
        t.tm_year,
        t.tm_hour,
        t.tm_min,
        t.tm_sec,
    )


def rpm_changelog_date(epoch: str) -> str:
    """RPM %changelog date, in UTC, from a recorded epoch."""
    t = time.gmtime(int(epoch))
    return "{} {} {:02d} {:04d}".format(
        _WEEKDAYS[t.tm_wday], _MONTHS[t.tm_mon - 1], t.tm_mday, t.tm_year
    )


# ---------------------------------------------------------------------------
# Token substitution
# ---------------------------------------------------------------------------


def substitute(template: str, values: dict, where: str) -> str:
    """One pass, and every token must be known.

    One pass matters: a value that happens to contain something token-shaped is
    then data, not a second round of substitution. The output scan below still
    catches it, which is the safe direction.
    """
    missing = []

    def repl(match):
        name = match.group(1)
        if name not in values:
            missing.append(name)
            return match.group(0)
        return values[name]

    rendered = TOKEN_RE.sub(repl, template)
    if missing:
        raise GeneratorError(
            f"{where}: template uses undeclared token(s) {sorted(set(missing))}"
        )
    leftover = sorted(set(TOKEN_RE.findall(rendered)))
    if leftover:
        raise GeneratorError(
            f"{where}: unresolved template token(s) {leftover} survived substitution; "
            "refusing to emit a recipe a build would consume literally"
        )
    return rendered


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _deb_paragraph(lines: list) -> str:
    """Debian control-file continuation: one leading space, '.' for a blank."""
    out = []
    for line in lines:
        out.append(" ." if not line.strip() else " " + line.rstrip())
    return "\n".join(out)


def _deb_relations(items: list) -> str:
    return "\n".join(f" {item}," for item in items)


def _rpm_description(lines: list) -> str:
    return "\n".join(line.rstrip() for line in lines)


def _copyright_stanzas(model: dict) -> str:
    out = []
    for entry in model["copyright"]["files"]:
        out.append(
            "Files: {}\nCopyright: {}\nLicense: {}\n".format(
                entry["pattern"], entry["holder"], entry["license"]
            )
        )
    return "\n".join(out)


def _license_stanzas(model: dict, licenses_dir: Path) -> str:
    short = model["license"]["debian_short_name"]
    path = licenses_dir / f"{short}.debian"
    if not path.is_file():
        raise GeneratorError(
            f"no reviewed Debian licence stanza for {short!r} at {path}. A package may not "
            "ship an unresolved or non-machine-readable licence; write the stanza first."
        )
    return path.read_text(encoding="utf-8").rstrip("\n")


def _deb_auto_build_block(model: dict) -> str:
    """Serialise make on the Debian backend when the overlay asks for it.

    `parallel_build` had exactly one consumer until 2026-07-28: the RPM backend
    rendered it as `%make_build -j1` and the Debian backend rendered nothing at
    all, so `dh_auto_build` ran at `-j$(nproc)`. dict declares `no` because
    upstream's src/Makefile.am generates vcc_if.c, vcc_if.h and the man-page
    source from one rule and builds the manual from the last of them without
    declaring that edge; the first live CI build raced and died on a missing
    vcc_if.c.tmp2. A declared field with one asserted consumer is a field that
    can be half-ignored, which is what happened.
    """
    if model["build"]["parallel_build"] != "no":
        return ""
    return (
        "# Serialised on purpose. This VMOD has a generator rule whose\n"
        "# prerequisites are not fully declared, which is harmless at -j1 and a\n"
        "# race above it. Declared by the VMOD overlay, not guessed here.\n"
        "override_dh_auto_build:\n"
        "\tdh_auto_build -- -j1\n"
    )


def _deb_auto_test_block(model: dict) -> str:
    tests = model["build"]["build_time_tests"]
    if tests == "none":
        return (
            "# No build-time test target. This VMOD's suite drives a real daemon, which\n"
            "# proves the build tree works and says nothing about the package; behaviour\n"
            "# is verified against the installed package instead.\n"
            "override_dh_auto_test:\n"
            "\t@echo 'build-time tests: none (declared by the VMOD overlay)'\n"
        )
    return (
        "# The build-time test subset the VMOD overlay declares safe. The full suite\n"
        "# runs against the installed package, not here.\n"
        "override_dh_auto_test:\n"
        f"\tdh_auto_test -- TESTS={tests}\n"
    )


def _rpm_check_block(model: dict) -> str:
    tests = model["build"]["build_time_tests"]
    if tests == "none":
        return (
            "# No %check section. This VMOD's suite drives a real daemon; behaviour is\n"
            "# verified against the installed package instead.\n"
        )
    return f"%check\n%make_build check TESTS={tests}\n"


def _rpm_bootstrap_block(model: dict) -> str:
    if model["build"]["bootstrap"] == "none":
        return (
            "\n# The release archive already carries configure, Makefile.in, aclocal.m4 and\n"
            "# build-aux, so the build system is the one the release was tested with.\n"
        )
    return (
        "\n# The recorded source archive carries no generated build system, so regenerate\n"
        "# it. The autotools this needs are declared as BuildRequires above.\n"
        "autoreconf -fi\n"
    )


def _rpm_files(model: dict) -> str:
    lines = []
    for name in model["payload"]["license_files"]:
        lines.append(f"%license {name}")
    for name in model["payload"]["doc_files"]:
        lines.append(f"%doc {name}")
    # The macro, not the literal path: %build has already asserted that the
    # macro, the installed pkg-config value and the manifest all agree, so
    # spelling the path a second time could only ever disagree with them.
    lines.append("%{{vinyl_vmoddir}}/{}".format(model["payload"]["vmod_object"]))
    for page in model["payload"]["man_pages"]:
        lines.append("%{_mandir}/" + page + "*")
    return "\n".join(lines)


def _continued_args(args: list) -> str:
    """Configure arguments as make/spec line continuations, or nothing at all."""
    if not args:
        return ""
    return " \\\n\t\t" + " \\\n\t\t".join(args)


def token_values(model: dict, licenses_dir: Path) -> dict:
    """Every token both backends can use. Missing entries fail at substitution."""
    epoch = model["source"]["source_date_epoch"]
    return {
        "VMOD_ID": model["vmod"]["id"],
        "ADAPTER": model["vmod"]["adapter"],
        "ADAPTER_REVISION": model["vmod"]["adapter_revision"],
        "OVERLAY_REVISION": model["vmod"]["overlay_revision"],
        "MAINTAINER_NAME": model["maintainer"]["name"],
        "MAINTAINER_EMAIL": model["maintainer"]["email"],
        "UPSTREAM_NAME": model["upstream"]["name"],
        "UPSTREAM_CONTACT": model["upstream"]["contact"],
        "UPSTREAM_VERSION": model["source"]["version"],
        "HOMEPAGE": model["upstream"]["homepage"],
        "VCS_GIT": model["upstream"]["vcs_git"],
        "VCS_BROWSER": model["upstream"]["vcs_browser"],
        "SOURCE_NAME": model["package"]["debian_source_name"],
        "BINARY_NAME": model["package"]["debian_binary_name"],
        "RPM_NAME": model["package"]["rpm_name"],
        "DEBIAN_SECTION": model["package"]["debian_section"],
        "DEBIAN_STANDARDS_VERSION": "4.7.2",
        "SUMMARY": model["package"]["summary"],
        "DEB_DESCRIPTION": _deb_paragraph(model["package"]["description"]),
        "RPM_DESCRIPTION": _rpm_description(model["package"]["description"]),
        "DEB_BUILD_DEPENDS": _deb_relations(model["build"]["dependencies"]),
        "DEB_DEPENDS": _deb_relations(
            ["${shlibs:Depends}", "${misc:Depends}"]
            + [d.strip() for d in model["abi"]["deb_depends"].split(",")]
        ),
        "RPM_BUILD_REQUIRES": "\n".join(
            f"BuildRequires:  {d}" for d in model["build"]["dependencies"]
        ),
        "RPM_REQUIRES": "\n".join(f"Requires:       {r}" for r in model["abi"]["rpm_requires"]),
        "LICENSE_EXPRESSION": model["license"]["expression"],
        "LICENSE_DEBIAN_SHORT": model["license"]["debian_short_name"],
        "LICENSE_STANZAS": _license_stanzas(model, licenses_dir),
        "COPYRIGHT_FILES_STANZAS": _copyright_stanzas(model),
        "PACKAGING_COPYRIGHT": model["copyright"]["packaging"],
        "SOURCE_URL": model["source"]["archive_url"],
        "SOURCE_ARCHIVE": model["source"]["archive_name"],
        "SOURCE_SHA256": model["source"]["archive_sha256"],
        "SOURCE_REF": model["source"]["ref"],
        "SOURCE_COMMIT": model["source"]["commit"],
        "SOURCE_METHOD": model["source"]["archive_method"],
        "SOURCE_DIRECTORY": model["source"]["directory"],
        "DEBIAN_VERSION": model["package"]["versions"]["debian"]["version"],
        "DEBIAN_DISTRIBUTION": model["target"]["debian_distribution"],
        "DEBIAN_DATE": debian_date(epoch),
        "RPM_CHANGELOG_DATE": rpm_changelog_date(epoch),
        "PACKAGE_REVISION": str(model["package"]["revision"]),
        "COHORT_ID": model["engine"]["cohort"],
        "VINYL_PACKAGE_VERSION": model["engine"]["runtime_version"],
        "VINYL_VRT": model["engine"]["vrt"],
        "VINYL_STRICT_ABI": model["engine"]["strict_abi"],
        "VINYL_VMODDIR": model["engine"]["vmoddir"],
        "TARGET_ID": model["target"]["id"],
        "VMOD_OBJECT": model["payload"]["vmod_object"],
        "CONFIGURE_ARGS_CONTINUED": _continued_args(model["build"]["configure_args"]),
        "DH_ARGS": "" if model["build"]["bootstrap"] != "none" else " --without autoreconf",
        "DEB_AUTO_BUILD_BLOCK": _deb_auto_build_block(model),
        "DEB_AUTO_TEST_BLOCK": _deb_auto_test_block(model),
        "RPM_CHECK_BLOCK": _rpm_check_block(model),
        "RPM_BOOTSTRAP_BLOCK": _rpm_bootstrap_block(model),
        "RPM_MAKE_FLAGS": " -j1" if model["build"]["parallel_build"] == "no" else "",
        "RPM_FILES": _rpm_files(model),
        "DEBIAN_DOC_FILES": "\n".join(model["payload"]["doc_files"]),
        "SOURCE_LINTIAN_OVERRIDES": "\n".join(model["lintian_overrides"]["source"]),
        "BINARY_LINTIAN_OVERRIDES": "\n".join(model["lintian_overrides"]["binary"]),
    }


def render(model: dict, templates_dir, licenses_dir) -> dict:
    """Render the native recipe tree. Returns {relative path: text}, sorted."""
    templates_dir = Path(templates_dir)
    licenses_dir = Path(licenses_dir)
    values = token_values(model, licenses_dir)
    fmt = model["target"]["package_format"]

    def read(name: str) -> str:
        path = templates_dir / name
        if not path.is_file():
            raise GeneratorError(f"missing template {path}")
        return path.read_text(encoding="utf-8")

    out: dict = {}
    if fmt == "deb":
        binary = model["package"]["debian_binary_name"]
        out["debian/control"] = substitute(read("debian/control.in"), values, "debian/control")
        out["debian/changelog"] = substitute(
            read("debian/changelog.in"), values, "debian/changelog"
        )
        out["debian/copyright"] = substitute(
            read("debian/copyright.in"), values, "debian/copyright"
        )
        out["debian/rules"] = substitute(read("debian/rules.in"), values, "debian/rules")
        out["debian/source/format"] = read("debian/source/format")
        out["debian/source/lintian-overrides"] = substitute(
            read("debian/source-lintian-overrides.in"), values, "debian/source/lintian-overrides"
        )
        if model["payload"]["doc_files"]:
            out[f"debian/{binary}.docs"] = substitute(
                read("debian/docs.in"), values, f"debian/{binary}.docs"
            )
        out[f"debian/{binary}.lintian-overrides"] = substitute(
            read("debian/binary-lintian-overrides.in"),
            values,
            f"debian/{binary}.lintian-overrides",
        )
    else:
        name = model["package"]["rpm_name"]
        out[f"{name}.spec"] = substitute(read("rpm/vmod.spec.in"), values, f"{name}.spec")

    return {key: _normalise(out[key]) for key in sorted(out)}


def _normalise(text: str) -> str:
    """No trailing whitespace, no blank-line runs at the end, one final newline.

    Rendered blocks are multi-line and it is easy for one to arrive with or
    without its own trailing newline depending on the branch taken. Normalising
    here means the two cases cannot produce different bytes.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Generation record
# ---------------------------------------------------------------------------


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generation_record(
    *, model: dict, outputs: dict, inputs: dict, generator_path=None
) -> dict:
    """Every input digest, every output digest, and the expected package names.

    `inputs` maps a label to a file path. The generator's own source is digested
    too: a recipe is only reproducible against the code that rendered it.
    """
    generator_path = Path(generator_path or __file__)
    input_digests = {
        label: {"path": _repo_relative(path), "sha256": _digest_file(path)}
        for label, path in sorted(inputs.items())
    }
    input_digests["generator"] = {
        "path": _repo_relative(generator_path),
        "sha256": _digest_file(generator_path),
    }
    return {
        "schema": RECORD_SCHEMA,
        "vmod": dict(model["vmod"]),
        "target": model["target"]["id"],
        "package_format": model["target"]["package_format"],
        "cohort": model["engine"]["cohort"],
        "engine": dict(model["engine"]),
        "source": dict(model["source"]),
        "maintainer": "{} <{}>".format(
            model["maintainer"]["name"], model["maintainer"]["email"]
        ),
        "license": dict(model["license"]),
        "abi": dict(model["abi"]),
        "build": dict(model["build"]),
        "payload": dict(model["payload"]),
        "artifacts": dict(model["artifacts"]),
        "inputs": input_digests,
        "outputs": {
            name: {"sha256": _digest_text(text), "bytes": len(text.encode("utf-8"))}
            for name, text in sorted(outputs.items())
        },
        "recipe_sha256": _tree_digest(outputs),
    }


def _tree_digest(outputs: dict) -> str:
    """One digest over the whole rendered tree: sorted 'name sha256' lines."""
    blob = "".join(
        f"{name} {_digest_text(text)}\n" for name, text in sorted(outputs.items())
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _repo_relative(path) -> str:
    path = Path(path).resolve()
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def dumps_record(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Whole-generation entry point
# ---------------------------------------------------------------------------


def build(
    *,
    manifest_path,
    overlay_path,
    cohort_id: str,
    target_id: str,
    maintainer: str,
    channel: str = "release",
    debian_distribution: str = None,
    recipe_root=None,
    repo_root=None,
    require_maintainer: bool = True,
) -> tuple:
    """Load every input and build the normalized model. Renders nothing.

    Split out from :func:`generate` so the inspection subcommands can answer
    "what would this package be called" without a maintainer, which they have
    no use for and which the rendering path rightly refuses to go without.
    """
    repo_root = Path(repo_root) if repo_root else REPO_ROOT
    recipe_root = Path(recipe_root) if recipe_root else repo_root / RECIPE_ROOT

    vmod_manifest = load_vmod_manifest(manifest_path)
    overlay = load_overlay(overlay_path)
    adapter_path = recipe_root / "adapters" / overlay["adapter"] / "adapter.yml"
    if not adapter_path.is_file():
        raise GeneratorError(
            f"no adapter {overlay['adapter']!r} at {adapter_path}; a VMOD may only name a "
            "checked-in, reviewed adapter"
        )
    adapter = load_adapter(adapter_path)

    cohort_path = repo_root / "registry" / "cohorts" / f"{cohort_id}.yml"
    target_path = repo_root / "registry" / "targets" / cohort_id / f"{target_id}.yml"
    try:
        cohort = manifest_mod.load_cohort(cohort_path)
        target = manifest_mod.load_target(target_path)
    except OSError as exc:
        raise GeneratorError(f"cannot read the engine row: {exc}") from None

    model = build_model(
        vmod_manifest=vmod_manifest,
        overlay=overlay,
        adapter=adapter,
        cohort=cohort,
        target=target,
        maintainer=maintainer,
        channel=channel,
        debian_distribution=debian_distribution,
        require_maintainer=require_maintainer,
    )
    paths = {
        "manifest": Path(manifest_path),
        "overlay": Path(overlay_path),
        "adapter": adapter_path,
        "cohort": cohort_path,
        "target": target_path,
    }
    return model, recipe_root, paths


def generate(
    *,
    manifest_path,
    overlay_path,
    cohort_id: str,
    target_id: str,
    maintainer: str,
    channel: str = "release",
    debian_distribution: str = None,
    recipe_root=None,
    repo_root=None,
) -> tuple:
    """Load, model, render. Returns (model, outputs, record). Writes nothing."""
    model, recipe_root, paths = build(
        manifest_path=manifest_path,
        overlay_path=overlay_path,
        cohort_id=cohort_id,
        target_id=target_id,
        maintainer=maintainer,
        channel=channel,
        debian_distribution=debian_distribution,
        recipe_root=recipe_root,
        repo_root=repo_root,
    )
    outputs = render(model, recipe_root / "templates", recipe_root / "licenses")
    record = generation_record(
        model=model,
        outputs=outputs,
        inputs={**paths, **_template_inputs(recipe_root, model)},
    )
    return model, outputs, record


def _template_inputs(recipe_root: Path, model: dict) -> dict:
    """Digest every template and licence file that fed this render."""
    templates = recipe_root / "templates"
    fmt = model["target"]["package_format"]
    names = (
        [
            "debian/control.in",
            "debian/changelog.in",
            "debian/copyright.in",
            "debian/rules.in",
            "debian/source/format",
            "debian/source-lintian-overrides.in",
            "debian/docs.in",
            "debian/binary-lintian-overrides.in",
        ]
        if fmt == "deb"
        else ["rpm/vmod.spec.in"]
    )
    inputs = {f"template:{name}": templates / name for name in names}
    if fmt == "deb":
        short = model["license"]["debian_short_name"]
        inputs[f"license:{short}"] = recipe_root / "licenses" / f"{short}.debian"
    return inputs


def write_outputs(out_dir, outputs: dict, record: dict) -> list:
    """Write the rendered tree and its generation record. Sorted, deterministic."""
    out_dir = Path(out_dir)
    written = []
    for name in sorted(outputs):
        path = out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outputs[name], encoding="utf-8")
        if name.endswith("/rules") or name == "debian/rules":
            path.chmod(0o755)
        written.append(name)
    record_path = out_dir / "generation-record.json"
    record_path.write_text(dumps_record(record), encoding="utf-8")
    written.append("generation-record.json")
    return written


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def cmd_generate(args) -> int:
    model, outputs, record = generate(
        manifest_path=args.manifest,
        overlay_path=args.overlay,
        cohort_id=args.cohort,
        target_id=args.target,
        maintainer=args.maintainer,
        channel=args.channel,
        debian_distribution=args.debian_distribution,
        recipe_root=args.recipe_root,
    )
    written = write_outputs(args.out, outputs, record)
    for name in written:
        print(f"{Path(args.out) / name}")
    print(f"recipe sha256: {record['recipe_sha256']}", file=sys.stderr)
    return 0


def _inspect(args) -> dict:
    """Model only, for the subcommands that print rather than render.

    `--maintainer` is optional here. Refusing without one is a refusal to emit
    a recipe; it should not stop anybody asking what the package will be
    called, and a deb target's changelog suite is equally irrelevant to that
    question.
    """
    model, _recipe_root, _paths = build(
        manifest_path=args.manifest,
        overlay_path=args.overlay,
        cohort_id=args.cohort,
        target_id=args.target,
        maintainer=args.maintainer,
        channel=args.channel,
        debian_distribution=args.debian_distribution or "UNSET",
        recipe_root=args.recipe_root,
        require_maintainer=False,
    )
    return model


def cmd_names(args) -> int:
    print(json.dumps(_inspect(args)["artifacts"], indent=2, sort_keys=True))
    return 0


def cmd_model(args) -> int:
    print(json.dumps(_inspect(args), indent=2, sort_keys=True))
    return 0


_DOCKER_PLATFORM = {
    "amd64": "linux/amd64",
    "x86_64": "linux/amd64",
    "arm64": "linux/arm64",
    "aarch64": "linux/arm64",
}


def cmd_lane_env(args) -> int:
    """Shell assignments the containerised lane stages need.

    Derived, never typed: the package names and versions come from the same
    model the recipe was rendered from, and the engine facts from the registry.
    A lane script that computed any of these itself would be a second place for
    them to be wrong.
    """
    model = _inspect(args)
    package = model["package"]
    engine = model["engine"]
    payload = model["payload"]
    values = {
        "VMOD_SOURCE_NAME": package["debian_source_name"],
        "VMOD_BINARY_NAME": package["debian_binary_name"],
        "VMOD_RPM_NAME": package["rpm_name"],
        "VMOD_UPSTREAM_VERSION": model["source"]["version"],
        "VMOD_DEBIAN_VERSION": package["versions"]["debian"]["version"],
        "VMOD_RPM_RELEASE": package["versions"]["rpm"]["release"],
        "VMOD_SOURCE_DATE_EPOCH": model["source"]["source_date_epoch"],
        "VMOD_OBJECT": payload["vmod_object"],
        "VMOD_MAN_PAGE": payload["man_pages"][0],
        "VINYL_VMODDIR": engine["vmoddir"],
        "VINYL_STRICT_ABI": engine["strict_abi"],
        "VINYL_VRT": engine["vrt"],
        "COHORT_ID": engine["cohort"],
        # The container platform the lane must build in. Derived from the
        # TARGET's architecture, not from whatever the host happens to be: a
        # row for debian-13-amd64 must produce amd64 packages whether it runs
        # on a CI runner or on an arm64 laptop, and without this the local
        # verify stage would try to install x86_64 packages into an aarch64
        # container. Docker resolves it through binfmt where the host differs.
        "TARGET_PLATFORM": _DOCKER_PLATFORM.get(model["target"]["arch"], ""),
    }
    for key, value in values.items():
        escaped = str(value).replace("'", "'\\''")
        print(f"{key}='{escaped}'")
    return 0


def cmd_selftest(args) -> int:
    import vmod_recipe_selftest

    return vmod_recipe_selftest.main()


def _add_common(parser) -> None:
    parser.add_argument("--manifest", required=True, help="the VMOD's vmod-ci/v1 manifest")
    parser.add_argument("--overlay", required=True, help="the VMOD's packaging overlay")
    parser.add_argument("--cohort", required=True, help="cohort id, e.g. vinyl-9.0.1-ac4f719c16f4")
    parser.add_argument("--target", required=True, help="target id, e.g. debian-13-amd64")
    parser.add_argument("--maintainer", default="", help='package maintainer, "Name <email>"')
    parser.add_argument("--channel", default="release", help="source channel (default: release)")
    parser.add_argument(
        "--debian-distribution",
        default=None,
        help="changelog suite for deb targets, e.g. trixie (from the lane pin file)",
    )
    parser.add_argument("--recipe-root", default=None, help=f"default: {RECIPE_ROOT}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="render a native recipe tree")
    _add_common(p_gen)
    p_gen.add_argument("--out", required=True, help="output directory")
    p_gen.set_defaults(func=cmd_generate)

    p_names = sub.add_parser("names", help="print the expected package names")
    _add_common(p_names)
    p_names.set_defaults(func=cmd_names)

    p_model = sub.add_parser("model", help="print the normalized package model")
    _add_common(p_model)
    p_model.set_defaults(func=cmd_model)

    p_env = sub.add_parser(
        "lane-env", help="shell assignments the containerised lane stages need"
    )
    _add_common(p_env)
    p_env.set_defaults(func=cmd_lane_env)

    p_self = sub.add_parser("selftest", help="run this tool's own tests")
    p_self.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (GeneratorError, manifest_mod.ValidationError, yaml_subset.ManifestSyntaxError) as exc:
        print(f"E: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
