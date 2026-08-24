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

import jsonschema_gen  # noqa: E402
import matrix  # noqa: E402
import package_contract  # noqa: E402
import recipe  # noqa: E402
import release_gate  # noqa: E402
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
    varnish_trunk = matrix.find_engine(catalog, "varnish-trunk")
    eq(varnish_trunk["source"], {
        "git_url": "https://github.com/varnish/varnish.git",
        "branch": "main",
    }, "varnish trunk follows the current upstream")
    ok("el10-aarch64" in matrix.find_engine(catalog, "vinyl-9.0.1")["targets"], "vinyl release has EL ARM64")


@test
def catalog_basicauth_tracks_its_real_head_branch():
    catalog = matrix.load_catalog(matrix.default_root())
    eq(catalog["vmods"]["basicauth"]["sources"]["head"], "master",
       "basicauth trunk source follows the upstream default branch")


@test
def catalog_dispatch_declares_its_required_engine_source():
    catalog = matrix.load_catalog(matrix.default_root())
    eq(catalog["vmods"]["dispatch"].get("engine_source"), "required",
       "dispatch receives VINYLSRC because upstream configure requires it")


@test
def catalog_promoted_vinyl_vmods_are_not_implicitly_varnish_promoted():
    catalog = matrix.load_catalog(matrix.default_root())
    for vmod_id in ("cachetag", "dict", "pesi", "remoteip", "tbf"):
        eq(catalog["vmods"][vmod_id]["package"].get("families"), ["vinyl"],
           f"{vmod_id}: package promotion remains Vinyl-only until Varnish proof")


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
def catalog_real_rust_vmods_are_unpromoted_and_explicitly_mapped():
    catalog = matrix.load_catalog(Path(__file__).resolve().parent.parent)
    expected = {
        "reqwest": ("v0.1.0", "libvmod_reqwest.so", "reqwest"),
        "fileserver": ("v0.1.0", "libvmod_fileserver.so", "fileserver"),
        "rers": ("v0.0.14", "libvmod_rers.so", "rers"),
        "fcgi": ("821221922e7437a22e668c42680d98e6560aa4ca", "libvmod_fastcgi.so", "fastcgi"),
    }
    for vmod_id, (ref, artifact, module) in expected.items():
        vmod = catalog["vmods"][vmod_id]
        eq(matrix.vmod_build(vmod), "cargo", f"{vmod_id} uses Cargo")
        eq(vmod["sources"]["default"]["ref"], ref, f"{vmod_id} immutable release ref")
        eq(matrix.vmod_artifacts(vmod), [artifact], f"{vmod_id} artifact mapping")
        eq(matrix.vmod_modules(vmod), [module], f"{vmod_id} VCL import mapping")
        ok("promoted" not in vmod["package"], f"{vmod_id} remains unpromoted")
        eq(vmod["package"].get("families"), ["varnish"], f"{vmod_id} family gate")


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
        eq(matrix.vmod_package_version("1.8", varnish),
           {"deb": "1.8-1~varnish9.0.3.1", "rpm_version": "1.8", "rpm_release": "1.varnish9.0.3.1"},
           "Varnish VMOD package version")


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
        eq([row for shard in matrix.shard_vmods(expansion["vmods"])
            for row in json.loads(shard["items"])], expansion["vmods"],
           "target filter limits every reusable-workflow shard")
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
        workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml").read_text()
        ok("inputs:\n      targets:" in workflow, "release dispatch accepts an optional target selector")
        ok('args+=(--targets "$RELEASE_TARGETS")' in workflow,
           "release expansion passes the optional selector without shell interpolation")
        ok('RELEASE_TARGETS: ${{ inputs.targets }}' in workflow,
           "release expansion and publication gate receive the same selector")
        ok('matrix.release_package_target_filter(catalog, os.environ.get("RELEASE_TARGETS", ""))' in workflow,
           "publication gate re-expands the filtered target cohort")


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
        eq(len(lines), 5, "github format includes source and package-pair matrices")
        ok(lines[0].startswith("engines=") and lines[1].startswith("vmods=")
           and lines[2].startswith("vmod_sources=") and lines[3].startswith("vmod_shards=")
           and lines[4].startswith("package_pairs="),
           "github output keys")
        engines = json.loads(lines[0][len("engines="):])
        vmods = json.loads(lines[1][len("vmods="):])
        sources = json.loads(lines[2][len("vmod_sources="):])
        shards = json.loads(lines[3][len("vmod_shards="):])
        package_pairs = json.loads(lines[4][len("package_pairs="):])
        ok(engines and vmods, "neither github array is empty")
        eq(len(sources), 1, "one trunk source feeds every matching VMOD cell")
        ok(all(set(r) >= {"engine", "target", "runner"} for r in engines), "engines= row shape")
        ok(all(r["row"] != r["engine"] for r in vmods), "vmods= excludes engine rows")
        eq([row for shard in shards for row in json.loads(shard["items"])], vmods,
           "vmod_shards preserves every VMOD row")
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
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["VMOD_BUILD_DEPS"], "'python3-docutils'", "debian build deps for a debian target")
        code, out, _ = run_cli(["env", "--engine", "varnish-9.0.3", "--vmod", "dict",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "Varnish env exit code")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["ENGINE_RUNTIME_PACKAGE"], "'varnish'", "Varnish runtime package comes from family")
        eq(values["ENGINE_DEVELOPMENT_PACKAGE"], "'varnish-dev'", "Varnish development package comes from family")
        eq(values["ENGINE_API"], "'varnishapi'", "Varnish API comes from family")
        eq(values["ENGINE_DAEMON"], "'varnishd'", "Varnish daemon comes from family")
        eq(values["ENGINE_RECIPE_DIR"], "'packaging/engine/varnish'", "Varnish recipe directory comes from family")
        eq(values["VMOD_PACKAGE_NAME"], "'varnish-vmod-dict'", "Varnish VMOD name comes from family")
        ok("VMOD_DEB_VERSION" not in values, "no package version for an un-packaged engine")
        code, out, _ = run_cli(["env", "--engine", "vinyl-trunk", "--vmod", "dict", "--root", root])
        eq(code, 0, "trunk env exit code")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
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


