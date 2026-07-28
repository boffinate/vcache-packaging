#!/usr/bin/env python3
"""Tests for tools/ci_matrix.py.

Run directly, or via `python3 tools/ci_matrix.py selftest`.
Standard library only; nothing here builds, fetches, or tests a package.

The fixtures are synthetic VMOD catalogs in temporary directories, because the
properties under test are about a catalog with several entries -- one of which
is broken -- and this repository deliberately contains exactly one real VMOD.
The checked-in cachetag manifest is also validated, so the fixtures cannot
drift away from the thing they stand in for.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_matrix  # noqa: E402
import yaml_subset  # noqa: E402

_RESULTS: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(condition), detail))


GOOD_MANIFEST = """schema: vmod-ci/v1
id: {id}
source_host: github
repository: example-org/libvmod-{id}
required: {required}
adapter: cachetag
recipe: upstream
sources:
  release:
    ref: v1.0.1
    expected_commit: a3897aaccf1d6996c00ee14b2c6e1ddac91ac982
    version: 1.0.1
    archive_sha256: "{sha}"
    publishable: true
  trunk:
    ref: main
    publishable: false
lanes:
  - kind: package
    source: release
    engine: vinyl-release
    tiers:
      - ci
      - release
    targets:
      - debian-13-amd64
      - el9-x86_64
  - kind: package
    source: release
    engine: vinyl-trunk-pinned
    tiers:
      - ci
    targets:
      - debian-13-amd64
      - el9-x86_64
  - kind: source-harness
    source: trunk
    engine: vinyl-trunk-head
    tiers:
      - trunk
"""

BROKEN_MANIFEST = """schema: vmod-ci/v1
id: broken
source_host: github
repository: example-org/libvmod-broken
required: true
adapter: cachetag
recipe: upstream
sources:
  release:
    ref: v9.9.9
    publishable: true
lanes:
  - kind: package
    source: release
    engine: vinyl-release
    tiers:
      - ci
    targets:
      - debian-13-amd64
