#!/usr/bin/env python3
# version 0.1.0
"""Highlight Go code in gocode blocks of .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds gocode, skips blocks that
already carry the canonical palette (<span class="cXXXXXX">), and colours the
rest:

    keywords (package, func, if, range, ...)   -> <span class="c5fd7ff">
    strings, raw strings and runes             -> <span class="cad7fa8">
    comments (// ..., /* ... */)               -> <span class="c999999">
    numbers (42, 3.5, 0x1f, 1_000, 2i)         -> <span class="c34e2e2">
    predeclared types (int, string, error)     -> <span class="c4e9a06">
    exported names (Println, Server, T)        -> <span class="cff5f00">
    the function name after func               -> <span class="cff0086">

Lower case only: the served sheets define .c5fd7ff and the others in lower
case, and class names are case sensitive, so c5FD7FF would match nothing.
The older blue/orange/yellow/lightblue palette that gocode blocks used
before is dropped and replaced by these classes, exactly as
tools/rubycode-highlight re-highlights the old Ruby colours.

A small Go lexer of its own, not Pygments: it reads // comments (not // inside
a string), "..." with backslash escapes, `...` raw strings that span lines,
'...' rune literals, /* ... */ block comments, numeric literals with bases,
underscores, exponents and the imaginary suffix, identifiers and keywords.
The name after func is coloured as a function name, also for methods
(func (s *Server) Start). A block it cannot lex (unterminated string, rune or
comment, or a tag other than code/span inside it) is reported and left byte for
byte as it was.

Each <code> line of a block is one line of Go: the lines are lexed together
(raw strings and block comments need that) and coloured one by one, so a
colour span never crosses a line.

Every block is verified: the text without tags, unescaped, equals the
original text without tags, unescaped. A file is written to a temporary file
next to it and renamed into place, so CRLF line endings stay.

Usage:
    python3 highlight_gocode.py [--write] [--quiet] PATH [PATH ...]

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

VERSION = "0.1.0"

C_KEYWORD = os.environ.get("SITE_C_KEYWORD", "c5fd7ff")  # package, func, var, if, range, nil, true
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # "...", `...`, '...'
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # // ... and /* ... */
C_NUMBER = os.environ.get("SITE_C_NUMBER", "c34e2e2")  # 42, 3.5, 0x1f, 1_000, 2i
C_TYPE = os.environ.get("SITE_C_TYPE", "c4e9a06")  # int, string, error, any
C_CONSTANT = os.environ.get("SITE_C_CONSTANT", "cff5f00")  # exported names: Println, Server
C_FUNC = os.environ.get("SITE_C_FUNC", "cff0086")  # the name after func

TARGET_CLASS = "gocode"
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9a-f]{6}"')
UNIT_RE = re.compile(r"(<!--.*?-->|</?[A-Za-z][^>]*>)|(&#?[0-9A-Za-z]+;)|(.)", re.DOTALL)
TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
TAG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")

KEYWORDS = frozenset(
    {
        "break",
        "case",
        "chan",
        "const",
        "continue",
        "default",
        "defer",
        "else",
        "fallthrough",
        "false",
        "for",
        "func",
        "go",
        "goto",
        "if",
        "import",
        "interface",
        "iota",
        "map",
        "nil",
        "package",
        "range",
        "return",
        "select",
        "struct",
        "switch",
        "true",
        "type",
        "var",
    }
)
PREDECLARED_TYPES = frozenset(
    {
        "any",
        "bool",
        "byte",
        "comparable",
        "complex64",
        "complex128",
        "error",
        "float32",
        "float64",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "rune",
        "string",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
    }
)

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER_RE = re.compile(
    r"0[xX][0-9a-fA-F_]+(?:\.[0-9a-fA-F_]*)?(?:[pP][+-]?\d+)?"
    r"|0[bB][01_]+"
    r"|0[oO][0-7_]+"
    r"|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?"
)
SUPPORTED_TAGS = frozenset({"code", "span"})


class GoSyntaxError(ValueError):
    """A block that this lexer refuses to colour."""


class GoLexer:  # pylint: disable=too-many-instance-attributes
    """Colour class for every character of a Go source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end
        self.func_state: str | None = None
        self.paren_depth = 0

    def run(self) -> list[str | None]:
        """Lex the whole source; one colour class or None per character."""
        while self.pos < self.end:
            char = self.src[self.pos]
            if char in " \t\r\n":
                self.pos += 1
                continue
            self.token(char)
        return self.colours

    def token(self, char: str) -> None:
        """One token at self.pos."""
        if char == "/" and self.src.startswith("//", self.pos):
            self.line_comment()
        elif char == "/" and self.src.startswith("/*", self.pos):
            self.block_comment()
        elif char == '"':
            self.interpreted_string()
        elif char == "`":
            self.raw_string()
        elif char == "'":
            self.rune()
        elif char.isdigit() or (char == "." and self.src[self.pos + 1 : self.pos + 2].isdigit()):
            self.number()
        elif char.isalpha() or char == "_":
            self.identifier()
        else:
            self.operator(char)

    def line_number(self) -> int:
        """1-based line of the current position."""
        return self.src.count("\n", 0, self.pos) + 1

    def paint(self, start: int, stop: int, colour: str) -> None:
        """Give characters start..stop-1 one colour."""
        for index in range(start, min(stop, self.end)):
            self.colours[index] = colour

    def line_comment(self) -> None:
        """// to the end of the line."""
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.paint(self.pos, stop, C_COMMENT)
        self.pos = stop

    def block_comment(self) -> None:
        """/* ... */; it may span lines."""
        stop = self.src.find("*/", self.pos + 2)
        if stop == -1:
            raise GoSyntaxError(f"line {self.line_number()}: unterminated /* comment")
        self.paint(self.pos, stop + 2, C_COMMENT)
        self.pos = stop + 2

    def interpreted_string(self) -> None:
        """A "..." literal with backslash escapes; a newline ends it in error."""
        start = self.pos
        pos = start + 1
        while pos < self.end:
            char = self.src[pos]
            if char == "\\":
                pos += 2
            elif char == '"':
                self.paint(start, pos + 1, C_STRING)
                self.pos = pos + 1
                return
            elif char == "\n":
                break
            else:
                pos += 1
        raise GoSyntaxError(f"line {self.line_number()}: unterminated string")

    def raw_string(self) -> None:
        """A `...` literal; it may span lines and has no escapes."""
        stop = self.src.find("`", self.pos + 1)
        if stop == -1:
            raise GoSyntaxError(f"line {self.line_number()}: unterminated raw string")
        self.paint(self.pos, stop + 1, C_STRING)
        self.pos = stop + 1

    def rune(self) -> None:
        """A '...' rune literal with backslash escapes."""
        start = self.pos
        pos = start + 1
        while pos < self.end:
            char = self.src[pos]
            if char == "\\":
                pos += 2
            elif char == "'":
                self.paint(start, pos + 1, C_STRING)
                self.pos = pos + 1
                return
            elif char == "\n":
                break
            else:
                pos += 1
        raise GoSyntaxError(f"line {self.line_number()}: unterminated rune literal")

    def number(self) -> None:
        """42, 1_000, 3.5, 0x1f, 0b101, 1e9, 2i."""
        match = NUMBER_RE.match(self.src, self.pos)
        stop = match.end() if match else self.pos + 1
        if stop < self.end and self.src[stop] == "i":
            stop += 1
        self.paint(self.pos, stop, C_NUMBER)
        self.pos = stop

    def identifier(self) -> None:
        """A keyword, a predeclared type, an exported name or a plain name."""
        match = IDENT_RE.match(self.src, self.pos)
        word = match.group() if match else self.src[self.pos]
        stop = match.end() if match else self.pos + 1
        if word in KEYWORDS:
            self.paint(self.pos, stop, C_KEYWORD)
            self.func_state = "name" if word == "func" else None
        elif self.func_state == "name":
            self.paint(self.pos, stop, C_FUNC)
            self.func_state = None
        elif word in PREDECLARED_TYPES:
            self.paint(self.pos, stop, C_TYPE)
        elif word[0].isupper():
            self.paint(self.pos, stop, C_CONSTANT)
        self.pos = stop

    def operator(self, char: str) -> None:
        """Operators; the parentheses of a method receiver hold the func name."""
        if self.func_state == "name" and char == "(":
            self.func_state = "receiver"
            self.paren_depth = 1
        elif self.func_state == "receiver":
            if char == "(":
                self.paren_depth += 1
            elif char == ")":
                self.paren_depth -= 1
                if self.paren_depth == 0:
                    self.func_state = "name"
        elif self.func_state == "name":
            self.func_state = None
        self.pos += 1


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
    """Coloured inner HTML; raises GoSyntaxError when it cannot be lexed."""
    unsupported = tag_names(inner) - SUPPORTED_TAGS
    if unsupported:
        raise GoSyntaxError(f"tag {sorted(unsupported)[0]} inside the block")
    segments: list[re.Match[str] | None] = list(CODE_RE.finditer(inner))
    if not segments:
        segments = [None]
    texts = [units_text(split_units(segment.group(1))) if segment else units_text(split_units(inner)) for segment in segments]
    colours = GoLexer("\n".join(texts)).run()
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
        raise GoSyntaxError("round trip failed (NOT written)")
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
    """Highlight the gocode blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without gocode is not
    decoded at all; one that has the class but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"gocode" not in raw:
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
        except GoSyntaxError as exc:
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
    parser = argparse.ArgumentParser(description="Highlight Go blocks (gocode) in .php files.")
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
