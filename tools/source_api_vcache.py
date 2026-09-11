#!/usr/bin/env python3
"""Convert a family-specific Autotools VMOD tree to the VCACHE API names."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path


MARKER = "vcache-api"
PREREQ = re.compile(rb"(?m)^(?P<indent>[ \t]*)(?:VARNISH|VINYL)_PREREQ\((?P<versions>[^\r\n]*)\)[ \t]*$")
PREFIX = re.compile(rb"(?:VARNISH|VINYL)(API)?_")
VTC_HEADER = re.compile(rb"(?m)^(?:varnish|vinyl)test(?=\s|$)")
VTC_COMMAND = re.compile(rb"(?m)^(?:varnish|vinyl)(?=\s)")
VTC_COMPILER = re.compile(rb"(?m)^(?P<line>[^\r\n]*VTC_LOG_COMPILER[^\r\n]*)$")
PRIVATE_HEADER = re.compile(rb"cache/cache_(?:varnish|vinyl)d\.h")


def _counted_sub(pattern: re.Pattern[bytes], replacement, data: bytes, label: str,
                 counts: Counter[str]) -> bytes:
    data, count = pattern.subn(replacement, data)
    if count:
        counts[label] += count
    return data


def convert_bytes(data: bytes, path: Path) -> tuple[bytes, Counter[str]]:
    counts: Counter[str] = Counter()

    def prereq(match: re.Match[bytes]) -> bytes:
        versions = match.group("versions")
        return match.group("indent") + b"VCACHE_REQUIRE([[varnish], " + versions + b"], [[vinyl], " + versions + b"])"

    data = _counted_sub(PREREQ, prereq, data, "prerequisite macro -> VCACHE_REQUIRE", counts)
    data = _counted_sub(PREFIX, lambda match: b"VCACHE" + (match.group(1) or b"") + b"_",
                        data, "build prefix -> VCACHE", counts)
    data = _counted_sub(PRIVATE_HEADER, b"cache/cache_int.h", data,
                        "private header -> cache_int.h", counts)
    if path.suffix == ".vtc":
        data = _counted_sub(VTC_HEADER, b"vtest", data, "VTC header -> vtest", counts)
        data = _counted_sub(VTC_COMMAND, b"vcache", data, "VTC command -> vcache", counts)

    def compiler(match: re.Match[bytes]) -> bytes:
        line = re.sub(rb"(?:varnish|vinyl)test", b"vtest", match.group("line"))
        return line

    before = data
    data = VTC_COMPILER.sub(compiler, data)
    if data != before:
        counts["VTC_LOG_COMPILER -> vtest"] += 1
    return data, counts


def convert_tree(root: Path) -> tuple[list[tuple[Path, Counter[str]]], Counter[str]]:
    changed: list[tuple[Path, Counter[str]]] = []
    totals: Counter[str] = Counter()
    for directory, names, filenames in os.walk(root):
        names[:] = sorted(name for name in names if name != ".git")
        for filename in sorted(filenames):
            path = Path(directory, filename)
            if path.is_symlink():
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue
            converted, counts = convert_bytes(data, path)
            if not counts:
                continue
            path.write_bytes(converted)
            changed.append((path.relative_to(root), counts))
            totals.update(counts)
    return changed, totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker", type=Path)
    parser.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        parser.error(f"source tree is not a directory: {args.source}")

    changed, totals = convert_tree(args.source)
    if not changed:
        print("no Vinyl or Varnish API spellings found while converting to VCACHE", file=sys.stderr)
        return 1
    if args.marker is not None:
        args.marker.write_text(MARKER + "\n", encoding="utf-8")
    print(f"converted VMOD source to the VCACHE API in {len(changed)} files")
    for path, counts in changed:
        detail = ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))
        print(f"  {path}: {detail}")
    print("totals:")
    for name, count in sorted(totals.items()):
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