"""

SHA = "9aba3effcb20cfc70b77a4729990e3cd2ae1712ad242f4e4c45a664e1949eac2"


def _manifest_text(vmod_id: str, required: str = "true") -> str:
    return GOOD_MANIFEST.format(id=vmod_id, required=required, sha=SHA)


def _catalog(root: Path, entries: dict) -> Path:
    directory = root / ci_matrix.CATALOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in entries.items():
        (directory / name).write_text(text, encoding="utf-8")
    return root


def _write_records(root: Path, records: list) -> Path:
    directory = root / "results"
    for index, record in enumerate(records):
        sub = directory / f"artifact-{index}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "result.json").write_text(json.dumps(record), encoding="utf-8")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _target_record(vmod, channel, engine, target, status, **kw):
    return ci_matrix.make_record(
        kind="package-target",
        vmod=vmod,
        channel=channel,
        engine=engine,
        target=target,
        status=status,
        **kw,
    )


def _source_record(vmod, channel, status, **kw):
    return ci_matrix.make_record(kind="source", vmod=vmod, channel=channel, status=status, **kw)


def _invocation_record(vmod, status, **kw):
    return ci_matrix.make_record(kind="invocation", vmod=vmod, status=status, **kw)


def _engine_record(engine, target, status, **kw):
    return ci_matrix.make_record(
        kind="engine", vmod="", engine=engine, target=target, status=status, **kw
    )


# The four shared engine rows every `ci`-tier fixture in this file expects,
# because GOOD_MANIFEST declares both engines against both targets. Passing them
# is the normal case; a test that wants an engine failure overrides one entry.
def _green_engine_records() -> list:
    return [
        _engine_record(engine, target, "passed")
        for engine in ("vinyl-release", "vinyl-trunk-pinned")
        for target in ("debian-13-amd64", "el9-x86_64")
    ]


# --- catalog ---------------------------------------------------------------


def test_discovery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(
            Path(tmp),
            {"cachetag.yml": _manifest_text("cachetag"), "broken.yml": BROKEN_MANIFEST},
        )
        entries = ci_matrix.discover(root)
        check(
            "discovery: ids and paths come from file names only",
            entries
            == [
                {"id": "broken", "manifest": "registry/vmods/broken.yml"},
                {"id": "cachetag", "manifest": "registry/vmods/cachetag.yml"},
            ],
            str(entries),
        )
        # Discovery must not care that broken.yml is broken: that is the whole
        # point of deriving the id from the file name.
        check(
            "discovery: a malformed manifest is still discovered",
            any(e["id"] == "broken" for e in entries),
            str(entries),
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"Cachetag.yml": _manifest_text("cachetag")})
        try:
            ci_matrix.discover(root)
            check("discovery: rejects an upper-case file name", False, "no error")
        except ci_matrix.CatalogError as exc:
            check("discovery: rejects an upper-case file name", "file name" in str(exc), str(exc))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ci_matrix.CATALOG_DIR / "nested").mkdir(parents=True)
        try:
            ci_matrix.discover(root)
            check("discovery: rejects a subdirectory in the catalog", False, "no error")
        except ci_matrix.CatalogError as exc:
            check("discovery: rejects a subdirectory in the catalog", "flat" in str(exc), str(exc))

    with tempfile.TemporaryDirectory() as tmp:
        try:
            ci_matrix.discover(Path(tmp))
            check("discovery: a missing catalog directory is an error", False, "no error")
        except ci_matrix.CatalogError as exc:
            check("discovery: a missing catalog directory is an error", "catalog" in str(exc), str(exc))


# --- manifest validation ---------------------------------------------------


def test_manifest_validation() -> None:
    data = yaml_subset.parse(_manifest_text("cachetag"))
    check(
        "manifest: the fixture validates",
        ci_matrix.validate_vmod_manifest(data, "registry/vmods/cachetag.yml") == [],
        str(ci_matrix.validate_vmod_manifest(data, "registry/vmods/cachetag.yml")),
    )
    errors = ci_matrix.validate_vmod_manifest(
        data, "registry/vmods/cachetag.yml", discovery_id="somethingelse"
    )
    check(
        "manifest: the declared id must match the trusted discovery id",
        any("discovery id" in e for e in errors),
        str(errors),
    )
    errors = ci_matrix.validate_vmod_manifest(data, "registry/vmods/other.yml")
    check(
        "manifest: the declared id must match the file name stem",
        any("file name stem" in e for e in errors),
        str(errors),
    )

    # A VMOD called `engine` would mint result-engine-<channel>-<engine>-<target>
    # artifact names next to the engine rows' result-engine-<engine>-<target>,
    # in the one namespace the collector keys results by.
    for reserved in ci_matrix.RESERVED_VMOD_IDS:
        clash = yaml_subset.parse(_manifest_text(reserved))
        errors = ci_matrix.validate_vmod_manifest(
            clash, f"registry/vmods/{reserved}.yml", discovery_id=reserved
        )
        check(
            f"manifest: the reserved id {reserved!r} is rejected",
            any("is reserved" in e for e in errors),
            str(errors),
        )
        check(
            f"manifest: rejecting {reserved!r} is the only complaint about it",
            len(errors) == 1,
            str(errors),
        )

    broken = yaml_subset.parse(BROKEN_MANIFEST)
    errors = ci_matrix.validate_vmod_manifest(broken, "registry/vmods/broken.yml")
    check(
        "manifest: a package lane on an unpinned source is rejected",
        any("pinned source" in e for e in errors),
        str(errors),
    )
    check(
        "manifest: publishable without a pin is rejected",
        any("publishable requires" in e for e in errors),
        str(errors),
    )

    unknown_target = _manifest_text("cachetag").replace("      - el9-x86_64", "      - sunos-sparc")
    errors = ci_matrix.validate_vmod_manifest(
        yaml_subset.parse(unknown_target), "registry/vmods/cachetag.yml"
    )
    check(
        "manifest: an unselected package target is rejected",
        any("not a selected package target" in e for e in errors),
        str(errors),
    )

    harness_with_targets = _manifest_text("cachetag").replace(
        "  - kind: source-harness\n    source: trunk\n    engine: vinyl-trunk-head\n    tiers:\n      - trunk\n",
        "  - kind: source-harness\n    source: trunk\n    engine: vinyl-trunk-head\n    tiers:\n"
        "      - trunk\n    targets:\n      - debian-13-amd64\n",
    )
    errors = ci_matrix.validate_vmod_manifest(
        yaml_subset.parse(harness_with_targets), "registry/vmods/cachetag.yml"
    )
    check(
        "manifest: a source-harness lane must not name package targets",
        any("must not name package targets" in e for e in errors),
        str(errors),
    )

    duplicated = _manifest_text("cachetag") + (
        "  - kind: package\n    source: release\n    engine: vinyl-release\n"
        "    tiers:\n      - ci\n    targets:\n      - debian-13-amd64\n"
    )
    errors = ci_matrix.validate_vmod_manifest(
        yaml_subset.parse(duplicated), "registry/vmods/cachetag.yml"
    )
    check(
        "manifest: a duplicate lane row is rejected",
        any("duplicates the row" in e for e in errors),
        str(errors),
    )

    bad_schema = _manifest_text("cachetag").replace("schema: vmod-ci/v1", "schema: vmod-ci/v2")
    errors = ci_matrix.validate_vmod_manifest(
        yaml_subset.parse(bad_schema), "registry/vmods/cachetag.yml"
    )
    check("manifest: an unknown schema version is rejected", errors != [], str(errors))


def test_source_cross_check() -> None:
    data = yaml_subset.parse(_manifest_text("cachetag"))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "libvmod-cachetag"
        src.mkdir()
        (src / "configure.ac").write_text(
            "AC_INIT([libvmod-cachetag], [1.0.1])\n", encoding="utf-8"
        )
        check(
            "cross-check: a matching configure.ac passes",
            ci_matrix.source_cross_check_errors(data, "m", src) == [],
            str(ci_matrix.source_cross_check_errors(data, "m", src)),
        )
        (src / "configure.ac").write_text(
            "AC_INIT([libvmod-cachetag], [1.0.2])\n", encoding="utf-8"
        )
        errors = ci_matrix.source_cross_check_errors(data, "m", src)
        check(
            "cross-check: a configure.ac disagreeing with the manifest fails",
            any("does not match configure.ac" in e for e in errors),
            str(errors),
        )
        errors = ci_matrix.source_cross_check_errors(data, "m", Path(tmp) / "absent")
        check(
            "cross-check: a missing checkout is an actionable error",
            any("configure.ac" in e for e in errors),
            str(errors),
        )
        # The trunk channel records no version: what it resolved to is
        # evidence, not a pin, so there is nothing to compare against.
        check(
            "cross-check: a moving channel has nothing to cross-check",
            ci_matrix.source_cross_check_errors(data, "m", src, channel="trunk") == [],
            str(ci_matrix.source_cross_check_errors(data, "m", src, channel="trunk")),
        )


# --- expansion -------------------------------------------------------------


def test_expansion() -> None:
    data = yaml_subset.parse(_manifest_text("cachetag"))

    ci = ci_matrix.expand(data, "ci")
    check(
        "expand ci: four package-target rows, one source row, no harness",
        (ci["target_count"], ci["source_count"], ci["harness_count"]) == (4, 1, 0),
        str((ci["target_count"], ci["source_count"], ci["harness_count"])),
    )
    engines = sorted({row["engine"] for row in ci["targets"]["include"]})
    check(
        "expand ci: both engine channels are present and named, not 'track'",
        engines == ["vinyl-release", "vinyl-trunk-pinned"],
        str(engines),
    )
    tracks = {row["engine"]: row["vinyl_track"] for row in ci["targets"]["include"]}
    check(
        "expand ci: engines map onto the VINYL_TRACK the lane scripts already select",
        tracks == {"vinyl-release": "release", "vinyl-trunk-pinned": "trunk"},
        str(tracks),
    )
    families = {row["target"]: row["family"] for row in ci["targets"]["include"]}
    check(
        "expand ci: each target carries its package family",
        families == {"debian-13-amd64": "deb", "el9-x86_64": "rpm"},
        str(families),
    )
    names = sorted(row["packages_artifact"] for row in ci["targets"]["include"])
    check(
        "expand ci: artifact names are derived from the stable row key alone",
        names
        == [
            "packages-cachetag-release-vinyl-release-debian-13-amd64",
            "packages-cachetag-release-vinyl-release-el9-x86_64",
            "packages-cachetag-release-vinyl-trunk-pinned-debian-13-amd64",
            "packages-cachetag-release-vinyl-trunk-pinned-el9-x86_64",
        ],
        str(names),
    )
    check(
        "expand ci: the source artifact name is stable",
        ci["sources"]["include"][0]["source_artifact"] == "vmod-source-cachetag-release",
        str(ci["sources"]["include"]),
    )

    release = ci_matrix.expand(data, "release")
    check(
        "expand release: only the release-engine rows",
        release["target_count"] == 2
        and {row["engine"] for row in release["targets"]["include"]} == {"vinyl-release"},
        str(release["targets"]),
    )

    trunk = ci_matrix.expand(data, "trunk")
    check(
        "expand trunk: only the source-harness row, and no package rows",
        (trunk["target_count"], trunk["harness_count"], trunk["source_count"]) == (0, 1, 0),
        str((trunk["target_count"], trunk["harness_count"], trunk["source_count"])),
    )

    nightly = ci_matrix.expand(data, "nightly")
    check(
        "expand nightly: no rows, because no lane claims that tier yet",
        (nightly["target_count"], nightly["source_count"], nightly["harness_count"]) == (0, 0, 0),
        str(nightly),
    )


def test_injection_is_confined_to_expansion() -> None:
    data = yaml_subset.parse(_manifest_text("cachetag"))
    plain = ci_matrix.expand(data, "ci")
    digest = ci_matrix.expand(data, "ci", inject="source_digest")
    checkout = ci_matrix.expand(data, "ci", inject="source_checkout")
    check(
        "inject: source_digest changes only the expected archive digest",
        digest["sources"]["include"][0]["archive_sha256"]
        != plain["sources"]["include"][0]["archive_sha256"]
        and digest["targets"] == plain["targets"],
        str(digest["sources"]),
    )
    check(
        "inject: source_checkout changes only the ref",
        checkout["sources"]["include"][0]["ref"] == "vmod-ci-injected-missing-ref"
        and checkout["targets"] == plain["targets"],
        str(checkout["sources"]),
    )
    check(
        "inject: the default expansion is untouched",
        plain["sources"]["include"][0]["archive_sha256"] == SHA
        and plain["sources"]["include"][0]["ref"] == "v1.0.1",
        str(plain["sources"]),
    )


# --- ledger ----------------------------------------------------------------


def test_ledger() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        data = ci_matrix.ledger("ci", root)
        keys = sorted(row["row_key"] for row in data["rows"])
        check(
            "ledger: the shared engine rows, one invocation row, one source row, every lane row",
            keys
            == [
                "engine/vinyl-release/debian-13-amd64",
                "engine/vinyl-release/el9-x86_64",
                "engine/vinyl-trunk-pinned/debian-13-amd64",
                "engine/vinyl-trunk-pinned/el9-x86_64",
                "harness/cachetag/trunk/vinyl-trunk-head",
                "source/cachetag/release",
                "target/cachetag/release/vinyl-release/debian-13-amd64",
                "target/cachetag/release/vinyl-release/el9-x86_64",
                "target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64",
                "target/cachetag/release/vinyl-trunk-pinned/el9-x86_64",
                "vmod/cachetag",
            ],
            str(keys),
        )
        selected = {row["row_key"]: row["selected"] for row in data["rows"]}
        check(
            "ledger: the trunk harness lane is present but not selected for ci",
            selected["harness/cachetag/trunk/vinyl-trunk-head"] is False,
            str(selected),
        )
        check(
            "ledger: every row records whether its VMOD is required",
            all(row["required"] for row in data["rows"]),
            str(data["rows"][0]),
        )
        engines = [row for row in data["rows"] if row["kind"] == "engine"]
        check(
            "ledger: an engine row belongs to no VMOD and names its own artifact",
            len(engines) == 4
            and all(row["vmod"] == "" for row in engines)
            and sorted(row["engine_artifact"] for row in engines)
            == [
                "engine-vinyl-release-debian-13-amd64",
                "engine-vinyl-release-el9-x86_64",
                "engine-vinyl-trunk-pinned-debian-13-amd64",
                "engine-vinyl-trunk-pinned-el9-x86_64",
            ],
            str(engines),
        )
        check(
            "ledger: no engine row is derived for the source-harness-only engine",
            all(row["engine"] != "vinyl-trunk-head" for row in engines),
            str(engines),
        )
        targets = [row for row in data["rows"] if row["kind"] == "package-target"]
        check(
            "ledger: every package row names the engine row it consumes",
            all(
                row["engine_row_key"] == f"engine/{row['engine']}/{row['target']}"
                and row["engine_artifact"] == f"engine-{row['engine']}-{row['target']}"
                for row in targets
            ),
            str(targets),
        )
        check(
            "ledger: result artifact names are predictable from the row key",
            sorted(row["result_artifact"] for row in data["rows"] if row["kind"] == "package-target")
            == [
                "result-cachetag-release-vinyl-release-debian-13-amd64",
                "result-cachetag-release-vinyl-release-el9-x86_64",
                "result-cachetag-release-vinyl-trunk-pinned-debian-13-amd64",
                "result-cachetag-release-vinyl-trunk-pinned-el9-x86_64",
            ],
            str([row["result_artifact"] for row in data["rows"]]),
        )


def test_ledger_keeps_a_broken_manifest_as_one_row() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(
            Path(tmp),
            {"cachetag.yml": _manifest_text("cachetag"), "broken.yml": BROKEN_MANIFEST},
        )
        data = ci_matrix.ledger("ci", root)
        broken = [row for row in data["rows"] if row["vmod"] == "broken"]
        check(
            "ledger: a malformed manifest contributes exactly one invocation row",
            len(broken) == 1 and broken[0]["kind"] == "invocation",
            str(broken),
        )
        check(
            "ledger: no lane rows are invented for a manifest that failed validation",
            not any(row["kind"] != "invocation" for row in broken),
            str(broken),
        )
        check(
            "ledger: the broken entry keeps its trusted discovery id and stays required",
            broken[0]["vmod"] == "broken" and broken[0]["required"] is True,
            str(broken[0]),
        )
        check(
            "ledger: the healthy VMOD's rows are unaffected",
            len([row for row in data["rows"] if row["vmod"] == "cachetag"]) == 7,
            str(len(data["rows"])),
        )
        check(
            "ledger: a malformed manifest contributes no engine demand of its own",
            sorted(
                (row["engine"], row["target"])
                for row in data["rows"]
                if row["kind"] == "engine"
            )
            == [
                ("vinyl-release", "debian-13-amd64"),
                ("vinyl-release", "el9-x86_64"),
                ("vinyl-trunk-pinned", "debian-13-amd64"),
                ("vinyl-trunk-pinned", "el9-x86_64"),
            ],
            str([r["row_key"] for r in data["rows"] if r["kind"] == "engine"]),
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag"), "bad.yml": "\ta: 1\n"})
        data = ci_matrix.ledger("ci", root)
        bad = [row for row in data["rows"] if row["vmod"] == "bad"]
        check(
            "ledger: a manifest that does not even parse is still one invocation row",
            len(bad) == 1 and bad[0]["manifest_valid"] is False,
            str(bad),
        )


# --- reconciliation --------------------------------------------------------


def _reconcile(root: Path, records: list, tier: str = "ci") -> dict:
    results = _write_records(root, records)
    return ci_matrix.reconcile(ci_matrix.ledger(tier, root), ci_matrix.load_records(results))


def test_reconcile_all_green() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        records = _green_engine_records() + [
            _invocation_record("cachetag", "passed"),
            _source_record("cachetag", "release", "passed", source={"ref": "v1.0.1"}),
        ]
        for engine in ("vinyl-release", "vinyl-trunk-pinned"):
            for target in ("debian-13-amd64", "el9-x86_64"):
                records.append(_target_record("cachetag", "release", engine, target, "passed"))
        resolved = _reconcile(root, records)
        check("reconcile: an all-green run is ok", resolved["ok"], json.dumps(resolved["counts"]))
        check(
            "reconcile: the counts describe the selected rows only",
            resolved["counts"]["expected"] == 10 and resolved["counts"]["passed"] == 10,
            json.dumps(resolved["counts"]),
        )
        check(
            "reconcile: the summary has a shared engine section",
            "### Shared engine packages" in ci_matrix.render_summary(resolved),
            ci_matrix.render_summary(resolved),
        )
        check(
            "reconcile: the unselected trunk harness row is reported as not_selected",
            any(
                row["status"] == "not_selected" and row["kind"] == "source-harness"
                for row in resolved["rows"]
            ),
            str([(r["row_key"], r["status"]) for r in resolved["rows"]]),
        )
        text = ci_matrix.render_summary(resolved)
        check(
            "reconcile: the summary groups by VMOD and shows passing rows",
            "### cachetag (required)" in text and text.count("PASS") >= 6,
            text,
        )


def test_reconcile_classifies_failures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        records = _green_engine_records() + [
            _invocation_record("cachetag", "passed"),
            _source_record("cachetag", "release", "passed"),
            _target_record(
                "cachetag",
                "release",
                "vinyl-release",
                "debian-13-amd64",
                "failed_package_build",
                detail="dpkg-buildpackage exited 1",
            ),
            _target_record("cachetag", "release", "vinyl-release", "el9-x86_64", "passed"),
            _target_record(
                "cachetag", "release", "vinyl-trunk-pinned", "debian-13-amd64", "passed"
            ),
            # el9/vinyl-trunk-pinned deliberately uploads nothing.
        ]
        resolved = _reconcile(root, records)
        by_key = {row["row_key"]: row for row in resolved["rows"]}
        check(
            "reconcile: a build failure keeps its classification",
            by_key["target/cachetag/release/vinyl-release/debian-13-amd64"]["status"]
            == "failed_package_build",
            str(by_key["target/cachetag/release/vinyl-release/debian-13-amd64"]),
        )
        check(
            "reconcile: an absent record for a row whose source passed is missing evidence",
            by_key["target/cachetag/release/vinyl-trunk-pinned/el9-x86_64"]["status"]
            == "missing_result_record",
            str(by_key["target/cachetag/release/vinyl-trunk-pinned/el9-x86_64"]),
        )
        check(
            "reconcile: unrelated rows still show their passes",
            by_key["target/cachetag/release/vinyl-release/el9-x86_64"]["status"] == "passed",
            str(by_key["target/cachetag/release/vinyl-release/el9-x86_64"]),
        )
        check(
            "reconcile: a required failure makes the run red",
            resolved["ok"] is False and resolved["counts"]["required_failed"] == 2,
            json.dumps(resolved["counts"]),
        )


def test_reconcile_blocked_by_vmod_source() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        records = _green_engine_records() + [
            _invocation_record("cachetag", "passed"),
            _source_record(
                "cachetag", "release", "failed_source_digest", detail="archive sha256 mismatch"
            ),
        ]
        resolved = _reconcile(root, records)
        statuses = {
            row["row_key"]: row["status"] for row in resolved["rows"] if row["kind"] == "package-target"
        }
        check(
            "reconcile: every target row of a failed source is blocked_by_vmod_source",
            set(statuses.values()) == {"blocked_by_vmod_source"} and len(statuses) == 4,
            str(statuses),
        )
        check(
            "reconcile: the blocked rows name the source row that caused it",
            all(
                "source/cachetag/release" in row["detail"]
                for row in resolved["rows"]
                if row["status"] == "blocked_by_vmod_source"
            ),
            str([(r["row_key"], r["detail"]) for r in resolved["rows"]]),
        )
        check("reconcile: a blocked required row is still a red run", resolved["ok"] is False)


def test_engine_matrix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        entries = {f"vmod{i}.yml": _manifest_text(f"vmod{i}") for i in range(1, 4)}
        entries["broken.yml"] = BROKEN_MANIFEST
        root = _catalog(Path(tmp), entries)
        matrix = ci_matrix.engine_matrix("ci", root)
        rows = sorted((e["engine"], e["target"]) for e in matrix["include"])
        check(
            "engine-matrix: three VMODs naming the same engines share four engine rows",
            rows
            == [
                ("vinyl-release", "debian-13-amd64"),
                ("vinyl-release", "el9-x86_64"),
                ("vinyl-trunk-pinned", "debian-13-amd64"),
                ("vinyl-trunk-pinned", "el9-x86_64"),
            ],
            str(rows),
        )
        check(
            "engine-matrix: a malformed manifest does not prevent the engine rows",
            len(matrix["include"]) == 4,
            str(matrix),
        )
        check(
            "engine-matrix: every entry carries the artifact name, family and track",
            all(
                e["engine_artifact"] == f"engine-{e['engine']}-{e['target']}"
                and e["family"] == ci_matrix.TARGETS[e["target"]]["family"]
                and e["vinyl_track"] == ci_matrix.ENGINES[e["engine"]]["vinyl_track"]
                and e["timeout_minutes"]
                == ci_matrix.TARGETS[e["target"]]["engine_timeout_minutes"]
                for e in matrix["include"]
            ),
            str(matrix["include"][0]),
        )
        check(
            "engine-matrix: injection is inert by default",
            all(
                e["inject_build"] == "false" and e["suppress_artifact"] == "false"
                for e in matrix["include"]
            ),
            str(matrix["include"]),
        )

        release_only = ci_matrix.engine_matrix("release", root)
        check(
            "engine-matrix: the release tier needs only the release-engine rows",
            sorted((e["engine"], e["target"]) for e in release_only["include"])
            == [
                ("vinyl-release", "debian-13-amd64"),
                ("vinyl-release", "el9-x86_64"),
            ],
            str(release_only),
        )

        for inject, field in (
            ("engine_build", "inject_build"),
            ("suppress_engine_artifact", "suppress_artifact"),
        ):
            injected = ci_matrix.engine_matrix("ci", root, inject=inject)
            marked = [
                (e["engine"], e["target"]) for e in injected["include"] if e[field] == "true"
            ]
            check(
                f"engine-matrix: {inject} marks exactly the one documented row",
                marked == [ci_matrix.INJECT_ENGINE_ROW],
                str(marked),
            )
            check(
                f"engine-matrix: {inject} leaves the other three rows alone",
                sum(1 for e in injected["include"] if e[field] == "false") == 3,
                str(injected["include"]),
            )


def test_reconcile_blocked_by_engine_artifact() -> None:
    """The plan's verification case 6, in the collector.

    One engine row fails; only the VMOD rows that name that exact engine and
    target may be reported blocked, and they must name the engine row rather
    than surfacing as an unclassified download error.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        records = [
            _engine_record("vinyl-release", "debian-13-amd64", "passed"),
            _engine_record("vinyl-release", "el9-x86_64", "passed"),
            _engine_record(
                "vinyl-trunk-pinned",
                "debian-13-amd64",
                "failed_engine_build",
                detail="injected engine-build failure",
            ),
            _engine_record("vinyl-trunk-pinned", "el9-x86_64", "passed"),
            _invocation_record("cachetag", "passed"),
            _source_record("cachetag", "release", "passed"),
            _target_record("cachetag", "release", "vinyl-release", "debian-13-amd64", "passed"),
            _target_record("cachetag", "release", "vinyl-release", "el9-x86_64", "passed"),
            _target_record("cachetag", "release", "vinyl-trunk-pinned", "el9-x86_64", "passed"),
            # The consumer of the failed engine row uploads nothing at all, so
            # the collector has to classify it from the ledger alone.
        ]
        resolved = _reconcile(root, records)
        by_key = {row["row_key"]: row for row in resolved["rows"]}
        blocked = by_key["target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64"]
        check(
            "engine: the consumer of a failed engine row is blocked_by_engine_artifact",
            blocked["status"] == "blocked_by_engine_artifact",
            str(blocked),
        )
        check(
            "engine: the blocked row names the engine row identity",
            "engine/vinyl-trunk-pinned/debian-13-amd64" in blocked["detail"]
            and "failed_engine_build" in blocked["detail"],
            str(blocked),
        )
        check(
            "engine: unrelated engine rows and their consumers still pass",
            all(
                by_key[key]["status"] == "passed"
                for key in (
                    "engine/vinyl-release/debian-13-amd64",
                    "engine/vinyl-release/el9-x86_64",
                    "engine/vinyl-trunk-pinned/el9-x86_64",
                    "target/cachetag/release/vinyl-release/debian-13-amd64",
                    "target/cachetag/release/vinyl-release/el9-x86_64",
                    "target/cachetag/release/vinyl-trunk-pinned/el9-x86_64",
                )
            ),
            str({k: v["status"] for k, v in by_key.items()}),
        )
        check(
            "engine: a failed engine row makes the run red",
            resolved["ok"] is False and resolved["counts"]["required_failed"] == 2,
            json.dumps(resolved["counts"]),
        )
        check(
            "engine: the summary reports the shared root cause in its own section",
            "### Shared engine packages" in ci_matrix.render_summary(resolved)
            and "vinyl-trunk-pinned / debian-13-amd64" in ci_matrix.render_summary(resolved),
            ci_matrix.render_summary(resolved),
        )


