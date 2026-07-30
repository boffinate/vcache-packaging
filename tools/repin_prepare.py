#!/usr/bin/env python3
"""Prepare a re-pin branch for one-click human review. Publishes nothing.

The next link in the detect -> verify -> notify chain the maintainer decided on
2026-07-30 (docs/20260730_1635_note_publication-authority-decision.md): when
tools/upstream_watch.py observes a new stable tag on a pinned row, automation
opens a branch and a pull request carrying the RECORDED RE-PIN -- tag name,
peeled commit, archive digest -- so that the maintainer reviews a diff instead
of reconstructing one. It publishes nothing, merges nothing, and produces no
evidence. The go-ahead is recorded in
docs/20260730_1812_note_auto-prepared-repin-pr.md.

Three rules shape every line below.

  * **Observed is not tested** (release-automation plan section 1.2). An
    observation is a trigger to go and produce evidence, never a substitute for
    it. The rendered pull-request body says so in as many words, and the
    selftest asserts the sentence is there -- because that sentence is the only
    thing standing between "a bot opened a PR" and "a bot claimed a pin works".

  * **Automation records pins; humans record evidence.** `apply` edits source
    identity and nothing else, and refuses outright to write under
    registry/targets/ or registry/cohorts/ or into a transactions expectation
    file. Those carry measured outcomes, and a measurement that was not
    measured is the one thing worse than a missing one.

  * **A refusal is loud and per-candidate.** Nothing is silently skipped. A
    candidate the tool will not prepare is reported with the reason, in the
    job summary and in the eligibility JSON; the other candidates carry on.

Eligibility is machine-readable rather than a hardcoded list of VMOD ids, so a
row becomes eligible or ineligible because of what the registry says about it
and not because of what somebody remembered to update here. Today exactly one
selected row qualifies (`dict`); see `INELIGIBLE_*` below for why each of the
others does not.

Usage:

    python3 tools/repin_prepare.py eligibility --report FILE [--json OUT]
                                   [--tsv OUT] [--summary OUT]
    python3 tools/repin_prepare.py plan --report FILE --vmod ID --tag TAG
                                   [--json OUT] [--env OUT]
    python3 tools/repin_prepare.py apply --plan FILE --commit SHA
                                   --archive-sha256 HEX [--archive-bytes N]
    python3 tools/repin_prepare.py pr-body --plan FILE --commit SHA
                                   --archive-sha256 HEX [--ancestry TEXT]
                                   [--issue URL] [--ci-run URL]
                                   [--title-file OUT] [--body-file OUT]
    python3 tools/repin_prepare.py issue-lookup --issues FILE --title TITLE
    python3 tools/repin_prepare.py selftest

Stdlib only, as AGENTS.md requires: the registry tooling must run on the host
and inside any buildroot with no install step.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_matrix  # noqa: E402
import upstream_watch  # noqa: E402
import yaml_subset  # noqa: E402

ELIGIBILITY_SCHEMA = "repin-prepare-eligibility/v1"
PLAN_SCHEMA = "repin-prepare-plan/v1"
REPORT_SCHEMA = "upstream-watch-report/v1"

BRANCH_PREFIX = "auto-release/"

# The one sentence this whole increment exists to keep honest. Release-
# automation plan section 1.2: an observation is a trigger to produce evidence,
# never a verdict. The selftest asserts it survives into every rendered body.
OBSERVED_NOT_TESTED = (
    "**Observed, not tested.** Nothing here has been built, installed, or "
    "verified. The watcher read a tag name off a remote; that is an "
    "observation, and an observation is a trigger to go and produce evidence, "
    "never a substitute for it. CI evidence for this pin is PENDING."
)

_DECISION_NOTE = "docs/20260730_1635_note_publication-authority-decision.md"
_PREPARE_NOTE = "docs/20260730_1812_note_auto-prepared-repin-pr.md"
_PLAN_DOC = "docs/20260730_1414_plan_release-automation.md"

# Paths `apply` refuses to write, whatever a plan asks for. registry/targets/
# and registry/cohorts/ carry recorded evidence and the cohort identity derived
# from it; recipes/*/transactions/expected/ carries the pinned per-scenario
# outcomes of a measurement. All three move with the release evidence, by a
# human, from a run that actually measured something.
FORBIDDEN_EDIT_PREFIXES = (
    "registry/targets/",
    "registry/cohorts/",
    "registry/distro-native/",
)
FORBIDDEN_EDIT_SUBSTRINGS = ("/transactions/expected/",)

# Watch keys that are not VMOD rows. The engine's release pin has no single
# machine-readable home yet -- it lives in two lane pin files, a cohort
# manifest, and this repository's watcher constants (release-automation plan
# section 2.3) -- so no automated engine re-pin is prepared before that home
# exists. That work is not part of this increment.
ENGINE_KEYS = (upstream_watch.VINYL_RELEASE_KEY, upstream_watch.VINYL_KEY)

INELIGIBLE_ENGINE = (
    "engine row: the Vinyl release pin has no single machine-readable home "
    "(it spans two lane pin files, the cohort manifest, and the watcher's own "
    "constants), so no engine re-pin is prepared automatically. "
    "Release-automation plan section 2.3."
)
INELIGIBLE_PATCHED = (
    "patched overlay: this VMOD carries a reviewed source patch, and a patch is "
    "pinned by digest against ONE upstream tag. Moving the tag obliges a human "
    "to re-derive the patch, re-read it against the new tree, and record a new "
    "reviewed_against -- an obligation SCOPE.md places on a person and "
    "vmod_recipe.py refuses to skip. Prepare this re-pin by hand."
)
INELIGIBLE_DERIVED_ARCHIVE = (
    "derived archive -- prepare manually: the manifest records no archive_url, "
    "so the archive is derived from the tag rather than downloaded. Deriving "
    "it and pinning the new digest is deliberate work with its own "
    "reproducibility record."
)
INELIGIBLE_COHORT_COUPLED = (
    "cohort-coupled pin: a cohort manifest records this VMOD's version, and "
    "release_tool.py validate cross-checks that value against the recorded "
    "upstream_version in the cohort's target evidence. The pin and the evidence "
    "move together, and this automation records pins, never evidence."
)


class PrepareError(Exception):
    """A refusal or a malformed input. Always carries the reason."""


# ---------------------------------------------------------------------------
# Reading the watcher's raw report
# ---------------------------------------------------------------------------


def load_report(path) -> dict:
    """The raw `upstream-watch-report/v1` document the gate wrote."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PrepareError(f"{path}: unreadable watch report ({exc})") from None
    if not isinstance(data, dict) or data.get("schema") != REPORT_SCHEMA:
        raise PrepareError(f"{path}: not a {REPORT_SCHEMA} document")
    if not isinstance(data.get("repin_candidates"), list):
        raise PrepareError(f"{path}: the report has no repin_candidates list")
    if not isinstance(data.get("entries"), list):
        raise PrepareError(f"{path}: the report has no entries list")
    return data


