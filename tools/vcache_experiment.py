#!/usr/bin/env python3
"""Expand a production matrix lane using the experimental VCACHE API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matrix  # noqa: E402


COMMIT_LENGTH = 40
LANE_MODES = {"release": "all", "trunk": "compat"}


def validate_commit(value: str) -> str:
    if len(value) != COMMIT_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("--vinyl-commit must be a lowercase 40-character commit")
    return value


def expand(root: Path, vinyl_commit: str, lane: str) -> dict:
    catalog = matrix.load_catalog(root)
    expansion = matrix.expand(catalog, lane, LANE_MODES[lane])

    engines = []
    for item in expansion["engines"]:
        item = dict(item)
        if item["engine"] == "vinyl-trunk":
            item["source_commit"] = vinyl_commit
        engines.append(item)

    vmods = []
    for item in expansion["vmods"]:
        item = dict(item)
        vmod = catalog["vmods"][item["row"]]
        if matrix.vmod_build(vmod) == "autotools":
            item["source_api_strategy"] = "vcache"
        vmods.append(item)

    source_groups = {vmod["id"]: matrix.vmod_build(vmod) for vmod in catalog["vmods"].values()}
    source_batches, source_artifacts = matrix.batch_sources(expansion["sources"], source_groups)
    return {
        "engine_batches": matrix.batch_engines(engines),
        "source_batches": source_batches,
        "vmod_batches": matrix.batch_vmods(vmods, source_artifacts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vinyl-commit", required=True)
    parser.add_argument("--lane", required=True, choices=tuple(LANE_MODES))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    try:
        commit = validate_commit(args.vinyl_commit)
        output = expand(args.root, commit, args.lane)
        for name in ("engine_batches", "source_batches", "vmod_batches"):
            print(name + "=" + json.dumps(output[name], separators=(",", ":")))
    except (matrix.CatalogError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
