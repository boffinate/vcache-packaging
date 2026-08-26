#!/usr/bin/env python3
"""Build every engine for one target while preserving per-engine outputs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_KEYS = {"engine", "target", "runner"}


def collect_tree(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def run_batch(items: list[dict], workdir: Path, repo_root: Path) -> int:
    if not items:
        raise ValueError("engine batch must contain at least one item")
    for item in items:
        missing = REQUIRED_KEYS - set(item)
        if missing:
            raise ValueError(f"engine batch item is missing keys: {', '.join(sorted(missing))}")
    contracts = {(item["target"], item["runner"]) for item in items}
    if len(contracts) != 1:
        raise ValueError("engine batch items must share target and runner")
    failed = False
    for index, item in enumerate(items, 1):
        cell = workdir / "cells" / f"{index:02d}-{item['engine']}"
        print(f"::group::engine {item['engine']} on {item['target']}", flush=True)
        try:
            environment = os.environ.copy()
            environment.pop("ENGINE_SOURCE_COMMIT", None)
            if "source_commit" in item:
                environment["ENGINE_SOURCE_COMMIT"] = item["source_commit"]
            returncode = subprocess.run([
                str(repo_root / "scripts" / "build-engine.sh"),
                item["engine"],
                item["target"],
                str(cell),
            ], check=False, env=environment).returncode
            failed = returncode != 0 or failed
        finally:
            collect_tree(
                cell / "artifacts",
                workdir / "artifacts" / f"engine-{item['engine']}-{item['target']}",
            )
            collect_tree(cell / "results", workdir / "results")
            print("::endgroup::", flush=True)
    return 1 if failed else 0


def main() -> int:
    try:
        items = json.loads(os.environ["ENGINE_BATCH_ITEMS"])
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("ENGINE_BATCH_ITEMS must be a JSON array of objects")
        return run_batch(items, Path("work"), Path(__file__).resolve().parent.parent)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
