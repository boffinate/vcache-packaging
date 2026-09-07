#!/usr/bin/env python3
"""Compare pinned VMOD source commits with each upstream's configured head.

The catalog deliberately keeps release sources pinned. This maintenance tool
reports which pins differ from the moving branch declared in ``sources.head``;
``--apply`` changes only pins whose current upstream head has complete passing
trunk compatibility evidence in the saved matrix state.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matrix  # noqa: E402


@dataclass(frozen=True)
class Pin:
    vmod: str
    source: str
    git_url: str
    head_ref: str
    pinned_commit: str


def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def pins(catalog: dict) -> list[Pin]:
    """Return every catalog source entry that declares an immutable commit."""
    output = []
    for vmod_id, vmod in sorted(catalog["vmods"].items()):
        sources = vmod["sources"]
        entries = [("default", sources["default"])]
        entries.extend((f"by_series.{series}", entry) for series, entry in sorted(sources.get("by_series", {}).items()))
        for source, entry in entries:
            commit = entry.get("commit", "")
            if commit:
                output.append(Pin(vmod_id, source, vmod["upstream"]["git"], sources["head"], commit))
    return output


def resolve_head(git_url: str, branch: str, timeout: int) -> str:
    """Resolve one advertised branch without checking out its repository."""
    command = ["git", "ls-remote", "--exit-code", "--heads", git_url, f"refs/heads/{branch}"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"timed out after {timeout}s") from exc
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"git exited {result.returncode}"
        raise RuntimeError(detail)
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1:
        raise RuntimeError(f"expected one result for refs/heads/{branch}, got {len(lines)}")
    commit, _, ref = lines[0].partition("\t")
    if ref != f"refs/heads/{branch}" or not matrix.COMMIT_RE.fullmatch(commit):
        raise RuntimeError(f"invalid git ls-remote result: {lines[0]!r}")
    return commit


def resolve_heads(pins_to_check: list[Pin], jobs: int, timeout: int) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """Resolve each distinct remote branch once, retaining every lookup error."""
    identities = sorted({(pin.git_url, pin.head_ref) for pin in pins_to_check})
    heads: dict[tuple[str, str], str] = {}
    errors: dict[tuple[str, str], str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {executor.submit(resolve_head, git_url, branch, timeout): (git_url, branch) for git_url, branch in identities}
        for future in concurrent.futures.as_completed(futures):
            identity = futures[future]
            try:
                heads[identity] = future.result()
            except Exception as exc:  # A failed remote must not hide the rest of the audit.
                errors[identity] = str(exc)
    return heads, errors


def results(pins_to_check: list[Pin], heads: dict[tuple[str, str], str], errors: dict[tuple[str, str], str]) -> list[dict]:
    output = []
    for pin in pins_to_check:
        identity = (pin.git_url, pin.head_ref)
        error = errors.get(identity)
        head = heads.get(identity, "")
        status = "error" if error else "current" if head == pin.pinned_commit else "changed"
        output.append({
            "vmod": pin.vmod,
            "source": pin.source,
            "git_url": pin.git_url,
            "head_ref": pin.head_ref,
            "pinned_commit": pin.pinned_commit,
            "head_commit": head,
            "status": status,
            "error": error or "",
        })
    return output


def load_saved_state(root: Path, remote: str, branch: str, fetch: bool) -> dict:
    """Read the state branch after optionally refreshing its remote object."""
    if fetch:
        fetched = subprocess.run(["git", "fetch", "--quiet", remote, branch], cwd=root,
                                 capture_output=True, text=True)
        if fetched.returncode:
            detail = fetched.stderr.strip() or fetched.stdout.strip() or f"git exited {fetched.returncode}"
            raise RuntimeError(f"could not fetch {remote}/{branch}: {detail}")
        ref = "FETCH_HEAD"
    else:
        ref = f"{remote}/{branch}"
    shown = subprocess.run(["git", "show", f"{ref}:matrix-state.json"], cwd=root,
                           capture_output=True, text=True)
    if shown.returncode:
        detail = shown.stderr.strip() or shown.stdout.strip() or f"git exited {shown.returncode}"
        raise RuntimeError(f"could not read matrix state from {ref}: {detail}")
    try:
        state = json.loads(shown.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"matrix state is not valid JSON: {exc}") from exc
    if state.get("schema") != matrix.STATE_SCHEMA or not isinstance(state.get("cells"), dict):
        raise RuntimeError("matrix state has an invalid schema or cells map")
    return state


def expected_trunk_cells(catalog: dict) -> dict[str, list[str]]:
    """Return the compatibility cells that prove each moving VMOD head."""
    expected: dict[str, list[str]] = {vmod_id: [] for vmod_id in catalog["vmods"]}
    for row in matrix.expand(catalog, "trunk", "compat")["vmods"]:
        expected[row["row"]].append("/".join((row["row"], row["engine"], row["target"], row["mode"])))
    return expected


def promotion_results(rows: list[dict], catalog: dict, state: dict) -> list[dict]:
    """Mark changed pins green only when every current trunk cell passed."""
    expected = expected_trunk_cells(catalog)
    output = []
    for row in rows:
        result = dict(row)
        if row["status"] == "error":
            result["promotion"] = "unresolved"
            result["evidence"] = "upstream head could not be resolved"
        elif row["status"] == "current":
            result["promotion"] = "not_needed"
            result["evidence"] = "pin already matches upstream head"
        else:
            cells = [state["cells"].get(key) for key in expected[row["vmod"]]]
            passing = [cell for cell in cells if cell and cell.get("status") == "pass" and cell.get("commit") == row["head_commit"]]
            result["promotion"] = "green" if len(passing) == len(cells) else "not_green"
            result["evidence"] = f"{len(passing)}/{len(cells)} trunk compatibility cell(s) pass at head"
        output.append(result)
    return output


def apply_pins(root: Path, pins_to_apply: list[Pin], heads: dict[tuple[str, str], str]) -> None:
    """Replace only the commits proven green by the saved matrix state."""
    for pin in pins_to_apply:
        head = heads[(pin.git_url, pin.head_ref)]
        path = root / "vmods" / f"{pin.vmod}.yml"
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^(\s*commit: )([\"']?){re.escape(pin.pinned_commit)}\2$", re.MULTILINE)
        text, replacements = pattern.subn(
            lambda match: f"{match.group(1)}{match.group(2)}{head}{match.group(2)}", text)
        if replacements != 1:
            raise RuntimeError(f"{path}: expected exactly one commit line for {pin.source}, found {replacements}")
        path.write_text(text, encoding="utf-8")


def print_text(rows: list[dict]) -> None:
    for row in rows:
        if row["status"] == "error":
            print(f"error   {row['vmod']} {row['source']} {row['head_ref']}: {row['error']}")
        else:
            print(
                f"{row['status']:<7} {row['vmod']:<16} {row['source']:<24} "
                f"{row['head_ref']:<16} {row['pinned_commit']} {row['head_commit']}"
            )
    changed = sum(row["status"] == "changed" for row in rows)
    errors = sum(row["status"] == "error" for row in rows)
    print(f"{len(rows)} pinned source(s) checked: {changed} changed, {errors} error(s)")


def print_promotion_text(rows: list[dict]) -> None:
    for row in rows:
        print(f"{row['promotion']:<11} {row['vmod']:<16} {row['source']:<24} {row['evidence']}")
    green = sum(row["promotion"] == "green" for row in rows)
    print(f"{green} pin(s) can move from current trunk compatibility evidence")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=default_root(), help="repo root holding engines.yml and vmods/")
    parser.add_argument("--jobs", type=int, default=8, help="concurrent remote lookups (default: 8)")
    parser.add_argument("--timeout", type=int, default=30, help="seconds allowed per remote lookup (default: 30)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--check", action="store_true", help="exit 1 when an upstream head differs from a pin")
    parser.add_argument("--green", action="store_true", help="require passing trunk compatibility evidence at the current head")
    parser.add_argument("--state-file", type=Path, help="matrix-state.json to use instead of fetching ci-state/matrix")
    parser.add_argument("--state-remote", default="origin", help="remote holding ci-state/matrix (default: origin)")
    parser.add_argument("--state-branch", default="ci-state/matrix", help="state branch name (default: ci-state/matrix)")
    parser.add_argument("--no-fetch-state", action="store_true", help="use the locally fetched state branch")
    parser.add_argument("--apply", action="store_true", help="update pins that --green proves safe to move")
    return parser


def main(argv=None, resolver=resolve_heads) -> int:
    args = build_parser().parse_args(argv)
    if args.jobs < 1:
        print("error: --jobs must be positive", file=sys.stderr)
        return 2
    if args.timeout < 1:
        print("error: --timeout must be positive", file=sys.stderr)
        return 2
    if args.apply and not args.green:
        print("error: --apply requires --green", file=sys.stderr)
        return 2
    try:
        catalog = matrix.load_catalog(args.root)
    except matrix.CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    pins_to_check = pins(catalog)
    heads, errors = resolver(pins_to_check, args.jobs, args.timeout)
    rows = results(pins_to_check, heads, errors)
    if args.green:
        try:
            state = matrix.load_state(args.state_file) if args.state_file else load_saved_state(
                args.root, args.state_remote, args.state_branch, not args.no_fetch_state)
        except (OSError, RuntimeError, matrix.CatalogError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        rows = promotion_results(rows, catalog, state)
        if args.apply:
            green_pins = [pin for pin, row in zip(pins_to_check, rows) if row["promotion"] == "green"]
            try:
                apply_pins(args.root, green_pins, heads)
                matrix.load_catalog(args.root)
            except (OSError, RuntimeError, matrix.CatalogError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
    if args.format == "json":
        print(json.dumps(rows, indent=2, sort_keys=True))
    elif args.green:
        print_promotion_text(rows)
    else:
        print_text(rows)
    if errors or (args.check and any(row["status"] == "changed" for row in rows)):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
