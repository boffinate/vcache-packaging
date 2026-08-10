#!/usr/bin/env python3
"""Selftests for tools/: yaml_subset, matrix, recipe (run by matrix.py selftest).

Fixture catalogs are written to temp dirs so the tests never depend on the
real engines.yml / vmods/ content; the real templates under packaging/ ARE
used, because rendering them is exactly what the recipe tests must prove.

Standard library only.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matrix  # noqa: E402
import recipe  # noqa: E402
import yaml_subset  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


class Fail(AssertionError):
    pass


def ok(cond, ctx: str) -> None:
    if not cond:
        raise Fail(ctx)


def eq(actual, expected, ctx: str) -> None:
    if actual != expected:
        raise Fail(f"{ctx}: got {actual!r}, expected {expected!r}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_ENGINES = textwrap.dedent(
    """\
    # fixture engine catalog
    schema: engines/1
    engines:
      - id: vinyl-9.0.1
        family: vinyl
        series: vinyl-9.0
        kind: release
        source:
          tarball_url: https://example.org/vinyl-cache-9.0.1.tgz
          sha256: "aa11"
        packages: "true"
        targets:
          - debian-13-amd64
          - el9-x86_64
      - id: varnish-9.0.3
        family: varnish
        series: varnish-9.0
        kind: release
        source:
          tarball_url: https://example.org/varnish-9.0.3.tar.gz
          sha256: "bb22"
        packages: "false"
        targets:
          - debian-13-amd64
      - id: vinyl-trunk
        family: vinyl
        series: vinyl-trunk
        kind: trunk
        source:
          git_url: https://example.org/vinyl.git
          branch: main
        packages: "false"
        targets:
          - debian-13-amd64
    """
)

FIXTURE_DICT = textwrap.dedent(
    """\
    schema: vmod/1
    id: dict
    upstream:
      git: https://example.org/vmod-dict.git
    sources:
      head: master
      default:
        ref: v1.7
        version: "1.7"
      by_series:
        varnish-9.0:
          ref: v1.8
          version: "1.8"
    package:
      summary: Dictionary look-up VMOD
      description:
        - Loads a keyword-to-value dictionary from a disk file and looks
        - keys up from VCL, with reloading support.
      license: GPL-3.0-or-later
      build_deps:
        debian:
          - python3-docutils
        rpm:
          - redhat-rpm-config
          - python3-docutils
    """
)


FIXTURE_MULTI = textwrap.dedent(
    """\
    schema: vmod/1
    id: multi
    upstream:
      git: https://example.org/vmod-multi.git
    sources:
      head: master
      default:
        ref: v0.1
        version: "0.1"
    package:
      summary: Multi-module VMOD
      description:
        - Ships two modules from one source tree.
      license: BSD-2-Clause
      modules:
        - alpha
        - beta_2
    tests: make-check
    """
)


def write_fixture(root: Path, engines: str = FIXTURE_ENGINES, vmods: dict = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if engines is not None:
        (root / "engines.yml").write_text(engines, encoding="utf-8")
    (root / "vmods").mkdir(exist_ok=True)
    for vid, text in (vmods if vmods is not None else {"dict": FIXTURE_DICT}).items():
        (root / "vmods" / f"{vid}.yml").write_text(text, encoding="utf-8")
    return root


def must_replace(text: str, old: str, new: str) -> str:
    if old not in text:
        raise Fail(f"fixture edit missed: {old!r} not found")
    return text.replace(old, new)


def expect_catalog_error(root: Path, needle: str, ctx: str) -> None:
    try:
        matrix.load_catalog(root)
    except matrix.CatalogError as exc:
        ok(needle in str(exc), f"{ctx}: error does not mention {needle!r}: {exc}")
        return
    raise Fail(f"{ctx}: expected CatalogError mentioning {needle!r}, got none")


def run_cli(argv: list) -> tuple:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = matrix.main(argv)
    return code, out.getvalue(), err.getvalue()


def make_cell(row, engine, target, mode, status, finished, **extra):
    cell = {
        "schema": "cell/1", "row": row, "engine": engine, "target": target, "mode": mode,
        "ref": "v1.7", "commit": "abcdef1234567890", "status": status, "detail": "",
        "run_url": "https://example.org/runs/1", "finished_at": finished,
    }
    cell.update(extra)
    return cell


# ---------------------------------------------------------------------------
# yaml_subset
# ---------------------------------------------------------------------------


@test
def yaml_parses_mappings_lists_and_quoting():
    doc = yaml_subset.parse(FIXTURE_ENGINES, "engines.yml")
    eq(doc["schema"], "engines/1", "schema")
    eq(len(doc["engines"]), 3, "engine count")
    first = doc["engines"][0]
    eq(first["targets"], ["debian-13-amd64", "el9-x86_64"], "block sequence of scalars")
    eq(first["source"]["sha256"], "aa11", "quoted scalar")
    eq(first["packages"], "true", "quoted 'true' stays a string")


@test
def yaml_parses_description_list_and_dotted_keys():
    doc = yaml_subset.parse(FIXTURE_DICT, "dict.yml")
    desc = doc["package"]["description"]
    eq(len(desc), 2, "description is a two-line list")
    ok(desc[1].startswith("keys up"), "description line content")
    ok("varnish-9.0" in doc["sources"]["by_series"], "dotted series key")
    eq(doc["sources"]["by_series"]["varnish-9.0"]["ref"], "v1.8", "by_series entry")


@test
def yaml_supports_flow_sequences_and_literals():
    doc = yaml_subset.parse("a: [x, y]\nb: []\nc: |\n  line one\n  line two\n", "t")
    eq(doc["a"], ["x", "y"], "flow sequence")
    eq(doc["b"], [], "empty flow sequence")
    eq(doc["c"], "line one\nline two\n", "literal block")


@test
def yaml_rejects_out_of_subset_input():
    bad = [
        ("a:\tb\n", "tab"),
        ("a: 1\na: 2\n", "duplicate"),
        ("a: 'oops\n", "unterminated"),
        ("a: {x: 1}\n", "unsupported"),
        ("a: [x, {y}]\n", "unsupported"),
        ("a: value \n", "trailing"),
        ("A-Key: v\n", "invalid key"),
        ("a: |\nb: c\n", "no content"),
    ]
    for text, needle in bad:
        try:
            yaml_subset.parse(text, "t")
        except yaml_subset.ManifestSyntaxError as exc:
            ok(needle in str(exc), f"{text!r}: error does not mention {needle!r}: {exc}")
        else:
            raise Fail(f"{text!r}: expected ManifestSyntaxError")


# ---------------------------------------------------------------------------
# Catalog validation
# ---------------------------------------------------------------------------


@test
def catalog_fixture_validates():
    with tempfile.TemporaryDirectory() as tmp:
        catalog = matrix.load_catalog(write_fixture(Path(tmp)))
        eq([e["id"] for e in catalog["engines"]], ["vinyl-9.0.1", "varnish-9.0.3", "vinyl-trunk"], "engines")
        eq(list(catalog["vmods"]), ["dict"], "vmods")


@test
def catalog_missing_files_is_a_clear_error():
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(Path(tmp), "engines.yml", "missing catalog")
        code, _, err = run_cli(["validate", "--root", tmp])
        eq(code, 1, "validate exit code on missing catalog")
        ok("engines.yml" in err, f"validate stderr names the missing file: {err!r}")


@test
def catalog_rejects_unknown_key():
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(FIXTURE_ENGINES, "    packages: \"true\"\n",
                               "    packages: \"true\"\n    surprise: x\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=engines), "unknown key 'surprise'", "unknown key")


@test
def catalog_rejects_missing_required():
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(FIXTURE_ENGINES, "    series: vinyl-9.0\n", "")
        expect_catalog_error(write_fixture(Path(tmp), engines=engines), "missing required key 'series'",
                             "missing series")


@test
def catalog_rejects_trunk_with_tarball():
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(
            FIXTURE_ENGINES,
            "      git_url: https://example.org/vinyl.git\n      branch: main\n",
            "      tarball_url: https://example.org/trunk.tgz\n      sha256: \"cc33\"\n",
        )
        root = write_fixture(Path(tmp), engines=engines)
        expect_catalog_error(root, "missing required key 'branch'", "trunk with tarball")
        expect_catalog_error(root, "unknown key 'tarball_url'", "trunk with tarball")


@test
def catalog_rejects_packages_on_trunk_or_varnish():
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(
            FIXTURE_ENGINES,
            "      branch: main\n    packages: \"false\"\n",
            "      branch: main\n    packages: \"true\"\n",
        )
        expect_catalog_error(write_fixture(Path(tmp), engines=engines),
                             'packages "true" requires kind release', "packages on trunk")
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(
            FIXTURE_ENGINES,
            "      sha256: \"bb22\"\n    packages: \"false\"\n",
            "      sha256: \"bb22\"\n    packages: \"true\"\n",
        )
        expect_catalog_error(write_fixture(Path(tmp), engines=engines),
                             'packages "true" requires family vinyl', "packages on varnish")


@test
def catalog_rejects_unknown_by_series_and_mismatched_id():
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_DICT, "    varnish-9.0:", "    varnish-99.0:")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": vmod}),
                             "no engine declares this series", "unknown series")
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(write_fixture(Path(tmp), vmods={"renamed": FIXTURE_DICT}),
                             "does not match the filename stem", "id/filename mismatch")


@test
def catalog_tests_and_modules_fields():
    with tempfile.TemporaryDirectory() as tmp:
        root = write_fixture(Path(tmp), vmods={"dict": FIXTURE_DICT, "multi": FIXTURE_MULTI})
        catalog = matrix.load_catalog(root)
        eq(catalog["vmods"]["multi"].get("tests"), "make-check", "tests field carried through")
        eq(matrix.vmod_modules(catalog["vmods"]["multi"]), ["alpha", "beta_2"], "explicit modules")
        eq(matrix.vmod_modules(catalog["vmods"]["dict"]), ["dict"], "modules default to [id]")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "tests: make-check", "tests: pytest")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi": vmod}),
                             "tests must be one of", "bad tests value")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "    - beta_2\n", "    - Beta-2\n")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi": vmod}),
                             "not a valid module name", "bad module name")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "  modules:\n    - alpha\n    - beta_2\n",
                            "  modules: []\n")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi": vmod}),
                             "non-empty list", "empty modules list")
    # Hyphens stay legal in VMOD ids (varnish-modules), but such an id cannot
    # be the module-name default, so package.modules becomes required.
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "id: multi\n", "id: multi-mod\n")
        catalog = matrix.load_catalog(write_fixture(Path(tmp), vmods={"multi-mod": vmod}))
        eq(matrix.vmod_modules(catalog["vmods"]["multi-mod"]), ["alpha", "beta_2"],
           "hyphenated id validates with explicit modules")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "id: multi\n", "id: multi-mod\n")
        vmod = must_replace(vmod, "  modules:\n    - alpha\n    - beta_2\n", "")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi-mod": vmod}),
                             "package.modules is required", "hyphenated id without modules")


# ---------------------------------------------------------------------------
# Resolution and versions
# ---------------------------------------------------------------------------


@test
def resolution_rule():
    with tempfile.TemporaryDirectory() as tmp:
        catalog = matrix.load_catalog(write_fixture(Path(tmp)))
        vmod = catalog["vmods"]["dict"]
        release = matrix.find_engine(catalog, "vinyl-9.0.1")
        varnish = matrix.find_engine(catalog, "varnish-9.0.3")
        trunk = matrix.find_engine(catalog, "vinyl-trunk")
        eq(matrix.resolve_source(vmod, release),
           {"source": "default", "ref": "v1.7", "version": "1.7"}, "default")
        eq(matrix.resolve_source(vmod, varnish),
           {"source": "by_series", "ref": "v1.8", "version": "1.8"}, "by_series")
        eq(matrix.resolve_source(vmod, trunk),
           {"source": "head", "ref": "master", "version": ""}, "head")
        eq(matrix.engine_version(release), "9.0.1", "engine version")
        eq(matrix.engine_version(trunk), "trunk", "trunk engine version")
        eq(matrix.engine_package_version(release), {"deb": "9.0.1-1", "rpm": "9.0.1-1%{?dist}"},
           "engine package version")
        eq(matrix.vmod_package_version("1.7", release),
           {"deb": "1.7-1~vinyl9.0.1", "rpm_version": "1.7", "rpm_release": "1.vinyl9.0.1"},
           "vmod package version")
        eq(matrix.vmod_package_name("dict"), "vinyl-vmod-dict", "package name")


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


@test
def expand_release_lane():
    with tempfile.TemporaryDirectory() as tmp:
        catalog = matrix.load_catalog(write_fixture(Path(tmp)))
        expansion = matrix.expand(catalog, "release", "all")
        eq(expansion["engines"], [
            {"engine": "vinyl-9.0.1", "target": "debian-13-amd64"},
            {"engine": "vinyl-9.0.1", "target": "el9-x86_64"},
            {"engine": "varnish-9.0.3", "target": "debian-13-amd64"},
        ], "engine pairs")
        eq(expansion["vmods"], [
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "compat"},
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "package"},
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "el9-x86_64", "mode": "package"},
            {"row": "dict", "engine": "varnish-9.0.3", "target": "debian-13-amd64", "mode": "compat"},
        ], "vmod rows")
        engine_rows = [r for r in expansion["rows"] if r["mode"] == "engine"]
        eq(len(engine_rows), 3, "engine rows in the full list")
        eq(engine_rows[0], {"row": "vinyl-9.0.1", "engine": "vinyl-9.0.1",
                            "target": "debian-13-amd64", "mode": "engine"}, "engine row shape")
        compat_only = matrix.expand(catalog, "release", "compat")
        eq(compat_only["engines"], [
            {"engine": "vinyl-9.0.1", "target": "debian-13-amd64"},
            {"engine": "varnish-9.0.3", "target": "debian-13-amd64"},
        ], "compat engine pairs use first target only")
        ok(all(r["mode"] == "compat" for r in compat_only["vmods"]), "compat filter")


@test
def expand_trunk_lane_and_github_format():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp)))
        catalog = matrix.load_catalog(root)
        expansion = matrix.expand(catalog, "trunk", "all")
        eq(expansion["engines"], [{"engine": "vinyl-trunk", "target": "debian-13-amd64"}], "trunk engines")
        eq(expansion["vmods"],
           [{"row": "dict", "engine": "vinyl-trunk", "target": "debian-13-amd64", "mode": "compat"}],
           "trunk vmod rows are compat only")
        code, out, _ = run_cli(["expand", "--lane", "trunk", "--format", "github", "--root", root])
        eq(code, 0, "expand exit code")
        lines = out.strip().split("\n")
        eq(len(lines), 2, "github format is exactly two lines")
        ok(lines[0].startswith("engines=") and lines[1].startswith("vmods="), "github output keys")
        engines = json.loads(lines[0][len("engines="):])
        vmods = json.loads(lines[1][len("vmods="):])
        ok(engines and vmods, "neither github array is empty")
        ok(all(set(r) >= {"engine", "target"} for r in engines), "engines= row shape")
        ok(all(r["row"] != r["engine"] for r in vmods), "vmods= excludes engine rows")
        code, _, err = run_cli(["expand", "--lane", "trunk", "--mode", "package", "--root", root])
        eq(code, 1, "trunk+package is an error")
        ok("no package cells" in err, "trunk+package error message")


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


@test
def env_output_is_sh_sourceable():
    eq(matrix.sh_quote("it's"), "'it'\\''s'", "single-quote escaping")
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp)))
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict",
                                "--target", "el9-x86_64", "--root", root])
        eq(code, 0, "env exit code")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["ENGINE_VERSION"], "'9.0.1'", "ENGINE_VERSION")
        eq(values["ENGINE_TARBALL_URL"], "'https://example.org/vinyl-cache-9.0.1.tgz'", "tarball url")
        eq(values["TARGET_ID"], "'el9-x86_64'", "TARGET_ID")
        eq(values["VMOD_REF"], "'v1.7'", "VMOD_REF")
        eq(values["VMOD_DEB_VERSION"], "'1.7-1~vinyl9.0.1'", "VMOD_DEB_VERSION")
        eq(values["VMOD_PACKAGE_NAME"], "'vinyl-vmod-dict'", "VMOD_PACKAGE_NAME")
        eq(values["VMOD_BUILD_DEPS"], "'redhat-rpm-config python3-docutils'",
           "rpm build deps for an el9 target")
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "deb-target env exit code")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["VMOD_BUILD_DEPS"], "'python3-docutils'", "debian build deps for a debian target")
        code, out, _ = run_cli(["env", "--engine", "vinyl-trunk", "--vmod", "dict", "--root", root])
        eq(code, 0, "trunk env exit code")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["ENGINE_BRANCH"], "'main'", "trunk branch")
        eq(values["ENGINE_VERSION"], "'trunk'", "trunk engine version placeholder")
        eq(values["VMOD_REF"], "'master'", "trunk vmod ref is head")
        eq(values["VMOD_BUILD_DEPS"], "'python3-docutils'",
           "no --target falls back to the engine's first target's format")
        ok("VMOD_DEB_VERSION" not in values, "no package version for a trunk engine")
        code, _, err = run_cli(["env", "--engine", "vinyl-trunk", "--target", "el9-x86_64", "--root", root])
        eq(code, 1, "target not in engine targets is an error")
        ok("not a target of engine" in err, "target error message")


@test
def env_emits_tests_and_modules():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp), vmods={"dict": FIXTURE_DICT, "multi": FIXTURE_MULTI}))
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "multi",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "env exit code with tests+modules")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["VMOD_TESTS"], "'make-check'", "VMOD_TESTS from the manifest")
        eq(values["VMOD_MODULES"], "'alpha beta_2'", "VMOD_MODULES space-separated")
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "env exit code without tests/modules")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["VMOD_TESTS"], "''", "no tests declared -> empty VMOD_TESTS")
        eq(values["VMOD_MODULES"], "'dict'", "VMOD_MODULES defaults to the id")


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


@test
def merge_newest_wins_and_recursive_glob():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        results = tmp / "results"
        nested = results / "results-vinyl-9.0.1"
        nested.mkdir(parents=True)
        old = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "build_failed",
                        "2026-08-01T00:00:00Z")
        new = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "pass",
                        "2026-08-09T00:00:00Z")
        engine_cell = make_cell("vinyl-9.0.1", "vinyl-9.0.1", "debian-13-amd64", "engine", "pass",
                                "2026-08-09T00:00:00Z")
        (results / "dict--vinyl-9.0.1--debian-13-amd64--compat.json").write_text(json.dumps(old))
        (nested / "dict--vinyl-9.0.1--debian-13-amd64--compat.json").write_text(json.dumps(new))
        (nested / "vinyl-9.0.1--vinyl-9.0.1--debian-13-amd64--engine.json").write_text(json.dumps(engine_cell))
        state_file = tmp / "state.json"
        code, out, _ = run_cli(["merge", "--results-dir", str(results), "--state-file", str(state_file)])
        eq(code, 0, "merge exit code (failed cells are still a success)")
        state = json.loads(state_file.read_text())
        eq(len(state["cells"]), 2, "two distinct cell keys")
        key = "dict/vinyl-9.0.1/debian-13-amd64/compat"
        eq(state["cells"][key]["status"], "pass", "newest finished_at wins")
        # A later merge of only the OLD cell must not regress the state.
        stale_dir = tmp / "stale"
        stale_dir.mkdir()
        (stale_dir / "dict--vinyl-9.0.1--debian-13-amd64--compat.json").write_text(json.dumps(old))
        code, _, _ = run_cli(["merge", "--results-dir", str(stale_dir), "--state-file", str(state_file)])
        eq(code, 0, "stale merge exit code")
        state = json.loads(state_file.read_text())
        eq(state["cells"][key]["status"], "pass", "stale cell does not overwrite a newer one")


@test
def merge_rejects_malformed_cells():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        results = tmp / "results"
        results.mkdir()
        bad = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "exploded",
                        "2026-08-09T00:00:00Z")
        (results / "bad.json").write_text(json.dumps(bad))
        code, _, err = run_cli(["merge", "--results-dir", str(results), "--state-file", str(tmp / "s.json")])
        eq(code, 1, "unknown status fails merge")
        ok("status must be one of" in err, "status error message")
        (results / "bad.json").write_text(json.dumps(
            make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "sideways", "pass", "2026-08-09T00:00:00Z")))
        code, _, err = run_cli(["merge", "--results-dir", str(results), "--state-file", str(tmp / "s.json")])
        eq(code, 1, "unknown mode fails merge")
        ok("mode must be one of" in err, "mode error message")


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


@test
def render_smoke():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        results = tmp / "results"
        results.mkdir()
        cells = [
            make_cell("vinyl-9.0.1", "vinyl-9.0.1", "debian-13-amd64", "engine", "pass",
                      "2026-08-09T00:00:00Z"),
            make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "pass", "2026-08-09T00:00:00Z"),
            make_cell("dict", "vinyl-9.0.1", "el9-x86_64", "package", "package_failed",
                      "2026-08-09T01:00:00Z", detail="rpmbuild exited 1"),
            make_cell("dict", "varnish-9.0.3", "debian-13-amd64", "compat", "infra_failed",
                      "2026-08-09T00:00:00Z"),
        ]
        for i, cell in enumerate(cells):
            (results / f"c{i}.json").write_text(json.dumps(cell))
        state_file = tmp / "state.json"
        run_cli(["merge", "--results-dir", str(results), "--state-file", str(state_file)])
        out_file = tmp / "index.html"
        code, out, _ = run_cli(["render", "--state-file", str(state_file), "--out", str(out_file),
                                "--root", str(root), "--generated-at", "2026-08-10T00:00:00Z"])
        eq(code, 0, "render exit code (failed cells are still a success)")
        html_text = out_file.read_text()
        for needle in ("vinyl-9.0.1", "varnish-9.0.3", "vinyl-trunk", "engine build", "dict",
                       'class="cell PASS"', 'class="cell FAIL"', 'class="cell INFRA"',
                       'class="cell MISSING"', "rpmbuild exited 1", "prefers-color-scheme",
                       "data-theme", "https://example.org/runs/1"):
            ok(needle in html_text, f"rendered page is missing {needle!r}")
        # dict x vinyl-9.0.1 aggregates a pass and a package_failed: worst wins.
        grid = matrix.build_grid(json.loads(state_file.read_text()), matrix.load_catalog(root))
        eq(grid["cells"][("dict", "vinyl-9.0.1")]["bucket"], "FAIL", "worst status colours the cell")
        eq(grid["cells"][("(engine)", "vinyl-9.0.1")]["bucket"], "PASS", "engine cell on the (engine) row")
        eq(grid["rows"][0], "(engine)", "engine row renders first")
        eq(grid["columns"], ["vinyl-9.0.1", "varnish-9.0.3", "vinyl-trunk"], "column order from catalog")
        ok(grid["counts"]["MISSING"] > 0, "cells without data count as missing")


@test
def test_failed_cell_merges_and_renders_red():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        results = tmp / "results"
        results.mkdir()
        cell = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "test_failed",
                         "2026-08-09T00:00:00Z", detail="FAIL: tests/x01.vtc FAIL: tests/x02.vtc")
        (results / "c.json").write_text(json.dumps(cell))
        state_file = tmp / "state.json"
        code, _, _ = run_cli(["merge", "--results-dir", str(results), "--state-file", str(state_file)])
        eq(code, 0, "a test_failed cell passes merge")
        grid = matrix.build_grid(json.loads(state_file.read_text()), matrix.load_catalog(root))
        cell_view = grid["cells"][("dict", "vinyl-9.0.1")]
        eq(cell_view["bucket"], "FAIL", "test_failed renders red, not infra")
        eq(cell_view["text"], "test", "test_failed short label")
        out_file = tmp / "index.html"
        code, _, _ = run_cli(["render", "--state-file", str(state_file), "--out", str(out_file),
                              "--root", str(root), "--generated-at", "2026-08-10T00:00:00Z"])
        eq(code, 0, "render exit code with a test_failed cell")
        html_text = out_file.read_text()
        ok('class="cell FAIL"' in html_text, "test_failed cell gets the FAIL class")
        ok("FAIL: tests/x01.vtc" in html_text, "failing test names survive into the tooltip")


# ---------------------------------------------------------------------------
# recipe
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


@test
def recipe_debian_generation():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        out = tmp / "out"
        written = recipe.generate(root, "dict", "vinyl-9.0.1", "debian-13-amd64", out,
                                  maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        eq(sorted(p.relative_to(out).as_posix() for p in written),
           ["debian/changelog", "debian/control", "debian/copyright", "debian/rules",
            "debian/source/format"], "written files")
        for path in written:
            leftover = recipe.TOKEN_RE.findall(path.read_text())
            eq(leftover, [], f"{path.name}: unresolved tokens")
        control = (out / "debian" / "control").read_text()
        ok("Package: vinyl-vmod-dict" in control, "binary package name")
        ok("vinyl-cache (= 9.0.1-1)" in control, "exact-version engine dependency")
        ok("vinyl-cache-dev (= 9.0.1-1)" in control, "exact-version -dev build dependency")
        ok("python3-docutils" in control, "manifest build_deps included")
        ok(" keys up from VCL, with reloading support." in control, "description lines carried over")
        changelog = (out / "debian" / "changelog").read_text()
        ok("vinyl-vmod-dict (1.7-1~vinyl9.0.1) unstable" in changelog, "debian version")
        ok("Test Maintainer <test@example.org>" in changelog, "maintainer identity")
        rules = out / "debian" / "rules"
        ok(rules.stat().st_mode & 0o111, "rules is executable")
        eq((out / "debian" / "source" / "format").read_text(), "3.0 (quilt)\n", "source format")


@test
def recipe_rpm_generation():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        out = tmp / "out"
        written = recipe.generate(root, "dict", "vinyl-9.0.1", "el9-x86_64", out,
                                  maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        eq([p.name for p in written], ["vinyl-vmod-dict.spec"], "spec filename")
        spec = written[0].read_text()
        eq(recipe.TOKEN_RE.findall(spec), [], "unresolved tokens in spec")
        ok("Name:           vinyl-vmod-dict" in spec, "rpm name")
        ok("Version:        1.7" in spec, "rpm version")
        ok("Release:        1.vinyl9.0.1%{?dist}" in spec, "rpm release")
        ok("Requires:       vinyl-cache = 9.0.1-1%{?dist}" in spec, "exact-version engine requires")
        ok("BuildRequires:  vinyl-cache-devel = 9.0.1-1%{?dist}" in spec, "exact-version -devel requires")
        ok("BuildRequires:  python3-docutils" in spec, "manifest build_deps included")


@test
def recipe_refusals_and_unresolved_tokens():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        for engine, target, needle in [
            ("varnish-9.0.3", "debian-13-amd64", "does not ship packages"),
            ("vinyl-trunk", "debian-13-amd64", "does not ship packages"),
            ("vinyl-9.0.1", "el7-x86_64", "not a target of engine"),
        ]:
            try:
                recipe.generate(root, "dict", engine, target, tmp / "out")
            except matrix.CatalogError as exc:
                ok(needle in str(exc), f"{engine}/{target}: error does not mention {needle!r}: {exc}")
            else:
                raise Fail(f"{engine}/{target}: expected CatalogError")
        try:
            recipe.render_text("t.in", "x @NO_SUCH_TOKEN@ y", {})
        except matrix.CatalogError as exc:
            ok("NO_SUCH_TOKEN" in str(exc), f"unresolved token error: {exc}")
        else:
            raise Fail("expected an unresolved-token error")
        eq(recipe.target_format("debian-13-amd64"), "deb", "deb target format")
        eq(recipe.target_format("el9-x86_64"), "rpm", "rpm target format")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a failing test must not stop the run
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        else:
            print(f"ok   {fn.__name__}")
    total = len(TESTS)
    print(f"{total - failed}/{total} test(s) passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
