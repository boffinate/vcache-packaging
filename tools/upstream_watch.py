#!/usr/bin/env python3
"""Live upstream freshness for the selected VMODs and for Vinyl trunk.

Maintainer decision (f), 2026-07-30: the freshness signal must come from a LIVE
check of each VMOD's own repository, not from the survey JSON. The survey is a
point-in-time sweep; it is out of date the moment it is written, and a freshness
check reading it would report the state of the world on the day of the sweep.

Three questions, per VMOD, and one more about the engine:

  (a) does the pinned tag still peel to the recorded commit?
      A moved tag is a LOUD FAILURE -- nonzero exit, labelled as such -- and
      never a re-pin candidate. It means the recorded identity and the thing
      upstream publishes under that name have diverged, which has to be
      established before anything is built from it. This is the same check
      scripts/ci/vmod/source.sh runs inside the lane; running it early and
      cheaply is the point of doing it here too.

  (b) are there tags sorting above the pinned one?
      Those are RE-PIN CANDIDATES, surfaced to the maintainer and never acted
      on. Moving a pin resets evidence, so it is a deliberate act. Computed
      statelessly against the manifest pin, so the state file never grows a
      "tags I have already mentioned" list that would go stale or lie.

  (c) has a watched trunk branch moved since the last run?
      cachetag's `main` today. This is the change-gate signal: a VMOD whose own
      upstream moved runs against Vinyl trunk even when Vinyl trunk itself did
      not.

  Plus Vinyl trunk HEAD, the other half of the gate.

Stdlib only, as AGENTS.md requires, and no HTTP at all: `git ls-remote` through
subprocess is the whole network surface. git is present on the host and on every
runner, it needs no install step, and registry/vmods/dict.yml already documents
ls-remote as its own verification mechanism -- "cheaper than a full clone, needs
no host-specific action, and checks the same thing". A GitHub API client would
add an auth surface and a rate limit to answer a question git already answers.

Usage:

    python3 tools/upstream_watch.py check [--state FILE] [--format github|json|text]
                                          [--vinyl-url URL] [--transcript FILE]
    python3 tools/upstream_watch.py selftest

The state file lives on an orphan branch `ci-state/trunk-watch`, created at the
first live run of the Wave 3c workflow -- an orphan branch because this is CI
bookkeeping and has no business in the history of the packaging tree. It is NOT
created here, and this tool never writes to a branch: it reads a state file and
writes a state file, and the workflow owns where those live.

Fail-open, deliberately: a missing or unparseable state file means everything is
treated as changed and the gate says run. The failure mode of a freshness gate
is skipping work that should have run, and that failure is silent; running work
that did not need to run costs runner minutes and says so in the output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_matrix  # noqa: E402

STATE_SCHEMA = "upstream-watch-state/v1"
TRANSCRIPT_SCHEMA = "upstream-watch-transcript/v1"

# Vinyl trunk, as trunk-vmod-ci.yml clones it today. Overridable with
# --vinyl-url so a fork or a mirror can be watched without editing the tool.
VINYL_TRUNK_URL = "https://code.vinyl-cache.org/vinyl-cache/vinyl-cache.git"

# The key the engine's own trunk HEAD is watched under. VMOD keys are
# "<vmod-id>/<channel>", which cannot collide with it: a VMOD id may not contain
# a slash and this has no channel.
VINYL_KEY = "vinyl-trunk"

# A tag carrying any of these is never a re-pin candidate, however it sorts.
# Upstream pre-releases version above the release they precede far more often
# than they version below it, and surfacing one as "newer than the pin" would
# train the reader to ignore the notice.
PRERELEASE_MARKERS = ("rc", "alpha", "beta", "pre", "dev", "snapshot", "nightly")

LS_REMOTE_TIMEOUT = 60


class WatchError(Exception):
    """A problem with the invocation or with a remote, not with a pin."""


# ---------------------------------------------------------------------------
# Reading remotes
# ---------------------------------------------------------------------------


def ls_remote(url: str, transcript: dict = None) -> dict:
    """Every ref a remote publishes: {refname: sha}.

    The full listing rather than `--refs`, because `--refs` strips the `^{}`
    peeled entries and those are exactly what answers question (a) for an
    annotated tag.
    """
    if transcript is not None:
        if url not in transcript:
            raise WatchError(f"the transcript has no canned listing for {url}")
        return parse_ls_remote(transcript[url])
    env = dict(os.environ)
    # No credential prompt, ever. A private or renamed repository must fail in
    # sixty seconds with a message, not hang a CI job waiting for a password
    # nobody is there to type.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_ASKPASS", "true")
    try:
        proc = subprocess.run(
            ["git", "ls-remote", url],
            capture_output=True,
            text=True,
            timeout=LS_REMOTE_TIMEOUT,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        raise WatchError("git is not on PATH; upstream_watch needs it to read a remote") from None
    except subprocess.TimeoutExpired:
        raise WatchError(f"git ls-remote {url} timed out after {LS_REMOTE_TIMEOUT}s") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise WatchError(
            f"git ls-remote {url} failed (exit {proc.returncode}): "
            + (detail[-1] if detail else "no output")
        )
    return parse_ls_remote(proc.stdout)


def parse_ls_remote(text: str) -> dict:
    """`<sha>\\t<refname>` lines, in order, into a mapping."""
    refs = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            continue
        refs[parts[1]] = parts[0]
    return refs


def peeled(refs: dict, tag: str) -> str:
    """The commit a tag names, following an annotated tag's peeled entry.

    Same fallback as the lane's own check: `^{}` when the tag object publishes
    one, the tag ref itself when it is lightweight. Either way the value is the
    commit the tag names today.
    """
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}", "")


# ---------------------------------------------------------------------------
# Version ordering
# ---------------------------------------------------------------------------


def version_key(tag: str):
    """The numeric components of a tag, for ordering. A heuristic, on purpose.

    Upstream tag shapes in the current fleet are `v1.0.1`, `v1.7` and
    `9.0-23.1`, and no single scheme covers them: redis tags
    <varnish-series>-<vmod-version>. Taking every numeric run in order handles
    all three and orders them the way a reader would.

    It is a heuristic and it is allowed to be, because what it produces is a
    NOTICE for a human, never a pin. A pin only ever moves through a deliberate
    re-pin with its own evidence reset.
    """
    return tuple(int(part) for part in re.findall(r"\d+", tag))


def is_prerelease(tag: str) -> bool:
    lowered = tag.lower()
    return any(marker in lowered for marker in PRERELEASE_MARKERS)


def newer_tags(refs: dict, pinned: str) -> list:
    """Tags sorting above the pinned one, newest first. Re-pin candidates."""
    base = version_key(pinned)
    if not base:
        # A pin with no numeric component at all cannot be ordered against
        # anything. Reported as no candidates rather than as every tag.
        return []
    found = []
    for name in refs:
        if not name.startswith("refs/tags/") or name.endswith("^{}"):
            continue
        tag = name[len("refs/tags/") :]
        if tag == pinned or is_prerelease(tag):
            continue
        key = version_key(tag)
        # Same shape as the pin, or the comparison is meaningless: a `v2`
        # against a `9.0-23.1` orders on one number against four.
        if len(key) != len(base) or key <= base:
            continue
        found.append(tag)
    return sorted(found, key=version_key, reverse=True)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def empty_state() -> dict:
    return {
        "schema": STATE_SCHEMA,
        "refs": {},
        # Reserved for Wave 3c: the run that produced the trunk engine artifacts
        # the next gated run may reuse instead of rebuilding. Written by the
        # workflow, never by this tool.
        "trunk_engine_run_id": "",
    }


def load_state(path) -> tuple:
    """(state, note). A missing or broken file is not an error -- see the module docstring."""
    if path is None:
        return empty_state(), "no --state given; every watched ref counts as changed"
    path = Path(path)
    if not path.is_file():
        return empty_state(), f"no state file at {path}; every watched ref counts as changed"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return empty_state(), f"state file at {path} is unreadable ({exc}); treating all as changed"
    if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
        return empty_state(), f"state file at {path} is not {STATE_SCHEMA}; treating all as changed"
    refs = data.get("refs")
    if not isinstance(refs, dict):
        return empty_state(), f"state file at {path} has no usable refs map; treating all as changed"
    state = empty_state()
    state["refs"] = {k: v for k, v in refs.items() if isinstance(v, dict)}
    state["trunk_engine_run_id"] = data.get("trunk_engine_run_id", "") or ""
    return state, ""


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def watch_targets(repo_root=None) -> list:
    """What to watch, derived from the catalog. [{key, kind, vmod, url, ...}]

    Clone URLs come from ci_matrix.source_facts, which already owns the
    github-repository-to-URL derivation. A second copy here would be a second
    thing that can disagree with what the lane actually clones.
    """
    targets = [
        {"key": VINYL_KEY, "kind": "branch", "vmod": "", "ref": "HEAD", "url": None}
    ]
    datas, broken = ci_matrix.valid_manifests("ci", repo_root)
    for entry, data in datas:
        for channel, source in sorted(data["sources"].items()):
            facts = ci_matrix.source_facts(data, channel)
            url = facts["VMOD_CLONE_URL"]
            if source.get("expected_commit"):
                targets.append(
                    {
                        "key": f"{entry['id']}/{channel}",
                        "kind": "tag",
                        "vmod": entry["id"],
                        "channel": channel,
                        "ref": source["ref"],
                        "expected_commit": source["expected_commit"],
                        "url": url,
                    }
                )
            else:
                # No recorded commit means a moving ref: a branch, watched for
                # movement rather than checked against a pin.
                targets.append(
                    {
                        "key": f"{entry['id']}/{channel}",
                        "kind": "branch",
                        "vmod": entry["id"],
                        "channel": channel,
                        "ref": source["ref"],
                        "url": url,
                    }
                )
    return targets, [row["vmod"] for row in broken]


def check(
    state_path=None,
    vinyl_url: str = VINYL_TRUNK_URL,
    transcript: dict = None,
    repo_root=None,
) -> dict:
    """The whole report. Never raises for a moved pin; records it and moves on."""
    state, state_note = load_state(state_path)
    stateless = bool(state_note)
    targets, broken = watch_targets(repo_root)

    listings: dict = {}
    entries = []
    moved_pins = []
    changed_vmods = []
    repin_candidates = []
    vinyl_changed = False
    vinyl_head = ""

    for target in targets:
        url = target["url"] or vinyl_url
        entry = {
            "key": target["key"],
            "kind": target["kind"],
            "vmod": target["vmod"],
            "ref": target["ref"],
            "url": url,
            "status": "ok",
            "sha": "",
            "previous_sha": state["refs"].get(target["key"], {}).get("sha", ""),
            "changed": False,
            "detail": "",
            "repin_candidates": [],
        }
        if url not in listings:
            try:
                listings[url] = ls_remote(url, transcript)
            except WatchError as exc:
                listings[url] = None
                entry["status"] = "unreachable"
                entry["detail"] = str(exc)
        refs = listings[url]
        if refs is None:
            if entry["status"] == "ok":
                entry["status"] = "unreachable"
                entry["detail"] = f"{url} could not be read earlier in this run"
            # An unreachable remote counts as changed, for the same reason a
            # missing state file does: the gate must not skip work because it
            # could not find out whether the work was needed.
            entry["changed"] = True
            if target["vmod"]:
                changed_vmods.append(target["vmod"])
            else:
                vinyl_changed = True
            entries.append(entry)
            continue

        if target["kind"] == "tag":
            sha = peeled(refs, target["ref"])
            entry["sha"] = sha
            if not sha:
                entry["status"] = "missing_tag"
                entry["detail"] = f"tag {target['ref']} does not exist at {url}"
                moved_pins.append(entry)
            elif sha != target["expected_commit"]:
                entry["status"] = "moved_pin"
                entry["detail"] = (
                    f"tag {target['ref']} now resolves to {sha}, not the recorded "
                    f"{target['expected_commit']}"
                )
                moved_pins.append(entry)
            else:
                candidates = newer_tags(refs, target["ref"])
                entry["repin_candidates"] = candidates
                for tag in candidates:
                    repin_candidates.append(
                        {"vmod": target["vmod"], "pinned": target["ref"], "tag": tag}
                    )
        else:
            ref_name = "HEAD" if target["ref"] == "HEAD" else f"refs/heads/{target['ref']}"
            sha = refs.get(ref_name, "")
            entry["sha"] = sha
            if not sha:
                entry["status"] = "missing_branch"
                entry["detail"] = f"{ref_name} does not exist at {url}"
                entry["changed"] = True
            else:
                entry["changed"] = stateless or sha != entry["previous_sha"]
            if entry["changed"]:
                if target["vmod"]:
                    changed_vmods.append(target["vmod"])
                else:
                    vinyl_changed = True
            if target["key"] == VINYL_KEY:
                vinyl_head = sha
        entries.append(entry)

    changed_vmods = sorted(set(changed_vmods))
    report = {
        "schema": "upstream-watch-report/v1",
        "state_note": state_note,
        "stateless": stateless,
        "broken_manifests": broken,
        "entries": entries,
        "moved_pins": [e["key"] for e in moved_pins],
        "repin_candidates": repin_candidates,
        "vinyl_changed": vinyl_changed,
        "vinyl_head_sha": vinyl_head,
        "changed_vmods": changed_vmods,
        # A broken manifest is not a reason to skip: whatever is wrong with it,
        # the rest of the fleet still needs its answer, and the run reports the
        # manifest failure through the ordinary invocation row.
        "run": bool(vinyl_changed or changed_vmods or stateless or broken),
        "ok": not moved_pins,
    }
    return report


def next_state(report: dict, previous: dict = None) -> dict:
    """The state file to write after a run. Only reachable refs are updated."""
    state = empty_state()
    if previous:
        state["refs"] = dict(previous.get("refs", {}))
        state["trunk_engine_run_id"] = previous.get("trunk_engine_run_id", "")
    for entry in report["entries"]:
        if entry["sha"]:
            state["refs"][entry["key"]] = {
                "url": entry["url"],
                "ref": entry["ref"],
                "sha": entry["sha"],
            }
    return state


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _gate_name(key: str) -> str:
    """A GitHub output name from a watch key: `dict/release` -> `sha_dict_release`."""
    return "sha_" + re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def render_text(report: dict) -> str:
    lines = ["===== upstream freshness ====="]
    if report["state_note"]:
        lines.append(f"NOTE: {report['state_note']}")
    for entry in report["entries"]:
        mark = {
            "ok": "ok",
            "moved_pin": "MOVED PIN",
            "missing_tag": "MISSING TAG",
            "missing_branch": "MISSING BRANCH",
            "unreachable": "UNREACHABLE",
        }[entry["status"]]
        change = ""
        if entry["kind"] == "branch":
            change = " CHANGED" if entry["changed"] else " unchanged"
        lines.append(
            f"{entry['key']:<22} {entry['kind']:<6} {entry['ref']:<12} "
            f"{entry['sha'] or '-':<40} {mark}{change}"
        )
        if entry["detail"]:
            lines.append(f"  {entry['detail']}")
        if entry["kind"] == "branch" and entry["previous_sha"] and entry["changed"]:
            lines.append(f"  previously {entry['previous_sha']}")
        for tag in entry["repin_candidates"]:
            lines.append(f"  re-pin candidate: {tag} (pinned: {entry['ref']})")
    lines.append("")
    lines.append(f"vinyl trunk changed : {str(report['vinyl_changed']).lower()}")
    lines.append(f"vinyl trunk HEAD    : {report['vinyl_head_sha'] or '-'}")
    lines.append(f"changed VMODs       : {' '.join(report['changed_vmods']) or '(none)'}")
    lines.append(
        "re-pin candidates   : "
        + (
            " ".join(f"{c['vmod']}:{c['tag']}" for c in report["repin_candidates"])
            or "(none)"
        )
    )
    lines.append(f"run                 : {str(report['run']).lower()}")
    if not report["ok"]:
        lines.append("")
        lines.append(
            "FAILURE: a pinned tag no longer names its recorded commit ("
            + ", ".join(report["moved_pins"])
            + "). This is NOT a re-pin candidate. Establish what moved before"
            " anything is built from it; do not update the manifest to make this pass."
        )
    return "\n".join(lines)


def render_github(report: dict) -> str:
    lines = [
        f"run={str(report['run']).lower()}",
        f"vinyl_changed={str(report['vinyl_changed']).lower()}",
        f"vinyl_head_sha={report['vinyl_head_sha']}",
        f"changed_vmods={' '.join(report['changed_vmods'])}",
        f"moved_pins={' '.join(report['moved_pins'])}",
    ]
    for entry in report["entries"]:
        lines.append(f"{_gate_name(entry['key'])}={entry['sha']}")
    # Annotations go to the log, not to $GITHUB_OUTPUT; a caller redirects the
    # key=value lines and lets these through, or reads both from the step log.
    if report["state_note"]:
        lines.append(f"::notice title=upstream-watch::{report['state_note']}")
    for candidate in report["repin_candidates"]:
        lines.append(
            f"::notice title=re-pin candidate::{candidate['vmod']} publishes "
            f"{candidate['tag']}, above the pinned {candidate['pinned']}. "
            "Surfaced only; a pin moves deliberately."
        )
    for entry in report["entries"]:
        if entry["status"] == "unreachable":
            lines.append(f"::warning title=upstream unreachable::{entry['key']}: {entry['detail']}")
        elif entry["status"] in ("moved_pin", "missing_tag"):
            lines.append(f"::error title=moved pin::{entry['key']}: {entry['detail']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def load_transcript(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != TRANSCRIPT_SCHEMA:
        raise WatchError(f"{path}: not a {TRANSCRIPT_SCHEMA} transcript")
    remotes = data.get("remotes")
    if not isinstance(remotes, dict):
        raise WatchError(f"{path}: the transcript has no remotes map")
    return remotes


def cmd_check(args) -> int:
    transcript = load_transcript(args.transcript) if args.transcript else None
    report = check(
        state_path=args.state,
        vinyl_url=args.vinyl_url,
        transcript=transcript,
        repo_root=args.repo_root,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "github":
        print(render_github(report))
    else:
        print(render_text(report))
    if args.write_state:
        previous, _ = load_state(args.state)
        Path(args.write_state).write_text(
            json.dumps(next_state(report, previous), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0 if report["ok"] else 1


def cmd_selftest(args) -> int:
    import upstream_watch_selftest

    return upstream_watch_selftest.main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_check = subparsers.add_parser("check", help="read every watched remote and report")
    p_check.add_argument("--state", help="the last-seen state file; missing is fine")
    p_check.add_argument("--write-state", help="write the updated state file here")
    p_check.add_argument("--vinyl-url", default=VINYL_TRUNK_URL)
    p_check.add_argument("--transcript", help="canned ls-remote output; no network is touched")
    p_check.add_argument("--repo-root", help="the checkout to read registry/vmods/ from")
    p_check.add_argument("--format", default="text", choices=["text", "json", "github"])
    p_check.set_defaults(func=cmd_check)

    p_self = subparsers.add_parser("selftest", help="run the tests")
    p_self.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except WatchError as exc:
        print(f"E: {exc}", file=sys.stderr)
        return 2
    except ci_matrix.CatalogError as exc:
        print(f"E: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
