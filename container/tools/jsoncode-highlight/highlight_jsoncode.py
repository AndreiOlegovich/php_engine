#!/usr/bin/env python3
"""Highlight JSON code inside .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds jsoncode (this
includes jsoncode-with-hidden-lines blocks, which carry both classes),
skips blocks already highlighted with <span class="cXXXXXX">, and colours
the rest with the Pygments JSON lexer, which also reads // and /* */
comments (opencode.jsonc and the like):

    keys ("action")              -> <span class="c5fd7ff">
    string and number values     -> <span class="cad7fa8">
    true / false / null          -> <span class="cfce94f">
    comments                     -> <span class="c34e2e2">
    { } [ ] , :                  -> plain

The palette of the Python blocks (tools/pycode-highlight), written in
lower case: the built sheets define .c5fd7ff and friends only in lower
case (source/_colors.css), and class names are case-sensitive.

Every tag already in a block (the span.extra-lines runs of a
jsoncode-with-hidden-lines block, links, ...) and every entity stays as
it was: only colour spans are added, and a colour span is split where
such a tag falls, so tags always nest. Verification per block: removing
the added colour spans gives back the original inner HTML byte for byte.
A block the lexer cannot read (an Error token) is left alone and
reported.

Usage:
    python3 highlight_jsoncode.py [--write] [--quiet] PATH [PATH ...]

    PATH: .php file or directory (recursive for dirs).
    Default is a check (no writes): exit 1 if blocks need highlighting,
    exit 2 if a block could not be read.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pygments.lexers.data import JsonLexer
from pygments.token import Comment, Keyword, Name, Number, String, Token, _TokenType

VERSION = "0.1.0"

C_KEY = os.environ.get("SITE_C_KEY", "c5fd7ff")  # object keys
C_VALUE = os.environ.get("SITE_C_VALUE", "cad7fa8")  # string and number values
C_CONSTANT = os.environ.get("SITE_C_CONSTANT", "cfce94f")  # true, false, null
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c34e2e2")  # // and /* */ comments

P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9A-Fa-f]{6}"')
ADDED_SPAN_RE = re.compile(r'<span class="c[0-9a-f]{6}">([^<]*)</span>')
UNIT_RE = re.compile(r"(<[^>]*>)|(&#?\w+;)|(.)", re.DOTALL)
TARGET_CLASSES = {"jsoncode", "jsoncode-with-hidden-lines"}


@dataclass
class Unit:
    """One decoded character and how the page spells it, or a tag."""

    text: str
    spelling: str
    is_tag: bool = False


@dataclass
class FileStats:
    """What one file needed and got."""

    path: Path
    highlighted: int = 0
    fixed: int = 0
    errors: list[str] = field(default_factory=list)
    changed: bool = False


def colour_of(kind: _TokenType) -> str | None:
    """Palette class for a Pygments token type, or None for plain text."""
    if kind in Name.Tag:
        return C_KEY
    if kind in Keyword.Constant:
        return C_CONSTANT
    if kind in String or kind in Number:
        return C_VALUE
    if kind in Comment:
        return C_COMMENT
    return None


def class_list(attrs: str) -> list[str]:
    """Classes of a tag's attribute string."""
    match = CLASS_ATTR_RE.search(attrs)
    if not match:
        return []
    return (match.group(2) or match.group(3) or "").split()


def split_units(inner: str) -> list[Unit]:
    """Inner HTML as tags and characters, each with its original spelling."""
    units = []
    for tag, entity, char in UNIT_RE.findall(inner):
        if tag:
            units.append(Unit("", tag, is_tag=True))
        elif entity:
            units.append(Unit(html.unescape(entity), entity))
        else:
            units.append(Unit(char, char))
    return units


