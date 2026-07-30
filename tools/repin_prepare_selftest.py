#!/usr/bin/env python3
"""Tests for tools/repin_prepare.py.

Run directly, or via `python3 tools/repin_prepare.py selftest`, or through the
`ci_matrix.py selftest` chain, which is what the CI structural-validation gate
invokes.

Nothing here touches the network, and nothing here writes to the real
repository: every test that applies a plan does it against a throwaway copy of
the two files a re-pin edits. The eligibility tests DO read the committed
registry, on purpose -- the whole point of machine-readable eligibility is that
it answers from what the registry says today, so a manifest or overlay change
that would silently make a patched or cohort-coupled row auto-preparable has to
break a test here.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import repin_prepare as rp  # noqa: E402
import upstream_watch as uw  # noqa: E402

_RESULTS: list = []

REPO = Path(__file__).resolve().parents[1]

DICT_PIN = "784584d272894a39cf995377618aad551a196424"
NEW_COMMIT = "1234567890abcdef1234567890abcdef12345678"
NEW_DIGEST = "a" * 64


def check(name: str, condition: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(condition), detail))


def _report(candidates, observations=None) -> dict:
    """A synthetic `upstream-watch-report/v1` carrying the given candidates.

    Shaped exactly as tools/upstream_watch.py builds it -- one tag entry per
    candidate row, with the candidate listed in `repin_candidates` and its
    peeled commit in `tag_observations` -- so a change to that shape breaks
    these tests rather than the live job.
    """
    observations = observations or {}
    entries = []
    for candidate in candidates:
        vmod = candidate["vmod"]
        key = vmod if "/" in vmod or vmod in rp.ENGINE_KEYS else f"{vmod}/release"
        entries.append(
            {
                "key": key,
                "kind": "tag",
                "vmod": "" if vmod in rp.ENGINE_KEYS else vmod,
                "ref": candidate["pinned"],
                "url": "https://example.invalid/x.git",
                "status": "ok",
                "sha": "0" * 40,
                "previous_sha": "",
                "changed": False,
                "detail": "",
                "repin_candidates": [candidate["tag"]],
                "informational_tags": [],
                "poisoned_tags": [],
                "fleet_new_tags": [],
                "tag_observations": {
                    candidate["tag"]: {
                        "sha": observations.get(candidate["tag"], NEW_COMMIT),
                        "poisoned": False,
                    }
                },
            }
        )
    return {
        "schema": "upstream-watch-report/v1",
        "state_note": "",
        "stateless": False,
        "broken_manifests": [],
        "entries": entries,
        "moved_pins": [],
        "repin_candidates": list(candidates),
        "poisoned_tags": [],
        "fleet_candidates": [],
        "fleet_seeded": [],
        "vinyl_changed": False,
        "vinyl_head_sha": "",
        "changed_vmods": [],
        "run": False,
        "ok": True,
    }


DICT_CANDIDATE = {"vmod": "dict", "pinned": "v1.7", "tag": "v1.8"}
REDIS_CANDIDATE = {"vmod": "redis", "pinned": "9.0-23.1", "tag": "9.0-24.0"}
CACHETAG_CANDIDATE = {"vmod": "cachetag", "pinned": "v1.0.1", "tag": "v1.1.0"}
ENGINE_CANDIDATE = {
    "vmod": uw.VINYL_RELEASE_KEY,
    "pinned": uw.VINYL_RELEASE_TAG,
    "tag": "vinyl-cache-9.1",
}
ALL_CANDIDATES = [DICT_CANDIDATE, REDIS_CANDIDATE, CACHETAG_CANDIDATE, ENGINE_CANDIDATE]


def _classify(candidates=None) -> dict:
    return rp.classify(REPO, _report(candidates or ALL_CANDIDATES))


def _row(result: dict, vmod: str) -> dict:
    return next(r for r in result["candidates"] if r["vmod"] == vmod)


def _dict_plan() -> dict:
    plan, reasons = rp.analyse(REPO, _report([DICT_CANDIDATE]), DICT_CANDIDATE)
    assert plan is not None, reasons
    return plan


# --- eligibility -----------------------------------------------------------


def test_the_engine_row_is_never_prepared() -> None:
    row = _row(_classify(), uw.VINYL_RELEASE_KEY)
    check(
        "eligibility: the engine release row is refused",
        not row["eligible"] and any("engine row" in r for r in row["reasons"]),
        str(row["reasons"]),
    )
    check(
        "eligibility: the refusal names the missing machine-readable pin home",
        any("machine-readable home" in r for r in row["reasons"]),
        str(row["reasons"]),
    )


def test_a_patched_overlay_is_refused_and_says_why() -> None:
    row = _row(_classify(), "redis")
    check(
        "eligibility: a VMOD carrying a reviewed patch is refused",
        not row["eligible"] and any("patched overlay" in r for r in row["reasons"]),
        str(row["reasons"]),
    )
    check(
        "eligibility: the refusal names the human re-review obligation",
        any("reviewed_against" in r for r in row["reasons"]),
        str(row["reasons"]),
    )


def test_patch_eligibility_reads_the_overlay_rather_than_a_list() -> None:
    """The mechanism, not the outcome: an empty patches list must be eligible.

    dict's overlay has no `patches` key at all and redis's has one entry. If
    eligibility were a hardcoded id list, adding a patch to dict tomorrow would
    quietly keep it auto-preparable; this asserts the classification follows the
    overlay's own data.
    """
    overlay = rp._load_yaml(rp.overlay_path(REPO, "redis"))
    check(
        "eligibility: redis's overlay really does declare patches",
        bool(overlay.get("patches")),
        str(overlay.get("patches")),
    )
    dict_overlay = rp._load_yaml(rp.overlay_path(REPO, "dict"))
    check(
        "eligibility: dict's overlay declares no patches",
        not dict_overlay.get("patches"),
        str(dict_overlay.get("patches")),
    )


def test_a_cohort_coupled_pin_is_refused() -> None:
    row = _row(_classify(), "cachetag")
    check(
        "eligibility: cachetag is refused because a cohort records its version",
        not row["eligible"] and any("cohort-coupled" in r for r in row["reasons"]),
        str(row["reasons"]),
    )
    check(
        "eligibility: the coupling is detected from the tree, not asserted",
        rp.cohort_version_blocks(REPO, "cachetag")
        and not rp.cohort_version_blocks(REPO, "dict"),
        str(rp.cohort_version_blocks(REPO, "cachetag")),
    )
    check(
        "eligibility: cachetag is also refused for its derived archive",
        any("derived archive" in r for r in row["reasons"]),
        str(row["reasons"]),
    )


def test_dict_is_the_one_row_prepared_today() -> None:
    result = _classify()
    eligible = [r["vmod"] for r in result["candidates"] if r["eligible"]]
    check(
        "eligibility: exactly the rows the registry qualifies are prepared",
        eligible == ["dict"],
        str(eligible),
    )
    check(
        "eligibility: the counts agree with the rows",
        result["counts"] == {"candidates": 4, "eligible": 1, "ineligible": 3},
        str(result["counts"]),
    )
    row = _row(result, "dict")
    check(
        "eligibility: the eligible row carries its branch, clone url and issue title",
        row["branch"] == "auto-release/dict-v1.8"
        and row["clone_url"] == "https://git.gnu.org.ua/vmod-dict.git"
        and row["issue_title"] == "upstream-watch: re-pin candidate dict v1.8",
        str(row),
    )


def test_no_refusal_is_silent() -> None:
    result = _classify()
    check(
        "eligibility: every refused candidate carries at least one reason",
        all(r["reasons"] for r in result["candidates"] if not r["eligible"]),
        str([(r["vmod"], r["reasons"]) for r in result["candidates"]]),
    )
    summary = rp.eligibility_summary(result)
    check(
        "eligibility: the step summary names every candidate, prepared or not",
        all(f"`{r['vmod']}`" in summary for r in result["candidates"]),
        summary,
    )
    tsv = rp.eligible_tsv(result)
    check(
        "eligibility: the tsv carries only the eligible rows, one per line",
        [line.split("\t")[0] for line in tsv.splitlines()] == ["dict"]
        and all(len(line.split("\t")) == len(rp.TSV_COLUMNS) for line in tsv.splitlines()),
        repr(tsv),
    )


def test_a_report_the_registry_does_not_recognise_is_refused() -> None:
    unknown = {"vmod": "nosuchvmod", "pinned": "v1", "tag": "v2"}
    row = _row(_classify([unknown]), "nosuchvmod")
    check(
        "eligibility: a candidate with no manifest is refused loudly",
        not row["eligible"] and any("structurally surprising" in r for r in row["reasons"]),
        str(row["reasons"]),
    )
    stale = {"vmod": "dict", "pinned": "v1.6", "tag": "v1.8"}
    row = _row(_classify([stale]), "dict")
    check(
        "eligibility: a report that disagrees with the registry's pin is refused",
        not row["eligible"] and any("registry disagree" in r for r in row["reasons"]),
        str(row["reasons"]),
    )


# --- deriving the new pin --------------------------------------------------


def test_the_version_scheme_is_derived_from_the_rows_own_pin() -> None:
    cases = [
        ("v1.7", "1.7", "v1.8", "1.8"),
        ("v1.0.1", "1.0.1", "v1.1.0", "1.1.0"),
        ("9.0-23.1", "23.1", "9.0-24.0", "24.0"),
        ("vinyl-cache-9.0.1", "9.0.1", "vinyl-cache-9.1", "9.1"),
    ]
    for pinned_tag, pinned_version, new_tag, expected in cases:
        got = rp.derive_version(pinned_tag, pinned_version, new_tag)
        check(
            f"version: {pinned_tag} -> {pinned_version} makes {new_tag} mean {expected}",
            got == expected,
            got,
        )


def test_an_ambiguous_version_scheme_is_refused_not_guessed() -> None:
    for pinned_tag, pinned_version, new_tag, why in [
        ("v1.7-1.7", "1.7", "v1.8-1.8", "the version appears twice in the pinned tag"),
        ("release-seven", "1.7", "release-eight", "the version is not in the pinned tag"),
        ("9.0-23.1", "23.1", "9.1-24.0", "the candidate leaves the recorded scheme"),
        ("v1.7", "1.7", "v1.7", "the derived version does not move"),
    ]:
        try:
            rp.derive_version(pinned_tag, pinned_version, new_tag)
        except rp.PrepareError as exc:
            check(f"version: refused -- {why}", True, str(exc))
        else:
            check(f"version: refused -- {why}", False, "no refusal")


def test_the_archive_url_substitution_must_be_unambiguous() -> None:
    url = "https://download.gnu.org.ua/release/vmod-dict/vmod-dict-1.7.tar.gz"
    check(
        "url: a single occurrence of the version is substituted",
        rp.substitute_version(url, "1.7", "1.8").endswith("vmod-dict-1.8.tar.gz"),
        rp.substitute_version(url, "1.7", "1.8"),
    )
    for bad, why in [
        ("https://example.invalid/1.7/vmod-1.7.tar.gz", "two occurrences"),
        ("https://example.invalid/vmod-latest.tar.gz", "no occurrence"),
    ]:
        try:
            rp.substitute_version(bad, "1.7", "1.8")
        except rp.PrepareError as exc:
            check(f"url: refused -- {why}", True, str(exc))
        else:
            check(f"url: refused -- {why}", False, "no refusal")


def test_the_branch_name_follows_the_plan() -> None:
    check(
        "branch: auto-release/<vmod>-<tag>, per release-automation plan section 5",
        rp.branch_name("dict", "v1.8") == "auto-release/dict-v1.8",
    )
    try:
        rp.branch_name("dict", "v1.8 ; rm -rf /")
    except rp.PrepareError as exc:
        check("branch: a tag that is not a plain ref name is refused", True, str(exc))
    else:
        check("branch: a tag that is not a plain ref name is refused", False)


# --- the plan --------------------------------------------------------------


def test_the_plan_records_what_a_deliberate_repin_records() -> None:
    plan = _dict_plan()
    check(
        "plan: the tag, the peeled commit and the digest are all named",
        plan["new_tag"] == "v1.8"
        and plan["observed_commit"] == NEW_COMMIT
        and plan["pinned_commit"] == DICT_PIN,
        str({k: plan[k] for k in ("new_tag", "observed_commit", "pinned_commit")}),
    )
    check(
        "plan: the new version and archive url are derived, not invented",
        plan["new_version"] == "1.8"
        and plan["new_archive_url"].endswith("vmod-dict-1.8.tar.gz"),
        str((plan["new_version"], plan["new_archive_url"])),
    )
    paths = [edit["path"] for edit in plan["edits"]]
    check(
        "plan: it edits the manifest and the overlay's copy of the same url, nothing else",
        paths == ["registry/vmods/dict.yml", "recipes/vmods/overlays/dict/overlay.yml"],
        str(paths),
    )
    keys = [f["key"] for f in plan["edits"][0]["fields"]]
    check(
        "plan: the manifest edit is exactly the five source-identity fields",
        keys == ["ref", "expected_commit", "version", "archive_url", "archive_sha256"],
        str(keys),
    )
    runtime = [f["key"] for edit in plan["edits"] for f in edit["fields"] if "from" in f]
    check(
        "plan: the commit, digest and byte count come from the job, not from the plan",
        runtime == ["expected_commit", "archive_sha256", "bytes"],
        str(runtime),
    )


def test_the_plan_never_names_an_evidence_path() -> None:
    plan = _dict_plan()
    for edit in plan["edits"]:
        rp.check_edit_path(edit["path"])
    check("plan: every edit path passes the evidence guard", True)


# --- apply -----------------------------------------------------------------


def _sandbox(tmp: Path) -> Path:
    """A throwaway tree carrying just the two files a dict re-pin edits."""
    root = tmp / "repo"
    for relative in ("registry/vmods/dict.yml", "recipes/vmods/overlays/dict/overlay.yml"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO / relative).read_text(encoding="utf-8"), encoding="utf-8")
    return root


def test_apply_rewrites_only_the_named_fields_and_keeps_the_comments() -> None:
    plan = _dict_plan()
    with tempfile.TemporaryDirectory() as tmp:
        root = _sandbox(Path(tmp))
        before = (root / "registry/vmods/dict.yml").read_text(encoding="utf-8")
        rp.apply_plan(
            root,
            plan,
            {"commit": NEW_COMMIT, "archive_sha256": NEW_DIGEST, "archive_bytes": "999"},
        )
        after = (root / "registry/vmods/dict.yml").read_text(encoding="utf-8")
        overlay = (root / "recipes/vmods/overlays/dict/overlay.yml").read_text(encoding="utf-8")

    check(
        "apply: the pinned fields carry the new identity",
        "    ref: v1.8" in after
        and f"    expected_commit: {NEW_COMMIT}" in after
        and '    version: "1.8"' in after
        and f"    archive_sha256: {NEW_DIGEST}" in after
        and "vmod-dict-1.8.tar.gz" in after,
        after,
    )
    values_after = [l for l in after.splitlines() if not l.lstrip().startswith("#")]
    check(
        "apply: the old identity is gone from every value line",
        not any("v1.7" in l or DICT_PIN in l for l in values_after),
        str([l for l in values_after if "v1.7" in l or DICT_PIN in l]),
    )
    check(
        "apply: the old identity SURVIVES in the comments, which is the human's job to fix",
        any("v1.7" in l for l in after.splitlines() if l.lstrip().startswith("#")),
        "the prose describing the old pin is not rewritten; the PR checklist says so",
    )
    check(
        "apply: the quoting style of the value it replaced is preserved",
        '    version: "1.8"' in after and "    version: 1.8" not in after,
        after,
    )
    comments_before = [l for l in before.splitlines() if l.lstrip().startswith("#")]
    comments_after = [l for l in after.splitlines() if l.lstrip().startswith("#")]
    check(
        "apply: every comment line survives the edit",
        comments_before == comments_after,
        f"{len(comments_before)} before, {len(comments_after)} after",
    )
    check(
        "apply: the line count is unchanged, so nothing was reflowed",
        len(before.splitlines()) == len(after.splitlines()),
    )
    check(
        "apply: the overlay's duplicate url and byte count follow the pin",
        "    url: https://download.gnu.org.ua/release/vmod-dict/vmod-dict-1.8.tar.gz" in overlay
        and '    bytes: "999"' in overlay,
        overlay,
    )
    check(
        "apply: the overlay's reviewed revision is NOT touched",
        '\nrevision: "2"\n' in overlay,
        overlay,
    )


def test_apply_refuses_to_write_evidence() -> None:
    """The plan section 1.2 contract, on the writing side.

    Evidence is a measured outcome. This tool measures nothing, so it must be
    incapable of writing one however a plan is worded.
    """
    # The plain spellings, and then the ones that name the same file without
    # starting with the forbidden prefix. A guard that compares raw strings
    # passes every one of the second group while `root / relative` resolves
    # them all to the real evidence file, so "however a plan is worded" is only
    # true if these are refused too.
    for relative in (
        "registry/targets/vinyl-9.0.1-ac4f719c16f4/debian-13-amd64.yml",
        "registry/cohorts/vinyl-9.0.1-ac4f719c16f4.yml",
        "recipes/debian-13/transactions/expected/scenarios.tsv",
        "registry/distro-native/debian-13-amd64.yml",
        "../elsewhere/thing.yml",
        "/etc/passwd",
        # non-normalized spellings of the first entry
        "./registry/targets/vinyl-9.0.1-ac4f719c16f4/debian-13-amd64.yml",
        "registry//targets/vinyl-9.0.1-ac4f719c16f4/debian-13-amd64.yml",
        "registry/./targets/vinyl-9.0.1-ac4f719c16f4/debian-13-amd64.yml",
        "./registry/./targets//vinyl-9.0.1-ac4f719c16f4/debian-13-amd64.yml",
        "recipes/debian-13/./transactions/expected/scenarios.tsv",
        "registry/vmods/../targets/vinyl-9.0.1-ac4f719c16f4/debian-13-amd64.yml",
        "",
        " registry/vmods/dict.yml",
    ):
        try:
            rp.check_edit_path(relative)
        except rp.PrepareError as exc:
            check(f"apply: refuses to edit {relative!r}", True, str(exc))
        else:
            check(f"apply: refuses to edit {relative!r}", False, "no refusal")

    check(
        "apply: a legal path normalizes to its canonical spelling and is returned",
        rp.check_edit_path("./registry/vmods/dict.yml") == "registry/vmods/dict.yml"
        and rp.check_edit_path("registry//vmods/dict.yml") == "registry/vmods/dict.yml",
        rp.check_edit_path("./registry/vmods/dict.yml"),
    )

    plan = _dict_plan()
    for forged_path in (
        "registry/targets/vinyl-9.0.1-ac4f719c16f4/el9-x86_64.yml",
        "./registry/targets/vinyl-9.0.1-ac4f719c16f4/el9-x86_64.yml",
        "registry//targets/vinyl-9.0.1-ac4f719c16f4/el9-x86_64.yml",
        "registry/./targets/vinyl-9.0.1-ac4f719c16f4/el9-x86_64.yml",
        "registry/vmods/../targets/vinyl-9.0.1-ac4f719c16f4/el9-x86_64.yml",
    ):
        forged = json.loads(json.dumps(plan))
        forged["edits"][0]["path"] = forged_path
        with tempfile.TemporaryDirectory() as tmp:
            root = _sandbox(Path(tmp))
            try:
                rp.apply_plan(root, forged, {"commit": NEW_COMMIT, "archive_sha256": NEW_DIGEST})
            except rp.PrepareError as exc:
                check(f"apply: a forged plan spelled {forged_path!r} is refused", True, str(exc))
            else:
                check(f"apply: a forged plan spelled {forged_path!r} is refused", False)


def test_apply_refuses_when_the_registry_moved_under_the_plan() -> None:
    plan = _dict_plan()
    with tempfile.TemporaryDirectory() as tmp:
        root = _sandbox(Path(tmp))
        path = root / "registry/vmods/dict.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("    ref: v1.7", "    ref: v1.7.1"),
            encoding="utf-8",
        )
        try:
            rp.apply_plan(root, plan, {"commit": NEW_COMMIT, "archive_sha256": NEW_DIGEST})
        except rp.PrepareError as exc:
            check("apply: a changed recorded value stops the edit", "not the" in str(exc), str(exc))
        else:
            check("apply: a changed recorded value stops the edit", False)


def test_apply_refuses_a_missing_runtime_value() -> None:
    plan = _dict_plan()
    with tempfile.TemporaryDirectory() as tmp:
        root = _sandbox(Path(tmp))
        try:
            rp.apply_plan(root, plan, {"commit": NEW_COMMIT})
        except rp.PrepareError as exc:
            check("apply: a pin is never recorded from a default", True, str(exc))
        else:
            check("apply: a pin is never recorded from a default", False)


def test_edit_field_needs_exactly_one_match() -> None:
    text = "sources:\n  release:\n    ref: v1.7\n  other:\n    ref: v1.7\n"
    edited = rp.edit_field(text, ["sources", "release"], "ref", "v1.7", "v1.8", "x.yml")
    check(
        "edit: the block scopes the match, so the sibling channel is untouched",
        edited == "sources:\n  release:\n    ref: v1.8\n  other:\n    ref: v1.7\n",
        repr(edited),
    )
    try:
        rp.edit_field(text, ["sources", "nope"], "ref", "v1.7", "v1.8", "x.yml")
    except rp.PrepareError as exc:
        check("edit: a missing block is refused", True, str(exc))
    else:
        check("edit: a missing block is refused", False)
    try:
        rp.edit_field("sources:\n  release:\n    other: 1\n", ["sources", "release"],
                      "ref", "v1.7", "v1.8", "x.yml")
    except rp.PrepareError as exc:
        check("edit: a field found zero times is refused", True, str(exc))
    else:
        check("edit: a field found zero times is refused", False)


# --- the pull request ------------------------------------------------------


def test_the_pr_body_says_observed_is_not_tested() -> None:
    """The release-automation plan section 1.2 contract test.

    If this sentence ever falls out of the body, a machine-opened pull request
    starts reading like a claim that the pin works.
    """
    plan = _dict_plan()
    body = rp.pr_body(plan, NEW_COMMIT, NEW_DIGEST, ancestry="reachable from origin/master")
    check(
        "pr: the body carries the observed-not-tested statement verbatim",
        rp.OBSERVED_NOT_TESTED in body,
        body,
    )
    check(
        "pr: it says evidence is pending in as many words",
        "CI evidence for this pin is PENDING" in body,
        body,
    )
    check(
        "pr: it says the automation publishes nothing",
        "publishes nothing and merges nothing" in body and "never publishes" in body,
        body,
    )


def test_the_pr_body_states_the_facts_and_the_human_checklist() -> None:
    plan = _dict_plan()
    body = rp.pr_body(
        plan,
        NEW_COMMIT,
        NEW_DIGEST,
        archive_bytes="414560",
        ancestry="reachable from origin/master",
        issue_url="https://example.invalid/issues/7",
        ci_run_url="https://example.invalid/runs/9",
    )
    for needle in (
        NEW_COMMIT,
        NEW_DIGEST,
        "v1.7",
        "v1.8",
        "reachable from origin/master",
        "https://example.invalid/issues/7",
        "https://example.invalid/runs/9",
        "registry/targets/",
        "transactions/expected/",
        "deliberate dispatch",
        "auto-release/dict-v1.8",
        "414560",
    ):
        check(f"pr: the body states {needle!r}", needle in body, body[:400])
    check(
        "pr: the checklist is a checklist, so nothing reads as already done",
        body.count("- [ ] ") >= 4,
        body,
    )
    check(
        "pr: the title names both tags and says evidence is pending",
        rp.pr_title(plan)
        == "re-pin dict v1.7 -> v1.8 (prepared automatically, evidence pending)",
        rp.pr_title(plan),
    )


def test_the_pr_body_is_deterministic() -> None:
    plan = _dict_plan()
    first = rp.pr_body(plan, NEW_COMMIT, NEW_DIGEST, ancestry="ok")
    second = rp.pr_body(plan, NEW_COMMIT, NEW_DIGEST, ancestry="ok")
    check("pr: the same inputs render the same body", first == second)


def test_the_issue_title_comes_from_the_watcher() -> None:
    """The interlock's dedupe key. Two spellings of it would be two issues."""
    plan = _dict_plan()
    report = uw.render_issues(
        {
            "entries": [],
            "poisoned_tags": [],
            "fleet_candidates": [],
            "repin_candidates": [{"vmod": "dict", "pinned": "v1.7", "tag": "v1.8"}],
        }
    )
    check(
        "issue: the title the job looks up is the title the notify job files",
        plan["issue_title"] == report[0]["title"],
        f"{plan['issue_title']!r} vs {report[0]['title']!r}",
    )


