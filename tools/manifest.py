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
# Upstream reference URLs: https only, no spaces, no fragments of shell
# metacharacters. These are rendered verbatim into generated release content,
# so the shape is deliberately strict.
URL_RE = r"^https://[A-Za-z0-9][A-Za-z0-9./_%~#+-]*$"
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


def _dynmap(item: dict, key_pattern: str, min_len: int = 0, **kw) -> dict:
    """A mapping whose KEYS are data, not schema.

    `_map` declares its field names; this one declares the shape every value
    must have and constrains the keys with a pattern. Used for the per-target
    `vmods:` block, whose keys are VMOD ids: which ids belong there is decided
    by the catalog and its lanes, not by this schema, so listing them here
    would be a second place to keep the selected set true.
    """
    node = {"type": "dynmap", "item": item, "key_pattern": key_pattern, "min_len": min_len}
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
                # Pointers to UPSTREAM's own release statements for the pinned
                # version -- release notes, changelog renderings -- recorded as
                # {title, url} and rendered verbatim into generated release
                # content as links. Deliberately references, never claims: it
                # is upstream's job to state what is in their release, and this
                # registry records where they state it, not restatements in our
                # voice. Optional, because a trunk snapshot has no upstream
                # release statement; absent means no section is rendered. Not a
                # digest input: the cohort identity describes source bytes, not
                # documentation about them.
                "release_notes": _list(
                    _map({"title": _s(FREE_TEXT_RE), "url": _s(URL_RE)}),
                    min_len=1,
                    optional=True,
                ),
            }
        ),
        # Which engine input built this cohort. registry/README.md listed an
        # explicit track field under "deliberately not here yet", with the
        # condition that it becomes worth its validation rules "when a policy
        # decision has to read it mechanically". That moment arrived with the
        # second VMOD: the per-target evidence map must contain exactly the
        # VMODs whose catalog lanes build for this cohort and target, and the
        # lanes name an engine input. Deriving it from vinyl.version does not
        # work -- the trunk cohorts record a bare 9.0.0, not a ~git snapshot
        # version -- and guessing from the shape of source_url would be an
        # inference where a statement is available. Wiring, not identity: it is
        # not a cohort-input digest field and cannot change a cohort id.
        "engine": _enum(["vinyl-release", "vinyl-trunk-pinned"]),
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

# Facts about the BUILDROOT, which is one per target and not one per VMOD.
# Every VMOD built for this cohort and target is built in this container with
# this compiler; recording that twice would make it possible for the two copies
# to disagree, and there is no meaning to attach to the disagreement.
_BUILDROOT_FIELDS = {
    "image_ref": _s(FREE_TEXT_RE),
    "image_digest": _s(IMAGE_DIGEST_RE),
    "compiler": _s(FREE_TEXT_RE),
}

