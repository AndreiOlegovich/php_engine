#!/usr/bin/env python3
# version 0.1.1
"""Highlight Ruby code in rubycode blocks of .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds rubycode, skips blocks
that already carry the canonical palette (<span class="cXXXXXX">), and
colours the rest:

    keywords (def, end, if, do, ...)        -> <span class="c5fd7ff">
    strings, heredocs, %w[] %i[] %q()       -> <span class="cad7fa8">
    comments (# ..., =begin..=end)          -> <span class="c999999">
    symbols (:admin, :"a b")                -> <span class="cfce94f">
    numbers                                 -> <span class="c34e2e2">
    constants (ENV, LIMIT, __FILE__)        -> <span class="cff5f00">
    @instance and $global variables         -> <span class="c4e9a06">
    the name after def (also def <=>)       -> <span class="cff0086">

Lower case only: the served sheets define .c5fd7ff and the others in lower
case, and class names are case sensitive, so c5FD7FF would match nothing.
The older blue/orange/lightblue palette that rubycode blocks used before is
dropped and replaced by these classes, exactly as tools/pycode-highlight
re-highlights the old Python colours.

A small Ruby lexer of its own, not Pygments: it reads # comments (not #
inside a string), "..." with #{...} interpolation, '...', `...`,
%w[] %W[] %i[] %I[] %q() %Q{} %x<> %r// %s, heredocs (<<EOS, <<-EOS,
<<~EOS), symbols versus the colon of a ? : ternary, /regex/ versus
division, =begin/=end block comments at column 0, __END__ and the data
after it. A block it cannot lex (unterminated string, heredoc, % literal
or =begin, or a tag other than code/span inside it) is reported and left
byte for byte as it was.

Each <code> line of a block is one line of Ruby: the lines are lexed
together (heredocs and =begin need that) and coloured one by one, so a
colour span never crosses a line.

Every block is verified: the text without tags, unescaped, equals the
original text without tags, unescaped. A file is written to a temporary
file next to it and renamed into place, so CRLF line endings stay.

Usage:
    python3 highlight_rubycode.py [--write] [--quiet] PATH [PATH ...]

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

C_KEYWORD = os.environ.get("SITE_C_KEYWORD", "c5fd7ff")  # def, end, if, do
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # strings, heredocs, % literals, regex literals
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # # comments and =begin .. =end
C_SYMBOL = os.environ.get("SITE_C_SYMBOL", "cfce94f")  # :admin
C_NUMBER = os.environ.get("SITE_C_NUMBER", "c34e2e2")  # 42, 3.5, 0x1f
C_CONSTANT = os.environ.get("SITE_C_CONSTANT", "cff5f00")  # ENV, LIMIT, __FILE__
C_VARIABLE = os.environ.get("SITE_C_VARIABLE", "c4e9a06")  # @name, @@count, $stdout
C_DEF = os.environ.get("SITE_C_DEF", "cff0086")  # the name after def

TARGET_CLASS = "rubycode"
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9a-f]{6}"')
UNIT_RE = re.compile(r"(<!--.*?-->|</?[A-Za-z][^>]*>)|(&#?[0-9A-Za-z]+;)|(.)", re.DOTALL)
TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
TAG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")

KEYWORDS = frozenset(
    {
        "BEGIN",
        "END",
        "alias",
        "and",
        "begin",
        "break",
        "case",
        "class",
        "def",
        "defined?",
        "do",
        "else",
        "elsif",
        "end",
        "ensure",
        "false",
        "for",
        "if",
        "in",
        "module",
        "next",
        "nil",
        "not",
        "or",
        "redo",
        "rescue",
        "retry",
        "return",
        "self",
        "super",
        "then",
        "true",
        "undef",
        "unless",
        "until",
        "when",
        "while",
        "yield",
    }
)
# keywords after which the expression is complete: / after them is division
VALUE_KEYWORDS = frozenset({"end", "nil", "true", "false", "self", "super"})
MAGIC_CONSTANTS = frozenset({"__FILE__", "__LINE__", "__dir__", "__method__", "__ENCODING__"})

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*[?!]?")
MAGIC_RE = re.compile(r"__[A-Za-z]+__")
NUMBER_RE = re.compile(r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?")
VARIABLE_RE = re.compile(r"@@?[A-Za-z_][A-Za-z0-9_]*|\$[A-Za-z_][A-Za-z0-9_]*|\$\d+|\$[!@&`'+~=/\\,;.<>_*?:\"$-]")
HEREDOC_RE = re.compile(r"<<([~-]?)(?:([A-Za-z_][A-Za-z0-9_]*)|'([^']*)'|\"([^\"]*)\")")
PERCENT_RE = re.compile(r"%([qQwWiIxrs]?)([^\w\s])")
PAIRED = {"(": ")", "[": "]", "{": "}", "<": ">"}
SYMBOL_OPERATORS = "+-*/%<>=!~^&|[]"
OPERATORS = (
    "<=>",
    "===",
    "**=",
    "<<=",
    ">>=",
    "&&=",
    "||=",
    "==",
    "!=",
    ">=",
    "<=",
    "=~",
    "!~",
    "**",
    "<<",
    ">>",
    "&&",
    "||",
    "::",
    "=>",
    "->",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "|=",
    "&=",
    "^=",
    "...",
    "..",
    "&.",
    "[]=",
    "[]",
)
SUPPORTED_TAGS = frozenset({"code", "span"})
# operators that can also be the name of a method: def <=>, def []=, def <<
DEF_OPERATORS = frozenset(["<=>", "[]", "[]=", "<<", ">>", "==", "===", "**", "+", "-", "*", "/", "%", "!", "~", "&", "|", "^", "<", ">"])


class RubySyntaxError(ValueError):
    """A block that this lexer refuses to colour."""


class RubyLexer:  # pylint: disable=too-many-public-methods,too-many-instance-attributes
    """Colour class for every character of a Ruby source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end
        self.heredocs: list[tuple[str, str]] = []
        self.line_start = 0
        self.prev = "operator"  # value | operator: decides /regex/ from a / b
        self.def_pending = False

    def run(self) -> list[str | None]:
        """Lex the whole source; one colour class or None per character."""
        while self.pos < self.end:
            char = self.src[self.pos]
            if char == "\n":
                self.pos += 1
                self.line_start = self.pos
                if self.heredocs:
                    self.heredoc_bodies()
                continue
            if self.pos == self.line_start and self.line_marker():
                continue
            if char in " \t\r":
                self.pos += 1
                continue
            self.token(char)
        return self.colours

    def line_marker(self) -> bool:
        """=begin, =end and __END__ are only markers at column 0."""
        if self.src.startswith("=begin", self.pos) and self.word_end(self.pos + 6):
            self.block_comment()
            return True
        if self.src.startswith("=end", self.pos) and self.word_end(self.pos + 4):
            raise RubySyntaxError(f"line {self.line_number()}: =end without =begin")
        if self.src.startswith("__END__", self.pos) and self.word_end(self.pos + 7):
            self.pos = self.end
            return True
        return False

    def token(self, char: str) -> None:  # pylint: disable=too-many-branches
        """One token at self.pos."""
        if char == "#":
            self.comment()
        elif char in "'\"`":
            self.string(char)
        elif char == "%" and self.percent_literal():
            return
        elif char == "/" and self.prev != "value":
            self.regex()
        elif char == ":" and self.symbol() or char == "<" and self.heredoc_start():
            return
        elif char in "@$":
            self.variable()
        elif char.isdigit():
            self.number()
        elif char.isalpha() or char == "_":
            self.identifier()
        else:
            self.operator()

    def line_number(self) -> int:
        """1-based line of the current position."""
        return self.src.count("\n", 0, self.pos) + 1

    def paint(self, start: int, stop: int, colour: str) -> None:
        """Give characters start..stop-1 one colour."""
        for index in range(start, min(stop, self.end)):
            self.colours[index] = colour

    def word_end(self, pos: int) -> bool:
        """Whether a keyword that ends at pos ends there and not inside a word."""
        return pos >= self.end or not (self.src[pos].isalnum() or self.src[pos] == "_")

    def at_line_start(self, pos: int) -> bool:
        """Whether pos is the first column of a line."""
        return pos == self.src.rfind("\n", 0, pos) + 1

    def comment(self) -> None:
        """# to the end of the line."""
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.paint(self.pos, stop, C_COMMENT)
        self.pos = stop

    def block_comment(self) -> None:
        """=begin .. =end, both at column 0."""
        start = self.pos
        pos = self.src.find("=end", start + 6)
        while pos != -1 and not (self.at_line_start(pos) and self.word_end(pos + 4)):
            pos = self.src.find("=end", pos + 4)
        if pos == -1:
            raise RubySyntaxError(f"line {self.line_number()}: =begin without =end")
        stop = self.src.find("\n", pos)
        stop = self.end if stop == -1 else stop
        self.paint(start, stop, C_COMMENT)
        self.pos = stop

    def string(self, quote: str) -> None:
        """'...', "..." and `...`; " and ` read #{...} interpolation."""
        start = self.pos
        stop = self.string_end(self.pos, quote, interpolation=quote != "'")
        self.paint(start, stop, C_STRING)
        self.pos = stop
        self.prev = "value"

    def string_end(self, pos: int, quote: str, *, interpolation: bool) -> int:
        """Index after the quote that ends the string opened at pos."""
        src = self.src
        pos += 1
        while pos < self.end:
            char = src[pos]
            if char == "\\":
                pos += 2
            elif char == quote:
                return pos + 1
            elif interpolation and char == "#" and src.startswith("{", pos + 1):
                pos = self.interpolation(pos + 2)
            else:
                pos += 1
        raise RubySyntaxError(f"line {self.line_number()}: unterminated {quote} string")

    def interpolation(self, pos: int) -> int:
        """Index after the } that closes #{; quotes inside are skipped."""
        src = self.src
        depth = 1
        while pos < self.end:
            char = src[pos]
            if char == "\\":
                pos += 2
            elif char in "'\"`":
                pos = self.string_end(pos, char, interpolation=False)
            elif char == "{":
                depth += 1
                pos += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return pos + 1
                pos += 1
            else:
                pos += 1
        raise RubySyntaxError(f"line {self.line_number()}: unclosed #{{...}}")

    def percent_literal(self) -> bool:
        """%w[...] %i(...) %q{} %Q<> %() %r// %x: the whole literal is a string."""
        if self.prev == "value":
            return False
        match = PERCENT_RE.match(self.src, self.pos)
        if not match:
            return False
        kind = match.group(1)
        opening = match.group(2)
        closing = PAIRED.get(opening, opening)
        pos = match.end()
        depth = 1
        while pos < self.end:
            char = self.src[pos]
            if char == "\\":
                pos += 2
            elif char == "#" and self.src.startswith("{", pos + 1):
                pos = self.interpolation(pos + 2)
            elif char == opening and closing != opening:
                depth += 1
                pos += 1
            elif char == closing:
                depth -= 1
                pos += 1
                if depth == 0:
                    break
            else:
                pos += 1
        else:
            raise RubySyntaxError(f"line {self.line_number()}: unclosed %{kind}{opening}")
        self.paint(self.pos, pos, C_STRING)
        self.pos = pos
        self.prev = "value"
        return True

    def heredoc_start(self) -> bool:
        """<<EOS, <<-EOS, <<~EOS and the quoted forms; << alone is a shift."""
        match = HEREDOC_RE.match(self.src, self.pos)
        if not match:
            return False
        squiggle = match.group(1)
        bare = match.group(2)
        if bare is not None and not squiggle and not (bare[0].isupper() or bare[0] == "_"):
            return False
        quoted = match.group(3) if match.group(3) is not None else match.group(4)
        delimiter = bare if bare is not None else (quoted or "")
        self.paint(self.pos, match.end(), C_STRING)
        self.pos = match.end()
        self.heredocs.append((delimiter, squiggle))
        self.prev = "value"
        return True

    def heredoc_bodies(self) -> None:
        """Paint the bodies of the heredocs that start on the line just read."""
        src = self.src
        for delimiter, squiggle in self.heredocs:
            painted = self.pos
            closed = False
            while self.pos < self.end:
                newline = src.find("\n", self.pos)
                line_end = self.end if newline == -1 else newline
                line = src[self.pos : line_end]
                self.pos = line_end if newline == -1 else newline + 1
                if self.terminates(line, delimiter, squiggle):
                    self.paint(painted, line_end, C_STRING)
                    self.pos = line_end
                    closed = True
                    break
            if not closed:
                message = f"line {self.line_number()}: unterminated heredoc {delimiter}"
                raise RubySyntaxError(message)
        self.heredocs.clear()

    @staticmethod
    def terminates(line: str, delimiter: str, squiggle: str) -> bool:
        """Whether a heredoc line is the closing delimiter."""
        if squiggle == "~":
            return line.lstrip(" \t") == delimiter
        if squiggle == "-":
            return line.lstrip("\t") == delimiter
        return line == delimiter

    def regex(self) -> None:
        """A /regex/ literal: not after a value, else it is division."""
        src = self.src
        start = self.pos
        pos = start + 1
        in_class = False
        while pos < self.end:
            char = src[pos]
            if char == "\\":
                pos += 2
            elif char == "[":
                in_class = True
                pos += 1
            elif char == "]":
                in_class = False
                pos += 1
            elif char == "/" and not in_class:
                pos += 1
                while pos < self.end and src[pos] in "imxouesn":
                    pos += 1
                self.paint(start, pos, C_STRING)
                self.pos = pos
                self.prev = "value"
                return
            elif char == "\n":
                break
            else:
                pos += 1
        raise RubySyntaxError(f"line {self.line_number()}: unterminated /regex/")

    def symbol(self) -> bool:
        """:name, :"quoted", :+ — but a label and a ? : colon are not symbols."""
        src = self.src
        if self.pos > 0:
            before = src[self.pos - 1]
            if before.isalnum() or before in "_)]}":
                return False  # name: is a hash label, not a symbol
        nxt = self.pos + 1
        if nxt >= self.end:
            return False
        char = src[nxt]
        if char in "'\"":
            stop = self.string_end(nxt, char, interpolation=False)
            self.paint(self.pos, stop, C_SYMBOL)
            self.pos = stop
            self.prev = "value"
            return True
        if char in SYMBOL_OPERATORS:
            match = re.match(r":[\[\]]=?|[:][^\s\w]+", src[self.pos :])
            stop = self.pos + match.end() if match else self.pos + 2
            self.paint(self.pos, stop, C_SYMBOL)
            self.pos = stop
            self.prev = "value"
            return True
        if char.isalpha() or char == "_":
            match = IDENT_RE.match(src, nxt)
            stop = match.end() if match else nxt + 1
            self.paint(self.pos, stop, C_SYMBOL)
            self.pos = stop
            self.prev = "value"
            return True
        return False

    def variable(self) -> None:
        """@name, @@count, $stdout, $1 and the punctuation globals."""
        match = VARIABLE_RE.match(self.src, self.pos)
        if not match:
            self.pos += 1
            self.prev = "operator"
            return
        self.paint(self.pos, match.end(), C_VARIABLE)
        self.pos = match.end()
        self.prev = "value"

    def number(self) -> None:
        """42, 1_000, 3.5, 0x1f, 0b101."""
        match = NUMBER_RE.match(self.src, self.pos)
        stop = match.end() if match else self.pos + 1
        self.paint(self.pos, stop, C_NUMBER)
        self.pos = stop
        self.prev = "value"

    def identifier(self) -> None:
        """A keyword, a constant, a def name or a plain name."""
        src = self.src
        magic = MAGIC_RE.match(src, self.pos)
        if magic:
            self.paint(self.pos, magic.end(), C_CONSTANT)
            self.pos = magic.end()
            self.prev = "value"
            return
        match = IDENT_RE.match(src, self.pos)
        word = match.group() if match else src[self.pos]
        stop = match.end() if match else self.pos + 1
        if word in KEYWORDS:
            self.paint(self.pos, stop, C_KEYWORD)
            self.pos = stop
            self.prev = "operator" if word not in VALUE_KEYWORDS else "value"
            if word == "def":
                self.def_pending = True
            elif word != "self":
                self.def_pending = False
            return
        if self.def_pending and not self.next_is_dot(stop):
            self.paint(self.pos, stop, C_DEF)
            self.def_pending = False
        elif word[0].isupper():
            self.paint(self.pos, stop, C_CONSTANT)
        self.pos = stop
        self.prev = "value"

    def next_is_dot(self, pos: int) -> bool:
        """Whether the next non-blank character is a dot (def self.name)."""
        while pos < self.end and self.src[pos] in " \t":
            pos += 1
        return pos < self.end and self.src[pos] in "."

    def operator(self) -> None:
        """Operators; a def name such as <=> or [] is coloured as a def name."""
        src = self.src
        for operator in OPERATORS:
            if src.startswith(operator, self.pos):
                stop = self.pos + len(operator)
                if self.def_pending and operator in DEF_OPERATORS:
                    self.paint(self.pos, stop, C_DEF)
                    self.def_pending = False
                self.pos = stop
                self.prev = "value" if operator in (")", "]", "}") else "operator"
                return
        char = src[self.pos]
        if self.def_pending and char in "+-*/%<>=!~&|^":
            self.paint(self.pos, self.pos + 1, C_DEF)
            self.def_pending = False
        self.pos += 1
        self.prev = "value" if char in ")]}" else "operator"


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
    """Coloured inner HTML; raises RubySyntaxError when it cannot be lexed."""
    unsupported = tag_names(inner) - SUPPORTED_TAGS
    if unsupported:
        raise RubySyntaxError(f"tag {sorted(unsupported)[0]} inside the block")
    segments: list[re.Match[str] | None] = list(CODE_RE.finditer(inner))
    if not segments:
        segments = [None]
    texts = [units_text(split_units(segment.group(1))) if segment else units_text(split_units(inner)) for segment in segments]
    colours = RubyLexer("\n".join(texts)).run()
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
        raise RubySyntaxError("round trip failed (NOT written)")
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
    """Highlight the rubycode blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without rubycode is
    not decoded at all; one that has the class but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"rubycode" not in raw:
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
        except RubySyntaxError as exc:
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
    parser = argparse.ArgumentParser(description="Highlight Ruby blocks (rubycode) in .php files.")
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
