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
      cachetag's `main`, dict's `master` and redis's `main` today. This is the
      change-gate signal: a VMOD whose own upstream moved runs against Vinyl
      trunk even when Vinyl trunk itself did not.

  (d) has a tag name ever been observed peeling to a different commit than
      first recorded? Such a tag is POISONED: it gets permanent moved-tag
      treatment -- loud failure, never a candidate -- even if it later returns
      to the original value. A re-tagged beta that later looks like a stable
      release is exactly the thing this refuses to trust. The memory lives in
      the state file's `tags` map; a deliberate re-pin (the recorded commit
      changing) is the one thing that resets it.

  Plus the engine itself, twice: Vinyl trunk HEAD, the other half of the gate,
  and the pinned Vinyl release tag, which gets exactly questions (a) and (b) --
  a moved release tag is the same loud failure a moved VMOD pin is, and tags
  sorting above it are surfaced as re-pin candidates through the same output.

  Plus the watch-only FLEET: the active third-party VMOD upstreams from the
  compatibility survey, materialized in registry/fleet-watch.json. Fleet rows
  have deliberately weaker semantics than everything above: no pin, so no
  moved-pin rule; never a gate signal, a CI row, or a build; a new stable tag
  since the last recorded state surfaces as an informational packaging
  candidate, and the first observation of an upstream seeds the state silently
  rather than announcing its entire tag history. Policy record:
  docs/20260730_1635_note_publication-authority-decision.md.

Stdlib only, as AGENTS.md requires, and no HTTP at all: `git ls-remote` through
subprocess is the whole network surface. git is present on the host and on every
runner, it needs no install step, and registry/vmods/dict.yml already documents
ls-remote as its own verification mechanism -- "cheaper than a full clone, needs
no host-specific action, and checks the same thing". A GitHub API client would
add an auth surface and a rate limit to answer a question git already answers.

Usage:

    python3 tools/upstream_watch.py check [--state FILE] [--format github|json|text|issues]
                                          [--vinyl-url URL] [--transcript FILE]
                                          [--fleet FILE] [--issues FILE]
                                          [--report FILE]
    python3 tools/upstream_watch.py selftest

`--issues FILE` (or `--format issues`) emits the GitHub issues the workflow's
notify job should ensure exist: one issue per distinct upstream+tag for the
pinned rows, one rolling digest for fleet candidates. Generating the bodies
here keeps the notification content selftested; the workflow only creates.

`--report FILE` writes the whole report as JSON alongside whatever `--format`
prints, so one observation can feed several consumers. The prepare-repin job
reads it through tools/repin_prepare.py, which needs each candidate tag's
peeled commit -- a fact the gate strings do not carry. Running the watcher
twice to get it would be two observations of a world that can move between
them.

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

# The engine's pinned release tag, watched with the same questions (a) and (b)
# a VMOD pin gets: a moved tag is a loud failure, tags sorting above it are
# re-pin candidates. The pin lives in no ci_matrix manifest, so it cannot come
# from watch_targets' catalog walk; the recorded identity is the release block
# of recipes/debian-13/pins.env and recipes/el9/cohort.env (VINYL_GIT_COMMIT,
# the tag is named in its comment) and registry/cohorts/vinyl-9.0.1-*.yml's
# source.git_commit. This copy MUST move together with those on a Vinyl
# re-pin. The selftest transcript pins the same tag and commit, so a lone edit
# in any one place fails loudly rather than sliding past.
VINYL_RELEASE_KEY = "vinyl-release"
VINYL_RELEASE_TAG = "vinyl-cache-9.0.1"
VINYL_RELEASE_COMMIT = "423648c4cb6b225b3268ffc337354ea938f5efee"

# A tag carrying any of these is never a re-pin candidate, however it sorts.
# Upstream pre-releases version above the release they precede far more often
# than they version below it, and surfacing one as "newer than the pin" would
# train the reader to ignore the notice.
PRERELEASE_MARKERS = ("rc", "alpha", "beta", "pre", "dev", "snapshot", "nightly")

