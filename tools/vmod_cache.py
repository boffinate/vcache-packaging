#!/usr/bin/env python3
"""Content-addressed cache inputs and snapshots for bounded VMOD batches.

The GitHub cache service stores this module's directory.  It deliberately
does not decide whether a build is valid: the cell result and its inputs are
checked again by ``vmod_batch.py`` before any saved output is reused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matrix  # noqa: E402


SCHEMA = "vmod-cache/1"
KEY_VERSION = "v1"
CACHEABLE_STATUSES = frozenset(matrix.STATUSES) - {"infra_failed"}


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_digest(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def tree_digest(root: Path) -> str | None:
    """Hash a controlled tree without archive timestamps or ownership bits."""
    if not root.is_dir():
        return None
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            entries.append((path.relative_to(root).as_posix(), sha256_file(path)))
        elif path.is_symlink():
            entries.append((path.relative_to(root).as_posix(), "link:" + os.readlink(path)))
    return sha256_bytes(canonical_json(entries))


def cache_cell_id(item: dict) -> str:
    return "--".join(str(item[key]) for key in ("row", "engine", "target", "mode"))


def result_filename(item: dict) -> str:
    return cache_cell_id(item) + ".json"


def package_directory_name(item: dict) -> str:
    return f"packages-{item['row']}-{item['engine']}-{item['target']}"


def package_payload_available(path: Path) -> bool:
    return path.is_dir() and any(candidate.is_file() and candidate.stat().st_size > 0 for candidate in path.rglob("*"))


def source_input(sources: Path, item: dict) -> dict:
    source = sources / item["source_artifact"]
    archive = source / "source.tar.gz"
    names = ("source-sha256", "vmod-id", "url", "ref", "commit")
    metadata = {name: file_digest(source / name) for name in names}
    values = {}
    for name in ("source-sha256", "vmod-id", "url", "ref", "commit"):
        try:
            values[name] = (source / name).read_text().strip()
        except OSError:
            values[name] = None
    digest_value = values["source-sha256"]
    digest_valid = bool(
        isinstance(digest_value, str)
        and len(digest_value) == 64
        and all(character in "0123456789abcdef" for character in digest_value)
    )
    archive_available = archive.is_file() and archive.stat().st_size > 0
    return {
        "artifact": item["source_artifact"],
        "files": metadata,
        "archive_available": archive_available,
        # The canonical source-tree digest detects content changes. Commit and ref
        # remain explicit because upstream build systems can derive versions
        # from Git metadata even when two checkout trees look identical.
        "metadata": values,
        "available": archive_available and digest_valid and all(metadata.values()) and all(values.values()),
    }


def harness_input(repo_root: Path) -> dict:
    """Hash repository files that can alter VMOD build or package behaviour."""
    named = [
        "scripts/build-engine.sh", "scripts/build-vmod.sh", "scripts/lib.sh", "tools/matrix.py", "tools/recipe.py",
        "tools/engine_batch.py", "tools/engine_cache.py",
        "tools/package_contract.py", "tools/source_api_normalize.py", "tools/cargo-artifacts.py",
        "tools/source_digest.py", "tools/vmod_batch.py", "tools/vmod_cache.py",
    ]
    files = {name: file_digest(repo_root / name) for name in named}
    files["packaging"] = tree_digest(repo_root / "packaging")
    return files


def cell_manifest(item: dict, engine_artifacts: Path, sources: Path, repo_root: Path, harness: dict | None = None) -> dict:
    catalog = matrix.load_catalog(repo_root)
    engine = matrix.find_engine(catalog, item["engine"])
    target = matrix.find_target(catalog, item["target"])
    manifest_path = repo_root / "vmods" / f"{item['row']}.yml"
    source = source_input(sources, item)
    prefix = engine_artifacts / f"engine-{item['engine']}-{item['target']}-prefix.tar.gz"
    packages = engine_artifacts / f"engine-{item['engine']}-{item['target']}-pkgs"
    try:
        engine_source_commit = (engine_artifacts / "engine-source-commit").read_text().strip()
    except OSError:
        engine_source_commit = ""
    trunk_commit_available = bool(
        len(engine_source_commit) == 40
        and all(character in "0123456789abcdef" for character in engine_source_commit)
    )
    prefix_available = prefix.is_file() and prefix.stat().st_size > 0
    packages_available = packages.is_dir() and any(path.is_file() for path in packages.iterdir())
    engine_identity_available = engine["kind"] != "trunk" or trunk_commit_available
    engine_available = prefix_available and engine_identity_available and (item["mode"] != "package" or packages_available)
    return {
        "schema": SCHEMA,
        "cell": {key: item[key] for key in ("row", "engine", "target", "mode", "runner", "source_artifact")},
        # Engine artifacts are not reproducible archives. The catalog contract
        # identifies releases, while trunk additionally needs its resolved
        # commit; the artifact is still required before any cell may reuse.
        "engine_contract": engine,
        "engine_source_commit": engine_source_commit,
        "target_contract": target,
        "engine_artifacts_available": engine_available,
        "source": source,
        "vmod_manifest_sha256": file_digest(manifest_path),
        "harness": harness if harness is not None else harness_input(repo_root),
    }


def fingerprint(manifest: dict) -> str:
    # A missing download deliberately cannot share the successful key: the
    # driver must still run and emit its infrastructure result in that case.
    stable = dict(manifest)
    stable.pop("engine_artifacts_available", None)
    return sha256_bytes(canonical_json(stable))


def cache_eligible(manifest: dict) -> bool:
    return bool(manifest["engine_artifacts_available"] and manifest["source"]["available"] and manifest["vmod_manifest_sha256"])


def batch_key(items: list[dict], engine_artifacts: Path, sources: Path, repo_root: Path) -> tuple[dict, dict[str, dict]]:
    harness = harness_input(repo_root)
    manifests = {cache_cell_id(item): cell_manifest(item, engine_artifacts, sources, repo_root, harness) for item in items}
    fingerprints = {cell: fingerprint(manifest) for cell, manifest in manifests.items()}
    contract = {
        "schema": SCHEMA,
        "engine": items[0]["engine"], "target": items[0]["target"],
        "mode": items[0]["mode"], "runner": items[0]["runner"],
        # Restore prefixes must not cross batch membership. Multiple six-cell
        # batches have the same execution contract and run concurrently.
        "cells": [cache_cell_id(item) for item in items],
        "engine_contract": manifests[cache_cell_id(items[0])]["engine_contract"],
        "target_contract": manifests[cache_cell_id(items[0])]["target_contract"],
        "harness": harness,
    }
    restore_prefix = f"vmod-batch-{KEY_VERSION}-{sha256_bytes(canonical_json(contract))}-"
    aggregate = sha256_bytes(canonical_json({"contract": contract, "cells": fingerprints}))
    return {
        "schema": SCHEMA,
        "key": restore_prefix + aggregate,
        "restore_prefix": restore_prefix,
        "aggregate_fingerprint": aggregate,
        "cells": {cell: {"fingerprint": fingerprints[cell], "eligible": cache_eligible(manifests[cell])} for cell in manifests},
    }, manifests


def load_result(path: Path, item: dict) -> dict | None:
    try:
        result = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict) or result.get("schema") != "cell/1":
        return None
    if result.get("status") not in CACHEABLE_STATUSES:
        return None
    if any(result.get(key) != item[key] for key in ("row", "engine", "target", "mode")):
        return None
    return result


def restore_cell(cache_dir: Path, workdir: Path, item: dict, manifest: dict) -> bool:
    if not cache_eligible(manifest):
        return False
    cell = cache_dir / "cells" / cache_cell_id(item)
    try:
        record = json.loads((cell / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if record.get("schema") != SCHEMA or record.get("fingerprint") != fingerprint(manifest):
        return False
    source_result = cell / "result.json"
    result = load_result(source_result, item)
    if result is None:
        return False
    packages = cell / "packages"
    if item["mode"] == "package" and result["status"] == "pass" and not package_payload_available(packages):
        return False
    destination = workdir / "results" / result_filename(item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_result, destination)
    if packages.is_dir():
        destination = workdir / "packages" / package_directory_name(item)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(packages, destination)
    return True


def save_cell(cache_dir: Path, workdir: Path, item: dict, manifest: dict) -> bool:
    if not cache_eligible(manifest):
        return False
    result = workdir / "results" / result_filename(item)
    result_data = load_result(result, item)
    if result_data is None:
        return False
    packages = workdir / "packages" / package_directory_name(item)
    if item["mode"] == "package" and result_data["status"] == "pass" and not package_payload_available(packages):
        return False
    cell = cache_dir / "cells" / cache_cell_id(item)
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "manifest.json").write_bytes(canonical_json({"schema": SCHEMA, "fingerprint": fingerprint(manifest), "inputs": manifest}) + b"\n")
    shutil.copy2(result, cell / "result.json")
    if (cell / "packages").exists():
        shutil.rmtree(cell / "packages")
    if packages.is_dir():
        shutil.copytree(packages, cell / "packages", dirs_exist_ok=True)
    return True


def expected_result_status(workdir: Path, item: dict) -> str | None:
    result = workdir / "results" / result_filename(item)
    try:
        value = json.loads(result.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value.get("status") if isinstance(value, dict) else None


def results_cacheable(workdir: Path, items: list[dict]) -> bool:
    """Require every expected result and reject only infrastructure evidence."""
    for item in items:
        result = workdir / "results" / result_filename(item)
        try:
            value = json.loads(result.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(value, dict) or value.get("schema") != "cell/1":
            return False
        if any(value.get(key) != item[key] for key in ("row", "engine", "target", "mode")):
            return False
        if value.get("status") not in CACHEABLE_STATUSES:
            return False
        packages = workdir / "packages" / package_directory_name(item)
        if item["mode"] == "package" and value["status"] == "pass" and not package_payload_available(packages):
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    key = sub.add_parser("key", help="emit a batch cache key after inputs are downloaded")
    key.add_argument("--engine-artifacts", type=Path, required=True)
    key.add_argument("--sources", type=Path, required=True)
    key.add_argument("--items")
    key.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    cacheable = sub.add_parser("cacheable", help="report whether a complete batch snapshot may be saved")
    cacheable.add_argument("--workdir", type=Path, required=True)
    cacheable.add_argument("--items")
    args = parser.parse_args(argv)
    try:
        items = json.loads(args.items if args.items is not None else os.environ["VMOD_BATCH_ITEMS"])
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise ValueError("VMOD_BATCH_ITEMS must be a non-empty JSON array of objects")
        if args.command == "key":
            report, _ = batch_key(items, args.engine_artifacts, args.sources, args.repo_root)
            print(f"key={report['key']}")
            print(f"restore_prefix={report['restore_prefix']}")
        else:
            print(f"cacheable={'true' if results_cacheable(args.workdir, items) else 'false'}")
        return 0
    except (KeyError, ValueError, json.JSONDecodeError, matrix.CatalogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
