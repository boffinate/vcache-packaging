#!/usr/bin/env python3
"""Build and render a Vinyl-trunk VMOD source-conversion comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matrix  # noqa: E402


ENGINE = "vinyl-trunk"
COMMIT_LENGTH = 40
# (column id, column label, per-cell source strategy). Every column is built
# fresh against the same pinned engine so the columns differ only in how the
# VMOD source was prepared.
STRATEGIES = (
    ("unmodified", "No VMOD patching", "none"),
    ("issue-4537", "Issue #4537 recipe as posted", "vcache"),
    ("issue-4537-fixed", "Recipe with fixes", "vcache-fixed"),
    ("current-rules", "Current packaging rules", "directional"),
)
# The recipe operates at the shell and m4 level, so distributions cannot
# change its verdict. Two targets keep one comparison iteration near an hour.
TARGETS = ("debian-13-amd64", "debian-13-arm64")


def validate_commit(value: str) -> str:
    if len(value) != COMMIT_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("--vinyl-commit must be a lowercase 40-character commit")
    return value


def _named_batches(rows: list[dict], source_artifacts: dict[str, str], name: str) -> list[dict]:
    batches = matrix.batch_vmods(rows, source_artifacts)
    for batch in batches:
        batch["batch"] = f"{name}-{batch['batch']}"
    return batches


def expand(root: Path, vinyl_commit: str) -> dict:
    catalog = matrix.load_catalog(root)
    expansion = matrix.expand(catalog, "trunk", "compat")
    engine_rows = [
        dict(item, source_commit=vinyl_commit)
        for item in expansion["engines"]
        if item["engine"] == ENGINE and item["target"] in TARGETS
    ]
    base_vmods = [item for item in expansion["vmods"] if item["engine"] == ENGINE and item["target"] in TARGETS]
    artifacts = {item["source_artifact"] for item in base_vmods}
    source_rows = [item for item in expansion["sources"] if item["source_artifact"] in artifacts]
    source_groups = {vmod["id"]: matrix.vmod_build(vmod) for vmod in catalog["vmods"].values()}
    source_batches, source_artifacts = matrix.batch_sources(source_rows, source_groups)

    vmod_batches = []
    for name, _, source_strategy in STRATEGIES:
        rows = [dict(item, source_api_strategy=source_strategy) for item in base_vmods]
        vmod_batches.extend(_named_batches(rows, source_artifacts, name))
    return {
        "engine_batches": matrix.batch_engines(engine_rows),
        "source_batches": source_batches,
        "vmod_batches": vmod_batches,
    }


def _load_results(path: Path) -> list[dict]:
    if not path.is_dir():
        raise matrix.CatalogError(f"{path}: results directory not found")
    return [matrix.load_cell(candidate) for candidate in sorted(path.rglob("*.json"))]


def _remap(cell: dict, strategy: str) -> dict:
    remapped = dict(cell)
    remapped["engine"] = strategy
    return remapped


def _select(cells: list[dict], mode: str) -> list[dict]:
    return [cell for cell in cells if cell["engine"] == ENGINE and cell["mode"] == mode]


def comparison_state(strategy_results: dict[str, list[dict]], engine_results: list[dict]) -> dict:
    state = {"schema": matrix.STATE_SCHEMA, "cells": {}, "infra_failures": {}}
    fresh_engine = _select(engine_results, "engine")
    observations = []
    for strategy, cells in strategy_results.items():
        observations.extend(_remap(cell, strategy) for cell in _select(cells, "compat"))
        observations.extend(_remap(cell, strategy) for cell in fresh_engine)
    matrix.merge_cells(state, observations)
    return state


def _engine_commit(state: dict) -> str:
    commits = {
        cell.get("commit", "")
        for cell in state["cells"].values()
        if cell["mode"] == "engine" and cell.get("commit")
    }
    return ", ".join(sorted(commit[:12] for commit in commits)) or "unknown commit"


def render(root: Path, results: dict[str, Path], engine_results: Path, out: Path, state_out: Path | None,
           generated_at: str) -> None:
    catalog = matrix.load_catalog(root)
    columns = [name for name, _, _ in STRATEGIES]
    missing = [name for name in columns if name not in results]
    if missing:
        raise ValueError(f"missing results for strategies: {', '.join(missing)}")
    state = comparison_state(
        {name: _load_results(results[name]) for name in columns},
        _load_results(engine_results),
    )
    labels = {name: label for name, label, _ in STRATEGIES}
    rows = ["(engine)"] + list(catalog["vmods"])
    row_urls = {row: vmod["upstream"].get("homepage", "") for row, vmod in catalog["vmods"].items()}
    grids = []
    for target in TARGETS:
        grid = matrix.build_grid(state, target)
        grid["columns"] = columns
        grid["column_labels"] = labels
        grid["rows"] = rows
        grid["row_urls"] = row_urls
        grids.append(grid)

    note = (
        f"Every column is built fresh against Vinyl {_engine_commit(state)}. "
        "No VMOD patching leaves upstream source untouched. Issue #4537 recipe as posted applies the sed recipe from "
        "the issue blindly across the tree. Recipe with fixes adds the corrections found necessary: pkg-config probes "
        "each API in turn, the m4_ifndef prerequisite guard names VCACHE_REQUIRE, and PKG_CHECK_MODULES producers are "
        "renamed with their consumers. Current packaging rules are this repository's directional translator. "
        "Hover over a cell for the failing step, diagnostic, source revision, run and timestamp."
    )
    rendered = matrix.render_html(
        grids,
        generated_at or matrix.now_iso(),
        page_context="Vinyl trunk source conversion comparison",
        matrix_key=(
            "Rows are modules and columns are source-handling strategies against Vinyl trunk. Green passes; red fails; "
            "amber marks a source conversion; grey means no result was available."
        ),
        matrix_note=note,
        axis_label="VMOD \\ source handling",
        normalization_legend="source changed",
        normalization_help="The build changed VMOD source before compiling it against Vinyl trunk.",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    if state_out is not None:
        state_out.parent.mkdir(parents=True, exist_ok=True)
        state_out.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    expand_parser = subparsers.add_parser("expand")
    expand_parser.add_argument("--vinyl-commit", required=True)
    expand_parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument(
        "--results", action="append", default=[], metavar="STRATEGY=DIR",
        help="results directory for one comparison column; repeat for every strategy",
    )
    render_parser.add_argument("--engine-results", type=Path, required=True)
    render_parser.add_argument("--out", type=Path, required=True)
    render_parser.add_argument("--state-out", type=Path)
    render_parser.add_argument("--generated-at", default="")
    render_parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    try:
        if args.command == "expand":
            output = expand(args.root, validate_commit(args.vinyl_commit))
            for name in ("engine_batches", "source_batches", "vmod_batches"):
                print(name + "=" + json.dumps(output[name], separators=(",", ":")))
        else:
            results = {}
            for item in args.results:
                strategy, separator, directory = item.partition("=")
                if not separator or not directory:
                    raise ValueError(f"--results expects STRATEGY=DIR, got {item!r}")
                results[strategy] = Path(directory)
            render(args.root, results, args.engine_results, args.out, args.state_out, args.generated_at)
    except (matrix.CatalogError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
