#!/usr/bin/env python3
# version 0.1.1
"""Highlight Rust code in rustcode blocks of .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds rustcode, skips blocks
that already carry the canonical palette (<span class="cXXXXXX">), and
colours the rest:

    keywords (fn, let, if, match, impl, ...)  -> <span class="c5fd7ff">
    strings, chars, raw strings, byte strings -> <span class="cad7fa8">
    comments (// ... and /* ... */)           -> <span class="c999999">
    attributes and lifetimes (#[test], 'a)    -> <span class="cfce94f">
    numbers                                 -> <span class="c34e2e2">
    type names (String, Vec, i32, Some)      -> <span class="cff5f00">
    macro names (println!, vec!, write!)      -> <span class="c4e9a06">
    the name after fn (also fn fmt)           -> <span class="cff0086">

Lower case only: the served sheets define .c5fd7ff and the others in lower
case, and class names are case sensitive, so c5FD7FF would match nothing.
The older blue/orange/lightblue palette that rustcode blocks used before is
dropped and replaced by these classes, exactly as tools/pycode-highlight
re-highlights the old Python colours.

A small Rust lexer of its own, not Pygments: it reads // and nested /* */
comments, "..." strings with escapes, char literals versus lifetimes, raw
strings (r"..." and r#"..."#), byte strings (b"..."), numbers with suffixes,
attributes, macro calls, type names and the name after fn. A block it cannot
lex (unterminated string, char or block comment, or a tag other than code
and span inside it) is reported and left byte for byte as it was.

Each <code> line of a block is one line of Rust: the lines are lexed
together (a raw string or a block comment needs that) and coloured one by
one, so a colour span never crosses a line.

Every block is verified: the text without tags, unescaped, equals the
original text without tags, unescaped. A file is written to a temporary
file next to it and renamed into place, so CRLF line endings stay.

Usage:
    python3 highlight_rustcode.py [--write] [--quiet] PATH [PATH ...]

    PATH: .php file or directory (recursive for dirs).
    Default is a check (no writes): exit 1 if blocks need highlighting,
    exit 2 if a block could not be coloured.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.1.1"

C_KEYWORD = os.environ.get("SITE_C_KEYWORD", "c5fd7ff")  # fn, let, if, match
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # "text", 'a', r"raw", b"bytes"
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # // ... and /* ... */
C_ATTRIBUTE = os.environ.get("SITE_C_ATTRIBUTE", "cfce94f")  # #[test], #[derive(...)], 'a
C_NUMBER = os.environ.get("SITE_C_NUMBER", "c34e2e2")  # 42, 1_000, 3.5, 0x1f, 42u8
C_TYPE = os.environ.get("SITE_C_TYPE", "cff5f00")  # String, Vec, i32, Some, MAX
C_MACRO = os.environ.get("SITE_C_MACRO", "c4e9a06")  # println!, vec!, write!
C_FUNCTION = os.environ.get("SITE_C_FUNCTION", "cff0086")  # the name after fn

TARGET_CLASS = "rustcode"
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9a-f]{6}"')
UNIT_RE = re.compile(r"(<!--.*?-->|</?[A-Za-z][^>]*>)|(&#?[0-9A-Za-z]+;)|(.)", re.DOTALL)
TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
TAG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")

KEYWORDS = frozenset(
    {
        "as",
        "async",
        "await",
        "break",
        "const",
        "continue",
        "crate",
        "dyn",
        "else",
        "enum",
        "extern",
        "false",
        "fn",
        "for",
        "if",
        "impl",
        "in",
        "let",
        "loop",
        "macro_rules",
        "match",
        "mod",
        "move",
        "mut",
        "pub",
        "ref",
        "return",
        "self",
        "Self",
        "static",
        "struct",
        "super",
        "trait",
        "true",
        "type",
        "unsafe",
        "use",
        "where",
        "while",
    }
)
# built-in primitive types are lower case and still type names
PRIMITIVE_TYPES = frozenset(
    {
        "bool",
        "char",
        "f32",
        "f64",
        "i8",
        "i16",
        "i32",
        "i64",
        "i128",
        "isize",
        "str",
        "u8",
        "u16",
        "u32",
        "u64",
        "u128",
        "usize",
    }
)

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER_RE = re.compile(
    r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+"
    r"|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?"
    r"(?:[iu](?:8|16|32|64|128|size)|f(?:32|64))?"
)
RAW_PREFIX_RE = re.compile(r"(?:b|c)?r(#*)\"")
BYTE_PREFIX_RE = re.compile(r"b\"")
SUPPORTED_TAGS = frozenset({"code", "span"})


class RustSyntaxError(ValueError):
    """A block that this lexer refuses to colour."""


class RustLexer:  # pylint: disable=too-many-public-methods,too-many-instance-attributes
    """Colour class for every character of a Rust source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end
        self.fn_pending = False

    def run(self) -> list[str | None]:
        """Lex the whole source; one colour class or None per character."""
        while self.pos < self.end:
            char = self.src[self.pos]
            if char in " \t\r\n":
                self.pos += 1
                continue
            self.token(char)
        return self.colours

    def token(self, char: str) -> None:  # pylint: disable=too-many-branches
        """One token at self.pos."""
        if self.src.startswith("//", self.pos):
            self.comment()
        elif self.src.startswith("/*", self.pos):
            self.block_comment()
        elif char == "#" and self.attribute():
            return
        elif char in "\"'":
            self.string_or_lifetime(char)
        elif char in "rbc" and self.raw_string():
            return
        elif char.isdigit():
            self.number()
        elif char.isalpha() or char == "_":
            self.identifier()
        else:
            self.punctuation()

    def line_number(self) -> int:
        """1-based line of the current position."""
        return self.src.count("\n", 0, self.pos) + 1

    def paint(self, start: int, stop: int, colour: str) -> None:
        """Give characters start..stop-1 one colour."""
        for index in range(start, min(stop, self.end)):
            self.colours[index] = colour

    def comment(self) -> None:
        """// to the end of the line, including /// and //!."""
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.paint(self.pos, stop, C_COMMENT)
        self.pos = stop

    def block_comment(self) -> None:
        """/* ... */, nested as Rust allows."""
        start = self.pos
        pos = start + 2
        depth = 1
        while pos < self.end:
            if self.src.startswith("/*", pos):
                depth += 1
                pos += 2
            elif self.src.startswith("*/", pos):
                depth -= 1
                pos += 2
                if depth == 0:
                    self.paint(start, pos, C_COMMENT)
                    self.pos = pos
                    return
            else:
                pos += 1
        raise RustSyntaxError(f"line {self.line_number()}: unterminated /* comment")

    def attribute(self) -> bool:
        """#[test], #[derive(Debug, Clone)] and #![allow(...)] as one token."""
        start = self.pos
        pos = start + 1
        if pos < self.end and self.src[pos] == "!":
            pos += 1
        if pos >= self.end or self.src[pos] != "[":
            return False
        depth = 1
        pos += 1
        while pos < self.end and depth:
            char = self.src[pos]
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
            elif char in "\"'":
                pos = self.skip_literal(pos, char)
                continue
            pos += 1
        if depth:
            raise RustSyntaxError(f"line {self.line_number()}: unterminated #[ attribute")
        self.paint(start, pos, C_ATTRIBUTE)
        self.pos = pos
        return True

    def string_or_lifetime(self, quote: str) -> None:
        """A "..." string, a 'c' char literal, or 'a and 'static lifetimes."""
        if quote == '"':
            stop = self.skip_literal(self.pos, quote)
            self.paint(self.pos, stop, C_STRING)
            self.pos = stop
            return
        char_stop = self.char_end(self.pos)
        if char_stop is not None:
            self.paint(self.pos, char_stop, C_STRING)
            self.pos = char_stop
            return
        # a lifetime or a loop label: 'a, 'static, '_ or 'outer:
        match = IDENT_RE.match(self.src, self.pos + 1)
        stop = match.end() if match else self.pos + 1
        self.paint(self.pos, stop, C_ATTRIBUTE)
        self.pos = stop

    def char_end(self, pos: int) -> int | None:
        """Index after the quote that closes a char literal at pos, or None."""
        src = self.src
        index = pos + 1
        if index >= self.end or src[index] in "\n'":
            return None
        if src[index] == "\\":
            index += 1
            if index < self.end and src[index] == "u" and src.startswith("{", index + 1):
                close = src.find("}", index + 2)
                if close == -1:
                    return None
                index = close + 1
            else:
                index += 1
        else:
            index += 1
        if index < self.end and src[index] == "'":
            return index + 1
        return None

    def skip_literal(self, pos: int, quote: str) -> int:
        """Index after the closing quote of an ordinary quoted literal."""
        index = pos + 1
        while index < self.end:
            char = self.src[index]
            if char == "\\":
                index += 2
            elif char == quote:
                return index + 1
            else:
                index += 1
        raise RustSyntaxError(f"line {self.line_number()}: unterminated {quote} literal")

    def raw_string(self) -> bool:
        """r"..." and r#"..."# (also br#"..."#, cr#"..."#) as one string."""
        raw = RAW_PREFIX_RE.match(self.src, self.pos)
        if raw:
            hashes = len(raw.group(1))
            closing = '"' + "#" * hashes
            end = self.src.find(closing, raw.end())
            if end == -1:
                raise RustSyntaxError(f"line {self.line_number()}: unterminated raw string")
            stop = end + len(closing)
            self.paint(self.pos, stop, C_STRING)
            self.pos = stop
            return True
        byte = BYTE_PREFIX_RE.match(self.src, self.pos)
        if byte:
            stop = self.skip_literal(self.pos + 1, '"')
            self.paint(self.pos, stop, C_STRING)
            self.pos = stop
            return True
        return False

    def number(self) -> None:
        """42, 1_000, 3.5, 0x1f, 0b101, 42u8."""
        match = NUMBER_RE.match(self.src, self.pos)
        stop = match.end() if match else self.pos + 1
        self.paint(self.pos, stop, C_NUMBER)
        self.pos = stop

    def identifier(self) -> None:
        """A keyword, a primitive type, a macro name, a fn name or a type."""
        src = self.src
        match = IDENT_RE.match(src, self.pos)
        word = match.group() if match else src[self.pos]
        stop = match.end() if match else self.pos + 1
        if word in KEYWORDS:
            self.paint(self.pos, stop, C_KEYWORD)
            self.pos = stop
            self.fn_pending = word == "fn"
            return
        if stop < self.end and src[stop] == "!":
            self.paint(self.pos, stop + 1, C_MACRO)
            self.pos = stop + 1
            self.fn_pending = False
            return
        if self.fn_pending:
            self.paint(self.pos, stop, C_FUNCTION)
            self.fn_pending = False
        elif word in PRIMITIVE_TYPES or word[0].isupper():
            self.paint(self.pos, stop, C_TYPE)
        self.pos = stop
        self.fn_pending = False

    def punctuation(self) -> None:
        """Operators and brackets stay plain."""
        self.pos += 1
        self.fn_pending = False


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