def test_issue_lookup_prefers_an_open_issue_and_reports_absence() -> None:
    rows = [
        {"number": 3, "title": "upstream-watch: re-pin candidate dict v1.8", "state": "CLOSED"},
        {"number": 9, "title": "upstream-watch: re-pin candidate dict v1.8", "state": "OPEN"},
        {"number": 4, "title": "something else", "state": "OPEN"},
    ]
    check(
        "issue-lookup: an open issue wins over a closed one with the same title",
        rp.lookup_issue(rows, "upstream-watch: re-pin candidate dict v1.8") == "9\topen",
        rp.lookup_issue(rows, "upstream-watch: re-pin candidate dict v1.8"),
    )
    check(
        "issue-lookup: a closed-only title reports closed, which stops the candidate",
        rp.lookup_issue([rows[0]], "upstream-watch: re-pin candidate dict v1.8") == "3\tclosed",
    )
    check(
        "issue-lookup: an unknown title is absent, not an error",
        rp.lookup_issue(rows, "nope") == "0\tabsent",
    )


# --- the report contract ---------------------------------------------------


def test_a_report_that_is_not_a_watch_report_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "r.json"
        path.write_text(json.dumps({"schema": "something-else/v1"}), encoding="utf-8")
        try:
            rp.load_report(path)
        except rp.PrepareError as exc:
            check("report: a foreign document is refused", True, str(exc))
        else:
            check("report: a foreign document is refused", False)


