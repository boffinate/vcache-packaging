#!/usr/bin/env python3
"""Gate, stage, and serially publish immutable package release cohorts."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

import matrix
import release_gate
import release_publish


def _results() -> tuple[dict, dict]:
    results, cohorts = {}, {}
    for path in glob.glob("results/**/*.json", recursive=True):
        with open(path) as stream:
            cell = json.load(stream)
        if cell.get("schema") == "cohort/1":
            cohorts[(cell["engine"], cell["target"])] = cell["status"]
        else:
            results[(cell["row"], cell["engine"], cell["target"], cell["mode"])] = cell["status"]
    return results, cohorts


def _write_release_files(directory: Path, engine: str, target: str) -> tuple[Path, list[str]]:
    names = sorted(path.name for path in directory.iterdir() if path.is_file())
    sums = subprocess.run(["sha256sum", "--", *names], cwd=directory, check=True,
                          stdout=subprocess.PIPE, text=True).stdout
    (directory / "SHA256SUMS").write_text(sums)
    body = Path(f"body-{directory.name}.md")
    body.write_text(
        "This release contains every packaged module for this target, or it would not have been published.\n\n"
        f"Packages built from the pinned release catalog for `{target}`, against engine `{engine}`. "
        "Every package below passed its build-and-install-and-load check.\n\n## Packages\n\n" +
        "".join(f"- `{name}`\n" for name in names)
    )
    return body, names


def main() -> int:
    immutable_configured = os.environ.get("IMMUTABLE_RELEASES_CONFIGURED") == "true"
    if not immutable_configured:
        raise SystemExit("IMMUTABLE_RELEASES_CONFIGURED=true is required before publication")
    catalog = matrix.load_catalog(".")
    targets = matrix.release_package_target_filter(catalog, os.environ.get("RELEASE_TARGETS", ""))
    expansion = matrix.expand(catalog, "release", "package", targets)
    if not expansion["engines"] or not expansion["vmods"]:
        raise SystemExit("release package matrix is empty")
    results, cohort_results = _results()
    pairs: dict[tuple[str, str], list[dict]] = {}
    for cell in expansion["rows"]:
        pairs.setdefault((cell["engine"], cell["target"]), []).append(cell)
    prepared = []
    failed = False
    for (engine_id, target_id), cells in sorted(pairs.items()):
        engine = matrix.find_engine(catalog, engine_id)
        tag = release_publish.tag_name(engine, target_id)
        if cohort_results.get((engine_id, target_id), "missing result") != "pass":
            print(f"gate FAILED for {tag} — package cohort: {cohort_results.get((engine_id, target_id), 'missing result')}")
            failed = True
            continue
        failures = [(cell, results.get((cell["row"], engine_id, target_id, cell["mode"]), "missing result"))
                    for cell in cells]
        failures = [(cell, status) for cell, status in failures if status != "pass"]
        if failures:
            print(f"gate FAILED for {tag} — publishing nothing for this pair:")
            for cell, status in failures:
                print(f"  {cell['row']} ({cell['mode']}): {status}")
            failed = True
            continue
        directory = Path("dist") / tag
        shutil.rmtree(directory, ignore_errors=True)
        try:
            release_gate.validate_pair_payload("pkgdl", catalog, engine, matrix.find_target(catalog, target_id), cells,
                                               stage_dir=directory)
        except release_gate.PayloadError as exc:
            print(f"gate FAILED for {tag}: {exc}")
            failed = True
            continue
        body, names = _write_release_files(directory, engine_id, target_id)
        prepared.append((tag, tag.removesuffix(target_id), directory, body, names))
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    commit = os.environ.get("GITHUB_SHA", "")
    if not repo or not commit:
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_SHA are required for publication")
    api = release_publish.GhApi(repo)
    if not prepared:
        print("release validation failed for every pair; nothing was published")
        return 1
    releases = release_publish._releases(api)
    for tag, prefix, directory, body, names in prepared:
        release = release_publish.publish(api, tag, commit, tag, body.read_text(), directory, prefix,
                                          immutable_configured=immutable_configured, releases=releases)
        releases = [item for item in releases if item.get("tag_name") != tag] + [release]
        print(f"published {tag}: {len(names)} packages")
    if failed:
        print("release validation failed for one or more pairs; green pairs were published")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
