#!/usr/bin/env python3
"""Create and validate cache snapshots for pinned release engine batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _file_fingerprint(root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _contract_files(root: Path) -> list[dict[str, str]]:
    names = [
        "engines.yml", "tools/matrix.py", "tools/engine_batch.py", "tools/engine_cache.py",
        "scripts/build-engine.sh", "scripts/lib.sh",
    ]
    names.extend(str(path.relative_to(root)) for path in sorted((root / "packaging" / "engine").rglob("*")) if path.is_file())
    return [_file_fingerprint(root, name) for name in names]


def cache_key(root: Path, items: list[dict]) -> str:
    contract = {"items": items, "files": _contract_files(root)}
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    return "vcache-engine-v1-" + hashlib.sha256(encoded).hexdigest()


def _expected_files(root: Path, items: list[dict]) -> tuple[list[Path], list[Path]]:
    sys.path.insert(0, str(root / "tools"))
    import matrix

    catalog = matrix.load_catalog(root)
    artifacts: list[Path] = []
    results: list[Path] = []
    for item in items:
        engine = matrix.find_engine(catalog, item["engine"])
        target = item["target"]
        prefix = root / "work" / "artifacts" / f"engine-{item['engine']}-{target}"
        artifacts.append(prefix / f"engine-{item['engine']}-{target}-prefix.tar.gz")
        if engine.get("packages") == "true":
            packages = prefix / "engine-{}-{}-pkgs".format(item["engine"], target)
            artifacts.extend([packages])
        results.append(root / "work" / "results" / f"{item['engine']}--{item['engine']}--{target}--engine.json")
    return artifacts, results


def cacheable(root: Path, items: list[dict]) -> bool:
    artifacts, results = _expected_files(root, items)
    for path in artifacts:
        if not path.exists():
            return False
        if path.is_file() and path.stat().st_size == 0:
            return False
        if path.is_dir() and not any(path.iterdir()):
            return False
    if any(not path.is_file() for path in results):
        return False
    for item, path in zip(items, results):
        try:
            result = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if result.get("schema") != "cell/1" or result.get("mode") != "engine" or result.get("status") != "pass":
            return False
        if result.get("row") != item["engine"] or result.get("engine") != item["engine"] or result.get("target") != item["target"]:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("key", "cacheable"))
    parser.add_argument("--items", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    try:
        items = json.loads(args.items)
        required = {"engine", "target", "runner"}
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise ValueError("ENGINE_BATCH_ITEMS must be a non-empty JSON array of objects")
        if any(required - set(item) for item in items):
            raise ValueError("ENGINE_BATCH_ITEMS entries must contain engine, target, and runner")
        if args.command == "key":
            key = cache_key(args.root, items)
            print(f"key={key}")
        else:
            print(f"cacheable={'true' if cacheable(args.root, items) else 'false'}")
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
