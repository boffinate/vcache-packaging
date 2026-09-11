#!/usr/bin/env python3
"""Expand the isolated upstream VCACHE API compatibility experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matrix  # noqa: E402


COMMIT_LENGTH = 40


def validate_commit(value: str) -> str:
    if len(value) != COMMIT_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("--vinyl-commit must be a lowercase 40-character commit")
    return value


def expand(root: Path, vinyl_commit: str, target_ids: list[str]) -> dict:
    catalog = matrix.load_catalog(root)
    engine = matrix.find_engine(catalog, "vinyl-trunk")
    unknown = [target for target in target_ids if target not in engine["targets"]]
    if unknown:
        raise ValueError("target(s) are not supported by vinyl-trunk: " + ", ".join(unknown))

    engines = []
    vmods = []
    for target_id in target_ids:
        target = matrix.find_target(catalog, target_id)
        engines.append({
            "engine": engine["id"],
            "target": target_id,
            "runner": target["runner"],
            "source_commit": vinyl_commit,
        })
        for vmod in catalog["vmods"].values():
            if vmod.get("source_api_family") != "varnish" or matrix.vmod_build(vmod) != "autotools":
                continue
            row = matrix.vmod_matrix_row(vmod, engine, target_id, "compat", target["runner"])
            row["source_api_strategy"] = "vcache"
            vmods.append(row)

    sources = []
    seen = set()
    for row in vmods:
        if row["source_artifact"] in seen:
            continue
        seen.add(row["source_artifact"])
        sources.append({
            "row": row["row"],
            "engine": row["engine"],
            "source_artifact": row["source_artifact"],
        })
    source_groups = {vmod["id"]: matrix.vmod_build(vmod) for vmod in catalog["vmods"].values()}
    source_batches, source_artifacts = matrix.batch_sources(sources, source_groups)
    return {
        "engine_batches": matrix.batch_engines(engines),
        "source_batches": source_batches,
        "vmod_batches": matrix.batch_vmods(vmods, source_artifacts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vinyl-commit", required=True)
    parser.add_argument("--targets", default="debian-13-amd64,debian-13-arm64")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    try:
        commit = validate_commit(args.vinyl_commit)
        targets = args.targets.split(",")
        if not targets or any(not target or target != target.strip() for target in targets):
            raise ValueError("--targets must be a comma-separated list without whitespace")
        if len(set(targets)) != len(targets):
            raise ValueError("--targets must not repeat a target")
        output = expand(args.root, commit, targets)
        for name in ("engine_batches", "source_batches", "vmod_batches"):
            print(name + "=" + json.dumps(output[name], separators=(",", ":")))
    except (matrix.CatalogError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