# Per-row stable-version grammar overrides for the PINNED rows, keyed by watch
# key ("dict/release", VINYL_RELEASE_KEY, ...). A row absent here derives its
# grammar from the pinned tag's own shape via stable_tag_re(), which covers all
# four current pins (v1.0.1, v1.7, 9.0-23.1, vinyl-cache-9.0.1); the table
# exists so an upstream whose scheme the derivation gets wrong can be declared
# explicitly, next to the engine pin constants it belongs with. Moving the
# declaration into the registry manifests waits on the machine-readable pin
# home (release-automation plan section 2.3, now friction-reduction work).
STABLE_TAG_GRAMMARS: dict = {}

# The stable grammar for FLEET rows, which have no pin to derive one from: any
# non-digit prefix, then dotted/dashed numerics to the end. is_prerelease()
# applies on top, so "nightly-20260730" and "v2.0-rc1" are both refused. A
# roster row may declare its own `stable_tag_re` instead.
FLEET_STABLE_RE = r"\D*?\d+(?:[.\-]\d+)*"

# The watch-only fleet roster: a reviewable, maintainer-editable file, NOT
# derived at runtime from the survey. Missing file means no fleet rows -- the
# roster is an input, not a requirement.
FLEET_ROSTER_SCHEMA = "fleet-watch-roster/v1"
FLEET_ROSTER_PATH = "registry/fleet-watch.json"
FLEET_KEY_PREFIX = "fleet/"

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


def stable_tag_re(pinned: str) -> str:
    """The stable-version grammar derived from a pinned tag's own shape.

    The pin's non-digit prefix (`v`, `vinyl-cache-`, or nothing), then numeric
    components separated by `.` or `-`, and nothing after them. A tag with any
    suffix segment the pinned scheme has never used -- `-rc1`, `beta2`, `.dev0`
    -- fails the fullmatch and is never a re-pin candidate. Overridable per row
    through STABLE_TAG_GRAMMARS.
    """
    prefix = re.match(r"\D*", pinned).group(0)
    return re.escape(prefix) + r"\d+(?:[.\-]\d+)*"


def _padded(key: tuple, width: int) -> tuple:
    return key + (0,) * (width - len(key))


def sorts_above(candidate: tuple, base: tuple) -> bool:
    """Numeric comparison across differing component counts.

    Missing components count as zero, so `vinyl-cache-9.1` (9, 1) orders above
    `vinyl-cache-9.0.1` (9, 0, 1) instead of being silently incomparable. A
    twice-yearly major release is exactly the tag shape the old same-length
    rule failed to surface.
    """
    width = max(len(candidate), len(base))
    return _padded(candidate, width) > _padded(base, width)


def _tag_order(tag: str):
    """A sort key that agrees with sorts_above across shapes."""
    return _padded(version_key(tag)[:12], 12)


def tag_names(refs: dict) -> list:
    """Every tag name a listing publishes, peeled entries folded away."""
    return [
        name[len("refs/tags/") :]
        for name in refs
        if name.startswith("refs/tags/") and not name.endswith("^{}")
    ]


def stable_tags(refs: dict, pattern: str) -> list:
    """The tags that parse as stable versions under the given grammar."""
    return sorted(
        (t for t in tag_names(refs) if re.fullmatch(pattern, t) and not is_prerelease(t)),
        key=_tag_order,
    )


