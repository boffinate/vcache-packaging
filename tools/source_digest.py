#!/usr/bin/env python3
"""Compute a stable identity for a materialised source tree.

Git's checkout metadata is deliberately ignored: it describes the transport
and checkout rather than the source that the build consumes.  The manifest
below records paths, file kinds, executable bits, symlink targets, and file
contents in a deterministic order.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import struct
from pathlib import Path


def _entries(root: Path):
    def visit(directory: Path, relative: bytes):
        children = sorted(os.scandir(directory), key=lambda entry: os.fsencode(entry.name))
        for entry in children:
            name = os.fsencode(entry.name)
            path = name if not relative else relative + b"/" + name
            if name == b".git":
                continue
            info = entry.stat(follow_symlinks=False)
            mode = info.st_mode
            if stat.S_ISDIR(mode):
                yield b"d", path, mode & 0o111, b""
                yield from visit(Path(entry.path), path)
            elif stat.S_ISLNK(mode):
                yield b"l", path, 0, os.fsencode(os.readlink(entry.path))
            elif stat.S_ISREG(mode):
                with open(entry.path, "rb") as source:
                    yield b"f", path, mode & 0o111, source.read()
            else:
                raise ValueError(f"unsupported source-tree entry: {entry.path}")

    yield from visit(root, b"")


def digest_tree(root: Path) -> str:
    if not root.is_dir():
        raise ValueError(f"source tree is not a directory: {root}")
    digest = hashlib.sha256()
    for kind, path, executable, content in _entries(root):
        digest.update(struct.pack(">BQ", len(kind), len(path)))
        digest.update(kind)
        digest.update(path)
        digest.update(struct.pack(">BQ", executable, len(content)))
        digest.update(content)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    print(digest_tree(args.source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