def test_the_observed_commit_comes_from_the_same_observation() -> None:
    report = _report([DICT_CANDIDATE], observations={"v1.8": NEW_COMMIT})
    check(
        "report: the candidate's peeled commit is read from the entry that saw it",
        rp.observed_commit(report, "dict", "v1.8") == NEW_COMMIT,
    )
    broken = _report([DICT_CANDIDATE])
    broken["entries"][0]["tag_observations"]["v1.8"]["sha"] = "not-a-sha"
    try:
        rp.observed_commit(broken, "dict", "v1.8")
    except rp.PrepareError as exc:
        check("report: an unusable observation stops the candidate", True, str(exc))
    else:
        check("report: an unusable observation stops the candidate", False)


def test_the_watcher_writes_a_report_the_tool_can_read() -> None:
    """The end-to-end shape contract between the two tools, no network.

    upstream_watch's own battery proves the report's content; this proves the
    document it writes to --report is the document repin_prepare parses, which
    is the seam a change to either side would break silently.
    """
    import upstream_watch_selftest as uws

    transcript = uws._cachetag_with_v110("88" * 20)
    with tempfile.TemporaryDirectory() as tmp:
        state = uws._state(Path(tmp), dict(uws.HEALTHY_REFS))
        report = uws.run_check(state_path=state, transcript=transcript)
        path = Path(tmp) / "report.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        loaded = rp.load_report(path)
    result = rp.classify(REPO, loaded)
    check(
        "seam: a real watcher report classifies without raising",
        result["counts"]["candidates"] == 1,
        str(result["counts"]),
    )
    check(
        "seam: the real cachetag candidate is refused, not prepared",
        not result["candidates"][0]["eligible"],
        str(result["candidates"][0]["reasons"]),
    )


