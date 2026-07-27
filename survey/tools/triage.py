#!/usr/bin/env python3
"""Static triage of the survey worklist: clone each VMOD repo and extract signals.

Host-side analysis only — nothing is built here; builds happen in the survey
harness containers. Per repository this records: reachability, default branch,
head commit and date, the .vcc $ABI declaration, private-header usage, build
system, and whether the claimed compatibility branches actually exist.

The recorded head_commit doubles as the survey's pin: by default a rerun
checks out exactly the commit the existing triage snapshot records (also
after the cache directory was deleted), so the committed snapshot and the
sweep always describe the same code. Moving the survey to current upstream
HEADs is an explicit act: --repin. With --only, untouched entries keep their
existing records instead of being dropped from the snapshot.

Python 3 standard library only, per the repository tooling rule.
"""

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "vmod-survey-triage/v1"
SURVEY_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SURVEY_DIR / "data"
REPOS_DIR = SURVEY_DIR / "cache" / "repos"

# Including these marks a VMOD as depending on the daemon's private surface;
# the VRT API (vrt.h, vcc_if.h) is the public one. Distinct failure odds and
# porting cost follow from this, per the downstream packaging plan.
PRIVATE_INCLUDE_PATTERNS = [
    "cache/cache.h",
    "cache_varnishd.h",
    "struct objcore",
    "struct busyobj",
    "struct worker",
    "HSH_",
    "EXP_",
]

SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".vcc", ".in", ".am", ".ac"}


def run_git(args, timeout, cwd=None):
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": "/tmp"},
    )


def ls_remote(url):
    """Return (default_branch, [branch names]) from the remote."""
    proc = run_git(["ls-remote", "--symref", "--heads", url, "HEAD", "refs/heads/*"], timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"ls-remote failed: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'unknown error'}")
    default = None
    branches = []
    for line in proc.stdout.splitlines():
        if line.startswith("ref:"):
            match = re.match(r"ref: refs/heads/(\S+)\s+HEAD", line)
            if match:
                default = match.group(1)
        else:
            parts = line.split("\t")
            if len(parts) == 2 and parts[1].startswith("refs/heads/"):
                branches.append(parts[1][len("refs/heads/"):])
    return default, branches


def clone(url, dest, branch):
    if (dest / ".git").exists():
        return "cached"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Full history: a shallow clone cannot check out the recorded pin commit
    # once the branch has moved, and dumb-HTTP remotes (code.uplex.de) reject
    # shallow clones anyway. These repos are small.
    args = ["clone", "--no-tags", "--single-branch"]
    if branch:
        args += ["--branch", branch]
    proc = run_git([*args, url, str(dest)], timeout=900)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(f"clone failed: {stderr.splitlines()[-1] if stderr else 'unknown error'}")
    return "cloned"


def init_submodules(dest):
    # After checkout, not after clone: .gitmodules can differ per commit.
    if (dest / ".gitmodules").exists():
        run_git(["submodule", "update", "--init", "--recursive"], timeout=600, cwd=dest)


def ensure_pin(dest, pin):
    """Make the cache tree match the recorded pin, fetching as needed."""
    def checkout():
        return run_git(["checkout", "--quiet", "--detach", pin], timeout=120, cwd=dest).returncode == 0
    if not checkout():
        # Pre-pinning caches are shallow; deepen until the pin is reachable.
        for fetch in (["fetch", "--quiet", "origin", pin],
                      ["fetch", "--quiet", "--unshallow"],
                      ["fetch", "--quiet", "origin"]):
            run_git(fetch, timeout=900, cwd=dest)
            if checkout():
                break
        else:
            raise RuntimeError(f"pinned commit {pin[:12]} not reachable from the remote")
    init_submodules(dest)


def advance(dest, branch):
    """Move the checkout to the tip of the remote default branch (--repin)."""
    run_git(["fetch", "--quiet", "origin"], timeout=900, cwd=dest)
    target = f"origin/{branch}" if branch else "FETCH_HEAD"
    proc = run_git(["checkout", "--quiet", "--detach", target], timeout=120, cwd=dest)
    if proc.returncode != 0:
        raise RuntimeError(f"repin failed: cannot check out {target}")
    init_submodules(dest)


def head_info(dest):
    proc = run_git(["log", "-1", "--format=%H %cI"], timeout=30, cwd=dest)
    if proc.returncode != 0:
        return None, None
    parts = proc.stdout.strip().split()
    return (parts[0], parts[1]) if len(parts) == 2 else (None, None)


