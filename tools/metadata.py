"""Generate native package version metadata from cachetag release manifests.

Every native version string, package revision, artifact filename, and ABI
dependency expression used by a packaging recipe is derived here from the
cohort and target manifests. Recipes must not hand-edit these values.

Standard library only.
"""

from __future__ import annotations

import re

__all__ = ["package_versions", "target_metadata", "abi_expressions", "as_shell"]

# Native archive/package extension per package format.
_EXTENSION = {
    "deb": "deb",
    "rpm": "rpm",
    "arch": "pkg.tar.zst",
    "freebsd": "pkg",
    "apk": "apk",
}

# Ecosystems whose first packaging of an upstream version is revision 1 vs 0.
# The manifest always stores the canonical revision, which starts at 1.
_ZERO_BASED = {"freebsd", "apk"}


def package_versions(version: str, revision: int, dist_tag: str = "") -> dict:
    """Map one canonical package revision onto every supported ecosystem.

    ``revision`` is the canonical manifest revision and starts at 1. Debian,
    RPM, and Arch use it directly; FreeBSD PORTREVISION and Alpine pkgrel start
    at 0 and therefore use ``revision - 1``.
    """
    if revision < 1:
        raise ValueError("package revision starts at 1")
    zero_based = revision - 1
    rpm_release = f"{revision}.{dist_tag}" if dist_tag else str(revision)
    return {
        "debian": {
            "version": f"{version}-{revision}",
            "upstream_version": version,
            "debian_revision": str(revision),
        },
        "rpm": {
            "version": version,
            "release": rpm_release,
            "dist_tag": dist_tag,
        },
        "arch": {
            "pkgver": version,
            "pkgrel": str(revision),
        },
        "freebsd": {
            "portversion": version,
            "portrevision": str(zero_based),
            "pkg_version": version if zero_based == 0 else f"{version}_{zero_based}",
        },
        "alpine": {
            "pkgver": version,
            "pkgrel": str(zero_based),
        },
    }


def _native_filename(
    fmt: str, version: str, revision: int, arch: str, versions: dict, stem: str
) -> str:
    if fmt == "deb":
        return f"{stem}_{versions['debian']['version']}_{arch}.deb"
    if fmt == "rpm":
        return f"{stem}-{version}-{versions['rpm']['release']}.{arch}.rpm"
    if fmt == "arch":
        return f"{stem}-{version}-{versions['arch']['pkgrel']}-{arch}.pkg.tar.zst"
    if fmt == "freebsd":
        return f"{stem}-{versions['freebsd']['pkg_version']}.pkg"
    if fmt == "apk":
        return f"{stem}-{version}-r{versions['alpine']['pkgrel']}.apk"
    raise ValueError(f"unknown package format {fmt!r}")


def _source_filenames(fmt: str, version: str, versions: dict, stem: str) -> list:
    if fmt == "deb":
        return [
            f"{stem}_{version}.orig.tar.gz",
            f"{stem}_{versions['debian']['version']}.debian.tar.xz",
            f"{stem}_{versions['debian']['version']}.dsc",
        ]
    if fmt == "rpm":
        return [f"{stem}-{version}-{versions['rpm']['release']}.src.rpm"]
    return []


def abi_expressions(
    *, vrt: str, strict_abi: str, exact_package: str = None, cohort_id: str = None
) -> dict:
    """The native ABI dependency expressions for one engine row.

    Public because generated VMOD recipes need exactly these strings and must
    not carry a second implementation of the policy. ``tools/vmod_recipe.py``
    calls this; if the rules change, both cachetag's metadata and every
    generated recipe change together, which is the only way a generated recipe
    can be prevented from silently weakening them.
    """
    return _abi_strings(vrt, strict_abi, exact_package, cohort_id)