def test_engine_row_that_never_reported_blocks_its_consumers() -> None:
    """An engine row with no record at all is missing evidence, and still blocks.

    This is the `suppress_engine_artifact` shape: the collector must not decide
    the consumer is simply missing its own record when the thing it depended on
    produced nothing either.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        records = [
            _engine_record("vinyl-release", "debian-13-amd64", "passed"),
            _engine_record("vinyl-release", "el9-x86_64", "passed"),
            _engine_record("vinyl-trunk-pinned", "el9-x86_64", "passed"),
            _invocation_record("cachetag", "passed"),
            _source_record("cachetag", "release", "passed"),
            _target_record("cachetag", "release", "vinyl-release", "debian-13-amd64", "passed"),
            _target_record("cachetag", "release", "vinyl-release", "el9-x86_64", "passed"),
            _target_record("cachetag", "release", "vinyl-trunk-pinned", "el9-x86_64", "passed"),
        ]
        resolved = _reconcile(root, records)
        by_key = {row["row_key"]: row for row in resolved["rows"]}
        check(
            "engine: an unreported engine row is missing_result_record",
            by_key["engine/vinyl-trunk-pinned/debian-13-amd64"]["status"]
            == "missing_result_record",
            str(by_key["engine/vinyl-trunk-pinned/debian-13-amd64"]),
        )
        check(
            "engine: its consumer is blocked rather than reported as missing its own record",
            by_key["target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64"]["status"]
            == "blocked_by_engine_artifact",
            str(by_key["target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64"]),
        )


def test_vmod_source_failure_wins_over_an_engine_failure() -> None:
    """Both causes apply; the row's own source failure is the one it reports.

    The engine failure is not lost -- it is on the engine row, which is where a
    shared root cause belongs.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        records = [
            _engine_record("vinyl-release", "debian-13-amd64", "failed_engine_build"),
            _engine_record("vinyl-release", "el9-x86_64", "passed"),
            _engine_record("vinyl-trunk-pinned", "debian-13-amd64", "passed"),
            _engine_record("vinyl-trunk-pinned", "el9-x86_64", "passed"),
            _invocation_record("cachetag", "passed"),
            _source_record("cachetag", "release", "failed_source_digest"),
        ]
        resolved = _reconcile(root, records)
        by_key = {row["row_key"]: row for row in resolved["rows"]}
        check(
            "engine: a row blocked by both causes reports its own VMOD source",
            by_key["target/cachetag/release/vinyl-release/debian-13-amd64"]["status"]
            == "blocked_by_vmod_source",
            str(by_key["target/cachetag/release/vinyl-release/debian-13-amd64"]),
        )
        check(
            "engine: the engine failure is still reported on the engine row",
            by_key["engine/vinyl-release/debian-13-amd64"]["status"] == "failed_engine_build",
            str(by_key["engine/vinyl-release/debian-13-amd64"]),
        )