def strip_and_decode(inner: str) -> str:
    """Inner HTML -> the text without tags, unescaped."""
    return html.unescape(TAG_RE.sub("", inner))


def split_units(inner: str) -> list[Unit]:
    """Inner HTML as tags and characters, each with its original spelling."""
    units: list[Unit] = []
    for tag, entity, char in UNIT_RE.findall(inner):
        if tag:
            units.append(Unit("", tag, is_tag=True))
        elif entity:
            units.append(Unit(html.unescape(entity), entity))
        else:
            units.append(Unit(char, char))
    return units


def units_text(units: list[Unit]) -> str:
    """The characters of a run of units, tags left out."""
    return "".join(unit.text for unit in units if not unit.is_tag)


def coloured_units(units: list[Unit], colours: list[str | None], offset: int) -> str:
    """Inner HTML of one <code> line with colour spans added and tags kept."""
    out: list[str] = []
    piece: list[str] = []
    piece_colour: str | None = None
    piece_has_text = False
    index = offset

    def flush() -> None:
        nonlocal piece_has_text
        if piece:
            text = "".join(piece)
            if piece_colour and piece_has_text:
                out.append(f'<span class="{piece_colour}">{text}</span>')
            else:
                out.append(text)
            piece.clear()
        piece_has_text = False

    for unit in units:
        if unit.is_tag:
            if tag_name(unit.spelling) == "code":
                flush()
                out.append(unit.spelling)
            continue
        colour = colours[index] if index < len(colours) else None
        index += 1
        if colour != piece_colour:
            flush()
            piece_colour = colour
        piece.append(unit.spelling)
        piece_has_text = piece_has_text or not unit.text.isspace()
    flush()
    return "".join(out)


