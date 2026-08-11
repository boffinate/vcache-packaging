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
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jsonschema_gen  # noqa: E402
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
    targets:
      debian-13-amd64:
        image: debian:13
        format: deb
        runner: ubuntu-24.04
        platform: linux/amd64
        package_arch: amd64
      ubuntu-26.04-amd64:
        image: ubuntu:26.04
        format: deb
        runner: ubuntu-24.04
        platform: linux/amd64
        package_arch: amd64
      el10-x86_64:
        image: almalinux:10
        format: rpm
        runner: ubuntu-24.04
        platform: linux/amd64
        package_arch: x86_64
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
          - el10-x86_64
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
      homepage: https://example.org/dict
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
      promoted: "true"
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
    engine_source: required
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


def shell_failure_detail(log: Path, step: str = "pkg-build") -> str:
    """Run the host-safe shell classifier without invoking a container build."""
    lib = Path(__file__).resolve().parent.parent / "scripts" / "lib.sh"
    proc = subprocess.run(
        ["bash", "-c", '. "$1"; failure_detail "$2" "$3"', "bash", str(lib), str(log), step],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return proc.stdout


# ---------------------------------------------------------------------------
# yaml_subset
# ---------------------------------------------------------------------------


@test
def yaml_parses_mappings_lists_and_quoting():
    doc = yaml_subset.parse(FIXTURE_ENGINES, "engines.yml")
    eq(doc["schema"], "engines/1", "schema")
    eq(len(doc["engines"]), 3, "engine count")
    first = doc["engines"][0]
    eq(first["targets"], ["debian-13-amd64", "el10-x86_64"], "block sequence of scalars")
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
def yaml_rejects_flow_sequences_and_literals():
    bad = [
        ("a: [x, y]\n", "flow sequences are not supported"),
        ("a: []\n", "flow sequences are not supported"),
        ("a: |\n  line one\n  line two\n", "literal block scalars are not supported"),
    ]
    for text, needle in bad:
        try:
            yaml_subset.parse(text, "t")
        except yaml_subset.ManifestSyntaxError as exc:
            ok(needle in str(exc), f"{text!r}: error does not mention {needle!r}: {exc}")
        else:
            raise Fail(f"{text!r}: expected ManifestSyntaxError")


@test
def yaml_rejects_out_of_subset_input():
    bad = [
        ("a:\tb\n", "tab"),
        ("a: 1\na: 2\n", "duplicate"),
        ("a: 'oops\n", "unterminated"),
        ("a: {x: 1}\n", "unsupported"),
        ("a: [x, {y}]\n", "flow sequences are not supported"),
        ("a: value \n", "trailing"),
        ("A-Key: v\n", "invalid key"),
        ("a: |\nb: c\n", "literal block scalars are not supported"),
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
def catalog_target_registry_drives_metadata_and_rejects_bad_entries():
    with tempfile.TemporaryDirectory() as tmp:
        catalog = matrix.load_catalog(write_fixture(Path(tmp)))
        target = matrix.find_target(catalog, "el10-x86_64")
        eq(target["image"], "almalinux:10", "target image comes from registry")
        eq(target["format"], "rpm", "target format comes from registry")
        eq(target["runner"], "ubuntu-24.04", "target runner comes from registry")
        pairs = dict(matrix.env_pairs(catalog, "vinyl-9.0.1", target_id="el10-x86_64"))
        eq(pairs["TARGET_PLATFORM"], "linux/amd64", "target platform is exported")
        eq(pairs["TARGET_PACKAGE_ARCH"], "x86_64", "target package architecture is exported")
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(FIXTURE_ENGINES, "    format: rpm\n", "    format: apk\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=engines), "format must be one of", "bad target format")
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(FIXTURE_ENGINES, "      - debian-13-amd64\n      - el10-x86_64\n", "      - no-such-target\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=engines), "unknown target", "unknown engine target")


@test
def catalog_real_targets_use_native_runners():
    catalog = matrix.load_catalog(matrix.default_root())
    expected = {
        "debian-13-arm64": ("debian:13", "deb", "ubuntu-24.04-arm", "linux/arm64", "arm64"),
        "ubuntu-26.04-arm64": ("ubuntu:26.04", "deb", "ubuntu-24.04-arm", "linux/arm64", "arm64"),
        "el10-aarch64": ("almalinux:10", "rpm", "ubuntu-24.04-arm", "linux/arm64", "aarch64"),
    }
    for target_id, values in expected.items():
        target = matrix.find_target(catalog, target_id)
        eq(tuple(target[key] for key in ("image", "format", "runner", "platform", "package_arch")), values,
           f"{target_id} contract")
    for engine_id in ("vinyl-9.0.1", "varnish-9.0.3"):
        engine = matrix.find_engine(catalog, engine_id)
        ok("debian-13-arm64" in engine["targets"], f"{engine['id']} has Debian ARM64")
        ok("ubuntu-26.04-arm64" in engine["targets"], f"{engine['id']} has Ubuntu ARM64")
    for engine_id in ("vinyl-trunk", "varnish-trunk"):
        engine = matrix.find_engine(catalog, engine_id)
        ok("debian-13-arm64" in engine["targets"], f"{engine_id} has Debian ARM64")
        ok("el10-aarch64" in engine["targets"], f"{engine_id} has EL ARM64")
        ok("ubuntu-26.04-arm64" not in engine["targets"], f"{engine_id} omits Ubuntu ARM64")
    ok("el10-aarch64" in matrix.find_engine(catalog, "vinyl-9.0.1")["targets"], "vinyl release has EL ARM64")


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
        eq(catalog["vmods"]["multi"].get("engine_source"), "required", "engine_source carried through")
        eq(matrix.vmod_modules(catalog["vmods"]["multi"]), ["alpha", "beta_2"], "explicit modules")
        eq(matrix.vmod_modules(catalog["vmods"]["dict"]), ["dict"], "modules default to [id]")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "tests: make-check", "tests: pytest")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi": vmod}),
                             "tests must be one of", "bad tests value")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "engine_source: required", "engine_source: optional")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi": vmod}),
                             "engine_source must be one of", "bad engine_source value")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "    - beta_2\n", "    - Beta-2\n")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi": vmod}),
                             "not a valid module name", "bad module name")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "  modules:\n    - alpha\n    - beta_2\n",
                            "  modules: none\n")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi": vmod}),
                             "non-empty list", "modules must be a list")
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


