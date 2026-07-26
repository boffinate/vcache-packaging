"""Schema, cohort-identity digest, and validation for the Vinyl cohort registry.

See registry/README.md for the normative schema description. This module is the
executable copy of it: if the two disagree, that is a bug in one of them.

Standard library only.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import yaml_subset

__all__ = [
    "ValidationError",
    "REPO_ROOT",
    "DEFAULT_CACHETAG_SRC_NAME",
    "CACHETAG_SRC_ENV",
    "default_cachetag_src",
    "configure_ac_version",
    "cohort_input_blob",
    "cohort_input_id",
    "cohort_identifier",
    "validate_cohort",
    "validate_target",
    "validate_registry_tree",
    "load_cohort",
    "load_target",
]

REPO_ROOT = Path(__file__).resolve().parents[1]

PACKAGE_STEM = "libvmod-cachetag"

# The cachetag sources live in their own repository now. This registry needs
# exactly one fact from that checkout -- the authoritative version in
# configure.ac -- so its location is an explicit input rather than an
# assumption about where this file sits in a tree.
DEFAULT_CACHETAG_SRC_NAME = "libvmod-cachetag"
CACHETAG_SRC_ENV = "CACHETAG_SRC"

# ---------------------------------------------------------------------------
# Placeholder convention
# ---------------------------------------------------------------------------

PLACEHOLDER_SHA256 = "0" * 64
PLACEHOLDER_COMMIT = "0" * 40
PLACEHOLDER_ABI = "0" * 40
PLACEHOLDER_INPUT_ID = "0" * 12
PLACEHOLDER_TOKEN = "PLACEHOLDER"
PLACEHOLDER_HOST = "example.invalid"


def is_placeholder(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if value in (PLACEHOLDER_SHA256, PLACEHOLDER_COMMIT, PLACEHOLDER_INPUT_ID, PLACEHOLDER_TOKEN):
        return True
    if PLACEHOLDER_HOST in value:
        return True
    if value.startswith("sha256:") and set(value[7:]) == {"0"}:
        return True
    return False


# ---------------------------------------------------------------------------
# Scalar patterns
# ---------------------------------------------------------------------------

SHA256_RE = r"^(?:[0-9a-f]{64})$"
COMMIT_RE = r"^(?:[0-9a-f]{40})$"
ABI_RE = r"^(?:[0-9a-f]{40})$"
VERSION_RE = r"^[0-9]+\.[0-9]+\.[0-9]+$"
VRT_RE = r"^[0-9]+\.[0-9]+$"
INPUT_ID_RE = r"[0-9a-f]{12}"
COHORT_ID_RE = r"^vinyl-[0-9]+\.[0-9]+\.[0-9]+-" + INPUT_ID_RE + r"$"
TARGET_ID_RE = r"^[a-z][a-z0-9]*(?:-[a-z0-9._]+)+$"
DISTRO_ID_RE = r"^[a-z][a-z0-9]*(?:-[a-z0-9._]+)*$"
NAME_RE = r"^[a-z][a-z0-9+._-]*$"
# Buildroot package names belong to the distribution, not to this project, and
# RPM ships plenty that NAME_RE would reject: perl-AutoLoader, hunspell-en-US,
# perl-Text-Tabs+Wrap. Recording the exactly resolved buildroot is the EL9
# lane's whole reproducibility story -- Mock resolves from live mirrors with no
# snapshot service to pin -- so the pattern has to admit the names that lane
# actually resolves.
BUILDROOT_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9+._-]*$"
FILENAME_RE = r"^[A-Za-z0-9][A-Za-z0-9._+-]*$"
FREE_TEXT_RE = r"^[^\s].*$"
IMAGE_DIGEST_RE = r"^sha256:[0-9a-f]{64}$"
# An absolute, fully resolved directory: no relative path, no trailing slash,
# no empty segment, and no unexpanded ${libdir}-style pkg-config variable.
ABS_DIR_RE = r"^/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+$"

TEST_STATUS = ["pending", "pass", "fail", "not-applicable"]


def _s(pattern: str, **kw) -> dict:
    node = {"type": "str", "pattern": pattern}
    node.update(kw)
    return node


def _enum(values, **kw) -> dict:
    node = {"type": "enum", "values": list(values)}
    node.update(kw)
    return node


def _int(minimum: int = 0, **kw) -> dict:
    node = {"type": "int", "min": minimum}
    node.update(kw)
    return node


def _map(fields: dict, **kw) -> dict:
    node = {"type": "map", "fields": fields}
    node.update(kw)
    return node


def _list(item: dict, min_len: int = 0, **kw) -> dict:
    node = {"type": "list", "item": item, "min_len": min_len}
    node.update(kw)
    return node


# ---------------------------------------------------------------------------
# Cohort manifest schema
# ---------------------------------------------------------------------------

COHORT_SPEC = _map(
    {
        "schema": _enum(["cachetag-cohort/v1"]),
        "status": _enum(["template", "candidate", "released"]),
        "cohort": _s(COHORT_ID_RE),
        "cachetag": _map(
            {
                "version": _s(VERSION_RE),
                "source_sha256": _s(SHA256_RE),
                "git_commit": _s(COMMIT_RE),
            }
        ),
        "vinyl": _map(
            {
                "version": _s(VERSION_RE),
                "source_url": _s(FREE_TEXT_RE),
                "source_sha256": _s(SHA256_RE),
                "git_commit": _s(COMMIT_RE),
                "vrt": _s(VRT_RE),
                "strict_abi": _s(ABI_RE),
                "patches": _list(
                    _map({"name": _s(FILENAME_RE), "sha256": _s(SHA256_RE)}),
                    min_len=0,
                ),
            }
        ),
        "build_profile": _map({"name": _enum(["production", "diagnostic"]), "revision": _int(1)}),
        "required_vmods": _list(_s(NAME_RE), min_len=1),
        "storage_support": _list(_enum(["default", "buddy"]), min_len=1),
        "targets": _list(_s(TARGET_ID_RE), min_len=1),
        "support": _map(
            {
                "channel": _enum(["pre-release", "stable"]),
                "release_owner": _s(FREE_TEXT_RE),
                "fellow": _enum(["excluded", "supported"]),
                "buddy": _enum(["source-harness-only", "packaged"]),
            }
        ),
    }
)

# ---------------------------------------------------------------------------
# Target manifest schema
# ---------------------------------------------------------------------------

_BUILD_FIELDS = {
    "profile": _enum(["production", "diagnostic"]),
    "image_ref": _s(FREE_TEXT_RE),
    "image_digest": _s(IMAGE_DIGEST_RE),
    "compiler": _s(FREE_TEXT_RE),
    "configure_options": _s(FREE_TEXT_RE),
    "cflags": _s(FREE_TEXT_RE),
    "ldflags": _s(FREE_TEXT_RE),
    "source_date_epoch": _s(r"^(?:[0-9]+|PLACEHOLDER)$"),
    "hardening_check": _enum(TEST_STATUS),
    "build_dependencies": _list(
        _map({"name": _s(BUILDROOT_NAME_RE), "version": _s(FREE_TEXT_RE)}), min_len=0
    ),
}

# Installed layout resolved from the Vinyl development package at build time.
# vmoddir is the pkg-config 'vmoddir' variable from vinylapi.pc, fully expanded
# for this distro and architecture. Packaging recipes consume it as the
# @VINYL_VMODDIR@ substitution token.
_INSTALL_FIELDS = {
    "vmoddir": _s(ABS_DIR_RE),
    "vmoddir_source": _enum(["pkg-config", "recorded"]),
}

_TARGET_FIELDS = {
    "id": _s(TARGET_ID_RE),
    "distro": _s(NAME_RE),
    "distro_release": _s(r"^[0-9][0-9A-Za-z._-]*$"),
    "distro_id": _s(DISTRO_ID_RE),
    "arch": _s(r"^[a-z0-9_]+$"),
    "package_format": _enum(["deb", "rpm", "arch", "freebsd", "apk"]),
    "dist_tag": _s(r"^(?:|[a-z0-9._]+)$"),
}

_PACKAGE_FIELDS = {
    "revision": _int(1),
    "source_name": _s(NAME_RE),
    "binary_name": _s(NAME_RE),
}

_TESTS_FIELDS = {
    "package_lint": _enum(TEST_STATUS),
    "installed_package_smoke": _enum(TEST_STATUS),
    "full_behavior_suite": _enum(TEST_STATUS),
    "upgrade_transactions": _enum(TEST_STATUS),
}

_ARTIFACTS = _list(_map({"filename": _s(FILENAME_RE), "sha256": _s(SHA256_RE)}), min_len=0)

TARGET_SPEC = _map(
    {
        "schema": _enum(["cachetag-target/v1"]),
        "status": _enum(["template", "candidate", "released"]),
        "lane": _enum(["cohort"]),
        "cohort": _s(COHORT_ID_RE),
        "target": _map(dict(_TARGET_FIELDS)),
        "package": _map(dict(_PACKAGE_FIELDS)),
        "vinyl_packages": _map(
            {
                "origin": _enum(["cohort"]),
                "runtime_name": _s(NAME_RE),
                "runtime_version": _s(FREE_TEXT_RE),
                "dev_name": _s(NAME_RE),
                "dev_version": _s(FREE_TEXT_RE),
            }
        ),
        "build": _map(dict(_BUILD_FIELDS)),
        "install": _map(dict(_INSTALL_FIELDS)),
        "artifacts": _ARTIFACTS,
        "tests": _map(dict(_TESTS_FIELDS)),
    }
)

DISTRO_NATIVE_SPEC = _map(
    {
        "schema": _enum(["cachetag-distro-native/v1"]),
        "status": _enum(["template", "candidate", "released"]),
        "lane": _enum(["distro-native"]),
        "cachetag": _map({"version": _s(VERSION_RE), "source_sha256": _s(SHA256_RE)}),
        "target": _map(dict(_TARGET_FIELDS)),
        "package": _map(dict(_PACKAGE_FIELDS)),
        "distro_vinyl": _map(
            {
                "repository_origin": _s(FREE_TEXT_RE),
                "upstream_version": _s(VERSION_RE),
                "source_package_version": _s(FREE_TEXT_RE),
                "binary_package_version": _s(FREE_TEXT_RE),
                "runtime_package": _s(NAME_RE),
                "dev_package": _s(NAME_RE),
                "vrt": _s(VRT_RE),
                "strict_abi": _s(ABI_RE),
                "exposes_abi_provide": _enum(["yes", "no", "unknown"]),
                "patches": _list(_map({"name": _s(FILENAME_RE), "sha256": _s(SHA256_RE)}), min_len=0),
            }
        ),
        "build": _map(dict(_BUILD_FIELDS)),
        "install": _map(dict(_INSTALL_FIELDS)),
        "artifacts": _ARTIFACTS,
        "tests": _map(dict(_TESTS_FIELDS)),
    }
)


class ValidationError(Exception):
    """Raised when a manifest is structurally or semantically invalid."""


# ---------------------------------------------------------------------------
# Generic schema walk
# ---------------------------------------------------------------------------


def _check(node: dict, value, path: str, errors: list) -> None:
    kind = node["type"]
    if kind == "map":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected a mapping")
            return
        for key, sub in node["fields"].items():
            sub_path = f"{path}.{key}" if path else key
            if key not in value:
                errors.append(f"{sub_path}: missing required field")
                continue
            _check(sub, value[key], sub_path, errors)
        for key in value:
            if key not in node["fields"]:
                errors.append(f"{path}.{key}: unknown field" if path else f"{key}: unknown field")
        return
    if kind == "list":
        if not isinstance(value, list):
            errors.append(f"{path}: expected a list (use [] for an empty list)")
            return
        if len(value) < node["min_len"]:
            errors.append(f"{path}: needs at least {node['min_len']} entry/entries")
        for i, item in enumerate(value):
            _check(node["item"], item, f"{path}[{i}]", errors)
        return
    if not isinstance(value, str):
        errors.append(f"{path}: expected a scalar")
        return
    if kind == "enum":
        if value not in node["values"]:
            errors.append(f"{path}: {value!r} is not one of {node['values']}")
        return
    if kind == "int":
        if not re.match(r"^(?:0|[1-9][0-9]*)$", value):
            errors.append(f"{path}: {value!r} is not a non-negative integer")
            return
        if int(value) < node["min"]:
            errors.append(f"{path}: must be >= {node['min']}")
        return
    if kind == "str":
        if is_placeholder(value):
            return  # placeholder values bypass the pattern; releasability is checked separately
        if not re.match(node["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {node['pattern']}")
        return
    raise AssertionError(f"unknown schema node type {kind!r}")


def schema_errors(spec: dict, data, path: str) -> list:
    """Structural errors only: missing, unknown, or wrongly typed fields."""
    errors: list = []
    _check(spec, data, "", errors)
    return [f"{path}: {e}" for e in errors]


def _collect_placeholders(value, path: str, found: list) -> None:
    if isinstance(value, dict):
        for key, sub in value.items():
            _collect_placeholders(sub, f"{path}.{key}" if path else key, found)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _collect_placeholders(item, f"{path}[{i}]", found)
    elif is_placeholder(value):
        found.append(path)


# ---------------------------------------------------------------------------
# Project facts
# ---------------------------------------------------------------------------


def default_cachetag_src(repo_root: Path = None) -> Path:
    """Where to look for the cachetag checkout when no path is given.

    The CACHETAG_SRC environment variable wins, so a container or CI job can
    point at a checkout that is not a sibling. Otherwise it is the sibling
    directory next to this repository, which is how the workspace is laid out.
    """
    from_env = os.environ.get(CACHETAG_SRC_ENV)
    if from_env:
        return Path(from_env).expanduser()
    root = Path(repo_root) if repo_root else REPO_ROOT
    return root.parent / DEFAULT_CACHETAG_SRC_NAME


def configure_ac_version(cachetag_src=None, repo_root: Path = None) -> str:
    """Read the authoritative cachetag version from a cachetag checkout.

    ``cachetag_src`` is the root of a libvmod-cachetag checkout. When it is
    omitted the value from :func:`default_cachetag_src` is used. The version
    in a manifest must agree with AC_INIT there; that cross-check is the
    reason this registry knows about the cachetag repository at all.
    """
    src = Path(cachetag_src) if cachetag_src else default_cachetag_src(repo_root)
    configure_ac = src / "configure.ac"
    try:
        text = configure_ac.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValidationError(
            f"no cachetag configure.ac at {configure_ac}. The registry cross-checks "
            "cachetag.version against AC_INIT in a libvmod-cachetag checkout; point at "
            f"one with --cachetag-src PATH or {CACHETAG_SRC_ENV}=PATH."
        ) from None
    match = re.search(r"AC_INIT\(\[libvmod-cachetag\],\s*\[([^\]]+)\]", text)
    if match is None:
        raise ValidationError(
            f"could not read AC_INIT version from {configure_ac}; it does not look like "
            "a libvmod-cachetag configure.ac"
        )
    return match.group(1).strip()


# ---------------------------------------------------------------------------
# Cohort identity digest
# ---------------------------------------------------------------------------

COHORT_INPUT_MAGIC = "cachetag-cohort-input/v1"


def cohort_input_blob(cohort: dict) -> bytes:
    """Canonical byte encoding of the cohort compatibility inputs.

    Exactly six-plus-N LF-terminated UTF-8 lines, in this order:

        cachetag-cohort-input/v1
        vinyl-source-sha256=<64 lowercase hex>
        patch-count=<decimal count>
        patch[<i>]-sha256=<64 lowercase hex>      (one per patch, i from 0, in manifest order)
        build-profile=<profile name>
        build-profile-revision=<decimal revision>

    The cohort identifier itself and every generated output field are excluded.
    """
    vinyl = cohort["vinyl"]
    profile = cohort["build_profile"]
    patches = vinyl["patches"]
    lines = [
        COHORT_INPUT_MAGIC,
        "vinyl-source-sha256=" + vinyl["source_sha256"],
        "patch-count=" + str(len(patches)),
    ]
    for index, patch in enumerate(patches):
        lines.append(f"patch[{index}]-sha256=" + patch["sha256"])
    lines.append("build-profile=" + profile["name"])
    lines.append("build-profile-revision=" + str(profile["revision"]))
    return ("\n".join(lines) + "\n").encode("utf-8")


def cohort_input_id(cohort: dict) -> str:
    """First 12 lowercase hex characters of SHA-256 over the canonical blob."""
    return hashlib.sha256(cohort_input_blob(cohort)).hexdigest()[:12]


def cohort_identifier(cohort: dict) -> str:
    return "vinyl-{}-{}".format(cohort["vinyl"]["version"], cohort_input_id(cohort))


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def load_cohort(path) -> dict:
    return yaml_subset.parse_file(path)


def load_target(path) -> dict:
    return yaml_subset.parse_file(path)


def validate_cohort(data: dict, path: str, expected_version: str, require_releasable: bool = False) -> list:
    """Return a list of error strings ([] means valid)."""
    errors: list = []
    _check(COHORT_SPEC, data, "", errors)
    if errors:
        return [f"{path}: {e}" for e in errors]

    status = data["status"]
    version = data["cachetag"]["version"]
    if version != expected_version:
        errors.append(
            f"cachetag.version {version!r} does not match configure.ac AC_INIT "
            f"{expected_version!r} in the cachetag checkout"
        )

    cohort_id = data["cohort"]
    prefix = "vinyl-{}-".format(data["vinyl"]["version"])
    if not cohort_id.startswith(prefix):
        errors.append(
            f"cohort {cohort_id!r} does not embed vinyl.version {data['vinyl']['version']!r}"
        )
    input_id = cohort_id[len(prefix):] if cohort_id.startswith(prefix) else ""

    placeholders: list = []
    _collect_placeholders(data, "", placeholders)

    if status == "template":
        if input_id != PLACEHOLDER_INPUT_ID:
            errors.append(
                "a template cohort must use the placeholder input-id "
                f"{PLACEHOLDER_INPUT_ID!r}, not {input_id!r}"
            )
        if data["vinyl"]["source_sha256"] != PLACEHOLDER_SHA256:
            errors.append("a template cohort must use the placeholder vinyl.source_sha256")
        if require_releasable:
            errors.append("status is 'template'; a template manifest is never releasable")
    else:
        if placeholders:
            errors.append(
                "status is {!r} but placeholder values remain: {}".format(status, ", ".join(sorted(placeholders)))
            )
        computed = cohort_input_id(data)
        if input_id != computed:
            errors.append(
                f"cohort input-id {input_id!r} does not match the digest of the recorded "
                f"compatibility inputs (expected {computed!r})"
            )
        if data["build_profile"]["name"] != "production":
            errors.append("a releasable cohort must use build_profile.name 'production'")
    return [f"{path}: {e}" for e in errors]


def validate_target(
    data: dict,
    path: str,
    cohort: dict = None,
    cohort_status: str = None,
    require_releasable: bool = False,
    distro_native: bool = False,
    expected_version: str = None,
) -> list:
    errors: list = []
    spec = DISTRO_NATIVE_SPEC if distro_native else TARGET_SPEC
    _check(spec, data, "", errors)
    if errors:
        return [f"{path}: {e}" for e in errors]

    target = data["target"]
    expected_id = "{}-{}".format(target["distro_id"], target["arch"])
    if target["id"] != expected_id:
        errors.append(f"target.id {target['id']!r} must be '<distro_id>-<arch>' ({expected_id!r})")
    allowed_distro_ids = {
        "{}{}".format(target["distro"], target["distro_release"]),
        "{}-{}".format(target["distro"], target["distro_release"]),
    }
    if target["distro_id"] not in allowed_distro_ids:
        errors.append(
            f"target.distro_id {target['distro_id']!r} must be one of "
            f"{sorted(allowed_distro_ids)} (built from target.distro and target.distro_release)"
        )
    stem = Path(path).stem
    if stem != target["id"]:
        errors.append(f"file name stem {stem!r} must equal target.id {target['id']!r}")

    if target["package_format"] == "rpm":
        if not target["dist_tag"]:
            errors.append("an rpm target must set target.dist_tag (for example 'el9')")
    elif target["dist_tag"]:
        errors.append(f"target.dist_tag must be empty for package_format {target['package_format']!r}")

    if data["package"]["source_name"] != PACKAGE_STEM or data["package"]["binary_name"] != PACKAGE_STEM:
        errors.append(f"package.source_name and package.binary_name must both be {PACKAGE_STEM!r}")

    if distro_native and expected_version is not None:
        if data["cachetag"]["version"] != expected_version:
            errors.append(
                "cachetag.version {!r} does not match configure.ac AC_INIT {!r}".format(
                    data["cachetag"]["version"], expected_version
                )
            )

    if not distro_native and cohort is not None:
        if data["cohort"] != cohort["cohort"]:
            errors.append(f"cohort {data['cohort']!r} does not match its cohort manifest {cohort['cohort']!r}")
        if target["id"] not in cohort["targets"]:
            errors.append(f"target.id {target['id']!r} is not listed in the cohort manifest targets")
        if cohort_status is not None and data["status"] != cohort_status:
            errors.append(f"status {data['status']!r} does not match the cohort status {cohort_status!r}")

    placeholders: list = []
    _collect_placeholders(data, "", placeholders)
    if data["status"] == "template":
        if not placeholders:
            errors.append("status is 'template' but no placeholder values are present")
        if require_releasable:
            errors.append("status is 'template'; a template manifest is never releasable")
    else:
        if placeholders:
            errors.append(
                "status is {!r} but placeholder values remain: {}".format(
                    data["status"], ", ".join(sorted(placeholders))
                )
            )
        if data["build"]["profile"] != "production":
            errors.append("a releasable target must use build.profile 'production'")
        vmoddir = data["install"]["vmoddir"]
        # The Docker test harness installs Vinyl into /tmp/vinyl-prefix. That
        # path must never reach a package: it would mean the VMOD directory was
        # taken from the harness rather than from the installed dev package.
        if vmoddir.startswith("/tmp/") or "/vinyl-prefix" in vmoddir or "/vinyl-build" in vmoddir:
            errors.append(
                f"install.vmoddir {vmoddir!r} looks like a test-harness prefix, not an installed "
                "package path"
            )
        if require_releasable:
            if data["install"]["vmoddir_source"] != "pkg-config":
                errors.append(
                    "install.vmoddir_source must be 'pkg-config' for a releasable target; the "
                    "directory has to come from vinylapi.pc, not be written by hand"
                )
            for key, value in sorted(data["tests"].items()):
                if value not in ("pass", "not-applicable"):
                    errors.append(f"tests.{key} is {value!r}; a releasable target needs 'pass'")
            if data["build"]["hardening_check"] not in ("pass", "not-applicable"):
                errors.append("build.hardening_check must be 'pass' for a releasable target")
            if not data["artifacts"]:
                errors.append("a releasable target must record at least one artifact digest")
    return [f"{path}: {e}" for e in errors]


def registry_dir(repo_root: Path = None) -> Path:
    root = Path(repo_root) if repo_root else REPO_ROOT
    return root / "registry"


def validate_registry_tree(
    repo_root: Path = None,
    only_cohort: str = None,
    require_releasable: bool = False,
    cachetag_src=None,
) -> tuple:
    """Validate every manifest in registry/. Returns (checked_paths, errors)."""
    root = Path(repo_root) if repo_root else REPO_ROOT
    rel = registry_dir(root)
    expected_version = configure_ac_version(cachetag_src, repo_root=root)
    checked: list = []
    errors: list = []

    cohorts_dir = rel / "cohorts"
    targets_dir = rel / "targets"
    native_dir = rel / "distro-native"

    for directory in (cohorts_dir, targets_dir, native_dir):
        if not directory.is_dir():
            errors.append(f"{directory}: missing required registry directory")
    if errors:
        return checked, errors

    cohort_files = sorted(cohorts_dir.glob("*.yml"))
    if not cohort_files:
        errors.append(f"{cohorts_dir}: no cohort manifests found")

    seen_target_dirs = set()
    releasable_cohorts: list = []
    for cohort_path in cohort_files:
        rel_path = cohort_path.relative_to(root)
        checked.append(str(rel_path))
        try:
            cohort = load_cohort(cohort_path)
        except yaml_subset.ManifestSyntaxError as exc:
            errors.append(str(exc))
            continue
        # A template is a schema exemplar that lives in the registry
        # permanently -- registry/README.md's "template convention", and the
        # self-tests read the checked-in ones. It is never releasable, and
        # asking one to be releasable is a category error rather than a
        # finding, so in --require-releasable mode a template is held to the
        # schema and the tree-level requirement below is what actually bites:
        # some cohort must be releasable. Selecting a template with --cohort
        # is still an error, because that names a specific thing to release.
        is_template = cohort.get("status") == "template"
        cohort_releasable = require_releasable and not is_template
        if require_releasable and is_template and only_cohort == cohort.get("cohort"):
            errors.append(
                f"{rel_path}: --cohort {only_cohort!r} selects a template manifest, "
                "which is never releasable"
            )
        cohort_errors = validate_cohort(cohort, str(rel_path), expected_version, cohort_releasable)
        if cohort_path.stem != cohort.get("cohort"):
            cohort_errors.append(f"{rel_path}: file name stem must equal the cohort identifier")
        errors.extend(cohort_errors)
        if only_cohort and cohort.get("cohort") != only_cohort:
            continue
        # Semantic cohort errors (template status, digest mismatch) must not
        # hide the target manifests; only a structurally broken cohort can.
        if schema_errors(COHORT_SPEC, cohort, str(rel_path)):
            continue

        target_dir = targets_dir / cohort["cohort"]
        seen_target_dirs.add(target_dir.name)
        if not target_dir.is_dir():
            errors.append(
                f"{rel_path}: missing target directory registry/targets/{cohort['cohort']}/"
            )
            continue
        present = sorted(p.stem for p in target_dir.glob("*.yml"))
        listed = sorted(cohort["targets"])
        if present != listed:
            errors.append(
                f"{rel_path}: cohort targets {listed} do not match the manifests present {present}"
            )
        cohort_target_errors: list = []
        for target_path in sorted(target_dir.glob("*.yml")):
            target_rel = target_path.relative_to(root)
            checked.append(str(target_rel))
            try:
                target = load_target(target_path)
            except yaml_subset.ManifestSyntaxError as exc:
                errors.append(str(exc))
                continue
            cohort_target_errors.extend(
                validate_target(
                    target,
                    str(target_rel),
                    cohort=cohort,
                    cohort_status=cohort["status"],
                    require_releasable=cohort_releasable,
                )
            )
        errors.extend(cohort_target_errors)
        if cohort_releasable and not cohort_errors and not cohort_target_errors:
            releasable_cohorts.append(cohort["cohort"])

    for stray in sorted(targets_dir.iterdir()) if targets_dir.is_dir() else []:
        if stray.is_dir() and stray.name not in seen_target_dirs and not only_cohort:
            errors.append(f"registry/targets/{stray.name}: no matching cohort manifest")

    if not only_cohort:
        for native_path in sorted(native_dir.glob("*.yml")):
            native_rel = native_path.relative_to(root)
            checked.append(str(native_rel))
            try:
                native = load_target(native_path)
            except yaml_subset.ManifestSyntaxError as exc:
                errors.append(str(exc))
                continue
            errors.extend(
                validate_target(
                    native,
                    str(native_rel),
                    # Same rule as the cohort lane: a distro-native template is
                    # a schema exemplar, not a release candidate that happens
                    # to be unfinished. There is no distro Vinyl 9 package to
                    # build one against yet.
                    require_releasable=require_releasable and native.get("status") != "template",
                    distro_native=True,
                    expected_version=expected_version,
                )
            )

    if require_releasable and not releasable_cohorts:
        errors.append(
            "no releasable cohort: every cohort manifest is a template, or the only "
            "non-template ones failed the checks above. --require-releasable exists to "
            "answer 'is there something publishable here?', and the answer is no"
        )

    return checked, errors