def _abi_strings(
    vrt: str, strict_abi: str, exact_package: str = None, cohort_id: str = None
) -> dict:
    abi_provide = f"vinyld-abi-{strict_abi}"
    vrt_provide = f"vinyld-vrt = {vrt}"
    deb_depends = [abi_provide, f"vinyld-vrt (= {vrt})"]
    arch_depends = [abi_provide]

    # RPM does not use the Debian virtual-package names. recipes/el9/find-provides
    # injects arch-qualified capability provides on the Vinyl runtime package --
    #     vinyld(abi)%{?_isa} = <hash>
    #     vinyld(vrt)%{?_isa} = <major.minor>
    #     vinyld(cohort-<id>)%{?_isa}
    # -- and cachetag's audited spec depends on exactly those. Until 2026-07-28
    # this function emitted the Debian names on the RPM side too. Nothing
    # consumed the value, so nothing broke; the EL9 lane substitutes the tokens
    # into the spec directly. That stopped being harmless when generated VMOD
    # recipes started rendering Requires from here, because a dependency on
    # `vinyld-abi-<hash>` is unsatisfiable on a target where the provide is
    # named `vinyld(abi)`. Fixed in the one authoritative place rather than
    # worked around in the generator.
    rpm_requires = [
        f"vinyld(abi)%{{?_isa}} = {strict_abi}",
        f"vinyld(vrt)%{{?_isa}} = {vrt}",
    ]

    # The cohort-qualified provide. The ABI token is a hash of the upstream
    # source revision, so a repackaged, patched or vendor-respun runtime built
    # from that revision advertises the identical token; the step-9 transaction
    # matrices confirmed both apt and dnf accept such a package silently. The
    # cohort id is a digest over the pinned source archive, the ordered patch
    # series and the production build profile, so it is the value that can
    # answer the provenance question.
    #
    # It goes in the provide NAME, not its version, on every ecosystem: a cohort
    # id contains hyphens, which RPM does not permit in an EVR. Keeping the same
    # shape on the Debian side is a deliberate symmetry, not an accident.
    #
    # The distro-native lane has no cohort identity and therefore never gets
    # one; its equivalent guard is the exact binary package version dependency
    # below.
    cohort_provide = None
    rpm_cohort_provide = None
    if cohort_id:
        cohort_provide = f"vinyld-cohort-{cohort_id}"
        rpm_cohort_provide = f"vinyld(cohort-{cohort_id})"
        deb_depends.append(cohort_provide)
        rpm_requires.append(rpm_cohort_provide + "%{?_isa}")
        arch_depends.append(cohort_provide)

    if exact_package:
        deb_depends.append(exact_package)
        # "vinyl-cache (= 9.0.0-3)" -> "vinyl-cache%{?_isa} = 9.0.0-3"
        name, _, version = exact_package.partition(" (= ")
        rpm_requires.append("{}%{{?_isa}} = {}".format(name, version.rstrip(")")))
    return {
        "abi_provide": abi_provide,
        "vrt_provide": vrt_provide,
        "cohort_provide": cohort_provide or "",
        "rpm_cohort_provide": rpm_cohort_provide or "",
        "deb_depends": ", ".join(deb_depends),
        "rpm_requires": rpm_requires,
        "arch_depends": arch_depends,
        "freebsd_run_depends": list(arch_depends),
        "alpine_depends": list(arch_depends),
    }