def scan_tree(dest):
    """Extract static signals from a checked-out tree."""
    signals = {
        "vcc_files": [],
        "abi": None,
        "build_system": [],
        "private_includes": {},
        "configure_varnish_refs": [],
    }
    abi_declarations = set()
    for path in dest.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(dest).as_posix()
        name = path.name
        if name in ("configure.ac", "configure.in"):
            signals["build_system"].append("autotools")
            text = path.read_text(errors="ignore")
            for token in ("VARNISHAPI", "varnishapi", "VARNISH_PREREQ", "VARNISH_VMODS", "VARNISH_VMOD_INCLUDES", "vinylapi", "VINYL_PREREQ"):
                if token in text and token not in signals["configure_varnish_refs"]:
                    signals["configure_varnish_refs"].append(token)
        elif name == "CMakeLists.txt" and path.parent == dest:
            signals["build_system"].append("cmake")
        elif name == "meson.build" and path.parent == dest:
            signals["build_system"].append("meson")

        if path.suffix in SOURCE_SUFFIXES:
            if name.endswith(".vcc") or name.endswith(".vcc.in"):
                signals["vcc_files"].append(rel)
                for match in re.finditer(r"^\s*\$ABI\s+(\w+)", path.read_text(errors="ignore"), re.MULTILINE):
                    abi_declarations.add(match.group(1))
            if path.suffix in (".c", ".h", ".cc", ".cpp"):
                try:
                    text = path.read_text(errors="ignore")
                except OSError:
                    continue
                for pattern in PRIVATE_INCLUDE_PATTERNS:
                    if pattern in text:
                        signals["private_includes"][pattern] = signals["private_includes"].get(pattern, 0) + 1

    if abi_declarations:
        signals["abi"] = "+".join(sorted(abi_declarations))
    elif signals["vcc_files"]:
        signals["abi"] = "undeclared(strict-default)"
    else:
        signals["abi"] = "no-vcc-found"
    signals["build_system"] = sorted(set(signals["build_system"])) or ["none-detected"]
    return signals


def triage_one(entry, pin=None, repin=False):
    result = {
        "name": entry["name"],
        "origin": entry["origin"],
        "inactive": entry["inactive"],
        "clone_url": entry["clone_url"],
        "ok": False,
        "clone_state": None,
        "default_branch": None,
        "remote_branches": None,
        "claimed_branches_missing": [],
        "head_commit": None,
        "head_date": None,
        "error": None,
    }
    if not entry["clone_url"]:
        result["error"] = "no clone url"
        return result
    dest = REPOS_DIR / entry["name"]
    try:
        default, branches = ls_remote(entry["clone_url"])
        result["default_branch"] = default
        result["remote_branches"] = len(branches)
        claimed = set(entry.get("branches", {}).values())
        result["claimed_branches_missing"] = sorted(claimed - set(branches))
        result["clone_state"] = clone(entry["clone_url"], dest, default)
        if repin:
            advance(dest, default)
        elif pin:
            ensure_pin(dest, pin)
        else:
            init_submodules(dest)
        result["head_commit"], result["head_date"] = head_info(dest)
        result.update(scan_tree(dest))
        result["ok"] = True
    except (RuntimeError, subprocess.TimeoutExpired) as err:
        result["error"] = str(err)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worklist", type=Path, default=DATA_DIR / "worklist.json")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "triage.json")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--only", nargs="*", help="triage only these vmod names")
    parser.add_argument("--repin", action="store_true",
                        help="advance pins to the remote default-branch tips instead of honouring the recorded head commits")
    args = parser.parse_args()

    worklist = json.loads(args.worklist.read_text())
    entries = worklist["entries"]
    if args.only:
        entries = [entry for entry in entries if entry["name"] in set(args.only)]

    prior_results = {}
    if args.output.exists():
        try:
            prior_results = {r["name"]: r for r in json.loads(args.output.read_text()).get("results", [])}
        except ValueError:
            prior_results = {}
    pins = {} if args.repin else {name: r.get("head_commit") for name, r in prior_results.items()}

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(triage_one, entry, pins.get(entry["name"]), args.repin): entry["name"]
            for entry in entries
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            state = "ok" if result["ok"] else f"FAIL ({result['error']})"
            print(f"  {result['name']:<24} {state}", file=sys.stderr)

    if args.only:
        # Merge into the existing snapshot: --only must not truncate the pin
        # store for every unselected VMOD.
        merged = dict(prior_results)
        merged.update({r["name"]: r for r in results})
        results = list(merged.values())

    results.sort(key=lambda r: r["name"])
    ok = [r for r in results if r["ok"]]
    doc = {
        "schema": SCHEMA,
        "worklist_homepage_commit": worklist.get("homepage_commit"),
        "generated_by": "survey/tools/triage.py",
        "counts": {
            "total": len(results),
            "ok": len(ok),
            "failed": len(results) - len(ok),
            "abi": {},
            "build_system": {},
        },
        "results": results,
    }
    for result in ok:
        doc["counts"]["abi"][result["abi"]] = doc["counts"]["abi"].get(result["abi"], 0) + 1
        key = "+".join(result["build_system"])
        doc["counts"]["build_system"][key] = doc["counts"]["build_system"].get(key, 0) + 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {args.output} counts={doc['counts']}", file=sys.stderr)


if __name__ == "__main__":
    main()