# --- the workflow wiring ---------------------------------------------------


def test_the_workflow_wires_the_job_the_way_the_decision_requires() -> None:
    """Text assertions on the workflow, as ci_matrix_selftest does for its own.

    Not a YAML parse -- the tooling is stdlib-only and there is no YAML parser
    that reads GitHub's flow syntax here. What these guard is the wiring that
    would be silently wrong rather than loudly broken: a missing permission
    turns into a runtime 403 on the one run that had work, and a missing
    --report turns into a job that downloads an artifact nobody wrote.
    """
    workflow = (REPO / ".github/workflows/trunk-early-warning.yml").read_text(encoding="utf-8")
    check(
        "workflow: the gate writes the raw report and uploads it",
        "--report \"$RUNNER_TEMP/upstream-report.json\"" in workflow
        and "name: upstream-watch-report" in workflow,
        "gate step",
    )
    check(
        "workflow: the gate exposes repin_candidates as an output",
        "repin_candidates: ${{ steps.watch.outputs.repin_candidates }}" in workflow,
    )
    check(
        "workflow: the job runs after the gate and the notify job",
        "needs: [gate, notify]" in workflow,
    )
    check(
        "workflow: it runs only when there is a candidate, and survives a red gate",
        "!cancelled() && needs.gate.outputs.repin_candidates != ''" in workflow,
    )
    job = workflow.split("prepare-repin:", 1)[1].split("\n  structural-validation:", 1)[0]
    for permission in ("contents: write", "pull-requests: write", "actions: write", "issues: write"):
        check(f"workflow: the job asks for {permission}", permission in job, job[:200])
    check(
        "workflow: the logic lives in the script, not in the YAML",
        "scripts/ci/prepare-repin.sh" in job and job.count("run:") == 1,
        str(job.count("run:")),
    )
    check(
        "workflow: it borrows no secret; the workflow token is the whole credential",
        "GH_TOKEN: ${{ github.token }}" in job and "secrets." not in job,
        job[:200],
    )
    check(
        "workflow: overlapping runs cannot race the interlock",
        "concurrency:" in job and "cancel-in-progress: false" in job,
        job[:400],
    )
    script = (REPO / "scripts/ci/prepare-repin.sh").read_text(encoding="utf-8")
    check(
        "workflow: the script dispatches ci.yml rather than relying on pull_request",
        "gh workflow run ci.yml" in script,
    )
    check(
        "workflow: the script ends red when any candidate failed",
        'if [ "$errors" != 0 ]; then' in script and "exit 1" in script,
    )