def observed_commit(report: dict, vmod: str, tag: str) -> str:
    """The commit the watcher saw the candidate tag peel to, from the same read.

    The workflow re-peels the tag before acting and refuses a disagreement; this
    value is what it compares against, so that a tag moving between the
    observation and the preparation is a loud stop rather than a quiet
    substitution.
    """
    for entry in report["entries"]:
        if entry.get("kind") != "tag":
            continue
        if (entry.get("vmod") or entry.get("key")) != vmod:
            continue
        if tag not in (entry.get("repin_candidates") or []):
            continue
        record = (entry.get("tag_observations") or {}).get(tag) or {}
        sha = record.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
            raise PrepareError(
                f"{vmod} {tag}: the report records no usable peeled commit for the "
                "candidate tag; the observation is unusable and nothing is prepared"
            )
        return sha
    raise PrepareError(
        f"{vmod} {tag}: no watch entry in the report claims this candidate; the "
        "report and the candidate list disagree"
    )


def entry_for(report: dict, vmod: str) -> dict:
    for entry in report["entries"]:
        if entry.get("kind") == "tag" and (entry.get("vmod") or entry.get("key")) == vmod:
            return entry
    raise PrepareError(f"{vmod}: no tag entry in the watch report")


def issue_title(vmod: str, tag: str) -> str:
    """The notify job's issue title for this candidate.

    Rendered through upstream_watch.render_issues rather than re-spelled here,
    so the two can never drift into looking up an issue that was filed under a
    different name.
    """
    synthetic = {
        "schema": REPORT_SCHEMA,
        "entries": [],
        "poisoned_tags": [],
        "fleet_candidates": [],
        "repin_candidates": [{"vmod": vmod, "pinned": "", "tag": tag}],
    }
    for issue in upstream_watch.render_issues(synthetic):
        if issue["kind"] == "repin_candidate":
            return issue["title"]
    raise PrepareError("upstream_watch.render_issues no longer renders re-pin issues")


# ---------------------------------------------------------------------------
# Registry facts
# ---------------------------------------------------------------------------


def repo_root(explicit=None) -> Path:
    return Path(explicit).resolve() if explicit else Path(ci_matrix.REPO_ROOT)


def manifest_path(root: Path, vmod: str) -> Path:
    return root / "registry" / "vmods" / f"{vmod}.yml"


def overlay_path(root: Path, vmod: str) -> Path:
    return root / "recipes" / "vmods" / "overlays" / vmod / "overlay.yml"


def _load_yaml(path: Path) -> dict:
    try:
        return yaml_subset.parse_file(path)
    except (OSError, yaml_subset.ManifestSyntaxError) as exc:
        raise PrepareError(f"{path}: unreadable ({exc})") from None


def cohort_version_blocks(root: Path, vmod: str) -> list:
    """Cohort manifests carrying a `<vmod>:` block with a version.

    Cachetag has one; dict and redis do not. It is the coupling that matters,
    not the name: that block is cross-checked by release_tool.py validate
    against `vmods.<id>.package.upstream_version` in every target manifest of
    the cohort (tools/manifest.py), so moving it obliges a matching change to
    recorded evidence. Detected from the tree rather than hardcoded, so a
    future cohort schema that drops or adds the coupling changes the answer
    without changing this file.
    """
    found = []
    cohorts = root / "registry" / "cohorts"
    if not cohorts.is_dir():
        return found
    for path in sorted(cohorts.glob("*.yml")):
        data = _load_yaml(path)
        block = data.get(vmod)
        if isinstance(block, dict) and "version" in block:
            found.append(str(path.relative_to(root)))
    return found


