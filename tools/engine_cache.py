#!/usr/bin/env python3
"""Create and validate cache snapshots for engine batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
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


def _catalog(root: Path):
    sys.path.insert(0, str(root / "tools"))
    import matrix

    return matrix, matrix.load_catalog(root)


def resolve_trunk_commit(root: Path, git_url: str, branch: str) -> str:
    """Resolve a moving branch through the retry-governed shell boundary."""
    command = root / "scripts" / "resolve-engine-commit.sh"
    try:
        completed = subprocess.run(
            ["bash", str(command), git_url, branch], check=True, stdout=subprocess.PIPE, text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"could not resolve {git_url} {branch}: exit {exc.returncode}") from exc
    commit = completed.stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError(f"resolver returned an invalid commit for {git_url} {branch}: {commit!r}")
    return commit


def resolve_items(root: Path, items: list[dict], resolver=resolve_trunk_commit) -> list[dict]:
    """Bind every trunk item to one advertised source commit before cache lookup."""
    matrix, catalog = _catalog(root)
    commits: dict[tuple[str, str], str] = {}
    resolved = []
    for item in items:
        engine = matrix.find_engine(catalog, item["engine"])
        output = dict(item)
        if engine["kind"] == "trunk":
            source = engine["source"]
            identity = (source["git_url"], source["branch"])
            if identity not in commits:
                commits[identity] = resolver(root, *identity)
            output["source_commit"] = commits[identity]
        resolved.append(output)
    return resolved


def cache_key(root: Path, items: list[dict]) -> str:
    contract = {"items": items, "files": _contract_files(root)}
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    matrix, catalog = _catalog(root)
    kinds = {matrix.find_engine(catalog, item["engine"])["kind"] for item in items}
    if len(kinds) != 1:
        raise ValueError("engine cache batch must not mix release and trunk engines")
    if kinds == {"trunk"}:
        return "vcache-engine-trunk-v1-" + hashlib.sha256(encoded).hexdigest()
    # Separate namespaces prevent a trunk entry with moving source identity
    # from ever satisfying a release lookup.
    return "vcache-engine-v1-" + hashlib.sha256(encoded).hexdigest()


def _expected_files(root: Path, items: list[dict]) -> tuple[list[Path], list[Path]]:
    matrix, catalog = _catalog(root)
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
    matrix, catalog = _catalog(root)
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
        if matrix.find_engine(catalog, item["engine"])["kind"] == "trunk":
            expected_commit = item.get("source_commit")
            artifact_commit = root / "work" / "artifacts" / f"engine-{item['engine']}-{item['target']}" / "engine-source-commit"
            try:
                actual_commit = artifact_commit.read_text().strip()
            except OSError:
                return False
            if actual_commit != expected_commit:
                return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("resolve", "key", "cacheable"))
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
        resolved = resolve_items(args.root, items) if args.command == "resolve" else items
        matrix, catalog = _catalog(args.root)
        for item in resolved:
            engine = matrix.find_engine(catalog, item["engine"])
            if engine["kind"] == "trunk" and (
                not isinstance(item.get("source_commit"), str)
                or len(item["source_commit"]) != 40
                or any(character not in "0123456789abcdef" for character in item["source_commit"])
            ):
                raise ValueError("trunk engine batch entries must contain a lowercase 40-character source_commit")
        if args.command == "resolve":
            print("items=" + json.dumps(resolved, separators=(",", ":")))
        elif args.command == "key":
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