def target_metadata(target: dict, cohort: dict = None, vmod: str = "cachetag") -> dict:
    """Build the complete generated metadata block for one target manifest.

    ``vmod`` selects the entry in a cohort target's ``vmods:`` map. It defaults
    to ``cachetag`` because every existing caller -- the release manifest
    assembler and the lane pin cross-checks -- asks about cachetag, and the
    v1-to-v2 schema migration must not move a byte of what they read. The
    distro-native lane has no map: it is bound to one distribution package
    revision and cachetag is its only VMOD by construction.
    """
    lane = target["lane"]
    tgt = target["target"]
    fmt = tgt["package_format"]
    arch = tgt["arch"]

    if lane == "cohort":
        if cohort is None:
            raise ValueError("a cohort-lane target needs its cohort manifest")
        entry = target["vmods"].get(vmod)
        if entry is None:
            raise ValueError(
                "{} records no evidence for VMOD {!r}; it has entries for {}".format(
                    tgt["id"], vmod, sorted(target["vmods"])
                )
            )
        package = entry["package"]
        install = target["install"]
        version = package["upstream_version"]
        vrt = cohort["vinyl"]["vrt"]
        strict_abi = cohort["vinyl"]["strict_abi"]
        cohort_id = cohort["cohort"]
        exact_package = None
        origin = {"kind": "cohort", "cohort": cohort_id}
    else:
        package = target["package"]
        install = target["install"]
        version = target["cachetag"]["version"]
        distro_vinyl = target["distro_vinyl"]
        vrt = distro_vinyl["vrt"]
        strict_abi = distro_vinyl["strict_abi"]
        cohort_id = None
        exact_package = None
        if distro_vinyl["exposes_abi_provide"] != "yes":
            exact_package = "{} (= {})".format(
                distro_vinyl["runtime_package"], distro_vinyl["binary_package_version"]
            )
        origin = {
            "kind": "distro-native",
            "repository_origin": distro_vinyl["repository_origin"],
            "vinyl_binary_package_version": distro_vinyl["binary_package_version"],
        }

    revision = int(package["revision"])
    stem = package["binary_name"]
    source_stem = package["source_name"]
    versions = package_versions(version, revision, tgt["dist_tag"])
    native_filename = _native_filename(fmt, version, revision, arch, versions, stem)
    extension = _EXTENSION[fmt]
    release_asset = "{}-{}-{}-{}-{}.{}".format(
        stem, version, revision, tgt["distro_id"], arch, extension
    )

    return {
        "schema": "cachetag-package-metadata/v1",
        "lane": lane,
        "origin": origin,
        "target": tgt["id"],
        "distro": tgt["distro"],
        "distro_release": tgt["distro_release"],
        "distro_id": tgt["distro_id"],
        "arch": arch,
        "package_format": fmt,
        "vmod": vmod if lane == "cohort" else "cachetag",
        "cachetag_version": version,
        "package_revision": revision,
        "source_archive": f"{source_stem}-{version}.tar.gz",
        "versions": versions,
        "native": {
            "format": fmt,
            "version_fields": versions[_FORMAT_TO_ECOSYSTEM[fmt]],
        },
        "artifacts": {
            "native_filename": native_filename,
            "release_asset_filename": release_asset,
            "source_package_filenames": _source_filenames(fmt, version, versions, source_stem),
        },
        "abi": _abi_strings(vrt, strict_abi, exact_package, cohort_id),
        "vinyl": {"vrt": vrt, "strict_abi": strict_abi},
        "install": {
            "vmoddir": install["vmoddir"],
            "vmoddir_source": install["vmoddir_source"],
        },
        # Tokens that packaging recipes substitute into their templates.
        "substitutions": {
            "@VINYL_VMODDIR@": install["vmoddir"],
        },
    }


_FORMAT_TO_ECOSYSTEM = {
    "deb": "debian",
    "rpm": "rpm",
    "arch": "arch",
    "freebsd": "freebsd",
    "apk": "alpine",
}


def _flatten(prefix: str, value, out: list) -> None:
    if isinstance(value, dict):
        for key, sub in value.items():
            _flatten(f"{prefix}_{key}" if prefix else key, sub, out)
    elif isinstance(value, list):
        # A list becomes a count plus indexed variables. Joining with a
        # separator would corrupt values that legitimately contain spaces, such
        # as the RPM requirement "vinyld-vrt = 23.0".
        out.append((f"{prefix}_count", str(len(value))))
        for index, item in enumerate(value):
            _flatten(f"{prefix}_{index}", item, out)
    else:
        out.append((prefix, str(value)))


def as_shell(metadata: dict) -> str:
    """Render metadata as CACHETAG_* shell assignments for packaging recipes.

    Scalars become single variables. Lists become ``<name>_COUNT`` plus
    ``<name>_0``, ``<name>_1``, ... so that values containing spaces survive.
    Every value is single-quoted and safe to ``eval`` in POSIX sh, and every
    name is a valid POSIX shell identifier: characters that cannot appear in
    one - such as the ``@`` in substitution tokens like ``@VINYL_VMODDIR@`` -
    are folded to underscores.
    """
    flat: list = []
    _flatten("", metadata, flat)
    lines = []
    for key, value in flat:
        name = re.sub(r"[^A-Za-z0-9_]", "_", key.upper())
        if not name.startswith("CACHETAG_"):
            name = "CACHETAG_" + name
        escaped = value.replace("'", "'\\''")
        lines.append(f"{name}='{escaped}'")
    return "\n".join(lines) + "\n"
