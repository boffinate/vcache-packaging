#!/usr/bin/env python3
"""Tests for tools/vmod_recipe.py.

Run as ``python3 tools/vmod_recipe.py selftest``. ``ci_matrix.py selftest``
also runs them, which is how they reach the CI structural-validation job
without that job having to learn a third command.

The eight generator-contract requirements from
docs/20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md map onto
tests here as follows:

  1. loads manifest, adapter, engine and target metadata  -> test_dict_*
  2. rejects a missing mandatory input                    -> test_missing_*
  3. renders deterministically                            -> test_determinism
  4. dates from recorded epochs, never wall-clock         -> test_dates_*
  5. refuses unresolved template tokens                   -> test_tokens_*
  6. machine-readable record of every input/output digest -> test_generation_record
  7. expected binary and source package names             -> test_expected_names
  8. never builds                                         -> test_generator_never_builds

Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_matrix  # noqa: E402
import manifest as manifest_mod  # noqa: E402
import metadata as metadata_mod  # noqa: E402
import vmod_recipe as vr  # noqa: E402

_RESULTS: list = []

DICT_MANIFEST = "registry/vmods/dict.yml"
DICT_OVERLAY = "recipes/vmods/overlays/dict/overlay.yml"
RELEASE_COHORT = "vinyl-9.0.1-ac4f719c16f4"
MAINTAINER = "Boffinate <noreply@boffinate.com>"


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))


def _clone(value):
    return json.loads(json.dumps(value))


def _inputs(root: Path) -> dict:
    """The real dict inputs, loaded from the checked-in files."""
    recipe_root = root / vr.RECIPE_ROOT
    overlay = vr.load_overlay(root / DICT_OVERLAY)
    return {
        "vmod_manifest": vr.load_vmod_manifest(root / DICT_MANIFEST, "dict"),
        "overlay": overlay,
        "adapter": vr.load_adapter(recipe_root / "adapters" / overlay["adapter"] / "adapter.yml"),
        "cohort": manifest_mod.load_cohort(
            root / "registry" / "cohorts" / f"{RELEASE_COHORT}.yml"
        ),
        "target": manifest_mod.load_target(
            root / "registry" / "targets" / RELEASE_COHORT / "debian-13-amd64.yml"
        ),
        "maintainer": MAINTAINER,
        "debian_distribution": "trixie",
    }


def _model(root: Path, **overrides):
    kwargs = _inputs(root)
    kwargs.update(overrides)
    return vr.build_model(**kwargs)


def _expect_error(name: str, fn, fragment: str) -> None:
    try:
        fn()
    except vr.GeneratorError as exc:
        check(name, fragment.lower() in str(exc).lower(), f"message was: {exc}")
    except Exception as exc:  # noqa: BLE001 - a wrong exception type is the finding
        check(name, False, f"raised {type(exc).__name__}: {exc}")
    else:
        check(name, False, "no error raised")


# ---------------------------------------------------------------------------
# Contract 1: the real inputs load and model correctly
# ---------------------------------------------------------------------------


def test_dict_manifest_is_catalog_valid(root: Path) -> None:
    """The generator and the catalog must agree about dict's manifest.

    ci_matrix.py owns the schema and validates it in CI, but the generator is
    the other consumer of the same file, and a change satisfying one and not
    the other would otherwise be found late.
    """
    path = root / DICT_MANIFEST
    data = ci_matrix.load_vmod_manifest(path)
    errors = ci_matrix.validate_vmod_manifest(data, str(path), "dict")
    check("dict.yml validates as vmod-ci/v1", not errors, "; ".join(errors))
    check(
        "dict.yml names the autotools adapter",
        data["adapter"] == "autotools",
        data["adapter"],
    )
    check(
        "dict.yml pins a two-component version",
        data["sources"]["release"]["version"] == "1.7",
        str(data["sources"]["release"]),
    )


def test_dict_deb_model(root: Path) -> None:
    model = _model(root)
    check("model schema", model["schema"] == vr.MODEL_SCHEMA, model["schema"])
    check("model: vmod id", model["vmod"]["id"] == "dict")
    check(
        "model: adapter revision is recorded",
        model["vmod"]["adapter_revision"] == "1",
        model["vmod"]["adapter_revision"],
    )
    check(
        "model: source identity is the manifest's, not the overlay's",
        model["source"]["commit"] == "784584d272894a39cf995377618aad551a196424"
        and model["source"]["ref"] == "v1.7"
        and model["source"]["version"] == "1.7",
        str(model["source"]),
    )
    check(
        "model: engine row comes from the cohort and target manifests",
        model["engine"]["strict_abi"] == "423648c4cb6b225b3268ffc337354ea938f5efee"
        and model["engine"]["vrt"] == "23.0"
        and model["engine"]["cohort"] == RELEASE_COHORT
        and model["engine"]["vmoddir"] == "/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods",
        str(model["engine"]),
    )
    check(
        "model: the engine dev package is pinned to the exact cohort version",
        "vinyl-cache-dev (= 9.0.1-1)" in model["build"]["dependencies"],
        str(model["build"]["dependencies"]),
    )
    check(
        "model: adapter dependencies come first, overlay dependencies are added",
        model["build"]["dependencies"][:2] == ["debhelper-compat (= 13)", "pkgconf"]
        and "python3-docutils" in model["build"]["dependencies"],
        str(model["build"]["dependencies"]),
    )
    check(
        "model: the licence is GPL-3.0-or-later, verified from upstream COPYING",
        model["license"]["expression"] == "GPL-3.0-or-later"
        and model["license"]["debian_short_name"] == "GPL-3+",
        str(model["license"]),
    )


def test_abi_expressions_are_not_duplicated(root: Path) -> None:
    """The generator must not carry its own copy of the ABI dependency policy."""
    model = _model(root)
    cohort = _inputs(root)["cohort"]
    expected = metadata_mod.abi_expressions(
        vrt=cohort["vinyl"]["vrt"],
        strict_abi=cohort["vinyl"]["strict_abi"],
        cohort_id=cohort["cohort"],
    )
    check(
        "abi: every expression equals metadata.abi_expressions",
        all(model["abi"][key] == value for key, value in expected.items()),
        str({k: (model["abi"].get(k), v) for k, v in expected.items() if model["abi"].get(k) != v}),
    )
    source = (Path(__file__).resolve().parent / "vmod_recipe.py").read_text(encoding="utf-8")
    check(
        "abi: vmod_recipe.py contains no hand-written vinyld dependency string",
        "vinyld-abi-" not in source and "vinyld(abi)" not in source,
        "a literal vinyld expression appears in the generator",
    )


def test_dict_deb_render(root: Path) -> None:
    model = _model(root)
    recipe_root = root / vr.RECIPE_ROOT
    outputs = vr.render(model, recipe_root / "templates", recipe_root / "licenses")
    check(
        "deb render: the expected file set",
        sorted(outputs) == [
            "debian/changelog",
            "debian/control",
            "debian/copyright",
            "debian/rules",
            "debian/source/format",
            "debian/source/lintian-overrides",
            "debian/vmod-dict.docs",
            "debian/vmod-dict.lintian-overrides",
        ],
        str(sorted(outputs)),
    )
    control = outputs["debian/control"]
    for fragment in (
        "Source: vmod-dict",
        "Maintainer: Boffinate <noreply@boffinate.com>",
        "vinyl-cache-dev (= 9.0.1-1),",
        "vinyld-abi-423648c4cb6b225b3268ffc337354ea938f5efee,",
        "vinyld-vrt (= 23.0),",
        f"vinyld-cohort-{RELEASE_COHORT},",
        "Description: dictionary look-up VMOD for Vinyl Cache",
    ):
        check(f"deb control contains {fragment!r}", fragment in control, control)
    check(
        "deb control: blank description lines become '.'",
        "\n .\n" in control,
        control,
    )
    rules = outputs["debian/rules"]
    check(
        "deb rules: bootstrap none means dh --without autoreconf",
        "dh $@ --without autoreconf" in rules,
        rules,
    )
    check("deb rules: hardening is enabled explicitly", "hardening=+all" in rules, rules)
    check(
        "deb rules: refuses a wrong vmoddir",
        "/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods" in rules
        and "Refusing to build a VMOD" in rules,
        rules,
    )
    check(
        "deb rules: asserts the VMOD object was staged",
        "libvmod_dict.so" in rules and "was not staged in" in rules,
        rules,
    )
    check(
        "deb copyright: carries a machine-readable licence, not a pointer",
        "License: GPL-3+" in outputs["debian/copyright"]
        and "common-licenses/GPL-3" in outputs["debian/copyright"]
        and "See original" not in outputs["debian/copyright"],
        outputs["debian/copyright"],
    )
    check(
        "deb changelog: the source epoch, not a clock",
        "Wed, 25 Mar 2026 09:04:22 +0000" in outputs["debian/changelog"],
        outputs["debian/changelog"],
    )


def test_dict_rpm_render(root: Path) -> None:
    target = manifest_mod.load_target(
        root / "registry" / "targets" / RELEASE_COHORT / "el9-x86_64.yml"
    )
    model = _model(root, target=target, debian_distribution=None)
    recipe_root = root / vr.RECIPE_ROOT
    outputs = vr.render(model, recipe_root / "templates", recipe_root / "licenses")
    check("rpm render: one spec", sorted(outputs) == ["vmod-dict.spec"], str(sorted(outputs)))
    spec = outputs["vmod-dict.spec"]
    for fragment in (
        "Name:           vmod-dict",
        "Version:        1.7",
        "Release:        1%{?dist}",
        "License:        GPL-3.0-or-later",
        "BuildRequires:  vinyl-cache-devel = 9.0.1-1.el9",
        "Requires:       vinyld(abi)%{?_isa} = 423648c4cb6b225b3268ffc337354ea938f5efee",
        "Requires:       vinyld(vrt)%{?_isa} = 23.0",
        f"Requires:       vinyld(cohort-{RELEASE_COHORT})%{{?_isa}}",
        "%autosetup -n vmod-dict-1.7",
        "%{vinyl_vmoddir}/libvmod_dict.so",
        "%{_mandir}/man3/vmod_dict.3*",
        "%license COPYING",
        "* Wed Mar 25 2026 Boffinate <noreply@boffinate.com> - 1.7-1",
    ):
        check(f"rpm spec contains {fragment!r}", fragment in spec, spec)
    check(
        "rpm spec: debug packages are not disabled",
        "debug_package %{nil}" not in spec,
        spec,
    )
    check(
        "rpm spec: parallel_build no becomes make -j1",
        "%make_build -j1" in spec,
        spec,
    )
    check(
        "rpm spec: no %check when build-time tests are none",
        "\n%check\n" not in spec,
        spec,
    )
    check(
        "rpm spec: the payload is explicit, not a glob",
        "/vmods/*" not in spec and "*.so" not in spec.split("%files")[1],
        spec,
    )


# ---------------------------------------------------------------------------
# Contract 3: determinism
# ---------------------------------------------------------------------------


def test_determinism(root: Path) -> None:
    """Same inputs, byte-identical outputs -- including the generation record."""
    runs = []
    for _ in range(2):
        model, outputs, record = vr.generate(
            manifest_path=root / DICT_MANIFEST,
            overlay_path=root / DICT_OVERLAY,
            cohort_id=RELEASE_COHORT,
            target_id="debian-13-amd64",
            maintainer=MAINTAINER,
            debian_distribution="trixie",
            repo_root=root,
        )
        runs.append((outputs, vr.dumps_record(record), record["recipe_sha256"]))
    check(
        "determinism: rendered trees are byte-identical",
        runs[0][0] == runs[1][0],
        "outputs differ between two renders of identical inputs",
    )
    check(
        "determinism: generation records are byte-identical",
        runs[0][1] == runs[1][1],
        "generation records differ between two renders of identical inputs",
    )
    check("determinism: recipe digest is stable", runs[0][2] == runs[1][2], runs[0][2])

    # And through the filesystem, which is where a stray mode or ordering
    # difference would show up.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        for out in (a, b):
            model, outputs, record = vr.generate(
                manifest_path=root / DICT_MANIFEST,
                overlay_path=root / DICT_OVERLAY,
                cohort_id=RELEASE_COHORT,
                target_id="el9-x86_64",
                maintainer=MAINTAINER,
                repo_root=root,
            )
            vr.write_outputs(out, outputs, record)
        files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
        files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
        same = files_a == files_b and all(
            (a / p).read_bytes() == (b / p).read_bytes() for p in files_a
        )
        check("determinism: written trees are byte-identical", same, str(files_a))


# ---------------------------------------------------------------------------
# Contract 4: dates from recorded epochs
# ---------------------------------------------------------------------------


def test_dates_come_from_the_recorded_epoch(root: Path) -> None:
    check(
        "dates: debian date is the recorded epoch in UTC",
        vr.debian_date("1774429462") == "Wed, 25 Mar 2026 09:04:22 +0000",
        vr.debian_date("1774429462"),
    )
    check(
        "dates: rpm changelog date is the recorded epoch in UTC",
        vr.rpm_changelog_date("1774429462") == "Wed Mar 25 2026",
        vr.rpm_changelog_date("1774429462"),
    )
    inputs = _inputs(root)
    overlay = _clone(inputs["overlay"])
    overlay["source"]["archive"]["source_date_epoch"] = "0"
    model = _model(root, overlay=overlay)
    values = vr.token_values(model, root / vr.RECIPE_ROOT / "licenses")
    check(
        "dates: a different recorded epoch produces a different date",
        values["DEBIAN_DATE"] == "Thu, 01 Jan 1970 00:00:00 +0000",
        values["DEBIAN_DATE"],
    )


def test_dates_are_locale_independent() -> None:
    """strftime's %a and %b follow LC_TIME; the recipe bytes must not.

    Under fr_FR.UTF-8 the same epoch renders "mer." rather than "Wed", which
    would make a package's changelog depend on the environment the generator
    happened to run in. Debian and RPM both require the English abbreviations,
    so there is nothing to localise even in principle.
    """
    source = (Path(__file__).resolve().parent / "vmod_recipe.py").read_text(encoding="utf-8")
    # Calls, not the word: the comment above the date tables says "strftime"
    # and should keep saying it.
    code = "\n".join(
        line for line in source.split("\n") if not line.lstrip().startswith("#")
    )
    check(
        "dates: the generator never calls strftime",
        "strftime(" not in code,
        "a strftime call appears in vmod_recipe.py; %a/%b are LC_TIME-sensitive",
    )
    check(
        "dates: setlocale is not called either (it mutates global state)",
        "setlocale(" not in code,
        "a setlocale call appears in vmod_recipe.py",
    )
    # One epoch per weekday and one per month, so every table entry is
    # exercised rather than only the one dict happens to land on.
    weekdays = {vr.debian_date(str(345600 + 86400 * i)).split(",")[0] for i in range(7)}
    check(
        "dates: every weekday abbreviation is the English one",
        weekdays == {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"},
        str(sorted(weekdays)),
    )
    months = {vr.debian_date(str(e)).split()[2] for e in _MONTH_EPOCHS}
    check(
        "dates: every month abbreviation is the English one",
        months
        == {
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        },
        str(sorted(months)),
    )
    check(
        "dates: the RPM changelog date uses the same English tables",
        vr.rpm_changelog_date("1774429462") == "Wed Mar 25 2026"
        and vr.rpm_changelog_date("0") == "Thu Jan 01 1970",
        vr.rpm_changelog_date("0"),
    )
    check(
        "dates: single-digit days are zero-padded on both backends",
        vr.debian_date("1767225600").startswith("Thu, 01 Jan 2026")
        and vr.rpm_changelog_date("1767225600") == "Thu Jan 01 2026",
        vr.debian_date("1767225600") + " / " + vr.rpm_changelog_date("1767225600"),
    )


# 2026-<month>-15T00:00:00Z for each month, so all twelve names are covered.
_MONTH_EPOCHS = (
    1768435200, 1771113600, 1773532800, 1776211200, 1778803200, 1781481600,
    1784073600, 1786752000, 1789430400, 1792022400, 1794700800, 1797292800,
)


def test_inspection_commands_do_not_need_a_maintainer(root: Path) -> None:
    """`names` and `model` answer a question a maintainer is irrelevant to."""
    model, _recipe_root, _paths = vr.build(
        manifest_path=root / DICT_MANIFEST,
        overlay_path=root / DICT_OVERLAY,
        cohort_id=RELEASE_COHORT,
        target_id="debian-13-amd64",
        maintainer="",
        debian_distribution="UNSET",
        repo_root=root,
        require_maintainer=False,
    )
    check(
        "inspect: names resolve with no maintainer",
        model["artifacts"]["native_filenames"] == ["vmod-dict_1.7-1_amd64.deb"],
        json.dumps(model["artifacts"]),
    )
    check(
        "inspect: the model records the maintainer as absent, not invented",
        model["maintainer"] == {"name": "", "email": ""},
        json.dumps(model["maintainer"]),
    )
    _expect_error(
        "inspect: generate still refuses without a maintainer",
        lambda: vr.generate(
            manifest_path=root / DICT_MANIFEST,
            overlay_path=root / DICT_OVERLAY,
            cohort_id=RELEASE_COHORT,
            target_id="debian-13-amd64",
            maintainer="",
            debian_distribution="trixie",
            repo_root=root,
        ),
        "maintainer",
    )


def test_generator_never_builds(root: Path) -> None:
    """Contract 8: rendering text must not shell out, install or read a clock."""
    source = (Path(__file__).resolve().parent / "vmod_recipe.py").read_text(encoding="utf-8")
    for forbidden in ("import subprocess", "os.system", "shutil.which", "urllib", "socket"):
        check(
            f"never builds: {forbidden!r} does not appear in the generator",
            forbidden not in source,
            f"{forbidden} found in vmod_recipe.py",
        )
    for clock in ("time.time(", "time.localtime(", "datetime.now", "date.today"):
        check(
            f"never builds: no wall-clock call {clock!r}",
            clock not in source,
            f"{clock} found in vmod_recipe.py",
        )


# ---------------------------------------------------------------------------
# Contract 5: unresolved tokens
# ---------------------------------------------------------------------------


def test_tokens_undeclared_is_refused() -> None:
    _expect_error(
        "tokens: an undeclared token in a template is refused",
        lambda: vr.substitute("Source: @NO_SUCH_TOKEN@\n", {"SOURCE_NAME": "x"}, "fixture"),
        "undeclared token",
    )


def test_tokens_surviving_substitution_is_refused() -> None:
    _expect_error(
        "tokens: a token that survives substitution is refused",
        lambda: vr.substitute("A: @A@\n", {"A": "@LEFTOVER@"}, "fixture"),
        "unresolved template token",
    )


def test_tokens_single_pass() -> None:
    out = vr.substitute("@A@-@B@", {"A": "one", "B": "two"}, "fixture")
    check("tokens: ordinary substitution", out == "one-two", out)


def test_tokens_missing_template_is_refused(root: Path) -> None:
    model = _model(root)
    with tempfile.TemporaryDirectory() as tmp:
        _expect_error(
            "tokens: a missing template file is refused",
            lambda: vr.render(model, Path(tmp), root / vr.RECIPE_ROOT / "licenses"),
            "missing template",
        )


# ---------------------------------------------------------------------------
# Contract 2: every mandatory input is required
# ---------------------------------------------------------------------------


def test_missing_maintainer(root: Path) -> None:
    _expect_error(
        "reject: no maintainer",
        lambda: _model(root, maintainer=""),
        "maintainer",
    )
    _expect_error(
        "reject: placeholder maintainer email",
        lambda: _model(root, maintainer="Nobody <example@localhost>"),
        "placeholder",
    )
    _expect_error(
        "reject: malformed maintainer",
        lambda: _model(root, maintainer="Boffinate"),
        "name <email>",
    )


def test_missing_source_identity(root: Path) -> None:
    for field in ("expected_commit", "version", "archive_sha256"):
        vmod_manifest = _clone(_inputs(root)["vmod_manifest"])
        vmod_manifest["sources"]["release"][field] = ""
        _expect_error(
            f"reject: source identity without {field}",
            lambda m=vmod_manifest: _model(root, vmod_manifest=m),
            "missing source identity",
        )
    vmod_manifest = _clone(_inputs(root)["vmod_manifest"])
    vmod_manifest["sources"]["release"]["publishable"] = "false"
    _expect_error(
        "reject: a non-publishable channel",
        lambda: _model(root, vmod_manifest=vmod_manifest),
        "publishable",
    )


def test_missing_license(root: Path) -> None:
    overlay = _clone(_inputs(root)["overlay"])
    overlay["license"]["debian_short_name"] = "NoSuchLicence"
    model = _model(root, overlay=overlay)
    _expect_error(
        "reject: a licence with no reviewed Debian stanza",
        lambda: vr.render(
            model,
            root / vr.RECIPE_ROOT / "templates",
            root / vr.RECIPE_ROOT / "licenses",
        ),
        "no reviewed debian licence stanza",
    )
    overlay = _clone(_inputs(root)["overlay"])
    del overlay["license"]
    _expect_error(
        "reject: an overlay with no licence block",
        lambda: vr.load_overlay(_write_overlay(overlay)),
        "license: missing required field",
    )


def test_missing_description(root: Path) -> None:
    overlay = _clone(_inputs(root)["overlay"])
    overlay["package"]["summary"] = "   "
    _expect_error(
        "reject: an empty package summary",
        lambda: _model(root, overlay=overlay),
        "summary is empty",
    )
    overlay2 = _clone(_inputs(root)["overlay"])
    overlay2["package"]["description"] = ["", "  "]
    _expect_error(
        "reject: an empty package description",
        lambda: _model(root, overlay=overlay2),
        "description is empty",
    )


def test_missing_payload_policy(root: Path) -> None:
    overlay = _clone(_inputs(root)["overlay"])
    del overlay["payload"]
    _expect_error(
        "reject: an overlay with no payload policy",
        lambda: vr.load_overlay(_write_overlay(overlay)),
        "payload: missing required field",
    )
    overlay2 = _clone(_inputs(root)["overlay"])
    overlay2["payload"]["man_pages"] = []
    _expect_error(
        "reject: an overlay declaring no manual page",
        lambda: vr.load_overlay(_write_overlay(overlay2)),
        "needs at least 1",
    )


def test_missing_adapter_revision(root: Path) -> None:
    adapter = _clone(_inputs(root)["adapter"])
    del adapter["revision"]
    _expect_error(
        "reject: an adapter with no revision",
        lambda: vr.load_adapter(_write_yaml(adapter)),
        "revision: missing required field",
    )
    overlay = _clone(_inputs(root)["overlay"])
    del overlay["revision"]
    _expect_error(
        "reject: an overlay with no revision",
        lambda: vr.load_overlay(_write_overlay(overlay)),
        "revision: missing required field",
    )


def test_missing_abi_input(root: Path) -> None:
    overlay = _clone(_inputs(root)["overlay"])
    del overlay["abi"]
    _expect_error(
        "reject: an overlay with no declared ABI mode",
        lambda: vr.load_overlay(_write_overlay(overlay)),
        "abi: missing required field",
    )
    cohort = _clone(_inputs(root)["cohort"])
    del cohort["vinyl"]["strict_abi"]
    _expect_error(
        "reject: a cohort with no strict ABI",
        lambda: _model(root, cohort=cohort),
        "strict_abi",
    )


def test_missing_unknown_adapter(root: Path) -> None:
    overlay = _clone(_inputs(root)["overlay"])
    overlay["adapter"] = "cargo"
    vmod_manifest = _clone(_inputs(root)["vmod_manifest"])
    vmod_manifest["adapter"] = "cargo"
    _expect_error(
        "reject: an adapter file that does not match the overlay",
        lambda: _model(root, overlay=overlay, vmod_manifest=vmod_manifest),
        "adapter file declares",
    )


def test_missing_debian_distribution(root: Path) -> None:
    _expect_error(
        "reject: a deb target with no changelog suite",
        lambda: _model(root, debian_distribution=None),
        "debian-distribution",
    )


def test_mismatched_ids(root: Path) -> None:
    overlay = _clone(_inputs(root)["overlay"])
    overlay["id"] = "other"
    _expect_error(
        "reject: an overlay for a different VMOD",
        lambda: _model(root, overlay=overlay),
        "does not match manifest id",
    )
    target = _clone(_inputs(root)["target"])
    target["cohort"] = "vinyl-9.0.0-000000000000"
    _expect_error(
        "reject: a target from a different cohort",
        lambda: _model(root, target=target),
        "belongs to cohort",
    )


# ---------------------------------------------------------------------------
# Contract 6: the generation record
# ---------------------------------------------------------------------------


def test_generation_record(root: Path) -> None:
    model, outputs, record = vr.generate(
        manifest_path=root / DICT_MANIFEST,
        overlay_path=root / DICT_OVERLAY,
        cohort_id=RELEASE_COHORT,
        target_id="debian-13-amd64",
        maintainer=MAINTAINER,
        debian_distribution="trixie",
        repo_root=root,
    )
    check("record: schema", record["schema"] == vr.RECORD_SCHEMA, record["schema"])
    labels = set(record["inputs"])
    for required in ("manifest", "overlay", "adapter", "cohort", "target", "generator"):
        check(f"record: {required} is a digested input", required in labels, str(sorted(labels)))
    check(
        "record: every template that fed the render is digested",
        sum(1 for label in labels if label.startswith("template:")) == 8,
        str(sorted(labels)),
    )
    check(
        "record: the licence stanza is digested",
        "license:GPL-3+" in labels,
        str(sorted(labels)),
    )
    check(
        "record: every input digest is 64 hex characters",
        all(re.fullmatch(r"[0-9a-f]{64}", v["sha256"]) for v in record["inputs"].values()),
        str(record["inputs"]),
    )
    check(
        "record: every rendered file is digested",
        set(record["outputs"]) == set(outputs),
        str(sorted(record["outputs"])),
    )
    check(
        "record: the recipe digest covers the whole tree",
        record["recipe_sha256"] == vr._tree_digest(outputs),
        record["recipe_sha256"],
    )
    check(
        "record: changing one rendered byte changes the tree digest",
        vr._tree_digest({**outputs, "debian/control": outputs["debian/control"] + "x"})
        != record["recipe_sha256"],
    )
    check(
        "record: carries the maintainer, licence, source and ABI it rendered",
        record["maintainer"] == MAINTAINER
        and record["license"]["expression"] == "GPL-3.0-or-later"
        and record["source"]["archive_sha256"]
        == "eb2a86a780ba9628106dbe858d17ec4589ad6dcb70c6ad53decb5d32824e098c"
        and record["abi"]["mode"] == "strict",
        json.dumps(record["source"]),
    )
    check(
        "record: serialises to sorted, stable JSON",
        vr.dumps_record(record) == vr.dumps_record(json.loads(vr.dumps_record(record))),
    )


# ---------------------------------------------------------------------------
# Contract 7: expected package names, without a build
# ---------------------------------------------------------------------------


def test_expected_names(root: Path) -> None:
    deb = _model(root)["artifacts"]
    check(
        "names: Debian binary and source packages",
        deb["source_package_name"] == "vmod-dict"
        and deb["binary_package_names"] == ["vmod-dict", "vmod-dict-dbgsym"]
        and deb["native_filenames"] == ["vmod-dict_1.7-1_amd64.deb"],
        json.dumps(deb),
    )
    check(
        "names: Debian source package files",
        deb["source_package_filenames"]
        == [
            "vmod-dict_1.7.orig.tar.gz",
            "vmod-dict_1.7-1.debian.tar.xz",
            "vmod-dict_1.7-1.dsc",
        ],
        json.dumps(deb["source_package_filenames"]),
    )
    check(
        "names: the release asset carries distro and arch",
        deb["release_asset_filenames"] == ["vmod-dict-1.7-1-debian-13-amd64.deb"],
        json.dumps(deb["release_asset_filenames"]),
    )
    target = manifest_mod.load_target(
        root / "registry" / "targets" / RELEASE_COHORT / "el9-x86_64.yml"
    )
    rpm = _model(root, target=target, debian_distribution=None)["artifacts"]
    check(
        "names: RPM binary, debuginfo and source packages",
        rpm["binary_package_names"]
        == ["vmod-dict", "vmod-dict-debuginfo", "vmod-dict-debugsource"]
        and rpm["native_filenames"] == ["vmod-dict-1.7-1.el9.x86_64.rpm"]
        and rpm["source_package_filenames"] == ["vmod-dict-1.7-1.el9.src.rpm"],
        json.dumps(rpm),
    )
    check(
        "names: the upstream archive name is derived, not hand-written",
        rpm["upstream_archive"] == "vmod-dict-1.7.tar.gz",
        rpm["upstream_archive"],
    )


# ---------------------------------------------------------------------------
# A fixture render against a golden output
# ---------------------------------------------------------------------------

_FIXTURE_GOLDEN = """\
Source: vmod-fixture
Section: web
Priority: optional
Maintainer: Fixture Maintainer <fixture@example.test>
Build-Depends:
 debhelper-compat (= 13),
 vinyl-cache-dev (= 9.9.9-1),