@test
def catalog_package_families_field():
    with tempfile.TemporaryDirectory() as tmp:
        vmod = FIXTURE_DICT + "  families:\n    - varnish\n"
        catalog = matrix.load_catalog(write_fixture(Path(tmp), vmods={"dict": vmod}))
        eq(catalog["vmods"]["dict"]["package"]["families"], ["varnish"], "families carried through")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = FIXTURE_DICT + "  families:\n    - fastly\n"
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": vmod}),
                             "family must be one of", "unknown family")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = FIXTURE_DICT + "  families:\n    - varnish\n    - varnish\n"
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": vmod}),
                             "package.families contains duplicates", "duplicate family")
    # The field must be a list; absent means every family.
    with tempfile.TemporaryDirectory() as tmp:
        vmod = FIXTURE_DICT + "  families: none\n"
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": vmod}),
                             "non-empty list", "families must be a list")


@test
def catalog_promoted_and_package_targets_fields():
    with tempfile.TemporaryDirectory() as tmp:
        vmod = FIXTURE_DICT + "  targets:\n    - el10-x86_64\n"
        catalog = matrix.load_catalog(write_fixture(Path(tmp), vmods={"dict": vmod}))
        eq(catalog["vmods"]["dict"]["package"]["promoted"], "true", "promoted carried through")
        eq(catalog["vmods"]["dict"]["package"]["targets"], ["el10-x86_64"], "targets carried through")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_DICT, 'promoted: "true"', "promoted: yes")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": vmod}),
                             'promoted must be "true" or "false"', "bad promoted value")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = FIXTURE_DICT + "  targets:\n    - debian-14-amd64\n"
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": vmod}),
                             "unknown target", "unknown package target")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = FIXTURE_DICT + "  targets:\n    - el10-x86_64\n    - el10-x86_64\n"
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": vmod}),
                             "package.targets contains duplicates", "duplicate package target")
    # The field must be a list; absent means every target.
    with tempfile.TemporaryDirectory() as tmp:
        vmod = FIXTURE_DICT + "  targets: none\n"
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": vmod}),
                             "non-empty list", "package targets must be a list")


