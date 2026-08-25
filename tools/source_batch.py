#!/usr/bin/env python3
"""Fetch a bounded group of resolved VMOD sources on one runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


REQUIRED_KEYS = {"row", "engine", "source_artifact"}
SOURCE_FETCH_PARALLELISM = 5


def fetch(item: dict, workdir: Path, repo_root: Path) -> tuple[int, str]:
    destination = workdir / "sources" / item["source_artifact"]
    process = subprocess.run([
        str(repo_root / "scripts" / "fetch-vmod-source.sh"),
        item["row"],
        item["engine"],
        str(destination),
    ], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return process.returncode, process.stdout


def run_batch(items: list[dict], workdir: Path, repo_root: Path, parallelism: int = SOURCE_FETCH_PARALLELISM) -> int:
    if not items:
        raise ValueError("source batch must contain at least one item")
    if parallelism < 1:
        raise ValueError("source batch parallelism must be positive")
    for item in items:
        missing = REQUIRED_KEYS - set(item)
        if missing:
            raise ValueError(f"source batch item is missing keys: {', '.join(sorted(missing))}")
    workdir.mkdir(parents=True, exist_ok=True)
    failed = False
    with ThreadPoolExecutor(max_workers=min(parallelism, len(items))) as executor:
        futures = {executor.submit(fetch, item, workdir, repo_root): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            returncode, output = future.result()
            print(f"::group::source {item['row']} for {item['engine']}")
            print(output, end="")
            print("::endgroup::", flush=True)
            failed = returncode != 0 or failed
    return 1 if failed else 0


def main() -> int:
    try:
        items = json.loads(os.environ["SOURCE_BATCH_ITEMS"])
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("SOURCE_BATCH_ITEMS must be a JSON array of objects")
        return run_batch(items, Path("work"), Path(__file__).resolve().parent.parent)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
