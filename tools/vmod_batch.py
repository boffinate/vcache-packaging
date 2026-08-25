#!/usr/bin/env python3
"""Run several matrix cells sequentially while preserving cell isolation."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_ROW_KEYS = {"row", "engine", "target", "mode", "runner", "source_artifact"}


def link_or_copy(source: str, destination: str) -> str:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def materialize_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    shutil.copytree(source, destination, copy_function=link_or_copy, dirs_exist_ok=True)


def collect_tree(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def validate_items(items: list[dict]) -> None:
    if not items:
        raise ValueError("VMOD batch must contain at least one cell")
    for item in items:
        missing = REQUIRED_ROW_KEYS - set(item)
        if missing:
            raise ValueError(f"VMOD batch cell is missing keys: {', '.join(sorted(missing))}")
    contracts = {(item["engine"], item["target"], item["mode"], item["runner"]) for item in items}
    if len(contracts) != 1:
        raise ValueError("VMOD batch cells must share engine, target, mode, and runner")


def emit_timeout_result(cell: Path, item: dict, timeout: float) -> None:
    results = cell / "results"
    results.mkdir(parents=True, exist_ok=True)
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    result = {
        "schema": "cell/1",
        "row": item["row"],
        "engine": item["engine"],
        "target": item["target"],
        "mode": item["mode"],
        "ref": "",
        "commit": "",
        "status": "infra_failed",
        "detail": f"cell exceeded its {timeout:g}-second batch timeout",
        "source_api_normalization": "",
        "failure_step": "",
        "run_url": f"{server}/{repository}/actions/runs/{run_id}" if run_id else "",
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    destination = results / f"{item['row']}--{item['engine']}--{item['target']}--{item['mode']}.json"
    destination.write_text(json.dumps(result) + "\n")


def cleanup_timed_out_containers(cell: Path) -> None:
    for cidfile in (cell / "tmp").glob("*.cid"):
        container = cidfile.read_text().strip()
        if container:
            subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def run_batch(items: list[dict], engine_artifacts: Path, sources: Path, workdir: Path, repo_root: Path, cell_timeout: float = 3600) -> int:
    validate_items(items)
    results = workdir / "results"
    packages = workdir / "packages"
    results.mkdir(parents=True, exist_ok=True)
    packages.mkdir(parents=True, exist_ok=True)
    failed = False
    for index, item in enumerate(items, 1):
        cell_id = f"{index:02d}-{item['row']}-{item['mode']}"
        cell = workdir / "cells" / cell_id
        (cell / "engine" / "artifacts").mkdir(parents=True, exist_ok=True)
        materialize_tree(engine_artifacts, cell / "engine" / "artifacts")
        materialize_tree(sources / item["source_artifact"], cell / "vmod-source")
        print(f"::group::{item['row']} vs {item['engine']} ({item['mode']}, {item['target']})", flush=True)
        try:
            process = subprocess.Popen(
                [
                    str(repo_root / "scripts" / "build-vmod.sh"),
                    item["row"],
                    item["engine"],
                    item["target"],
                    item["mode"],
                    str(cell),
                ],
                env={**os.environ, "VCACHE_REQUIRE_PREFETCHED_VMOD_SOURCE": "1"},
                start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=cell_timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                cleanup_timed_out_containers(cell)
                emit_timeout_result(cell, item, cell_timeout)
                returncode = 1
            failed = returncode != 0 or failed
        finally:
            collect_tree(cell / "results", results)
            collect_tree(
                cell / "packages",
                packages / f"packages-{item['row']}-{item['engine']}-{item['target']}",
            )
            print("::endgroup::", flush=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-artifacts", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--cell-timeout", type=float, default=3600)
    args = parser.parse_args(argv)
    try:
        items = json.loads(os.environ["VMOD_BATCH_ITEMS"])
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("VMOD_BATCH_ITEMS must be a JSON array of objects")
        if args.cell_timeout <= 0:
            raise ValueError("--cell-timeout must be positive")
        return run_batch(items, args.engine_artifacts, args.sources, args.workdir, args.repo_root, args.cell_timeout)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
