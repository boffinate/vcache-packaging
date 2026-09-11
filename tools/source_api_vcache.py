#!/usr/bin/env python3
"""Convert a family-specific Autotools VMOD tree to the VCACHE API names.

Two strategies exist. ``posted`` is the sed recipe from Vinyl Cache issue
#4537 comment 62306, applied blindly across the tree the way the recipe's
``git grep -l`` invocations do. ``fixed`` adds the three corrections that the
comparison grid showed the recipe needs; see STRATEGY_MARKERS for the cell
markers each writes.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path


STRATEGY_MARKERS = {"posted": "vcache-api", "fixed": "vcache-api-fixed"}

# The sed has no anchor after the closing paren: it matches greedily to the
# last ")" on the line and keeps whatever follows (a trailing dnl comment).
PREREQ = re.compile(rb"(?m)^(?P<indent>[ \t]*)(?:VARNISH|VINYL)_PREREQ\((?P<versions>.*)\)")
PREFIX = re.compile(rb"(?:VARNISH|VINYL)(API)?_")
VTC_HEADER = re.compile(rb"(?m)^(?:varnish|vinyl)test(?=\s|$)")
VTC_COMMAND = re.compile(rb"(?m)^(?:varnish|vinyl)(?=\s)")
VTC_COMPILER = re.compile(rb"(?m)^(?P<line>[^\r\n]*VTC_LOG_COMPILER[^\r\n]*)$")
PRIVATE_HEADER = re.compile(rb"cache/cache_(?:varnish|vinyl)d\.h")
PKG_CONFIG_DATAROOTDIR = re.compile(
    rb"\((?P<command>pkg-config --variable=datarootdir )(?P<family>varnish|vinyl)api(?P<options>[^\r\n)]*)\)"
)
# vinyl.m4 defines no VCACHE_PREREQ, so the generic prefix rule turns the
# common template guard into a hard configure error.
PREREQ_GUARD = re.compile(rb"m4_ifndef\(\[(?:VARNISH|VINYL)_PREREQ\]")
# The generic prefix rule needs a trailing underscore, so it renames the
# consumers ($(VINYLAPI_CFLAGS)) but not this producer; the acvmod trees
# then compile with an empty include path.
PKG_CHECK_PRODUCER = re.compile(rb"PKG_CHECK_MODULES\(\[(?:VARNISH|VINYL)API\]")


def _counted_sub(pattern: re.Pattern[bytes], replacement, data: bytes, label: str,
                 counts: Counter[str]) -> bytes:
    data, count = pattern.subn(replacement, data)
    if count:
        counts[label] += count
    return data


def _pkg_config_posted(match: re.Match[bytes]) -> bytes:
    return (
        b"(" + match.group("command") + b"varnishapi vinylapi" + match.group("options")
        + b" | awk '{print $1}')"
    )


def _pkg_config_fixed(match: re.Match[bytes]) -> bytes:
    # pkg-config fails outright when any listed package is missing, so the
    # posted two-package form yields nothing on a single-project system. Probe
    # the source's own family first, then the other one (issue comment 62308).
    family = match.group("family")
    other = b"vinyl" if family == b"varnish" else b"varnish"
    command = match.group("command")
    options = match.group("options")
    return (
        b"(" + command + family + b"api" + options + b" || "
        + command + other + b"api" + options + b")"
    )


def convert_bytes(data: bytes, path: Path, strategy: str = "posted") -> tuple[bytes, Counter[str]]:
    if strategy not in STRATEGY_MARKERS:
        raise ValueError(f"unknown strategy: {strategy}")
    fixed = strategy == "fixed"
    counts: Counter[str] = Counter()

    def prereq(match: re.Match[bytes]) -> bytes:
        versions = match.group("versions")
        return match.group("indent") + b"VCACHE_REQUIRE([[varnish], " + versions + b"], [[vinyl], " + versions + b"])"

    data = _counted_sub(PREREQ, prereq, data, "prerequisite macro -> VCACHE_REQUIRE", counts)
    if fixed:
        data = _counted_sub(PREREQ_GUARD, b"m4_ifndef([VCACHE_REQUIRE]", data,
                            "prerequisite guard -> VCACHE_REQUIRE", counts)
        data = _counted_sub(PKG_CHECK_PRODUCER, b"PKG_CHECK_MODULES([VCACHEAPI]", data,
                            "pkg-config producer -> VCACHEAPI", counts)
    data = _counted_sub(PREFIX, lambda match: b"VCACHE" + (match.group(1) or b"") + b"_",
                        data, "build prefix -> VCACHE", counts)
    data = _counted_sub(PRIVATE_HEADER, b"cache/cache_int.h", data,
                        "private header -> cache_int.h", counts)
    if path.suffix == ".vtc":
        data = _counted_sub(VTC_HEADER, b"vtest", data, "VTC header -> vtest", counts)
        data = _counted_sub(VTC_COMMAND, b"vcache", data, "VTC command -> vcache", counts)

    def compiler(match: re.Match[bytes]) -> bytes:
        line = re.sub(rb"(?:varnish|vinyl)test", b"vtest -E@VTESTEXT@", match.group("line"))
        return line

    before = data
    data = VTC_COMPILER.sub(compiler, data)
    if data != before:
        counts["VTC_LOG_COMPILER -> vtest extension"] += 1

    data = _counted_sub(
        PKG_CONFIG_DATAROOTDIR,
        _pkg_config_fixed if fixed else _pkg_config_posted,
        data,
        "pkg-config datarootdir -> either API" if fixed else "pkg-config datarootdir -> both APIs",
        counts,
    )
    return data, counts


def convert_tree(root: Path, strategy: str = "posted") -> tuple[list[tuple[Path, Counter[str]]], Counter[str]]:
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
            converted, counts = convert_bytes(data, path, strategy)
            if not counts:
                continue
            path.write_bytes(converted)
            changed.append((path.relative_to(root), counts))
            totals.update(counts)
    return changed, totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=tuple(STRATEGY_MARKERS), default="posted")
    parser.add_argument("--marker", type=Path)
    parser.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        parser.error(f"source tree is not a directory: {args.source}")

    changed, totals = convert_tree(args.source, args.strategy)
    if not changed:
        print("no Vinyl or Varnish API spellings found; source already needs no conversion")
        return 0
    if args.marker is not None:
        args.marker.write_text(STRATEGY_MARKERS[args.strategy] + "\n", encoding="utf-8")
    print(f"converted VMOD source to the VCACHE API ({args.strategy} recipe) in {len(changed)} files")
    for path, counts in changed:
        detail = ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))
        print(f"  {path}: {detail}")
    print("totals:")
    for name, count in sorted(totals.items()):
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