# ---------------------------------------------------------------------------
# Deriving the new pin from the old one
# ---------------------------------------------------------------------------


def version_affixes(pinned_tag: str, pinned_version: str) -> tuple:
    """(prefix, suffix) such that prefix + version + suffix == tag.

    The tag-to-version scheme is derived from the row's OWN recorded pair
    rather than assumed, because the selected upstreams do not share one:
    `v1.7` -> `1.7` strips a prefix, `9.0-23.1` -> `23.1` strips a
    <varnish-series> prefix, `vinyl-cache-9.0.1` -> `9.0.1` strips a longer
    one. Ambiguity is refused rather than guessed: the version has to appear in
    the tag exactly once, or there is more than one defensible reading and a
    machine picking one of them is how a package ends up named after the wrong
    number.
    """
    if not pinned_version:
        raise PrepareError("the pin records no version, so no tag-to-version scheme can be derived")
    occurrences = pinned_tag.count(pinned_version)
    if occurrences == 0:
        raise PrepareError(
            f"ambiguous tag-to-version scheme: the pinned version {pinned_version!r} does "
            f"not appear in the pinned tag {pinned_tag!r}, so the new tag's version cannot "
            "be derived from the recorded pair"
        )
    if occurrences > 1:
        raise PrepareError(
            f"ambiguous tag-to-version scheme: the pinned version {pinned_version!r} appears "
            f"{occurrences} times in the pinned tag {pinned_tag!r}, so which occurrence names "
            "the version is a guess"
        )
    index = pinned_tag.index(pinned_version)
    return pinned_tag[:index], pinned_tag[index + len(pinned_version) :]


def derive_version(pinned_tag: str, pinned_version: str, new_tag: str) -> str:
    """The new version, under the scheme the recorded pair defines."""
    prefix, suffix = version_affixes(pinned_tag, pinned_version)
    if not new_tag.startswith(prefix) or not new_tag.endswith(suffix):
        raise PrepareError(
            f"the candidate tag {new_tag!r} does not follow the recorded scheme "
            f"{prefix!r} + version + {suffix!r} that {pinned_tag!r} -> {pinned_version!r} "
            "defines; the version cannot be derived and is not guessed"
        )
    end = len(new_tag) - len(suffix) if suffix else len(new_tag)
    version = new_tag[len(prefix) : end]
    if not version:
        raise PrepareError(
            f"the candidate tag {new_tag!r} leaves an empty version under the recorded scheme"
        )
    if version == pinned_version:
        raise PrepareError(
            f"the candidate tag {new_tag!r} derives the same version {version!r} the pin "
            "already records; a re-pin that changes no version is not a re-pin"
        )
    return version


def substitute_version(url: str, old_version: str, new_version: str) -> str:
    """The archive URL for the new version, or a refusal.

    The recorded URL is a string somebody reviewed; rewriting it is only safe
    when there is exactly one place the version can be. Two occurrences, or
    none, means the URL is not a simple function of the version and a human
    writes the new one.
    """
    if not url:
        raise PrepareError("no archive_url is recorded")
    occurrences = url.count(old_version)
    if occurrences != 1:
        raise PrepareError(
            f"the recorded archive URL contains the pinned version {old_version!r} "
            f"{occurrences} times, not exactly once, so substituting the new version is "
            f"ambiguous: {url}"
        )
    return url.replace(old_version, new_version)


def branch_name(vmod: str, tag: str) -> str:
    """`auto-release/<vmod>-<tag>`, per release-automation plan section 5."""
    name = f"{BRANCH_PREFIX}{vmod}-{tag}"
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", name) or ".." in name or name.endswith(".lock"):
        raise PrepareError(
            f"the candidate would need the branch {name!r}, which is not a plain git ref name; "
            "nothing is pushed under a name this tool cannot vouch for"
        )
    return name


# ---------------------------------------------------------------------------
# Classification and planning
# ---------------------------------------------------------------------------