# ---------------------------------------------------------------------------
# Editor JSON Schemas (DESIGN.md decision 11) - generated outputs, so what
# these tests guard is that they cannot drift from the validator.
# ---------------------------------------------------------------------------


@test
def schema_files_match_the_generator():
    problems = jsonschema_gen.check(matrix.default_root() / "schemas")
    ok(not problems, "checked-in schemas/ differ from the generator:\n" + "\n".join(problems))


@test
def schema_covers_every_keys_entry():
    covered = jsonschema_gen.covered_kinds()
    missing = sorted(set(matrix.KEYS) - covered)
    unknown = sorted(covered - set(matrix.KEYS))
    ok(not missing, f"matrix.KEYS entries absent from the editor schemas: {missing}")
    ok(not unknown, f"schemas describe mappings not in matrix.KEYS: {unknown}")


@test
def schema_documents_are_shaped_as_the_language_server_needs():
    docs = jsonschema_gen.build_all()
    eq(sorted(docs), ["engines.schema.json", "vmod.schema.json"], "generated files")
    for name, doc in docs.items():
        eq(doc["$schema"], jsonschema_gen.DRAFT, f"{name}: dialect")
        # additionalProperties:false everywhere is what turns a typo into a
        # squiggle rather than a silently ignored key.
        stack = [doc]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("type") == "object" and "patternProperties" not in node:
                    ok("additionalProperties" in node and node["additionalProperties"] is False,
                       f"{name}: an object node allows additional properties: {sorted(node)}")
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    engines = docs["engines.schema.json"]
    engine = engines["properties"]["engines"]["items"]
    eq(engine["properties"]["family"]["enum"], list(matrix.FAMILIES), "family enum tracks matrix.FAMILIES")
    eq(engine["properties"]["kind"]["enum"], list(matrix.KINDS), "kind enum tracks matrix.KINDS")
    eq(engines["properties"]["schema"]["const"], matrix.ENGINES_SCHEMA, "engines schema marker")
    vmod = docs["vmod.schema.json"]
    eq(vmod["properties"]["tests"]["enum"], list(matrix.TESTS_VALUES), "tests enum tracks matrix.TESTS_VALUES")
    eq(vmod["properties"]["engine_source"]["enum"], list(matrix.ENGINE_SOURCE_VALUES),
       "engine_source enum tracks matrix.ENGINE_SOURCE_VALUES")
    eq(vmod["properties"]["package"]["properties"]["modules"]["items"]["pattern"],
       matrix.MODULE_NAME_RE.pattern, "module name pattern tracks matrix.MODULE_NAME_RE")


@test
def schema_cli_writes_and_detects_drift():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "schemas"
        code, stdout, _ = run_cli(["schema", "--out", str(out)])
        eq(code, 0, "schema exit code")
        ok("engines.schema.json" in stdout, "schema names what it wrote")
        for name in jsonschema_gen.SCHEMA_FILES:
            json.loads((out / name).read_text(encoding="utf-8"))
        eq(run_cli(["schema", "--out", str(out), "--check"])[0], 0, "check passes on fresh output")
        # A hand-patched schema must be caught: these are outputs.
        target = out / "vmod.schema.json"
        target.write_text(target.read_text(encoding="utf-8").replace('"title"', '"tilte"', 1), encoding="utf-8")
        code, _, stderr = run_cli(["schema", "--out", str(out), "--check"])
        eq(code, 1, "check exit code on drift")
        ok("does not match the generator" in stderr, f"drift message: {stderr!r}")
        target.unlink()
        eq(run_cli(["schema", "--out", str(out), "--check"])[0], 1, "check exit code on a missing file")


