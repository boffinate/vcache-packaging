#!/usr/bin/env python3
"""Normalize an Autotools VMOD source tree for the selected engine.

Cross-family (DESIGN.md decision 19): rewrite the fixed vocabulary of build
macros, pkg-config names, daemon/CLI names, include paths and VTC commands from
one family's spelling to the other's. Same-family (decision 28): only the VSC
counter directives are touched, because both engines' shared vsctool accepts
the ``vinyl_vsc`` spelling alone.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path


FAMILIES = ("vinyl", "varnish")
DEFAULT_VINYL_PRIVATE_HEADER = "cache_vinyld.h"
VSC_DIRECTIVES = "vsc-directives"

# Varnish trunk still installs cache/cache_varnishd.h (a copy rule); Vinyl
# renamed its installed copy to cache/cache_int.h at 6d36364cc1, so the Vinyl
# spelling is a build-time input, not a table constant.
VINYL_PRIVATE_HEADERS = ("cache_vinyld.h", "cache_int.h")
VARNISH_PRIVATE_HEADER = "cache_varnishd.h"


def _header_macro(name: str) -> bytes:
    """autoconf's AC_CHECK_HEADERS spelling: cache_int.h -> CACHE_INT_H."""
    return name.upper().replace(".", "_").encode()


def _private_header_pairs(vinyl_header: str) -> tuple:
    """Vinyl-spelling -> Varnish-spelling pairs for one Vinyl header name."""
    return (
        (b"cache/" + vinyl_header.encode(), b"cache/" + VARNISH_PRIVATE_HEADER.encode()),
        (vinyl_header.encode(), VARNISH_PRIVATE_HEADER.encode()),
        (_header_macro(vinyl_header), _header_macro(VARNISH_PRIVATE_HEADER)),
    )


# Ordered: specific spellings before the bare VINYLD/VINYL_ rules that would
# otherwise eat them. The private-header pairs are prepended per direction.
# A bare pkg-config prefix (queryfilter's own m4) defines VINYL_CFLAGS on the
# m4 side while the underscore rule below rewrites the Makefile consumers, so
# it needs its own entry or the engine include path silently goes empty.
VINYL_TO_VARNISH = (
    (b"PKG_CHECK_MODULES([VINYL]", b"PKG_CHECK_MODULES([VARNISH]"),
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

# Varnish 9 and Vinyl 9 share one vsctool implementation that recognizes only
# the vinyl_vsc directives; the historic varnish_vsc spelling makes it emit no
# counter headers at all, on either engine.
VSC_RULE = (b"varnish_vsc", b"vinyl_vsc")


def replacements(source_family: str, target_family: str,
                 vinyl_private_header: str = DEFAULT_VINYL_PRIVATE_HEADER) -> tuple:
    if source_family == target_family:
        return ()
    if vinyl_private_header not in VINYL_PRIVATE_HEADERS:
        raise ValueError(f"unknown Vinyl private header spelling: {vinyl_private_header}")
    if (source_family, target_family) == ("vinyl", "varnish"):
        headers = ()
        for name in VINYL_PRIVATE_HEADERS:
            headers += _private_header_pairs(name)
        return headers + VINYL_TO_VARNISH
    if (source_family, target_family) == ("varnish", "vinyl"):
        headers = tuple((new, old) for old, new in _private_header_pairs(vinyl_private_header))
        return headers + tuple((new, old) for old, new in VINYL_TO_VARNISH)
    raise ValueError(f"unsupported family normalization: {source_family} -> {target_family}")


def normalize_bytes(data: bytes, path: Path, source_family: str, target_family: str,
                    vinyl_private_header: str = DEFAULT_VINYL_PRIVATE_HEADER) -> tuple:
    counts = Counter()
    if path.suffix == ".vsc":
        old, new = VSC_RULE
        count = data.count(old)
        if count:
            data = data.replace(old, new)
            counts[f"{old.decode()} -> {new.decode()}"] += count
    for old, new in replacements(source_family, target_family, vinyl_private_header):
        if path.suffix == ".vtc" and (old, new) in (
            (b"vinyltest", b"varnishtest"),
            (b"varnishtest", b"vinyltest"),
        ):
            continue
        count = data.count(old)
        if count:
            data = data.replace(old, new)
            counts[f"{old.decode()} -> {new.decode()}"] += count

    if path.suffix == ".vtc" and source_family != target_family:
        command = re.compile(rb"(?<![A-Za-z0-9_])" + source_family.encode() + rb"(?![A-Za-z0-9_])")
        data, count = command.subn(target_family.encode(), data)
        if count:
            counts[f"VTC command {source_family} -> {target_family}"] += count
        data, count = re.subn(rb"(?m)^vinyltest(?=\s|$)", b"varnishtest", data)
        if count:
            counts["VTC header vinyltest -> varnishtest"] += count
    return data, counts


def normalize_tree(root: Path, source_family: str, target_family: str,
                   vinyl_private_header: str = DEFAULT_VINYL_PRIVATE_HEADER) -> tuple:
    changed = []
    totals = Counter()
    same_family = source_family == target_family
    for directory, names, filenames in os.walk(root):
        names[:] = sorted(name for name in names if name != ".git")
        for filename in sorted(filenames):
            path = Path(directory, filename)
            if path.is_symlink():
                continue
            if same_family and path.suffix != ".vsc":
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue
            normalized, counts = normalize_bytes(data, path, source_family, target_family, vinyl_private_header)
            if not counts:
                continue
            path.write_bytes(normalized)
            changed.append((path.relative_to(root), counts))
            totals.update(counts)
    return changed, totals


def normalization_name(source_family: str, target_family: str) -> str:
    """The cell-result value recorded for a run that changed something."""
    if source_family == target_family:
        return VSC_DIRECTIVES
    return f"{source_family}-to-{target_family}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-family", required=True, choices=FAMILIES)
    parser.add_argument("--target-family", required=True, choices=FAMILIES)
    parser.add_argument("--vinyl-private-header", default=DEFAULT_VINYL_PRIVATE_HEADER,
                        choices=VINYL_PRIVATE_HEADERS,
                        help="the daemon-private header name the Vinyl engine installs under cache/")
    parser.add_argument("--marker", type=Path,
                        help="write the normalization name here when any file changed")
    parser.add_argument("source", type=Path)
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        parser.error(f"source tree is not a directory: {args.source}")

    changed, totals = normalize_tree(args.source, args.source_family, args.target_family,
                                     args.vinyl_private_header)
    same_family = args.source_family == args.target_family
    if not changed:
        if same_family:
            print("no legacy VSC directive spellings found; source left untouched")
            return 0
        print(
            f"no {args.source_family} API spellings found while normalizing for {args.target_family}",
            file=sys.stderr,
        )
        return 1

    name = normalization_name(args.source_family, args.target_family)
    if args.marker is not None:
        args.marker.write_text(name + "\n", encoding="utf-8")
    if same_family:
        print(f"normalized VSC directives in {len(changed)} files ({name})")
    else:
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