# Facts about ONE VMOD's build in that buildroot. These are per-VMOD because
# each one is configured, flagged, dated and dependency-resolved separately:
# cachetag and dict do not share a configure line, a SOURCE_DATE_EPOCH (each
# comes from its own release commit) or a build-dependency set.
_VMOD_BUILD_FIELDS = {
    "profile": _enum(["production", "diagnostic"]),
    "configure_options": _s(FREE_TEXT_RE),
    "cflags": _s(FREE_TEXT_RE),
    "ldflags": _s(FREE_TEXT_RE),
    "source_date_epoch": _s(r"^(?:[0-9]+|PLACEHOLDER)$"),
    "hardening_check": _enum(TEST_STATUS),
    "build_dependencies": _list(
        _map({"name": _s(BUILDROOT_NAME_RE), "version": _s(FREE_TEXT_RE)}), min_len=0
    ),
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

# `upstream_version` is per-VMOD: cachetag builds 1.0.1 into this cohort and
# vmod-dict builds 1.7 into the same one. v1 could take it from the cohort's
# `cachetag.version` because cachetag was the only VMOD; with two, that field
# is one VMOD's version and nothing else, and the validator cross-checks
# cachetag's entry against it so the two cannot drift.
_VMOD_PACKAGE_FIELDS = {
    "upstream_version": _s(FREE_TEXT_RE),
    "revision": _int(1),
    "source_name": _s(NAME_RE),
    "binary_name": _s(NAME_RE),
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

# One VMOD's complete evidence for this cohort and target.
#
# `evidence` is the honest state of the entry, not a test result:
#
#   pending   the lanes have not run for this VMOD yet, or their results were
#             reset. `pending_reason` says why, in words, so a reader does not
#             have to reconstruct it from the surrounding zeros. Never
#             releasable.
#   recorded  the fields below are outputs of a real run.
#
# A pending entry still has to exist. That is the point of the map: "both
# package families meet the same evidence policy" becomes something the
# validator checks rather than something a human remembers to look for.
VMOD_EVIDENCE_SPEC = _map(
    {
        "evidence": _enum(["pending", "recorded"]),
        "pending_reason": _s(FREE_TEXT_RE, optional=True),
        "package": _map(dict(_VMOD_PACKAGE_FIELDS)),
        "build": _map(dict(_VMOD_BUILD_FIELDS)),
        "artifacts": _ARTIFACTS,
        "tests": _map(dict(_TESTS_FIELDS)),
    }
)

TARGET_SPEC = _map(
    {
        # v2, 2026-07-28. v1 recorded exactly one VMOD's evidence per file, in
        # top-level `package`, `build`, `artifacts` and `tests` blocks, and its
        # validator hardcoded `libvmod-cachetag`. With a second VMOD that shape
        # cannot say what it needs to say. The migration is a restructure, not
        # an addition: the legacy blocks are gone and cachetag's data moved
        # into vmods.cachetag verbatim. There are no users and the runbook does
        # not require backwards compatibility, so a compatibility shim would
        # have been two shapes to keep true rather than one.
        "schema": _enum(["cachetag-target/v2"]),
        "status": _enum(["template", "candidate", "released"]),
        "lane": _enum(["cohort"]),
        "cohort": _s(COHORT_ID_RE),
        "target": _map(dict(_TARGET_FIELDS)),
        "vinyl_packages": _map(
            {
                "origin": _enum(["cohort"]),
                "runtime_name": _s(NAME_RE),
                "runtime_version": _s(FREE_TEXT_RE),
                "dev_name": _s(NAME_RE),
                "dev_version": _s(FREE_TEXT_RE),
            }
        ),
        "buildroot": _map(dict(_BUILDROOT_FIELDS)),
        "install": _map(dict(_INSTALL_FIELDS)),
        # Keyed by VMOD id. Which ids must be present is not a schema question
        # -- it depends on which catalog lanes select this cohort and target --
        # so it is checked in validate_target against registry/vmods/.
        "vmods": _dynmap(VMOD_EVIDENCE_SPEC, key_pattern=r"^[a-z][a-z0-9-]*$", min_len=1),
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
                if not sub.get("optional"):
                    errors.append(f"{sub_path}: missing required field")
                continue
            _check(sub, value[key], sub_path, errors)
        for key in value:
            if key not in node["fields"]:
                errors.append(f"{path}.{key}: unknown field" if path else f"{key}: unknown field")
        return
    if kind == "dynmap":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected a mapping keyed by id")
            return
        if len(value) < node["min_len"]:
            errors.append(f"{path}: needs at least {node['min_len']} entry/entries")
        for key in sorted(value):
            sub_path = f"{path}.{key}" if path else key
            if not re.match(node["key_pattern"], key):
                errors.append(f"{sub_path}: key {key!r} does not match {node['key_pattern']}")
                continue
            _check(node["item"], value[key], sub_path, errors)
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


def validate_cohort(
    data: dict,
    path: str,
    expected_version=None,
    require_releasable: bool = False,
    repo_root=None,
) -> list:
    """Return a list of error strings ([] means valid).

    ``expected_version`` is the authoritative cachetag version read from a
    cachetag checkout. Passing ``None`` runs every structural and identity
    check and skips only the cross-check itself, which is what the global,
    source-independent validation gate does: the cross-check needs a VMOD
    source checkout, so it belongs to that VMOD's own CI invocation rather than
    to a registry-wide gate that must never depend on one VMOD's repository.
    """
    errors: list = []
    _check(COHORT_SPEC, data, "", errors)
    if errors:
        return [f"{path}: {e}" for e in errors]

    status = data["status"]

    # required_vmods against the catalog, in both directions and for the same
    # reason the per-target evidence map is checked that way: a list of what a
    # cohort "must contain" that nobody compares to what is actually built for
    # it is a comment with a colon in it. A VMOD whose lanes build for this
    # cohort's engine on its targets is required by construction; one whose
    # lanes do not is not, and listing it would block every release on evidence
    # nothing produces.
    try:
        expected_required = set()
        for target_id in data["targets"]:
            expected_required.update(
                expected_vmods_for(data["engine"], target_id, repo_root)
            )
    except Exception as exc:  # noqa: BLE001 - a catalog problem must be legible
        errors.append(f"required_vmods: could not read the VMOD catalog to check this list ({exc})")
    else:
        declared = set(data["required_vmods"])
        for missing in sorted(expected_required - declared):
            errors.append(
                f"required_vmods: {missing!r} is missing. Its catalog lanes build a package "
                f"for engine {data['engine']} on this cohort's targets, so a release without "
                "it is incomplete by construction."
            )
        for extra in sorted(declared - expected_required):
            errors.append(
                f"required_vmods: {extra!r} has no catalog lane building for engine "
                f"{data['engine']} on this cohort's targets, so nothing can ever produce its "
                "evidence and every release would be blocked on it."
            )

    version = data["cachetag"]["version"]
    if expected_version is not None and version != expected_version:
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


def expected_vmods_for(engine: str, target_id: str, repo_root=None) -> list:
    """VMOD ids whose catalog lanes build a package for this engine and target.

    The catalog names engine inputs, and a cohort now records which one built
    it, so this is a lookup rather than an inference.

    Imported lazily on purpose: ci_matrix imports this module, so a top-level
    import here would be a cycle. The dependency is real but one-directional in
    time -- the catalog is read only when a target manifest is validated.
    """
    import ci_matrix  # noqa: PLC0415 - see the docstring

    engines = {engine}
    found: list = []
    for entry in ci_matrix.discover(repo_root):
        try:
            data = ci_matrix.load_vmod_manifest(Path(repo_root or REPO_ROOT) / entry["manifest"])
        except (OSError, yaml_subset.ManifestSyntaxError):
            # A malformed catalog entry is that VMOD's own failure and is
            # reported by ci_matrix. It must not make every target manifest in
            # the registry unvalidatable.
            continue
        for lane in data.get("lanes") or []:
            if lane.get("kind") != "package":
                continue
            if lane.get("engine") not in engines:
                continue
            if target_id in (lane.get("targets") or []):
                found.append(entry["id"])
                break
    return sorted(found)


def _vmod_evidence_errors(data: dict, path: str, engine: str = None, repo_root=None) -> list:
    """The per-VMOD evidence map against the catalog, in both directions.

    Missing entry: a VMOD this cohort and target must build has no evidence
    slot, so "the release is complete" could be true with its results absent.
    Extra entry: evidence exists for a VMOD nothing asked to be built here,
    which is either a stale record or a lane somebody forgot to declare.
    Neither is allowed to be a matter of opinion.
    """
    errors: list = []
    present = sorted(data["vmods"])
    if engine is None:
        # No cohort manifest was supplied, so there is nothing to look the
        # expected set up against. Skipped rather than guessed: the tree
        # validator always supplies one.
        expected = present
    else:
        try:
            expected = expected_vmods_for(engine, data["target"]["id"], repo_root)
        except Exception as exc:  # noqa: BLE001 - a catalog problem must be legible
            return [f"vmods: could not read the VMOD catalog to check this map ({exc})"]

    for missing in sorted(set(expected) - set(present)):
        errors.append(
            f"vmods: no entry for {missing!r}, whose catalog lanes build a package on "
            f"{engine} / {data['target']['id']}. Evidence may be 'pending' with a "
            "reason, but the entry has to exist: a cohort is releasable only when every "
            "selected VMOD's evidence is complete."
        )
    for extra in sorted(set(present) - set(expected)):
        errors.append(
            f"vmods.{extra}: no catalog lane builds {extra!r} on {engine} / "
            f"{data['target']['id']}. Either the lane is missing from registry/vmods/, or "
            "this evidence is stale and belongs to a lane that was removed."
        )

    for vmod_id in present:
        entry = data["vmods"][vmod_id]
        names = entry["package"]
        if entry["evidence"] == "pending" and not entry.get("pending_reason", "").strip():
            errors.append(
                f"vmods.{vmod_id}: evidence is 'pending' without a pending_reason. A reader "
                "should not have to reconstruct why from the surrounding zeros."
            )
        if entry["evidence"] == "recorded" and entry.get("pending_reason"):
            errors.append(
                f"vmods.{vmod_id}: evidence is 'recorded' but a pending_reason remains"
            )
        for field in ("source_name", "binary_name"):
            if not names[field].startswith(("lib", "vmod")):
                errors.append(
                    f"vmods.{vmod_id}.package.{field} is {names[field]!r}; a VMOD package name "
                    "starts with 'lib' or 'vmod' by this project's naming rule"
                )
    return errors


def validate_target(
    data: dict,
    path: str,
    cohort: dict = None,
    cohort_status: str = None,
    require_releasable: bool = False,
    distro_native: bool = False,
    expected_version: str = None,
    repo_root=None,
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

    if distro_native:
        if (
            data["package"]["source_name"] != PACKAGE_STEM
            or data["package"]["binary_name"] != PACKAGE_STEM
        ):
            errors.append(
                f"package.source_name and package.binary_name must both be {PACKAGE_STEM!r}"
            )
    else:
        errors.extend(
            _vmod_evidence_errors(
                data, path, cohort["engine"] if cohort else None, repo_root
            )
        )

    if distro_native and expected_version is not None:
        if data["cachetag"]["version"] != expected_version:
            errors.append(
                "cachetag.version {!r} does not match configure.ac AC_INIT {!r}".format(
                    data["cachetag"]["version"], expected_version
                )
            )

    if not distro_native and cohort is not None:
        entry = data["vmods"].get("cachetag")
        if entry and entry["package"]["upstream_version"] != cohort["cachetag"]["version"]:
            errors.append(
                "vmods.cachetag.package.upstream_version {!r} does not match the cohort's "
                "cachetag.version {!r}".format(
                    entry["package"]["upstream_version"], cohort["cachetag"]["version"]
                )
            )
        if data["cohort"] != cohort["cohort"]:
            errors.append(f"cohort {data['cohort']!r} does not match its cohort manifest {cohort['cohort']!r}")
        if target["id"] not in cohort["targets"]:
            errors.append(f"target.id {target['id']!r} is not listed in the cohort manifest targets")
        if cohort_status is not None and data["status"] != cohort_status:
            errors.append(f"status {data['status']!r} does not match the cohort status {cohort_status!r}")

    placeholders: list = []
    _collect_placeholders(data, "", placeholders)
    # A `pending` VMOD entry is placeholder by definition: its lanes have not
    # run, so there is no configure line, no flags and no epoch to record. That
    # is not a template masquerading as a candidate -- the entry says so in
    # words, and --require-releasable rejects `pending` by name below, which is
    # the check that actually protects a release. Exempting the entry keeps the
    # candidate cohort honest about the VMOD it has not built yet instead of
    # forcing a choice between a lie and a missing entry.
    if not distro_native:
        pending = tuple(
            f"vmods.{k}."
            for k, v in data["vmods"].items()
            if v.get("evidence") == "pending"
        )
        placeholders = [p for p in placeholders if not p.startswith(pending)]
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
        for label, block in _evidence_blocks(data, distro_native):
            if block["build"]["profile"] != "production":
                errors.append(f"{label}build.profile must be 'production' for a releasable target")
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
            # Every VMOD's evidence, held to the same policy. This loop is the
            # mechanical form of the Step 6 exit gate's "both package families
            # meet the same evidence policy as cachetag": it does not know
            # which VMOD is which, so it cannot hold one to a weaker standard.
            for label, block in _evidence_blocks(data, distro_native):
                if block.get("evidence") == "pending":
                    errors.append(
                        f"{label}evidence is 'pending' ({block.get('pending_reason', '')!r}); "
                        "a releasable target needs recorded evidence for every selected VMOD"
                    )
                for key, value in sorted(block["tests"].items()):
                    if value not in ("pass", "not-applicable"):
                        errors.append(f"{label}tests.{key} is {value!r}; a releasable target needs 'pass'")
                if block["build"]["hardening_check"] not in ("pass", "not-applicable"):
                    errors.append(f"{label}build.hardening_check must be 'pass' for a releasable target")
                if not block["artifacts"]:
                    errors.append(f"{label}artifacts: a releasable target needs at least one artifact digest")
    return [f"{path}: {e}" for e in errors]


def _evidence_blocks(data: dict, distro_native: bool) -> list:
    """(label, block) for every evidence block in a target manifest.

    One block on the distro-native lane, which has no cohort and therefore no
    VMOD set to enumerate; one per entry in `vmods:` on the cohort lane. The
    label is a path prefix so a message names the VMOD it is about.
    """
    if distro_native:
        return [("", data)]
    return [(f"vmods.{k}.", data["vmods"][k]) for k in sorted(data["vmods"])]


def registry_dir(repo_root: Path = None) -> Path:
    root = Path(repo_root) if repo_root else REPO_ROOT
    return root / "registry"


def validate_registry_tree(
    repo_root: Path = None,
    only_cohort: str = None,
    require_releasable: bool = False,
    cachetag_src=None,
    cross_check_cachetag: bool = True,
) -> tuple:
    """Validate every manifest in registry/. Returns (checked_paths, errors).

    With ``cross_check_cachetag=False`` this is pure structural and identity
    validation: schemas, cohort-input digests, target wiring, placeholder
    policy. It needs no VMOD source checkout and therefore cannot be broken by
    one VMOD's repository being unreachable. The cachetag ``configure.ac``
    cross-check is a source-coupled check and runs inside the cachetag CI
    invocation after its checkout (see tools/ci_matrix.py's ``validate-vmod
    --source-dir``).
    """
    root = Path(repo_root) if repo_root else REPO_ROOT
    rel = registry_dir(root)
    expected_version = (
        configure_ac_version(cachetag_src, repo_root=root) if cross_check_cachetag else None
    )
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
        cohort_errors = validate_cohort(
            cohort, str(rel_path), expected_version, cohort_releasable, repo_root=root
        )
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
                    repo_root=root,
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