@test
def catalog_files_carry_a_language_server_modeline():
    root = matrix.default_root()
    files = [(root / "engines.yml", "schemas/engines.schema.json")]
    files += [(p, "../schemas/vmod.schema.json") for p in sorted((root / "vmods").glob("*.yml"))]
    for path, target in files:
        first = path.read_text(encoding="utf-8").splitlines()[0]
        eq(first, jsonschema_gen.MODELINE.format(path=target), f"{path.name}: first line")
        ok((path.parent / target).is_file(), f"{path.name}: modeline points at a real schema file")


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
            {"engine": "vinyl-9.0.1", "target": "debian-13-amd64", "runner": "ubuntu-24.04"},
            {"engine": "vinyl-9.0.1", "target": "el10-x86_64", "runner": "ubuntu-24.04"},
            {"engine": "varnish-9.0.3", "target": "debian-13-amd64", "runner": "ubuntu-24.04"},
        ], "engine pairs")
        eq(expansion["vmods"], [
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04"},
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "el10-x86_64", "mode": "compat", "runner": "ubuntu-24.04"},
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "package", "runner": "ubuntu-24.04"},
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "el10-x86_64", "mode": "package", "runner": "ubuntu-24.04"},
            {"row": "dict", "engine": "varnish-9.0.3", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04"},
        ], "vmod rows")
        engine_rows = [r for r in expansion["rows"] if r["mode"] == "engine"]
        eq(len(engine_rows), 3, "engine rows in the full list")
        eq(engine_rows[0], {"row": "vinyl-9.0.1", "engine": "vinyl-9.0.1",
                            "target": "debian-13-amd64", "mode": "engine", "runner": "ubuntu-24.04"}, "engine row shape")
        compat_only = matrix.expand(catalog, "release", "compat")
        eq(compat_only["engines"], [
            {"engine": "vinyl-9.0.1", "target": "debian-13-amd64", "runner": "ubuntu-24.04"},
            {"engine": "vinyl-9.0.1", "target": "el10-x86_64", "runner": "ubuntu-24.04"},
            {"engine": "varnish-9.0.3", "target": "debian-13-amd64", "runner": "ubuntu-24.04"},
        ], "compat engine pairs use every target")
        eq(compat_only["vmods"], [
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04"},
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "el10-x86_64", "mode": "compat", "runner": "ubuntu-24.04"},
            {"row": "dict", "engine": "varnish-9.0.3", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04"},
        ], "compat vmod rows use every target")
        ok(all(r["mode"] == "compat" for r in compat_only["vmods"]), "compat filter")


@test
def expand_package_rows_honour_families():
    # packages "true" requires family vinyl, so no varnish engine can emit
    # package rows to filter; assert the gate both ways on the vinyl engine.
    def with_families(family):
        vmod = must_replace(FIXTURE_DICT, "id: dict\n", f"id: {family}only\n")
        return vmod + f"  families:\n    - {family}\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = write_fixture(Path(tmp), vmods={
            "dict": FIXTURE_DICT,
            "vinylonly": with_families("vinyl"),
            "varnishonly": with_families("varnish"),
        })
        catalog = matrix.load_catalog(root)
        expansion = matrix.expand(catalog, "release", "all")
        package_rows = [r for r in expansion["vmods"] if r["mode"] == "package"]
        eq({r["row"] for r in package_rows}, {"dict", "vinylonly"},
           "families gates package rows; absent means unrestricted")
        eq(len([r for r in package_rows if r["row"] == "vinylonly"]), 2,
           "a listed family keeps its package rows on every target")
        vinyl_compat = {r["row"] for r in expansion["vmods"]
                        if r["mode"] == "compat" and r["engine"] == "vinyl-9.0.1"}
        eq(vinyl_compat, {"dict", "vinylonly", "varnishonly"},
           "compat rows ignore families entirely")
        package_only = matrix.expand(catalog, "release", "package")
        eq({r["row"] for r in package_only["vmods"]}, {"dict", "vinylonly"},
           "--mode package applies the same gate")


@test
def expand_package_rows_honour_promotion_and_targets():
    with tempfile.TemporaryDirectory() as tmp:
        held = must_replace(FIXTURE_DICT, '  promoted: "true"\n', "")
        held = must_replace(held, "id: dict\n", "id: held\n")
        restricted = FIXTURE_DICT + "  targets:\n    - el10-x86_64\n"
        catalog = matrix.load_catalog(write_fixture(Path(tmp), vmods={
            "held": held,
            "dict": restricted,
        }))
        rows = matrix.expand(catalog, "release", "all")["vmods"]
        package = [(r["row"], r["target"]) for r in rows if r["mode"] == "package"]
        eq(package, [("dict", "el10-x86_64")],
           "unpromoted vmods expand no package rows; package.targets restricts the rest")
        held_compat = [r for r in rows if r["mode"] == "compat" and r["row"] == "held"]
        ok(len(held_compat) > 0, "an unpromoted vmod keeps every compat cell")
        package_only = matrix.expand(catalog, "release", "package")
        eq({r["row"] for r in package_only["vmods"]}, {"dict"},
           "--mode package applies the promotion gate too")


