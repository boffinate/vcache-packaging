#!/usr/bin/env python3
"""Verify the native metadata and VMOD payload of a finished package.

The package manager remains authoritative: this tool reads .deb/.rpm metadata
with native tooling, compares the exact engine dependency with the engine that
is installed in the build container, and normalizes the VMOD shared-object
payload into the same path vocabulary for both formats.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath


class ContractError(Exception):
    pass


def run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractError(f"command failed ({' '.join(command)}): {exc}") from exc
    return result.stdout


def deb_fields(package: Path) -> dict:
    output = run([
        "dpkg-deb", "-W", "--showformat=${Package}\\t${Architecture}\\t${Version}\\t${Depends}",
        str(package),
    ])
    values = output.split("\t", 3)
    if len(values) != 4 or not all(values[:3]):
        raise ContractError(f"incomplete Debian metadata in {package.name}")
    return dict(zip(("Package", "Architecture", "Version", "Depends"), values))


def rpm_fields(package: Path) -> dict:
    output = run([
        "rpm", "-qp", "--qf", "%{NAME}\\n%{ARCH}\\n%{VERSION}-%{RELEASE}\\n", str(package)
    ]).splitlines()
    if len(output) != 3:
        raise ContractError(f"incomplete RPM metadata in {package.name}")
    return dict(zip(("Package", "Architecture", "Version"), output))


def native_fields(package: Path) -> dict:
    if package.suffix == ".deb":
        return deb_fields(package)
    if package.suffix == ".rpm":
        return rpm_fields(package)
    raise ContractError(f"unsupported package suffix: {package}")


def deb_payload(package: Path) -> list[str]:
    try:
        process = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(package)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ContractError(f"cannot inspect Debian payload {package.name}: {exc}") from exc
    assert process.stdout is not None
    paths = []
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if member.isfile() or member.issym() or member.islnk():
                    paths.append(member.name)
    except tarfile.TarError as exc:
        process.kill()
        raise ContractError(f"invalid Debian payload {package.name}: {exc}") from exc
    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    if process.wait() != 0:
        raise ContractError(f"dpkg-deb could not read {package.name}: {stderr.strip()}")
    return paths


def rpm_payload(package: Path) -> list[str]:
    return run(["rpm", "-qpl", str(package)]).splitlines()


def normalize_path(value: str) -> str:
    path = value.strip()
    while path.startswith("./"):
        path = path[2:]
    return "/" + str(PurePosixPath(path)).lstrip("/")


def normalized_vmod_payload(paths: list[str]) -> list[str]:
    """Return every conventional VMOD .so path, normalized and sorted."""
    return sorted({normalize_path(path) for path in paths
                   if re.match(r"^libvmod_[A-Za-z0-9_]+\.so$", PurePosixPath(path).name)})


def expected_vmod_payload(vmod_dir: str, modules: list[str]) -> list[str]:
    root = normalize_path(vmod_dir).rstrip("/")
    return sorted(f"{root}/libvmod_{module}.so" for module in modules)


def deb_exact_dependency(depends: str, engine: str, version: str) -> bool:
    pattern = rf"(?:^|,\s*){re.escape(engine)}\s*\(\s*=\s*{re.escape(version)}\s*\)(?:\s*,|$)"
    return re.search(pattern, depends) is not None


def installed_engine_requirement(package_format: str, engine: str) -> tuple[str, str]:
    if package_format == "deb":
        version = run(["dpkg-query", "-W", "-f=${Version}", engine]).strip()
        return version, f"{engine} (= {version})"
    version = run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", engine]).strip()
    isa = run(["rpm", "--eval", "%{?_isa}"]).strip()
    return version, f"{engine}{isa} = {version}"


def verify_vmod(package_format: str, package: Path, expected_name: str,
                expected_arch: str, engine: str, vmod_dir: str,
                modules: list[str]) -> dict:
    if package_format == "deb":
        metadata = deb_fields(package)
        payload = deb_payload(package)
        engine_version, requirement = installed_engine_requirement(package_format, engine)
        if not deb_exact_dependency(metadata.get("Depends", ""), engine, engine_version):
            raise ContractError(
                f"{package.name}: missing exact dependency {requirement!r}; "
                f"Depends is {metadata.get('Depends', '')!r}"
            )
    elif package_format == "rpm":
        metadata = rpm_fields(package)
        payload = rpm_payload(package)
        _, requirement = installed_engine_requirement(package_format, engine)
        requires = run(["rpm", "-qp", "--requires", str(package)]).splitlines()
        if requirement not in requires:
            raise ContractError(
                f"{package.name}: missing exact dependency {requirement!r}; "
                f"native Requires are {requires!r}"
            )
    else:
        raise ContractError(f"unsupported package format {package_format!r}")

    if metadata["Package"] != expected_name:
        raise ContractError(
            f"{package.name}: package identity {metadata['Package']!r}, expected {expected_name!r}"
        )
    if metadata["Architecture"] != expected_arch:
        raise ContractError(
            f"{package.name}: architecture {metadata['Architecture']!r}, expected {expected_arch!r}"
        )
    actual_payload = normalized_vmod_payload(payload)
    expected_payload = expected_vmod_payload(vmod_dir, modules)
    if actual_payload != expected_payload:
        raise ContractError(
            f"{package.name}: normalized VMOD payload {actual_payload!r}, expected {expected_payload!r}"
        )
    return {
        "schema": "package-contract/1",
        "file": package.name,
        "package": metadata["Package"],
        "version": metadata["Version"],
        "architecture": metadata["Architecture"],
        "engine_requirement": requirement,
        "vmod_payload": actual_payload,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--format", required=True, choices=("deb", "rpm"))
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--engine-package", required=True)
    parser.add_argument("--vmod-dir", required=True)
    parser.add_argument("--modules", nargs="+", required=True)
    parser.add_argument("--manifest-out", type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = verify_vmod(args.format, args.package, args.name, args.arch,
                               args.engine_package, args.vmod_dir, args.modules)
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    output = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.manifest_out:
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_out.write_text(output)
    print(output, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
