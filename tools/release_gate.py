#!/usr/bin/env python3
"""Validate native package payloads before an immutable release is published.

The release workflow uses this module after all build cells have reported
success.  Keeping package identity and metadata checks here makes the gate
unit-testable without requiring a package toolchain on the host.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import matrix
import package_contract


class PayloadError(Exception):
    """A release pair does not contain exactly the packages it promises."""


def github_release_asset_name(path: Path) -> str:
    """Return the filename GitHub will retain for a release asset."""
    # GitHub replaces tildes in uploaded release asset names with dots. Stage
    # the same spelling that users and downstream checksum validators download.
    return path.name.replace("~", ".")


def _metadata(path: Path, target: dict) -> dict:
    """Read native metadata in the package target's utility container."""
    try:
        fields = package_contract.container_fields(path, target)
    except package_contract.ContractError as exc:
        raise PayloadError(f"cannot inspect native package {path.name}: {exc}") from exc
    return fields


def _rpm_version_matches(actual: str, rendered: str) -> bool:
    """Accept RPM's optional dist suffix without weakening the catalog revision."""
    base = rendered.removesuffix("%{?dist}")
    return actual == base or actual.startswith(base + ".")


def _version_matches(actual: str, expected: dict, package_format: str) -> bool:
    if package_format == "deb":
        return actual == expected["deb"]
    return _rpm_version_matches(actual, expected["rpm"])


def _vmod_dependency_matches(fields: dict, runtime_name: str, runtime_version: str,
                             package_format: str, package_arch: str) -> bool:
    if package_format == "deb":
        return package_contract.deb_exact_dependency(fields.get("Depends", ""), runtime_name,
                                                     runtime_version)
    requirement = package_contract.rpm_exact_requirement(runtime_name, package_arch, runtime_version)
    return requirement in fields.get("Requires", [])


def _expected_packages(catalog: dict, engine: dict, target: dict,
                       cells: list[dict]) -> dict[str, dict[str, dict]]:
    """Return the catalog contract for each build artifact directory."""
    expected: dict[str, dict[str, dict]] = {}
    engine_version = matrix.engine_package_version(engine)
    package_format = target["format"]
    runtime = matrix.engine_runtime_package(engine)
    for cell in cells:
        mode = cell["mode"]
        if mode == "engine":
            names = {
                runtime: {"version": {"deb": engine_version["deb"], "rpm": engine_version["rpm"]}},
                matrix.engine_development_package(engine, package_format): {
                    "version": {"deb": engine_version["deb"], "rpm": engine_version["rpm"]}
                },
            }
            source = f"engine-{engine['id']}-{cell['target']}"
        elif mode == "package":
            try:
                vmod = catalog["vmods"][cell["row"]]
            except KeyError as exc:
                raise PayloadError(f"unknown VMOD row {cell['row']!r} in release cell") from exc
            resolved = matrix.resolve_source(vmod, engine)
            version = matrix.vmod_package_version(resolved["version"], engine)
            names = {
                matrix.engine_vmod_package_name(engine, cell["row"]): {
                    "version": {
                        "deb": version["deb"],
                        "rpm": f"{version['rpm_version']}-{version['rpm_release']}%{{?dist}}",
                    },
                    "runtime": runtime,
                }
            }
            source = f"packages-{cell['row']}-{engine['id']}-{cell['target']}"
        else:
            raise PayloadError(f"unsupported release cell mode {mode!r}")
        expected[source] = names
    return expected


def _artifact_directories(pkgdl: Path, expected: dict[str, dict]) -> dict[str, list[Path]]:
    """Index the downloaded artifact tree once before checking each contract."""
    directories = {name: [] for name in expected}
    for path in Path(pkgdl).rglob("*"):
        if path.is_dir() and path.name in directories:
            directories[path.name].append(path)
    return directories


def validate_pair_payload(
    pkgdl: Path,
    catalog: dict,
    engine: dict,
    target: dict,
    cells: list[dict],
    *,
    metadata_reader=None,
    stage_dir: Path | None = None,
) -> list[Path]:
    """Verify and optionally stage every native package for one release pair.

    The metadata name and architecture are authoritative; filenames alone are
    never accepted as proof of package identity.  A pair must contain exactly
    the runtime/development engine packages and one package for each promoted
    VMOD cell.
    """
    if target["format"] not in matrix.TARGET_FORMATS:
        raise PayloadError(f"unsupported target package format {target['format']!r}")
    if metadata_reader is None:
        reader = lambda path: _metadata(path, target)
    else:
        reader = metadata_reader
    expected = _expected_packages(catalog, engine, target, cells)
    directories = _artifact_directories(Path(pkgdl), expected)
    suffix = f".{target['format']}"
    verified: list[Path] = []
    staged_metadata: dict[str, dict] = {}
    for source_name, names in expected.items():
        sources = directories[source_name]
        if not sources:
            raise PayloadError(f"missing package artifact directory {source_name}")
        if len(sources) > 1:
            raise PayloadError(f"duplicate package artifact directory {source_name}")
        source = sources[0]
        artifacts = sorted(p for p in source.rglob(f"*{suffix}") if p.is_file())
        if not artifacts:
            raise PayloadError(f"{source_name}: no native {target['format']} artifacts")
        seen: dict[str, Path] = {}
        for artifact in artifacts:
            fields = reader(artifact)
            name = fields["Package"]
            arch = fields["Architecture"]
            if name not in names:
                raise PayloadError(f"{source_name}: unexpected package {name!r} ({artifact.name})")
            if arch != target["package_arch"]:
                raise PayloadError(
                    f"{source_name}: {name} has architecture {arch!r}, "
                    f"expected {target['package_arch']!r}"
                )
            if name in seen:
                raise PayloadError(f"{source_name}: duplicate package metadata {name!r}")
            if not _version_matches(fields.get("Version", ""), names[name]["version"], target["format"]):
                raise PayloadError(
                    f"{source_name}: {name} has version {fields.get('Version', '')!r}, "
                    f"expected catalog revision {names[name]['version'][target['format']]!r}"
                )
            seen[name] = artifact
            staged_metadata[name] = fields
        missing = sorted(set(names) - set(seen))
        if missing:
            raise PayloadError(f"{source_name}: missing package artifact(s): {', '.join(missing)}")
        verified.extend(seen[name] for name in sorted(names))

    runtime_name = matrix.engine_runtime_package(engine)
    runtime = staged_metadata.get(runtime_name)
    if runtime is None:
        raise PayloadError(f"missing staged runtime package {runtime_name!r}")
    runtime_version = runtime["Version"]
    for source_name, names in expected.items():
        for name, contract in names.items():
            if "runtime" not in contract:
                continue
            if not _vmod_dependency_matches(staged_metadata[name], runtime_name, runtime_version,
                                            target["format"], target["package_arch"]):
                raise PayloadError(
                    f"{source_name}: {name} does not require exact staged runtime "
                    f"{runtime_name} {runtime_version}"
                )

    if stage_dir is not None:
        stage = Path(stage_dir)
        stage.mkdir(parents=True, exist_ok=True)
        for artifact in verified:
            destination = stage / github_release_asset_name(artifact)
            if destination.exists():
                raise PayloadError(f"duplicate staged release asset name {destination.name}")
            shutil.copy2(artifact, destination)
    return verified