@test
def cohort_env_is_generated_from_the_promoted_catalog():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp)))
        code, out, _ = run_cli(["cohort-env", "--engine", "vinyl-9.0.1",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "cohort-env exit code")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
        eq(values["COHORT_MODULES"], "'dict'", "cohort imports every declared module")


@test
def env_emits_cargo_execution_contract():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(write_fixture(Path(tmp), engines=cargo_fixture_engines(), vmods={"reqwest": FIXTURE_CARGO}))
        code, out, _ = run_cli(["env", "--engine", "varnish-9.0.3", "--vmod", "reqwest",
                                "--target", "debian-13-amd64", "--root", root])
        eq(code, 0, "Cargo env exit code")
        values = dict(line.split("=", 1) for line in out.strip().split("\n"))
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
def vmod_compat_build_is_serial():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-vmod.sh").read_text()
    ok('make -j"$(nproc)" || make' not in script, "compat build does not retry a parallel make")
    ok("# VMOD generators are not reliably parallel-safe.\n  make -j1" in script,
       "compat build is serial from the outset")


@test
def cfg_declares_xxd_build_dependency():
    cfg = (Path(__file__).resolve().parent.parent / "vmods" / "cfg.yml").read_text()
    ok("      - xxd" in cfg, "cfg declares xxd for its generated source step")


@test
def vmod_cargo_compat_contract_is_offline_after_one_fetch():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-vmod.sh").read_text()
    library = (Path(__file__).resolve().parent.parent / "scripts" / "lib.sh").read_text()
    for expected in (
        "build_autotools()",
        "build_cargo()",
        "prepare_cargo",
        'python3 /repo/tools/cargo-artifacts.py --release-dir "$CARGO_TARGET_DIR/release"',
        'artifact_args+=(--mapping "${modules[$i]}=${artifacts[$i]}")',
        'load_modules "${sos[@]}"',
    ):
        ok(expected in script, f"Cargo compatibility path uses {expected}")
    for expected in (
        "[ -f Cargo.lock ]",
        "cargo metadata --locked --offline --no-deps",
        "cargo fetch --locked",
        'export RUSTUP_HOME=/work/rustup',
        'export CARGO_HOME=/work/cargo',
        'export RUSTUP_TOOLCHAIN="${RUST_VERSION:?}"',
        '[ ! -x "$CARGO_HOME/bin/rustup" ]',
        'rustup run "$RUSTUP_TOOLCHAIN" rustc --version',
        'rustc --version | grep -F "rustc $RUST_VERSION "',
        'cargo --version | grep -F "cargo $RUST_VERSION "',
    ):
        ok(expected in library, f"shared Cargo preparation uses {expected}")
    for expected in ("cargo build --release --locked --offline", "cargo test --release --locked --offline"):
        ok(expected in script, f"Cargo compatibility path uses {expected}")
    ok(script.count("prepare_cargo") == 2, "compat and package paths share Cargo preparation")
    ok(library.count("step cargo-fetch") == 1, "shared Cargo preparation fetches once")
    ok('retry_command 3 "cargo fetch" cargo fetch --locked' in library,
       "Cargo fetch uses the shared bounded retry runner")
    ok("dnf_install_retry clang clang-devel" in library,
       "EL Cargo preparation uses the EL10 clang development package")
    ok("libclang-devel" not in library,
       "EL Cargo preparation does not request the removed libclang-devel name")


@test
def vmod_autotools_aliases_use_the_engine_prefix():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-vmod.sh").read_text()
    ok('ENGINE_API_DATAROOTDIR="$PREFIX/share"' in script,
       "Autotools aliases use the relocatable engine prefix")
    ok('pkg-config --variable=datarootdir' not in script,
       "Autotools aliases do not inherit unresolved pkg-config placeholders")


@test
def container_image_pull_retries_transient_registry_failures():
    library = (Path(__file__).resolve().parent.parent / "scripts" / "lib.sh").read_text()
    ok('docker image inspect "$image"' in library, "container runner checks the local image cache")
    ok("ensure_container_image" in library, "container runner shares the explicit image helper")
    ok('retry_command 3 "docker pull $image"' in library,
       "container image pulls use the shared bounded retry runner")


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
    library = (root / "scripts" / "lib.sh").read_text()
    engine_script = (root / "scripts" / "build-engine.sh").read_text()
    ok("download_retry()" in library, "shared immutable-download retry helper exists")
    ok('download_retry "${ENGINE_TARBALL_URL:?}" "/work/tmp/$TAG.tar.gz"' in engine_script,
       "engine release fetch uses the retry helper")
    ok('download_retry "${ENGINE_TARBALL_URL:?}" "$ETREE_ROOT/engine.tgz"' in library,
       "VMOD engine-source fetch uses the retry helper")
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
    engine_script = (root / "scripts" / "build-engine.sh").read_text()
    library = (root / "scripts" / "lib.sh").read_text()
    ok('preserve_engine_private_headers "$SRC" "$PREFIX" "$ENGINE_DAEMON"' in engine_script,
       "engine builds preserve their generated private headers before archiving")
    ok('seed_engine_private_headers "$PREFIX" "$ENGINE_TREE" "$ENGINE_DAEMON"' in library,
       "VMOD source provisioning restores private headers from the engine artifact")
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
def debian_dependency_installs_retry_mirror_sync_failures():
    root = Path(__file__).resolve().parent.parent
    library = (root / "scripts" / "lib.sh").read_text()
    for helper in ("apt_update_retry()", "apt_install_retry()"):
        ok(helper in library, f"Debian dependency helper {helper} exists")
    retry_call = "apt_install_retry " + "\\"
    ok(retry_call in (root / "scripts" / "build-engine.sh").read_text(),
       "engine dependency install uses the retry helper")
    vmod_script = (root / "scripts" / "build-vmod.sh").read_text()
    ok(vmod_script.count(retry_call) >= 2,
       "compat and package VMOD dependency installs use the retry helper")
    ok("rm -rf /var/lib/apt/lists/*" in library,
       "retry clears stale Debian package indexes")
    install_recovery = library.split("apt_install_recover() {", 1)[1].split("\n}", 1)[0]
    ok("apt-get update -qq" in install_recovery and "apt_update_retry" not in install_recovery,
       "install recovery refreshes once instead of nesting a second retry loop")


@test
def rpm_dependency_installs_retry_repo_metadata_failures():
    root = Path(__file__).resolve().parent.parent
    library = (root / "scripts" / "lib.sh").read_text()
    ok("dnf_install_retry()" in library, "RPM dependency retry helper exists")
    ok("dnf clean all" in library, "RPM retry clears stale repository metadata")
    for name in ("build-engine.sh", "build-vmod.sh"):
        script = (root / "scripts" / name).read_text()
        ok("dnf_install_retry " in script, f"{name} uses the RPM retry helper")


@test
def vmod_clone_retries_transient_failures():
    root = Path(__file__).resolve().parent.parent
    library = (root / "scripts" / "lib.sh").read_text()
    ok("clone_vmod()" in library, "shared library wraps VMOD source clones")
    ok('retry_command 3 "git clone $url"' in library, "VMOD clone uses the shared retry runner")
    ok('materialize_vmod_source "$VMOD_GIT" "$VMOD_REF"' in library,
       "local VMOD checkout retains a direct-clone fallback")
    ok('restore_vmod_source "$VMOD_SOURCE_ARTIFACT"' in library,
       "CI VMOD checkout restores its workflow source artifact")
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
def workflow_prefetches_each_vmod_source_for_build_cells():
    root = Path(__file__).resolve().parent.parent
    for name in ("matrix.yml", "trunk.yml", "release.yml"):
        workflow = (root / ".github" / "workflows" / name).read_text()
        ok("vmod_sources: ${{ steps.expand.outputs.vmod_sources }}" in workflow,
           f"{name} exports the deduplicated source matrix")
        ok("scripts/fetch-vmod-source.sh" in workflow,
           f"{name} fetches each resolved VMOD source")
        ok("name: ${{ matrix.source_artifact }}" in workflow,
           f"{name} publishes source artifacts under their matrix identity")
        ok("needs: [expand, engine, vmod_source]" in workflow,
           f"{name} waits for source acquisition before starting VMOD cells")
    shard = (root / ".github" / "workflows" / "vmod-shard.yml").read_text()
    ok("name: ${{ matrix.source_artifact }}" in shard,
       "VMOD cells download their resolved source artifact")
    ok("VCACHE_REQUIRE_PREFETCHED_VMOD_SOURCE: \"1\"" in shard,
       "CI cells cannot silently fall back to upstream clones")
    ok((root / "scripts" / "fetch-vmod-source.sh").is_file(),
       "the source acquisition job has a host-safe script entry point")


@test
def remaining_script_network_boundaries_use_shared_retries():
    root = Path(__file__).resolve().parent.parent
    library = (root / "scripts" / "lib.sh").read_text()
    cohort = (root / "scripts" / "test-package-cohort.sh").read_text()
    overlay = (root / "scripts" / "probe-upstream-varnish-overlay.sh").read_text()
    ordering = (root / "scripts" / "check-package-version-ordering.sh").read_text()
    for expected in (
        'git_retry "fetch VMOD ref $ref" -C "$destination" fetch --depth 1 origin "$ref"',
        'git_retry "update VMOD submodules" -C "$destination" submodule update --init --recursive',
        'download_retry https://sh.rustup.rs "$RUSTUP_INIT"',
        'retry_command 3 "rustup bootstrap" sh "$RUSTUP_INIT"',
        'retry_command 3 "install Rust toolchain $RUSTUP_TOOLCHAIN" rustup toolchain install',
        'apt_install_retry "$package_dir"/',
    ):
        ok(expected in library, f"shared library is missing retried network boundary {expected!r}")
    for expected in ("apt_update_retry", "apt_install_retry", "dnf_install_retry"):
        ok(expected in cohort, f"cohort uses {expected}")
    for expected in ("apt_update_retry", "apt_install_retry"):
        ok(expected in overlay, f"overlay uses {expected}")
    ok('download_retry https://packages.varnish-software.com/varnish/varnish.pub.asc' in overlay,
       "overlay signing key download uses the shared retry helper")
    ok(ordering.count("ensure_container_image") == 2,
       "version-order proof explicitly obtains both images with retries")


@test
def workflow_control_plane_commands_use_shared_retries():
    root = Path(__file__).resolve().parent.parent
    for name in ("matrix.yml", "trunk.yml"):
        workflow = (root / ".github" / "workflows" / name).read_text()
        ok('../repo/scripts/matrix-state.sh checkout' in workflow,
           f"{name} shares state-branch checkout")
        ok('../repo/scripts/matrix-state.sh publish "$GITHUB_RUN_ID"' in workflow,
           f"{name} shares state-branch publication")
    state_script = (root / "scripts" / "matrix-state.sh").read_text()
    for expected in (
        "git_remote_head_exists_retry origin ci-state/matrix",
        'git_retry "fetch matrix state" fetch --depth 1 origin ci-state/matrix',
        'git_retry "push matrix state" push origin HEAD:ci-state/matrix',
    ):
        ok(expected in state_script, f"matrix-state adapter is missing {expected!r}")
    release = (root / ".github" / "workflows" / "release.yml").read_text()
    ok('"scripts/retry.sh", "release"' in release,
       "stable release replacement is routed through the shared retry helper")
    retry_script = (root / "scripts" / "retry.sh").read_text()
    ok("replace_github_release_retry" in retry_script,
       "retry command adapter delegates release replacement to lib.sh")


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
def package_load_failure_reports_the_end_of_compiler_output():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-vmod.sh").read_text()
    ok('tail -n 40 /tmp/load.log' in script, "package load failure prints diagnostic tail")
    ok('sed -n \'1,40p\' /tmp/load.log' not in script,
       "package load failure no longer ends on a source-file header")


@test
def vmod_package_collection_and_install_use_family_names():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-vmod.sh").read_text()
    library = (Path(__file__).resolve().parent.parent / "scripts" / "lib.sh").read_text()
    combined = script + library
    ok("vinyl-vmod-" not in combined, "VMOD package collection has no Vinyl literal")
    for expected in (
        'VMOD_PACKAGE_NAME=${VMOD_PACKAGE_NAME:?}',
        'ENGINE_RUNTIME_PACKAGE=${ENGINE_RUNTIME_PACKAGE:?}',
        'ENGINE_DEVELOPMENT_PACKAGE=${ENGINE_DEVELOPMENT_PACKAGE:?}',
        'NAMEDIR="$VMOD_PACKAGE_NAME-${VMOD_VERSION:?}"',
        '"/work/tmp/$TAG-recipe/$VMOD_PACKAGE_NAME.spec"',
        'select_native_package deb "$VMOD_PACKAGE_NAME" /work/tmp/*.deb',
        'select_native_package rpm "$VMOD_PACKAGE_NAME" "$TOPD"/RPMS/*/*.rpm',
        '"$ENGINE_RUNTIME_PACKAGE"_*.deb',
        '"$ENGINE_DEVELOPMENT_PACKAGE"_*.deb',
        '"$ENGINE_RUNTIME_PACKAGE"-*.rpm',
        '"$ENGINE_DEVELOPMENT_PACKAGE"-*.rpm',
        'pkg-config --variable=vmoddir "$ENGINE_API"',
    ):
        ok(expected in combined, f"VMOD package flow uses {expected}")
    ok(script.count('install_engine_packages "$ENGINE_PKGDIR"') == 3,
       "build and fresh-install paths share engine package installation")
    ok('/work/tmp/*.deb' in script and '"$TOPD"/RPMS/*/*.rpm' in script,
       "every binary emitted by a VMOD recipe gets a native architecture check")
    ok('for c in vinyld varnishd' not in script, "VMOD load checks use the family daemon")


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
def engine_daemon_smoke_check_preserves_failure():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "build-engine.sh").read_text()
    ok('"$PREFIX/sbin/$ENGINE_DAEMON" -V 2>&1\n' in script,
       "engine smoke check executes the family daemon directly")
    ok('"$PREFIX/sbin/$ENGINE_DAEMON" -V 2>&1 | head -2 || true' not in script,
       "engine smoke check does not discard the daemon exit status")