def tag_name(tag: str) -> str:
    """Lower-case name of one tag, empty for a comment."""
    match = TAG_NAME_RE.match(tag)
    return match.group(1).lower() if match else ""


def tag_names(inner: str) -> set[str]:
    """Lower-case names of the tags inside a block."""
    return {match.group(1).lower() for match in TAG_NAME_RE.finditer(inner)}


def highlight_inner(inner: str) -> str:
    """Coloured inner HTML; raises RustSyntaxError when it cannot be lexed."""
    unsupported = tag_names(inner) - SUPPORTED_TAGS
    if unsupported:
        raise RustSyntaxError(f"tag {sorted(unsupported)[0]} inside the block")
    segments: list[re.Match[str] | None] = list(CODE_RE.finditer(inner))
    if not segments:
        segments = [None]
    texts = [units_text(split_units(segment.group(1))) if segment else units_text(split_units(inner)) for segment in segments]
    colours = RustLexer("\n".join(texts)).run()
    out: list[str] = []
    cursor = 0
    offset = 0
    for segment, text in zip(segments, texts, strict=True):
        if segment is None:
            out.append(coloured_units(split_units(inner), colours, 0))
            break
        out.append(inner[cursor : segment.start(1)])
        out.append(coloured_units(split_units(segment.group(1)), colours, offset))
        offset += len(text) + 1
        cursor = segment.end(1)
    if segments[0] is not None:
        out.append(inner[cursor:])
    result = "".join(out)
    if strip_and_decode(result) != strip_and_decode(inner):
        raise RustSyntaxError("round trip failed (NOT written)")
    return result


