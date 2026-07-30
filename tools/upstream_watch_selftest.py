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


def _state(tmp: Path, refs: dict) -> Path:
    path = tmp / "state.json"
    path.write_text(
        json.dumps(
            {
                "schema": uw.STATE_SCHEMA,
                "refs": {k: {"url": "", "ref": "", "sha": v} for k, v in refs.items()},
                "trunk_engine_run_id": "",
            }
        ),
        encoding="utf-8",
    )
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
    check(
        "order: only tags above the pin, newest first, pre-releases excluded",
        uw.newer_tags(refs, "v1.0.1") == ["v1.1.0", "v1.0.2"],
        str(uw.newer_tags(refs, "v1.0.1")),
    )
    redis = {"refs/tags/9.0-23.1": "", "refs/tags/9.0-23.2": "", "refs/tags/9.1-24.0": "",
             "refs/tags/9.0-23.0": ""}
    check(
        "order: redis's series-and-version tags order correctly",
        uw.newer_tags(redis, "9.0-23.1") == ["9.1-24.0", "9.0-23.2"],
        str(uw.newer_tags(redis, "9.0-23.1")),
    )
    mixed = {"refs/tags/v1.7": "", "refs/tags/v2": "", "refs/tags/v1.8": ""}
    check(
        "order: a differently shaped tag is not compared against the pin",
        uw.newer_tags(mixed, "v1.7") == ["v1.8"],
        str(uw.newer_tags(mixed, "v1.7")),
    )
    check("order: a pin with no digits yields no candidates", uw.newer_tags(mixed, "main") == [])


# --- the check -------------------------------------------------------------


def test_healthy_fleet_with_state_does_not_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = uw.check(state_path=state, transcript=_transcript())
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
        report = uw.check(state_path=state, transcript=moved)
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
        report = uw.check(state_path=state, transcript=gone)
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
        report = uw.check(state_path=state, transcript=newer)
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
        report = uw.check(state_path=state, transcript=moved)
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
        report = uw.check(state_path=state, transcript=moved)
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
        report = uw.check(state_path=state, transcript=newer)
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
        report = uw.check(state_path=state, transcript=moved)
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
        report = uw.check(state_path=state, transcript=_transcript())
    check("compat: no moved pin is invented from missing state", report["ok"] is True, str(report["moved_pins"]))
    check(
        "compat: the unrecorded branch rows count as changed, once",
        report["changed_vmods"] == ["dict", "redis"] and report["run"] is True,
        str(report["changed_vmods"]),
    )
    check("compat: known rows are still compared, not reset", report["vinyl_changed"] is False)
    entry = next(e for e in report["entries"] if e["key"] == uw.VINYL_RELEASE_KEY)
    check("compat: the engine release row is simply ok", entry["status"] == "ok", str(entry))


def test_a_moved_vinyl_trunk_gates_everything() -> None:
    moved = _transcript(**{uw.VINYL_TRUNK_URL: _listing([("ab" * 20, "HEAD")])})
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = uw.check(state_path=state, transcript=moved)
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
            report = uw.check(state_path=path, transcript=healthy)
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
        report = uw.check(state_path=None, transcript=healthy)
        check("fail-open: no --state at all runs too", report["run"] is True)


def test_an_unreachable_remote_runs_rather_than_skips() -> None:
    broken = _transcript()
    del broken[DICT_URL]
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = uw.check(state_path=state, transcript=broken)
    entry = next(e for e in report["entries"] if e["key"] == "dict/release")
    check("unreachable: the entry says so", entry["status"] == "unreachable", entry["detail"])
    check("unreachable: its VMOD counts as changed", "dict" in report["changed_vmods"])
    check("unreachable: the gate runs rather than skipping", report["run"] is True)
    check("unreachable: it is not a moved pin", report["ok"] is True, str(report["moved_pins"]))
    gh = uw.render_github(report)
    check("unreachable: github format warns rather than errors", "::warning title=upstream unreachable" in gh, gh)


# --- output contracts ------------------------------------------------------


def test_github_format_carries_the_gate_outputs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), dict(HEALTHY_REFS))
        report = uw.check(state_path=state, transcript=_transcript())
    emitted = dict(
        line.split("=", 1)
        for line in uw.render_github(report).splitlines()
        if "=" in line and not line.startswith("::")
    )
    for name in ("run", "vinyl_changed", "vinyl_head_sha", "changed_vmods", "moved_pins"):
        check(f"github: the {name} gate output is present", name in emitted, str(sorted(emitted)))
    check("github: run is a lower-case boolean the workflow can compare", emitted["run"] == "false")
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
        report = uw.check(state_path=None, transcript=broken)
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
    targets, _ = uw.watch_targets()
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