def analyse(root: Path, report: dict, candidate: dict) -> tuple:
    """(plan, reasons). Exactly one of the two is meaningful.

    A plan means the candidate can be prepared from registry facts alone. A
    non-empty reason list means it cannot, and every reason is reported --
    cachetag fails two independent tests and both belong in the record.
    """
    vmod = candidate["vmod"]
    tag = candidate["tag"]
    pinned = candidate.get("pinned", "")
    reasons: list = []

    if vmod in ENGINE_KEYS or "/" in vmod:
        return None, [INELIGIBLE_ENGINE]

    path = manifest_path(root, vmod)
    if not path.is_file():
        return None, [
            f"structurally surprising: no manifest at {path.relative_to(root)} for the "
            "candidate the watcher reported; nothing is prepared from a row this tool "
            "cannot read"
        ]
    try:
        data = ci_matrix.load_vmod_manifest(path)
    except (OSError, yaml_subset.ManifestSyntaxError) as exc:
        return None, [f"structurally surprising: {path.relative_to(root)} is unreadable ({exc})"]
    errors = ci_matrix.validate_vmod_manifest(data, str(path), discovery_id=vmod)
    if errors:
        return None, [
            "structurally surprising: the manifest does not validate as a vmod-ci/v1 "
            "document (" + "; ".join(errors[:3]) + ")"
        ]

    channel = None
    for name, source in sorted((data.get("sources") or {}).items()):
        if source.get("ref") == pinned and source.get("expected_commit"):
            channel = name
            break
    if channel is None:
        return None, [
            f"structurally surprising: no pinned source channel in {path.relative_to(root)} "
            f"records the tag {pinned!r} the report says is pinned; the report and the "
            "registry disagree and nothing is prepared until they do not"
        ]
    source = data["sources"][channel]

    overlay = None
    opath = overlay_path(root, vmod)
    if opath.is_file():
        overlay = _load_yaml(opath)
        patches = overlay.get("patches") or []
        if patches:
            reasons.append(INELIGIBLE_PATCHED)

    coupled = cohort_version_blocks(root, vmod)
    if coupled:
        reasons.append(INELIGIBLE_COHORT_COUPLED + " Coupled manifest(s): " + ", ".join(coupled))

    archive_url = source.get("archive_url") or ""
    method = ""
    if overlay is not None:
        method = ((overlay.get("source") or {}).get("archive") or {}).get("method") or ""
    if not archive_url or method == "derived-git-tag":
        reasons.append(INELIGIBLE_DERIVED_ARCHIVE)

    version = source.get("version") or ""
    new_version = None
    new_url = None
    try:
        new_version = derive_version(pinned, version, tag)
    except PrepareError as exc:
        reasons.append(str(exc))
    if archive_url and new_version is not None:
        try:
            new_url = substitute_version(archive_url, version, new_version)
        except PrepareError as exc:
            reasons.append(str(exc))

    overlay_url = ""
    if overlay is not None:
        overlay_url = ((overlay.get("source") or {}).get("archive") or {}).get("url") or ""
        if archive_url and overlay_url and overlay_url != archive_url:
            reasons.append(
                "structurally surprising: the overlay's source.archive.url and the "
                "manifest's archive_url already disagree, which vmod_recipe.py refuses; "
                "fix that before a re-pin moves either"
            )

    try:
        branch = branch_name(vmod, tag)
    except PrepareError as exc:
        reasons.append(str(exc))
        branch = ""

    if reasons:
        return None, reasons

    facts = ci_matrix.source_facts(data, channel)
    edits = [
        {
            "path": str(path.relative_to(root)),
            "block": ["sources", channel],
            "fields": [
                {"key": "ref", "old": pinned, "new": tag},
                {"key": "expected_commit", "old": source["expected_commit"], "from": "commit"},
                {"key": "version", "old": version, "new": new_version},
                {"key": "archive_url", "old": archive_url, "new": new_url},
                {
                    "key": "archive_sha256",
                    "old": source.get("archive_sha256", ""),
                    "from": "archive_sha256",
                },
            ],
        }
    ]
    if overlay is not None and overlay_url:
        # The overlay's URL is not an independent statement: vmod_recipe.py
        # refuses a recipe whose overlay URL is not the manifest's archive_url,
        # so this is the same pin written twice and it moves with the pin. The
        # byte count beside it is a measurement of the archive the job just
        # fetched, recorded for the same reason the digest is.
        overlay_fields = [{"key": "url", "old": overlay_url, "new": new_url}]
        obytes = ((overlay.get("source") or {}).get("archive") or {}).get("bytes")
        if obytes is not None:
            overlay_fields.append({"key": "bytes", "old": str(obytes), "from": "archive_bytes"})
        edits.append(
            {
                "path": str(opath.relative_to(root)),
                "block": ["source", "archive"],
                "fields": overlay_fields,
            }
        )

    plan = {
        "schema": PLAN_SCHEMA,
        "vmod": vmod,
        "channel": channel,
        "pinned_tag": pinned,
        "pinned_version": version,
        "pinned_commit": source["expected_commit"],
        "pinned_archive_url": archive_url,
        "pinned_archive_sha256": source.get("archive_sha256", ""),
        "new_tag": tag,
        "new_version": new_version,
        "new_archive_url": new_url,
        "observed_commit": observed_commit(report, vmod, tag),
        "archive_method": method or "upstream-release",
        "clone_url": facts["VMOD_CLONE_URL"],
        "branch": branch,
        "issue_title": issue_title(vmod, tag),
        "manifest_path": str(path.relative_to(root)),
        "overlay_path": str(opath.relative_to(root)) if overlay is not None else "",
        "edits": edits,
    }
    return plan, []