@test
def engine_family_recipes_and_script_use_the_contract():
    root = Path(__file__).resolve().parent.parent
    vinyl = root / "packaging" / "engine" / "vinyl"
    varnish = root / "packaging" / "engine" / "varnish"
    for recipe_dir, runtime, development in [
        (vinyl, "vinyl-cache", "vinyl-cache-dev"),
        (varnish, "varnish", "varnish-dev"),
    ]:
        ok((recipe_dir / "debian" / "control").is_file(), f"{runtime}: Debian control exists")
        ok((recipe_dir / "debian" / "rules").is_file(), f"{runtime}: Debian rules exists")
        ok((recipe_dir / f"{runtime}.spec").is_file(), f"{runtime}: RPM spec exists")
        spec = (recipe_dir / f"{runtime}.spec").read_text()
        ok('%{!?engine_release:%{error:' in spec, f"{runtime}: RPM recipe requires package revision")
        control = (recipe_dir / "debian" / "control").read_text()
        ok(f"Package: {runtime}" in control, f"{runtime}: runtime identity")
        ok(f"Package: {development}" in control, f"{runtime}: development identity")
    ok(not (root / "packaging" / "engine" / "debian").exists(), "old unscoped Debian recipe directory moved")
    script = (root / "scripts" / "build-engine.sh").read_text()
    for exported in ("ENGINE_RUNTIME_PACKAGE", "ENGINE_DEVELOPMENT_PACKAGE", "ENGINE_RECIPE_DIR",
                     "ENGINE_SOURCE_NAME", "ENGINE_RPM_ARCHIVE_STEM", "ENGINE_DAEMON",
                     "ENGINE_PACKAGE_REVISION"):
        ok(f"${{{exported}:?}}" in script, f"build script requires {exported} from matrix env")
    ok("$ENGINE_SOURCE_NAME ($ENGINE_VERSION-$ENGINE_PACKAGE_REVISION)" in script,
       "engine Debian package version uses the package revision")
    ok('--define "engine_release $ENGINE_PACKAGE_REVISION"' in script,
       "engine RPM package release uses the package revision")
    ok("/repo/packaging/engine/debian" not in script, "build script has no unscoped recipe path")
    ok("/repo/packaging/engine/vinyl-cache.spec" not in script, "build script has no Vinyl RPM spec path")


