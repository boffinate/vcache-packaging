#!/usr/bin/env python3
"""Validate native package payloads before a stable release is mutated.

The release workflow uses this module after all build cells have reported
success.  Keeping package identity and metadata checks here makes the gate
unit-testable without requiring a package toolchain on the host.
"""

from __future__ import annotations

import shutil
import subprocess
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


def _metadata(path: Path) -> tuple[str, str]:
    """Read package name and architecture using the native package tool."""
    try:
        fields = package_contract.native_fields(path)
    except package_contract.ContractError as exc:
        raise PayloadError(f"cannot inspect native package {path.name}: {exc}") from exc
    return fields["Package"], fields["Architecture"]


def _expected_packages(engine: dict, target: dict, cells: list[dict]) -> dict[str, set[str]]:
    expected: dict[str, set[str]] = {}
    for cell in cells:
        mode = cell["mode"]
        if mode == "engine":
            names = {
                matrix.engine_runtime_package(engine),
                matrix.engine_development_package(engine, target["format"]),
            }
            source = f"engine-{engine['id']}-{cell['target']}"
        elif mode == "package":
            names = {matrix.engine_vmod_package_name(engine, cell["row"])}
            source = f"packages-{cell['row']}-{engine['id']}-{cell['target']}"
        else:
            raise PayloadError(f"unsupported release cell mode {mode!r}")
        expected[source] = names
    return expected


def validate_pair_payload(
    pkgdl: Path,
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
    reader = metadata_reader or _metadata
    expected = _expected_packages(engine, target, cells)
    suffix = f".{target['format']}"
    verified: list[Path] = []
    for source_name, names in expected.items():
        sources = [path for path in Path(pkgdl).rglob(source_name) if path.is_dir()]
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
            name, arch = reader(artifact)
            if name not in names:
                raise PayloadError(f"{source_name}: unexpected package {name!r} ({artifact.name})")
            if arch != target["package_arch"]:
                raise PayloadError(
                    f"{source_name}: {name} has architecture {arch!r}, "
                    f"expected {target['package_arch']!r}"
                )
            if name in seen:
                raise PayloadError(f"{source_name}: duplicate package metadata {name!r}")
            seen[name] = artifact
        missing = sorted(names - set(seen))
        if missing:
            raise PayloadError(f"{source_name}: missing package artifact(s): {', '.join(missing)}")
        verified.extend(seen[name] for name in sorted(names))

    if stage_dir is not None:
        stage = Path(stage_dir)
        stage.mkdir(parents=True, exist_ok=True)
        for artifact in verified:
            destination = stage / github_release_asset_name(artifact)
            if destination.exists():
                raise PayloadError(f"duplicate staged release asset name {destination.name}")
            shutil.copy2(artifact, destination)
    return verified
