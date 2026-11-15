#!/usr/bin/env python3
# version 0.1.1
"""Highlight C code in ccode blocks of .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds ccode, skips blocks that
already carry the canonical palette (<span class="cXXXXXX">), and colours
the rest:

    C keywords and preprocessor directives   -> <span class="c5fd7ff">
    strings, character constants, headers    -> <span class="cad7fa8">
    comments (// ... and /* ... */)          -> <span class="c999999">
    numbers (42, 0x1f, 3.5f, 1e-3)           -> <span class="c34e2e2">
    macros and upper-case constants          -> <span class="cff5f00">
    a name followed by '(' (a function)      -> <span class="cff0086">
    a member after '.' or '->'               -> <span class="c4e9a06">

Lower case only: the served sheets define .c5fd7ff and the others in lower
case, and class names are case sensitive, so c5FD7FF would match nothing.
The older blue/orange/lightblue palette that ccode blocks used before is
dropped and replaced by these classes, exactly as tools/rubycode-highlight
re-highlights the old Ruby colours.

A small C lexer of its own, not Pygments: it reads // and /* */ comments,
"..." and '...' literals with their escapes and L/u/U/u8 prefixes,
preprocessor lines (#include <...>, #define NAME), numbers with C suffixes,
macros and upper-case constants, function names and struct members. A block
it cannot lex (an unterminated string, character constant or /* comment, or
a tag other than code/span inside it) is reported and left byte for byte as
it was.

Each <code> line of a block is one line of C: the lines are lexed together
(a block comment can span lines) and coloured one by one, so a colour span
never crosses a line.

Every block is verified: the text without tags, unescaped, equals the
original text without tags, unescaped. A file is written to a temporary
file next to it and renamed into place, so CRLF line endings stay.

Usage:
    python3 highlight_ccode.py [--write] [--quiet] PATH [PATH ...]

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

C_KEYWORD = os.environ.get("SITE_C_KEYWORD", "c5fd7ff")  # if, for, int, #include, #define
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # "...", '...', <stdio.h>
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # // ... and /* ... */
C_NUMBER = os.environ.get("SITE_C_NUMBER", "c34e2e2")  # 42, 0x1f, 3.5f
C_CONSTANT = os.environ.get("SITE_C_CONSTANT", "cff5f00")  # MAX, NULL, a macro name after #define
C_VARIABLE = os.environ.get("SITE_C_VARIABLE", "c4e9a06")  # the member after . or ->
C_FUNCTION = os.environ.get("SITE_C_FUNCTION", "cff0086")  # the name before '('

TARGET_CLASS = "ccode"
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9a-f]{6}"')
UNIT_RE = re.compile(r"(<!--.*?-->|</?[A-Za-z][^>]*>)|(&#?[0-9A-Za-z]+;)|(.)", re.DOTALL)
TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
TAG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")

KEYWORDS = frozenset(
    {
        "_Alignas",
        "_Alignof",
        "_Atomic",
        "_BitInt",
        "_Bool",
        "_Complex",
        "_Decimal128",
        "_Decimal32",
        "_Decimal64",
        "_Generic",
        "_Imaginary",
        "_Noreturn",
        "_Static_assert",
        "_Thread_local",
        "alignas",
        "alignof",
        "asm",
        "auto",
        "bool",
        "break",
        "case",
        "char",
        "const",
        "constexpr",
        "continue",
        "default",
        "do",
        "double",
        "else",
        "enum",
        "extern",
        "false",
        "float",
        "for",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "nullptr",
        "register",
        "restrict",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "static_assert",
        "struct",
        "switch",
        "thread_local",
        "true",
        "typedef",
        "typeof",
        "typeof_unqual",
        "union",
        "unsigned",
        "void",
        "volatile",
        "while",
        "__attribute__",
        "__asm__",
        "__inline",
        "__restrict",
        "__volatile__",
    }
)
INCLUDE_DIRECTIVES = frozenset({"include", "include_next", "import"})
STRING_PREFIXES = frozenset({"L", "u", "U", "u8"})
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER_RE = re.compile(
    r"(?:0[xX][0-9a-fA-F']*(?:\.[0-9a-fA-F']*)?(?:[pP][+-]?\d+)?"
    r"|0[bB][01']+"
    r"|[0-9][0-9']*(?:\.[0-9']*)?(?:[eE][+-]?\d+)?"
    r"|\.[0-9][0-9']*(?:[eE][+-]?\d+)?)"
    r"(?:[uUlLfF]+)?"
)
SUPPORTED_TAGS = frozenset({"code", "span"})


class CSyntaxError(ValueError):
    """A block that this lexer refuses to colour."""


class CLexer:  # pylint: disable=too-many-instance-attributes
    """Colour class for every character of a C source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end
        self.line_start = 0
        self.directive_name = ""
        self.member_pending = False
        self.tag_pending = False

    def run(self) -> list[str | None]:
        """Lex the whole source; one colour class or None per character."""
        while self.pos < self.end:
            char = self.src[self.pos]
            if char == "\n":
                self.pos += 1
                self.line_start = self.pos
                self.directive_name = ""
                self.member_pending = False
                continue
            if char in " \t\r":
                self.pos += 1
                continue
            if self.pos == self.line_start and char == "#":
                self.directive()
                continue
            self.token(char)
        return self.colours

    def line_number(self) -> int:
        """1-based line of the current position."""
        return self.src.count("\n", 0, self.pos) + 1

    def paint(self, start: int, stop: int, colour: str) -> None:
        """Give characters start..stop-1 one colour."""
        for index in range(start, min(stop, self.end)):
            self.colours[index] = colour

    def token(self, char: str) -> None:
        """One token at self.pos."""
        if char == "/" and self.src.startswith("//", self.pos):
            self.line_comment()
        elif char == "/" and self.src.startswith("/*", self.pos):
            self.block_comment()
        elif char in ('"', "'"):
            self.literal(char, self.pos)
        elif char == "-" and self.src.startswith("->", self.pos):
            self.pos += 2
            self.member_pending = True
        elif char == "." and not (self.pos + 1 < self.end and self.src[self.pos + 1].isdigit()):
            self.pos += 1
            self.member_pending = True
        elif char.isdigit() or (char == "." and self.pos + 1 < self.end and self.src[self.pos + 1].isdigit()):
            self.number()
        elif char.isalpha() or char == "_":
            self.identifier()
        else:
            if char in ")]}":
                self.member_pending = False
            self.pos += 1

    def line_comment(self) -> None:
        """// to the end of the line."""
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.paint(self.pos, stop, C_COMMENT)
        self.pos = stop

    def block_comment(self) -> None:
        """/* .. */, which may span lines."""
        start = self.pos
        stop = self.src.find("*/", start + 2)
        if stop == -1:
            raise CSyntaxError(f"line {self.line_number()}: unterminated /* comment")
        self.paint(start, stop + 2, C_COMMENT)
        self.pos = stop + 2

    def literal(self, quote: str, start: int, paint_from: int | None = None) -> None:
        """'...' or "...", escapes included; the whole literal is one string."""
        paint_start = start if paint_from is None else paint_from
        pos = start + 1
        while pos < self.end:
            char = self.src[pos]
            if char == "\\":
                pos += 2
            elif char == quote:
                self.paint(paint_start, pos + 1, C_STRING)
                self.pos = pos + 1
                self.member_pending = False
                return
            elif char == "\n":
                break
            else:
                pos += 1
        kind = "string" if quote == '"' else "character constant"
        raise CSyntaxError(f"line {self.line_number()}: unterminated {kind}")

    def number(self) -> None:
        """42, 0x1f, 0b101, 0755, 3.5f, 1e-3."""
        match = NUMBER_RE.match(self.src, self.pos)
        stop = match.end() if match else self.pos + 1
        self.paint(self.pos, stop, C_NUMBER)
        self.pos = stop
        self.member_pending = False

    def identifier(self) -> None:
        """A keyword, a directive, a constant, a function name or plain text."""
        match = IDENT_RE.match(self.src, self.pos)
        word = match.group() if match else self.src[self.pos]
        stop = match.end() if match else self.pos + 1

        if self.directive_name == "define" and self.defining_macro():
            self.paint(self.pos, stop, C_CONSTANT)
            self.pos = stop
            return

        if word in STRING_PREFIXES and stop < self.end and self.src[stop] in "\"'":
            self.literal(self.src[stop], stop, self.pos)
            return

        if word in KEYWORDS:
            self.paint(self.pos, stop, C_KEYWORD)
            self.pos = stop
            if word in ("struct", "union", "enum"):
                self.member_pending = False
                self.tag_pending = True
            return

        if self.tag_pending:
            self.paint(self.pos, stop, C_CONSTANT)
            self.tag_pending = False
            self.member_pending = False
        elif self.member_pending:
            self.paint(self.pos, stop, C_VARIABLE)
            self.member_pending = False
        elif self.next_is_call(stop):
            self.paint(self.pos, stop, C_FUNCTION)
        elif is_constant(word):
            self.paint(self.pos, stop, C_CONSTANT)
        self.pos = stop

    def defining_macro(self) -> bool:
        """Whether the identifier at self.pos is the name after #define."""
        before = self.src[self.line_start : self.pos]
        return re.fullmatch(r"\s*#\s*define\s*", before) is not None

    def next_is_call(self, pos: int) -> bool:
        """Whether the next non-blank character is '(' (a function name)."""
        while pos < self.end and self.src[pos] in " \t":
            pos += 1
        return pos < self.end and self.src[pos] == "("

    def directive(self) -> None:
        """A preprocessor line: #, the directive name, and #include <...>."""
        start = self.pos
        match = re.match(r"#\s*([A-Za-z_][A-Za-z0-9_]*)", self.src[start:])
        if not match:
            self.paint(start, start + 1, C_KEYWORD)
            self.pos = start + 1
            return
        self.paint(start, start + match.end(0), C_KEYWORD)
        self.directive_name = match.group(1)
        self.pos = start + match.end(0)
        if self.directive_name not in INCLUDE_DIRECTIVES:
            return
        while self.pos < self.end and self.src[self.pos] in " \t":
            self.pos += 1
        if self.pos < self.end and self.src[self.pos] == "<":
            stop = self.src.find(">", self.pos)
            if stop == -1:
                raise CSyntaxError(f"line {self.line_number()}: unterminated <header>")
            self.paint(self.pos, stop + 1, C_STRING)
            self.pos = stop + 1


