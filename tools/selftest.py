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
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine_batch  # noqa: E402
import engine_cache  # noqa: E402
import jsonschema_gen  # noqa: E402
import matrix  # noqa: E402
import package_contract  # noqa: E402
import recipe  # noqa: E402
import release_gate  # noqa: E402
import source_api_normalize  # noqa: E402
import source_batch  # noqa: E402
import source_digest  # noqa: E402
import vmod_batch  # noqa: E402
import vmod_cache  # noqa: E402
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
        package_revision: "1"
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
        pkgconfig_version: "9.99.0"
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
        commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
        version: "1.7"
      by_series:
        varnish-9.0:
          ref: v1.8
          commit: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
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
    source_api_family: varnish
    """
)

FIXTURE_CARGO = textwrap.dedent(
    """\
    schema: vmod/1
    id: reqwest
    upstream:
      git: https://example.org/vmod-reqwest.git
    sources:
      head: main
      default:
        ref: v0.1.0
        version: "0.1.0"
    build: cargo
    tests: cargo-test
    package:
      summary: HTTP client VMOD
      description:
        - Sends HTTP requests from VCL.
      license: BSD-3-Clause
      modules:
        - reqwest
      artifacts:
        - libvmod_reqwest.so
      cargo_features:
        - vmod
    """
)


def cargo_fixture_engines() -> str:
    return must_replace(
        FIXTURE_ENGINES,
        "schema: engines/1\n",
        "schema: engines/1\ntoolchains:\n  rust:\n    version: \"1.90.0\"\n    bootstrap: rustup\n",
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


def varnish_package_fixture(include_rpm: bool = False) -> str:
    """A proof fixture only: the real Varnish catalog row stays disabled."""
    engines = must_replace(
        FIXTURE_ENGINES,
        '      sha256: "bb22"\n    packages: "false"\n',
        '      sha256: "bb22"\n    packages: "true"\n    package_revision: "1"\n',
    )
    if include_rpm:
        engines = must_replace(
            engines,
            "    targets:\n      - debian-13-amd64\n  - id: vinyl-trunk\n",
            "    targets:\n      - debian-13-amd64\n      - el10-x86_64\n  - id: vinyl-trunk\n",
        )
    return engines


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


def without_source_artifacts(rows: list) -> list:
    return [{key: value for key, value in row.items() if key != "source_artifact"} for row in rows]


def parse_env_output(output: str) -> dict:
    return dict(line.split("=", 1) for line in output.strip().splitlines())


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


def shell_status_for_step(step: str, mode: str = "") -> str:
    lib = Path(__file__).resolve().parent.parent / "scripts" / "lib.sh"
    proc = subprocess.run(
        ["bash", "-c", '. "$1"; status_for_step "$2" "$3"', "bash", str(lib), step, mode],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return proc.stdout.strip()


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
def catalog_source_api_family_is_autotools_only():
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_DICT, "id: dict\n", "id: dict\nsource_api_family: other\n")
        expect_catalog_error(
            write_fixture(Path(tmp), vmods={"dict": vmod}),
            "source_api_family must be one of",
            "unknown source API family",
        )
    with tempfile.TemporaryDirectory() as tmp:
        cargo = must_replace(FIXTURE_CARGO, "id: reqwest\n", "id: reqwest\nsource_api_family: varnish\n")
        expect_catalog_error(
            write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": cargo}),
            "source_api_family is only supported for build autotools",
            "Cargo normalization",
        )


@test
def catalog_target_registry_drives_metadata_and_rejects_bad_entries():
    with tempfile.TemporaryDirectory() as tmp:
        catalog = matrix.load_catalog(write_fixture(Path(tmp)))
        target = matrix.find_target(catalog, "el10-x86_64")
        eq(target["image"], "almalinux:10", "target image comes from registry")
        eq(target["format"], "rpm", "target format comes from registry")
        eq(target["runner"], "ubuntu-24.04", "target runner comes from registry")
        pairs = dict(matrix.env_pairs(catalog, "vinyl-9.0.1", target_id="el10-x86_64"))
        eq(pairs["TARGET_RUNNER"], "ubuntu-24.04", "target runner is exported")
        eq(pairs["TARGET_PLATFORM"], "linux/amd64", "target platform is exported")
        eq(pairs["TARGET_PACKAGE_ARCH"], "x86_64", "target package architecture is exported")
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(FIXTURE_ENGINES, "    format: rpm\n", "    format: apk\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=engines), "format must be one of", "bad target format")
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(FIXTURE_ENGINES, "      - debian-13-amd64\n      - el10-x86_64\n", "      - no-such-target\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=engines), "unknown target", "unknown engine target")


@test
def catalog_rejects_missing_required():
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(FIXTURE_ENGINES, "    series: vinyl-9.0\n", "")
        expect_catalog_error(write_fixture(Path(tmp), engines=engines), "missing required key 'series'",
                             "missing series")


@test
def catalog_requires_canonical_package_revision():
    """Published engine builds must carry a positive, quoted package revision."""
    packaged = FIXTURE_ENGINES
    with tempfile.TemporaryDirectory() as tmp:
        matrix.load_catalog(write_fixture(Path(tmp), engines=packaged))
    for revision, needle in [(None, "package_revision"), ("0", "package_revision"),
                             ("01", "package_revision"), ("one", "package_revision")]:
        engines = packaged
        if revision is None:
            engines = must_replace(engines, '    package_revision: "1"\n', "")
        else:
            engines = must_replace(engines, '    package_revision: "1"\n',
                                   f'    package_revision: "{revision}"\n')
        with tempfile.TemporaryDirectory() as tmp:
            expect_catalog_error(write_fixture(Path(tmp), engines=engines), needle,
                                 f"package revision {revision!r}")
    inactive = must_replace(
        FIXTURE_ENGINES,
        '    packages: "false"\n    targets:\n',
        '    packages: "false"\n    package_revision: "1"\n    targets:\n',
    )
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(write_fixture(Path(tmp), engines=inactive),
                             'valid only when packages is "true"', "inactive package revision")


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
def catalog_rejects_packages_on_trunk_and_checks_family_identity():
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(
            FIXTURE_ENGINES,
            "      branch: main\n    packages: \"false\"\n",
            "      branch: main\n    packages: \"true\"\n    package_revision: \"1\"\n",
        )
        expect_catalog_error(write_fixture(Path(tmp), engines=engines),
                             'packages "true" requires kind release', "packages on trunk")
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(FIXTURE_ENGINES, "id: varnish-9.0.3", "id: cache-9.0.3")
        expect_catalog_error(write_fixture(Path(tmp), engines=engines),
                             "id must start with its family prefix varnish-", "family/id identity")
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(
            FIXTURE_ENGINES,
            "      sha256: \"bb22\"\n    packages: \"false\"\n",
            "      sha256: \"bb22\"\n    packages: \"true\"\n    package_revision: \"1\"\n",
        )
        catalog = matrix.load_catalog(write_fixture(Path(tmp), engines=engines))
        eq(matrix.find_engine(catalog, "varnish-9.0.3")["packages"], "true",
           "release Varnish engines may opt into packages after proof")


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
def catalog_cargo_contract_requires_pinned_ordered_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        catalog = matrix.load_catalog(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": FIXTURE_CARGO}))
        vmod = catalog["vmods"]["reqwest"]
        eq(matrix.vmod_build(vmod), "cargo", "Cargo build is carried through")
        eq(matrix.vmod_artifacts(vmod), ["libvmod_reqwest.so"], "Cargo artifact is carried through")
        eq(matrix.vmod_cargo_features(vmod), ["vmod"], "Cargo features are carried through")
        eq(catalog["toolchains"]["rust"], {"version": "1.90.0", "bootstrap": "rustup"},
           "Cargo toolchain pin is carried through")
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(write_fixture(Path(tmp), vmods={"reqwest": FIXTURE_CARGO}),
                             "requires engines.yml toolchains.rust", "Cargo requires the global toolchain")
    with tempfile.TemporaryDirectory() as tmp:
        engines = must_replace(cargo_fixture_engines(), 'version: "1.90.0"', 'version: "stable"')
        expect_catalog_error(write_fixture(Path(tmp), engines=engines, vmods={"reqwest": FIXTURE_CARGO}),
                             "exact major.minor.patch Rust version", "Rust toolchain is exactly pinned")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_CARGO, "  artifacts:\n    - libvmod_reqwest.so\n", "")
        expect_catalog_error(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": vmod}),
                             "requires package.artifacts", "Cargo requires artifacts")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_CARGO, "    - libvmod_reqwest.so\n", "    - ../libvmod_reqwest.so\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": vmod}),
                             "must be a basename ending in .so", "Cargo artifact is basename only")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_CARGO, "    - vmod\n", "    - vmod\n    - vmod\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": vmod}),
                             "package.cargo_features contains duplicates", "Cargo features are unique")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_CARGO, "    - reqwest\n", "    - reqwest\n    - extra\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": vmod}),
                             "must have equal lengths", "Cargo module/artifact ordering has equal lengths")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_CARGO, "    - reqwest\n", "    - reqwest\n    - reqwest\n")
        vmod = must_replace(vmod, "    - libvmod_reqwest.so\n", "    - libvmod_reqwest.so\n    - libvmod_extra.so\n")
        expect_catalog_error(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": vmod}),
                             "package.modules contains duplicates", "Cargo modules are an unambiguous mapping")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_CARGO, "tests: cargo-test", "tests: make-check")
        expect_catalog_error(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": vmod}),
                             "make-check requires build autotools", "Cargo cannot use make-check")
    with tempfile.TemporaryDirectory() as tmp:
        vmod = must_replace(FIXTURE_MULTI, "tests: make-check", "tests: cargo-test")
        expect_catalog_error(write_fixture(Path(tmp), vmods={"multi": vmod}),
                             "cargo-test requires build cargo", "Autotools cannot use cargo-test")


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


@test
def catalog_promoted_sources_require_immutable_commits():
    without_default = must_replace(
        FIXTURE_DICT, "    commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", ""
    )
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(
            write_fixture(Path(tmp), vmods={"dict": without_default}),
            "sources.default.commit must be a full lowercase 40-character Git commit",
            "promoted default source without commit",
        )
    bad_commit = must_replace(
        FIXTURE_DICT,
        "    commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
        "    commit: ABC123\n",
    )
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(write_fixture(Path(tmp), vmods={"dict": bad_commit}),
                             "commit must match", "malformed promoted source commit")
    unpromoted = must_replace(without_default, '  promoted: "true"\n', "")
    with tempfile.TemporaryDirectory() as tmp:
        matrix.load_catalog(write_fixture(Path(tmp), vmods={"dict": unpromoted}))


# ---------------------------------------------------------------------------
# Editor JSON Schemas
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
    rust = engines["properties"]["toolchains"]["properties"]["rust"]
    eq(rust["properties"]["version"]["pattern"], matrix.RUST_VERSION_RE.pattern, "Rust pin pattern tracks matrix")
    vmod = docs["vmod.schema.json"]
    eq(vmod["properties"]["build"]["enum"], list(matrix.BUILD_FAMILIES), "build enum tracks matrix.BUILD_FAMILIES")
    eq(vmod["properties"]["tests"]["enum"], list(matrix.TESTS_VALUES), "tests enum tracks matrix.TESTS_VALUES")
    eq(vmod["properties"]["engine_source"]["enum"], list(matrix.ENGINE_SOURCE_VALUES),
       "engine_source enum tracks matrix.ENGINE_SOURCE_VALUES")
    eq(vmod["properties"]["package"]["properties"]["modules"]["items"]["pattern"],
       matrix.MODULE_NAME_RE.pattern, "module name pattern tracks matrix.MODULE_NAME_RE")
    eq(vmod["properties"]["package"]["properties"]["artifacts"]["items"]["pattern"],
       matrix.ARTIFACT_BASENAME_RE.pattern, "artifact name pattern tracks matrix.ARTIFACT_BASENAME_RE")


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
        catalog = matrix.load_catalog(write_fixture(Path(tmp), engines=varnish_package_fixture()))
        vmod = catalog["vmods"]["dict"]
        release = matrix.find_engine(catalog, "vinyl-9.0.1")
        varnish = matrix.find_engine(catalog, "varnish-9.0.3")
        trunk = matrix.find_engine(catalog, "vinyl-trunk")
        eq(matrix.resolve_source(vmod, release),
           {"source": "default", "ref": "v1.7", "version": "1.7",
            "commit": "a" * 40}, "default")
        eq(matrix.resolve_source(vmod, varnish),
           {"source": "by_series", "ref": "v1.8", "version": "1.8",
            "commit": "b" * 40}, "by_series")
        eq(matrix.resolve_source(vmod, trunk),
           {"source": "head", "ref": "master", "version": "", "commit": ""}, "head")
        eq(matrix.engine_version(release), "9.0.1", "engine version")
        eq(matrix.engine_version(trunk), "trunk", "trunk engine version")
        eq(matrix.engine_package_version(release), {"deb": "9.0.1-1", "rpm": "9.0.1-1%{?dist}"},
           "engine package version")
        eq(matrix.vmod_package_version("1.7", release),
           {"deb": "1.7-1~vinyl9.0.1.1", "rpm_version": "1.7", "rpm_release": "1.vinyl9.0.1.1"},
           "vmod package version")
        eq(matrix.engine_runtime_package(release), "vinyl-cache", "Vinyl runtime package")
        eq(matrix.engine_development_package(release, "deb"), "vinyl-cache-dev", "Vinyl Debian development package")
        eq(matrix.engine_development_package(release, "rpm"), "vinyl-cache-devel", "Vinyl RPM development package")
        eq(matrix.engine_api(release), "vinylapi", "Vinyl API")
        eq(matrix.engine_daemon(release), "vinyld", "Vinyl daemon")
        eq(matrix.engine_vmod_dir_component(release), "vinyl-cache", "Vinyl VMOD directory component")
        eq(matrix.engine_source_name(release), "vinyl-cache", "Vinyl source identity")
        eq(matrix.engine_rpm_archive_stem(release), "vinyl-cache", "Vinyl RPM archive stem")
        eq(matrix.engine_recipe_directory(release), "packaging/engine/vinyl", "Vinyl recipe directory")
        eq(matrix.engine_runtime_package(varnish), "varnish", "Varnish runtime package")
        eq(matrix.engine_development_package(varnish, "deb"), "varnish-dev", "Varnish Debian development package")
        eq(matrix.engine_development_package(varnish, "rpm"), "varnish-devel", "Varnish RPM development package")
        eq(matrix.engine_api(varnish), "varnishapi", "Varnish API")
        eq(matrix.engine_daemon(varnish), "varnishd", "Varnish daemon")
        eq(matrix.engine_vmod_dir_component(varnish), "varnish", "Varnish VMOD directory component")
        eq(matrix.engine_source_name(varnish), "varnish", "Varnish source identity")
        eq(matrix.engine_rpm_archive_stem(varnish), "varnish", "Varnish RPM archive stem")
        eq(matrix.engine_recipe_directory(varnish), "packaging/engine/varnish", "Varnish recipe directory")
        eq(matrix.engine_vmod_package_name(varnish, "dict"), "varnish-vmod-dict", "Varnish VMOD package name")
        eq(matrix.engine_vmod_package_name(varnish, "k8s_endpoint"), "varnish-vmod-k8s-endpoint",
           "package names normalize VMOD identifier underscores")
        eq(matrix.vmod_package_version("1.8", varnish),
           {"deb": "1.8-1~varnish9.0.3.1", "rpm_version": "1.8", "rpm_release": "1.varnish9.0.3.1"},
           "Varnish VMOD package version")
        eq(matrix.vmod_package_version("0~vinyl-main", varnish),
           {"deb": "0~vinyl-main-1~varnish9.0.3.1", "rpm_version": "0~vinyl.main",
            "rpm_release": "1.varnish9.0.3.1"},
           "RPM version normalizes Debian's hyphen separator")


@test
def package_revision_drives_versions_and_exact_dependencies():
    revision_12 = must_replace(FIXTURE_ENGINES, '    package_revision: "1"\n',
                               '    package_revision: "12"\n')
    revision_13 = must_replace(revision_12, '    package_revision: "12"\n',
                               '    package_revision: "13"\n')
    next_engine = must_replace(
        FIXTURE_ENGINES,
        '  - id: vinyl-9.0.1\n    family: vinyl\n    series: vinyl-9.0\n',
        '  - id: vinyl-9.0.2\n    family: vinyl\n    series: vinyl-9.0\n',
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "revision-12", engines=revision_12)
        catalog = matrix.load_catalog(root)
        engine = matrix.find_engine(catalog, "vinyl-9.0.1")
        eq(matrix.engine_package_version(engine),
           {"deb": "9.0.1-12", "rpm": "9.0.1-12%{?dist}"}, "engine revision version")
        version_12 = matrix.vmod_package_version("1.7", engine)
        eq(version_12,
           {"deb": "1.7-1~vinyl9.0.1.12", "rpm_version": "1.7", "rpm_release": "1.vinyl9.0.1.12"},
           "VMOD revision version")
        deb_out = tmp / "deb-out"
        recipe.generate(root, "dict", "vinyl-9.0.1", "debian-13-amd64", deb_out,
                        maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        control = (deb_out / "debian" / "control").read_text()
        changelog = (deb_out / "debian" / "changelog").read_text()
        ok("vinyl-cache (= 9.0.1-12)" in control, "Debian runtime dependency has the revision")
        ok("vinyl-cache-dev (= 9.0.1-12)" in control, "Debian build dependency has the revision")
        ok("vinyl-vmod-dict (1.7-1~vinyl9.0.1.12)" in changelog, "Debian VMOD version has the revision")
        rpm_out = tmp / "rpm-out"
        written = recipe.generate(root, "dict", "vinyl-9.0.1", "el10-x86_64", rpm_out,
                                  maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        spec = written[0].read_text()
        ok("Release:        1.vinyl9.0.1.12%{?dist}" in spec, "RPM adds dist exactly once")
        ok("Requires:       vinyl-cache%{?_isa} = 9.0.1-12%{?dist}" in spec,
           "RPM runtime dependency has the revision")
        ok("BuildRequires:  vinyl-cache-devel = 9.0.1-12%{?dist}" in spec,
           "RPM build dependency has the revision")

        same_engine = matrix.find_engine(matrix.load_catalog(write_fixture(tmp / "revision-13", engines=revision_13)),
                                         "vinyl-9.0.1")
        newer_engine = matrix.find_engine(matrix.load_catalog(write_fixture(tmp / "next-engine", engines=next_engine)),
                                          "vinyl-9.0.2")
        eq(matrix.vmod_package_version("1.7", same_engine),
           {"deb": "1.7-1~vinyl9.0.1.13", "rpm_version": "1.7", "rpm_release": "1.vinyl9.0.1.13"},
           "same-engine revision rendering")
        eq(matrix.vmod_package_version("1.7", newer_engine),
           {"deb": "1.7-1~vinyl9.0.2.1", "rpm_version": "1.7", "rpm_release": "1.vinyl9.0.2.1"},
           "new-engine first revision rendering")


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
        eq(without_source_artifacts(expansion["vmods"]), [
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
        eq(without_source_artifacts(compat_only["vmods"]), [
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04"},
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "el10-x86_64", "mode": "compat", "runner": "ubuntu-24.04"},
            {"row": "dict", "engine": "varnish-9.0.3", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04"},
        ], "compat vmod rows use every target")
        ok(all(r["mode"] == "compat" for r in compat_only["vmods"]), "compat filter")


@test
def expansion_uses_one_native_runner_per_target():
    with tempfile.TemporaryDirectory() as tmp:
        catalog = matrix.load_catalog(write_fixture(
            Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": FIXTURE_CARGO}
        ))
        expansion = matrix.expand(catalog, "release", "all")
        eq({row["runner"] for row in expansion["engines"]}, {"ubuntu-24.04"},
           "engine builds use the target runner")
        eq({row["runner"] for row in expansion["vmods"]}, {"ubuntu-24.04"},
           "Cargo VMOD builds use the target runner")
        eq({pair["runner"] for pair in expansion["package_pairs"]}, {"ubuntu-24.04"},
           "package cohorts use the target runner")


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
def release_package_target_filter_limits_every_matrix_output():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp)))
        catalog = matrix.load_catalog(root)
        targets = matrix.release_package_target_filter(catalog, "el10-x86_64")
        expansion = matrix.expand(catalog, "release", "package", targets)
        eq({pair["target"] for pair in expansion["engines"]}, {"el10-x86_64"},
           "target filter limits engine builds")
        eq({row["target"] for row in expansion["vmods"]}, {"el10-x86_64"},
           "target filter limits VMOD builds")
        eq({pair["target"] for pair in expansion["package_pairs"]}, {"el10-x86_64"},
           "target filter limits cohort and publication pairs")
        eq([row for batch in matrix.batch_vmods(expansion["vmods"])
            for row in json.loads(batch["items"])], expansion["vmods"],
           "target filter limits every reusable-workflow batch")
        for selector, expected in (
            ("missing", "without a package-enabled release pair"),
            ("el10-x86_64,el10-x86_64", "must not repeat"),
            ("el10-x86_64, debian-13-amd64", "without whitespace"),
        ):
            try:
                matrix.release_package_target_filter(catalog, selector)
            except matrix.CatalogError as exc:
                ok(expected in str(exc), f"{selector!r} reports its selector error")
            else:
                raise AssertionError(f"{selector!r} must be rejected")
        code, _, err = run_cli([
            "expand", "--lane", "release", "--mode", "package", "--targets", "missing", "--root", root,
        ])
        eq(code, 1, "CLI rejects a target outside the package release matrix")
        ok("without a package-enabled release pair" in err, "CLI preserves the strict target error")
        code, _, err = run_cli([
            "expand", "--lane", "release", "--mode", "compat", "--targets", "el10-x86_64", "--root", root,
        ])
        eq(code, 1, "CLI rejects target filtering outside package release dispatches")
        ok("only supported" in err, "CLI explains the release package restriction")


@test
def expand_trunk_lane_and_github_format():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp)))
        catalog = matrix.load_catalog(root)
        expansion = matrix.expand(catalog, "trunk", "all")
        eq(expansion["engines"], [{"engine": "vinyl-trunk", "target": "debian-13-amd64", "runner": "ubuntu-24.04"}], "trunk engines")
        eq(without_source_artifacts(expansion["vmods"]),
           [{"row": "dict", "engine": "vinyl-trunk", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04"}],
           "trunk vmod rows are compat only")
        code, out, _ = run_cli(["expand", "--lane", "trunk", "--format", "github", "--root", root])
        eq(code, 0, "expand exit code")
        lines = out.strip().split("\n")
        eq(len(lines), 7, "github format includes bounded source and engine matrices")
        ok(lines[0].startswith("engines=") and lines[1].startswith("engine_batches=")
           and lines[2].startswith("vmods=") and lines[3].startswith("vmod_sources=")
           and lines[4].startswith("source_batches=") and lines[5].startswith("vmod_batches=")
           and lines[6].startswith("package_pairs="),
           "github output keys")
        engines = json.loads(lines[0][len("engines="):])
        engine_batches = json.loads(lines[1][len("engine_batches="):])
        vmods = json.loads(lines[2][len("vmods="):])
        sources = json.loads(lines[3][len("vmod_sources="):])
        source_batches = json.loads(lines[4][len("source_batches="):])
        batches = json.loads(lines[5][len("vmod_batches="):])
        package_pairs = json.loads(lines[6][len("package_pairs="):])
        ok(engines and vmods, "neither github array is empty")
        eq([item for batch in engine_batches for item in json.loads(batch["items"])], engines,
           "engine batches preserve every engine pair")
        eq(len(sources), 1, "one trunk source feeds every matching VMOD cell")
        eq([item for batch in source_batches for item in json.loads(batch["items"])], sources,
           "source batches preserve every resolved source")
        ok(all(set(r) >= {"engine", "target", "runner"} for r in engines), "engines= row shape")
        ok(all(r["row"] != r["engine"] for r in vmods), "vmods= excludes engine rows")
        eq([row for batch in batches for row in json.loads(batch["items"])], vmods,
           "vmod_batches preserves every VMOD row")
        eq(package_pairs, [], "trunk has no publishable package cohorts")
        code, _, err = run_cli(["expand", "--lane", "trunk", "--mode", "package", "--root", root])
        eq(code, 1, "trunk+package is an error")
        ok("no package cells" in err, "trunk+package error message")


@test
def expand_deduplicates_resolved_vmod_sources():
    with tempfile.TemporaryDirectory() as tmp:
        catalog = matrix.load_catalog(write_fixture(Path(tmp)))
        expansion = matrix.expand(catalog, "release", "all")
        sources = expansion["sources"]
        eq(len(sources), 2, "default and by-series refs are fetched independently")
        ok(all(set(source) == {"row", "engine", "source_artifact"} for source in sources),
           "source rows contain only fetch-job inputs")
        artifacts = {source["source_artifact"] for source in sources}
        eq(len(artifacts), 2, "distinct resolved sources have distinct artifacts")
        ok(all(artifact.startswith("vmod-source-dict-") for artifact in artifacts),
           "source artifacts retain a readable VMOD identity")
        vinyl_artifacts = {row["source_artifact"] for row in expansion["vmods"]
                           if row["engine"] == "vinyl-9.0.1"}
        varnish_artifacts = {row["source_artifact"] for row in expansion["vmods"]
                             if row["engine"] == "varnish-9.0.3"}
        eq(len(vinyl_artifacts), 1, "compat and package cells share one default source")
        eq(len(varnish_artifacts), 1, "all cells for one by-series ref share its source")
        ok(vinyl_artifacts.isdisjoint(varnish_artifacts),
           "different resolved refs cannot consume each other's artifact")


@test
def vmod_batches_are_homogeneous_bounded_and_ordered():
    def row(index, engine="vinyl-9.0.1", target="debian-13-amd64", mode="compat", runner="ubuntu-24.04"):
        return {
            "row": f"vmod-{index}",
            "engine": engine,
            "target": target,
            "mode": mode,
            "runner": runner,
            "source_artifact": f"vmod-source-{index % 2}",
        }

    first_group = [row(index) for index in range(matrix.VMOD_BATCH_SIZE + 1)]
    second_group = [row(20, target="el10-x86_64"), row(21, target="el10-x86_64")]
    rows = first_group + second_group
    batches = matrix.batch_vmods(rows)
    eq([batch["batch"] for batch in batches], ["batch-001", "batch-002", "batch-003"],
       "batch labels")
    eq([len(json.loads(batch["items"])) for batch in batches], [matrix.VMOD_BATCH_SIZE, 1, 2],
       "batch sizes")
    eq([row for batch in batches for row in json.loads(batch["items"])], rows,
       "batches preserve group and row order")
    for batch in batches:
        items = json.loads(batch["items"])
        eq({(item["engine"], item["target"], item["mode"], item["runner"]) for item in items},
           {(batch["engine"], batch["target"], batch["mode"], batch["runner"])},
           "one batch uses one execution contract")
    eq(batches[0]["source_pattern"], "{vmod-source-0,vmod-source-1}",
       "batch source pattern deduplicates exact artifact names")
    eq(batches[1]["source_pattern"], "vmod-source-0",
       "a one-source batch uses an exact artifact name")


@test
def source_and_engine_batches_are_bounded_and_preserve_inputs():
    sources = [
        {"row": f"vmod-{index}", "engine": "vinyl-9.0.1", "source_artifact": f"source-{index}"}
        for index in range(matrix.SOURCE_BATCH_SIZE + 2)
    ]
    source_batches, source_artifacts = matrix.batch_sources(sources)
    eq([len(json.loads(batch["items"])) for batch in source_batches], [matrix.SOURCE_BATCH_SIZE, 2],
       "source batches use the configured bound")
    eq([item for batch in source_batches for item in json.loads(batch["items"])], sources,
       "source batches preserve source order")
    eq(source_artifacts["source-0"], "vmod-sources-001", "source lookup points to its bundle")
    eq(source_artifacts[f"source-{matrix.SOURCE_BATCH_SIZE}"], "vmod-sources-002",
       "source lookup crosses the bundle boundary")

    engines = [
        {"engine": engine, "target": target, "runner": runner}
        for target, runner in (("debian-13-amd64", "x64"), ("debian-13-arm64", "arm64"))
        for engine in ("vinyl-9.0.1", "varnish-9.0.3")
    ]
    engine_batches = matrix.batch_engines(engines)
    eq(len(engine_batches), 2, "one engine batch is emitted per native target")
    eq([item for batch in engine_batches for item in json.loads(batch["items"])], engines,
       "engine batches preserve every engine pair")
    ok(all(len({item["target"] for item in json.loads(batch["items"])}) == 1 for batch in engine_batches),
       "engine batches never mix targets")


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
        values = parse_env_output(out)
        eq(values["ENGINE_VERSION"], "'9.0.1'", "ENGINE_VERSION")
        eq(values["ENGINE_PACKAGE_REVISION"], "'1'", "ENGINE_PACKAGE_REVISION")
        eq(values["ENGINE_TARBALL_URL"], "'https://example.org/vinyl-cache-9.0.1.tgz'", "tarball url")
        eq(values["ENGINE_RUNTIME_PACKAGE"], "'vinyl-cache'", "runtime package comes from family")
        eq(values["ENGINE_DEVELOPMENT_PACKAGE"], "'vinyl-cache-devel'", "target development package comes from family")
        eq(values["ENGINE_API"], "'vinylapi'", "API comes from family")
        eq(values["ENGINE_DAEMON"], "'vinyld'", "daemon comes from family")
        eq(values["ENGINE_SOURCE_NAME"], "'vinyl-cache'", "Debian source identity comes from family")
        eq(values["ENGINE_RPM_ARCHIVE_STEM"], "'vinyl-cache'", "RPM archive identity comes from family")
        eq(values["ENGINE_RECIPE_DIR"], "'packaging/engine/vinyl'", "recipe directory comes from family")
        eq(values["TARGET_ID"], "'el10-x86_64'", "TARGET_ID")
        eq(values["TARGET_RUNNER"], "'ubuntu-24.04'", "TARGET_RUNNER")
        eq(values["VMOD_REF"], "'v1.7'", "VMOD_REF")
        eq(values["VMOD_EXPECTED_COMMIT"], "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'",
           "promoted release source commit")
        eq(values["VMOD_DEB_VERSION"], "'1.7-1~vinyl9.0.1.1'", "VMOD_DEB_VERSION")
        eq(values["VMOD_PACKAGE_NAME"], "'vinyl-vmod-dict'", "VMOD_PACKAGE_NAME")
        eq(values["VMOD_BUILD_DEPS"], "'redhat-rpm-config python3-docutils'",
           "rpm build deps for an el10 target")
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "deb-target env exit code")
        values = parse_env_output(out)
        eq(values["VMOD_BUILD_DEPS"], "'python3-docutils'", "debian build deps for a debian target")
        code, out, _ = run_cli(["env", "--engine", "varnish-9.0.3", "--vmod", "dict",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "Varnish env exit code")
        values = parse_env_output(out)
        eq(values["ENGINE_RUNTIME_PACKAGE"], "'varnish'", "Varnish runtime package comes from family")
        eq(values["ENGINE_DEVELOPMENT_PACKAGE"], "'varnish-dev'", "Varnish development package comes from family")
        eq(values["ENGINE_API"], "'varnishapi'", "Varnish API comes from family")
        eq(values["ENGINE_DAEMON"], "'varnishd'", "Varnish daemon comes from family")
        eq(values["ENGINE_RECIPE_DIR"], "'packaging/engine/varnish'", "Varnish recipe directory comes from family")
        eq(values["VMOD_PACKAGE_NAME"], "'varnish-vmod-dict'", "Varnish VMOD name comes from family")
        ok("VMOD_DEB_VERSION" not in values, "no package version for an un-packaged engine")
        code, out, _ = run_cli(["env", "--engine", "vinyl-trunk", "--vmod", "dict", "--root", root])
        eq(code, 0, "trunk env exit code")
        values = parse_env_output(out)
        eq(values["ENGINE_BRANCH"], "'main'", "trunk branch")
        eq(values["ENGINE_VERSION"], "'trunk'", "trunk engine version placeholder")
        eq(values["VMOD_REF"], "'master'", "trunk vmod ref is head")
        eq(values["VMOD_EXPECTED_COMMIT"], "''", "trunk source deliberately remains moving")
        eq(values["VMOD_BUILD_DEPS"], "'python3-docutils'",
           "no --target falls back to the engine's first target's format")
        ok("VMOD_DEB_VERSION" not in values, "no package version for a trunk engine")
        code, _, err = run_cli(["env", "--engine", "vinyl-trunk", "--target", "el10-x86_64", "--root", root])
        eq(code, 1, "target not in engine targets is an error")
        ok("not a target of engine" in err, "target error message")

        code, out, _ = run_cli(["select-engine", "--family", "varnish", "--kind", "release",
                                "--root", root])
        eq(code, 0, "select-engine exit code")
        eq(out.strip(), "varnish-9.0.3", "select-engine returns the unique catalog match")


@test
def env_emits_tests_and_modules():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp), vmods={"dict": FIXTURE_DICT, "multi": FIXTURE_MULTI}))
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "multi",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "env exit code with tests+modules")
        values = parse_env_output(out)
        eq(values["VMOD_TESTS"], "'make-check'", "VMOD_TESTS from the manifest")
        eq(values["VMOD_ENGINE_SOURCE"], "'required'", "VMOD_ENGINE_SOURCE from the manifest")
        eq(values["VMOD_SOURCE_API_FAMILY"], "'varnish'", "VMOD_SOURCE_API_FAMILY from the manifest")
        eq(values["VMOD_MODULES"], "'alpha beta_2'", "VMOD_MODULES space-separated")
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "env exit code without tests/modules")
        values = parse_env_output(out)
        eq(values["VMOD_TESTS"], "''", "no tests declared -> empty VMOD_TESTS")
        eq(values["VMOD_ENGINE_SOURCE"], "''", "no engine_source declared -> empty VMOD_ENGINE_SOURCE")
        eq(values["VMOD_SOURCE_API_FAMILY"], "''", "no source_api_family -> empty VMOD_SOURCE_API_FAMILY")
        eq(values["VMOD_MODULES"], "'dict'", "VMOD_MODULES defaults to the id")


@test
def source_api_normalization_is_directional_and_preserves_vtc_syntax():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "configure.ac").write_bytes(
            b"PKG_CHECK_MODULES([VARNISHAPI], [varnishapi])\nAC_SUBST([VARNISHSRC])\n"
        )
        (root / "test.vtc").write_bytes(b"varnishtest test\nserver s1 -start\nvarnish v1 -vcl+backend {}\n")
        (root / "private.c").write_bytes(b'#include "cache/cache_varnishd.h"\nvarnishadm\n')
        (root / "stats.vsc").write_bytes(
            b".. varnish_vsc_begin:: example\n.. varnish_vsc_end:: example\n"
        )
        (root / "binary").write_bytes(b"varnishapi\0unchanged")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_bytes(b"varnishapi")

        changed, totals = source_api_normalize.normalize_tree(root, "varnish", "vinyl")

        eq(
            [str(path) for path, _ in changed],
            ["configure.ac", "private.c", "stats.vsc", "test.vtc"],
            "changed files",
        )
        eq((root / "configure.ac").read_text(), "PKG_CHECK_MODULES([VINYLAPI], [vinylapi])\nAC_SUBST([VINYLSRC])\n", "build spellings")
        eq((root / "private.c").read_text(), '#include "cache/cache_vinyld.h"\nvinyladm\n', "header and CLI")
        eq(
            (root / "stats.vsc").read_text(),
            ".. vinyl_vsc_begin:: example\n.. vinyl_vsc_end:: example\n",
            "VSC directives",
        )
        eq((root / "test.vtc").read_text(), "varnishtest test\nserver s1 -start\nvinyl v1 -vcl+backend {}\n", "VTC syntax")
        eq((root / "binary").read_bytes(), b"varnishapi\0unchanged", "binary skipped")
        eq((root / ".git" / "config").read_bytes(), b"varnishapi", ".git skipped")
        ok(totals["varnishapi -> vinylapi"] == 1, "replacement totals")

        reverse, _ = source_api_normalize.normalize_tree(root, "vinyl", "varnish")
        ok(reverse, "reverse normalization changes files")
        eq((root / "test.vtc").read_text().splitlines()[0], "varnishtest test", "VTC header remains stable")


@test
def source_api_normalization_keeps_vsctool_directives_in_the_shared_spelling():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cachetag_vsc = root / "cachetag.vsc"
        cachetag_vsc.write_bytes(
            b".. vinyl_vsc_begin:: cachetag\n.. vinyl_vsc_end:: cachetag\n"
        )

        changed, _ = source_api_normalize.normalize_tree(root, "vinyl", "varnish")

        eq(changed, [], "Varnish uses the shared vsctool directive spelling")
        eq(
            cachetag_vsc.read_bytes(),
            b".. vinyl_vsc_begin:: cachetag\n.. vinyl_vsc_end:: cachetag\n",
            "Vinyl source keeps directives Varnish vsctool recognizes",
        )

        tinykvm_vsc = root / "tinykvm.vsc"
        tinykvm_vsc.write_bytes(
            b".. varnish_vsc_begin:: vmod_kvm\n.. varnish_vsc_end:: vmod_kvm\n"
        )

        changed, _ = source_api_normalize.normalize_tree(root, "varnish", "vinyl")

        eq([str(path) for path, _ in changed], ["tinykvm.vsc"], "legacy Varnish spelling is normalized")
        eq(
            tinykvm_vsc.read_bytes(),
            b".. vinyl_vsc_begin:: vmod_kvm\n.. vinyl_vsc_end:: vmod_kvm\n",
            "both engines receive directives their shared vsctool recognizes",
        )


@test
def cohort_env_is_generated_from_the_promoted_catalog():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp)))
        code, out, _ = run_cli(["cohort-env", "--engine", "vinyl-9.0.1",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "cohort-env exit code")
        values = parse_env_output(out)
        eq(values["COHORT_MODULES"], "'dict'", "cohort imports every declared module")


@test
def env_emits_cargo_execution_contract():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": FIXTURE_CARGO}))
        code, out, _ = run_cli(["env", "--engine", "varnish-9.0.3", "--vmod", "reqwest",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "Cargo env exit code")
        values = parse_env_output(out)
        eq(values["VMOD_BUILD"], "'cargo'", "Cargo build kind")
        eq(values["VMOD_ARTIFACTS"], "'libvmod_reqwest.so'", "Cargo declared artifacts")
        eq(values["RUST_VERSION"], "'1.90.0'", "global Rust version")
        eq(values["RUST_BOOTSTRAP"], "'rustup'", "global Rust bootstrap")


# ---------------------------------------------------------------------------
# shell failure details
# ---------------------------------------------------------------------------


@test
def cargo_status_map_preserves_existing_failure_meanings():
    for step in ("cargo-preflight", "cargo-build", "cargo-artifacts"):
        eq(shell_status_for_step(step), "build_failed", f"{step} is an honest build failure")
    for step in ("cargo-fetch", "cargo-bootstrap", "cargo-deps"):
        eq(shell_status_for_step(step), "infra_failed", f"{step} is a bootstrap/transport failure")
    eq(shell_status_for_step("cargo-test"), "test_failed", "Cargo test failure is a test failure")
    eq(shell_status_for_step("load"), "load_failed", "load failure meaning is preserved")
    eq(shell_status_for_step("pkg-build"), "package_failed", "package failure meaning is preserved")
    eq(shell_status_for_step("pkg-verify"), "package_failed",
       "native metadata and payload mismatch is a package failure")
    eq(shell_status_for_step("pkg-install"), "install_failed", "install failure meaning is preserved")
    eq(shell_status_for_step("cargo-preflight", "package"), "package_failed",
       "Cargo package preflight is a package failure")
    eq(shell_status_for_step("source-api-normalize"), "build_failed",
       "source normalization is an honest source build failure")


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

        cargo_log = tmp / "cargo.log"
        cargo_log.write_text(
            "error: failed to run custom build command for `varnish-sys v0.1.0`\n"
            "Caused by:\n"
            "  Varnish API version mismatch\n"
            "note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace\n"
            "warning: build failed, waiting for other jobs to finish...\n"
        )
        eq(shell_failure_detail(cargo_log, "cargo-build"),
           "error: failed to run custom build command for `varnish-sys v0.1.0`",
           "Cargo detail retains the actual build error rather than its backtrace hint")


@test
def shared_retry_runner_bounds_attempts_and_preserves_status():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        attempts = tmp / "attempts"
        result = subprocess.run(
            ["bash", "-c",
             'source "$1"; sleep() { :; }; ATTEMPTS_FILE=$2; flaky() { '
             'attempt=$(cat "$ATTEMPTS_FILE" 2>/dev/null || echo 0); attempt=$((attempt + 1)); '
             'printf "%s\\n" "$attempt" > "$ATTEMPTS_FILE"; [ "$attempt" -ge 3 ]; }; '
             'retry_command 3 "fixture operation" flaky',
             "bash", str(root / "scripts" / "lib.sh"), str(attempts)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 0, "shared retry runner succeeds on a later attempt")
        eq(attempts.read_text().strip(), "3", "shared retry runner makes the configured attempts")
        ok("fixture operation failed; retrying" in result.stderr, "shared retry runner names retrying work")
        attempts.unlink()
        result = subprocess.run(
            ["bash", "-c",
             'source "$1"; sleep() { :; }; ATTEMPTS_FILE=$2; always_42() { '
             'attempt=$(cat "$ATTEMPTS_FILE" 2>/dev/null || echo 0); attempt=$((attempt + 1)); '
             'printf "%s\\n" "$attempt" > "$ATTEMPTS_FILE"; return 42; }; '
             'retry_command 3 "fixture exhaustion" always_42',
             "bash", str(root / "scripts" / "lib.sh"), str(attempts)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 42, "shared retry runner preserves the exhausted command status")
        eq(attempts.read_text().strip(), "3", "shared retry runner stops at its attempt bound")


@test
def missing_remote_branch_is_not_retried_as_a_transport_failure():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        attempts = tmp / "attempts"
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "count=$(cat \"$GIT_ATTEMPTS\" 2>/dev/null || echo 0)\n"
            "printf '%s\\n' \"$((count + 1))\" > \"$GIT_ATTEMPTS\"\n"
            "exit 2\n"
        )
        fake_git.chmod(0o755)
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; git_remote_head_exists_retry origin missing',
             "bash", str(root / "scripts" / "lib.sh")],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
                 "GIT_ATTEMPTS": str(attempts)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 2, "missing remote branch keeps git's not-found status")
        eq(attempts.read_text().strip(), "1", "missing remote branch performs one lookup")


@test
def immutable_downloads_retry_transient_failures():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        attempts = tmp / "attempts"
        fake_curl = fake_bin / "curl"
        fake_curl.write_text(
            "#!/usr/bin/env bash\n"
            "attempt=$(cat \"$DOWNLOAD_ATTEMPTS\" 2>/dev/null || echo 0)\n"
            "attempt=$((attempt + 1))\n"
            "printf '%s\\n' \"$attempt\" > \"$DOWNLOAD_ATTEMPTS\"\n"
            "destination=\n"
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = -o ]; then destination=$2; shift 2; else shift; fi\n"
            "done\n"
            "if [ \"$attempt\" -lt \"${DOWNLOAD_SUCCESS_AT:-3}\" ]; then exit 22; fi\n"
            "printf 'verified payload\\n' > \"$destination\"\n"
        )
        fake_curl.chmod(0o755)
        destination = tmp / "archive.tgz"
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; sleep() { :; }; download_retry "$2" "$3"',
             "bash", str(root / "scripts" / "lib.sh"), "https://example.test/archive.tgz", str(destination)],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
                 "DOWNLOAD_ATTEMPTS": str(attempts)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 0, "download succeeds after transient failures")
        eq(attempts.read_text().strip(), "3", "download retries twice before succeeding")
        eq(destination.read_text(), "verified payload\n", "only a completed download reaches its destination")
        ok(not Path(f"{destination}.part").exists(), "temporary partial download is removed")
        attempts.unlink()
        exhausted = tmp / "exhausted.tgz"
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; sleep() { :; }; download_retry "$2" "$3"',
             "bash", str(root / "scripts" / "lib.sh"), "https://example.test/unavailable.tgz", str(exhausted)],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
                 "DOWNLOAD_ATTEMPTS": str(attempts), "DOWNLOAD_SUCCESS_AT": "99"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 22, "download preserves curl's status after exhausting retries")
        eq(attempts.read_text().strip(), "5", "download attempts are bounded at five")
        ok(not exhausted.exists() and not Path(f"{exhausted}.part").exists(),
           "exhausted download publishes neither a destination nor a partial file")


@test
def engine_artifact_carries_and_restores_generated_private_headers():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source = tmp / "source"
        prefix = tmp / "prefix"
        provisioned = tmp / "provisioned"
        source_cache = source / "bin" / "vinyld" / "cache"
        source_cache.mkdir(parents=True)
        (source_cache / "cache_vinyld.h").write_text("generated trunk header\n")
        (source_cache / "cache_main.c").write_text("engine source\n")
        result = subprocess.run(
            ["bash", "-c",
             'source "$1"; preserve_engine_private_headers "$2" "$3" vinyld; '
             'seed_engine_private_headers "$3" "$4" vinyld vinyl-cache vinylapi',
             "bash", str(root / "scripts" / "lib.sh"), str(source), str(prefix), str(provisioned)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 0, "private-header artifact round trip succeeds")
        restored = provisioned / "bin" / "vinyld" / "cache" / "cache_vinyld.h"
        eq(restored.read_text(), "generated trunk header\n",
           "the provisioned source tree receives the engine build's generated header")
        ok(not (prefix / "share" / "vcache-packaging" / "engine-source" / "vinyld" /
                "cache" / "cache_main.c").exists(),
           "the engine artifact carries headers rather than the complete build tree")


@test
def vmod_clone_retries_transient_failures():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        attempts = tmp / "attempts"
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "attempt=$(cat \"$CLONE_ATTEMPTS\" 2>/dev/null || echo 0)\n"
            "attempt=$((attempt + 1))\n"
            "printf '%s\\n' \"$attempt\" > \"$CLONE_ATTEMPTS\"\n"
            "if [ \"$attempt\" -lt 3 ]; then exit 1; fi\n"
            "mkdir -p \"$3\"\n"
        )
        fake_git.chmod(0o755)
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; sleep() { :; }; clone_vmod "$2" "$3"',
             "bash", str(root / "scripts" / "lib.sh"), "https://example.test/vmod.git", str(tmp / "src")],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "CLONE_ATTEMPTS": str(attempts)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 0, "VMOD clone succeeds after transient failures")
        eq(attempts.read_text().strip(), "3", "VMOD clone retries twice before succeeding")
        ok((tmp / "src").is_dir(), "successful retry leaves the cloned destination")


@test
def prefetched_vmod_source_round_trips_without_upstream():
    root = Path(__file__).resolve().parent.parent
    library = root / "scripts" / "lib.sh"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        upstream = tmp / "upstream"
        upstream.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
        subprocess.run(["git", "-C", str(upstream), "config", "user.name", "Source Test"], check=True)
        subprocess.run(["git", "-C", str(upstream), "config", "user.email", "source@example.test"], check=True)
        (upstream / "payload.txt").write_text("source payload\n")
        subprocess.run(["git", "-C", str(upstream), "add", "payload.txt"], check=True)
        subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "fixture"], check=True)
        commit = subprocess.run(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"],
            check=True, stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        fetched = tmp / "fetched"
        artifact = tmp / "artifact"
        commit_file = tmp / "fetched.commit"
        result = subprocess.run(
            ["bash", "-c",
             'source "$1"; materialize_vmod_source "$2" main "$3" "$4" "$5"; '
             'archive_vmod_source "$4" "$6" fixture "$2" main "$3"',
             "bash", str(library), str(upstream), commit, str(fetched), str(commit_file), str(artifact)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 0, "source acquisition and archiving succeeds")
        upstream.rename(tmp / "upstream-offline")
        restored = tmp / "restored"
        restored_commit = tmp / "restored.commit"
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        git_args = tmp / "git-args"
        real_git = shutil.which("git")
        ok(real_git is not None, "Git is available for the source artifact test")
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$GIT_ARGS\"\n"
            "exec \"$REAL_GIT\" \"$@\"\n"
        )
        fake_git.chmod(0o755)
        result = subprocess.run(
            ["bash", "-c",
             'source "$1"; restore_vmod_source "$2" "$3" fixture "$4" main "$5" "$6"',
             "bash", str(library), str(artifact), str(restored), str(upstream), commit, str(restored_commit)],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
                 "GIT_ARGS": str(git_args), "REAL_GIT": real_git},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 0, "source artifact restores after its upstream becomes unavailable")
        eq((restored / "payload.txt").read_text(), "source payload\n", "restored source payload")
        eq(restored_commit.read_text().strip(), commit, "restored commit remains pinned")
        git_calls = git_args.read_text()
        eq(git_calls.count("safe.directory"), 0,
           "an owned tree needs no per-command ownership exception")
        st = (restored / ".git").stat()
        eq((st.st_uid, st.st_gid), (os.getuid(), os.getgid()),
           "restore chowns the extracted tree to the build user")


@test
def source_digest_is_stable_across_checkout_metadata_and_tar_noise():
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        roots = [tmp / "first", tmp / "second"]
        for index, root in enumerate(roots):
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "payload.txt").write_text("payload\n")
            executable = root / "run.sh"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            os.symlink("nested/payload.txt", root / "link")
            (root / ".git").mkdir()
            (root / ".git" / f"transport-{index}").write_text("different checkout metadata\n")
            os.utime(root / "nested" / "payload.txt", (index + 1, index + 1))
        first = source_digest.digest_tree(roots[0])
        eq(source_digest.digest_tree(roots[1]), first,
           "source identity ignores Git transport metadata and timestamps")
        (roots[1] / "run.sh").chmod(0o644)
        ok(source_digest.digest_tree(roots[1]) != first,
           "source identity includes executable bits")
        (roots[1] / "run.sh").chmod(0o755)
        (roots[1] / "link").unlink()
        os.symlink("run.sh", roots[1] / "link")
        ok(source_digest.digest_tree(roots[1]) != first,
           "source identity includes symlink targets")


@test
def vmod_cache_reuses_only_matching_conclusive_cells():
    root = Path(__file__).resolve().parent.parent
    item = {
        "row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64",
        "mode": "compat", "runner": "ubuntu-24.04", "source_artifact": "fixture-source",
    }
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        engine = tmp / "engine"
        engine.mkdir()
        (engine / "engine-vinyl-9.0.1-debian-13-amd64-prefix.tar.gz").write_bytes(b"engine")
        source = tmp / "sources" / item["source_artifact"]
        source.mkdir(parents=True)
        (source / "source.tar.gz").write_bytes(b"checkout archive")
        (source / "source-sha256").write_text("a" * 64 + "\n")
        (source / "vmod-id").write_text("dict\n")
        (source / "url").write_text("https://example.invalid/dict.git\n")
        (source / "ref").write_text("main\n")
        (source / "commit").write_text("b" * 40 + "\n")
        manifest = vmod_cache.cell_manifest(item, engine, tmp / "sources", root)
        work = tmp / "work"
        result = work / "results" / vmod_cache.result_filename(item)
        result.parent.mkdir(parents=True)
        result.write_text(json.dumps({
            "schema": "cell/1", "row": item["row"], "engine": item["engine"],
            "target": item["target"], "mode": item["mode"], "status": "configure_failed",
        }) + "\n")
        cache = tmp / "cache"
        ok(vmod_cache.save_cell(cache, work, item, manifest),
           "a conclusive incompatibility is cacheable")
        result.unlink()
        ok(vmod_cache.restore_cell(cache, work, item, manifest),
           "a matching conclusive cell is restored")
        result.unlink()
        (source / "commit").write_text("c" * 40 + "\n")
        changed = vmod_cache.cell_manifest(item, engine, tmp / "sources", root)
        ok(not vmod_cache.restore_cell(cache, work, item, changed),
           "a changed source commit invalidates the cell")
        other = {**item, "row": "cachetag"}
        first_key, _ = vmod_cache.batch_key([item], engine, tmp / "sources", root)
        other_key, _ = vmod_cache.batch_key([other], engine, tmp / "sources", root)
        ok(first_key["restore_prefix"] != other_key["restore_prefix"],
           "fallback snapshots cannot cross batch membership")
        package_item = {**item, "mode": "package"}
        engine_packages = engine / "engine-vinyl-9.0.1-debian-13-amd64-pkgs"
        engine_packages.mkdir()
        (engine_packages / "vinyl-cache.deb").write_bytes(b"engine package")
        package_manifest = vmod_cache.cell_manifest(package_item, engine, tmp / "sources", root)
        package_result = work / "results" / vmod_cache.result_filename(package_item)
        package_result.write_text(json.dumps({
            "schema": "cell/1", "row": package_item["row"], "engine": package_item["engine"],
            "target": package_item["target"], "mode": package_item["mode"], "status": "pass",
        }) + "\n")
        ok(not vmod_cache.save_cell(cache, work, package_item, package_manifest),
           "a passing package cell without a package payload is not cacheable")
        package_payload = work / "packages" / vmod_cache.package_directory_name(package_item)
        package_payload.mkdir(parents=True)
        (package_payload / "vinyl-vmod-dict.deb").write_bytes(b"VMOD package")
        ok(vmod_cache.save_cell(cache, work, package_item, package_manifest),
           "a passing package cell with its payload is cacheable")
        trunk_item = {**item, "engine": "vinyl-trunk"}
        (engine / "engine-vinyl-trunk-debian-13-amd64-prefix.tar.gz").write_bytes(b"trunk engine")
        engine_commit = engine / "engine-source-commit"
        engine_commit.write_text("d" * 40 + "\n")
        trunk_manifest = vmod_cache.cell_manifest(trunk_item, engine, tmp / "sources", root)
        engine_commit.write_text("e" * 40 + "\n")
        moved_trunk_manifest = vmod_cache.cell_manifest(trunk_item, engine, tmp / "sources", root)
        ok(vmod_cache.fingerprint(trunk_manifest) != vmod_cache.fingerprint(moved_trunk_manifest),
           "a moved trunk engine commit invalidates VMOD conclusions")


@test
def engine_cache_requires_complete_passing_outputs():
    item = {"engine": "vinyl-9.0.1", "target": "debian-13-amd64", "runner": "ubuntu-24.04"}
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = write_fixture(Path(tmp_name))
        pair = tmp / "work" / "artifacts" / "engine-vinyl-9.0.1-debian-13-amd64"
        pair.mkdir(parents=True)
        (pair / "engine-vinyl-9.0.1-debian-13-amd64-prefix.tar.gz").write_bytes(b"prefix")
        packages = pair / "engine-vinyl-9.0.1-debian-13-amd64-pkgs"
        packages.mkdir()
        (packages / "vinyl-cache.deb").write_bytes(b"package")
        result = tmp / "work" / "results" / "vinyl-9.0.1--vinyl-9.0.1--debian-13-amd64--engine.json"
        result.parent.mkdir(parents=True)
        result.write_text(json.dumps({
            "schema": "cell/1", "row": item["engine"], "engine": item["engine"],
            "target": item["target"], "mode": "engine", "status": "pass",
        }) + "\n")
        ok(engine_cache.cacheable(tmp, [item]), "complete passing engine outputs are cacheable")
        result.write_text(json.dumps({
            "schema": "cell/1", "row": "wrong", "engine": item["engine"],
            "target": item["target"], "mode": "engine", "status": "pass",
        }) + "\n")
        ok(not engine_cache.cacheable(tmp, [item]), "engine result identity is validated")


@test
def engine_cache_binds_trunk_entries_to_resolved_commits():
    item = {"engine": "vinyl-trunk", "target": "debian-13-amd64", "runner": "ubuntu-24.04"}
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = write_fixture(Path(tmp_name))
        calls = []

        def resolver(root, url, branch):
            calls.append((url, branch))
            return "a" * 40

        duplicate = {**item, "target": "el10-x86_64"}
        resolved = engine_cache.resolve_items(tmp, [item, duplicate], resolver)
        eq(resolved, [{**item, "source_commit": "a" * 40}, {**duplicate, "source_commit": "a" * 40}],
           "trunk cache inputs retain the resolved commit")
        eq(calls, [("https://example.org/vinyl.git", "main")],
           "one upstream branch is resolved once for all native targets")
        repo_root = Path(__file__).resolve().parent.parent
        real_item = {"engine": "vinyl-trunk", "target": "debian-13-amd64", "runner": "ubuntu-24.04"}
        first = engine_cache.cache_key(repo_root, [{**real_item, "source_commit": "a" * 40}])
        second = engine_cache.cache_key(repo_root, [{**real_item, "source_commit": "b" * 40}])
        ok(first.startswith("vcache-engine-trunk-v1-"), "trunk keys have their own namespace")
        ok(first != second, "a new trunk commit invalidates its engine cache")

        pair = tmp / "work" / "artifacts" / "engine-vinyl-trunk-debian-13-amd64"
        pair.mkdir(parents=True)
        (pair / "engine-vinyl-trunk-debian-13-amd64-prefix.tar.gz").write_bytes(b"prefix")
        (pair / "engine-source-commit").write_text("a" * 40 + "\n")
        result = tmp / "work" / "results" / "vinyl-trunk--vinyl-trunk--debian-13-amd64--engine.json"
        result.parent.mkdir(parents=True)
        result.write_text(json.dumps({
            "schema": "cell/1", "row": "vinyl-trunk", "engine": "vinyl-trunk",
            "target": "debian-13-amd64", "mode": "engine", "status": "pass",
        }) + "\n")
        ok(engine_cache.cacheable(tmp, [resolved[0]]), "matching trunk artifact commit is cacheable")
        (pair / "engine-source-commit").write_text("b" * 40 + "\n")
        ok(not engine_cache.cacheable(tmp, [resolved[0]]), "a stale trunk artifact cannot satisfy the cache key")


@test
def vmod_batch_isolates_cells_reuses_inputs_and_collects_every_result():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = tmp / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        fake_build = scripts / "build-vmod.sh"
        fake_build.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "row=$1; work=$5\n"
            "test -f \"$work/engine/artifacts/engine.tar.gz\"\n"
            "test -f \"$work/vmod-source/source.tar.gz\"\n"
            "if test \"$row\" = slow; then sleep 3; fi\n"
            "mkdir -p \"$work/results\" \"$work/packages/$row\"\n"
            "printf '%s\\n' \"$row\" > \"$work/results/$row.json\"\n"
            "printf '%s\\n' \"$row\" > \"$work/packages/$row/$row.pkg\"\n"
            "test \"$row\" != broken\n"
        )
        fake_build.chmod(0o755)
        engine = tmp / "input" / "engine"
        source_a = tmp / "input" / "sources" / "source-a"
        source_b = tmp / "input" / "sources" / "source-b"
        engine.mkdir(parents=True)
        source_a.mkdir(parents=True)
        source_b.mkdir(parents=True)
        (engine / "engine.tar.gz").write_bytes(b"engine")
        (source_a / "source.tar.gz").write_bytes(b"source a")
        (source_b / "source.tar.gz").write_bytes(b"source b")
        items = [
            {"row": "broken", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04", "source_artifact": "source-a"},
            {"row": "slow", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04", "source_artifact": "source-a"},
            {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "compat", "runner": "ubuntu-24.04", "source_artifact": "source-b"},
        ]
        work = tmp / "work"
        code = vmod_batch.run_batch(items, engine, tmp / "input" / "sources", work, repo, cell_timeout=1)
        eq(code, 1, "an infrastructure failure makes the batch fail after every cell runs")
        eq(sorted(path.name for path in (work / "results").iterdir()),
           ["broken.json", "dict.json", "slow--vinyl-9.0.1--debian-13-amd64--compat.json"],
           "results from failed and later cells are collected")
        timeout_result = json.loads((work / "results" / "slow--vinyl-9.0.1--debian-13-amd64--compat.json").read_text())
        eq(timeout_result["status"], "infra_failed", "a timed-out cell emits explicit infrastructure evidence")
        ok((work / "packages" / "packages-broken-vinyl-9.0.1-debian-13-amd64" / "broken" / "broken.pkg").is_file(),
           "packages from the failed cell are retained")
        ok((work / "packages" / "packages-dict-vinyl-9.0.1-debian-13-amd64" / "dict" / "dict.pkg").is_file(),
           "the batch continues to later cells")
        cells = sorted((work / "cells").iterdir())
        eq(len(cells), 3, "each cell has a distinct work directory")
        ok(cells[0].stat().st_ino != cells[1].stat().st_ino, "cell work directories are isolated")


@test
def source_batch_attempts_every_fetch_and_keeps_successful_members():
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        repo = tmp / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        fetch = scripts / "fetch-vmod-source.sh"
        fetch.write_text(textwrap.dedent("""\
            #!/bin/sh
            if [ "$1" = broken ]; then exit 1; fi
            mkdir -p "$3"
            printf '%s\\n' "$1" > "$3/vmod-id"
            : > "$3/source.tar.gz"
        """))
        fetch.chmod(0o755)
        items = [
            {"row": "broken", "engine": "vinyl", "source_artifact": "source-broken"},
            {"row": "dict", "engine": "vinyl", "source_artifact": "source-dict"},
        ]
        work = tmp / "work"
        code = source_batch.run_batch(items, work, repo, parallelism=2)
        eq(code, 1, "one failed source makes the completed batch fail")
        ok((work / "sources" / "source-dict" / "source.tar.gz").is_file(),
           "a sibling source remains available after another fetch fails")


@test
def engine_batch_attempts_every_engine_and_preserves_engine_directories():
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        repo = tmp / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        build = scripts / "build-engine.sh"
        build.write_text(textwrap.dedent("""\
            #!/bin/sh
            mkdir -p "$3/results"
            printf '{}\\n' > "$3/results/$1.json"
            if [ "$1" = broken ]; then exit 1; fi
            mkdir -p "$3/artifacts"
            : > "$3/artifacts/engine-$1-$2-prefix.tar.gz"
        """))
        build.chmod(0o755)
        items = [
            {"engine": "broken", "target": "debian-13-amd64", "runner": "x64"},
            {"engine": "vinyl", "target": "debian-13-amd64", "runner": "x64"},
        ]
        work = tmp / "work"
        code = engine_batch.run_batch(items, work, repo)
        eq(code, 1, "one infrastructure failure makes the completed engine batch fail")
        eq(sorted(path.name for path in (work / "results").iterdir()), ["broken.json", "vinyl.json"],
           "the batch retains results for every engine")
        ok((work / "artifacts" / "engine-vinyl-debian-13-amd64" /
            "engine-vinyl-debian-13-amd64-prefix.tar.gz").is_file(),
           "the successful engine remains addressable by its exact pair")


@test
def engine_batch_passes_resolved_commit_only_to_its_matching_engine():
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        repo = tmp / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        build = scripts / "build-engine.sh"
        build.write_text("#!/usr/bin/env bash\n"
                         "set -eu\n"
                         "mkdir -p \"$3/results\" \"$3/artifacts\"\n"
                         "printf '%s\\n' \"${ENGINE_SOURCE_COMMIT-unset}\" > \"$3/artifacts/commit\"\n"
                         ": > \"$3/artifacts/engine-$1-$2-prefix.tar.gz\"\n")
        build.chmod(0o755)
        items = [
            {"engine": "release", "target": "debian-13-amd64", "runner": "x64"},
            {"engine": "trunk", "target": "debian-13-amd64", "runner": "x64", "source_commit": "c" * 40},
        ]
        work = tmp / "work"
        eq(engine_batch.run_batch(items, work, repo), 0, "engine batch completes")
        release = work / "artifacts" / "engine-release-debian-13-amd64" / "commit"
        trunk = work / "artifacts" / "engine-trunk-debian-13-amd64" / "commit"
        eq(release.read_text().strip(), "unset", "release builds do not inherit a trunk commit")
        eq(trunk.read_text().strip(), "c" * 40, "trunk build receives the resolved commit")


@test
def github_release_retry_restarts_the_replace_transaction():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        creates = tmp / "creates"
        deletes = tmp / "deletes"
        fake_gh = fake_bin / "gh"
        fake_gh.write_text(
            "#!/usr/bin/env bash\n"
            "case \"$1 $2\" in\n"
            "'release delete')\n"
            "  count=$(cat \"$DELETE_ATTEMPTS\" 2>/dev/null || echo 0)\n"
            "  printf '%s\\n' \"$((count + 1))\" > \"$DELETE_ATTEMPTS\"\n"
            "  exit 0 ;;\n"
            "'release create')\n"
            "  count=$(cat \"$CREATE_ATTEMPTS\" 2>/dev/null || echo 0)\n"
            "  count=$((count + 1)); printf '%s\\n' \"$count\" > \"$CREATE_ATTEMPTS\"\n"
            "  [ \"$count\" -ge 3 ] ;;\n"
            "*) exit 64 ;;\n"
            "esac\n"
        )
        fake_gh.chmod(0o755)
        fake_sleep = fake_bin / "sleep"
        fake_sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake_sleep.chmod(0o755)
        notes = tmp / "notes.md"
        asset = tmp / "asset.deb"
        notes.write_text("release notes\n")
        asset.write_text("package\n")
        result = subprocess.run(
            [str(root / "scripts" / "retry.sh"), "release", "engine-target", "deadbeef",
             str(notes), str(asset)],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
                 "CREATE_ATTEMPTS": str(creates), "DELETE_ATTEMPTS": str(deletes)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 0, "release replacement succeeds on a later transaction attempt")
        eq(creates.read_text().strip(), "3", "release creation uses the shared attempt bound")
        eq(deletes.read_text().strip(), "3", "each retry removes a possibly partial draft first")


@test
def rpm_collection_selects_the_main_package_by_metadata():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fake_bin = tmp / "bin"
        packages = tmp / "packages"
        fake_bin.mkdir()
        packages.mkdir()
        rpm = fake_bin / "rpm"
        rpm.write_text(textwrap.dedent("""\
            #!/bin/sh
            case "$4" in
              *-debuginfo-*) printf '%s\n' vinyl-vmod-dict-debuginfo ;;
              *-debugsource-*) printf '%s\n' vinyl-vmod-dict-debugsource ;;
              *) printf '%s\n' vinyl-vmod-dict ;;
            esac
            """))
        rpm.chmod(0o755)
        main = packages / "vinyl-vmod-dict-1.7-1.el10.x86_64.rpm"
        debuginfo = packages / "vinyl-vmod-dict-debuginfo-1.7-1.el10.x86_64.rpm"
        debugsource = packages / "vinyl-vmod-dict-debugsource-1.7-1.el10.x86_64.rpm"
        for package in (main, debuginfo, debugsource):
            package.touch()
        result = subprocess.run(
            ["bash", "-c",
             'source "$1"; select_native_package rpm vinyl-vmod-dict "$2"/*.rpm',
             "bash", str(root / "scripts" / "lib.sh"), str(packages)],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        eq(result.returncode, 0, "RPM selection accepts one main package beside debug packages")
        eq(result.stdout.strip(), str(main), "RPM selection returns only the declared package name")


@test
def release_payload_gate_rejects_missing_artifact():
    engine = {"id": "vinyl-9.0.1", "family": "vinyl"}
    target = {"format": "deb", "package_arch": "amd64"}
    cells = [
        {"row": "vinyl-9.0.1", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "engine"},
        {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "package"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        pkgdl = Path(tmp) / "pkgdl"
        engine_dir = pkgdl / "engine-vinyl-9.0.1-debian-13-amd64"
        vmod_dir = pkgdl / "packages-batch-001-vinyl-9.0.1-debian-13-amd64" / "packages-dict-vinyl-9.0.1-debian-13-amd64"
        engine_dir.mkdir(parents=True)
        vmod_dir.mkdir(parents=True)
        runtime = engine_dir / "runtime.deb"
        development = engine_dir / "development.deb"
        vmod = vmod_dir / "dict.deb"
        for path in (runtime, development, vmod):
            path.write_bytes(b"native package placeholder")
        metadata = {
            runtime: ("vinyl-cache", "amd64"),
            development: ("vinyl-cache-dev", "amd64"),
            vmod: ("vinyl-vmod-dict", "amd64"),
        }
        reader = lambda path: metadata[path]
        staged = Path(tmp) / "dist"
        release_gate.validate_pair_payload(pkgdl, engine, target, cells,
                                            metadata_reader=reader, stage_dir=staged)
        eq(sorted(path.name for path in staged.iterdir()),
           ["development.deb", "dict.deb", "runtime.deb"],
           "release payload stages every expected package")
        vmod.unlink()
        try:
            release_gate.validate_pair_payload(pkgdl, engine, target, cells,
                                                metadata_reader=reader)
        except release_gate.PayloadError as exc:
            ok("no native deb artifacts" in str(exc) or "missing package artifact" in str(exc),
               "missing package is reported by release gate")
        else:
            raise Fail("release payload gate accepted a missing VMOD artifact")


@test
def release_payload_gate_stages_github_asset_names():
    engine = {"id": "vinyl-9.0.1", "family": "vinyl"}
    target = {"format": "deb", "package_arch": "amd64"}
    cells = [
        {"row": "dict", "engine": "vinyl-9.0.1", "target": "debian-13-amd64", "mode": "package"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        pkgdl = Path(tmp) / "pkgdl"
        source = pkgdl / "packages-dict-vinyl-9.0.1-debian-13-amd64"
        source.mkdir(parents=True)
        artifact = source / "vinyl-vmod-dict_1.7-1~vinyl9.0.1.1_amd64.deb"
        artifact.write_bytes(b"native package placeholder")
        staged = Path(tmp) / "dist"
        release_gate.validate_pair_payload(
            pkgdl, engine, target, cells,
            metadata_reader=lambda path: ("vinyl-vmod-dict", "amd64"), stage_dir=staged,
        )
        eq(sorted(path.name for path in staged.iterdir()),
           ["vinyl-vmod-dict_1.7-1.vinyl9.0.1.1_amd64.deb"],
           "release checksums use GitHub's retained asset name")


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
def merge_preserves_conclusive_cells_across_infrastructure_failures():
    key = "dict/vinyl-9.0.1/debian-13-amd64/compat"
    conclusive = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "pass",
                           "2026-08-09T00:00:00Z")
    interrupted = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "infra_failed",
                            "2026-08-10T00:00:00Z", detail="upstream returned HTTP 503")
    recovered = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "build_failed",
                          "2026-08-11T00:00:00Z")
    state = {"schema": matrix.STATE_SCHEMA, "cells": {key: conclusive}}
    eq(matrix.merge_cells(state, [interrupted]), 1, "new infrastructure evidence is applied")
    eq(state["cells"][key]["status"], "pass", "infrastructure failure preserves last conclusive status")
    eq(state["infra_failures"][key]["detail"], "upstream returned HTTP 503",
       "latest infrastructure failure is retained separately")
    grid = matrix.build_grid(state, "debian-13-amd64")
    cell_view = grid["cells"][("dict", "vinyl-9.0.1")]
    eq(cell_view["bucket"], "PASS", "newer infrastructure evidence does not repaint the cell")
    ok("A newer attempt could not be tested" in cell_view["title"],
       "newer infrastructure evidence remains visible in the tooltip")
    eq(matrix.merge_cells(state, [recovered]), 1, "new conclusive result is applied")
    eq(state["cells"][key]["status"], "build_failed", "new conclusive result replaces the old one")
    ok(key not in state["infra_failures"], "recovery clears older infrastructure evidence")


@test
def merge_keeps_first_infrastructure_failure_visible_when_no_conclusion_exists():
    interrupted = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "infra_failed",
                            "2026-08-10T00:00:00Z")
    state = {"schema": matrix.STATE_SCHEMA, "cells": {}}
    matrix.merge_cells(state, [interrupted])
    key = "dict/vinyl-9.0.1/debian-13-amd64/compat"
    eq(state["cells"][key]["status"], "infra_failed",
       "a cell with no conclusive history remains visibly infra failed")
    older_conclusion = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "pass",
                                 "2026-08-09T00:00:00Z")
    matrix.merge_cells(state, [older_conclusion])
    eq(state["cells"][key]["status"], "pass",
       "a later merge reconstructs the last conclusion even when it is older")
    eq(state["infra_failures"][key]["status"], "infra_failed",
       "the newer interruption remains recorded after reconstruction")


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
        (results / "bad.json").write_text(json.dumps(
            make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "pass",
                      "2026-08-09T00:00:00Z", source_api_normalization="varnish-to-squid")))
        code, _, err = run_cli(["merge", "--results-dir", str(results), "--state-file", str(tmp / "s.json")])
        eq(code, 1, "unknown source normalization fails merge")
        ok("source_api_normalization must be one of" in err, "source normalization error message")


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


@test
def render_smoke():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        engines = must_replace(
            FIXTURE_ENGINES,
            "  - id: vinyl-trunk\n",
            "  - id: varnish-trunk\n"
            "    family: varnish\n"
            "    series: varnish-trunk\n"
            "    kind: trunk\n"
            "    source:\n"
            "      git_url: https://example.org/varnish.git\n"
            "      branch: main\n"
            "    packages: \"false\"\n"
            "    pkgconfig_version: \"9.99.0\"\n"
            "    targets:\n"
            "      - debian-13-amd64\n"
            "  - id: vinyl-trunk\n",
        )
        root = write_fixture(tmp / "repo", engines=engines)
        results = tmp / "results"
        results.mkdir()
        cells = [
            make_cell("vinyl-9.0.1", "vinyl-9.0.1", "debian-13-amd64", "engine", "pass",
                      "2026-08-09T00:00:00Z"),
            make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "pass", "2026-08-09T00:00:00Z",
                      source_api_normalization="varnish-to-vinyl"),
            make_cell("dict", "vinyl-9.0.1", "el10-x86_64", "package", "build_failed",
                      "2026-08-09T01:00:00Z", detail="source normalization found no API spellings",
                      source_api_normalization="varnish-to-vinyl", failure_step="source-api-normalize"),
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
        for needle in ("vinyl-9.0.1", "varnish-9.0.3", "vinyl-trunk", "dict",
                       'class="cell PASS"', 'class="cell PASS NORMALIZED"',
                       'class="cell FAIL NORMALIZED"', 'class="cell INFRA"',
                       'class="cell MISSING"', "source normalization found no API spellings",
                       "https://example.org/runs/1", 'class="target-matrices"',
                       '<time datetime="2026-08-10T00:00:00Z">'):
            ok(needle in html_text, f"rendered page is missing {needle!r}")
        eq(html_text.count('class="target-matrix"'), 2, "one rendered matrix per target")
        eq(html_text.count('class="matrix-key"'), 1, "the matrix key renders once")
        eq(html_text.count('class="matrix-note"'), 1, "the package-scope note renders once")
        ok('<h2 class="target">debian-13-amd64' in html_text, "Debian matrix heading")
        ok('<h2 class="target">el10-x86_64' in html_text, "EL10 matrix heading")
        ok('href="https://example.org/dict"' in html_text, "VMOD row links to its configured homepage")
        state = json.loads(state_file.read_text())
        catalog = matrix.load_catalog(root)
        eq(matrix.matrix_targets(state, catalog), ["debian-13-amd64", "el10-x86_64"], "target order from catalog")
        debian_grid = matrix.build_grid(state, "debian-13-amd64", catalog)
        el10_grid = matrix.build_grid(state, "el10-x86_64", catalog)
        eq(debian_grid["cells"][("dict", "vinyl-9.0.1")]["bucket"], "PASS", "Debian status stays separate")
        eq(debian_grid["cells"][("dict", "vinyl-9.0.1")]["modifier"], "NORMALIZED",
           "normalized pass retains its distinct treatment")
        eq(debian_grid["cells"][("dict", "vinyl-9.0.1")]["text"], "pass",
           "normalized pass keeps the outcome label compact")
        eq(el10_grid["cells"][("dict", "vinyl-9.0.1")]["bucket"], "FAIL", "EL10 failure stays separate")
        eq(el10_grid["cells"][("dict", "vinyl-9.0.1")]["text"], "translate",
           "normalizer failure is distinct from a later build failure")
        ok("Source translated from Varnish API to Vinyl API." in
           debian_grid["cells"][("dict", "vinyl-9.0.1")]["title"],
           "normalized tooltip records the direction")
        eq(debian_grid["cells"][("(engine)", "vinyl-9.0.1")]["bucket"], "PASS", "engine cell on the (engine) row")
        eq(debian_grid["rows"][0], "(engine)", "engine row renders first")
        eq(debian_grid["columns"],
           ["varnish-9.0.3", "varnish-trunk", "vinyl-9.0.1", "vinyl-trunk"],
           "Debian columns group numbered and trunk engines by family")
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
        ok("FAIL: tests/x01.vtc" in cell_view["title"], "failing test names survive into the tooltip")


@test
def translated_build_failure_keeps_its_outcome_label():
    cell = make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "build_failed",
                     "2026-08-09T00:00:00Z", source_api_normalization="varnish-to-vinyl",
                     failure_step="make")
    state = {"schema": matrix.STATE_SCHEMA, "cells": {matrix.cell_key(cell): cell}, "infra_failures": {}}
    cell_view = matrix.build_grid(state, "debian-13-amd64")["cells"][("dict", "vinyl-9.0.1")]
    eq(cell_view["bucket"], "FAIL", "a later failure stays a failure")
    eq(cell_view["modifier"], "NORMALIZED", "the source intervention remains visible")
    eq(cell_view["text"], "build", "the amber edge carries the modifier without lengthening the outcome label")


@test
def mixed_modes_fold_and_retain_result_context():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        cells = [
            make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "compat", "pass", "2026-08-09T00:00:00Z"),
            make_cell("dict", "vinyl-9.0.1", "debian-13-amd64", "package", "package_failed",
                      "2026-08-09T01:00:00Z"),
            make_cell("redis", "vinyl-9.0.1", "debian-13-amd64", "compat", "configure_failed",
                      "2026-08-09T00:00:00Z"),
        ]
        state = {"schema": matrix.STATE_SCHEMA, "cells": {}, "infra_failures": {}}
        eq(matrix.merge_cells(state, cells), 3, "each result is merged")
        grid = matrix.build_grid(state, "debian-13-amd64", matrix.load_catalog(root))
        eq(grid["cells"][("dict", "vinyl-9.0.1")]["bucket"], "FAIL",
           "mixed cell keeps the worst-across-modes colour fold")
        redis_title = grid["cells"][("redis", "vinyl-9.0.1")]["title"]
        ok("compat:" in redis_title and "(configure_failed)" in redis_title,
           "failing compat result retains its mode and status")
        dict_title = grid["cells"][("dict", "vinyl-9.0.1")]["title"]
        ok("compat:" in dict_title and "(pass)" in dict_title,
           "passing compat result retains its mode and status")
        ok("package:" in dict_title and "(package_failed)" in dict_title,
           "failing package result retains its mode and status")
        ok("[v1.7 @ abcdef123456]" in dict_title, "ref and commit stay in the tooltip")
        ok("(2026-08-09T01:00:00Z)" in dict_title, "timestamps stay in the tooltip")


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
        changelog = (out / "debian" / "changelog").read_text()
        ok("vinyl-vmod-dict (1.7-1~vinyl9.0.1.1) unstable" in changelog, "debian version")
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
        ok("restore_upstream_debian_inputs:" in rules_text,
           "Debian recipe isolates upstream configure inputs from generated metadata")
        ok("override_dh_auto_configure:" in rules_text and "restore_vcache_debian_metadata" in rules_text,
           "Debian recipe restores generated metadata after upstream configure")
        ok("dh_auto_configure -- " in rules_text,
           "Debian recipe renders the catalog-scoped configure argument seam")
        ok("$(MAKE) -f debian/rules restore_vcache_debian_metadata" in rules_text,
           "Debian configure restores metadata through the generated rules rather than upstream's Makefile")
        ok("override_dh_auto_clean:\n\t:\n" in rules_text,
           "Debian recipe never cleans the disposable checkout (decision 25)")
        ok("dh_auto_clean" not in rules_text.replace("override_dh_auto_clean", ""),
           "Debian recipe does not invoke dh_auto_clean")
        ok("export DEB_CFLAGS_MAINT_APPEND = -Wno-error" in rules_text
           and "export DEB_CXXFLAGS_MAINT_APPEND = -Wno-error" in rules_text,
           "Debian recipe appends -Wno-error (decision 30)")
        ok("$(MAKE) install DESTDIR=$(CURDIR)/debian/vinyl-vmod-dict" in rules_text,
           "Debian recipe installs through an explicit make with the default install target")
        ok("\tdh_auto_install\n" not in rules_text, "Debian recipe does not invoke dh_auto_install")
        eq((out / "debian" / "source" / "format").read_text(), "3.0 (quilt)\n", "source format")
        backup = out / "debian" / ".vcache-packaging"
        eq((backup / "control").read_text(), control,
           "Debian recipe retains a private control backup across upstream distclean")
        eq((backup / "changelog").read_text(), changelog,
           "Debian recipe retains a private changelog backup across upstream distclean")
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
        ok("Release:        1.vinyl9.0.1.1%{?dist}" in spec, "rpm release")
        ok("Requires:       vinyl-cache%{?_isa} = 9.0.1-1%{?dist}" in spec,
           "exact-version arch-qualified engine requires")
        ok("BuildRequires:  vinyl-cache-devel = 9.0.1-1%{?dist}" in spec, "exact-version -devel requires")
        ok("BuildRequires:  python3-docutils" in spec, "manifest build_deps included")
        ok('export CFLAGS="%{build_cflags} -Wno-error"' in spec
           and 'export CXXFLAGS="%{build_cxxflags} -Wno-error"' in spec,
           "RPM recipe appends -Wno-error (decision 30)")
        ok('%{__make} %{?_smp_mflags} install DESTDIR=%{buildroot} INSTALL="%{__install} -p"' in spec,
           "RPM recipe installs through an explicit make with the default install target")
        ok("\n%make_install\n" not in spec, "RPM recipe does not invoke %make_install")
        ok("%global debug_package %{nil}" in spec,
           "RPM recipe disables debug subpackages that can be empty for Cargo VMODs")


@test
def recipe_varnish_family_generation():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        deb_root = write_fixture(tmp / "deb-repo", engines=varnish_package_fixture())
        deb_out = tmp / "deb-out"
        recipe.generate(deb_root, "dict", "varnish-9.0.3", "debian-13-amd64", deb_out,
                        maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        control = (deb_out / "debian" / "control").read_text()
        changelog = (deb_out / "debian" / "changelog").read_text()
        ok("Package: varnish-vmod-dict" in control, "Varnish Debian package name")
        ok("varnish (= 9.0.3-1)" in control, "Varnish exact runtime dependency")
        ok("varnish-dev (= 9.0.3-1)" in control, "Varnish exact development build dependency")
        ok("varnish-vmod-dict (1.8-1~varnish9.0.3.1) unstable" in changelog, "Varnish Debian version")

        rpm_root = write_fixture(tmp / "rpm-repo", engines=varnish_package_fixture(include_rpm=True))
        rpm_out = tmp / "rpm-out"
        written = recipe.generate(rpm_root, "dict", "varnish-9.0.3", "el10-x86_64", rpm_out,
                                  maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        eq([path.name for path in written], ["varnish-vmod-dict.spec"], "Varnish RPM spec filename")
        spec = written[0].read_text()
        eq(recipe.TOKEN_RE.findall(spec), [], "unresolved tokens in Varnish RPM spec")
        ok("%global vmoddir %(pkg-config --variable=vmoddir varnishapi)" in spec,
           "Varnish RPM VMOD directory follows the selected engine API")
        ok("Requires:       varnish%{?_isa} = 9.0.3-1%{?dist}" in spec,
           "Varnish exact RPM runtime dependency")
        ok("BuildRequires:  varnish-devel = 9.0.3-1%{?dist}" in spec,
           "Varnish exact RPM development build dependency")


@test
def engine_packages_declare_build_and_systemd_contracts():
    root = Path(__file__).resolve().parents[1]
    families = {
        "varnish": {
            "package": "varnish",
            "daemon": "varnishd",
            "reload": "varnishreload",
            "config": "/etc/varnish/default.vcl",
        },
        "vinyl": {
            "package": "vinyl-cache",
            "daemon": "vinyld",
            "reload": "vinylreload",
            "config": "/etc/vinyl-cache/default.vcl",
        },
    }
    for family, contract in families.items():
        recipe_dir = root / "packaging" / "engine" / family
        debian = recipe_dir / "debian"
        control = (debian / "control").read_text()
        rules = (debian / "rules").read_text()
        install_manifest = (debian / f"{contract['package']}.install").read_text()
        service = (debian / f"{contract['package']}.service").read_text()
        reload_helper = root / "packaging" / "engine" / "reload-vcl"
        postinst = (debian / f"{contract['package']}.postinst").read_text()
        spec = (recipe_dir / f"{contract['package']}.spec").read_text()

        ok("libssl-dev" in control, f"{family} Debian build declares OpenSSL headers")
        ok("adduser" in control and "openssl" in control,
           f"{family} Debian runtime declares account and OpenSSL tools")
        ok(contract["daemon"] in service, f"{family} unit starts its daemon")
        ok(contract["config"] in service, f"{family} unit loads the packaged default VCL")
        ok("-p feature=+http2" in service, f"{family} unit enables HTTP/2")
        ok(contract["reload"] in service, f"{family} unit supports safe VCL reloads")
        ok(reload_helper.is_file(), f"{family} reload helper is checked in")
        ok("#DEBHELPER#" in postinst, f"{family} postinst retains debhelper hooks")
        ok(contract["config"].removeprefix("/") in install_manifest,
           f"{family} Debian payload includes its default VCL")
        ok("usr/sbin/*" in install_manifest,
           f"{family} Debian payload includes its reload helper")
        ok("etc/example.vcl" in rules, f"{family} Debian rules install the upstream example VCL")

        ok("openssl-devel" in spec, f"{family} RPM build declares OpenSSL headers")
        ok("BuildRequires:  systemd-rpm-macros" in spec, f"{family} RPM build declares systemd macros")
        ok("Requires:       openssl" in spec, f"{family} RPM runtime declares OpenSSL tools")
        ok("export VCC_CC=" in spec, f"{family} RPM does not compile runtime VCL with build-only flags")
        ok(f"Source1:        {contract['package']}.service" in spec,
           f"{family} RPM consumes the shared unit")
        ok(f"Source2:        {contract['package']}.reload" in spec,
           f"{family} RPM consumes the shared reload helper")
        ok("%systemd_post" in spec and "%systemd_preun" in spec and "%systemd_postun_with_restart" in spec,
           f"{family} RPM has complete systemd lifecycle hooks")


@test
def recipe_cargo_debian_and_rpm_mapping():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo", engines=cargo_fixture_engines(), vmods={"reqwest": FIXTURE_CARGO})
        deb_out = tmp / "deb-out"
        recipe.generate(root, "reqwest", "vinyl-9.0.1", "debian-13-amd64", deb_out,
                        maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        control = (deb_out / "debian" / "control").read_text()
        rules = (deb_out / "debian" / "rules").read_text()
        ok("clang" in control and "libclang-dev" in control, "Cargo Debian native dependencies")
        ok("cargo build --release --locked --offline" in rules, "Cargo Debian build")
        ok("cargo build --release --locked --offline --features vmod" in rules,
           "Cargo Debian build enables declared features")
        ok("--mapping reqwest=libvmod_reqwest.so" in rules, "Cargo Debian artifact mapping")
        ok("/repo/tools/cargo-artifacts.py" in rules, "Cargo Debian shared artifact helper")
        ok("pkg-config --variable=vmoddir vinylapi" in rules,
           "Cargo Debian install follows the selected engine's pkg-config VMOD directory")

        rpm_out = tmp / "rpm-out"
        written = recipe.generate(root, "reqwest", "vinyl-9.0.1", "el10-x86_64", rpm_out,
                                  maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        spec = written[0].read_text()
        ok("BuildRequires:  clang" in spec and "BuildRequires:  clang-devel" in spec,
           "Cargo RPM native dependencies")
        ok("cargo build --release --locked --offline" in spec, "Cargo RPM build")
        ok("cargo build --release --locked --offline --features vmod" in spec,
           "Cargo RPM build enables declared features")
        ok("--mapping reqwest=libvmod_reqwest.so" in spec, "Cargo RPM artifact mapping")
        ok("%global vmoddir %(pkg-config --variable=vmoddir vinylapi)" in spec,
           "Cargo RPM install follows the selected engine's pkg-config VMOD directory")


@test
def recipe_debian_generation_normalizes_underscored_vmod_package_names():
    root = matrix.default_root()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        recipe.generate(root, "k8s_endpoint", "varnish-9.0.3", "debian-13-amd64", out,
                        maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        control = (out / "debian" / "control").read_text()
        changelog = (out / "debian" / "changelog").read_text()
        ok("Package: varnish-vmod-k8s-endpoint" in control,
           "Debian package names convert VMOD identifier underscores to hyphens")
        ok("varnish-vmod-k8s-endpoint (0.1.0-" in changelog,
           "Debian changelog starts with a valid normalized source package name and version")


@test
def recipe_debian_generation_scopes_configure_arguments_to_the_declaring_vmod():
    root = matrix.default_root()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        recipe.generate(root, "querystring", "varnish-9.0.3", "debian-13-amd64", out,
                        maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        ok("dh_auto_configure -- --enable-docs" in (out / "debian" / "rules").read_text(),
           "querystring alone carries its declared documentation configure argument")
        rpm_out = Path(tmp) / "rpm-out"
        recipe.generate(root, "querystring", "varnish-9.0.3", "el10-aarch64", rpm_out,
                        maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        rpm = (rpm_out / "varnish-vmod-querystring.spec").read_text()
        ok("%configure --enable-docs" in rpm,
           "querystring configure argument reaches the RPM recipe too")


@test
def package_handoff_preserves_autotools_inputs_until_bootstrap():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-vmod.sh").read_text()
    bootstrap = script.index('step bootstrap\n  cd "$SRC"', script.index('# --------------------------------------------------------------- package ----'))
    replace = script.index('rm -rf "$SRC/debian"', bootstrap)
    ok(bootstrap < replace,
       "Debian package handoff bootstraps before replacing upstream debian/ inputs")
    ok('NAMEDIR="$VMOD_PACKAGE_NAME-${VMOD_RPM_VERSION:?}"' in script,
       "RPM source archive directory follows the rendered RPM-safe version")
    ok('UPSTREAM_DEBIAN="/work/tmp/$TAG-upstream-debian"' in script,
       "Debian package handoff keeps upstream configure inputs separate from the generated recipe")


@test
def native_package_contract_normalizes_payload_and_exact_dependencies():
    paths = [
        "./usr/lib/vinyl-cache/vmods/libvmod_beta_2.so",
        "/usr/share/doc/example/changelog.gz",
        "usr/lib/vinyl-cache/vmods/libvmod_alpha.so",
        "/usr/lib/.build-id/aa/bb",
    ]
    eq(package_contract.normalized_vmod_payload(paths), [
        "/usr/lib/vinyl-cache/vmods/libvmod_alpha.so",
        "/usr/lib/vinyl-cache/vmods/libvmod_beta_2.so",
    ], "native payload normalization ignores package-manager housekeeping")
    eq(package_contract.expected_vmod_payload("/usr/lib/vinyl-cache/vmods", ["beta_2", "alpha"]), [
        "/usr/lib/vinyl-cache/vmods/libvmod_alpha.so",
        "/usr/lib/vinyl-cache/vmods/libvmod_beta_2.so",
    ], "expected module manifest is deterministic")
    ok(package_contract.deb_exact_dependency(
        "libc6 (>= 2.38), vinyl-cache (= 9.0.1-1), zlib1g", "vinyl-cache", "9.0.1-1"
    ), "Debian exact dependency is recognized")
    ok(not package_contract.deb_exact_dependency(
        "vinyl-cache (>= 9.0.1-1)", "vinyl-cache", "9.0.1-1"
    ), "a lower-bound dependency cannot masquerade as the exact engine cohort")


@test
def cargo_artifact_helper_accepts_rers_mapping():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        release = tmp / "release"
        destination = tmp / "dest"
        release.mkdir()
        (release / "libvmod_rers.so").write_bytes(b"shared object")
        command = [
            sys.executable, str(Path(__file__).resolve().parent / "cargo-artifacts.py"),
            "--release-dir", str(release), "--destination", str(destination),
            "--mapping", "rers=libvmod_rers.so",
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        eq(result.returncode, 0, "Cargo artifact helper accepts rers mapping")
        ok((destination / "libvmod_rers.so").is_file(), "rers mapping installs conventional basename")
        duplicate = subprocess.run(command + ["--mapping", "rers=other.so"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ok(duplicate.returncode != 0 and "duplicate Cargo module mapping" in duplicate.stderr,
           "Cargo artifact helper rejects duplicate module mappings")
        invalid = subprocess.run(command[:-2] + ["--mapping", "../rers=libvmod_rers.so"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ok(invalid.returncode != 0 and "invalid artifact mapping" in invalid.stderr,
           "Cargo artifact helper validates module names independently")
        (release / "unexpected.so").write_bytes(b"extra")
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ok(result.returncode != 0 and "unexpected" in result.stderr,
           "Cargo artifact helper rejects undeclared shared objects")


@test
def vmod_artifact_helper_keeps_only_declared_modules():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        stage = tmp / "stage"
        vmod_dir = "/usr/lib/vinyl-cache/vmods"
        destination = stage / vmod_dir.lstrip("/")
        destination.mkdir(parents=True)
        (destination / "libvmod_slash.so").write_bytes(b"public shared object")
        (destination / "libvmod_slashwitness.so").write_bytes(b"test shared object")
        (stage / "usr/bin").mkdir(parents=True)
        (stage / "usr/bin/slashmap").write_bytes(b"utility")
        command = [
            sys.executable, str(Path(__file__).resolve().parent / "vmod-artifacts.py"),
            "--stage-root", str(stage), "--vmod-dir", vmod_dir, "--modules", "slash",
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        eq(result.returncode, 0, "VMOD artifact helper accepts the declared public module")
        eq([path.relative_to(stage).as_posix() for path in stage.rglob("*") if path.is_file()],
           ["usr/lib/vinyl-cache/vmods/libvmod_slash.so"],
           "VMOD artifact helper removes test modules and upstream utilities")
        (destination / "libvmod_slash.so").unlink()
        missing = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ok(missing.returncode != 0 and "missing or empty" in missing.stderr,
           "VMOD artifact helper fails before accepting an incomplete staged payload")


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


@test
def catalog_requires_a_numeric_pkgconfig_version_on_trunk_engines_only():
    missing = must_replace(FIXTURE_ENGINES, '    pkgconfig_version: "9.99.0"\n', "")
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(write_fixture(Path(tmp), engines=missing),
                             "kind trunk requires pkgconfig_version", "trunk without pkgconfig_version")
    for bad in ("trunk", "9.99", "9.99.0-rc1"):
        engines = must_replace(FIXTURE_ENGINES, '    pkgconfig_version: "9.99.0"\n',
                               f'    pkgconfig_version: "{bad}"\n')
        with tempfile.TemporaryDirectory() as tmp:
            expect_catalog_error(write_fixture(Path(tmp), engines=engines),
                                 "pkgconfig_version must match", f"pkgconfig_version {bad!r}")
    on_release = must_replace(FIXTURE_ENGINES, '      sha256: "bb22"\n    packages: "false"\n',
                              '      sha256: "bb22"\n    packages: "false"\n    pkgconfig_version: "9.99.0"\n')
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(write_fixture(Path(tmp), engines=on_release),
                             "valid only for kind trunk", "pkgconfig_version on a release engine")
    with tempfile.TemporaryDirectory() as tmp:
        root = write_fixture(Path(tmp))
        code, out, _ = run_cli(["env", "--engine", "vinyl-trunk", "--root", str(root)])
        eq(code, 0, "env exit code")
        ok("ENGINE_PKGCONFIG_VERSION='9.99.0'" in out, "trunk env exposes the pkg-config version")
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--root", str(root)])
        ok("ENGINE_PKGCONFIG_VERSION" not in out, "release env carries no pkg-config stand-in")


@test
def install_target_is_an_autotools_package_key_with_an_install_default():
    scoped = must_replace(FIXTURE_DICT, '  promoted: "true"\n',
                          '  promoted: "true"\n  build_target: "-C src libvmod_dict.la"\n'
                          '  install_target: "-C src install-vmodLTLIBRARIES"\n')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo", vmods={"dict": scoped})
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict", "--root", str(root)])
        eq(code, 0, "env exit code")
        ok("VMOD_INSTALL_TARGET='-C src install-vmodLTLIBRARIES'" in out, "env exposes install_target")
        recipe.generate(root, "dict", "vinyl-9.0.1", "debian-13-amd64", tmp / "deb",
                        maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)
        rules_text = (tmp / "deb" / "debian" / "rules").read_text()
        ok("dh_auto_build -- -C src libvmod_dict.la" in rules_text, "rules build target")
        ok("$(MAKE) -C src install-vmodLTLIBRARIES DESTDIR=$(CURDIR)/debian/vinyl-vmod-dict" in rules_text,
           "rules install target")
        spec = recipe.generate(root, "dict", "vinyl-9.0.1", "el10-x86_64", tmp / "rpm",
                               maintainer=("Test Maintainer", "test@example.org"), now=FIXED_NOW)[0].read_text()
        ok("%make_build -C src libvmod_dict.la" in spec, "spec build target")
        ok("%{__make} %{?_smp_mflags} -C src install-vmodLTLIBRARIES DESTDIR=%{buildroot}" in spec,
           "spec install target")
    with tempfile.TemporaryDirectory() as tmp:
        root = write_fixture(Path(tmp))
        code, out, _ = run_cli(["env", "--engine", "vinyl-9.0.1", "--vmod", "dict", "--root", str(root)])
        ok("VMOD_INSTALL_TARGET='install'" in out, "install_target defaults to install")
    cargo = must_replace(FIXTURE_CARGO, "  cargo_features:\n",
                         '  install_target: "-C src install"\n  cargo_features:\n')
    with tempfile.TemporaryDirectory() as tmp:
        expect_catalog_error(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": cargo}),
                             "package.install_target is only supported for build autotools",
                             "install_target on a Cargo VMOD")


@test
def source_api_normalization_follows_the_engines_private_header_spelling():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "configure.ac").write_bytes(b"AC_CHECK_HEADERS([cache/cache_varnishd.h])\n")
        (root / "vmod.c").write_bytes(
            b"#ifdef HAVE_CACHE_CACHE_VARNISHD_H\n#include <cache/cache_varnishd.h>\n#endif\n"
            b'#include "cache_varnishd.h"\nVARNISHD\n'
        )
        changed, _ = source_api_normalize.normalize_tree(root, "varnish", "vinyl",
                                                         vinyl_private_header="cache_int.h")
        eq([str(path) for path, _ in changed], ["configure.ac", "vmod.c"], "changed files")
        eq((root / "configure.ac").read_text(), "AC_CHECK_HEADERS([cache/cache_int.h])\n",
           "configure probe follows the installed spelling")
        eq((root / "vmod.c").read_text(),
           "#ifdef HAVE_CACHE_CACHE_INT_H\n#include <cache/cache_int.h>\n#endif\n"
           '#include "cache_int.h"\nVINYLD\n',
           "include and autoconf macro follow the installed spelling; bare VINYLD rule still applies")

        back, _ = source_api_normalize.normalize_tree(root, "vinyl", "varnish")
        ok(back, "reverse normalization changes files")
        eq((root / "vmod.c").read_text(),
           "#ifdef HAVE_CACHE_CACHE_VARNISHD_H\n#include <cache/cache_varnishd.h>\n#endif\n"
           '#include "cache_varnishd.h"\nVARNISHD\n',
           "cache_int.h maps back to Varnish's one installed name")

        (root / "old.c").write_bytes(b"#include <cache/cache_vinyld.h>\n")
        source_api_normalize.normalize_tree(root, "vinyl", "varnish")
        eq((root / "old.c").read_text(), "#include <cache/cache_varnishd.h>\n",
           "the historic Vinyl spelling maps to Varnish too")

    try:
        source_api_normalize.replacements("varnish", "vinyl", vinyl_private_header="cache.h")
    except ValueError as exc:
        ok("unknown Vinyl private header" in str(exc), "unknown spelling is rejected")
    else:
        raise Fail("unknown private header spelling accepted")


@test
def same_family_normalization_touches_only_vsc_directives():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "configure.ac").write_bytes(b"PKG_CHECK_MODULES([VARNISHAPI], [varnishapi])\n")
        (root / "vmod.c").write_bytes(b"#include <cache/cache_varnishd.h>\nvarnish_vsc\n")
        (root / "VSC_vmod_kvm.vsc").write_bytes(
            b".. varnish_vsc_begin:: vmod_kvm\n.. varnish_vsc:: x\n.. varnish_vsc_end:: vmod_kvm\n"
        )
        (root / "quiet.vsc").write_bytes(b".. vinyl_vsc_begin:: quiet\n")
        changed, _ = source_api_normalize.normalize_tree(root, "varnish", "varnish")
        eq([str(path) for path, _ in changed], ["VSC_vmod_kvm.vsc"], "only the legacy .vsc changes")
        eq((root / "VSC_vmod_kvm.vsc").read_text(),
           ".. vinyl_vsc_begin:: vmod_kvm\n.. vinyl_vsc:: x\n.. vinyl_vsc_end:: vmod_kvm\n",
           "directives respelled for the shared vsctool")
        eq((root / "configure.ac").read_text(), "PKG_CHECK_MODULES([VARNISHAPI], [varnishapi])\n",
           "same-family pass leaves build spellings alone")
        eq((root / "vmod.c").read_text(), "#include <cache/cache_varnishd.h>\nvarnish_vsc\n",
           "same-family pass leaves C sources alone, even a varnish_vsc string")
        eq(source_api_normalize.normalization_name("varnish", "varnish"), "vsc-directives", "same-family name")
        eq(source_api_normalize.normalization_name("varnish", "vinyl"), "varnish-to-vinyl", "cross-family name")

        marker = root / "marker"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = source_api_normalize.main(["--source-family", "vinyl", "--target-family", "vinyl",
                                              "--marker", str(marker), str(root)])
        eq(code, 0, "a same-family pass with nothing to do succeeds")
        ok(not marker.exists(), "no marker when nothing changed")
        (root / "again.vsc").write_bytes(b".. varnish_vsc_begin:: again\n")
        with contextlib.redirect_stdout(out):
            code = source_api_normalize.main(["--source-family", "vinyl", "--target-family", "vinyl",
                                              "--marker", str(marker), str(root)])
        eq(code, 0, "same-family pass exit code")
        eq(marker.read_text(), "vsc-directives\n", "marker records the same-family pass")
        err = io.StringIO()
        untouched = root / "untouched"; untouched.mkdir()
        (untouched / "plain.c").write_bytes(b"int x;\n")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = source_api_normalize.main(["--source-family", "varnish", "--target-family", "vinyl",
                                              str(untouched)])
        eq(code, 1, "a cross-family pass that changes nothing still fails the build")
    ok("vsc-directives" in matrix.SOURCE_API_NORMALIZATIONS, "cell schema accepts the same-family value")


@test
def vsc_directive_cells_render_their_own_tooltip_sentence():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = write_fixture(tmp / "repo")
        results = tmp / "results"; results.mkdir()
        (results / "c.json").write_text(json.dumps(make_cell(
            "dict", "varnish-9.0.3", "debian-13-amd64", "compat", "pass", "2026-09-02T00:00:00Z",
            source_api_normalization="vsc-directives")))
        state_file = tmp / "state.json"
        code, _, err = run_cli(["merge", "--results-dir", str(results), "--state-file", str(state_file)])
        eq(code, 0, f"merge accepts vsc-directives: {err}")
        grid = matrix.build_grid(json.loads(state_file.read_text()), "debian-13-amd64", matrix.load_catalog(root))
        cell = grid["cells"][("dict", "varnish-9.0.3")]
        eq(cell["modifier"], "NORMALIZED", "same-family respelling still shows the translated edge")
        ok("VSC counter directives respelled" in cell["title"], "tooltip names the respelling")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


# Git exports these to hooks, and `git commit -a` points GIT_INDEX_FILE at a
# temporary index. Fixture repositories created by the tests would otherwise
# commit into the caller's index and die with "invalid object ... Error
# building trees" whenever the pre-commit hook runs the selftest.
GIT_HOOK_ENVIRONMENT = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
)


def main() -> int:
    for name in GIT_HOOK_ENVIRONMENT:
        os.environ.pop(name, None)
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
