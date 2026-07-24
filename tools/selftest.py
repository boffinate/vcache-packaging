#!/usr/bin/env python3
"""Tests for the Vinyl cohort registry tooling.

Run directly, or via `python3 tools/release_tool.py selftest`.
Standard library only; no build tooling is invoked.

The synthetic fixtures build a two-repository layout in a temporary
directory -- a registry checkout beside a libvmod-cachetag checkout -- because
that is the shape the tooling now runs in: the manifests live here and the
authoritative cachetag version lives in a different repository.

The digest test vectors below were computed independently of this
implementation with:

    printf 'cachetag-cohort-input/v1\\nvinyl-source-sha256=%s\\npatch-count=2\\n...' ... | shasum -a 256

so a bug in cohort_input_blob() cannot silently agree with itself.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manifest  # noqa: E402
import metadata as metadata_mod  # noqa: E402
import yaml_subset  # noqa: E402

# --- hand-computed vectors -------------------------------------------------

VEC_SOURCE = "0123456789abcdef" * 4
VEC_PATCH_0 = "fedcba9876543210" * 4
VEC_PATCH_1 = "00112233445566778899aabbccddeeff" * 2
VEC_SHA256 = "546d7171ef8e64724e444e69c06fd8e5cc319050324f9ad670fcbd45831ae50e"
VEC_INPUT_ID = VEC_SHA256[:12]  # 546d7171ef8e

TEMPLATE_ZERO_SHA256 = "018f1ab810ef811d26fae909b42883f280ab74404cfb71f85a6c8e7b946a1aec"

VALID_COHORT_ID = f"vinyl-9.0.0-{VEC_INPUT_ID}"

CONFIGURE_AC = """AC_PREREQ([2.68])
AC_INIT([libvmod-cachetag], [1.0.0])
"""

VALID_COHORT = f"""schema: cachetag-cohort/v1
status: released
cohort: {VALID_COHORT_ID}
cachetag:
  version: 1.0.0
  source_sha256: "{'ab' * 32}"
  git_commit: "{'1' * 40}"
vinyl:
  version: 9.0.0
  source_url: https://code.vinyl-cache.org/vinyl-cache-9.0.0.tgz
  source_sha256: "{VEC_SOURCE}"
  git_commit: "{'2' * 40}"
  vrt: 23.0
  strict_abi: a90954814766d933a75d4c808c449cb9bc0ae3d3
  patches:
    - name: 0001-fix-thing.patch
      sha256: "{VEC_PATCH_0}"
    - name: 0002-fix-other-thing.patch
      sha256: "{VEC_PATCH_1}"
build_profile:
  name: production
  revision: 3
required_vmods:
  - cachetag
storage_support:
  - default
targets:
  - debian-13-amd64
support:
  channel: pre-release
  release_owner: Release Owner
  fellow: excluded
  buddy: source-harness-only
"""

VALID_TARGET = f"""schema: cachetag-target/v1
status: released
lane: cohort
cohort: {VALID_COHORT_ID}
target:
  id: debian-13-amd64
  distro: debian
  distro_release: 13
  distro_id: debian-13
  arch: amd64
  package_format: deb
  dist_tag: ""
package:
  revision: 2
  source_name: libvmod-cachetag
  binary_name: libvmod-cachetag
vinyl_packages:
  origin: cohort
  runtime_name: vinyl-cache
  runtime_version: 9.0.0-1
  dev_name: vinyl-cache-dev
  dev_version: 9.0.0-1
build:
  profile: production
  image_ref: docker.io/library/debian:13
  image_digest: "sha256:{'3' * 64}"
  compiler: gcc 14.2.0-1
  configure_options: --prefix=/usr --libdir=/usr/lib/x86_64-linux-gnu
  cflags: -O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIC
  ldflags: -Wl,-z,relro -Wl,-z,now
  source_date_epoch: 1780000000
  hardening_check: pass
  build_dependencies:
    - name: debhelper
      version: 13.24.1
    - name: vinyl-cache-dev
      version: 9.0.0-1