def test_nothing_the_job_needs_lives_where_the_tree_reset_would_delete_it() -> None:
    """The defect this pair of assertions exists for.

    The script resets the working tree with `git clean -fd` before every
    candidate, which deletes every UNTRACKED file under the checkout. An input
    downloaded into the workspace therefore survives exactly until the first
    reset: candidate one works, every candidate after it fails on a missing
    file. It is invisible in a one-candidate rehearsal and it cannot happen at
    all on the host, so a text assertion on both ends is the honest guard --
    the live path is not exercised by any test here.
    """
    workflow = (REPO / ".github/workflows/trunk-early-warning.yml").read_text(encoding="utf-8")
    job = workflow.split("prepare-repin:", 1)[1].split("\n  structural-validation:", 1)[0]
    check(
        "reset: the report artifact is downloaded outside the checkout",
        "path: ${{ runner.temp }}/watch-report" in job,
        job,
    )
    check(
        "reset: the script is handed the runner-temp copy, not a workspace path",
        '"$RUNNER_TEMP/watch-report/upstream-report.json"' in job
        and "GITHUB_WORKSPACE/watch-report" not in job,
        job,
    )
    script = (REPO / "scripts/ci/prepare-repin.sh").read_text(encoding="utf-8")
    check(
        "reset: the script refuses a work directory inside the checkout",
        'case "$work/" in' in script and '"$repo"/*)' in script,
        "containment guard",
    )
    check(
        "reset: the script copies the report into its own work dir before the loop",
        'cp "$report" "$work/watch-report.json"' in script
        and 'report="$work/watch-report.json"' in script
        and script.index('report="$work/watch-report.json"')
        < script.index('git -C "$repo" clean'),
        "defensive copy ordering",
    )


