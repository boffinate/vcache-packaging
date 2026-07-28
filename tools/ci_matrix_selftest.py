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
repository: example-org/libvmod-{id}
required: {required}
adapter: cachetag
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
repository: example-org/libvmod-broken
required: true
adapter: cachetag
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
            "ledger: one invocation row, one source row and every lane row",
            keys
            == [
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
        records = [
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
            resolved["counts"]["expected"] == 6 and resolved["counts"]["passed"] == 6,
            json.dumps(resolved["counts"]),
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
        records = [
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
        records = [
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


def test_reconcile_manifest_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _catalog(
            Path(tmp),
            {"cachetag.yml": _manifest_text("cachetag"), "broken.yml": BROKEN_MANIFEST},
        )
        records = [
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

        records = []
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
            all(f"### {v}" in ci_matrix.render_summary(resolved) for v in sorted(by_vmod)),
            ci_matrix.render_summary(resolved),
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


# --- the checked-in manifest ----------------------------------------------


def test_repo_catalog(repo_root: Path) -> None:
    entries = ci_matrix.discover(repo_root)
    check(
        "repo: cachetag is the only catalog entry",
        entries == [{"id": "cachetag", "manifest": "registry/vmods/cachetag.yml"}],
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
    test_reconcile_manifest_validation()
    test_multi_vmod_isolation()
    test_record_precedence_and_validation()
    test_synthesize_missing()
    test_repo_catalog(root)

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