@test
def expand_trunk_lane_and_github_format():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp)))
        catalog = matrix.load_catalog(root)
        expansion = matrix.expand(catalog, "trunk", "all")
        eq(expansion["engines"], [{"engine": "vinyl-trunk", "target": "debian-13-amd64", "runner": "ubuntu-24.04"}], "trunk engines")
        eq(expansion["vmods"],
           [{"row": "dict", "engine": "vinyl-trunk", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04"}],
           "trunk vmod rows are compat only")
        code, out, _ = run_cli(["expand", "--lane", "trunk", "--format", "github", "--root", root])
        eq(code, 0, "expand exit code")
        lines = out.strip().split("\n")
        eq(len(lines), 3, "github format is exactly three lines")
        ok(lines[0].startswith("engines=") and lines[1].startswith("vmods=")
           and lines[2].startswith("vmod_shards="), "github output keys")
        engines = json.loads(lines[0][len("engines="):])
        vmods = json.loads(lines[1][len("vmods="):])
        shards = json.loads(lines[2][len("vmod_shards="):])
        ok(engines and vmods, "neither github array is empty")
        ok(all(set(r) >= {"engine", "target", "runner"} for r in engines), "engines= row shape")
        ok(all(r["row"] != r["engine"] for r in vmods), "vmods= excludes engine rows")
        eq([row for shard in shards for row in json.loads(shard["items"])], vmods,
           "vmod_shards preserves every VMOD row")
        code, _, err = run_cli(["expand", "--lane", "trunk", "--mode", "package", "--root", root])
        eq(code, 1, "trunk+package is an error")
        ok("no package cells" in err, "trunk+package error message")


@test
def vmod_shards_are_bounded_and_ordered():
    rows = [{"row": str(index)} for index in range(matrix.VMOD_SHARD_SIZE * 2 + 1)]
    shards = matrix.shard_vmods(rows)
    eq([shard["shard"] for shard in shards], ["1/3", "2/3", "3/3"], "shard labels")
    eq([len(json.loads(shard["items"])) for shard in shards],
       [matrix.VMOD_SHARD_SIZE, matrix.VMOD_SHARD_SIZE, 1], "shard sizes")
    eq([row for shard in shards for row in json.loads(shard["items"])], rows,
       "shards preserve order and rows")


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


@test
def env_output_is_sh_sourceable():
    eq(matrix.sh_quote("it's"), "'it'\\''s'", "single-quote escaping")
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp)))
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict",
                                "--target", "el10-x86_64", "--root", root])
        eq(code, 0, "env exit code")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["ENGINE_VERSION"], "'9.0.1'", "ENGINE_VERSION")
        eq(values["ENGINE_TARBALL_URL"], "'https://example.org/vinyl-cache-9.0.1.tgz'", "tarball url")
        eq(values["TARGET_ID"], "'el10-x86_64'", "TARGET_ID")
        eq(values["VMOD_REF"], "'v1.7'", "VMOD_REF")
        eq(values["VMOD_DEB_VERSION"], "'1.7-1~vinyl9.0.1'", "VMOD_DEB_VERSION")
        eq(values["VMOD_PACKAGE_NAME"], "'vinyl-vmod-dict'", "VMOD_PACKAGE_NAME")
        eq(values["VMOD_BUILD_DEPS"], "'redhat-rpm-config python3-docutils'",
           "rpm build deps for an el10 target")
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
        code, _, err = run_cli(["env", "--engine", "vinyl-trunk", "--target", "el10-x86_64", "--root", root])
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
        eq(values["VMOD_ENGINE_SOURCE"], "'required'", "VMOD_ENGINE_SOURCE from the manifest")
        eq(values["VMOD_MODULES"], "'alpha beta_2'", "VMOD_MODULES space-separated")
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "env exit code without tests/modules")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["VMOD_TESTS"], "''", "no tests declared -> empty VMOD_TESTS")
        eq(values["VMOD_ENGINE_SOURCE"], "''", "no engine_source declared -> empty VMOD_ENGINE_SOURCE")
        eq(values["VMOD_MODULES"], "'dict'", "VMOD_MODULES defaults to the id")