install:
  vmoddir: /usr/lib/x86_64-linux-gnu/vinyl-cache/vmods
  vmoddir_source: pkg-config
artifacts:
  - filename: libvmod-cachetag_1.0.0-2_amd64.deb
    sha256: "{'4' * 64}"
tests:
  package_lint: pass
  installed_package_smoke: pass
  full_behavior_suite: pass
  upgrade_transactions: pass
"""


class Failure(Exception):
    pass


_RESULTS: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(condition), detail))


def _write_workspace(
    root: Path, cohort_text: str, target_text: str, cohort_id: str = VALID_COHORT_ID
) -> tuple:
    """Lay out a synthetic workspace: a registry checkout beside a cachetag one.

    Returns (registry_root, cachetag_src). The two are siblings, so omitting an
    explicit cachetag path exercises the default sibling resolution.
    """
    cachetag_src = root / "libvmod-cachetag"
    registry_root = root / "vinyl-packaging"
    cachetag_src.mkdir(parents=True, exist_ok=True)
    (cachetag_src / "configure.ac").write_text(CONFIGURE_AC, encoding="utf-8")
    (registry_root / "registry" / "cohorts").mkdir(parents=True, exist_ok=True)
    (registry_root / "registry" / "targets" / cohort_id).mkdir(parents=True, exist_ok=True)
    (registry_root / "registry" / "distro-native").mkdir(parents=True, exist_ok=True)
    (registry_root / "registry" / "cohorts" / f"{cohort_id}.yml").write_text(
        cohort_text, encoding="utf-8"
    )
    (registry_root / "registry" / "targets" / cohort_id / "debian-13-amd64.yml").write_text(
        target_text, encoding="utf-8"
    )
    return registry_root, cachetag_src


def _validate_synthetic(cohort_text: str, target_text: str, cohort_id: str = VALID_COHORT_ID, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        registry_root, cachetag_src = _write_workspace(
            Path(tmp), cohort_text, target_text, cohort_id
        )
        kw.setdefault("cachetag_src", cachetag_src)
        return manifest.validate_registry_tree(repo_root=registry_root, **kw)


# --- parser tests ----------------------------------------------------------


def test_parser() -> None:
    data = yaml_subset.parse(
        "a: 1\nb:\n  c: two\n  d:\n    - x\n    - y\ne: []\nf:\n  - name: n\n    sha256: s\n"
    )
    check(
        "parser: nested maps, lists, empty list, list of maps",
        data == {
            "a": "1",
            "b": {"c": "two", "d": ["x", "y"]},
            "e": [],
            "f": [{"name": "n", "sha256": "s"}],
        },
        repr(data),
    )
    check("parser: no type coercion (23.0 stays a string)", yaml_subset.parse("vrt: 23.0")["vrt"] == "23.0")
    check("parser: quoted empty string", yaml_subset.parse('dist_tag: ""')["dist_tag"] == "")

    rejects = [
        ("tab indentation", "a:\n\tb: c\n"),
        ("duplicate key", "a: 1\na: 2\n"),
        ("flow mapping", "a: {b: c}\n"),
        ("non-empty flow sequence", "a: [1, 2]\n"),
        ("anchor", "a: &anchor 1\n"),
        ("alias", "a: *anchor\n"),
        ("block scalar", "a: |\n  text\n"),
        ("document marker", "---\na: 1\n"),
        ("odd indentation", "a:\n   b: c\n"),
        ("unterminated quote", 'a: "b\n'),
        ("trailing comment on a value", "a: 1 # comment\n"),
        ("dangling key", "a:\nb: 1\n"),
        ("carriage return", "a: 1\r\n"),
        ("bare dash item", "a:\n  -\n"),
    ]
    for name, text in rejects:
        try:
            yaml_subset.parse(text)
            check(f"parser rejects {name}", False, "parsed without error")
        except yaml_subset.ManifestSyntaxError:
            check(f"parser rejects {name}", True)


# --- digest tests ----------------------------------------------------------


def test_digest() -> None:
    cohort = yaml_subset.parse(VALID_COHORT)
    blob = manifest.cohort_input_blob(cohort)
    expected_blob = (
        "cachetag-cohort-input/v1\n"
        f"vinyl-source-sha256={VEC_SOURCE}\n"
        "patch-count=2\n"
        f"patch[0]-sha256={VEC_PATCH_0}\n"
        f"patch[1]-sha256={VEC_PATCH_1}\n"
        "build-profile=production\n"
        "build-profile-revision=3\n"
    ).encode("utf-8")
    check("digest: canonical blob byte-for-byte", blob == expected_blob, repr(blob))
    import hashlib

    check(
        "digest: sha256 matches the hand-computed vector",
        hashlib.sha256(blob).hexdigest() == VEC_SHA256,
        hashlib.sha256(blob).hexdigest(),
    )
    check("digest: input-id is the first 12 hex chars", manifest.cohort_input_id(cohort) == VEC_INPUT_ID)
    check("digest: cohort identifier form", manifest.cohort_identifier(cohort) == VALID_COHORT_ID)

    reordered = yaml_subset.parse(VALID_COHORT)
    reordered["vinyl"]["patches"].reverse()
    check(
        "digest: patch order changes the identity",
        manifest.cohort_input_id(reordered) != VEC_INPUT_ID,
    )
    bumped = yaml_subset.parse(VALID_COHORT)
    bumped["build_profile"]["revision"] = "4"
    check(
        "digest: build-profile revision changes the identity",
        manifest.cohort_input_id(bumped) != VEC_INPUT_ID,
    )

    template_like = yaml_subset.parse(VALID_COHORT)
    template_like["vinyl"]["source_sha256"] = "0" * 64
    template_like["vinyl"]["patches"] = []
    template_like["build_profile"]["revision"] = "1"
    check(
        "digest: zero-patch vector matches the second hand-computed vector",
        hashlib.sha256(manifest.cohort_input_blob(template_like)).hexdigest() == TEMPLATE_ZERO_SHA256,
    )


# --- validation tests ------------------------------------------------------


def test_valid_manifest_passes() -> None:
    checked, errors = _validate_synthetic(VALID_COHORT, VALID_TARGET)
    check("validate: a valid released cohort+target passes", errors == [], "; ".join(errors))
    check("validate: both manifests were checked", len(checked) == 2, str(checked))
    checked, errors = _validate_synthetic(VALID_COHORT, VALID_TARGET, require_releasable=True)
    check(
        "validate: the valid pair is also releasable",
        errors == [],
        "; ".join(errors),
    )


def test_version_mismatch_fails() -> None:
    bad = VALID_COHORT.replace("version: 1.0.0", "version: 1.0.1", 1)
    checked, errors = _validate_synthetic(bad, VALID_TARGET)
    check(
        "validate: cachetag version not matching configure.ac fails",
        any("does not match configure.ac AC_INIT" in e for e in errors),
        "; ".join(errors),
    )


def test_bad_cohort_id_fails() -> None:
    wrong_id = "vinyl-9.0.0-deadbeef0000"
    bad_cohort = VALID_COHORT.replace(VALID_COHORT_ID, wrong_id)
    bad_target = VALID_TARGET.replace(VALID_COHORT_ID, wrong_id)
    checked, errors = _validate_synthetic(bad_cohort, bad_target, cohort_id=wrong_id)
    check(
        "validate: an input-id that is not the digest of the inputs fails",
        any("does not match the digest" in e for e in errors),
        "; ".join(errors),
    )

    malformed = "vinyl-9.0.0-NOTHEX123456"
    bad_cohort = VALID_COHORT.replace(VALID_COHORT_ID, malformed)
    bad_target = VALID_TARGET.replace(VALID_COHORT_ID, malformed)
    checked, errors = _validate_synthetic(bad_cohort, bad_target, cohort_id=malformed)
    check(
        "validate: a malformed cohort id fails the format check",
        any("does not match" in e for e in errors),
        "; ".join(errors),
    )

    mismatched_version = "vinyl-9.1.0-" + VEC_INPUT_ID
    bad_cohort = VALID_COHORT.replace(VALID_COHORT_ID, mismatched_version)
    bad_target = VALID_TARGET.replace(VALID_COHORT_ID, mismatched_version)
    checked, errors = _validate_synthetic(bad_cohort, bad_target, cohort_id=mismatched_version)
    check(
        "validate: a cohort id whose upstream version disagrees with vinyl.version fails",
        any("does not embed vinyl.version" in e for e in errors),
        "; ".join(errors),
    )


def test_schema_failures() -> None:
    missing = "\n".join(line for line in VALID_COHORT.splitlines() if not line.startswith("  vrt:")) + "\n"
    checked, errors = _validate_synthetic(missing, VALID_TARGET)
    check(
        "validate: a missing required field fails",
        any("vinyl.vrt: missing required field" in e for e in errors),
        "; ".join(errors),
    )
    extra = VALID_COHORT + "unexpected_field: 1\n"
    checked, errors = _validate_synthetic(extra, VALID_TARGET)
    check(
        "validate: an unknown field fails",
        any("unexpected_field: unknown field" in e for e in errors),
        "; ".join(errors),
    )
    leftover = VALID_TARGET.replace("compiler: gcc 14.2.0-1", "compiler: PLACEHOLDER")
    checked, errors = _validate_synthetic(VALID_COHORT, leftover)
    check(
        "validate: a placeholder left in a released manifest fails",
        any("placeholder values remain" in e for e in errors),
        "; ".join(errors),
    )
    mismatched_status = VALID_TARGET.replace("status: released", "status: candidate")
    checked, errors = _validate_synthetic(VALID_COHORT, mismatched_status)
    check(
        "validate: target status must match its cohort status",
        any("does not match the cohort status" in e for e in errors),
        "; ".join(errors),
    )
    unlisted = VALID_TARGET.replace("id: debian-13-amd64", "id: debian-13-arm64").replace(
        "arch: amd64", "arch: arm64"
    )
    checked, errors = _validate_synthetic(VALID_COHORT, unlisted)
    check(
        "validate: a target file whose id is not listed in the cohort fails",
        any("do not match the manifests present" in e or "not listed in the cohort" in e for e in errors),
        "; ".join(errors),
    )


def test_vmoddir() -> None:
    checked, errors = _validate_synthetic(VALID_COHORT, VALID_TARGET)
    check("vmoddir: a resolved absolute vmoddir passes", errors == [], "; ".join(errors))

    rejects = [
        ("unexpanded pkg-config variable", '"${libdir}/vinyl-cache/vmods"'),
        ("relative path", "usr/lib/vinyl-cache/vmods"),
        ("trailing slash", "/usr/lib/vinyl-cache/vmods/"),
        ("empty path segment", "/usr//vinyl-cache/vmods"),
    ]
    good = "vmoddir: /usr/lib/x86_64-linux-gnu/vinyl-cache/vmods"
    for name, value in rejects:
        bad = VALID_TARGET.replace(good, f"vmoddir: {value}")
        checked, errors = _validate_synthetic(VALID_COHORT, bad)
        check(
            f"vmoddir: rejects {name}",
            any("install.vmoddir" in e for e in errors),
            "; ".join(errors),
        )

    harness = VALID_TARGET.replace(good, "vmoddir: /tmp/vinyl-prefix/lib/vinyl-cache/vmods")
    checked, errors = _validate_synthetic(VALID_COHORT, harness)
    check(
        "vmoddir: rejects the Docker harness /tmp/vinyl-prefix path",
        any("test-harness prefix" in e for e in errors),
        "; ".join(errors),
    )

    handwritten = VALID_TARGET.replace("vmoddir_source: pkg-config", "vmoddir_source: recorded")
    checked, errors = _validate_synthetic(VALID_COHORT, handwritten)
    check(
        "vmoddir: a hand-recorded source is allowed while a target is not yet releasable",
        errors == [],
        "; ".join(errors),
    )
    checked, errors = _validate_synthetic(VALID_COHORT, handwritten, require_releasable=True)
    check(
        "vmoddir: a releasable target must take vmoddir from pkg-config",
        any("must be 'pkg-config'" in e for e in errors),
        "; ".join(errors),
    )

    meta = metadata_mod.target_metadata(yaml_subset.parse(VALID_TARGET), yaml_subset.parse(VALID_COHORT))
    check(
        "vmoddir: metadata carries the resolved directory",
        meta["install"]["vmoddir"] == "/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods",
        str(meta["install"]),
    )
    check(
        "vmoddir: metadata exposes the @VINYL_VMODDIR@ substitution token",
        meta["substitutions"]["@VINYL_VMODDIR@"] == "/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods",
        str(meta["substitutions"]),
    )
    shell = metadata_mod.as_shell(meta)
    check(
        "vmoddir: shell rendering exports the directory",
        "CACHETAG_INSTALL_VMODDIR='/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods'" in shell,
        shell,
    )
    import re as _re

    bad_names = [
        line.split("=", 1)[0]
        for line in shell.splitlines()
        if not _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", line.split("=", 1)[0])
    ]
    check(
        "vmoddir: every shell variable name is a valid POSIX identifier",
        bad_names == [],
        str(bad_names),
    )


def test_repo_templates(repo_root: Path, cachetag_src: Path) -> None:
    checked, errors = manifest.validate_registry_tree(
        repo_root=repo_root, cachetag_src=cachetag_src
    )
    check(
        "repo: checked-in registry/ manifests are schema-valid",
        errors == [],
        "; ".join(errors),
    )
    check("repo: at least three manifests are checked", len(checked) >= 3, str(checked))
    check(
        "repo: the cachetag version comes from the separate cachetag checkout",
        manifest.configure_ac_version(cachetag_src, repo_root=repo_root)
        == manifest.load_cohort(
            repo_root / "registry" / "cohorts" / "vinyl-9.0.0-000000000000.yml"
        )["cachetag"]["version"],
        str(cachetag_src),
    )
    checked, errors = manifest.validate_registry_tree(
        repo_root=repo_root, require_releasable=True, cachetag_src=cachetag_src
    )
    check(
        "repo: checked-in templates are refused for release use",
        any("never releasable" in e for e in errors),
        "; ".join(errors),
    )
    cohort = manifest.load_cohort(
        repo_root / "registry" / "cohorts" / "vinyl-9.0.0-000000000000.yml"
    )
    check(
        "repo: the template cohort id is the reserved placeholder input-id",
        cohort["cohort"].endswith(manifest.PLACEHOLDER_INPUT_ID),
    )
    fake_real = manifest.load_cohort(
        repo_root / "registry" / "cohorts" / "vinyl-9.0.0-000000000000.yml"
    )
    fake_real["status"] = "released"
    errors = manifest.validate_cohort(fake_real, "synthetic", "1.0.0")
    check(
        "repo: flipping the template to released fails on placeholders and digest",
        any("placeholder values remain" in e for e in errors)
        and any("does not match the digest" in e for e in errors),
        "; ".join(errors),
    )


# --- metadata tests --------------------------------------------------------


def test_metadata() -> None:
    versions = metadata_mod.package_versions("1.0.0", 1, "")
    check("metadata: debian version at revision 1", versions["debian"]["version"] == "1.0.0-1")
    check("metadata: arch pkgrel at revision 1", versions["arch"]["pkgrel"] == "1")
    check("metadata: freebsd PORTREVISION at revision 1 is 0", versions["freebsd"]["portrevision"] == "0")
    check("metadata: freebsd pkg version omits _0", versions["freebsd"]["pkg_version"] == "1.0.0")
    check("metadata: alpine pkgrel at revision 1 is 0", versions["alpine"]["pkgrel"] == "0")

    versions = metadata_mod.package_versions("1.0.0", 3, "el9")
    check("metadata: rpm release includes the dist tag", versions["rpm"]["release"] == "3.el9")
    check("metadata: rpm version excludes the revision", versions["rpm"]["version"] == "1.0.0")
    check("metadata: freebsd pkg version at revision 3", versions["freebsd"]["pkg_version"] == "1.0.0_2")
    check("metadata: alpine pkgrel at revision 3", versions["alpine"]["pkgrel"] == "2")

    cohort = yaml_subset.parse(VALID_COHORT)
    target = yaml_subset.parse(VALID_TARGET)
    meta = metadata_mod.target_metadata(target, cohort)
    check(
        "metadata: deb native filename",
        meta["artifacts"]["native_filename"] == "libvmod-cachetag_1.0.0-2_amd64.deb",
        meta["artifacts"]["native_filename"],
    )
    check(
        "metadata: release asset filename carries distro and arch",
        meta["artifacts"]["release_asset_filename"] == "libvmod-cachetag-1.0.0-2-debian-13-amd64.deb",
        meta["artifacts"]["release_asset_filename"],
    )
    check(
        "metadata: debian source package filenames",
        meta["artifacts"]["source_package_filenames"]
        == [
            "libvmod-cachetag_1.0.0.orig.tar.gz",
            "libvmod-cachetag_1.0.0-2.debian.tar.xz",
            "libvmod-cachetag_1.0.0-2.dsc",
        ],
        str(meta["artifacts"]["source_package_filenames"]),
    )
    check(
        "metadata: abi provide string",
        meta["abi"]["abi_provide"] == "vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3",
    )
    check(
        "metadata: debian depends string",
        meta["abi"]["deb_depends"]
        == "vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3, vinyld-vrt (= 23.0)",
        meta["abi"]["deb_depends"],
    )
    check("metadata: vrt provide string", meta["abi"]["vrt_provide"] == "vinyld-vrt = 23.0")
    check("metadata: source archive name", meta["source_archive"] == "libvmod-cachetag-1.0.0.tar.gz")
    check("metadata: origin identifies the cohort", meta["origin"]["cohort"] == VALID_COHORT_ID)

    rpm_target = (
        VALID_TARGET.replace("id: debian-13-amd64", "id: el9-x86_64")
        .replace("distro: debian", "distro: el")
        .replace("distro_release: 13", "distro_release: 9")
        .replace("distro_id: debian-13", "distro_id: el9")
        .replace("arch: amd64", "arch: x86_64")
        .replace("package_format: deb", "package_format: rpm")
        .replace('dist_tag: ""', "dist_tag: el9")
    )
    meta = metadata_mod.target_metadata(yaml_subset.parse(rpm_target), cohort)
    check(
        "metadata: rpm native filename",
        meta["artifacts"]["native_filename"] == "libvmod-cachetag-1.0.0-2.el9.x86_64.rpm",
        meta["artifacts"]["native_filename"],
    )
    check(
        "metadata: srpm filename",
        meta["artifacts"]["source_package_filenames"] == ["libvmod-cachetag-1.0.0-2.el9.src.rpm"],
        str(meta["artifacts"]["source_package_filenames"]),
    )
    check(
        "metadata: rpm requires the exact strict abi",
        "vinyld-abi-a90954814766d933a75d4c808c449cb9bc0ae3d3" in meta["abi"]["rpm_requires"],
    )
    shell = metadata_mod.as_shell(meta)
    check(
        "metadata: shell rendering exports the native filename",
        "CACHETAG_ARTIFACTS_NATIVE_FILENAME='libvmod-cachetag-1.0.0-2.el9.x86_64.rpm'" in shell,
        shell,
    )
    check(
        "metadata: shell rendering keeps list entries separate and unmangled",
        "CACHETAG_ABI_RPM_REQUIRES_COUNT='2'" in shell
        and "CACHETAG_ABI_RPM_REQUIRES_1='vinyld-vrt = 23.0'" in shell,
        shell,
    )
    check(
        "metadata: shell rendering does not double the CACHETAG_ prefix",
        "CACHETAG_VERSION='1.0.0'" in shell and "CACHETAG_CACHETAG_VERSION" not in shell,
        shell,
    )


def test_distro_native(repo_root: Path) -> None:
    path = repo_root / "registry" / "distro-native" / "debian-13-amd64.yml"
    data = manifest.load_target(path)
    errors = manifest.validate_target(data, str(path), distro_native=True, expected_version="1.0.0")
    check("distro-native: template is schema-valid", errors == [], "; ".join(errors))
    errors = manifest.validate_target(
        data, str(path), distro_native=True, expected_version="1.0.0", require_releasable=True
    )
    check(
        "distro-native: template is refused for release use",
        any("never releasable" in e for e in errors),
        "; ".join(errors),
    )
    filled = manifest.load_target(path)
    filled["distro_vinyl"]["exposes_abi_provide"] = "no"
    filled["distro_vinyl"]["binary_package_version"] = "9.0.0-3"
    filled["distro_vinyl"]["strict_abi"] = "a90954814766d933a75d4c808c449cb9bc0ae3d3"
    meta = metadata_mod.target_metadata(filled)
    check(
        "distro-native: falls back to an exact package dependency when no abi provide exists",
        "vinyl-cache (= 9.0.0-3)" in meta["abi"]["deb_depends"],
        meta["abi"]["deb_depends"],
    )


def test_cachetag_src() -> None:
    """The configure.ac cross-check now spans two repositories."""
    with tempfile.TemporaryDirectory() as tmp:
        registry_root, cachetag_src = _write_workspace(Path(tmp), VALID_COHORT, VALID_TARGET)

        check(
            "cachetag-src: an explicit checkout path supplies the version",
            manifest.configure_ac_version(cachetag_src) == "1.0.0",
        )

        # No explicit path: the sibling next to the registry checkout is used.
        saved = os.environ.pop(manifest.CACHETAG_SRC_ENV, None)
        try:
            check(
                "cachetag-src: defaults to the sibling libvmod-cachetag checkout",
                manifest.default_cachetag_src(registry_root) == cachetag_src,
                str(manifest.default_cachetag_src(registry_root)),
            )
            checked, errors = manifest.validate_registry_tree(repo_root=registry_root)
            check(
                "cachetag-src: a registry validates against its sibling checkout with no explicit path",
                errors == [] and len(checked) == 2,
                "; ".join(errors) or str(checked),
            )

            # The environment variable wins over the sibling default.
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            os.environ[manifest.CACHETAG_SRC_ENV] = str(elsewhere)
            check(
                "cachetag-src: CACHETAG_SRC overrides the sibling default",
                manifest.default_cachetag_src(registry_root) == elsewhere,
                str(manifest.default_cachetag_src(registry_root)),
            )
        finally:
            os.environ.pop(manifest.CACHETAG_SRC_ENV, None)
            if saved is not None:
                os.environ[manifest.CACHETAG_SRC_ENV] = saved

        # A missing checkout must say how to fix it, not raise FileNotFoundError.
        try:
            manifest.configure_ac_version(Path(tmp) / "no-such-checkout")
            check("cachetag-src: a missing checkout is an actionable error", False, "no error raised")
        except manifest.ValidationError as exc:
            check(
                "cachetag-src: a missing checkout is an actionable error",
                "--cachetag-src" in str(exc) and manifest.CACHETAG_SRC_ENV in str(exc),
                str(exc),
            )

        # A configure.ac from some other project must not be read as cachetag's.
        foreign = Path(tmp) / "foreign"
        foreign.mkdir()
        (foreign / "configure.ac").write_text(
            "AC_INIT([libvmod-something-else], [9.9.9])\n", encoding="utf-8"
        )
        try:
            manifest.configure_ac_version(foreign)
            check("cachetag-src: a foreign configure.ac is rejected", False, "no error raised")
        except manifest.ValidationError as exc:
            check(
                "cachetag-src: a foreign configure.ac is rejected",
                "does not look like" in str(exc),
                str(exc),
            )


def main(repo_root: Path = None, cachetag_src=None) -> int:
    root = Path(repo_root) if repo_root else manifest.REPO_ROOT
    src = Path(cachetag_src) if cachetag_src else manifest.default_cachetag_src(root)
    _RESULTS.clear()
    test_parser()
    test_digest()
    test_valid_manifest_passes()
    test_version_mismatch_fails()
    test_bad_cohort_id_fails()
    test_schema_failures()
    test_metadata()
    test_vmoddir()
    test_cachetag_src()
    test_repo_templates(root, src)
    test_distro_native(root)

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
