#!/usr/bin/env python3
# version 0.1.0
"""Highlight C++ code in cppcode blocks of .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds cppcode, skips blocks
that already carry the canonical palette (<span class="cXXXXXX">), and
colours the rest:

    keywords and built-in types (int, if, return, class)  -> <span class="c5fd7ff">
    string and character literals ("...", 'c')            -> <span class="cad7fa8">
    comments (// ..., /* ... */)                          -> <span class="c999999">
    preprocessor directives (#include, #pragma)           -> <span class="cfce94f">
    numbers (42, 3.5, 0x1f, 1'000)                        -> <span class="c34e2e2">
    type and constant names (Point, SIZE)                 -> <span class="cff5f00">
    std and the names after :: (std::cout, vector)        -> <span class="c4e9a06">
    the name after class/struct/enum/union/namespace      -> <span class="cff0086">

Lower case only: the served sheets define .c5fd7ff and the others in lower
case, and class names are case sensitive, so c5FD7FF would match nothing.
The older blue/orange/lightblue/violet/yellow palette that cppcode blocks
used before is dropped and replaced by these classes, exactly as
tools/rubycode-highlight re-highlights the old Ruby colours.

A small C++ lexer of its own, not Pygments: it reads // and /* */ comments
(not inside a string), "..." and '...' with escapes, raw strings R"(...)",
# directives at the start of a line with the <header> of #include, numbers
with digit separators and suffixes, and identifiers. A block it cannot lex
(unterminated string, raw string or comment, or a tag other than code,
span, b, strong, i and em inside it) is reported and left byte for byte as
it was.

Each <code> line of a block is one line of C++: the lines are lexed
together (# directives need the line starts) and coloured one by one, so a
colour span never crosses a line. The <code> tag is written back without
attributes, so a hand-made class on a line (class="green") is dropped.

Every block is verified: the text without tags, unescaped, equals the
original text without tags, unescaped. A file is written to a temporary
file next to it and renamed into place, so CRLF line endings stay.

Usage:
    python3 highlight_cppcode.py [--write] [--quiet] PATH [PATH ...]

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

C_KEYWORD = os.environ.get("SITE_C_KEYWORD", "c5fd7ff")  # int, if, return, class
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # "..." and 'c'
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # // ... and /* ... */
C_DIRECTIVE = os.environ.get("SITE_C_DIRECTIVE", "cfce94f")  # #include, #pragma
C_NUMBER = os.environ.get("SITE_C_NUMBER", "c34e2e2")  # 42, 3.5, 0x1f
C_CONSTANT = os.environ.get("SITE_C_CONSTANT", "cff5f00")  # Point, SIZE
C_LIBRARY = os.environ.get("SITE_C_LIBRARY", "c4e9a06")  # std, cout after ::
C_TYPE = os.environ.get("SITE_C_TYPE", "cff0086")  # the name after class, struct, enum

TARGET_CLASS = "cppcode"
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9a-f]{6}"')
# A tag of a cppcode block: a known name, then either whitespace and
# attributes that hold no angle bracket, or the end of the tag. C++ is full
# of "<" (a < b, p->x, vector<int>), so an unknown name is not a tag.
TAG_RE = re.compile(
    r"<!--.*?-->|</?(?:code|span|b|strong|i|em)(?:\s[^<>]*)?/?>",
    re.DOTALL | re.IGNORECASE,
)
UNIT_RE = re.compile(r"(" + TAG_RE.pattern + r")|(&#?[0-9A-Za-z]+;)|(.)", re.DOTALL | re.IGNORECASE)
TAG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")

KEYWORDS = frozenset(
    {
        "alignas",
        "alignof",
        "and",
        "and_eq",
        "asm",
        "auto",
        "bitand",
        "bitor",
        "bool",
        "break",
        "case",
        "catch",
        "char",
        "char8_t",
        "char16_t",
        "char32_t",
        "class",
        "compl",
        "concept",
        "const",
        "consteval",
        "constexpr",
        "constinit",
        "const_cast",
        "continue",
        "co_await",
        "co_return",
        "co_yield",
        "decltype",
        "default",
        "delete",
        "do",
        "double",
        "dynamic_cast",
        "else",
        "enum",
        "explicit",
        "export",
        "extern",
        "false",
        "float",
        "for",
        "friend",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "mutable",
        "namespace",
        "new",
        "noexcept",
        "not",
        "not_eq",
        "nullptr",
        "operator",
        "or",
        "or_eq",
        "private",
        "protected",
        "public",
        "register",
        "reinterpret_cast",
        "requires",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "static_assert",
        "static_cast",
        "struct",
        "switch",
        "template",
        "this",
        "thread_local",
        "throw",
        "true",
        "try",
        "typedef",
        "typeid",
        "typename",
        "union",
        "unsigned",
        "using",
        "virtual",
        "void",
        "volatile",
        "wchar_t",
        "while",
        "xor",
        "xor_eq",
        # names of the fixed-width integer types, coloured like the types
        "size_t",
        "ptrdiff_t",
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
    }
)
# keywords that introduce the name of a type or a namespace
DEFINING_KEYWORDS = frozenset({"class", "struct", "enum", "union", "namespace"})
# of those, the ones that may come before another one (enum class Color)
STACKING_KEYWORDS = frozenset({"class", "struct"})
# directives whose <header> is a string
HEADER_DIRECTIVES = frozenset({"include", "include_next", "import"})

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER_RE = re.compile(
    r"0[xX][0-9a-fA-F']+|0[bB][01']+|\d[\d']*(?:\.\d[\d']*)?(?:[eE][+-]?\d+)?"
    r"(?:[uUlLfFzZ]*)"
)
RAW_STRING_RE = re.compile(r'R"([^()\\\s]*)\((.*?)\)\1"', re.DOTALL)
SUPPORTED_TAGS = frozenset({"code", "span", "b", "strong", "i", "em"})


class CppSyntaxError(ValueError):
    """A block that this lexer refuses to colour."""


class CppLexer:  # pylint: disable=too-many-instance-attributes
    """Colour class for every character of a C++ source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end
        self.type_pending = False
        self.after_scope = False
        self.prev_word = ""

    def run(self) -> list[str | None]:
        """Lex the whole source; one colour class or None per character."""
        while self.pos < self.end:
            char = self.src[self.pos]
            if char == "\n":
                self.pos += 1
                continue
            if char in " \t\r":
                self.pos += 1
                continue
            self.token(char)
        return self.colours

    def token(self, char: str) -> None:  # pylint: disable=too-many-branches
        """One token at self.pos."""
        nxt = self.src[self.pos + 1] if self.pos + 1 < self.end else ""
        if char == "/" and nxt == "/":
            self.line_comment()
        elif char == "/" and nxt == "*":
            self.block_comment()
        elif char == "#" and self.at_line_start():
            self.directive()
        elif char in "'\"":
            self.string(char)
        elif char.isdigit():
            self.number()
        elif char.isalpha() or char == "_":
            self.identifier()
        else:
            self.punctuation(char)

    def line_number(self) -> int:
        """1-based line of the current position."""
        return self.src.count("\n", 0, self.pos) + 1

    def paint(self, start: int, stop: int, colour: str) -> None:
        """Give characters start..stop-1 one colour."""
        for index in range(start, min(stop, self.end)):
            self.colours[index] = colour

    def at_line_start(self) -> bool:
        """Whether only spaces and tabs come before pos on its line."""
        start = self.src.rfind("\n", 0, self.pos) + 1
        return self.src[start : self.pos].strip(" \t") == ""

    def line_comment(self) -> None:
        """// to the end of the line."""
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.paint(self.pos, stop, C_COMMENT)
        self.pos = stop

    def block_comment(self) -> None:
        """/* ... */, which may span lines."""
        stop = self.src.find("*/", self.pos + 2)
        if stop == -1:
            raise CppSyntaxError(f"line {self.line_number()}: unterminated /* comment")
        self.paint(self.pos, stop + 2, C_COMMENT)
        self.pos = stop + 2

    def string(self, quote: str) -> None:
        """'...' or "...", escapes included; unterminated is an error."""
        start = self.pos
        pos = start + 1
        while pos < self.end:
            char = self.src[pos]
            if char == "\\":
                pos += 2
            elif char == quote:
                pos += 1
                self.paint(start, pos, C_STRING)
                self.pos = pos
                return
            elif char == "\n":
                break
            else:
                pos += 1
        kind = "character" if quote == "'" else "string"
        raise CppSyntaxError(f"line {self.line_number()}: unterminated {kind} literal")

    def raw_string(self) -> bool:
        """R"(...)" and R"tag(...)tag": the whole literal is a string."""
        match = RAW_STRING_RE.match(self.src, self.pos)
        if not match:
            return False
        self.paint(self.pos, match.end(), C_STRING)
        self.pos = match.end()
        return True

    def directive(self) -> None:
        """#include, #pragma, #define: the directive and the <header>."""
        start = self.pos
        pos = start + 1
        while pos < self.end and self.src[pos] in " \t":
            pos += 1
        name_start = pos
        while pos < self.end and self.src[pos].isalpha():
            pos += 1
        name = self.src[name_start:pos]
        self.paint(start, pos, C_DIRECTIVE)
        if name in HEADER_DIRECTIVES:
            while pos < self.end and self.src[pos] in " \t":
                pos += 1
            if pos < self.end and self.src[pos] == "<":
                close = self.src.find(">", pos + 1)
                line_end = self.src.find("\n", pos + 1)
                if close != -1 and (line_end == -1 or close < line_end):
                    self.paint(pos, close + 1, C_STRING)
                    pos = close + 1
        self.pos = pos
        self.type_pending = False
        self.after_scope = False

    def number(self) -> None:
        """42, 1'000, 3.5, 0x1f, 0b101, 10u."""
        match = NUMBER_RE.match(self.src, self.pos)
        stop = match.end() if match else self.pos + 1
        self.paint(self.pos, stop, C_NUMBER)
        self.pos = stop
        self.type_pending = False
        self.after_scope = False

    def identifier(self) -> None:
        """A keyword, the name of a defined type, std, or a plain name."""
        src = self.src
        if src.startswith('R"', self.pos) and self.raw_string():
            self.type_pending = False
            self.after_scope = False
            return
        match = IDENT_RE.match(src, self.pos)
        word = match.group() if match else src[self.pos]
        stop = match.end() if match else self.pos + 1
        pending = self.type_pending
        if word in KEYWORDS:
            self.paint(self.pos, stop, C_KEYWORD)
            if word == "namespace" and self.prev_word == "using":
                self.type_pending = False
            elif word in DEFINING_KEYWORDS:
                self.type_pending = True
            elif not (pending and word in STACKING_KEYWORDS):
                self.type_pending = False
            self.after_scope = False
        elif pending:
            self.paint(self.pos, stop, C_TYPE)
            self.type_pending = False
            self.after_scope = False
        elif self.after_scope or word == "std":
            self.paint(self.pos, stop, C_LIBRARY)
            self.after_scope = False
        else:
            if word[0].isupper() or (word.isupper() and len(word) > 1):
                self.paint(self.pos, stop, C_CONSTANT)
            self.after_scope = False
        self.prev_word = word
        self.pos = stop

    def punctuation(self, char: str) -> None:
        """Operators; :: makes the next name a library name."""
        if char == ":" and self.src.startswith("::", self.pos):
            self.pos += 2
            self.after_scope = True
            self.type_pending = False
            return
        self.pos += 1
        self.after_scope = False
        if char not in " \t\r\n":
            self.type_pending = False


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
                out.append("<code>")
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
    """Coloured inner HTML; raises CppSyntaxError when it cannot be lexed."""
    unsupported = tag_names(inner) - SUPPORTED_TAGS
    if unsupported:
        raise CppSyntaxError(f"tag {sorted(unsupported)[0]} inside the block")
    segments: list[re.Match[str] | None] = list(CODE_RE.finditer(inner))
    if not segments:
        segments = [None]
    texts = [units_text(split_units(segment.group(1))) if segment else units_text(split_units(inner)) for segment in segments]
    colours = CppLexer("\n".join(texts)).run()
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
    # A hand-made class on a line (class="green") is dropped: the colours
    # below come from the lexer only.
    result = re.sub(r"<code\b[^>]*>", "<code>", result, flags=re.IGNORECASE)
    result = re.sub(r"</code\s*>", "</code>", result, flags=re.IGNORECASE)
    if strip_and_decode(result) != strip_and_decode(inner):
        raise CppSyntaxError("round trip failed (NOT written)")
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
    """Highlight the cppcode blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without cppcode is
    not decoded at all; one that has the class but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"cppcode" not in raw:
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
        except CppSyntaxError as exc:
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
    parser = argparse.ArgumentParser(description="Highlight C++ blocks (cppcode) in .php files.")
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