def highlight_inner(inner: str) -> str:
    """Coloured inner HTML; raises ValueError when the JSON cannot be read."""
    units = split_units(inner)
    chars = [unit for unit in units if not unit.is_tag]
    source = "".join(unit.text for unit in chars)
    tokens = list(JsonLexer(stripnl=False, ensurenl=False).get_tokens_unprocessed(source))
    if "".join(value for _, _, value in tokens) != source:
        raise ValueError("lexer changed the text")
    bad = [value for _, kind, value in tokens if kind in Token.Error]
    if bad:
        raise ValueError(f"not JSON near {bad[0]!r}")

    colours: list[str | None] = [None] * len(source)
    for start, kind, value in tokens:
        colour = colour_of(kind)
        for offset in range(start, start + len(value)):
            colours[offset] = colour

    out: list[str] = []
    piece: list[str] = []
    piece_colour: str | None = None
    piece_has_text = False

    def flush() -> None:
        nonlocal piece_has_text
        if piece:
            text = "".join(piece)
            out.append(f'<span class="{piece_colour}">{text}</span>' if piece_colour and piece_has_text else text)
            piece.clear()
        piece_has_text = False

    offset = 0
    for unit in units:
        if unit.is_tag:
            flush()
            out.append(unit.spelling)
            continue
        colour = colours[offset]
        offset += 1
        if colour != piece_colour or unit.text == "\n":
            flush()
            piece_colour = colour
        piece.append(unit.spelling)
        piece_has_text = piece_has_text or not unit.text.isspace()
        if unit.text == "\n":
            flush()
    flush()
    result = "".join(out)
    if ADDED_SPAN_RE.sub(r"\1", result) != inner:
        raise ValueError("round trip failed")
    return result


def process_file(path: Path, *, write: bool) -> FileStats:
    """Highlight the JSON blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without jsoncode
    is not decoded at all; one that has it but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"jsoncode" not in raw:
        return stats
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        stats.errors.append(f"{path}: not UTF-8 ({exc.reason}), left alone")
        return stats

    def repl(match: re.Match[str]) -> str:
        attrs, inner = match.group(1), match.group(2)
        if not TARGET_CLASSES & set(class_list(attrs)):
            return match.group(0)
        if COLOUR_SPAN_RE.search(inner):
            stats.highlighted += 1
            return match.group(0)
        line = text.count("\n", 0, match.start()) + 1
        try:
            coloured = highlight_inner(inner)
        except ValueError as exc:
            stats.errors.append(f"{path}:{line}: {exc}")
            return match.group(0)
        stats.fixed += 1
        return f"<p{attrs}>{coloured}</p>"

    new_text = P_BLOCK_RE.sub(repl, text)
    stats.changed = new_text != text
    if write and stats.changed:
        path.write_bytes(new_text.encode("utf-8"))
    return stats


def collect(paths: list[str]) -> list[Path]:
    """The .php files behind the given paths, scratch copies left out."""
    files: list[Path] = []
    for name in paths:
        path = Path(name)
        if path.is_dir():
            files += sorted(item for item in path.rglob("*.php") if item.is_file())
        elif path.is_file():
            files.append(path)
        else:
            print(f"no such file or directory: {name}", file=sys.stderr)
    return [item for item in files if " copy" not in item.name and not item.name.startswith("sess_")]


def main(argv: list[str] | None = None) -> int:
    """Check or write; exit 1 when blocks need highlighting, 2 on errors."""
    parser = argparse.ArgumentParser(description="Highlight JSON code blocks in .php files.")
    parser.add_argument("paths", nargs="+", help=".php file or directory (recursive)")
    parser.add_argument("--write", action="store_true", help="write the highlighting in place")
    parser.add_argument("--quiet", action="store_true", help="print only problems")
    args = parser.parse_args(argv)

    need: list[Path] = []
    failed = False
    total_ok = total_fixed = 0
    files = collect(args.paths)
    for path in files:
        stats = process_file(path, write=args.write)
        total_ok += stats.highlighted
        total_fixed += stats.fixed
        for error in stats.errors:
            failed = True
            print(f"ERROR {error}", file=sys.stderr)
        if stats.changed and not args.write:
            need.append(path)
        if stats.fixed and not args.quiet:
            print(f"{path}: highlighted already={stats.highlighted} {'fixed' if args.write else 'to fix'}={stats.fixed}")
    if not args.quiet:
        mode = "write" if args.write else "check"
        print(f"files={len(files)} highlighted={total_ok} fixed={total_fixed} mode={mode}")
    if failed:
        return 2
    if need:
        print(f"{len(need)} file(s) need highlighting (run with --write)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