def test_the_script_treats_the_abnormal_cases_as_errors() -> None:
    """Three outcomes that must be red rather than shrugged at.

    Each one leaves something a reader would misread: a preparation nobody was
    told about, a pull request with no evidence run, or a candidate list that
    stopped halfway. Text assertions, for the same reason as above -- the live
    path is not exercised here.
    """
    script = (REPO / "scripts/ci/prepare-repin.sh").read_text(encoding="utf-8")
    check(
        "script: a candidate with no watcher issue is an error, never issues/0",
        'if [ "$issue_number" = 0 ]; then' in script
        and "no watcher issue" in script
        and "issues/0" not in script,
        "absent-issue handling",
    )
    check(
        "script: a failed CI dispatch counts as an error and says so honestly",
        "dispatched=no" in script
        and "CI was NOT dispatched" in script
        and "CI DISPATCH FAILED" in script,
        "dispatch handling",
    )
    check(
        "script: the failure comment does not claim the attempt will not be retried",
        "does not retry it" not in script
        and "scheduled run will try this candidate again" in script
        and "CLOSE this issue" in script,
        "retry wording: a pre-push failure records nothing, so the next run tries again",
    )
    # The exact guard text, not "the word appears somewhere": under `set -e` an
    # unguarded failure here aborts the whole script, leaving later candidates
    # unprocessed and nobody's issue commented.
    for label, guard in (
        ("issue-lookup", 'if ! lookup=$(python3 "$repo/tools/repin_prepare.py" issue-lookup'),
        ("pr-body", 'if ! python3 "$repo/tools/repin_prepare.py" pr-body'),
        ("the branch commit", '|| ! git -C "$repo" commit --quiet -F "$work/commit-msg.txt"'),
        ("the branch checkout", 'if ! git -C "$repo" checkout --quiet -B "$branch"'),
    ):
        check(
            f"script: {label} is guarded so one candidate cannot abort the loop",
            guard in script,
            guard,
        )
    # Nothing inside the loop may exit: one candidate's failure has to leave the
    # rest of the list to be processed, each with its own comment. The only
    # `exit 1` is the final one, after the loop, which is what makes the job red.
    body = script.split("while IFS=", 1)[1].split("done 3<", 1)[0]
    check(
        "script: no per-candidate path exits the script from inside the loop",
        "exit " not in body and body.count("continue") >= 10,
        str(body.count("continue")),
    )
    check(
        "script: every candidate-level failure is counted",
        script.count("errors=$((errors + 1))") >= 10,
        str(script.count("errors=$((errors + 1))")),
    )