def write_atomic(path: Path, data: bytes) -> None:
    """Replace path with data through a temporary file in the same folder."""
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as temp:
            temp.write(data)
        shutil.copymode(path, temp_name)
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


def process_file(path: Path, *, write: bool) -> FileStats:
    """Highlight the rustcode blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without rustcode is
    not decoded at all; one that has the class but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"rustcode" not in raw:
        return stats
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        stats.errors.append(f"{path}: not UTF-8 ({exc.reason}), left alone")
        return stats

    def repl(match: re.Match[str]) -> str:
        attrs, inner = match.group(1), match.group(2)
        if TARGET_CLASS not in class_list(attrs):
            return match.group(0)
        if COLOUR_SPAN_RE.search(inner):
            stats.highlighted += 1
            return match.group(0)
        if not strip_and_decode(inner).strip():
            return match.group(0)
        line = text.count("\n", 0, match.start()) + 1
        try:
            coloured = highlight_inner(inner)
        except RustSyntaxError as exc:
            stats.errors.append(f"{path}:{line}: {exc}")
            return match.group(0)
        if coloured == inner:
            return match.group(0)
        stats.fixed += 1
        return f"<p{attrs}>{coloured}</p>"

    new_text = P_BLOCK_RE.sub(repl, text)
    stats.changed = new_text != text
    if write and stats.changed:
        write_atomic(path, new_text.encode("utf-8"))
    return stats


def class_list(attrs: str) -> list[str]:
    """Classes of a tag's attribute string."""
    match = CLASS_ATTR_RE.search(attrs)
    if not match:
        return []
    return (match.group(2) or match.group(3) or "").split()


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
    parser = argparse.ArgumentParser(description="Highlight Rust blocks (rustcode) in .php files.")
    parser.add_argument("paths", nargs="+", help=".php file or directory (recursive)")
    parser.add_argument("--write", action="store_true", help="write the highlighting in place")
    parser.add_argument("--quiet", action="store_true", help="print only problems")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
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