# ---------------------------------------------------------------------------
# shell failure details
# ---------------------------------------------------------------------------


@test
def shell_failure_details_prefer_causes_over_rpm_epilogues():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        dict_log = tmp / "dict.log"
        dict_log.write_text(
            "aclocal: warning: couldn't open directory 'm4': No such file or directory\n"
            "checking command ... ./configure: line 8133: No such file or directory\n"
            "FileNotFoundError: [Errno 2] No such file or directory: 'vcc_if.c.tmp2'\n"
            "make[2]: *** [Makefile:784: vcc_if.h] Error 1\n"
            "RPM build errors:\n"
            "error: Bad exit status from /var/tmp/rpm-tmp.x (%build)\n"
            "    Bad exit status from /var/tmp/rpm-tmp.x (%build)\n"
        )
        eq(shell_failure_detail(dict_log),
           "FileNotFoundError: [Errno 2] No such file or directory: 'vcc_if.c.tmp2'",
           "RPM detail keeps the vmodtool race rather than its footer")

        automake_log = tmp / "automake.log"
        automake_log.write_text(
            "configure.ac:28: error: require Automake 1.16.5, but have 1.16.2\n"
            "autoreconf: error: automake failed with exit status: 1\n"
            "RPM build errors:\n"
            "    Macro expanded in comment on line 12: %make_build and %make_install.\n"
            "    Bad exit status from /var/tmp/rpm-tmp.x (%build)\n"
        )
        eq(shell_failure_detail(automake_log),
           "configure.ac:28: error: require Automake 1.16.5, but have 1.16.2\n"
           "autoreconf: error: automake failed with exit status: 1",
           "RPM detail keeps the Autotools cause rather than a macro warning")

        generic_log = tmp / "generic.log"
        generic_log.write_text("first\nsecond\nthird\nfourth\n")
        eq(shell_failure_detail(generic_log, "load"), "second\nthird\nfourth",
           "non-package failures retain the log-tail fallback")


@test
def shell_failure_details_preserve_compat_make_diagnostics():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        race_log = tmp / "race.log"
        race_log.write_text(
            "FileNotFoundError: [Errno 2] No such file or directory: 'vcc_if.c.tmp2'\n"
            "make[2]: *** [Makefile:784: vcc_if.h] Error 1\n"
            "make[1]: *** [Makefile:514: all-recursive] Error 1\n"
            "make: *** [Makefile:425: all] Error 2\n"
        )
        eq(shell_failure_detail(race_log, "make"),
           "FileNotFoundError: [Errno 2] No such file or directory: 'vcc_if.c.tmp2'",
           "compat detail retains a generated-source race")

        api_log = tmp / "api.log"
        api_log.write_text(
            "match.c:55:17: error: implicit declaration of function 'WS_Assert_Allocated'; "
            "did you mean 'WS_Allocated'? [-Wimplicit-function-declaration]\n"
            "make[2]: *** [Makefile:750: match.lo] Error 1\n"
            "make[1]: *** [Makefile:514: all-recursive] Error 1\n"
            "make: *** [Makefile:425: all] Error 2\n"
        )
        eq(shell_failure_detail(api_log, "make"),
           "match.c:55:17: error: implicit declaration of function 'WS_Assert_Allocated'; "
           "did you mean 'WS_Allocated'? [-Wimplicit-function-declaration]",
           "compat detail retains an API compiler error")


@test
def vmod_compat_build_is_serial():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-vmod.sh").read_text()
    ok('make -j"$(nproc)" || make' not in script, "compat build does not retry a parallel make")
    ok("# VMOD generators are not reliably parallel-safe.\nmake -j1" in script,
       "compat build is serial from the outset")


@test
def package_load_failure_reports_the_end_of_compiler_output():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-vmod.sh").read_text()
    ok('tail -n 40 /tmp/load.log' in script, "package load failure prints diagnostic tail")
    ok('sed -n \'1,40p\' /tmp/load.log' not in script,
       "package load failure no longer ends on a source-file header")


