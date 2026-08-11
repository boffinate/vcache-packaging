"""Strict parser for the restricted YAML subset used by the catalog files.

``engines.yml`` and ``vmods/*.yml`` are YAML-shaped but this parser
deliberately accepts only a small subset:

* nested block mappings (``key: value`` and ``key:`` + indented block);
* block sequences (``- scalar``, ``- key: value`` + aligned continuation);
* plain and quoted scalars, all returned as ``str``.

Everything else - flow collections, block scalars, anchors, aliases, tags,
multiple documents, tabs, merge keys, comments that trail a value - is a hard
error. There is no type coercion: every scalar is returned as a string and the
schema layer in ``matrix.py`` applies typing. That avoids YAML's
implicit-typing surprises (``9.0`` becoming a float, ``no`` becoming
``False``) in files whose entire purpose is exact identity.

Ported from v1 ``tools/yaml_subset.py`` with hyphens added to keys
(``by_series`` is keyed by series ids like ``varnish-10``).

Standard library only.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["ManifestSyntaxError", "parse", "parse_file"]


class ManifestSyntaxError(Exception):
    """Raised for any input outside the accepted subset."""

    def __init__(self, path: str, lineno: int, message: str) -> None:
        self.path = path
        self.lineno = lineno
        self.message = message
        super().__init__(f"{path}:{lineno}: {message}")


# Keys allow hyphens and dots so series ids such as ``vinyl-9.0`` can key
# ``by_series`` maps; v1 allowed lower_snake_case only.
KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
MAP_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:[ ](.+))?$")
# Characters that introduce YAML syntax we do not support. Rejecting them in
# plain scalars keeps "looks like a string" and "is a string" identical.
FORBIDDEN_PLAIN = set("{}[]&*!|>%@`\"'#")


class _Line:
    __slots__ = ("lineno", "indent", "text", "is_item")

    def __init__(self, lineno: int, indent: int, text: str, is_item: bool) -> None:
        self.lineno = lineno
        self.indent = indent
        self.text = text
        self.is_item = is_item


def parse_file(path) -> dict:
    """Parse a catalog file. Raises ManifestSyntaxError or OSError."""
    path = Path(path)
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestSyntaxError(str(path), 1, f"file is not valid UTF-8: {exc}") from None
    return parse(text, str(path))


def parse(text: str, path: str = "<string>") -> dict:
    lines = _tokenize(text, path)
    if not lines:
        raise ManifestSyntaxError(path, 1, "file is empty")
    if lines[0].indent != 0:
        raise ManifestSyntaxError(path, lines[0].lineno, "top-level block must not be indented")
    value, index = _parse_block(lines, 0, 0, path)
    if index != len(lines):
        raise ManifestSyntaxError(path, lines[index].lineno, "unexpected content after the top-level block")
    if not isinstance(value, dict):
        raise ManifestSyntaxError(path, lines[0].lineno, "top level of a catalog file must be a mapping")
    return value


def _tokenize(text: str, path: str) -> list:
    lines: list = []
    raw_lines = text.split("\n")
    i = 0
    while i < len(raw_lines):
        raw = raw_lines[i]
        lineno = i + 1
        if "\r" in raw:
            raise ManifestSyntaxError(path, lineno, "carriage return found; use LF line endings")
        if "\t" in raw:
            raise ManifestSyntaxError(path, lineno, "tab character found; indent with spaces only")
        if raw.strip() == "":
            if raw != "":
                raise ManifestSyntaxError(path, lineno, "blank line contains whitespace")
            i += 1
            continue
        if raw != raw.rstrip():
            raise ManifestSyntaxError(path, lineno, "trailing whitespace")
        stripped = raw.lstrip(" ")
        indent = len(raw) - len(stripped)
        if stripped.startswith("#"):
            i += 1
            continue
        if stripped.startswith("---") or stripped.startswith("..."):
            raise ManifestSyntaxError(path, lineno, "document markers are not supported; one document per file")
        if indent % 2 != 0:
            raise ManifestSyntaxError(path, lineno, f"indent of {indent} spaces is not a multiple of two")
        if stripped == "-":
            raise ManifestSyntaxError(path, lineno, "list item must be written as '- value' on one line")
        if stripped.startswith("-") and not stripped.startswith("- "):
            raise ManifestSyntaxError(path, lineno, "a line starting with '-' must be a list item written as '- value'")
        if re.match(r"^[A-Za-z0-9_.-]+: \|", stripped):
            raise ManifestSyntaxError(
                path, lineno,
                "literal block scalars are not supported; use a block sequence of plain scalar lines",
            )
        if stripped.startswith("- "):
            rest = stripped[2:]
            if rest.startswith(" "):
                raise ManifestSyntaxError(path, lineno, "exactly one space must follow the list item dash")
            lines.append(_Line(lineno, indent, "", True))
            lines.append(_Line(lineno, indent + 2, rest, False))
            i += 1
            continue
        lines.append(_Line(lineno, indent, stripped, False))
        i += 1
    return lines


def _parse_block(lines: list, index: int, indent: int, path: str):
    line = lines[index]
    if line.is_item:
        return _parse_sequence(lines, index, indent, path)
    if MAP_RE.match(line.text):
        return _parse_mapping(lines, index, indent, path)
    return _parse_scalar_block(lines, index, indent, path)


def _parse_scalar_block(lines: list, index: int, indent: int, path: str):
    line = lines[index]
    # Only for a PLAIN scalar: `key: value` written where a scalar belongs is a
    # mistyped mapping and this is the diagnostic for it. A quoted scalar is
    # unambiguous and exempt (quoting is the documented fix for ': ' values).
    if line.text[:1] not in ("\"", "'") and (": " in line.text or line.text.endswith(":")):
        raise ManifestSyntaxError(path, line.lineno, f"malformed mapping entry: {line.text!r}")
    value = _parse_scalar(line.text, path, line.lineno)
    index += 1
    if index < len(lines) and lines[index].indent > indent:
        raise ManifestSyntaxError(path, lines[index].lineno, "unexpected indented block after a scalar")
    return value, index


def _parse_mapping(lines: list, index: int, indent: int, path: str):
    result: dict = {}
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if line.is_item:
            raise ManifestSyntaxError(
                path,
                line.lineno,
                "list item at mapping indentation; indent list items two spaces under their key",
            )
        match = MAP_RE.match(line.text)
        if match is None:
            raise ManifestSyntaxError(path, line.lineno, f"expected 'key: value' or 'key:', got {line.text!r}")
        key = match.group(1)
        if not KEY_RE.match(key):
            raise ManifestSyntaxError(path, line.lineno, f"invalid key {key!r}; use lower case, digits, '_' or '-'")
        if key in result:
            raise ManifestSyntaxError(path, line.lineno, f"duplicate key {key!r}")
        raw_value = match.group(2)
        if raw_value is None:
            if index + 1 >= len(lines) or lines[index + 1].indent <= indent:
                raise ManifestSyntaxError(path, line.lineno, f"key {key!r} has no value and no nested block")
            if lines[index + 1].indent != indent + 2:
                raise ManifestSyntaxError(
                    path,
                    lines[index + 1].lineno,
                    "nested block must be indented exactly two spaces from its key",
                )
            value, index = _parse_block(lines, index + 1, indent + 2, path)
        else:
            value = _parse_scalar(raw_value, path, line.lineno)
            index += 1
            if index < len(lines) and lines[index].indent > indent:
                raise ManifestSyntaxError(
                    path, lines[index].lineno, f"unexpected indented block after the value of {key!r}"
                )
        result[key] = value
    if index < len(lines) and lines[index].indent > indent:
        raise ManifestSyntaxError(path, lines[index].lineno, "unexpected indentation")
    return result, index


def _parse_sequence(lines: list, index: int, indent: int, path: str):
    items: list = []
    while index < len(lines) and lines[index].indent == indent and lines[index].is_item:
        index += 1
        if index >= len(lines) or lines[index].indent != indent + 2:
            raise ManifestSyntaxError(path, lines[index - 1].lineno, "list item has no value")
        value, index = _parse_block(lines, index, indent + 2, path)
        items.append(value)
    if index < len(lines) and lines[index].indent > indent:
        raise ManifestSyntaxError(path, lines[index].lineno, "unexpected indentation inside a list")
    return items, index


def _parse_scalar(raw: str, path: str, lineno: int):
    if raw.startswith("["):
        raise ManifestSyntaxError(path, lineno, "flow sequences are not supported; use a block sequence")
    if raw[0] in "\"'":
        quote = raw[0]
        if len(raw) < 2 or not raw.endswith(quote):
            raise ManifestSyntaxError(path, lineno, "unterminated quoted scalar")
        inner = raw[1:-1]
        if quote in inner or "\\" in inner:
            raise ManifestSyntaxError(
                path, lineno, "quoted scalars must not contain their quote character or a backslash"
            )
        return inner
    bad = sorted(set(raw) & FORBIDDEN_PLAIN)
    if bad:
        raise ManifestSyntaxError(
            path,
            lineno,
            "plain scalar contains unsupported character(s) {}; quote the value".format(
                ", ".join(repr(ch) for ch in bad)
            ),
        )
    if ": " in raw or raw.endswith(":"):
        raise ManifestSyntaxError(path, lineno, "plain scalar contains ': '; quote the value")
    return raw