def test_reconcile_harness_row_without_a_source_row() -> None:
    """A harness lane has no source row, so it can never be *blocked* by one.

    vmod_rows emits a source row only for a channel a package lane consumes: a
    source-harness lane on a moving branch derives no archive. Reporting its
    missing record as blocked_by_vmod_source would name a cause that was never
    expected to run.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        resolved = _reconcile(root, [_invocation_record("cachetag", "passed")], tier="trunk")
        by_key = {row["row_key"]: row for row in resolved["rows"]}
        harness = by_key["harness/cachetag/trunk/vinyl-trunk-head"]
        check(
            "reconcile: an unreported harness row with no source row is missing evidence",
            harness["selected"] and harness["status"] == "missing_result_record",
            str(harness),
        )
        check(
            "reconcile: the trunk tier does not select the package rows",
            all(
                row["status"] == "not_selected"
                for row in resolved["rows"]
                if row["kind"] in ("package-target", "source")
            ),
            str([(r["row_key"], r["status"]) for r in resolved["rows"]]),
        )


def test_summary_names_optional_failures_on_a_green_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"optional.yml": _manifest_text("optional", required="false")})
        records = _green_engine_records() + [
            _invocation_record("optional", "passed"),
            _source_record("optional", "release", "passed"),
            _target_record("optional", "release", "vinyl-release", "debian-13-amd64", "failed_lint"),
            _target_record("optional", "release", "vinyl-release", "el9-x86_64", "passed"),
            _target_record(
                "optional", "release", "vinyl-trunk-pinned", "debian-13-amd64", "passed"
            ),
            _target_record("optional", "release", "vinyl-trunk-pinned", "el9-x86_64", "passed"),
        ]
        resolved = _reconcile(root, records)
        text = ci_matrix.render_summary(resolved)
        check(
            "summary: a green run that still contains failures says so",
            resolved["ok"] and "1 optional row(s) failed and did not redden the run." in text,
            text,
        )
        all_green = _green_engine_records() + [
            _invocation_record("optional", "passed"),
            _source_record("optional", "release", "passed"),
        ]
        for engine in ("vinyl-release", "vinyl-trunk-pinned"):
            for target in ("debian-13-amd64", "el9-x86_64"):
                all_green.append(_target_record("optional", "release", engine, target, "passed"))
        with tempfile.TemporaryDirectory() as tmp2:
            clean_root = _catalog(
                Path(tmp2), {"optional.yml": _manifest_text("optional", required="false")}
            )
            clean = _reconcile(clean_root, all_green)
        check(
            "summary: the optional-failure sentence is absent when nothing failed",
            clean["ok"]
            and clean["counts"]["failed"] == 0
            and "did not redden" not in ci_matrix.render_summary(clean),
            ci_matrix.render_summary(clean),
        )


def test_reconcile_manifest_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(
            Path(tmp),
            {"cachetag.yml": _manifest_text("cachetag"), "broken.yml": BROKEN_MANIFEST},
        )
        records = _green_engine_records() + [
            _invocation_record("cachetag", "passed"),
            _source_record("cachetag", "release", "passed"),
        ]
        for engine in ("vinyl-release", "vinyl-trunk-pinned"):
            for target in ("debian-13-amd64", "el9-x86_64"):
                records.append(_target_record("cachetag", "release", engine, target, "passed"))
        resolved = _reconcile(root, records)
        by_vmod = {}
        for row in resolved["rows"]:
            by_vmod.setdefault(row["vmod"], []).append(row)
        check(
            "reconcile: the broken VMOD is classified failed_manifest_validation",
            [row["status"] for row in by_vmod["broken"]] == ["failed_manifest_validation"],
            str(by_vmod["broken"]),
        )
        check(
            "reconcile: the classification is reached without a record from the broken VMOD",
            by_vmod["broken"][0]["observed"] is False,
            str(by_vmod["broken"][0]),
        )
        check(
            "reconcile: a broken manifest does not stop the other VMOD being reconciled",
            all(row["status"] == "passed" for row in by_vmod["cachetag"] if row["selected"]),
            str([(r["row_key"], r["status"]) for r in by_vmod["cachetag"]]),
        )
        check(
            "reconcile: the run is red because a required VMOD failed validation",
            resolved["ok"] is False and resolved["counts"]["required_failed"] == 1,
            json.dumps(resolved["counts"]),
        )


def test_multi_vmod_isolation() -> None:
    """The plan's acceptance shape, in miniature: one entry fails, the rest report."""
    with tempfile.TemporaryDirectory() as tmp:
        entries = {f"vmod{i}.yml": _manifest_text(f"vmod{i}") for i in range(1, 5)}
        entries["broken.yml"] = BROKEN_MANIFEST
        entries["optional.yml"] = _manifest_text("optional", required="false")
        root = _catalog(Path(tmp), entries)

        # Every entry names the same four engine rows, so the shared half of the
        # graph is built once and is green throughout: this test is about VMOD
        # isolation, and an engine failure would blur it.
        records = _green_engine_records()
        for i in range(1, 5):
            vmod = f"vmod{i}"
            records.append(_invocation_record(vmod, "passed"))
            if vmod == "vmod2":
                # This one dies at source verification; its four target rows
                # must be reported as blocked, not as absent or cancelled.
                records.append(_source_record(vmod, "release", "failed_source_checkout"))
                continue
            records.append(_source_record(vmod, "release", "passed"))
            for engine in ("vinyl-release", "vinyl-trunk-pinned"):
                for target in ("debian-13-amd64", "el9-x86_64"):
                    records.append(_target_record(vmod, "release", engine, target, "passed"))
        records.append(_invocation_record("optional", "passed"))
        records.append(_source_record("optional", "release", "passed"))
        for engine in ("vinyl-release", "vinyl-trunk-pinned"):
            for target in ("debian-13-amd64", "el9-x86_64"):
                records.append(
                    _target_record("optional", "release", engine, target, "failed_lint")
                )

        resolved = _reconcile(root, records)
        by_vmod = {}
        for row in resolved["rows"]:
            by_vmod.setdefault(row["vmod"], []).append(row)

        healthy = [f"vmod{i}" for i in (1, 3, 4)]
        check(
            "multi-VMOD: every healthy entry reaches its final rows",
            all(
                all(row["status"] == "passed" for row in by_vmod[v] if row["selected"])
                for v in healthy
            ),
            str({v: [(r["row_key"], r["status"]) for r in by_vmod[v]] for v in healthy}),
        )
        check(
            "multi-VMOD: the failing entry's targets are blocked_by_vmod_source",
            [row["status"] for row in by_vmod["vmod2"] if row["kind"] == "package-target"]
            == ["blocked_by_vmod_source"] * 4,
            str([(r["row_key"], r["status"]) for r in by_vmod["vmod2"]]),
        )
        check(
            "multi-VMOD: the malformed entry is one classified row",
            [row["status"] for row in by_vmod["broken"]] == ["failed_manifest_validation"],
            str(by_vmod["broken"]),
        )
        check(
            "multi-VMOD: an optional VMOD's failures are reported",
            all(
                row["status"] == "failed_lint"
                for row in by_vmod["optional"]
                if row["kind"] == "package-target"
            ),
            str(by_vmod["optional"]),
        )
        check(
            "multi-VMOD: the summary shows all six entries, red run included",
            all(f"### {v}" in ci_matrix.render_summary(resolved) for v in sorted(by_vmod) if v),
            ci_matrix.render_summary(resolved),
        )
        check(
            "multi-VMOD: six entries share exactly four engine rows",
            len(by_vmod[""]) == 4 and all(row["kind"] == "engine" for row in by_vmod[""]),
            str([r["row_key"] for r in by_vmod[""]]),
        )
        # Six required failures: vmod2's source row plus its four blocked
        # target rows, and the malformed manifest. The optional VMOD's four
        # lint failures are counted as failures but do not make the run red.
        check(
            "multi-VMOD: the run is red, and red because of the required entries",
            resolved["ok"] is False
            and resolved["counts"]["required_failed"] == 6
            and resolved["counts"]["failed"] == 10,
            json.dumps(resolved["counts"]),
        )


