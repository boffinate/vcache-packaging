#!/usr/bin/env python3
"""Render a VMOD's Debian source dir or RPM spec from packaging/ templates.

One generic template set (``packaging/debian/*.in``, ``packaging/rpm/*.in``)
plus one VMOD's catalog entry and one engine's version strings produce the
whole recipe. Substitution is ``@TOKEN@`` replacement and nothing else; an
unresolved token fails loudly. Naming, versioning, and the exact-version
engine dependency follow DESIGN.md:

  * binary package ``<family>-vmod-<id>`` on both formats, with identifier
    underscores rendered as hyphens for native package-name validity;
  * Debian version ``<upstream>-1~<family><engine>.<package-revision>``, RPM
    release ``1.<family><engine>.<package-revision>%{?dist}``;
  * exact dependency on the selected family's runtime package -- the RPM side
    needs ``%{?_isa}`` because a dlopen()ed plugin must match the daemon's
    architecture exactly and multilib would otherwise let an i686 engine
    satisfy an x86_64 VMOD; Debian encodes that in the package architecture.

Templates live next to this tool (they are code, not catalog data), so a
``--root`` pointing at a fixture catalog still renders the real templates.

The source archive contract for the build scripts: both recipes unpack
``<package>-<version>/`` from ``<package>-<version>.tar.gz`` (Debian:
``<package>_<version>.orig.tar.gz``), which ``git archive
--prefix=<package>-<version>/`` produces from the resolved ref.  For RPM,
``<version>`` is the RPM-safe spelling returned by
``matrix.vmod_package_version``.

Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matrix  # noqa: E402

DEFAULT_MAINTAINER_NAME = "Vinyl Cache Packaging"
DEFAULT_MAINTAINER_EMAIL = "peter@mapledesign.co.uk"

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "packaging"

TOKEN_RE = re.compile(r"@([A-Z][A-Z0-9_]*)@")

# Template file -> rendered path inside the output directory.
DEB_TEMPLATES = {
    "control.in": "debian/control",
    "rules.in": "debian/rules",
    "changelog.in": "debian/changelog",
    "copyright.in": "debian/copyright",
    "source-format.in": "debian/source/format",
}
DEB_CLEAN_BACKUP = ("control", "changelog", "copyright", "source/format")
RPM_TEMPLATE = "vmod.spec.in"

# The implied build-dependency set every VMOD gets; the manifest's
# package.build_deps adds to it, never replaces it.
DEB_BASE_BUILD_DEPS = ["debhelper-compat (= 13)", "autoconf", "automake", "libtool", "pkgconf"]
RPM_BASE_BUILD_REQS = ["autoconf", "automake", "libtool", "make", "gcc", "pkgconfig"]
DEB_CARGO_BUILD_DEPS = ["clang", "libclang-dev"]
RPM_CARGO_BUILD_REQS = ["clang", "clang-devel"]


# One lookup, owned by matrix.py; re-exported for existing callers.
target_format = matrix.target_format


def maintainer_from_env() -> tuple:
    return (
        os.environ.get("MAINTAINER_NAME") or DEFAULT_MAINTAINER_NAME,
        os.environ.get("MAINTAINER_EMAIL") or DEFAULT_MAINTAINER_EMAIL,
    )


def deb_description(lines: list) -> str:
    """The catalog's description lines as a debian/control extended description."""
    out = []
    for line in lines:
        out.append(" ." if line.strip() == "" else f" {line}")
    return "\n".join(out)


def render_text(template_name: str, text: str, tokens: dict) -> str:
    missing: list = []

    def _sub(match):
        token = match.group(1)
        if token not in tokens:
            missing.append(token)
            return match.group(0)
        return str(tokens[token])

    rendered = TOKEN_RE.sub(_sub, text)
    if missing:
        raise matrix.CatalogError(
            f"{template_name}: unresolved template token(s): {', '.join(sorted(set(missing)))}"
        )
    return rendered


