#!/usr/bin/env python3
"""Keep only declared VMOD shared objects in an upstream install tree."""

from __future__ import annotations

import argparse
from pathlib import Path

import matrix


def retain(stage_root: Path, vmod_dir: Path, modules: list[str]) -> None:
    if not stage_root.is_dir():
        raise ValueError(f"VMOD staging root does not exist: {stage_root}")
    if not vmod_dir.is_absolute():
        raise ValueError(f"VMOD directory must be absolute: {vmod_dir}")
    if not modules or any(not matrix.MODULE_NAME_RE.fullmatch(module) for module in modules):
        raise ValueError("VMOD modules must be non-empty valid module names")
    if len(modules) != len(set(modules)):
        raise ValueError("VMOD modules must be unique")

    destination = stage_root / vmod_dir.relative_to("/")
    expected = {destination / f"libvmod_{module}.so" for module in modules}
    for artifact in expected:
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise ValueError(f"declared VMOD artifact is missing or empty: {artifact}")

    for path in sorted(stage_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path in expected:
            continue
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
        else:
            path.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--vmod-dir", type=Path, required=True)
    parser.add_argument("--modules", nargs="+", required=True)
    args = parser.parse_args(argv)
    try:
        retain(args.stage_root, args.vmod_dir, args.modules)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