def test_record_precedence_and_validation() -> None:
    real = _target_record("cachetag", "release", "vinyl-release", "debian-13-amd64", "passed")
    synthetic = ci_matrix.make_record(
        kind="package-target",
        vmod="cachetag",
        channel="release",
        engine="vinyl-release",
        target="debian-13-amd64",
        status="missing_result_record",
        synthesized=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        results = _write_records(Path(tmp), [synthetic, real])
        loaded = ci_matrix.load_records(results)
        check(
            "records: a row's own record beats a synthesized one",
            loaded[real["row_key"]]["status"] == "passed",
            str(loaded),
        )
    with tempfile.TemporaryDirectory() as tmp:
        results = _write_records(Path(tmp), [real, synthetic])
        loaded = ci_matrix.load_records(results)
        check(
            "records: precedence does not depend on load order",
            loaded[real["row_key"]]["status"] == "passed",
            str(loaded),
        )
    try:
        ci_matrix.make_record(kind="package-target", vmod="x", status="it-broke")
        check("records: an unknown status is refused", False, "no error")
    except ValueError as exc:
        check("records: an unknown status is refused", "unknown status" in str(exc), str(exc))
    check(
        "records: the plan's whole failure vocabulary is implemented",
        set(ci_matrix.STATUSES)
        >= {
            "failed_manifest_validation",
            "failed_source_checkout",
            "failed_source_digest",
            "failed_source_archive",
            "blocked_by_vmod_source",
            "failed_engine_build",
            "blocked_by_engine_artifact",
            "failed_package_build",
            "failed_abi_or_hardening",
            "failed_lint",
            "failed_install_or_smoke",
            "failed_behavior",
            "failed_transactions",
            "missing_result_record",
            "failed_infrastructure",
            "passed",
            "not_selected",
        },
        str(ci_matrix.STATUSES),
    )


def test_synthesize_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(Path(tmp), {"cachetag.yml": _manifest_text("cachetag")})
        expected = ci_matrix.ledger("ci", root)
        observed = ci_matrix.load_records(
            _write_records(
                Path(tmp),
                [
                    _invocation_record("cachetag", "passed"),
                    _source_record("cachetag", "release", "failed_source_archive"),
                ],
            )
        )
        synthesized = ci_matrix.synthesize_missing(expected, observed, "cachetag")
        check(
            "synthesize: the per-VMOD summary fills in every unreported selected row",
            sorted(r["row_key"] for r in synthesized)
            == [
                "target/cachetag/release/vinyl-release/debian-13-amd64",
                "target/cachetag/release/vinyl-release/el9-x86_64",
                "target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64",
                "target/cachetag/release/vinyl-trunk-pinned/el9-x86_64",
            ],
            str([r["row_key"] for r in synthesized]),
        )
        check(
            "synthesize: the synthesized rows carry the blocked classification and say so",
            all(
                r["status"] == "blocked_by_vmod_source" and r["synthesized"] for r in synthesized
            ),
            str(synthesized),
        )


# --- the second VMOD: schema, isolation, classification -------------------


def _load(repo_root: Path, vmod: str) -> dict:
    return ci_matrix.load_vmod_manifest(repo_root / "registry" / "vmods" / f"{vmod}.yml")


def test_non_github_upstream_is_representable(repo_root: Path) -> None:
    """Step 5's ruling 5: a non-GitHub upstream must be sayable, not implied."""
    dict_data = _load(repo_root, "dict")
    check(
        "schema: dict declares a git host and a clone URL",
        dict_data["source_host"] == "git"
        and dict_data["clone_url"] == "https://git.gnu.org.ua/vmod-dict.git"
        and "repository" not in dict_data,
        str({k: dict_data.get(k) for k in ("source_host", "clone_url", "repository")}),
    )
    cachetag = _load(repo_root, "cachetag")
    check(
        "schema: cachetag still declares a GitHub owner/name",
        cachetag["source_host"] == "github"
        and cachetag["repository"] == "boffinate/libvmod-cachetag"
        and "clone_url" not in cachetag,
        str({k: cachetag.get(k) for k in ("source_host", "clone_url", "repository")}),
    )

    # Both halves of the exclusivity rule, because either one alone would let
    # an entry carry an address its declared host cannot be reached at.
    bad = dict(dict_data)
    bad["repository"] = "someone/vmod-dict"
    errors = ci_matrix.validate_vmod_manifest(bad, "x/dict.yml", "dict")
    check(
        "schema: a git-hosted entry may not carry an owner/name",
        any("meaningful only on GitHub" in e for e in errors),
        str(errors),
    )
    bad = dict(cachetag)
    bad["clone_url"] = "https://github.com/boffinate/libvmod-cachetag.git"
    errors = ci_matrix.validate_vmod_manifest(bad, "x/cachetag.yml", "cachetag")
    check(
        "schema: a GitHub entry may not carry a clone URL",
        any("not used with source_host: github" in e for e in errors),
        str(errors),
    )
    bad = dict(dict_data)
    del bad["clone_url"]
    errors = ci_matrix.validate_vmod_manifest(bad, "x/dict.yml", "dict")
    check(
        "schema: a git-hosted entry needs a clone URL",
        any("requires clone_url" in e for e in errors),
        str(errors),
    )


def test_recipe_strategy_is_recorded(repo_root: Path) -> None:
    """The plan forbids discovering a strategy; it must be in the manifest."""
    check(
        "schema: cachetag records the upstream-owned recipe strategy",
        _load(repo_root, "cachetag")["recipe"] == "upstream",
    )
    check(
        "schema: dict records the generated recipe strategy",
        _load(repo_root, "dict")["recipe"] == "generated",
    )
    data = _load(repo_root, "dict")
    bad = json.loads(json.dumps(data))
    del bad["sources"]["release"]["archive_url"]
    errors = ci_matrix.validate_vmod_manifest(bad, "x/dict.yml", "dict")
    check(
        "schema: a generated recipe needs a published archive URL",
        any("needs archive_url" in e for e in errors),
        str(errors),
    )


def test_dict_expands_to_release_lanes_only(repo_root: Path) -> None:
    result = ci_matrix.expand(_load(repo_root, "dict"), "ci")
    engines = sorted({t["engine"] for t in result["targets"]["include"]})
    check(
        "dict: vinyl-release only, both targets",
        engines == ["vinyl-release"] and result["target_count"] == 2,
        str(result["targets"]["include"]),
    )
    check(
        "dict: one source channel, no trunk lane",
        result["source_count"] == 1 and result["harness_count"] == 0,
    )
    check(
        "dict: the source row carries the published archive URL",
        result["sources"]["include"][0]["archive_url"].endswith("vmod-dict-1.7.tar.gz"),
        str(result["sources"]["include"][0]),
    )


def test_injections_are_confined_to_one_vmod(repo_root: Path) -> None:
    """The two-VMOD isolation property, at the level the tool controls.

    An injection aimed at cachetag must leave every dict row unmarked and
    unmodified, and vice versa. If this were not true the failure-injection
    cases would demonstrate a broken run rather than a contained one.
    """
    cachetag = _load(repo_root, "cachetag")
    dictm = _load(repo_root, "dict")

    for inject, victim, bystander, bystander_data in (
        ("source_checkout", "cachetag", "dict", dictm),
        ("debian_build", "cachetag", "dict", dictm),
        ("suppress_result", "cachetag", "dict", dictm),
        ("dict_source", "dict", "cachetag", cachetag),
        ("dict_build", "dict", "cachetag", cachetag),
        ("recipe_generation", "dict", "cachetag", cachetag),
    ):
        clean = ci_matrix.expand(bystander_data, "ci")
        injected = ci_matrix.expand(bystander_data, "ci", inject=inject)
        check(
            f"isolation: inject={inject} leaves every {bystander} row untouched",
            clean == injected,
            f"{bystander} expansion changed under an injection aimed at {victim}",
        )

    # And the victim really is marked, or the test above would pass vacuously.
    injected = ci_matrix.expand(cachetag, "ci", inject="source_checkout")
    check(
        "isolation: inject=source_checkout does mark cachetag's source row",
        injected["sources"]["include"][0]["ref"] == "vmod-ci-injected-missing-ref",
        str(injected["sources"]["include"][0]),
    )
    injected = ci_matrix.expand(dictm, "ci", inject="dict_source")
    check(
        "isolation: inject=dict_source does mark dict's source row",
        injected["sources"]["include"][0]["ref"] == "vmod-ci-injected-missing-ref",
        str(injected["sources"]["include"][0]),
    )
    injected = ci_matrix.expand(dictm, "ci", inject="recipe_generation")
    marked = [t for t in injected["targets"]["include"] if t["inject_recipe"] == "true"]
    check(
        "isolation: inject=recipe_generation marks exactly one dict target row",
        len(marked) == 1 and marked[0]["family"] == "deb",
        str([(t["target"], t["inject_recipe"]) for t in injected["targets"]["include"]]),
    )
    injected = ci_matrix.expand(cachetag, "ci", inject="debian_build")
    marked = [t for t in injected["targets"]["include"] if t["inject_build"] == "true"]
    check(
        "isolation: inject=debian_build marks only cachetag's Debian rows",
        marked and all(t["family"] == "deb" for t in marked),
        str([(t["target"], t["inject_build"]) for t in injected["targets"]["include"]]),
    )


def test_recipe_generation_status_is_in_the_vocabulary() -> None:
    check(
        "classification: failed_recipe_generation exists",
        "failed_recipe_generation" in ci_matrix.STATUSES,
    )
    check(
        "classification: it is a failure, not an OK status",
        "failed_recipe_generation" not in ci_matrix.OK_STATUSES,
    )
    record = ci_matrix.make_record(
        kind="package-target",
        vmod="dict",
        channel="release",
        engine="vinyl-release",
        target="debian-13-amd64",
        status="failed_recipe_generation",
        stage="generate",
        detail="an unresolved token survived into the generated recipe",
    )
    check(
        "classification: a record carrying it is well formed and keyed to its row",
        record["row_key"] == "target/dict/release/vinyl-release/debian-13-amd64"
        and record["status"] == "failed_recipe_generation",
        str(record),
    )


def test_source_facts_are_emitted_for_a_lane_script(repo_root: Path) -> None:
    facts = ci_matrix.source_facts(_load(repo_root, "dict"), "release")
    check(
        "source-facts: dict's recorded identity, ready for a shell",
        facts["VMOD_SOURCE_REF"] == "v1.7"
        and facts["VMOD_SOURCE_COMMIT"] == "784584d272894a39cf995377618aad551a196424"
        and facts["VMOD_SOURCE_VERSION"] == "1.7"
        and facts["VMOD_CLONE_URL"] == "https://git.gnu.org.ua/vmod-dict.git"
        and facts["VMOD_RECIPE"] == "generated",
        str(facts),
    )
    facts = ci_matrix.source_facts(_load(repo_root, "cachetag"), "release")
    check(
        "source-facts: a GitHub entry gets a derived clone URL",
        facts["VMOD_CLONE_URL"] == "https://github.com/boffinate/libvmod-cachetag.git",
        str(facts),
    )


def test_ledger_covers_both_vmods(repo_root: Path) -> None:
    ledger = ci_matrix.ledger("ci", repo_root)
    keys = sorted(r["row_key"] for r in ledger["rows"] if r["selected"])
    expected = sorted(
        [
            "engine/vinyl-release/debian-13-amd64",
            "engine/vinyl-release/el9-x86_64",
            "engine/vinyl-trunk-pinned/debian-13-amd64",
            "engine/vinyl-trunk-pinned/el9-x86_64",
            "vmod/cachetag",
            "source/cachetag/release",
            "target/cachetag/release/vinyl-release/debian-13-amd64",
            "target/cachetag/release/vinyl-release/el9-x86_64",
            "target/cachetag/release/vinyl-trunk-pinned/debian-13-amd64",
            "target/cachetag/release/vinyl-trunk-pinned/el9-x86_64",
            "vmod/dict",
            "source/dict/release",
            "target/dict/release/vinyl-release/debian-13-amd64",
            "target/dict/release/vinyl-release/el9-x86_64",
        ]
    )
    check("ledger: the ci tier expects exactly these 14 rows", keys == expected, str(keys))
    check(
        "ledger: dict adds no engine row, because it shares vinyl-release",
        sum(1 for r in ledger["rows"] if r["kind"] == "engine" and r["selected"]) == 4,
    )


# --- the checked-in manifest ----------------------------------------------


def test_repo_catalog(repo_root: Path) -> None:
    entries = ci_matrix.discover(repo_root)
    # The selected set, in discovery (file name) order. Asserted exactly rather
    # than by membership: adding a VMOD doubles the per-cohort evidence
    # obligation, so it is a SCOPE.md decision and this test is one of the
    # places that must be updated deliberately when one is made.
    check(
        "repo: the catalog holds exactly the selected VMODs",
        entries
        == [
            {"id": "cachetag", "manifest": "registry/vmods/cachetag.yml"},
            {"id": "dict", "manifest": "registry/vmods/dict.yml"},
        ],
        str(entries),
    )
    path = repo_root / "registry" / "vmods" / "cachetag.yml"
    data = ci_matrix.load_vmod_manifest(path)
    errors = ci_matrix.validate_vmod_manifest(data, "registry/vmods/cachetag.yml", discovery_id="cachetag")
    check("repo: the checked-in cachetag manifest validates", errors == [], str(errors))
    expanded = ci_matrix.expand(data, "ci")
    check(
        "repo: the ci tier expands to the same four package rows the old matrix built",
        sorted((r["engine"], r["target"]) for r in expanded["targets"]["include"])
        == [
            ("vinyl-release", "debian-13-amd64"),
            ("vinyl-release", "el9-x86_64"),
            ("vinyl-trunk-pinned", "debian-13-amd64"),
            ("vinyl-trunk-pinned", "el9-x86_64"),
        ],
        str(expanded["targets"]),
    )
    check(
        "repo: the release source pin is the one the lane pin files carry",
        data["sources"]["release"]["ref"] == "v1.0.1"
        and data["sources"]["release"]["expected_commit"]
        == "a3897aaccf1d6996c00ee14b2c6e1ddac91ac982"
        and data["sources"]["release"]["archive_sha256"] == SHA,
        str(data["sources"]["release"]),
    )


# --- engine artifact metadata ----------------------------------------------

IDENTITY_TEXT = """# scripts/ci/engine-identity.sh, deb
cohort_id=vinyl-9.0.1-ac4f719c16f4
vinyl_track=release
vinyl_source_kind=tarball
vinyl_strict_abi=423648c4cb6b225b3268ffc337354ea938f5efee
vinyl_abi_string=Vinyl Cache 9.0.1 423648c4cb6b225b3268ffc337354ea938f5efee
vinyl_package_version=9.0.1-1
vinyl_source_sha256=2e8ec67cd213ea6864c763939d64912025557342fad2a5ffda6c7c5b59bdeb17
"""


def _engine_fixture(root: Path, identity_text: str = IDENTITY_TEXT) -> tuple:
    packages = root / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    (packages / "vinyl-cache_9.0.1-1_amd64.deb").write_bytes(b"runtime")
    (packages / "vinyl-cache-dev_9.0.1-1_amd64.deb").write_bytes(b"development")
    (packages / "vinyl-cache-dbgsym_9.0.1-1_amd64.deb").write_bytes(b"debug")
    identity_path = root / "engine-identity.env"
    identity_path.write_text(identity_text, encoding="utf-8")
    return packages, identity_path


def test_engine_identity_parsing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _, identity_path = _engine_fixture(root)
        identity = ci_matrix.parse_identity(identity_path)
        check(
            "engine identity: comments and blank lines are skipped, values keep their spaces",
            identity["cohort_id"] == "vinyl-9.0.1-ac4f719c16f4"
            and identity["vinyl_abi_string"]
            == "Vinyl Cache 9.0.1 423648c4cb6b225b3268ffc337354ea938f5efee",
            str(identity),
        )
        for text, why in (
            ("", "an empty identity file"),
            ("cohort_id=x\ncohort_id=y\n", "a duplicated key"),
            ("cohort_id\n", "a line that is not key=value"),
            ("cohort_id=x\nvinyl_track=release\n", "a missing required key"),
            (
                re.sub(r"vinyl_strict_abi=.*", "vinyl_strict_abi=", IDENTITY_TEXT),
                "an empty required key",
            ),
        ):
            bad = root / "bad.env"
            bad.write_text(text, encoding="utf-8")
            failed = False
            try:
                ci_matrix.parse_identity(bad)
            except ci_matrix.EngineMetadataError:
                failed = True
            check(f"engine identity: {why} is rejected", failed, text)


def test_engine_metadata_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        packages, identity_path = _engine_fixture(root)
        (packages / "libvmod-cachetag_1.0.1-1_amd64.deb").write_bytes(b"not the engine")
        identity = ci_matrix.parse_identity(identity_path)
        data = ci_matrix.engine_metadata(
            "vinyl-release", "debian-13-amd64", identity, ci_matrix.describe_packages(packages)
        )
        check(
            "engine metadata: only the engine's own files are recorded",
            [entry["name"] for entry in data["packages"]]
            == [
                "vinyl-cache-dbgsym_9.0.1-1_amd64.deb",
                "vinyl-cache-dev_9.0.1-1_amd64.deb",
                "vinyl-cache_9.0.1-1_amd64.deb",
            ],
            str(data["packages"]),
        )
        check(
            "engine metadata: the artifact address is derived from the row key",
            data["artifact"] == "engine-vinyl-release-debian-13-amd64"
            and data["row_key"] == "engine/vinyl-release/debian-13-amd64"
            and data["family"] == "deb"
            and data["vinyl_track"] == "release",
            json.dumps(data, sort_keys=True),
        )
        check(
            "engine metadata: the resolved identity is inside the artifact",
            data["identity"] == identity and data["schema"] == ci_matrix.ENGINE_SCHEMA,
            json.dumps(data, sort_keys=True),
        )
        check(
            "engine metadata: a consumer with the same pins and files verifies it",
            ci_matrix.verify_engine_metadata(
                data, "vinyl-release", "debian-13-amd64", identity, packages
            )
            == [],
            str(ci_matrix.verify_engine_metadata(
                data, "vinyl-release", "debian-13-amd64", identity, packages
            )),
        )


def test_engine_metadata_rejections() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        packages, identity_path = _engine_fixture(root)
        identity = ci_matrix.parse_identity(identity_path)
        good = ci_matrix.engine_metadata(
            "vinyl-release", "debian-13-amd64", identity, ci_matrix.describe_packages(packages)
        )

        def problems(metadata=None, engine="vinyl-release", target="debian-13-amd64", ident=None,
                     directory=None) -> list:
            return ci_matrix.verify_engine_metadata(
                json.loads(json.dumps(metadata if metadata is not None else good)),
                engine,
                target,
                ident if ident is not None else identity,
                directory or packages,
            )

        check(
            "engine verify: an artifact built for another engine is rejected",
            any("engine" in p for p in problems(engine="vinyl-trunk-pinned")),
            str(problems(engine="vinyl-trunk-pinned")),
        )
        check(
            "engine verify: an artifact built for another target is rejected",
            any("target" in p for p in problems(target="el9-x86_64")),
            str(problems(target="el9-x86_64")),
        )

        drifted = dict(identity, vinyl_strict_abi="0" * 40)
        check(
            "engine verify: an ABI the consumer did not ask for is rejected",
            any("vinyl_strict_abi" in p for p in problems(ident=drifted)),
            str(problems(ident=drifted)),
        )
        extra = dict(identity, vinyl_new_pin="something")
        check(
            "engine verify: a pin the artifact does not record is rejected",
            any("vinyl_new_pin" in p for p in problems(ident=extra)),
            str(problems(ident=extra)),
        )

        tampered = json.loads(json.dumps(good))
        tampered["packages"][0]["sha256"] = "0" * 64
        check(
            "engine verify: a rewritten digest fails the roll-up before the files are read",
            any("packages_sha256" in p for p in problems(metadata=tampered)),
            str(problems(metadata=tampered)),
        )

        swapped = root / "swapped"
        swapped.mkdir()
        for path in packages.iterdir():
            (swapped / path.name).write_bytes(path.read_bytes())
        (swapped / "vinyl-cache_9.0.1-1_amd64.deb").write_bytes(b"a different runtime")
        check(
            "engine verify: a package whose bytes moved is rejected",
            any("sha256" in p for p in problems(directory=swapped)),
            str(problems(directory=swapped)),
        )

        missing = root / "missing"
        missing.mkdir()
        (missing / "vinyl-cache_9.0.1-1_amd64.deb").write_bytes(b"runtime")
        check(
            "engine verify: a package recorded but not delivered is rejected",
            any("not delivered" in p for p in problems(directory=missing)),
            str(problems(directory=missing)),
        )

        smuggled = root / "smuggled"
        smuggled.mkdir()
        for path in packages.iterdir():
            (smuggled / path.name).write_bytes(path.read_bytes())
        (smuggled / "vinyl-cache-extra_9.0.1-1_amd64.deb").write_bytes(b"where did this come from")
        check(
            "engine verify: an engine package nobody recorded is rejected",
            any("not recorded" in p for p in problems(directory=smuggled)),
            str(problems(directory=smuggled)),
        )

        check(
            "engine verify: a foreign schema is rejected outright",
            problems(metadata=dict(good, schema="something-else/v1"))
            == ["engine metadata schema 'something-else/v1' is not 'engine-artifact/v1'"],
            str(problems(metadata=dict(good, schema="something-else/v1"))),
        )

        empty = root / "empty"
        empty.mkdir()
        failed = False
        try:
            ci_matrix.engine_metadata(
                "vinyl-release", "debian-13-amd64", identity, ci_matrix.describe_packages(empty)
            )
        except ci_matrix.EngineMetadataError:
            failed = True
        check("engine metadata: an artifact with no engine packages is refused", failed)

        failed = False
        try:
            ci_matrix.engine_metadata(
                "vinyl-trunk-head",
                "debian-13-amd64",
                identity,
                ci_matrix.describe_packages(packages),
            )
        except ci_matrix.EngineMetadataError:
            failed = True
        check("engine metadata: the source-harness engine has no package artifact", failed)


def test_engine_identity_script_covers_both_lanes() -> None:
    """The one shell script in this path must emit what the tool requires.

    It is not run here -- it sources a lane pin file, which is the lane's job --
    but the key list it prints is the whole of what the comparison compares, so
    a required key that the script never emits would make every verification
    fail at run time and nothing here would notice.
    """
    script = ci_matrix.REPO_ROOT / "scripts" / "ci" / "engine-identity.sh"
    check("engine identity: the script is checked in", script.is_file(), str(script))
    if not script.is_file():
        return
    text = script.read_text(encoding="utf-8")
    for key in ci_matrix.REQUIRED_IDENTITY_KEYS:
        check(
            f"engine identity: the script emits {key}",
            f"emit {key} " in text,
            f"{key} not emitted by {script}",
        )
    check(
        "engine identity: both package families are covered",
        "recipes/debian-13/pins.env" in text and "recipes/el9/cohort.env" in text,
        text,
    )


# Which manifest field each pin name must agree with. The two lanes do not
# agree on a name for the archive digest -- Debian calls it
# CACHETAG_SOURCE_SHA256 and EL9 calls it CACHETAG_SHA256 -- and both are the
# digest the build asserts against, so both are mapped. A guard that knows only
# one of the two names silently ignores the other lane's most load-bearing pin.
PIN_FIELDS = {
    "CACHETAG_REF": "ref",
    "CACHETAG_GIT_COMMIT": "expected_commit",
    "CACHETAG_SOURCE_SHA256": "archive_sha256",
    "CACHETAG_SHA256": "archive_sha256",
    "CACHETAG_VERSION": "version",
}

# The lane pin files and the two workflows that still carry their own copies of
# the cachetag source pins, and exactly which pins each one must carry. ci.yml
# and vmod-package.yml are absent because they read the manifest now;
# nightly-transactions.yml and release-draft.yml migrate in Phase 4 and this
# guard retires with them.
#
# The expected list is per file, not "at least one pin somewhere": a rename
# that this table does not know about must fail loudly rather than quietly
# reduce the guard to checking nothing.
PIN_SOURCES = [
    (
        "recipes/debian-13/pins.env",
        "env",
        ["CACHETAG_VERSION", "CACHETAG_GIT_COMMIT", "CACHETAG_SOURCE_SHA256"],
    ),
    (
        "recipes/el9/cohort.env",
        "env",
        ["CACHETAG_VERSION", "CACHETAG_GIT_COMMIT", "CACHETAG_SHA256"],
    ),
    (
        ".github/workflows/nightly-transactions.yml",
        "yaml",
        ["CACHETAG_REF", "CACHETAG_GIT_COMMIT", "CACHETAG_SOURCE_SHA256"],
    ),
    (
        ".github/workflows/release-draft.yml",
        "yaml",
        ["CACHETAG_REF", "CACHETAG_GIT_COMMIT", "CACHETAG_SOURCE_SHA256"],
    ),
]

# `export FOO=bar`, leading indentation and trailing comments all have to
# parse: a pin the guard cannot read is a pin the guard does not check.
_ENV_PIN_RE = re.compile(r"^\s*(?:export\s+)?(CACHETAG_[A-Z0-9_]+)=(.*)$")
_YAML_PIN_RE = re.compile(r"^\s*(CACHETAG_[A-Z0-9_]+):\s*(.*)$")
_QUOTED_RE = re.compile(r"""^(["'])(.*?)\1""")


def _pin_value(raw: str) -> str:
    """The value of a pin assignment: quotes stripped, trailing comment dropped."""
    raw = raw.strip()
    quoted = _QUOTED_RE.match(raw)
    if quoted:
        return quoted.group(2)
    return raw.split("#", 1)[0].split()[0] if raw.split("#", 1)[0].split() else ""


def _read_pins(path: Path, kind: str) -> dict:
    pattern = _ENV_PIN_RE if kind == "env" else _YAML_PIN_RE
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = pattern.match(line)
        if match and match.group(1) in PIN_FIELDS:
            found.setdefault(match.group(1), _pin_value(match.group(2)))
    return found


def test_pin_parsing() -> None:
    """The guard is only worth having if it can read the assignments it guards."""
    cases = [
        ("CACHETAG_REF=v1.0.1", "v1.0.1"),
        ("export CACHETAG_REF=v1.0.1", "v1.0.1"),
        ("  CACHETAG_REF=v1.0.1", "v1.0.1"),
        ('CACHETAG_REF="v1.0.1"', "v1.0.1"),
        ("CACHETAG_REF='v1.0.1'", "v1.0.1"),
        ("CACHETAG_REF=v1.0.1 # re-pinned 2026-07-28", "v1.0.1"),
        ('CACHETAG_REF="v1.0.1" # re-pinned', "v1.0.1"),
    ]
    for line, expected in cases:
        match = _ENV_PIN_RE.match(line)
        got = _pin_value(match.group(2)) if match else None
        check(f"pins: parses {line!r}", got == expected, repr(got))
    yaml_cases = [
        ("  CACHETAG_REF: v1.0.1", "v1.0.1"),
        ('  CACHETAG_REF: "v1.0.1"', "v1.0.1"),
        ("  CACHETAG_REF: v1.0.1 # mirrored from pins.env", "v1.0.1"),
    ]
    for line, expected in yaml_cases:
        match = _YAML_PIN_RE.match(line)
        got = _pin_value(match.group(2)) if match else None
        check(f"pins: parses {line!r}", got == expected, repr(got))


def test_pins_do_not_drift_from_the_manifest(repo_root: Path) -> None:
    """Every hand-maintained copy of the cachetag source pins must agree.

    The manifest is the record of what cachetag source this project builds, but
    the lane pin files and two unmigrated workflows still carry their own
    copies. Nothing else would notice them drifting apart until a build failed
    on a digest assertion, or worse, did not.
    """
    release = ci_matrix.load_vmod_manifest(repo_root / "registry" / "vmods" / "cachetag.yml")
    release = release["sources"]["release"]
    for relative, kind, expected_pins in PIN_SOURCES:
        path = repo_root / relative
        if not path.is_file():
            check(f"pins: {relative} exists", False, "file not found")
            continue
        pins = _read_pins(path, kind)
        for name in expected_pins:
            if name not in pins:
                check(
                    f"pins: {relative} still carries {name}",
                    False,
                    "not found; a renamed pin must update this guard, not slip past it",
                )
        for name, value in sorted(pins.items()):
            expected = release[PIN_FIELDS[name]]
            check(
                f"pins: {relative} {name} agrees with the manifest",
                value == expected,
                f"{value!r} != manifest {expected!r}",
            )


def main(repo_root: Path = None) -> int:
    root = Path(repo_root) if repo_root else ci_matrix.REPO_ROOT
    _RESULTS.clear()
    test_discovery()
    test_manifest_validation()
    test_source_cross_check()
    test_expansion()
    test_injection_is_confined_to_expansion()
    test_ledger()
    test_ledger_keeps_a_broken_manifest_as_one_row()
    test_reconcile_all_green()
    test_reconcile_classifies_failures()
    test_reconcile_blocked_by_vmod_source()
    test_engine_matrix()
    test_reconcile_blocked_by_engine_artifact()
    test_engine_row_that_never_reported_blocks_its_consumers()
    test_vmod_source_failure_wins_over_an_engine_failure()
    test_engine_identity_parsing()
    test_engine_metadata_round_trip()
    test_engine_metadata_rejections()
    test_engine_identity_script_covers_both_lanes()
    test_reconcile_harness_row_without_a_source_row()
    test_summary_names_optional_failures_on_a_green_run()
    test_reconcile_manifest_validation()
    test_multi_vmod_isolation()
    test_record_precedence_and_validation()
    test_synthesize_missing()
    test_non_github_upstream_is_representable(root)
    test_recipe_strategy_is_recorded(root)
    test_dict_expands_to_release_lanes_only(root)
    test_injections_are_confined_to_one_vmod(root)
    test_recipe_generation_status_is_in_the_vocabulary()
    test_source_facts_are_emitted_for_a_lane_script(root)
    test_ledger_covers_both_vmods(root)
    test_repo_catalog(root)
    test_pin_parsing()
    test_pins_do_not_drift_from_the_manifest(root)

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