def build_tokens(vmod: dict, engine: dict, maintainer: tuple, now: datetime) -> dict:
    resolved = matrix.resolve_source(vmod, engine)
    if not resolved["version"]:
        raise matrix.CatalogError(
            f"vmod {vmod['id']!r} resolves to branch {resolved['ref']!r} with no upstream version "
            f"against engine {engine['id']!r}; packages are built from release engines only"
        )
    package = vmod["package"]
    pv = matrix.vmod_package_version(resolved["version"], engine)
    epv = matrix.engine_package_version(engine)
    build_deps = package.get("build_deps") or {}
    runtime_package = matrix.engine_runtime_package(engine)
    deb_development_package = matrix.engine_development_package(engine, "deb")
    rpm_development_package = matrix.engine_development_package(engine, "rpm")
    build = matrix.vmod_build(vmod)
    deb_base = DEB_BASE_BUILD_DEPS + (DEB_CARGO_BUILD_DEPS if build == "cargo" else [])
    rpm_base = RPM_BASE_BUILD_REQS + (RPM_CARGO_BUILD_REQS if build == "cargo" else [])
    deb_deps = deb_base + [f"{deb_development_package} (= {epv['deb']})"] + list(build_deps.get("debian", []))
    rpm_reqs = rpm_base + [f"{rpm_development_package} = {epv['rpm']}"] + list(build_deps.get("rpm", []))
    modules = matrix.vmod_modules(vmod)
    artifacts = matrix.vmod_artifacts(vmod)
    cargo_features = matrix.vmod_cargo_features(vmod)
    configure_args = matrix.vmod_configure_args(vmod)
    return {
        "PACKAGE_NAME": matrix.engine_vmod_package_name(engine, vmod["id"]),
        "VMOD_ID": vmod["id"],
        "SUMMARY": package["summary"],
        "DEB_DESCRIPTION": deb_description(package["description"]),
        "RPM_DESCRIPTION": "\n".join(package["description"]),
        "LICENSE": package["license"],
        "HOMEPAGE": vmod["upstream"].get("homepage") or vmod["upstream"]["git"],
        "UPSTREAM_GIT": vmod["upstream"]["git"],
        "VMOD_REF": resolved["ref"],
        "VMOD_VERSION": resolved["version"],
        "DEB_VERSION": pv["deb"],
        "RPM_VERSION": pv["rpm_version"],
        "RPM_RELEASE": pv["rpm_release"],
        "ENGINE_ID": engine["id"],
        "ENGINE_VERSION": matrix.engine_version(engine),
        "ENGINE_DISPLAY_NAME": matrix.engine_display_name(engine),
        "ENGINE_RUNTIME_PACKAGE": runtime_package,
        "ENGINE_API": matrix.engine_api(engine),
        "VMOD_DIR_COMPONENT": matrix.engine_vmod_dir_component(engine),
        "ENGINE_DEB_PKG_VERSION": epv["deb"],
        "ENGINE_RPM_PKG_VERSION": epv["rpm"],
        "DEB_BUILD_DEPS": ", ".join(deb_deps),
        "RPM_BUILD_REQUIRES": "\n".join(f"BuildRequires:  {req}" for req in rpm_reqs),
        "VMOD_BUILD": build,
        "CARGO_BUILD": "1" if build == "cargo" else "0",
        "CARGO_ARTIFACT_ARGS": " ".join(f"--mapping {module}={artifact}" for module, artifact in zip(modules, artifacts)),
        "CARGO_FEATURE_ARGS": f"--features {','.join(cargo_features)}" if cargo_features else "",
        "VMOD_MODULES": " ".join(modules),
        "BUILD_TARGET": package.get("build_target", "all"),
        "INSTALL_TARGET": package.get("install_target", "install"),
        "CONFIGURE_ARGS": " ".join(configure_args),
        "MAINTAINER_NAME": maintainer[0],
        "MAINTAINER_EMAIL": maintainer[1],
        "DEB_DATE": format_datetime(now),
        "RPM_DATE": now.strftime("%a %b %d %Y"),
    }


def generate(root, vmod_id: str, engine_id: str, target_id: str, out_dir, maintainer: tuple = None,
             now: datetime = None) -> list:
    """Render the recipe for one (vmod, engine, target); returns written paths."""
    catalog = matrix.load_catalog(root)
    engine = matrix.find_engine(catalog, engine_id)
    if engine["packages"] != "true":
        raise matrix.CatalogError(
            f"engine {engine_id!r} does not ship packages (packages: \"{engine['packages']}\"); "
            "recipes exist only for package engines"
        )
    if target_id not in engine["targets"]:
        raise matrix.CatalogError(
            f"target {target_id!r} is not a target of engine {engine_id!r} (targets: {engine['targets']})"
        )
    vmod = matrix.find_vmod(catalog, vmod_id)
    fmt = target_format(catalog, target_id)
    tokens = build_tokens(vmod, engine, maintainer or maintainer_from_env(), now or datetime.now(timezone.utc))
    out = Path(out_dir)
    written = []
    if fmt == "deb":
        for template_name, rel_path in DEB_TEMPLATES.items():
            text = (TEMPLATE_DIR / "debian" / template_name).read_text(encoding="utf-8")
            path = out / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_text(template_name, text, tokens), encoding="utf-8")
            written.append(path)
        os.chmod(out / "debian" / "rules", 0o755)
        # Some Autotools projects generate debian/* from configure.ac. Their
        # distclean removes those active files before Debhelper has finished
        # its clean sequence, so keep the generated package metadata in a
        # private directory that upstream build rules do not own.
        backup = out / "debian" / ".vcache-packaging"
        for rel_path in DEB_CLEAN_BACKUP:
            source = out / "debian" / rel_path
            target = backup / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        text = (TEMPLATE_DIR / "rpm" / RPM_TEMPLATE).read_text(encoding="utf-8")
        path = out / f"{tokens['PACKAGE_NAME']}.spec"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_text(RPM_TEMPLATE, text, tokens), encoding="utf-8")
        written.append(path)
    return written


def cmd_generate(args) -> int:
    written = generate(args.root, args.vmod, args.engine, args.target, args.out)
    for path in written:
        print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recipe.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("generate", help="render one VMOD's packaging recipe")
    p.add_argument("--vmod", required=True)
    p.add_argument("--engine", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=matrix.default_root(), help="repo root holding engines.yml and vmods/")
    p.set_defaults(func=cmd_generate)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except matrix.CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