@test
def missing_engine_artifact_reaches_vmod_classifier():
    workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows" /
                "vmod-shard.yml").read_text()
    start = workflow.index("      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1")
    end = workflow.index("      - run: scripts/build-vmod.sh", start)
    download_step = workflow[start:end]
    ok("continue-on-error: true" in download_step,
       "a missing engine artifact does not stop the job before build-vmod.sh classifies it")


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
        vmod_dir = pkgdl / "packages-dict-vinyl-9.0.1-debian-13-amd64"
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


@test
def stable_release_keeps_green_pairs_independent():
    workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows" /
                "release.yml").read_text()
    ok("github.ref == 'refs/heads/main'" in workflow,
       "stable release job is restricted to main")
    ok("group: release-stable" in workflow and "cancel-in-progress: false" in workflow,
       "stable release replacement is serialized without cancellation")
    publish = workflow.index("for tag, dist, body, names in prepared:")
    gate_failure = workflow.index("release validation failed for one or more pairs", publish)
    ok(publish < gate_failure,
       "a failed pair reports red only after all independently green pairs publish")
    ok(workflow.index('sys.exit(1)', gate_failure) > gate_failure,
       "a failed pair still makes the release workflow fail")
    ok("needs: [expand, engine, vmod, cohort]" in workflow,
       "publication cannot run before the full package cohort smoke test")
    cohort_script = (Path(__file__).resolve().parent.parent / "scripts" /
                     "test-package-cohort.sh").read_text()
    ok("for module in $COHORT_MODULES" in cohort_script,
       "cohort VCL is generated from authoritative package.modules metadata")
    ok('kill -0 "$PID"' in cohort_script,
       "cohort smoke proves the daemon survives actual startup")
    ok("cohort_results.get((engine, target), \"missing result\")" in workflow,
       "the pair gate consumes the cohort result instead of trusting job topology")