Standards-Version: 4.7.2
Homepage: https://fixture.invalid/vmod

Package: vmod-fixture
Depends:
 ${shlibs:Depends},
 vinyld-abi-1111111111111111111111111111111111111111,
 vinyld-vrt (= 99.9),
 vinyld-cohort-vinyl-9.9.9-111111111111,
Description: a fixture VMOD
 First line.
 .
 Third line.
"""


def _fixture_model() -> dict:
    abi = metadata_mod.abi_expressions(
        vrt="99.9", strict_abi="1" * 40, cohort_id="vinyl-9.9.9-111111111111"
    )
    return {
        "schema": vr.MODEL_SCHEMA,
        "vmod": {
            "id": "fixture",
            "adapter": "autotools",
            "adapter_revision": "1",
            "overlay_revision": "1",
        },
        "maintainer": {"name": "Fixture Maintainer", "email": "fixture@example.test"},
        "upstream": {
            "name": "vmod-fixture",
            "contact": "Nobody <nobody@fixture.invalid>",
            "homepage": "https://fixture.invalid/vmod",
            "vcs_git": "https://fixture.invalid/vmod.git",
            "vcs_browser": "https://fixture.invalid/vmod",
        },
        "source": {
            "channel": "release",
            "ref": "v9.9",
            "commit": "2" * 40,
            "version": "9.9",
            "archive_sha256": "3" * 64,
            "archive_name": "vmod-fixture-9.9.tar.gz",
            "archive_url": "https://fixture.invalid/vmod-fixture-9.9.tar.gz",
            "archive_method": "upstream-release",
            "archive_bytes": "1",
            "directory": "vmod-fixture-9.9",
            "source_date_epoch": "1774429462",
            "clone_url": "https://fixture.invalid/vmod.git",
            "submodules": [],
        },
        "package": {
            "debian_source_name": "vmod-fixture",
            "debian_binary_name": "vmod-fixture",
            "rpm_name": "vmod-fixture",
            "debian_section": "web",
            "revision": 1,
            "summary": "a fixture VMOD",
            "description": ["First line.", "", "Third line."],
            "versions": metadata_mod.package_versions("9.9", 1, ""),
        },
        "license": {
            "expression": "GPL-3.0-or-later",
            "debian_short_name": "GPL-3+",
            "files": ["COPYING"],
        },
        "copyright": {
            "files": [{"pattern": "*", "holder": "2026 Nobody", "license": "GPL-3+"}],
            "packaging": "2026 Nobody",
        },
        "abi": {"mode": "strict", **abi},
        "engine": {
            "cohort": "vinyl-9.9.9-111111111111",
            "vinyl_version": "9.9.9",
            "vrt": "99.9",
            "strict_abi": "1" * 40,
            "runtime_package": "vinyl-cache",
            "runtime_version": "9.9.9-1",
            "dev_package": "vinyl-cache-dev",
            "dev_version": "9.9.9-1",
            "vmoddir": "/usr/lib/fixture/vmods",
        },
        "target": {
            "id": "debian-13-amd64",
            "distro": "debian",
            "distro_id": "debian-13",
            "distro_release": "13",
            "arch": "amd64",
            "package_format": "deb",
            "dist_tag": "",
            "debian_distribution": "trixie",
        },
        "build": {
            "bootstrap": "none",
            "configure_args": [],
            "build_time_tests": "none",
            "parallel_build": "yes",
            "dependencies": ["debhelper-compat (= 13)", "vinyl-cache-dev (= 9.9.9-1)"],
        },
        "payload": {
            "vmod_object": "libvmod_fixture.so",
            "man_pages": ["man3/vmod_fixture.3"],
            "doc_files": [],
            "license_files": ["COPYING"],
        },
        "lintian_overrides": {"source": [], "binary": []},
        "artifacts": {},
    }


def test_fixture_render_matches_the_golden(root: Path) -> None:
    """A tiny template rendered from a tiny model, compared byte for byte.

    Deliberately not the real templates: this test is about the renderer, and it
    must not have to change every time a comment in a production template does.
    """
    model = _fixture_model()
    template = (
        "Source: @SOURCE_NAME@\n"
        "Section: @DEBIAN_SECTION@\n"
        "Priority: optional\n"
        "Maintainer: @MAINTAINER_NAME@ <@MAINTAINER_EMAIL@>\n"
        "Build-Depends:\n"
        "@DEB_BUILD_DEPENDS@\n"
        "Standards-Version: @DEBIAN_STANDARDS_VERSION@\n"
        "Homepage: @HOMEPAGE@\n"
        "\n"
        "Package: @BINARY_NAME@\n"
        "Depends:\n"
        "@DEB_DEPENDS@\n"
        "Description: @SUMMARY@\n"
        "@DEB_DESCRIPTION@\n"
    )
    values = vr.token_values(model, root / vr.RECIPE_ROOT / "licenses")
    # The fixture control file has no ${misc:Depends}; drop it so the golden
    # stays small without special-casing the renderer.
    values["DEB_DEPENDS"] = values["DEB_DEPENDS"].replace(" ${misc:Depends},\n", "")
    rendered = vr._normalise(vr.substitute(template, values, "fixture control"))
    check(
        "fixture: rendered control matches the golden byte for byte",
        rendered == _FIXTURE_GOLDEN,
        "got:\n" + rendered + "\nwant:\n" + _FIXTURE_GOLDEN,
    )
    check(
        "fixture: rendering twice is byte-identical",
        rendered == vr._normalise(vr.substitute(template, values, "fixture control")),
    )


# ---------------------------------------------------------------------------
# Adapter defaults and overlay overrides
# ---------------------------------------------------------------------------


def test_parallel_build_reaches_both_backends(root: Path) -> None:
    """Every backend that can serialise make must be asserted to do so.

    This test exists because its absence let a defect ship. `parallel_build`
    had one asserted consumer -- the RPM `-j1` flag -- so the Debian backend
    rendering nothing at all went unnoticed until the first live CI build ran
    `dh_auto_build` at -j4 and raced on upstream's undeclared generator
    prerequisites. A declared field with one asserted consumer is a field that
    can be half-ignored.

    Both directions, on both backends: `no` must serialise on each, and the
    default must serialise on neither.
    """
    inputs = _inputs(root)
    licenses = root / vr.RECIPE_ROOT / "licenses"
    el9 = manifest_mod.load_target(
        root / "registry" / "targets" / RELEASE_COHORT / "el9-x86_64.yml"
    )

    serial = vr.token_values(_model(root), licenses)
    serial_rpm = vr.token_values(
        _model(root, target=el9, debian_distribution=None), licenses
    )
    check(
        "parallel: 'no' renders a serialising override on the Debian backend",
        "override_dh_auto_build:" in serial["DEB_AUTO_BUILD_BLOCK"]
        and "-j1" in serial["DEB_AUTO_BUILD_BLOCK"],
        repr(serial["DEB_AUTO_BUILD_BLOCK"]),
    )
    check(
        "parallel: 'no' renders -j1 on the RPM backend",
        serial_rpm["RPM_MAKE_FLAGS"] == " -j1",
        repr(serial_rpm["RPM_MAKE_FLAGS"]),
    )

    overlay = _clone(inputs["overlay"])
    overlay["build"]["parallel_build"] = "yes"
    par = vr.token_values(_model(root, overlay=overlay), licenses)
    par_rpm = vr.token_values(
        _model(root, overlay=overlay, target=el9, debian_distribution=None), licenses
    )
    check(
        "parallel: the default renders no Debian override",
        par["DEB_AUTO_BUILD_BLOCK"] == "",
        repr(par["DEB_AUTO_BUILD_BLOCK"]),
    )
    check(
        "parallel: the default renders no RPM flag",
        par_rpm["RPM_MAKE_FLAGS"] == "",
        repr(par_rpm["RPM_MAKE_FLAGS"]),
    )

    # And in the rendered files, not only in the token values: a token nothing
    # substitutes is exactly the kind of gap this test is here to close.
    templates = root / vr.RECIPE_ROOT / "templates"
    rules = vr.render(_model(root), templates, licenses)["debian/rules"]
    check(
        "parallel: the serialising override reaches debian/rules",
        "override_dh_auto_build:" in rules and "dh_auto_build -- -j1" in rules,
        rules,
    )
    spec = vr.render(
        _model(root, target=el9, debian_distribution=None), templates, licenses
    )["vmod-dict.spec"]
    check(
        "parallel: -j1 reaches the spec's %make_build",
        "%make_build -j1" in spec,
        spec,
    )
    rules_par = vr.render(_model(root, overlay=overlay), templates, licenses)["debian/rules"]
    check(
        "parallel: a parallel VMOD's rules carry no override at all",
        "override_dh_auto_build" not in rules_par,
        rules_par,
    )


def test_adapter_defaults_and_overlay_overrides(root: Path) -> None:
    inputs = _inputs(root)
    model = _model(root)
    check(
        "adapter: the overlay's bootstrap override wins",
        model["build"]["bootstrap"] == "none",
        model["build"]["bootstrap"],
    )
    check(
        "adapter: the shared configure args are kept",
        model["build"]["configure_args"] == ["--disable-static"],
        str(model["build"]["configure_args"]),
    )
    overlay = _clone(inputs["overlay"])
    overlay["build"]["bootstrap"] = "autoreconf"
    boot = _model(root, overlay=overlay)
    check(
        "adapter: bootstrap autoreconf pulls in the autotools dependencies",
        "autoconf" in boot["build"]["dependencies"]
        and "automake" in boot["build"]["dependencies"]
        and "libtool" in boot["build"]["dependencies"],
        str(boot["build"]["dependencies"]),
    )
    values = vr.token_values(boot, root / vr.RECIPE_ROOT / "licenses")
    check(
        "adapter: bootstrap autoreconf drops --without autoreconf from dh",
        values["DH_ARGS"] == "",
        repr(values["DH_ARGS"]),
    )
    check(
        "adapter: bootstrap autoreconf adds autoreconf -fi to the spec",
        "autoreconf -fi" in values["RPM_BOOTSTRAP_BLOCK"],
        values["RPM_BOOTSTRAP_BLOCK"],
    )
    overlay2 = _clone(inputs["overlay"])
    overlay2["build"]["build_time_tests"] = "unit_test"
    tested = _model(root, overlay=overlay2)
    values2 = vr.token_values(tested, root / vr.RECIPE_ROOT / "licenses")
    check(
        "adapter: a declared build-time test subset reaches both backends",
        "TESTS=unit_test" in values2["DEB_AUTO_TEST_BLOCK"]
        and "TESTS=unit_test" in values2["RPM_CHECK_BLOCK"],
        values2["DEB_AUTO_TEST_BLOCK"] + values2["RPM_CHECK_BLOCK"],
    )
    check(
        "adapter: an overlay cannot drop a shared build dependency",
        "debhelper-compat (= 13)" in model["build"]["dependencies"],
        str(model["build"]["dependencies"]),
    )


# ---------------------------------------------------------------------------
# Writing YAML fixtures for the rejection tests
# ---------------------------------------------------------------------------

_TMPDIR = None


def _tmpdir() -> Path:
    global _TMPDIR
    if _TMPDIR is None:
        _TMPDIR = tempfile.TemporaryDirectory()
    return Path(_TMPDIR.name)


def _write_overlay(data: dict) -> Path:
    return _write_yaml(data)


_counter = [0]


def _write_yaml(data: dict) -> Path:
    _counter[0] += 1
    path = _tmpdir() / f"fixture-{_counter[0]}.yml"
    path.write_text(_dump_yaml(data), encoding="utf-8")
    return path


def _dump_yaml(data, indent: int = 0) -> str:
    """Emit the restricted YAML subset yaml_subset.py accepts. Tests only."""
    pad = " " * indent
    out = []
    for key, value in data.items():
        if isinstance(value, dict):
            out.append(f"{pad}{key}:\n" + _dump_yaml(value, indent + 2))
        elif isinstance(value, list):
            if not value:
                out.append(f"{pad}{key}: []\n")
                continue
            out.append(f"{pad}{key}:\n")
            for item in value:
                if isinstance(item, dict):
                    body = _dump_yaml(item, indent + 4)
                    first, rest = body.split("\n", 1)
                    out.append(f"{pad}  - {first.strip()}\n" + rest)
                else:
                    out.append(f"{pad}  - {_scalar(item)}\n")
        else:
            out.append(f"{pad}{key}: {_scalar(value)}\n")
    return "".join(out)


def _scalar(value) -> str:
    text = str(value)
    if text == "":
        return '""'
    if set(text) & set("#{}[]&*!|>%@`\"'") or ": " in text or text.endswith(":"):
        return '"' + text + '"'
    return text


# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Rendered changelog width, and reviewed lint overrides
# ---------------------------------------------------------------------------


def test_changelog_lines_fit(root: Path) -> None:
    """lintian's debian-changelog-line-too-long fires above 80 columns.

    Wave B run 30410876882 emitted a warning on the entry line carrying the
    64-character source digest. No test could have caught it, because nothing
    asserted a property of the rendered changelog's shape -- so the assertion
    is the fix as much as the wrapping is, and it is written against the real
    templates and the real dict inputs rather than a fixture.
    """
    model = _model(root)
    recipe_root = root / vr.RECIPE_ROOT
    changelog = vr.render(model, recipe_root / "templates", recipe_root / "licenses")[
        "debian/changelog"
    ]
    over = [line for line in changelog.split("\n") if len(line) > vr.CHANGELOG_WIDTH]
    check(
        "changelog: no rendered line exceeds 80 columns",
        not over,
        "\n".join(over),
    )
    check(
        "changelog: the source digest survives the wrap intact",
        model["source"]["archive_sha256"] in changelog,
        changelog,
    )
    check(
        "changelog: the trailer keeps its exact syntax through the wrap",
        "\n -- Boffinate <noreply@boffinate.com>  " in changelog,
        changelog,
    )
    check(
        "changelog: the version header is untouched",
        changelog.startswith("vmod-dict (1.7-1) trixie; urgency=medium\n"),
        changelog,
    )

    # A value long enough to need wrapping but made of ordinary words must wrap
    # on whitespace, and a single over-long token must not be split -- a halved
    # digest is worse than a long line.
    wrapped = vr.wrap_changelog(
        "pkg (1-1) trixie; urgency=medium\n"
        "\n"
        "  * " + " ".join(["word"] * 40) + "\n"
        "  * Source sha256: " + "a" * 64 + ".\n"
        "\n"
        " -- M <m@example.com>  Wed, 25 Mar 2026 09:04:22 +0000\n"
    )
    lines = wrapped.split("\n")
    check(
        "changelog: a long prose entry wraps to 80 columns",
        all(len(line) <= vr.CHANGELOG_WIDTH for line in lines),
        wrapped,
    )
    check(
        "changelog: an over-long token is never split",
        ("a" * 64) in wrapped,
        wrapped,
    )
    check(
        "changelog: wrapping is idempotent",
        vr.wrap_changelog(wrapped) == wrapped,
        wrapped,
    )


def test_reviewed_lint_overrides_are_rendered(root: Path) -> None:
    """A declared override must reach the package, or the gate has no escape.

    dict declares exactly one binary override -- upstream's rst2man page selects
    font C, which groff warns about and which cannot be fixed without patching
    upstream source the Step 6 adapter deliberately cannot patch. The override
    list existed in the schema from Wave A1 and had never been non-empty, so
    nothing had ever proved the declared lines reach the rendered file.
    """
    inputs = _inputs(root)
    model = _model(root)
    recipe_root = root / vr.RECIPE_ROOT
    outputs = vr.render(model, recipe_root / "templates", recipe_root / "licenses")
    rendered = outputs["debian/vmod-dict.lintian-overrides"]
    declared = inputs["overlay"]["lintian_overrides"]["binary"]
    check(
        "lint overrides: the overlay declares at least one binary override",
        bool(declared),
        str(declared),
    )
    for line in declared:
        check(
            f"lint overrides: rendered file carries {line[:40]!r}",
            line in rendered,
            rendered,
        )
    check(
        "lint overrides: the template's own tag is still there",
        "vmod-dict: initial-upload-closes-no-bugs" in rendered,
        rendered,
    )
    check(
        "lint overrides: an empty source list renders no stray line",
        outputs["debian/source/lintian-overrides"].endswith(
            "vmod-dict source: debian-watch-file-is-missing\n"
        ),
        outputs["debian/source/lintian-overrides"],
    )



def main(repo_root: Path = None) -> int:
    root = Path(repo_root) if repo_root else vr.REPO_ROOT
    _RESULTS.clear()

    test_dict_manifest_is_catalog_valid(root)
    test_dict_deb_model(root)
    test_abi_expressions_are_not_duplicated(root)
    test_dict_deb_render(root)
    test_dict_rpm_render(root)
    test_determinism(root)
    test_dates_come_from_the_recorded_epoch(root)
    test_dates_are_locale_independent()
    test_inspection_commands_do_not_need_a_maintainer(root)
    test_generator_never_builds(root)
    test_tokens_undeclared_is_refused()
    test_tokens_surviving_substitution_is_refused()
    test_tokens_single_pass()
    test_tokens_missing_template_is_refused(root)
    test_missing_maintainer(root)
    test_missing_source_identity(root)
    test_missing_license(root)
    test_missing_description(root)
    test_missing_payload_policy(root)
    test_missing_adapter_revision(root)
    test_missing_abi_input(root)
    test_missing_unknown_adapter(root)
    test_missing_debian_distribution(root)
    test_mismatched_ids(root)
    test_generation_record(root)
    test_expected_names(root)
    test_fixture_render_matches_the_golden(root)
    test_parallel_build_reaches_both_backends(root)
    test_adapter_defaults_and_overlay_overrides(root)
    test_changelog_lines_fit(root)
    test_reviewed_lint_overrides_are_rendered(root)

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