def is_constant(word: str) -> bool:
    """An upper-case C name: MAX_SIZE, EOF, NULL, __FILE__."""
    return any(char.isalpha() for char in word) and word == word.upper()


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


def tag_name(tag: str) -> str:
    """Lower-case name of one tag, empty for a comment."""
    match = TAG_NAME_RE.match(tag)
    return match.group(1).lower() if match else ""


def tag_names(inner: str) -> set[str]:
    """Lower-case names of the tags inside a block."""
    return {match.group(1).lower() for match in TAG_NAME_RE.finditer(inner)}


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


def highlight_inner(inner: str) -> str:
    """Coloured inner HTML; raises CSyntaxError when it cannot be lexed."""
    unsupported = tag_names(inner) - SUPPORTED_TAGS
    if unsupported:
        raise CSyntaxError(f"tag {sorted(unsupported)[0]} inside the block")
    matches = list(CODE_RE.finditer(inner))
    if not matches:
        colours = CLexer(units_text(split_units(inner))).run()
        result = coloured_units(split_units(inner), colours, 0)
        if strip_and_decode(result) != strip_and_decode(inner):
            raise CSyntaxError("round trip failed (NOT written)")
        return result
    texts = [units_text(split_units(match.group(1))) for match in matches]
    colours = CLexer("\n".join(texts)).run()
    out: list[str] = []
    cursor = 0
    offset = 0
    for match, text in zip(matches, texts, strict=True):
        out.append(inner[cursor : match.start(1)])
        out.append(coloured_units(split_units(match.group(1)), colours, offset))
        offset += len(text) + 1
        cursor = match.end(1)
    out.append(inner[cursor:])
    result = "".join(out)
    if strip_and_decode(result) != strip_and_decode(inner):
        raise CSyntaxError("round trip failed (NOT written)")
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
    """Highlight the ccode blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without ccode is
    not decoded at all; one that has the class but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"ccode" not in raw:
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
        except CSyntaxError as exc:
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
    parser = argparse.ArgumentParser(description="Highlight C blocks (ccode) in .php files.")
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
            action = "fixed" if args.write else "to fix"
            print(f"{path}: highlighted already={stats.highlighted} {action}={stats.fixed}")
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