def main() -> int:
    _RESULTS.clear()
    test_the_engine_row_is_never_prepared()
    test_a_patched_overlay_is_refused_and_says_why()
    test_patch_eligibility_reads_the_overlay_rather_than_a_list()
    test_a_cohort_coupled_pin_is_refused()
    test_dict_is_the_one_row_prepared_today()
    test_no_refusal_is_silent()
    test_a_report_the_registry_does_not_recognise_is_refused()
    test_the_version_scheme_is_derived_from_the_rows_own_pin()
    test_an_ambiguous_version_scheme_is_refused_not_guessed()
    test_the_archive_url_substitution_must_be_unambiguous()
    test_the_branch_name_follows_the_plan()
    test_the_plan_records_what_a_deliberate_repin_records()
    test_the_plan_never_names_an_evidence_path()
    test_apply_rewrites_only_the_named_fields_and_keeps_the_comments()
    test_apply_refuses_to_write_evidence()
    test_apply_refuses_when_the_registry_moved_under_the_plan()
    test_apply_refuses_a_missing_runtime_value()
    test_edit_field_needs_exactly_one_match()
    test_the_pr_body_says_observed_is_not_tested()
    test_the_pr_body_states_the_facts_and_the_human_checklist()
    test_the_pr_body_is_deterministic()
    test_the_issue_title_comes_from_the_watcher()
    test_issue_lookup_prefers_an_open_issue_and_reports_absence()
    test_a_report_that_is_not_a_watch_report_is_refused()
    test_the_observed_commit_comes_from_the_same_observation()
    test_the_watcher_writes_a_report_the_tool_can_read()
    test_the_workflow_wires_the_job_the_way_the_decision_requires()
    test_nothing_the_job_needs_lives_where_the_tree_reset_would_delete_it()
    test_the_script_treats_the_abnormal_cases_as_errors()

    failed = 0
    for name, ok, detail in _RESULTS:
        if ok:
            print(f"PASS  {name}")
        else:
            failed += 1
            print(f"FAIL  {name}" + (f"\n      {detail}" if detail else ""))
    print(f"\n# TOTAL: {len(_RESULTS)}\n# PASS:  {len(_RESULTS) - failed}\n# FAIL:  {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
