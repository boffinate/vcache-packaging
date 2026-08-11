#!/usr/bin/env python3
"""Install a Cargo VMOD's declared release artifacts into a package tree."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import matrix


def install(release_dir: Path, destination: Path, mappings: list[str]) -> None:
    pairs = []
    modules = set()
    artifacts = set()
    for mapping in mappings:
        try:
            module, artifact = mapping.split("=", 1)
        except ValueError:
            raise ValueError(f"invalid artifact mapping {mapping!r}") from None
        if not matrix.MODULE_NAME_RE.fullmatch(module) or not matrix.ARTIFACT_BASENAME_RE.fullmatch(artifact):
            raise ValueError(f"invalid artifact mapping {mapping!r}")
        if module in modules:
            raise ValueError(f"duplicate Cargo module mapping: {module}")
        if artifact in artifacts:
            raise ValueError(f"duplicate Cargo artifact mapping: {artifact}")
        modules.add(module)
        artifacts.add(artifact)
        pairs.append((module, artifact))
    if not pairs:
        raise ValueError("no Cargo artifact mappings were declared")
    actual = {path.name for path in release_dir.glob("*.so") if path.is_file()}
    if actual != artifacts:
        missing = sorted(artifacts - actual)
        extra = sorted(actual - artifacts)
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected: {', '.join(extra)}")
        raise ValueError("Cargo release artifact mismatch (" + "; ".join(details) + ")")
    destination.mkdir(parents=True, exist_ok=True)
    for module, artifact in pairs:
        source = release_dir / artifact
        if source.stat().st_size == 0:
            raise ValueError(f"Cargo artifact is empty: {source}")
        target = destination / f"libvmod_{module}.so"
        shutil.copy2(source, target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--mapping", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        install(args.release_dir, args.destination, args.mapping)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
