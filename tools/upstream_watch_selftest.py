#!/usr/bin/env python3
"""Tests for tools/upstream_watch.py.

Run directly, or via `python3 tools/upstream_watch.py selftest`, or through the
`ci_matrix.py selftest` chain, which is what the CI structural-validation gate
invokes.

Nothing here touches the network. Every remote listing is canned through
`--transcript`, which exists for exactly this reason: a freshness checker whose
tests need the internet is a checker whose tests are skipped the first time a
runner has no egress.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import upstream_watch as uw  # noqa: E402

_RESULTS: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(condition), detail))


# Every hermetic test runs with an EMPTY fleet roster, so the battery keeps
# describing exactly the packaged rows unless a test opts into fleet rows by
# writing its own roster. The real committed roster gets its own tests below.
_FLEET_DIR = tempfile.mkdtemp(prefix="uw-selftest-")
_EMPTY_FLEET = Path(_FLEET_DIR) / "empty-fleet.json"
_EMPTY_FLEET.write_text(
    json.dumps({"schema": uw.FLEET_ROSTER_SCHEMA, "rows": []}), encoding="utf-8"
)


def run_check(**kwargs):
    kwargs.setdefault("fleet_path", _EMPTY_FLEET)
    return uw.check(**kwargs)


def _fleet_roster(tmp: Path, rows: list) -> Path:
    path = tmp / "fleet.json"
    path.write_text(json.dumps({"schema": uw.FLEET_ROSTER_SCHEMA, "rows": rows}), encoding="utf-8")
    return path


# The three real clone URLs, derived the way the tool derives them, so a change
# to the derivation or to a manifest breaks these tests rather than sliding past.
CACHETAG_URL = "https://github.com/boffinate/libvmod-cachetag.git"
DICT_URL = "https://git.gnu.org.ua/vmod-dict.git"
REDIS_URL = "https://github.com/carlosabalde/libvmod-redis.git"

CACHETAG_PIN = "a3897aaccf1d6996c00ee14b2c6e1ddac91ac982"
DICT_PIN = "784584d272894a39cf995377618aad551a196424"
REDIS_PIN = "b6ca669fc9af3399f3845d9d4930683b4e378aa8"

# The engine's release pin, hardcoded like the VMOD pins above and for the same
# reason: it must equal both the tool's VINYL_RELEASE_TAG/COMMIT constants and
# the pins.env release block, so an edit to any one of them alone fails here.
VINYL_RELEASE_TAG = "vinyl-cache-9.0.1"
VINYL_RELEASE_PIN = "423648c4cb6b225b3268ffc337354ea938f5efee"

VINYL_HEAD = "1111111111111111111111111111111111111111"
CACHETAG_MAIN = "2222222222222222222222222222222222222222"
DICT_MASTER = "4444444444444444444444444444444444444444"
REDIS_MAIN = "6666666666666666666666666666666666666666"

# The last-seen state of a fleet where nothing has moved: every watched branch
# at the sha the healthy transcript publishes. Tag rows are stateless.
HEALTHY_REFS = {
    "vinyl-trunk": VINYL_HEAD,
    "cachetag/trunk": CACHETAG_MAIN,
    "dict/trunk": DICT_MASTER,
    "redis/trunk": REDIS_MAIN,
}


def _listing(lines) -> str:
    return "".join(f"{sha}\t{ref}\n" for sha, ref in lines)


def _transcript(**overrides) -> dict:
    """A healthy fleet: every pin peels, no newer tags, nothing moved."""
    remotes = {
        uw.VINYL_TRUNK_URL: _listing(
            [
                (VINYL_HEAD, "HEAD"),
                (VINYL_HEAD, "refs/heads/master"),
                ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", f"refs/tags/{VINYL_RELEASE_TAG}"),
                (VINYL_RELEASE_PIN, f"refs/tags/{VINYL_RELEASE_TAG}^{{}}"),
            ]
        ),
        CACHETAG_URL: _listing(
            [
                (CACHETAG_MAIN, "HEAD"),
                (CACHETAG_MAIN, "refs/heads/main"),
                ("3333333333333333333333333333333333333333", "refs/tags/v1.0.1"),
                (CACHETAG_PIN, "refs/tags/v1.0.1^{}"),
            ]
        ),
        DICT_URL: _listing(
            [
                (DICT_MASTER, "HEAD"),
                (DICT_MASTER, "refs/heads/master"),
                ("5555555555555555555555555555555555555555", "refs/tags/v1.7"),
                (DICT_PIN, "refs/tags/v1.7^{}"),
            ]
        ),
        REDIS_URL: _listing(
            [
                (REDIS_MAIN, "HEAD"),
                (REDIS_MAIN, "refs/heads/main"),
                ("7777777777777777777777777777777777777777", "refs/tags/9.0-23.1"),
                (REDIS_PIN, "refs/tags/9.0-23.1^{}"),
            ]
        ),
    }
    remotes.update(overrides)
    return remotes


def _state(tmp: Path, refs: dict, tags: dict = None) -> Path:
    path = tmp / "state.json"
    body = {
        "schema": uw.STATE_SCHEMA,
        "refs": {k: {"url": "", "ref": "", "sha": v} for k, v in refs.items()},
        "trunk_engine_run_id": "",
    }
    if tags is not None:
        body["tags"] = tags
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --- parsing and ordering --------------------------------------------------


def test_ls_remote_parsing() -> None:
    refs = uw.parse_ls_remote(
        "abc\tnot-a-sha\n"
        + _listing([(VINYL_HEAD, "HEAD"), (CACHETAG_PIN, "refs/tags/v1.0.1^{}")])
        + "\n   \n"
    )
    check(
        "parse: well-formed lines only, malformed and blank ignored",
        refs == {"HEAD": VINYL_HEAD, "refs/tags/v1.0.1^{}": CACHETAG_PIN},
        str(refs),
    )


def test_peeling_prefers_the_peeled_entry() -> None:
    annotated = {"refs/tags/v1": "aa" * 20, "refs/tags/v1^{}": "bb" * 20}
    lightweight = {"refs/tags/v1": "cc" * 20}
    check(
        "peel: an annotated tag resolves through its ^{} entry",
        uw.peeled(annotated, "v1") == "bb" * 20,
    )
    check(
        "peel: a lightweight tag resolves through the tag ref itself",
        uw.peeled(lightweight, "v1") == "cc" * 20,
    )
    check("peel: an absent tag is the empty string", uw.peeled({}, "v1") == "")


def test_version_ordering_covers_every_shape_in_the_fleet() -> None:
    # The three real shapes. redis tags <varnish-series>-<vmod-version>, which
    # is why the key is every numeric run rather than a dotted triple.
    refs = {
        "refs/tags/v1.0.1": "", "refs/tags/v1.0.2": "", "refs/tags/v1.1.0": "",
        "refs/tags/v1.0.0": "", "refs/tags/v1.0.2-rc1": "", "refs/tags/nightly": "",
    }
    cands, info = uw.newer_tags(refs, "v1.0.1")
    check(
        "order: only stable tags above the pin are candidates, newest first",
        cands == ["v1.1.0", "v1.0.2"],
        str(cands),
    )
    check(
        "order: a pre-release above the pin is informational, never a candidate",
        info == ["v1.0.2-rc1"],
        str(info),
    )
    redis = {"refs/tags/9.0-23.1": "", "refs/tags/9.0-23.2": "", "refs/tags/9.1-24.0": "",
             "refs/tags/9.0-23.0": ""}
    check(
        "order: redis's series-and-version tags order correctly",
        uw.newer_tags(redis, "9.0-23.1")[0] == ["9.1-24.0", "9.0-23.2"],
        str(uw.newer_tags(redis, "9.0-23.1")),
    )
    mixed = {"refs/tags/v1.7": "", "refs/tags/v2": "", "refs/tags/v1.8": ""}
    check(
        "order: a shorter tag is compared by zero-padding, not silently dropped",
        uw.newer_tags(mixed, "v1.7")[0] == ["v2", "v1.8"],
        str(uw.newer_tags(mixed, "v1.7")),
    )
    check(
        "order: a pin with no digits yields no candidates",
        uw.newer_tags(mixed, "main") == ([], []),
    )


def test_cross_shape_candidates_surface() -> None:
    """The plan's known heuristic gap: vinyl-cache-9.1 vs vinyl-cache-9.0.1.

    The old rule compared only same-component-count tags, so a twice-yearly
    major release with fewer components would never have surfaced. Missing
    components now count as zero.
    """
    refs = {
        f"refs/tags/{VINYL_RELEASE_TAG}": "",
        "refs/tags/vinyl-cache-9.1": "",
        "refs/tags/vinyl-cache-9.0.0.1": "",
    }
    cands, info = uw.newer_tags(refs, VINYL_RELEASE_TAG)
    check(
        "cross-shape: a two-component tag surfaces above a three-component pin",
        cands == ["vinyl-cache-9.1"],
        str((cands, info)),
    )
    check(
        "cross-shape: a longer tag sorting below the pin stays below it",
        "vinyl-cache-9.0.0.1" not in cands,
        str(cands),
    )
    check(
        "cross-shape: equal after zero-padding is not newer",
        uw.newer_tags({"refs/tags/v1.0": ""}, "v1.0.0") == ([], []),
        str(uw.newer_tags({"refs/tags/v1.0": ""}, "v1.0.0")),
    )
    # And end to end through the check: the engine remote publishing 9.1
    # surfaces it as a re-pin candidate on the engine row.
    newer = _transcript(
        **{
            uw.VINYL_TRUNK_URL: _listing(
                [
                    (VINYL_HEAD, "HEAD"),
                    (VINYL_HEAD, "refs/heads/master"),
                    ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", f"refs/tags/{VINYL_RELEASE_TAG}"),
                    (VINYL_RELEASE_PIN, f"refs/tags/{VINYL_RELEASE_TAG}^{{}}"),
                    ("bb" * 20, "refs/tags/vinyl-cache-9.1"),
                ]
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=newer)
    check(
        "cross-shape: the major release reaches the report as a candidate",
        report["repin_candidates"]
        == [{"vmod": uw.VINYL_RELEASE_KEY, "pinned": VINYL_RELEASE_TAG, "tag": "vinyl-cache-9.1"}],
        str(report["repin_candidates"]),
    )


def test_stable_grammar_is_derived_from_the_pins_own_scheme() -> None:
    """Pre-release-shaped and foreign-family tags never pollute candidates."""
    refs = {
        "refs/tags/v1.0.1": "",
        "refs/tags/v1.1.0-rc1": "",
        "refs/tags/v1.1.0beta2": "",
        "refs/tags/v2.0.0.dev0": "",
        "refs/tags/banana-2.0": "",
        "refs/tags/v1.2.0": "",
    }
    cands, info = uw.newer_tags(refs, "v1.0.1")
    check("grammar: the one stable tag is the one candidate", cands == ["v1.2.0"], str(cands))
    check(
        "grammar: suffixed tags in the family are informational only",
        set(info) == {"v1.1.0-rc1", "v1.1.0beta2", "v2.0.0.dev0"},
        str(info),
    )
    check(
        "grammar: a foreign-family tag is neither candidate nor informational",
        "banana-2.0" not in cands and "banana-2.0" not in info,
        str((cands, info)),
    )
    import re as _re

    pattern = uw.stable_tag_re("vinyl-cache-9.0.1")
    check(
        "grammar: the derived pattern accepts the pin's own scheme and refuses suffixes",
        bool(_re.fullmatch(pattern, "vinyl-cache-9.1"))
        and not _re.fullmatch(pattern, "vinyl-cache-9.1-rc1")
        and not _re.fullmatch(pattern, "other-9.1"),
        pattern,
    )


# --- the check -------------------------------------------------------------


def test_healthy_fleet_with_state_does_not_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=_transcript())
    check("healthy: every pin still peels", report["ok"] and not report["moved_pins"], str(report["moved_pins"]))
    check("healthy: nothing moved, so the gate says do not run", report["run"] is False, str(report))
    check("healthy: no VMOD is marked changed", report["changed_vmods"] == [], str(report["changed_vmods"]))
    check("healthy: no re-pin candidates", report["repin_candidates"] == [], str(report["repin_candidates"]))
    check(
        "healthy: Vinyl trunk HEAD is reported whether or not it moved",
        report["vinyl_head_sha"] == VINYL_HEAD,
        report["vinyl_head_sha"],
    )
    # All three VMODs on both channels, plus the engine twice: trunk HEAD and
    # the pinned release tag.
    check(
        "healthy: the watch list is derived from the catalog",
        sorted(e["key"] for e in report["entries"])
        == [
            "cachetag/release",
            "cachetag/trunk",
            "dict/release",
            "dict/trunk",
            "redis/release",
            "redis/trunk",
            uw.VINYL_RELEASE_KEY,
            uw.VINYL_KEY,
        ],
        str(sorted(e["key"] for e in report["entries"])),
    )


def test_a_moved_tag_is_a_failure_and_never_a_candidate() -> None:
    moved = _transcript(
        **{
            DICT_URL: _listing(
                [
                    ("4444444444444444444444444444444444444444", "HEAD"),
                    ("5555555555555555555555555555555555555555", "refs/tags/v1.7"),
                    ("dead" + "0" * 36, "refs/tags/v1.7^{}"),
                ]
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=moved)
    check("moved pin: the report is not ok", report["ok"] is False)
    check("moved pin: it names the ref", report["moved_pins"] == ["dict/release"], str(report["moved_pins"]))
    check(
        "moved pin: it is NOT surfaced as a re-pin candidate",
        report["repin_candidates"] == [],
        str(report["repin_candidates"]),
    )
    entry = next(e for e in report["entries"] if e["key"] == "dict/release")
    check(
        "moved pin: the detail names both commits",
        "not the recorded" in entry["detail"] and DICT_PIN in entry["detail"],
        entry["detail"],
    )
    text = uw.render_text(report)
    check(
        "moved pin: the text output refuses to suggest updating the manifest",
        "do not update the manifest to make this pass" in text and "FAILURE" in text,
        text[-300:],
    )


def test_a_missing_tag_is_the_same_failure() -> None:
    gone = _transcript(**{REDIS_URL: _listing([("66" * 20, "HEAD")])})
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=gone)
    check("missing tag: reported as a moved pin, not as a candidate", report["moved_pins"] == ["redis/release"])
    check("missing tag: the report is not ok", report["ok"] is False)


def test_newer_tags_are_candidates_and_do_not_gate() -> None:
    newer = _transcript(
        **{
            CACHETAG_URL: _listing(
                [
                    (CACHETAG_MAIN, "HEAD"),
                    (CACHETAG_MAIN, "refs/heads/main"),
                    ("33" * 20, "refs/tags/v1.0.1"),
                    (CACHETAG_PIN, "refs/tags/v1.0.1^{}"),
                    ("88" * 20, "refs/tags/v1.1.0"),
                ]
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=newer)
    check(
        "candidates: a newer tag is surfaced against the pin it beats",
        report["repin_candidates"] == [{"vmod": "cachetag", "pinned": "v1.0.1", "tag": "v1.1.0"}],
        str(report["repin_candidates"]),
    )
    check("candidates: the pin itself is untouched and still ok", report["ok"] is True)
    # A re-pin candidate is not a change-gate signal: the lane still builds the
    # pinned tag, so nothing about the run's inputs has moved.
    check("candidates: they do not make the gate run", report["run"] is False, str(report))
    gh = uw.render_github(report)
    check(
        "candidates: github format annotates them as notices, not errors",
        "::notice title=re-pin candidate::cachetag publishes v1.1.0" in gh and "::error" not in gh,
        gh,
    )
    check(
        "candidates: the annotation says a pin moves deliberately",
        "a pin moves deliberately" in gh,
        gh,
    )


def test_a_moved_trunk_branch_gates_that_vmod() -> None:
    moved = _transcript(
        **{
            CACHETAG_URL: _listing(
                [
                    ("99" * 20, "HEAD"),
                    ("99" * 20, "refs/heads/main"),
                    ("33" * 20, "refs/tags/v1.0.1"),
                    (CACHETAG_PIN, "refs/tags/v1.0.1^{}"),
                ]
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=moved)
    check("branch: the VMOD whose branch moved is the changed one", report["changed_vmods"] == ["cachetag"])
    check("branch: Vinyl trunk did not move", report["vinyl_changed"] is False)
    check(
        "branch: the gate runs, which is decision (a)'s 'even if trunk unchanged'",
        report["run"] is True,
    )
    entry = next(e for e in report["entries"] if e["key"] == "cachetag/trunk")
    check("branch: the previous sha is reported alongside the new one", entry["previous_sha"] == CACHETAG_MAIN)


def test_a_moved_vinyl_release_tag_is_the_same_loud_failure() -> None:
    moved = _transcript(
        **{
            uw.VINYL_TRUNK_URL: _listing(
                [
                    (VINYL_HEAD, "HEAD"),
                    (VINYL_HEAD, "refs/heads/master"),
                    ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", f"refs/tags/{VINYL_RELEASE_TAG}"),
                    ("dead" + "0" * 36, f"refs/tags/{VINYL_RELEASE_TAG}^{{}}"),
                ]
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=moved)
    check("vinyl tag: a moved engine release tag fails the report", report["ok"] is False)
    check(
        "vinyl tag: moved_pins names the engine row",
        report["moved_pins"] == [uw.VINYL_RELEASE_KEY],
        str(report["moved_pins"]),
    )
    check(
        "vinyl tag: it is NOT surfaced as a re-pin candidate",
        report["repin_candidates"] == [],
        str(report["repin_candidates"]),
    )
    entry = next(e for e in report["entries"] if e["key"] == uw.VINYL_RELEASE_KEY)
    check(
        "vinyl tag: the detail names both commits",
        "not the recorded" in entry["detail"] and VINYL_RELEASE_PIN in entry["detail"],
        entry["detail"],
    )
    check(
        "vinyl tag: trunk did not move, so the gate itself is unaffected",
        report["vinyl_changed"] is False and report["run"] is False,
        str(report),
    )


def test_a_newer_vinyl_release_tag_is_a_candidate() -> None:
    newer = _transcript(
        **{
            uw.VINYL_TRUNK_URL: _listing(
                [
                    (VINYL_HEAD, "HEAD"),
                    (VINYL_HEAD, "refs/heads/master"),
                    ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", f"refs/tags/{VINYL_RELEASE_TAG}"),
                    (VINYL_RELEASE_PIN, f"refs/tags/{VINYL_RELEASE_TAG}^{{}}"),
                    ("bb" * 20, "refs/tags/vinyl-cache-9.0.2"),
                    ("cc" * 20, "refs/tags/vinyl-cache-9.1.0-rc1"),
                ]
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=newer)
    check(
        "vinyl candidates: a newer engine release surfaces against the watch key",
        report["repin_candidates"]
        == [{"vmod": uw.VINYL_RELEASE_KEY, "pinned": VINYL_RELEASE_TAG, "tag": "vinyl-cache-9.0.2"}],
        str(report["repin_candidates"]),
    )
    check("vinyl candidates: the pin itself is untouched and still ok", report["ok"] is True)
    check("vinyl candidates: they do not make the gate run", report["run"] is False, str(report))
    gh = uw.render_github(report)
    check(
        "vinyl candidates: the github notice is labelled with the engine key",
        "::notice title=re-pin candidate::vinyl-release publishes vinyl-cache-9.0.2" in gh
        and "::error" not in gh,
        gh,
    )


def test_moved_dict_and_redis_trunk_branches_gate_their_vmods() -> None:
    moved = _transcript(
        **{
            DICT_URL: _listing(
                [
                    ("88" * 20, "HEAD"),
                    ("88" * 20, "refs/heads/master"),
                    ("5555555555555555555555555555555555555555", "refs/tags/v1.7"),
                    (DICT_PIN, "refs/tags/v1.7^{}"),
                ]
            ),
            REDIS_URL: _listing(
                [
                    ("99" * 20, "HEAD"),
                    ("99" * 20, "refs/heads/main"),
                    ("7777777777777777777777777777777777777777", "refs/tags/9.0-23.1"),
                    (REDIS_PIN, "refs/tags/9.0-23.1^{}"),
                ]
            ),
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=moved)
    check(
        "branch: dict and redis are the changed VMODs",
        report["changed_vmods"] == ["dict", "redis"],
        str(report["changed_vmods"]),
    )
    check("branch: their pins are untouched", report["ok"] is True and report["repin_candidates"] == [])
    check("branch: the gate runs", report["run"] is True)
    check("branch: Vinyl trunk did not move", report["vinyl_changed"] is False)
    entry = next(e for e in report["entries"] if e["key"] == "dict/trunk")
    check("branch: dict/trunk reports the previous sha", entry["previous_sha"] == DICT_MASTER)
    entry = next(e for e in report["entries"] if e["key"] == "redis/trunk")
    check("branch: redis/trunk reports the previous sha", entry["previous_sha"] == REDIS_MAIN)


def test_a_state_predating_the_new_rows_does_not_false_alarm() -> None:
    """Backward compatibility with state recorded before these rows existed.

    The orphan-branch state file knows nothing of dict/trunk, redis/trunk or
    the engine release row. The tag row must not false-alarm -- question (a) is
    answered against the recorded pin, never against the state -- and the new
    branch rows count as changed exactly once, which is the fail-open rule
    doing its job rather than a failure.
    """
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), {uw.VINYL_KEY: VINYL_HEAD, "cachetag/trunk": CACHETAG_MAIN})
        report = run_check(state_path=state, transcript=_transcript())
    check("compat: no moved pin is invented from missing state", report["ok"] is True, str(report["moved_pins"]))
    check(
        "compat: the unrecorded branch rows count as changed, once",
        report["changed_vmods"] == ["dict", "redis"] and report["run"] is True,
        str(report["changed_vmods"]),
    )
    check("compat: known rows are still compared, not reset", report["vinyl_changed"] is False)
    entry = next(e for e in report["entries"] if e["key"] == uw.VINYL_RELEASE_KEY)
    check("compat: the engine release row is simply ok", entry["status"] == "ok", str(entry))
    check(
        "compat: a state predating the tags map poisons nothing",
        report["poisoned_tags"] == [] and report["ok"] is True,
        str(report["poisoned_tags"]),
    )


def test_a_moved_vinyl_trunk_gates_everything() -> None:
    moved = _transcript(**{uw.VINYL_TRUNK_URL: _listing([("ab" * 20, "HEAD")])})
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=moved)
    check("engine: vinyl_changed is set", report["vinyl_changed"] is True)
    check("engine: the new head is reported", report["vinyl_head_sha"] == "ab" * 20)
    check("engine: the gate runs", report["run"] is True)
    check("engine: no VMOD is blamed for it", report["changed_vmods"] == [], str(report["changed_vmods"]))


def test_state_failures_fail_open() -> None:
    healthy = _transcript()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cases = {
            "absent": root / "nope.json",
            "unparseable": root / "bad.json",
            "wrong schema": root / "wrong.json",
        }
        cases["unparseable"].write_text("{ not json", encoding="utf-8")
        cases["wrong schema"].write_text(json.dumps({"schema": "other/v1"}), encoding="utf-8")
        for label, path in cases.items():
            report = run_check(state_path=path, transcript=healthy)
            check(
                f"fail-open: a {label} state file still runs",
                report["run"] is True and report["stateless"] is True,
                str(report["state_note"]),
            )
            check(
                f"fail-open: a {label} state file says so in the output",
                bool(report["state_note"]) and report["state_note"] in uw.render_text(report),
                report["state_note"],
            )
        report = run_check(state_path=None, transcript=healthy)
        check("fail-open: no --state at all runs too", report["run"] is True)


def test_an_unreachable_remote_runs_rather_than_skips() -> None:
    broken = _transcript()
    del broken[DICT_URL]
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=broken)
    entry = next(e for e in report["entries"] if e["key"] == "dict/release")
    check("unreachable: the entry says so", entry["status"] == "unreachable", entry["detail"])
    check("unreachable: its VMOD counts as changed", "dict" in report["changed_vmods"])
    check("unreachable: the gate runs rather than skipping", report["run"] is True)
    check("unreachable: it is not a moved pin", report["ok"] is True, str(report["moved_pins"]))
    gh = uw.render_github(report)
    check("unreachable: github format warns rather than errors", "::warning title=upstream unreachable" in gh, gh)


# --- poisoned tags ---------------------------------------------------------


def _cachetag_with_v110(sha: str) -> dict:
    return _transcript(
        **{
            CACHETAG_URL: _listing(
                [
                    (CACHETAG_MAIN, "HEAD"),
                    (CACHETAG_MAIN, "refs/heads/main"),
                    ("33" * 20, "refs/tags/v1.0.1"),
                    (CACHETAG_PIN, "refs/tags/v1.0.1^{}"),
                    (sha, "refs/tags/v1.1.0"),
                ]
            )
        }
    )


def test_a_retagged_name_is_poisoned_and_never_a_candidate() -> None:
    """A tag first seen at one commit and later at another is permanently bad."""
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(
            Path(tmp),
            dict(HEALTHY_REFS),
            tags={"cachetag/release": {"v1.1.0": {"sha": "99" * 20}}},
        )
        report = run_check(state_path=state, transcript=_cachetag_with_v110("88" * 20))
    check("poison: the report is not ok", report["ok"] is False)
    check(
        "poison: the tag is named with both commits",
        report["poisoned_tags"]
        == [
            {
                "key": "cachetag/release",
                "vmod": "cachetag",
                "tag": "v1.1.0",
                "first_seen": "99" * 20,
                "now": "88" * 20,
            }
        ],
        str(report["poisoned_tags"]),
    )
    check(
        "poison: a poisoned name is NOT a re-pin candidate, however stable it looks",
        report["repin_candidates"] == [],
        str(report["repin_candidates"]),
    )
    check(
        "poison: the pin itself is untouched, so this is not a moved pin",
        report["moved_pins"] == [],
        str(report["moved_pins"]),
    )
    text = uw.render_text(report)
    check(
        "poison: the text output says permanently distrusted",
        "POISONED" in text and "permanently distrusted" in text and "FAILURE" in text,
        text[-400:],
    )
    gh = uw.render_github(report)
    check(
        "poison: github format errors and notifies",
        "::error title=poisoned tag::cachetag/release" in gh and "notify=true" in gh,
        gh,
    )
    state_after = uw.next_state(report)
    check(
        "poison: the memory keeps the FIRST-seen commit and the poisoned flag",
        state_after["tags"]["cachetag/release"]["v1.1.0"] == {"sha": "99" * 20, "poisoned": True},
        str(state_after["tags"].get("cachetag/release")),
    )


def test_poison_persists_after_the_tag_returns() -> None:
    """Even back at the original commit, a once-moved name stays distrusted."""
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(
            Path(tmp),
            dict(HEALTHY_REFS),
            tags={"cachetag/release": {"v1.1.0": {"sha": "88" * 20, "poisoned": True}}},
        )
        report = run_check(state_path=state, transcript=_cachetag_with_v110("88" * 20))
    check("poison return: still not ok", report["ok"] is False)
    check(
        "poison return: still not a candidate",
        report["repin_candidates"] == [],
        str(report["repin_candidates"]),
    )
    state_after = uw.next_state(report)
    check(
        "poison return: the flag survives the next state",
        state_after["tags"]["cachetag/release"]["v1.1.0"].get("poisoned") is True,
        str(state_after["tags"].get("cachetag/release")),
    )


def test_the_pinned_tags_memory_is_the_recorded_commit() -> None:
    """The pin's first-seen is the manifest commit, so a moved pin poisons and a
    deliberate re-pin (recorded commit changed) resets."""
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        healthy = run_check(state_path=state, transcript=_transcript())
    seeded = uw.next_state(healthy)
    check(
        "pin memory: a healthy pin is recorded at the manifest commit, unpoisoned",
        seeded["tags"]["dict/release"]["v1.7"] == {"sha": DICT_PIN},
        str(seeded["tags"].get("dict/release")),
    )
    moved = _transcript(
        **{
            DICT_URL: _listing(
                [
                    (DICT_MASTER, "HEAD"),
                    (DICT_MASTER, "refs/heads/master"),
                    ("5555555555555555555555555555555555555555", "refs/tags/v1.7"),
                    ("dead" + "0" * 36, "refs/tags/v1.7^{}"),
                ]
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=moved)
    check(
        "pin memory: a moved pin is poisoned against the RECORDED commit",
        any(
            p["tag"] == "v1.7" and p["first_seen"] == DICT_PIN for p in report["poisoned_tags"]
        ),
        str(report["poisoned_tags"]),
    )
    state_after = uw.next_state(report)
    check(
        "pin memory: the moved pin's poison is persisted",
        state_after["tags"]["dict/release"]["v1.7"] == {"sha": DICT_PIN, "poisoned": True},
        str(state_after["tags"].get("dict/release")),
    )


def test_a_deliberate_repin_resets_the_memory() -> None:
    """A poison record for a PREVIOUS pin commit does not outlive the re-pin."""
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(
            Path(tmp),
            dict(HEALTHY_REFS),
            tags={"dict/release": {"v1.7": {"sha": "ab" * 20, "poisoned": True}}},
        )
        report = run_check(state_path=state, transcript=_transcript())
    check("re-pin: the report is ok again", report["ok"] is True, str(report["poisoned_tags"]))
    check("re-pin: nothing is poisoned", report["poisoned_tags"] == [])
    state_after = uw.next_state(report)
    check(
        "re-pin: the memory now records the new pin commit, unpoisoned",
        state_after["tags"]["dict/release"]["v1.7"] == {"sha": DICT_PIN},
        str(state_after["tags"].get("dict/release")),
    )


# --- the watch-only fleet --------------------------------------------------


FLEET_URL = "https://example.test/libvmod-awsrest.git"


def _fleet_row(tmp: Path, extra_rows: list = None) -> Path:
    rows = [{"id": "awsrest", "url": FLEET_URL, "watch": True}]
    rows += extra_rows or []
    return _fleet_roster(tmp, rows)


def _fleet_listing(tags: dict) -> str:
    lines = [("aa" * 20, "HEAD"), ("aa" * 20, "refs/heads/master")]
    for tag, sha in tags.items():
        lines.append((sha, f"refs/tags/{tag}"))
    return _listing(lines)


def test_fleet_first_observation_seeds_silently() -> None:
    transcript = _transcript(**{FLEET_URL: _fleet_listing({"v70.6": "bb" * 20})})
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        roster = _fleet_row(tmp, [{"id": "ancient", "url": "https://example.test/x.git", "watch": False}])
        state = _state(tmp, dict(HEALTHY_REFS))
        report = uw.check(state_path=state, transcript=transcript, fleet_path=roster)
    check(
        "fleet seed: the enabled row is watched, the disabled row is not",
        [e["key"] for e in report["entries"] if e["kind"] == "fleet"] == ["fleet/awsrest"],
        str([e["key"] for e in report["entries"]]),
    )
    check(
        "fleet seed: first observation announces nothing",
        report["fleet_candidates"] == [] and report["fleet_seeded"] == ["fleet/awsrest"],
        str(report),
    )
    check("fleet seed: the gate is untouched", report["run"] is False and report["ok"] is True)
    state_after = uw.next_state(report)
    check(
        "fleet seed: the tag set is recorded for next time",
        state_after["tags"]["fleet/awsrest"] == {"v70.6": {"sha": "bb" * 20}},
        str(state_after["tags"].get("fleet/awsrest")),
    )


def test_fleet_new_stable_tags_surface_and_never_gate() -> None:
    transcript = _transcript(
        **{
            FLEET_URL: _fleet_listing(
                {"v70.6": "bb" * 20, "v70.7": "cc" * 20, "v71.0-rc1": "dd" * 20}
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        roster = _fleet_row(tmp)
        state = _state(
            tmp,
            dict(HEALTHY_REFS),
            tags={"fleet/awsrest": {"v70.6": {"sha": "bb" * 20}}},
        )
        report = uw.check(state_path=state, transcript=transcript, fleet_path=roster)
    check(
        "fleet: the new stable tag is an informational candidate",
        report["fleet_candidates"]
        == [{"id": "awsrest", "key": "fleet/awsrest", "tag": "v70.7", "url": FLEET_URL}],
        str(report["fleet_candidates"]),
    )
    check(
        "fleet: the rc-shaped tag is not announced",
        all(c["tag"] != "v71.0-rc1" for c in report["fleet_candidates"]),
        str(report["fleet_candidates"]),
    )
    check(
        "fleet: candidates never gate, never blame a VMOD, never fail the run",
        report["run"] is False and report["changed_vmods"] == [] and report["ok"] is True,
        str(report),
    )
    gh = uw.render_github(report)
    check(
        "fleet: github format notifies with a notice, not an error",
        "notify=true" in gh
        and "fleet_candidates=awsrest:v70.7" in gh
        and "::notice title=fleet candidate::awsrest publishes v70.7" in gh
        and "::error" not in gh,
        gh,
    )
    check(
        "fleet: no per-ref sha output is emitted for fleet rows",
        "sha_fleet_awsrest" not in gh,
        gh,
    )
    state_after = uw.next_state(report)
    check(
        "fleet: the announced tag joins the record, so it is announced once",
        set(state_after["tags"]["fleet/awsrest"]) == {"v70.6", "v70.7"},
        str(state_after["tags"].get("fleet/awsrest")),
    )


def test_fleet_unreachable_is_a_warning_not_a_gate_signal() -> None:
    transcript = _transcript()  # no listing for FLEET_URL at all
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        roster = _fleet_row(tmp)
        state = _state(tmp, dict(HEALTHY_REFS))
        report = uw.check(state_path=state, transcript=transcript, fleet_path=roster)
    entry = next(e for e in report["entries"] if e["key"] == "fleet/awsrest")
    check("fleet unreachable: the entry says so", entry["status"] == "unreachable")
    check(
        "fleet unreachable: the gate does NOT run for a watch-only row",
        report["run"] is False and report["changed_vmods"] == [] and report["ok"] is True,
        str(report),
    )
    check(
        "fleet unreachable: github warns",
        "::warning title=upstream unreachable::fleet/awsrest" in uw.render_github(report),
        uw.render_github(report),
    )


def test_fleet_seeding_an_upstream_with_no_stable_tags_still_marks_it_seen() -> None:
    empty = _transcript(**{FLEET_URL: _fleet_listing({})})
    first_tag = _transcript(**{FLEET_URL: _fleet_listing({"v1.0": "ee" * 20})})
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        roster = _fleet_row(tmp)
        state = _state(tmp, dict(HEALTHY_REFS))
        seeded = uw.next_state(uw.check(state_path=state, transcript=empty, fleet_path=roster))
        check(
            "fleet empty seed: the key exists with no tags",
            seeded["tags"].get("fleet/awsrest") == {},
            str(seeded["tags"]),
        )
        state2 = tmp / "state2.json"
        state2.write_text(json.dumps(seeded), encoding="utf-8")
        report = uw.check(state_path=state2, transcript=first_tag, fleet_path=roster)
    check(
        "fleet empty seed: the first real tag is announced, not silently swallowed",
        [c["tag"] for c in report["fleet_candidates"]] == ["v1.0"],
        str(report["fleet_candidates"]),
    )


def test_the_committed_roster_loads_and_stays_clear_of_the_pinned_rows() -> None:
    rows = uw.load_fleet()
    urls = {row["url"] for row in rows}
    check("roster: it loads and is not small", len(rows) >= 40, str(len(rows)))
    check(
        "roster: the pinned packaged upstreams are not watched twice",
        DICT_URL not in urls and REDIS_URL not in urls and CACHETAG_URL not in urls,
        str(sorted(urls)),
    )
    ids = [row["id"] for row in rows]
    check("roster: ids are unique", len(ids) == len(set(ids)), str(ids))
    check(
        "roster: no id smuggles a slash into the watch key",
        all("/" not in rid for rid in ids),
        str([r for r in ids if "/" in r]),
    )


# --- notification issues ---------------------------------------------------


def test_issues_for_pinned_rows_are_one_per_upstream_and_tag() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        healthy = run_check(state_path=state, transcript=_transcript())
    check("issues: a healthy run raises none", uw.render_issues(healthy) == [], str(uw.render_issues(healthy)))

    newer = _cachetag_with_v110("88" * 20)
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=newer)
    issues = uw.render_issues(report)
    check(
        "issues: a re-pin candidate gets one issue titled by upstream and tag",
        [i["title"] for i in issues] == ["upstream-watch: re-pin candidate cachetag v1.1.0"],
        str([i["title"] for i in issues]),
    )
    check(
        "issues: the body says surfaced-only under the manual gate",
        "never publishes" in issues[0]["body"] and "a pin moves deliberately" in issues[0]["body"],
        issues[0]["body"],
    )
    check(
        "issues: every issue carries the watcher label",
        all(i["labels"] == [uw.ISSUE_LABEL] for i in issues),
        str(issues),
    )

    moved = _transcript(
        **{
            DICT_URL: _listing(
                [
                    (DICT_MASTER, "HEAD"),
                    (DICT_MASTER, "refs/heads/master"),
                    ("5555555555555555555555555555555555555555", "refs/tags/v1.7"),
                    ("dead" + "0" * 36, "refs/tags/v1.7^{}"),
                ]
            )
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=moved)
    issues = uw.render_issues(report)
    check(
        "issues: a moved pin gets exactly one issue, not a poisoned twin",
        [i["title"] for i in issues] == ["upstream-watch: moved pin dict/release v1.7"],
        str([i["title"] for i in issues]),
    )
    check(
        "issues: the moved-pin body refuses the manifest shortcut",
        "do not update the manifest" in issues[0]["body"],
        issues[0]["body"],
    )

    with tempfile.TemporaryDirectory() as tmp:
        state = _state(
            Path(tmp),
            dict(HEALTHY_REFS),
            tags={"cachetag/release": {"v1.1.0": {"sha": "99" * 20}}},
        )
        report = run_check(state_path=state, transcript=_cachetag_with_v110("88" * 20))
    issues = uw.render_issues(report)
    check(
        "issues: a poisoned tag that is not the pin gets its own issue",
        [i["title"] for i in issues] == ["upstream-watch: poisoned tag cachetag/release v1.1.0"],
        str([i["title"] for i in issues]),
    )


def test_fleet_candidates_collapse_into_one_digest_issue() -> None:
    transcript = _transcript(
        **{
            FLEET_URL: _fleet_listing({"v70.6": "bb" * 20, "v70.7": "cc" * 20, "v71.0": "dd" * 20})
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        roster = _fleet_row(tmp)
        state = _state(
            tmp,
            dict(HEALTHY_REFS),
            tags={"fleet/awsrest": {"v70.6": {"sha": "bb" * 20}}},
        )
        report = uw.check(state_path=state, transcript=transcript, fleet_path=roster)
    issues = uw.render_issues(report)
    check(
        "digest: two new fleet tags produce ONE digest issue",
        len(issues) == 1 and issues[0]["kind"] == "fleet_digest",
        str([i["title"] for i in issues]),
    )
    check(
        "digest: the stable rolling title is the dedupe key",
        issues[0]["title"] == uw.FLEET_DIGEST_TITLE,
        issues[0]["title"],
    )
    check(
        "digest: the body groups the upstream's tags together",
        "awsrest" in issues[0]["body"]
        and "`v71.0`" in issues[0]["body"]
        and "`v70.7`" in issues[0]["body"]
        and "maintainer" in issues[0]["body"],
        issues[0]["body"],
    )


# --- output contracts ------------------------------------------------------


def test_github_format_carries_the_gate_outputs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = run_check(state_path=state, transcript=_transcript())
    emitted = dict(
        line.split("=", 1)
        for line in uw.render_github(report).splitlines()
        if "=" in line and not line.startswith("::")
    )
    for name in (
        "run",
        "vinyl_changed",
        "vinyl_head_sha",
        "changed_vmods",
        "moved_pins",
        "poisoned_tags",
        "fleet_candidates",
        "notify",
    ):
        check(f"github: the {name} gate output is present", name in emitted, str(sorted(emitted)))
    check("github: run is a lower-case boolean the workflow can compare", emitted["run"] == "false")
    check("github: a healthy run does not notify", emitted["notify"] == "false", emitted["notify"])
    check(
        "github: a per-ref sha output exists for every watched ref",
        all(uw._gate_name(e["key"]) in emitted for e in report["entries"]),
        str(sorted(emitted)),
    )
    check(
        "github: the key becomes a legal output name",
        uw._gate_name("cachetag/trunk") == "sha_cachetag_trunk"
        and uw._gate_name(uw.VINYL_KEY) == "sha_vinyl_trunk",
    )


def test_next_state_only_records_what_was_read() -> None:
    broken = _transcript()
    del broken[REDIS_URL]
    with tempfile.TemporaryDirectory() as tmp:
        previous, _ = uw.load_state(_state(Path(tmp), {"redis/release": "old" + "0" * 37}))
        report = run_check(state_path=None, transcript=broken)
    state = uw.next_state(report, previous)
    check("state: the schema is stamped", state["schema"] == uw.STATE_SCHEMA)
    check(
        "state: an unreachable ref keeps its previous value rather than being erased",
        state["refs"]["redis/release"]["sha"] == "old" + "0" * 37,
        str(state["refs"].get("redis/release")),
    )
    check(
        "state: a ref that was read is updated",
        state["refs"][uw.VINYL_KEY]["sha"] == VINYL_HEAD,
        str(state["refs"][uw.VINYL_KEY]),
    )
    check(
        "state: the Wave 3c engine-run field is reserved and preserved",
        "trunk_engine_run_id" in state,
        str(sorted(state)),
    )


def test_the_catalog_is_the_only_source_of_urls() -> None:
    """No URL is written down here that the catalog does not produce."""
    all_targets, _ = uw.watch_targets()
    targets = [t for t in all_targets if t["kind"] != "fleet"]
    check(
        "urls: fleet targets exist, carry the fleet/ prefix, and nothing else does",
        all(t["key"].startswith(uw.FLEET_KEY_PREFIX) for t in all_targets if t["kind"] == "fleet")
        and all(not t["key"].startswith(uw.FLEET_KEY_PREFIX) for t in targets),
        str([t["key"] for t in all_targets if t["kind"] == "fleet"][:5]),
    )
    urls = {t["url"] for t in targets if t["url"]}
    check(
        "urls: the three clone URLs come from the manifests, through source_facts",
        urls == {CACHETAG_URL, DICT_URL, REDIS_URL},
        str(sorted(urls)),
    )
    # The engine's release pin is the one tag row that does NOT come from a
    # manifest; it carries the tool's own recorded pin, which this file also
    # hardcodes so the two cannot drift apart silently.
    pins = {t["key"]: t.get("expected_commit") for t in targets if t["kind"] == "tag"}
    check(
        "urls: every pinned channel carries the manifest's recorded commit",
        pins
        == {
            "cachetag/release": CACHETAG_PIN,
            "dict/release": DICT_PIN,
            "redis/release": REDIS_PIN,
            uw.VINYL_RELEASE_KEY: VINYL_RELEASE_PIN,
        },
        str(pins),
    )
    vinyl_release = next(t for t in targets if t["key"] == uw.VINYL_RELEASE_KEY)
    check(
        "urls: the engine release row watches the recorded tag on the vinyl remote",
        vinyl_release["ref"] == VINYL_RELEASE_TAG and vinyl_release["url"] is None,
        str(vinyl_release),
    )
    trunk = [t for t in targets if t["kind"] == "branch" and t["vmod"]]
    check(
        "urls: every selected VMOD's trunk branch is watched",
        [(t["key"], t["ref"]) for t in trunk]
        == [("cachetag/trunk", "main"), ("dict/trunk", "master"), ("redis/trunk", "main")],
        str([(t["key"], t["ref"]) for t in trunk]),
    )


def test_the_transcript_never_touches_the_network() -> None:
    """The mechanism the whole battery relies on, asserted rather than assumed."""
    try:
        uw.ls_remote("https://example.invalid/nope.git", {})
    except uw.WatchError as exc:
        check(
            "transcript: an unknown URL is refused rather than reaching for git",
            "no canned listing" in str(exc),
            str(exc),
        )
    else:
        check("transcript: an unknown URL is refused rather than reaching for git", False)


def main() -> int:
    _RESULTS.clear()
    test_ls_remote_parsing()
    test_peeling_prefers_the_peeled_entry()
    test_version_ordering_covers_every_shape_in_the_fleet()
    test_cross_shape_candidates_surface()
    test_stable_grammar_is_derived_from_the_pins_own_scheme()
    test_a_retagged_name_is_poisoned_and_never_a_candidate()
    test_poison_persists_after_the_tag_returns()
    test_the_pinned_tags_memory_is_the_recorded_commit()
    test_a_deliberate_repin_resets_the_memory()
    test_fleet_first_observation_seeds_silently()
    test_fleet_new_stable_tags_surface_and_never_gate()
    test_fleet_unreachable_is_a_warning_not_a_gate_signal()
    test_fleet_seeding_an_upstream_with_no_stable_tags_still_marks_it_seen()
    test_the_committed_roster_loads_and_stays_clear_of_the_pinned_rows()
    test_issues_for_pinned_rows_are_one_per_upstream_and_tag()
    test_fleet_candidates_collapse_into_one_digest_issue()
    test_healthy_fleet_with_state_does_not_run()
    test_a_moved_tag_is_a_failure_and_never_a_candidate()
    test_a_missing_tag_is_the_same_failure()
    test_newer_tags_are_candidates_and_do_not_gate()
    test_a_moved_trunk_branch_gates_that_vmod()
    test_a_moved_vinyl_release_tag_is_the_same_loud_failure()
    test_a_newer_vinyl_release_tag_is_a_candidate()
    test_moved_dict_and_redis_trunk_branches_gate_their_vmods()
    test_a_state_predating_the_new_rows_does_not_false_alarm()
    test_a_moved_vinyl_trunk_gates_everything()
    test_state_failures_fail_open()
    test_an_unreachable_remote_runs_rather_than_skips()
    test_github_format_carries_the_gate_outputs()
    test_next_state_only_records_what_was_read()
    test_the_catalog_is_the_only_source_of_urls()
    test_the_transcript_never_touches_the_network()

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