def classify(root: Path, report: dict) -> dict:
    """Every re-pin candidate in the report, eligible or refused with reasons."""
    rows = []
    for candidate in report["repin_candidates"]:
        vmod = candidate.get("vmod", "")
        tag = candidate.get("tag", "")
        try:
            plan, reasons = analyse(root, report, candidate)
        except PrepareError as exc:
            plan, reasons = None, [str(exc)]
        row = {
            "vmod": vmod,
            "pinned": candidate.get("pinned", ""),
            "tag": tag,
            "eligible": plan is not None,
            "reasons": reasons,
            "branch": plan["branch"] if plan else "",
            "clone_url": plan["clone_url"] if plan else "",
            "observed_commit": plan["observed_commit"] if plan else "",
            "issue_title": plan["issue_title"] if plan else "",
        }
        rows.append(row)
    eligible = [r for r in rows if r["eligible"]]
    return {
        "schema": ELIGIBILITY_SCHEMA,
        "candidates": rows,
        "counts": {
            "candidates": len(rows),
            "eligible": len(eligible),
            "ineligible": len(rows) - len(eligible),
        },
    }


def eligibility_summary(result: dict) -> str:
    """A GitHub step-summary table. Every refusal is visible, none is silent."""
    lines = [
        "## Auto-prepared re-pin branches",
        "",
        f"{result['counts']['candidates']} re-pin candidate(s): "
        f"{result['counts']['eligible']} eligible, "
        f"{result['counts']['ineligible']} not prepared automatically.",
        "",
        "| candidate | prepared | why not |",
        "| --- | --- | --- |",
    ]
    for row in result["candidates"]:
        why = " ".join(r.replace("\n", " ") for r in row["reasons"]).replace("|", "\\|")
        lines.append(
            f"| `{row['vmod']}` `{row['pinned']}` -> `{row['tag']}` | "
            f"{'yes' if row['eligible'] else 'no'} | {why or '-'} |"
        )
    lines += [
        "",
        "Preparing a branch publishes nothing and proves nothing: the pull request "
        f"carries an observation, and evidence is produced by CI. See `{_PREPARE_NOTE}`.",
        "",
    ]
    return "\n".join(lines)


TSV_COLUMNS = ("vmod", "tag", "pinned", "observed_commit", "branch", "clone_url", "issue_title")


def eligible_tsv(result: dict) -> str:
    """Tab-separated eligible rows, for the shell loop. No header: a header
    would be one more line the loop has to know to skip."""
    out = []
    for row in result["candidates"]:
        if not row["eligible"]:
            continue
        out.append("\t".join(str(row[column]) for column in TSV_COLUMNS))
    return "".join(line + "\n" for line in out)


# ---------------------------------------------------------------------------
# Applying a plan
# ---------------------------------------------------------------------------


