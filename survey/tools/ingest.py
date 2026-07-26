#!/usr/bin/env python3
"""Build the VMOD survey worklist from the vinyl-cache.org directory data.

Fetches the VMOD registration JSON files from the homepage repository on
code.vinyl-cache.org (anonymous raw access; the git transport is dumb-HTTP
and impractically slow, and archive downloads require login), merges in
survey/data/manual-additions.json, and writes the normalised worklist to
survey/data/worklist.json.

Python 3 standard library only, per the repository tooling rule.
"""

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FORGE_BASE = "https://code.vinyl-cache.org"
REPO = "vinyl-cache/homepage"
VMODS_PATH = "R1/source/vmods"
SCHEMA = "vmod-survey-worklist/v1"

SURVEY_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = SURVEY_DIR / "cache" / "homepage-vmods"
DATA_DIR = SURVEY_DIR / "data"


def http_get(url, retries=3, timeout=30):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vcache-packaging-vmod-survey"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as err:
            last_err = err
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


def resolve_head():
    data = json.loads(http_get(f"{FORGE_BASE}/api/v1/repos/{REPO}/branches/main"))
    return data["commit"]["id"], data["commit"]["timestamp"]


def list_vmod_files(commit):
    listing = json.loads(http_get(f"{FORGE_BASE}/api/v1/repos/{REPO}/contents/{VMODS_PATH}?ref={commit}"))
    names = sorted(
        entry["name"]
        for entry in listing
        if entry["type"] == "file" and entry["name"].startswith("vmod_") and entry["name"].endswith(".json")
    )
    if not names:
        raise RuntimeError("directory listing returned no vmod_*.json files")
    return names


def fetch_file(commit, name):
    """Fetch one registration file, using the per-commit cache.

    Uses the contents API rather than the raw endpoints: raw-by-commit
    answers with a ~24s redirect per file on this Forgejo, while the
    contents API serves base64 content pinned to the commit immediately.
    """
    cached = CACHE_DIR / commit / name
    if cached.exists():
        return cached.read_bytes()
    doc = json.loads(http_get(f"{FORGE_BASE}/api/v1/repos/{REPO}/contents/{VMODS_PATH}/{name}?ref={commit}"))
    if doc.get("encoding") != "base64" or doc.get("content") is None:
        raise RuntimeError(f"contents API returned no base64 content for {name}")
    raw = base64.b64decode(doc["content"])
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(raw)
    return raw


def normalise(record, origin, source_file, warnings):
    """Map one registration record to the worklist entry shape."""
    entry = {
        "name": record.get("name"),
        "desc": record.get("desc"),
        "status": record.get("status"),
        "inactive": bool(record.get("inactive", False)),
        "date": record.get("date"),
        "license": record.get("license"),
        "maintainer": record.get("maintainer"),
        "support": record.get("support", []),
        "product": record.get("product"),
        "origin": origin,
        "source_file": source_file,
        "forge": None,
        "clone_url": None,
        "web_url": None,
        "branches": {},
        "vcc_path": None,
        "doc_path": None,
        "warnings": warnings,
    }
    if not entry["name"]:
        warnings.append("missing required field: name")

    github = record.get("github")
    if github:
        user, project = github.get("user"), github.get("project")
        if user and project:
            entry["forge"] = "github"
            entry["clone_url"] = f"https://github.com/{user}/{project}.git"
            entry["web_url"] = f"https://github.com/{user}/{project}"
        else:
            warnings.append("github object missing user/project")
        entry["branches"] = github.get("branches", {})
        entry["vcc_path"] = github.get("vcc_path")
        entry["doc_path"] = github.get("doc_path")
    elif record.get("repos"):
        repos = record["repos"]
        url = None
        if isinstance(repos, str):
            url = repos
        elif isinstance(repos, dict) and repos:
            # Label → URL map, e.g. {"UPLEX": ..., "gitlab": ...}. Prefer the
            # big-forge mirror (fast smart-HTTP transport) over self-hosted.
            mirrors = {label.lower(): value for label, value in repos.items()}
            for preferred in ("github", "gitlab"):
                if preferred in mirrors:
                    url = mirrors[preferred]
                    break
            else:
                url = next(iter(repos.values()))
            entry["mirrors"] = repos
        else:
            warnings.append(f"unrecognised repos field shape: {type(repos).__name__}")
        if url:
            entry["forge"] = "other"
            entry["web_url"] = url
            # Sourcehut clone URLs are the bare repo URL; elsewhere the .git
            # suffix distinguishes the clone URL from the web URL.
            if url.endswith(".git") or "git.sr.ht" in url:
                entry["clone_url"] = url
            else:
                entry["clone_url"] = url + ".git"
        rev = record.get("rev")
        if isinstance(rev, dict):
            entry["branches"] = {
                version: value.get("branch", version) if isinstance(value, dict) else str(value)
                for version, value in rev.items()
            }
    else:
        warnings.append("no github object and no repos field: not cloneable")

    if entry["date"] in (None, "", "YYYY-MM-DD"):
        entry["date"] = None
        warnings.append("no usable last-revision date")
    return entry


def load_manual_additions():
    path = DATA_DIR / "manual-additions.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    entries = []
    for record in doc.get("entries", []):
        warnings = []
        entry = normalise(record, "manual", "manual-additions.json", warnings)
        entries.append(entry)
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", help="homepage commit id to ingest (default: resolve main HEAD)")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "worklist.json")
    args = parser.parse_args()

    if args.commit:
        commit, commit_time = args.commit, None
    else:
        commit, commit_time = resolve_head()
    print(f"homepage {REPO} @ {commit}" + (f" ({commit_time})" if commit_time else ""), file=sys.stderr)

    names = list_vmod_files(commit)
    print(f"{len(names)} registration files", file=sys.stderr)

    entries, parse_failures = [], []
    for name in names:
        raw = fetch_file(commit, name)
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as err:
            parse_failures.append({"source_file": name, "error": str(err)})
            print(f"  PARSE FAIL {name}: {err}", file=sys.stderr)
            continue
        entries.append(normalise(record, "homepage", name, []))

    manual = load_manual_additions()
    by_name = {entry["name"]: index for index, entry in enumerate(entries)}
    for entry in manual:
        if entry["name"] in by_name:
            entry["warnings"].append("overrides the homepage entry of the same name")
            entries[by_name[entry["name"]]] = entry
        else:
            entries.append(entry)

    entries.sort(key=lambda entry: (entry["name"] or "", entry["origin"]))
    active = [entry for entry in entries if not entry["inactive"]]
    cloneable = [entry for entry in entries if entry["clone_url"]]

    doc = {
        "schema": SCHEMA,
        "homepage_repo": f"{FORGE_BASE}/{REPO}",
        "homepage_commit": commit,
        "homepage_commit_time": commit_time,
        "generated_by": "survey/tools/ingest.py",
        "counts": {
            "total": len(entries),
            "homepage": sum(1 for entry in entries if entry["origin"] == "homepage"),
            "manual": len(manual),
            "active": len(active),
            "inactive": len(entries) - len(active),
            "cloneable": len(cloneable),
            "parse_failures": len(parse_failures),
        },
        "parse_failures": parse_failures,
        "entries": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
    print(f"wrote {args.output} counts={doc['counts']}", file=sys.stderr)


if __name__ == "__main__":
    main()