def newer_tags(refs: dict, pinned: str, stable_re: str = None) -> tuple:
    """(candidates, informational): tags sorting above the pin, newest first.

    Candidates parse as a *stable* version in the pinned tag's own scheme (or
    the row's declared grammar) -- these are the re-pin candidates, surfaced to
    the maintainer and never acted on. Informational tags share the pin's
    family (prefix then a digit) and sort above it but are not stable-shaped:
    pre-releases, betas, oddly suffixed tags. They are surfaced separately so
    they can never pollute the candidate list.
    """
    base = version_key(pinned)
    if not base:
        # A pin with no numeric component at all cannot be ordered against
        # anything. Reported as no candidates rather than as every tag.
        return [], []
    pattern = stable_re or stable_tag_re(pinned)
    family = re.escape(re.match(r"\D*", pinned).group(0)) + r"\d"
    candidates = []
    informational = []
    for tag in tag_names(refs):
        if tag == pinned:
            continue
        key = version_key(tag)
        if not key or not sorts_above(key, base):
            continue
        if re.fullmatch(pattern, tag) and not is_prerelease(tag):
            candidates.append(tag)
        elif re.match(family, tag):
            informational.append(tag)
    return (
        sorted(candidates, key=_tag_order, reverse=True),
        sorted(informational, key=_tag_order, reverse=True),
    )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def empty_state() -> dict:
    return {
        "schema": STATE_SCHEMA,
        "refs": {},
        # Tag memory, additive to schema v1 so pre-existing state files load
        # unchanged and simply have none: {watch key: {tag name: {"sha":
        # first-seen commit, "poisoned": true when ever seen elsewhere}}}.
        # For a pinned row the pinned tag's first-seen is the manifest's
        # recorded commit, so a deliberate re-pin resets the memory; for every
        # other tag the first live observation is the record. Fleet rows use
        # the same map: the presence of a key marks that upstream as seeded.
        "tags": {},
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
    tags = data.get("tags")
    if isinstance(tags, dict):
        # A state file predating the tags map simply has no memory yet; that
        # must never look like anything having moved.
        state["tags"] = {
            key: {t: dict(rec) for t, rec in val.items() if isinstance(rec, dict)}
            for key, val in tags.items()
            if isinstance(val, dict)
        }
    state["trunk_engine_run_id"] = data.get("trunk_engine_run_id", "") or ""
    return state, ""


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def load_fleet(repo_root=None, fleet_path=None) -> list:
    """The enabled fleet roster rows: [{id, url, stable_tag_re}].

    A missing roster at the DEFAULT location is no fleet -- the roster is an
    input, not a requirement, and the tool predates it. An explicitly given
    path that is missing or malformed is a configuration error and raises: a
    maintainer-edited roster with a typo must fail loudly, not silently drop
    forty upstreams from the watch.
    """
    if fleet_path is None:
        root = Path(repo_root) if repo_root else ci_matrix.REPO_ROOT
        path = root / FLEET_ROSTER_PATH
        if not path.is_file():
            return []
    else:
        path = Path(fleet_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WatchError(f"fleet roster {path}: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != FLEET_ROSTER_SCHEMA:
        raise WatchError(f"fleet roster {path}: not a {FLEET_ROSTER_SCHEMA} document")
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise WatchError(f"fleet roster {path}: no rows list")
    fleet = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("watch"):
            continue
        rid = row.get("id")
        url = row.get("url")
        if not rid or not url or rid in seen:
            raise WatchError(
                f"fleet roster {path}: every watched row needs a unique id and a url"
            )
        seen.add(rid)
        fleet.append(
            {
                "id": rid,
                "url": url,
                "stable_tag_re": row.get("stable_tag_re") or FLEET_STABLE_RE,
            }
        )
    return fleet


def watch_targets(repo_root=None, fleet_path=None) -> list:
    """What to watch, derived from the catalog. [{key, kind, vmod, url, ...}]

    Clone URLs come from ci_matrix.source_facts, which already owns the
    github-repository-to-URL derivation. A second copy here would be a second
    thing that can disagree with what the lane actually clones.
    """
    targets = [
        {"key": VINYL_KEY, "kind": "branch", "vmod": "", "ref": "HEAD", "url": None},
        # The engine's release pin, against the same remote (url None resolves
        # to --vinyl-url), so both engine rows read one listing.
        {
            "key": VINYL_RELEASE_KEY,
            "kind": "tag",
            "vmod": "",
            "ref": VINYL_RELEASE_TAG,
            "expected_commit": VINYL_RELEASE_COMMIT,
            "url": None,
        },
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
    # The watch-only fleet, last: it can never collide with the rows above
    # (its keys carry the fleet/ prefix) and nothing about it feeds the gate.
    for row in load_fleet(repo_root, fleet_path):
        targets.append(
            {
                "key": FLEET_KEY_PREFIX + row["id"],
                "kind": "fleet",
                "vmod": "",
                "ref": "(stable tags)",
                "url": row["url"],
                "stable_tag_re": row["stable_tag_re"],
            }
        )
    return targets, [row["vmod"] for row in broken]


def check(
    state_path=None,
    vinyl_url: str = VINYL_TRUNK_URL,
    transcript: dict = None,
    repo_root=None,
    fleet_path=None,
) -> dict:
    """The whole report. Never raises for a moved pin; records it and moves on."""
    state, state_note = load_state(state_path)
    stateless = bool(state_note)
    targets, broken = watch_targets(repo_root, fleet_path)

    listings: dict = {}
    entries = []
    moved_pins = []
    changed_vmods = []
    repin_candidates = []
    poisoned_tags = []
    fleet_candidates = []
    fleet_seeded = []
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
            "informational_tags": [],
            "poisoned_tags": [],
            "fleet_new_tags": [],
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
            if target["kind"] == "fleet":
                # A fleet row is watch-only: unreachable is a warning in the
                # output, never a reason to run anything. The recorded tag set
                # is left as it was.
                entries.append(entry)
                continue
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

        if target["kind"] == "fleet":
            # Watch-only: no pin, no moved-pin rule, no gate signal. The state
            # records the stable-tag set; what is new since the record is an
            # informational packaging candidate. A key absent from the state is
            # an unseeded upstream: its whole current tag set is recorded
            # silently, because announcing years of history as "new" on the
            # first run would bury the one notice that matters.
            known = state["tags"].get(target["key"])
            observed = {}
            for tag in stable_tags(refs, target["stable_tag_re"]):
                sha = peeled(refs, tag)
                if sha:
                    first = (known or {}).get(tag, {}).get("sha") or sha
                    observed[tag] = {"sha": first}
            entry["tag_observations"] = observed
            if known is None:
                fleet_seeded.append(target["key"])
                entry["detail"] = (
                    f"first observation: {len(observed)} stable tags recorded silently"
                )
            else:
                new = sorted(
                    (t for t in observed if t not in known),
                    key=_tag_order,
                    reverse=True,
                )
                entry["fleet_new_tags"] = new
                for tag in new:
                    fleet_candidates.append(
                        {
                            "id": target["key"][len(FLEET_KEY_PREFIX) :],
                            "key": target["key"],
                            "tag": tag,
                            "url": url,
                        }
                    )
            entries.append(entry)
            continue

        if target["kind"] == "tag":
            grammar = STABLE_TAG_GRAMMARS.get(target["key"]) or stable_tag_re(target["ref"])
            sha = peeled(refs, target["ref"])
            entry["sha"] = sha
            # The poisoned-tag memory (question d): every stable-family tag is
            # tracked against its first-seen commit. The pinned tag's first-seen
            # is the manifest's RECORDED commit -- so a deliberate re-pin, which
            # changes the recorded commit, is the one thing that resets the
            # memory -- and a tag once seen elsewhere stays poisoned even after
            # it returns to the original value.
            known = state["tags"].get(target["key"]) or {}
            observed = {}
            poisoned_here = set()
            for tag in sorted(set(stable_tags(refs, grammar)) | {target["ref"]}):
                tag_sha = peeled(refs, tag)
                if not tag_sha:
                    continue
                rec = known.get(tag) or {}
                if tag == target["ref"]:
                    first = target["expected_commit"]
                    remembered = rec.get("poisoned", False) and rec.get("sha") == first
                else:
                    first = rec.get("sha") or tag_sha
                    remembered = rec.get("poisoned", False)
                poisoned = remembered or tag_sha != first
                observed[tag] = {"sha": first, "poisoned": poisoned}
                if poisoned:
                    poisoned_here.add(tag)
                    poisoned_tags.append(
                        {
                            "key": target["key"],
                            "vmod": target["vmod"] or target["key"],
                            "tag": tag,
                            "first_seen": first,
                            "now": tag_sha,
                        }
                    )
            entry["tag_observations"] = observed
            entry["poisoned_tags"] = sorted(poisoned_here)
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
                candidates, informational = newer_tags(refs, target["ref"], grammar)
                # A poisoned name is never a candidate, however stable it looks.
                candidates = [t for t in candidates if t not in poisoned_here]
                entry["repin_candidates"] = candidates
                entry["informational_tags"] = informational
                for tag in candidates:
                    repin_candidates.append(
                        {
                            # The engine rows have no VMOD; the watch key is
                            # the honest label there ("vinyl-release publishes
                            # ..."), and for a VMOD row the two spell the VMOD.
                            "vmod": target["vmod"] or target["key"],
                            "pinned": target["ref"],
                            "tag": tag,
                        }
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
        "poisoned_tags": poisoned_tags,
        "fleet_candidates": fleet_candidates,
        "fleet_seeded": fleet_seeded,
        "vinyl_changed": vinyl_changed,
        "vinyl_head_sha": vinyl_head,
        "changed_vmods": changed_vmods,
        # A broken manifest is not a reason to skip: whatever is wrong with it,
        # the rest of the packaged rows still need their answer, and the run
        # reports the manifest failure through the ordinary invocation row.
        # Fleet rows contribute NOTHING here: they are watch-only.
        "run": bool(vinyl_changed or changed_vmods or stateless or broken),
        "ok": not moved_pins and not poisoned_tags,
    }
    return report


def next_state(report: dict, previous: dict = None) -> dict:
    """The state file to write after a run. Only reachable refs are updated."""
    state = empty_state()
    if previous:
        state["refs"] = dict(previous.get("refs", {}))
        state["tags"] = {
            key: {t: dict(rec) for t, rec in val.items()}
            for key, val in previous.get("tags", {}).items()
        }
        state["trunk_engine_run_id"] = previous.get("trunk_engine_run_id", "")
    for entry in report["entries"]:
        if entry["sha"]:
            state["refs"][entry["key"]] = {
                "url": entry["url"],
                "ref": entry["ref"],
                "sha": entry["sha"],
            }
        observed = entry.get("tag_observations")
        if observed is None:
            # Nothing was read for this row (unreachable, or a branch row);
            # whatever memory exists is kept rather than erased.
            continue
        # setdefault even when empty: for a fleet row the PRESENCE of the key
        # is what marks the upstream as seeded, so an upstream with no stable
        # tags yet still gets its (empty) record, and its first real tag is
        # announced rather than swallowed by a second silent seeding.
        rec = state["tags"].setdefault(entry["key"], {})
        for tag, info in observed.items():
            item = {"sha": info["sha"]}
            if info.get("poisoned"):
                item["poisoned"] = True
            rec[tag] = item
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
        for tag in entry["informational_tags"]:
            lines.append(f"  informational: {tag} (not stable-shaped; never a candidate)")
        for tag in entry["poisoned_tags"]:
            lines.append(f"  POISONED: {tag} was observed at a different commit; permanently distrusted")
        for tag in entry["fleet_new_tags"]:
            lines.append(f"  new stable tag: {tag}")
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
    lines.append(
        "poisoned tags       : "
        + (" ".join(f"{p['key']}:{p['tag']}" for p in report["poisoned_tags"]) or "(none)")
    )
    lines.append(
        "fleet candidates    : "
        + (" ".join(f"{c['id']}:{c['tag']}" for c in report["fleet_candidates"]) or "(none)")
    )
    if report["fleet_seeded"]:
        lines.append(f"fleet state seeded  : {len(report['fleet_seeded'])} upstreams (first observation, nothing announced)")
    lines.append(f"run                 : {str(report['run']).lower()}")
    if report["moved_pins"]:
        lines.append("")
        lines.append(
            "FAILURE: a pinned tag no longer names its recorded commit ("
            + ", ".join(report["moved_pins"])
            + "). This is NOT a re-pin candidate. Establish what moved before"
            " anything is built from it; do not update the manifest to make this pass."
        )
    if report["poisoned_tags"]:
        lines.append("")
        lines.append(
            "FAILURE: poisoned tag(s) "
            + ", ".join(f"{p['key']}:{p['tag']}" for p in report["poisoned_tags"])
            + ": each was observed peeling to a different commit than first recorded and"
            " is permanently distrusted, even if it has returned to the original value."
            " Only a deliberate re-pin resets this."
        )
    return "\n".join(lines)


def render_github(report: dict) -> str:
    notify = bool(
        report["repin_candidates"]
        or report["moved_pins"]
        or report["poisoned_tags"]
        or report["fleet_candidates"]
    )
    lines = [
        f"run={str(report['run']).lower()}",
        f"vinyl_changed={str(report['vinyl_changed']).lower()}",
        f"vinyl_head_sha={report['vinyl_head_sha']}",
        f"changed_vmods={' '.join(report['changed_vmods'])}",
        f"moved_pins={' '.join(report['moved_pins'])}",
        # The re-pin candidates as a gate output, so the prepare-repin job can
        # decide whether it has anything to do without downloading and parsing
        # the report first. It is a GATE signal only: what gets prepared is
        # decided by tools/repin_prepare.py from the raw report and the
        # registry, never from this string.
        "repin_candidates="
        + " ".join(f"{c['vmod']}:{c['tag']}" for c in report["repin_candidates"]),
        "poisoned_tags=" + " ".join(f"{p['key']}:{p['tag']}" for p in report["poisoned_tags"]),
        "fleet_candidates=" + " ".join(f"{c['id']}:{c['tag']}" for c in report["fleet_candidates"]),
        # The one output the notify job gates on: is there anything a
        # maintainer must be told about? A manual publication gate only works
        # if detection is loud, so this must never sit at false while a
        # candidate hides in a green run's log.
        f"notify={str(notify).lower()}",
    ]
    for entry in report["entries"]:
        # Fleet rows are watch-only and carry no gate sha; forty empty
        # key=value lines in $GITHUB_OUTPUT would be noise.
        if entry["kind"] != "fleet":
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
    for candidate in report["fleet_candidates"]:
        lines.append(
            f"::notice title=fleet candidate::{candidate['id']} publishes "
            f"{candidate['tag']}. Watch-only; packaging a fleet VMOD is a "
            "maintainer decision."
        )
    for entry in report["entries"]:
        if entry["status"] == "unreachable":
            lines.append(f"::warning title=upstream unreachable::{entry['key']}: {entry['detail']}")
        elif entry["status"] in ("moved_pin", "missing_tag"):
            lines.append(f"::error title=moved pin::{entry['key']}: {entry['detail']}")
    for poisoned in report["poisoned_tags"]:
        lines.append(
            f"::error title=poisoned tag::{poisoned['key']}: {poisoned['tag']} was "
            f"first seen at {poisoned['first_seen']} and has been observed at "
            f"{poisoned['now']}; permanently distrusted."
        )
    return "\n".join(lines)


# The dedupe key for notification issues is the TITLE: one issue per distinct
# upstream+tag, an open issue means already reported, and a closed issue means
# seen/handled -- the workflow never reopens one. The fleet digest is the one
# exception: a single rolling issue, commented on while open, recreated only
# when new candidates appear after it was closed.
ISSUE_LABEL = "upstream-watch"
FLEET_DIGEST_TITLE = "upstream-watch: fleet packaging candidates"
_DECISION_NOTE = "docs/20260730_1635_note_publication-authority-decision.md"


def render_issues(report: dict) -> list:
    """The GitHub issues the notify job should ensure exist, as data.

    Generated here rather than in workflow YAML so the notification content --
    the piece that makes a manual publication gate workable -- is covered by
    the selftest battery like every other output.
    """
    issues = []
    moved_pairs = set()
    for entry in report["entries"]:
        if entry["status"] not in ("moved_pin", "missing_tag"):
            continue
        moved_pairs.add((entry["key"], entry["ref"]))
        issues.append(
            {
                "kind": "moved_pin",
                "slug": f"moved-pin {entry['key']} {entry['ref']}",
                "title": f"upstream-watch: moved pin {entry['key']} {entry['ref']}",
                "labels": [ISSUE_LABEL],
                "body": (
                    f"The pinned tag `{entry['ref']}` for `{entry['key']}` no longer names "
                    f"its recorded commit.\n\n{entry['detail']}\n\nRemote: {entry['url']}\n\n"
                    "This is a loud failure, never a re-pin candidate: establish what moved "
                    "before anything is built from it, and do not update the manifest to "
                    f"make the check pass. Policy: `{_DECISION_NOTE}`.\n\n"
                    "Closing this issue records it as seen and handled; the watcher will "
                    "not reopen it."
                ),
            }
        )
    for poisoned in report["poisoned_tags"]:
        # A currently moved pin already has its issue above; the poisoned issue
        # for the same name would say the same thing twice. The permanence
        # shows up when the tag RETURNS: the moved-pin condition clears, this
        # one does not.
        if (poisoned["key"], poisoned["tag"]) in moved_pairs:
            continue
        issues.append(
            {
                "kind": "poisoned_tag",
                "slug": f"poisoned-tag {poisoned['key']} {poisoned['tag']}",
                "title": f"upstream-watch: poisoned tag {poisoned['key']} {poisoned['tag']}",
                "labels": [ISSUE_LABEL],
                "body": (
                    f"Tag `{poisoned['tag']}` at `{poisoned['key']}` was first recorded "
                    f"peeling to `{poisoned['first_seen']}` and has been observed peeling "
                    f"to `{poisoned['now']}`. A tag name ever seen at a different commit "
                    "is permanently distrusted, even if it returns to the original value; "
                    "it will never surface as a re-pin candidate. Only a deliberate re-pin "
                    f"resets the memory. Policy: `{_DECISION_NOTE}`.\n\n"
                    "Closing this issue records it as seen and handled; the watcher will "
                    "not reopen it."
                ),
            }
        )
    for candidate in report["repin_candidates"]:
        issues.append(
            {
                "kind": "repin_candidate",
                "slug": f"repin {candidate['vmod']} {candidate['tag']}",
                "title": f"upstream-watch: re-pin candidate {candidate['vmod']} {candidate['tag']}",
                "labels": [ISSUE_LABEL],
                "body": (
                    f"`{candidate['vmod']}` publishes `{candidate['tag']}`, above the "
                    f"pinned `{candidate['pinned']}`.\n\n"
                    "Surfaced only; a pin moves deliberately, with its own evidence reset. "
                    "Publication authority is a manual gate: automation detects, verifies "
                    f"pin integrity, and notifies -- it never publishes (`{_DECISION_NOTE}`).\n\n"
                    "Closing this issue records the candidate as seen and handled; the "
                    "watcher will not reopen it."
                ),
            }
        )
    if report["fleet_candidates"]:
        grouped: dict = {}
        for candidate in report["fleet_candidates"]:
            grouped.setdefault(candidate["id"], {"url": candidate["url"], "tags": []})[
                "tags"
            ].append(candidate["tag"])
        body = [
            "New stable tags observed across the watch-only fleet roster "
            "(`registry/fleet-watch.json`) since the last recorded state. Informational "
            "packaging candidates only: fleet rows have no pin, gate nothing, and build "
            "nothing. Selecting a VMOD for packaging remains an explicit maintainer "
            "decision recorded in `SCOPE.md`.",
            "",
        ]
        for rid in sorted(grouped):
            body.append(
                f"- **{rid}** ({grouped[rid]['url']}): "
                + ", ".join(f"`{t}`" for t in grouped[rid]["tags"])
            )
        body += [
            "",
            "Each tag is announced exactly once: this digest carries only tags newly "
            "observed by the run that produced it.",
        ]
        issues.append(
            {
                "kind": "fleet_digest",
                "slug": "fleet-digest",
                "title": FLEET_DIGEST_TITLE,
                "labels": [ISSUE_LABEL],
                "body": "\n".join(body),
            }
        )
    return issues


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
        fleet_path=args.fleet,
    )
    if args.report:
        # The whole report, verbatim, as a file. --format json prints the same
        # document, but the workflow's gate step already spends stdout on the
        # key=value gate outputs, and running the watcher a second time to get
        # the structured findings could observe a different world. Consumers
        # that need more than the gate strings -- tools/repin_prepare.py, which
        # needs each candidate's peeled commit -- read this.
        Path(args.report).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.issues:
        Path(args.issues).write_text(
            json.dumps(render_issues(report), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "github":
        print(render_github(report))
    elif args.format == "issues":
        print(json.dumps(render_issues(report), indent=2, sort_keys=True))
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
    p_check.add_argument("--fleet", help="the fleet roster; defaults to registry/fleet-watch.json")
    p_check.add_argument("--issues", help="also write the notification issues as JSON here")
    p_check.add_argument("--report", help="also write the whole report as JSON here")
    p_check.add_argument("--format", default="text", choices=["text", "json", "github", "issues"])
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