def normalize_edit_path(relative: str) -> str:
    """The canonical repository-relative spelling of a path, or a refusal.

    A prefix test against a raw string is only as strong as the spelling it is
    handed: `./registry/targets/x.yml`, `registry//targets/x.yml` and
    `registry/./targets/x.yml` all name the same file and none of them starts
    with `registry/targets/`. `root / relative` resolves every one of them to
    the real thing, so the guard has to compare canonical forms or it is
    decoration. `..` and absolute paths are refused outright rather than
    normalized: neither has an innocent reading in a plan.
    """
    if relative != relative.strip() or not relative:
        raise PrepareError("refusing to edit an empty or space-padded path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or relative.startswith("/") or "\\" in relative:
        raise PrepareError(f"refusing to edit {relative!r}: edits are repository-relative paths")
    parts = [part for part in pure.parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PrepareError(
            f"refusing to edit {relative!r}: a plan never reaches outside the repository"
        )
    if not parts:
        raise PrepareError(f"refusing to edit {relative!r}: it names no file")
    return "/".join(parts)


def check_edit_path(relative: str) -> str:
    """Refuse anything that is not a pin. See FORBIDDEN_EDIT_* above.

    Returns the normalized path, which is what the caller must then use: a
    guard that checks one spelling and lets the caller open another is not a
    guard.
    """
    normalized = normalize_edit_path(relative)
    for prefix in FORBIDDEN_EDIT_PREFIXES:
        if normalized.startswith(prefix):
            raise PrepareError(
                f"refusing to edit {relative!r} ({normalized}): {prefix} carries recorded "
                "evidence and the cohort identity derived from it. Evidence is written by a "
                "run that measured something, by a human moving it with the release -- never "
                "by this tool."
            )
    for part in FORBIDDEN_EDIT_SUBSTRINGS:
        if part in "/" + normalized:
            raise PrepareError(
                f"refusing to edit {relative!r} ({normalized}): a transactions expectation is "
                "a pinned measured outcome, updated with the evidence that measured it."
            )
    return normalized


def _block_range(lines: list, block: list, path: str) -> tuple:
    """(start, end, indent) of the innermost named block's body."""
    start, end, indent = 0, len(lines), 0
    for depth, name in enumerate(block):
        want = " " * (2 * depth) + f"{name}:"
        found = None
        for index in range(start, end):
            if lines[index] == want:
                found = index
                break
        if found is None:
            raise PrepareError(f"{path}: no block {'.'.join(block)} (looking for {want!r})")
        indent = 2 * depth + 2
        body_start = found + 1
        body_end = body_start
        while body_end < end:
            line = lines[body_end]
            if line.strip() == "" or line.lstrip().startswith("#"):
                body_end += 1
                continue
            if len(line) - len(line.lstrip(" ")) < indent:
                break
            body_end += 1
        start, end = body_start, body_end
    return start, end, indent


def edit_field(text: str, block: list, key: str, old: str, new: str, path: str) -> str:
    """Rewrite exactly one `key: value` line inside a named block.

    A targeted text edit rather than a parse-and-dump, because these files are
    mostly comments and the comments are the reviewed part: a round-trip
    through any serializer would delete the reasoning and leave the values.
    Everything about the edit is asserted -- the block exists, the key appears
    exactly once inside it, and the value found is the value the plan recorded
    -- so a manifest that moved under the plan stops the candidate instead of
    being overwritten.
    """
    lines = text.split("\n")
    start, end, indent = _block_range(lines, block, path)
    pattern = re.compile(r"^ {%d}(%s): (.*)$" % (indent, re.escape(key)))
    matches = [(index, pattern.match(lines[index])) for index in range(start, end)]
    matches = [(index, m) for index, m in matches if m]
    if len(matches) != 1:
        raise PrepareError(
            f"{path}: expected exactly one {'.'.join(block)}.{key} line, found {len(matches)}"
        )
    index, match = matches[0]
    raw = match.group(2)
    quote = raw[0] if raw[:1] in ("\"", "'") and raw.endswith(raw[:1]) and len(raw) > 1 else ""
    current = raw[1:-1] if quote else raw
    if current != old:
        raise PrepareError(
            f"{path}: {'.'.join(block)}.{key} records {current!r}, not the {old!r} the plan "
            "was built from; the registry moved under the plan and nothing is overwritten"
        )
    if quote and (quote in new or "\\" in new):
        raise PrepareError(f"{path}: the new value for {key} cannot be quoted as written")
    lines[index] = " " * indent + f"{key}: {quote}{new}{quote}"
    return "\n".join(lines)


def resolve_field(field: dict, runtime: dict) -> str:
    if "from" in field:
        source = field["from"]
        value = runtime.get(source)
        if value in (None, ""):
            raise PrepareError(
                f"the plan needs a runtime value for {field['key']} (--{source.replace('_', '-')}) "
                "and none was given; a pin is never recorded from a default"
            )
        return str(value)
    return str(field["new"])


def apply_plan(root: Path, plan: dict, runtime: dict) -> list:
    """Rewrite every field the plan names. Returns the touched paths."""
    if plan.get("schema") != PLAN_SCHEMA:
        raise PrepareError(f"not a {PLAN_SCHEMA} document")
    touched = []
    for edit in plan["edits"]:
        # The NORMALIZED path is what gets opened, so the path the guard passed
        # and the path that is written are the same string.
        relative = check_edit_path(edit["path"])
        path = root / relative
        if not path.is_file():
            raise PrepareError(f"{relative}: the plan names a file that is not there")
        text = path.read_text(encoding="utf-8")
        for field in edit["fields"]:
            text = edit_field(
                text,
                edit["block"],
                field["key"],
                str(field["old"]),
                resolve_field(field, runtime),
                relative,
            )
        path.write_text(text, encoding="utf-8")
        touched.append(relative)
    return touched


# ---------------------------------------------------------------------------
# The pull request
# ---------------------------------------------------------------------------


def pr_title(plan: dict) -> str:
    return (
        f"re-pin {plan['vmod']} {plan['pinned_tag']} -> {plan['new_tag']} "
        "(prepared automatically, evidence pending)"
    )


def pr_body(
    plan: dict,
    commit: str,
    archive_sha256: str,
    archive_bytes: str = "",
    ancestry: str = "",
    issue_url: str = "",
    ci_run_url: str = "",
) -> str:
    vmod = plan["vmod"]
    rows = [
        ("tag", plan["pinned_tag"], plan["new_tag"]),
        ("peeled commit", plan["pinned_commit"], commit),
        ("version", plan["pinned_version"], plan["new_version"]),
    ]
    if plan["pinned_archive_url"]:
        rows.append(("archive url", plan["pinned_archive_url"], plan["new_archive_url"]))
    rows.append(("archive sha256", plan["pinned_archive_sha256"], archive_sha256))
    if archive_bytes:
        rows.append(("archive bytes", "", archive_bytes))

    lines = [
        f"Automation observed `{plan['new_tag']}` on `{vmod}`, above the pinned "
        f"`{plan['pinned_tag']}`, and prepared the re-pin for review. "
        "**It publishes nothing and merges nothing.**",
        "",
        "## What was recorded",
        "",
        "| field | pinned | prepared |",
        "| --- | --- | --- |",
    ]
    for name, before, after in rows:
        before_cell = f"`{before}`" if before else "-"
        lines.append(f"| {name} | {before_cell} | `{after}` |")
    lines += [
        "",
        "Exactly what a deliberate re-pin records -- tag name, peeled commit, and the "
        "archive digest under this row's own source policy (release-automation plan "
        "section 2.1). Files changed:",
        "",
    ]
    for edit in plan["edits"]:
        keys = ", ".join(f"`{f['key']}`" for f in edit["fields"])
        lines.append(f"- `{edit['path']}` -- {'.'.join(edit['block'])}: {keys}")
    lines += [
        "",
        "Nothing else was touched. No evidence file, cohort manifest, or transaction "
        "expectation is in this diff, by construction: `tools/repin_prepare.py` refuses "
        "to write them.",
        "",
        "## Observed is not tested",
        "",
        OBSERVED_NOT_TESTED,
        "",
        f"Contract: `{_PLAN_DOC}` section 1.2. Publication authority is a manual gate "
        f"(`{_DECISION_NOTE}`): automation detects, verifies pin integrity, notifies, and "
        f"-- as of this increment (`{_PREPARE_NOTE}`) -- prepares. It never publishes.",
        "",
        "## Checks that ran before this branch was pushed",
        "",
        f"- the candidate tag was re-peeled from `{plan['clone_url']}` and matched the "
        f"watcher's observation (`{plan['observed_commit']}`)",
        f"- ancestry: {ancestry or 'not recorded'}",
        "- the archive was downloaded and its sha256 computed from the bytes recorded above",
        "- `python3 tools/release_tool.py --no-cachetag-cross-check validate`",
        "- `python3 tools/ci_matrix.py check-catalog`",
        f"- `python3 tools/ci_matrix.py validate-vmod --manifest {plan['manifest_path']} "
        f"--id {vmod}`",
        "",
        "Those are structural checks on the edited tree. None of them builds, installs, or "
        "tests anything.",
        "",
        "## Links",
        "",
        f"- watcher issue: {issue_url or '(not linked)'}",
        "- CI evidence run: "
        + (
            ci_run_url
            or "(dispatched after this description was written; linked in a comment below)"
        ),
        "",
        "## Before this merges -- human work this automation cannot do",
        "",
        "- [ ] read the diff. A prepared re-pin is a proposal built from an observation, not "
        "a reviewed change.",
        "- [ ] wait for the dispatched CI run and read its evidence. A green run is the "
        "first thing in this pull request that is a test result.",
        "- [ ] rewrite the prose. Only value lines were edited -- deliberately, so that the "
        "reviewed comments survive -- which means any comment describing the OLD pin (its "
        "signature, its byte count, what was verified against which tree, and on which date) "
        "is now stale and says so about the wrong release.",
        f"- [ ] decide the packaging revisions: `{plan['overlay_path'] or 'the overlay'}` says "
        "to bump `revision` on any change to it, and `package.revision` policy on an upstream "
        "version bump is a judgement (`registry/README.md`, package revision rules). This "
        "automation makes no reviewed-data judgements and left both alone.",
        "- [ ] move the evidence with the release: the recorded target evidence under "
        "`registry/targets/` and the pinned transaction expectations under "
        "`recipes/*/transactions/expected/*.tsv` are measured outcomes for the OLD pin. They "
        "are updated from runs that measured the new one, not from this branch.",
        "- [ ] release remains a deliberate dispatch, and the draft-to-published flip remains "
        "a human action.",
        "",
        "## Interlock",
        "",
        f"One preparation per upstream and tag. This branch (`{plan['branch']}`) existing, an "
        "open pull request from it, or a CLOSED watcher issue all stop a second attempt. "
        "Closing the watcher issue is how you decline this candidate.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _write(path, text: str) -> None:
    if path:
        Path(path).write_text(text, encoding="utf-8")


def _load_plan(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PrepareError(f"{path}: unreadable plan ({exc})") from None
    if not isinstance(data, dict) or data.get("schema") != PLAN_SCHEMA:
        raise PrepareError(f"{path}: not a {PLAN_SCHEMA} document")
    return data


def cmd_eligibility(args) -> int:
    root = repo_root(args.repo_root)
    result = classify(root, load_report(args.report))
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    _write(args.json, text)
    if args.tsv:
        _write(args.tsv, eligible_tsv(result))
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(eligibility_summary(result))
    if not args.json:
        sys.stdout.write(text)
    for row in result["candidates"]:
        if row["eligible"]:
            print(f"eligible   {row['vmod']} {row['tag']} -> {row['branch']}", file=sys.stderr)
        else:
            print(f"NOT PREPARED  {row['vmod']} {row['tag']}", file=sys.stderr)
            for reason in row["reasons"]:
                print(f"    {reason}", file=sys.stderr)
    return 0


def cmd_plan(args) -> int:
    root = repo_root(args.repo_root)
    report = load_report(args.report)
    for candidate in report["repin_candidates"]:
        if candidate.get("vmod") == args.vmod and candidate.get("tag") == args.tag:
            break
    else:
        raise PrepareError(f"{args.vmod} {args.tag}: not a re-pin candidate in this report")
    plan, reasons = analyse(root, report, candidate)
    if plan is None:
        raise PrepareError(
            f"{args.vmod} {args.tag} is not prepared automatically: " + " | ".join(reasons)
        )
    text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    _write(args.json, text)
    if args.env:
        _write(
            args.env,
            "".join(
                "REPIN_{}='{}'\n".format(key.upper(), str(plan[value]).replace("'", "'\\''"))
                for key, value in (
                    ("vmod", "vmod"),
                    ("pinned_tag", "pinned_tag"),
                    ("new_tag", "new_tag"),
                    ("new_version", "new_version"),
                    ("archive_url", "new_archive_url"),
                    ("archive_method", "archive_method"),
                    ("clone_url", "clone_url"),
                    ("branch", "branch"),
                    ("issue_title", "issue_title"),
                    ("observed_commit", "observed_commit"),
                    ("manifest_path", "manifest_path"),
                )
            ),
        )
    if not args.json and not args.env:
        sys.stdout.write(text)
    return 0


def cmd_apply(args) -> int:
    root = repo_root(args.repo_root)
    plan = _load_plan(args.plan)
    runtime = {
        "commit": args.commit,
        "archive_sha256": args.archive_sha256,
        "archive_bytes": args.archive_bytes,
    }
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit or ""):
        raise PrepareError(f"--commit {args.commit!r} is not a 40-character commit id")
    if not re.fullmatch(r"[0-9a-f]{64}", args.archive_sha256 or ""):
        raise PrepareError(f"--archive-sha256 {args.archive_sha256!r} is not a sha256 digest")
    for path in apply_plan(root, plan, runtime):
        print(f"edited   {path}")
    return 0


def cmd_pr_body(args) -> int:
    plan = _load_plan(args.plan)
    title = pr_title(plan)
    body = pr_body(
        plan,
        commit=args.commit,
        archive_sha256=args.archive_sha256,
        archive_bytes=args.archive_bytes or "",
        ancestry=args.ancestry or "",
        issue_url=args.issue or "",
        ci_run_url=args.ci_run or "",
    )
    _write(args.title_file, title + "\n")
    _write(args.body_file, body)
    if not args.title_file and not args.body_file:
        print(json.dumps({"title": title, "body": body}, indent=2, sort_keys=True))
    return 0


def cmd_issue_lookup(args) -> int:
    """`<number>\\t<state>` for an exact issue title, or `0\\tabsent`.

    The workflow needs it for the interlock and for the comment target; doing
    the matching here rather than in shell keeps the exact-title rule in one
    selftested place, next to the code that generates the title.
    """
    try:
        rows = json.loads(Path(args.issues).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PrepareError(f"{args.issues}: unreadable issue listing ({exc})") from None
    print(lookup_issue(rows, args.title))
    return 0


def lookup_issue(rows, title: str) -> str:
    matches = [r for r in rows if isinstance(r, dict) and r.get("title") == title]
    if not matches:
        return "0\tabsent"
    open_ones = [r for r in matches if str(r.get("state", "")).lower() == "open"]
    chosen = open_ones[0] if open_ones else matches[0]
    return f"{chosen.get('number', 0)}\t{str(chosen.get('state', '')).lower()}"


def cmd_selftest(args) -> int:
    import repin_prepare_selftest

    return repin_prepare_selftest.main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", help="the checkout to read the registry from")
    sub = parser.add_subparsers(dest="command", required=True)

    p_elig = sub.add_parser("eligibility", help="classify every re-pin candidate in a report")
    p_elig.add_argument("--report", required=True, help="the raw upstream-watch-report/v1 JSON")
    p_elig.add_argument("--json", help="write the classification here")
    p_elig.add_argument("--tsv", help="write the eligible rows here, tab separated")
    p_elig.add_argument("--summary", help="append a markdown summary here")
    p_elig.set_defaults(func=cmd_eligibility)

    p_plan = sub.add_parser("plan", help="what one eligible candidate's re-pin would record")
    p_plan.add_argument("--report", required=True)
    p_plan.add_argument("--vmod", required=True)
    p_plan.add_argument("--tag", required=True)
    p_plan.add_argument("--json", help="write the plan here")
    p_plan.add_argument("--env", help="write shell-sourceable plan facts here")
    p_plan.set_defaults(func=cmd_plan)

    p_apply = sub.add_parser("apply", help="rewrite the pinned fields a plan names")
    p_apply.add_argument("--plan", required=True)
    p_apply.add_argument("--commit", required=True, help="the tag's peeled commit, re-peeled")
    p_apply.add_argument("--archive-sha256", required=True, help="of the archive as fetched")
    p_apply.add_argument("--archive-bytes", help="the fetched archive's size")
    p_apply.set_defaults(func=cmd_apply)

    p_body = sub.add_parser("pr-body", help="render the pull request title and body")
    p_body.add_argument("--plan", required=True)
    p_body.add_argument("--commit", required=True)
    p_body.add_argument("--archive-sha256", required=True)
    p_body.add_argument("--archive-bytes")
    p_body.add_argument("--ancestry", help="the ancestry check's result, in words")
    p_body.add_argument("--issue", help="the watcher issue URL")
    p_body.add_argument("--ci-run", help="the dispatched CI run URL, if already known")
    p_body.add_argument("--title-file")
    p_body.add_argument("--body-file")
    p_body.set_defaults(func=cmd_pr_body)

    p_look = sub.add_parser("issue-lookup", help="number and state for an exact issue title")
    p_look.add_argument("--issues", required=True, help="`gh issue list --json number,title,state`")
    p_look.add_argument("--title", required=True)
    p_look.set_defaults(func=cmd_issue_lookup)

    p_self = sub.add_parser("selftest", help="run the tests")
    p_self.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PrepareError as exc:
        print(f"E: {exc}", file=sys.stderr)
        return 2
    except ci_matrix.CatalogError as exc:
        print(f"E: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