@test
def engine_daemon_smoke_check_preserves_failure():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-engine.sh").read_text()
    ok('"$DAEMON" -V 2>&1\n' in script, "engine smoke check executes the daemon directly")
    ok('"$DAEMON" -V 2>&1 | head -2 || true' not in script,
       "engine smoke check does not discard the daemon exit status")


@test
def missing_engine_artifact_reaches_vmod_classifier():
    workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows" /
                "vmod-shard.yml").read_text()
    start = workflow.index("      - uses: actions/download-artifact@v8")
    end = workflow.index("      - run: scripts/build-vmod.sh", start)
    download_step = workflow[start:end]
    ok("continue-on-error: true" in download_step,
       "a missing engine artifact does not stop the job before build-vmod.sh classifies it")


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
            make_cell("dict", "vinyl-9.0.1", "el10-x86_64", "package", "package_failed",
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
        eq(html_text.count('class="target-matrix"'), 2, "one rendered matrix per target")
        ok('<h2 class="target">debian-13-amd64' in html_text, "Debian matrix heading")
        ok('<h2 class="target">el10-x86_64' in html_text, "EL10 matrix heading")
        ok('<title>Vinyl Cache and Varnish Cache VMOD compatibility matrix</title>' in html_text,
           "page title names both cache projects")
        ok('<h1><span>Vinyl Cache and Varnish Cache</span><span class="title-context">VMOD compatibility matrix</span></h1>'
           in html_text, "page heading names both cache projects on compact lines")
        ok('header.page{display:flex;flex-wrap:wrap;align-items:center;min-height:65px;' in html_text,
           "two-line heading keeps the existing header height")
        ok('<time datetime="2026-08-10T00:00:00Z">10 August 2026 at 00:00 UTC</time>' in html_text,
           "generated timestamp is a human-readable time element")
        ok('<td class="rid"><a href="https://example.org/dict" target="_blank" rel="noopener">dict</a></td>'
           in html_text, "VMOD row links to its configured homepage")
        state = json.loads(state_file.read_text())
        catalog = matrix.load_catalog(root)
        eq(matrix.matrix_targets(state, catalog), ["debian-13-amd64", "el10-x86_64"], "target order from catalog")
        debian_grid = matrix.build_grid(state, "debian-13-amd64", catalog)
        el10_grid = matrix.build_grid(state, "el10-x86_64", catalog)
        eq(debian_grid["cells"][("dict", "vinyl-9.0.1")]["bucket"], "PASS", "Debian status stays separate")
        eq(el10_grid["cells"][("dict", "vinyl-9.0.1")]["bucket"], "FAIL", "EL10 failure stays separate")
        eq(debian_grid["cells"][("(engine)", "vinyl-9.0.1")]["bucket"], "PASS", "engine cell on the (engine) row")
        eq(debian_grid["rows"][0], "(engine)", "engine row renders first")
        eq(debian_grid["columns"], ["vinyl-9.0.1", "varnish-9.0.3", "vinyl-trunk"], "Debian column order from catalog")
        eq(el10_grid["columns"], ["vinyl-9.0.1"], "EL10 excludes unsupported engines")
        ok(debian_grid["counts"]["MISSING"] > 0, "cells without data count as missing")


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
        grid = matrix.build_grid(json.loads(state_file.read_text()), "debian-13-amd64", matrix.load_catalog(root))
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


@test
def key_line_is_exact_and_tooltips_speak_human():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        results = tmp / "results"
        results.mkdir()
        cells = [
            make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "pass", "2026-08-09T00:00:00Z"),
            make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "package", "package_failed",
                      "2026-08-09T01:00:00Z"),
            make_cell("redis", "vinyl-9.0.1", "debian-13-amd64", "compat", "configure_failed",
                      "2026-08-09T00:00:00Z"),
        ]
        for i, cell in enumerate(cells):
            (results / f"c{i}.json").write_text(json.dumps(cell))
        state_file = tmp / "state.json"
        run_cli(["merge", "--results-dir", str(results), "--state-file", str(state_file)])
        grid = matrix.build_grid(json.loads(state_file.read_text()), "debian-13-amd64", matrix.load_catalog(root))
        eq(grid["cells"][("dict", "vinyl-9.0.1")]["bucket"], "FAIL",
           "mixed cell keeps the worst-across-modes colour fold")
        redis_title = grid["cells"][("redis", "vinyl-9.0.1")]["title"]
        ok("compat: This module fails to compile or load against this engine "
           "(usually: upstream does not support this engine yet). (configure_failed)" in redis_title,
           "failing compat tooltip carries the human sentence plus its status")
        dict_title = grid["cells"][("dict", "vinyl-9.0.1")]["title"]
        ok("compat: This module compiles from source against this engine and loads. (pass)" in dict_title,
           "passing compat tooltip line speaks human")
        ok("package: The ready-to-install package (.deb/.rpm) failed to build or install. "
           "(package_failed)" in dict_title, "failing package tooltip line speaks human")
        ok("[v1.7 @ abcdef123456]" in dict_title, "ref and commit stay in the tooltip")
        ok("(2026-08-09T01:00:00Z)" in dict_title, "timestamps stay in the tooltip")
        out_file = tmp / "index.html"
        code, _, _ = run_cli(["render", "--state-file", str(state_file), "--out", str(out_file),
                              "--root", str(root), "--generated-at", "2026-08-10T00:00:00Z"])
        eq(code, 0, "render exit code")
        html_text = out_file.read_text()
        eq(html_text.count('class="matrix-key"'), 1, "the key line renders exactly once")
        ok('<p class="matrix-key">Rows are modules, columns are engine versions. Green: works. '
           "Red: doesn't — usually upstream doesn't support that engine yet. Grey: not tested.</p>"
           in html_text, "key line is exactly the contract text")
        ok('class="cell FAIL"' in html_text, "mixed cell td keeps the worst-fold class")


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
        rules_text = rules.read_text()
        ok("override_dh_autoreconf:" in rules_text,
           "Debian recipe takes responsibility for upstream bootstrap")
        ok("if [ -f bootstrap ]; then sh ./bootstrap" in rules_text,
           "Debian recipe prefers an upstream bootstrap script")
        ok("elif [ -f autogen.sh ]; then sh ./autogen.sh" in rules_text,
           "Debian recipe falls back to an upstream autogen script")
        ok("else autoreconf -fi; fi" in rules_text,
           "Debian recipe retains an autoreconf fallback")
        eq((out / "debian" / "source" / "format").read_text(), "3.0 (quilt)\n", "source format")
        ubuntu_engines = must_replace(
            FIXTURE_ENGINES,
            "      - debian-13-amd64\n      - el10-x86_64\n",
            "      - debian-13-amd64\n      - ubuntu-26.04-amd64\n      - el10-x86_64\n",
        )
        ubuntu_root = write_fixture(tmp / "ubuntu-repo", engines=ubuntu_engines)
        ubuntu_out = tmp / "ubuntu-out"
        ubuntu_written = recipe.generate(
            ubuntu_root, "dict", "vinyl-9.0.1", "ubuntu-26.04-amd64", ubuntu_out,
            maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW,
        )
        eq(sorted(p.relative_to(ubuntu_out).as_posix() for p in ubuntu_written),
           ["debian/changelog", "debian/control", "debian/copyright", "debian/rules",
            "debian/source/format"], "Ubuntu uses the shared Debian recipe templates")


@test
def recipe_rpm_generation():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        out = tmp / "out"
        written = recipe.generate(root, "dict", "vinyl-9.0.1", "el10-x86_64", out,
                                  maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        eq([p.name for p in written], ["vinyl-vmod-dict.spec"], "spec filename")
        spec = written[0].read_text()
        eq(recipe.TOKEN_RE.findall(spec), [], "unresolved tokens in spec")
        ok("Name:           vinyl-vmod-dict" in spec, "rpm name")
        ok("Version:        1.7" in spec, "rpm version")
        ok("Release:        1.vinyl9.0.1%{?dist}" in spec, "rpm release")
        ok("Requires:       vinyl-cache%{?_isa} = 9.0.1-1%{?dist}" in spec,
           "exact-version arch-qualified engine requires")
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
        catalog = matrix.load_catalog(root)
        eq(recipe.target_format(catalog, "debian-13-amd64"), "deb", "deb target format")
        eq(recipe.target_format(catalog, "ubuntu-26.04-amd64"), "deb", "Ubuntu target format")
        eq(recipe.target_format(catalog, "el10-x86_64"), "rpm", "rpm target format")


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