@test
def upstream_varnish_overlay_is_strictly_non_publishing_evidence():
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github" / "workflows" / "upstream-varnish-overlay.yml").read_text()
    probe = (root / "scripts" / "probe-upstream-varnish-overlay.sh").read_text()
    ok("workflow_dispatch:" in workflow and "schedule:" not in workflow,
       "upstream overlay proof is manual evidence, not fleet surveillance")
    ok("select-engine --family varnish --kind release" in workflow and "TARGET_RUNNER" in workflow,
       "overlay engine and runner are selected from the catalog")
    ok("gh release" not in workflow and "release.yml" not in workflow,
       "experimental overlay workflow has no publication path")
    ok("STRICT_ABI=$(dpkg-query" in probe and "varnishd-abi-" in probe,
       "overlay discovers the strict ABI from the installed upstream package")
    ok("Depends: varnish (= ${UPSTREAM_VERSION}), ${STRICT_ABI}" in probe,
       "overlay proof package binds exact upstream version and strict ABI")
    ok("COHORT.json" not in probe and "PACKAGE-CONTRACT.json" not in probe,
       "overlay keeps no unconsumed evidence ledger")


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
                       "data-theme", "https://example.org/runs/1",
                       'class="github-badge" href="https://github.com/boffinate/vcache-packaging/"',
                       'aria-label="View vcache-packaging on GitHub"',
                       'class="target-matrices"',
                       'grid-template-columns:repeat(auto-fit,minmax(min(100%,580px),1fr))',
                       '.matrix-scroll{width:fit-content;max-width:100%;overflow-x:auto;'):
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
        ok("Release:        1.vinyl9.0.1.1%{?dist}" in spec, "rpm release")
        ok("Requires:       vinyl-cache%{?_isa} = 9.0.1-1%{?dist}" in spec,
           "exact-version arch-qualified engine requires")
        ok("BuildRequires:  vinyl-cache-devel = 9.0.1-1%{?dist}" in spec, "exact-version -devel requires")
        ok("BuildRequires:  python3-docutils" in spec, "manifest build_deps included")


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
        ok("Built against Varnish Cache 9.0.3" in changelog, "Varnish family description")

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
        ok("Built against Varnish Cache 9.0.3" in spec, "Varnish RPM family description")


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
