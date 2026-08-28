#!/usr/bin/env python3
"""Normalize an Autotools VMOD source tree for another engine family."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path


FAMILIES = ("vinyl", "varnish")

VINYL_TO_VARNISH = (
    (b"cache/cache_vinyld.h", b"cache/cache_varnishd.h"),
    (b"cache_vinyld.h", b"cache_varnishd.h"),
    (b"vinyl_vsc", b"varnish_vsc"),
    (b"VINYLAPI", b"VARNISHAPI"),
    (b"VINYLSRC", b"VARNISHSRC"),
    (b"VINYLTEST", b"VARNISHTEST"),
    (b"VINYLD", b"VARNISHD"),
    (b"VINYL_", b"VARNISH_"),
    (b"vinylapi", b"varnishapi"),
    (b"vinyltest", b"varnishtest"),
    (b"vinyld", b"varnishd"),
    (b"vinyladm", b"varnishadm"),
    (b"vinylstat", b"varnishstat"),
    (b"vinyl-cache/", b"varnish/"),
    (b"/vinyl/", b"/varnish/"),
)


def replacements(source_family: str, target_family: str) -> tuple[tuple[bytes, bytes], ...]:
    if source_family == target_family:
        return ()
    if (source_family, target_family) == ("vinyl", "varnish"):
        return VINYL_TO_VARNISH
    if (source_family, target_family) == ("varnish", "vinyl"):
        return tuple((new, old) for old, new in VINYL_TO_VARNISH)
    raise ValueError(f"unsupported family normalization: {source_family} -> {target_family}")


def normalize_bytes(data: bytes, path: Path, source_family: str, target_family: str) -> tuple[bytes, Counter]:
    counts = Counter()
    for old, new in replacements(source_family, target_family):
        if path.suffix == ".vtc" and (old, new) in (
            (b"vinyltest", b"varnishtest"),
            (b"varnishtest", b"vinyltest"),
        ):
            continue
        count = data.count(old)
        if count:
            data = data.replace(old, new)
            counts[f"{old.decode()} -> {new.decode()}"] += count

    if path.suffix == ".vtc":
        command = re.compile(rb"(?<![A-Za-z0-9_])" + source_family.encode() + rb"(?![A-Za-z0-9_])")
        data, count = command.subn(target_family.encode(), data)
        if count:
            counts[f"VTC command {source_family} -> {target_family}"] += count
        data, count = re.subn(rb"(?m)^vinyltest(?=\s|$)", b"varnishtest", data)
        if count:
            counts["VTC header vinyltest -> varnishtest"] += count
    return data, counts


def normalize_tree(root: Path, source_family: str, target_family: str) -> tuple[list[tuple[Path, Counter]], Counter]:
    changed = []
    totals = Counter()
    for directory, names, filenames in os.walk(root):
        names[:] = sorted(name for name in names if name != ".git")
        for filename in sorted(filenames):
            path = Path(directory, filename)
            if path.is_symlink():
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue
            normalized, counts = normalize_bytes(data, path, source_family, target_family)
            if not counts:
                continue
            path.write_bytes(normalized)
            changed.append((path.relative_to(root), counts))
            totals.update(counts)
    return changed, totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-family", required=True, choices=FAMILIES)
    parser.add_argument("--target-family", required=True, choices=FAMILIES)
    parser.add_argument("source", type=Path)
    args = parser.parse_args(argv)

    if args.source_family == args.target_family:
        print("source and target API families are identical; nothing to normalize")
        return 0
    if not args.source.is_dir():
        parser.error(f"source tree is not a directory: {args.source}")

    changed, totals = normalize_tree(args.source, args.source_family, args.target_family)
    if not changed:
        print(
            f"no {args.source_family} API spellings found while normalizing for {args.target_family}",
            file=sys.stderr,
        )
        return 1

    print(f"normalized {args.source_family} VMOD source for {args.target_family} in {len(changed)} files")
    for path, counts in changed:
        detail = ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))
        print(f"  {path}: {detail}")
    print("totals:")
    for name, count in sorted(totals.items()):
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
