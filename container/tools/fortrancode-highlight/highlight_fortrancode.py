#!/usr/bin/env python3
# version 0.1.0
"""Highlight Fortran code in fortrancode blocks of .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds fortrancode, skips blocks
that already carry the canonical palette (<span class="cXXXXXX">), and
colours the rest:

    keywords and statements (program, implicit none, do, end, ...)
                                            -> <span class="c5fd7ff">
    strings ('...' and "...")               -> <span class="cad7fa8">
    comments (! ...)                        -> <span class="c999999">
    numbers (42, 1.0e-6, 7_int32)           -> <span class="c34e2e2">
    intrinsic functions (sum, size, trim)   -> <span class="cff5f00">

Lower case only: the served sheets define .c5fd7ff and the others in lower
case, and class names are case sensitive, so c5FD7FF would match nothing.
The older blue/yellow/orange/lightblue palette that fortrancode blocks used
before is dropped and replaced by these classes, exactly as
tools/pycode-highlight re-highlights the old Python colours.

A small free form Fortran lexer of its own, not Pygments: it reads ! comments
(not ! inside a string), '...' and "..." with a doubled quote as the escape,
numbers with an exponent and a kind suffix (1.0d0, 7_int32), identifiers,
dot operators (.and., .true., .eq.), and the statement keywords of the
language. Fortran is case insensitive, so PROGRAM and program both count.
A block it cannot lex (an unterminated string, or a tag other than code/span
inside it) is reported and left byte for byte as it was.

Each <code> line of a block is one line of Fortran: the lines are lexed
together and coloured one by one, so a colour span never crosses a line.

Every block is verified: the text without tags, unescaped, equals the
original text without tags, unescaped. A file is written to a temporary
file next to it and renamed into place, so CRLF line endings stay.

Usage:
    python3 highlight_fortrancode.py [--write] [--quiet] PATH [PATH ...]

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

C_KEYWORD = os.environ.get("SITE_C_KEYWORD", "c5fd7ff")  # program, implicit none, do, end, if
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # '...' and "..."
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # ! ... and !$omp directives
C_NUMBER = os.environ.get("SITE_C_NUMBER", "c34e2e2")  # 42, 1.0e-6, 7_int32
C_INTRINSIC = os.environ.get("SITE_C_INTRINSIC", "cff5f00")  # sum, size, trim, allocated

TARGET_CLASS = "fortrancode"
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9a-f]{6}"')
UNIT_RE = re.compile(r"(<!--.*?-->|</?[A-Za-z][^>]*>)|(&#?[0-9A-Za-z]+;)|(.)", re.DOTALL)
TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
TAG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")

# Statement keywords, attributes and the words of a declaration.
KEYWORDS = frozenset(
    {
        "abstract",
        "allocatable",
        "allocate",
        "assignment",
        "associate",
        "asynchronous",
        "backspace",
        "bind",
        "block",
        "call",
        "case",
        "character",
        "class",
        "close",
        "common",
        "complex",
        "contains",
        "continue",
        "critical",
        "cycle",
        "concurrent",
        "data",
        "deallocate",
        "default",
        "deferred",
        "dimension",
        "do",
        "double",
        "double precision",
        "elemental",
        "else",
        "elseif",
        "elsewhere",
        "end",
        "endassociate",
        "endblock",
        "enddo",
        "endfile",
        "endif",
        "endinterface",
        "endmodule",
        "endprogram",
        "endselect",
        "endsubroutine",
        "endtype",
        "endwhere",
        "entry",
        "enum",
        "equivalence",
        "error",
        "exit",
        "extends",
        "external",
        "final",
        "flush",
        "forall",
        "format",
        "function",
        "generic",
        "goto",
        "if",
        "implicit",
        "import",
        "in",
        "include",
        "inout",
        "inquire",
        "integer",
        "intent",
        "interface",
        "images",
        "intrinsic",
        "is",
        "kind",
        "len",
        "local",
        "logical",
        "module",
        "namelist",
        "none",
        "non_intrinsic",
        "non_overridable",
        "nopass",
        "nullify",
        "only",
        "open",
        "operator",
        "optional",
        "out",
        "parameter",
        "pass",
        "pointer",
        "precision",
        "print",
        "private",
        "procedure",
        "program",
        "protected",
        "public",
        "pure",
        "read",
        "real",
        "reduce",
        "recursive",
        "result",
        "return",
        "rewind",
        "save",
        "select",
        "sequence",
        "shared",
        "stop",
        "subroutine",
        "sync",
        "target",
        "then",
        "type",
        "use",
        "value",
        "volatile",
        "wait",
        "where",
        "while",
        "write",
    }
)
# Intrinsic procedures: the names a reader can call without declaring them.
INTRINSICS = frozenset(
    {
        "abs",
        "achar",
        "acos",
        "adjustl",
        "adjustr",
        "aimag",
        "aint",
        "all",
        "allocated",
        "anint",
        "any",
        "asin",
        "associated",
        "atan",
        "atan2",
        "ceiling",
        "char",
        "cmplx",
        "command_argument_count",
        "conjg",
        "cos",
        "cosh",
        "count",
        "cpu_time",
        "c_associated",
        "c_f_pointer",
        "c_funloc",
        "c_loc",
        "c_null_ptr",
        "date_and_time",
        "dble",
        "digits",
        "dot_product",
        "dprod",
        "eoshift",
        "epsilon",
        "exp",
        "exponent",
        "floor",
        "fraction",
        "get_command",
        "get_command_argument",
        "get_environment_variable",
        "huge",
        "iachar",
        "iand",
        "ibclr",
        "ibits",
        "ichar",
        "ieor",
        "image_index",
        "index",
        "int",
        "ior",
        "ishft",
        "ishftc",
        "kind",
        "lbound",
        "len",
        "len_trim",
        "lge",
        "lgt",
        "lle",
        "llt",
        "log",
        "log10",
        "logical",
        "matmul",
        "max",
        "maxexponent",
        "maxloc",
        "maxval",
        "merge",
        "minexponent",
        "min",
        "minloc",
        "minval",
        "mod",
        "modulo",
        "move_alloc",
        "nearest",
        "nint",
        "norm2",
        "not",
        "null",
        "num_images",
        "pack",
        "present",
        "product",
        "radix",
        "random_number",
        "random_seed",
        "range",
        "real",
        "repeat",
        "reshape",
        "rrspacing",
        "scale",
        "scan",
        "selected_int_kind",
        "selected_real_kind",
        "set_exponent",
        "shape",
        "sign",
        "sin",
        "sinh",
        "size",
        "spacing",
        "spread",
        "sqrt",
        "sum",
        "system_clock",
        "tan",
        "tanh",
        "this_image",
        "tiny",
        "transfer",
        "transpose",
        "trim",
        "ubound",
        "unpack",
        "verify",
    }
)
SUPPORTED_TAGS = frozenset({"code", "span"})

IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
NUMBER_RE = re.compile(
    r"\d+\.\d*(?:[eEdDqQ][+-]?\d+)?(?:_[A-Za-z0-9_]+)?"
    r"|\.\d+(?:[eEdDqQ][+-]?\d+)?(?:_[A-Za-z0-9_]+)?"
    r"|\d+(?:[eEdDqQ][+-]?\d+)?(?:_[A-Za-z0-9_]+)?"
)
DOT_WORD_RE = re.compile(r"\.\s*[A-Za-z]+\s*\.")
BOZ_RE = re.compile(r"\d*[bBoOzZ]'[0-9A-Fa-f]+'")


class FortranSyntaxError(ValueError):
    """A block that this lexer refuses to colour."""


class FortranLexer:  # pylint: disable=too-few-public-methods
    """Colour class for every character of a Fortran source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end

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

    def token(self, char: str) -> None:
        """One token at self.pos."""
        if char == "!":
            self.comment()
        elif char in "'\"":
            self.string(char)
        elif char.isdigit():
            self.number()
        elif char == "." and self.dot_word():
            return
        elif char.isalpha() or char == "_":
            self.identifier()
        else:
            self.pos += 1

    def line_number(self) -> int:
        """1-based line of the current position."""
        return self.src.count("\n", 0, self.pos) + 1

    def paint(self, start: int, stop: int, colour: str) -> None:
        """Give characters start..stop-1 one colour."""
        for index in range(start, min(stop, self.end)):
            self.colours[index] = colour

    def comment(self) -> None:
        """! to the end of the line; !$omp and !$acc directives count as comments."""
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.paint(self.pos, stop, C_COMMENT)
        self.pos = stop

    def string(self, quote: str) -> None:
        """'...' or "...", where a doubled quote is one quote inside the string."""
        src = self.src
        start = self.pos
        pos = start + 1
        while pos < self.end:
            char = src[pos]
            if char == quote:
                if pos + 1 < self.end and src[pos + 1] == quote:
                    pos += 2
                    continue
                pos += 1
                self.paint(start, pos, C_STRING)
                self.pos = pos
                return
            pos += 1
        raise FortranSyntaxError(f"line {self.line_number()}: unterminated {quote} string")

    def number(self) -> None:
        """42, 1.0, 1.0e-6, 1.0d0, 7_int32, 3.14_dp."""
        boz = BOZ_RE.match(self.src, self.pos)
        if boz:
            self.paint(self.pos, boz.end(), C_NUMBER)
            self.pos = boz.end()
            return
        match = NUMBER_RE.match(self.src, self.pos)
        stop = match.end() if match else self.pos + 1
        self.paint(self.pos, stop, C_NUMBER)
        self.pos = stop

    def dot_word(self) -> bool:
        """.true., .and., .eq. are keywords; a lone dot is punctuation."""
        match = DOT_WORD_RE.match(self.src, self.pos)
        if not match:
            return False
        self.paint(self.pos, match.end(), C_KEYWORD)
        self.pos = match.end()
        return True

    def identifier(self) -> None:
        """A statement keyword, an intrinsic name, or a plain name."""
        match = IDENT_RE.match(self.src, self.pos)
        if not match:
            self.pos += 1
            return
        word = match.group()
        stop = match.end()
        lowered = word.lower()
        if lowered in KEYWORDS:
            self.paint(self.pos, stop, C_KEYWORD)
        elif lowered in INTRINSICS and self.call_follows(stop):
            self.paint(self.pos, stop, C_INTRINSIC)
        self.pos = stop

    def call_follows(self, pos: int) -> bool:
        """Whether the next non-blank character is an opening bracket.

        An intrinsic is coloured only when it is called, so a variable called
        count or sum keeps the plain colour of every other name.
        """
        while pos < self.end and self.src[pos] in " \t":
            pos += 1
        return pos < self.end and self.src[pos] == "("


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
    """Coloured inner HTML; raises FortranSyntaxError when it cannot be lexed."""
    unsupported = tag_names(inner) - SUPPORTED_TAGS
    if unsupported:
        raise FortranSyntaxError(f"tag {sorted(unsupported)[0]} inside the block")
    segments: list[re.Match[str] | None] = list(CODE_RE.finditer(inner))
    if not segments:
        segments = [None]
    texts = [units_text(split_units(segment.group(1))) if segment else units_text(split_units(inner)) for segment in segments]
    colours = FortranLexer("\n".join(texts)).run()
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
        raise FortranSyntaxError("round trip failed (NOT written)")
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
    """Highlight the fortrancode blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without fortrancode
    is not decoded at all; one that has the class but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"fortrancode" not in raw:
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
        except FortranSyntaxError as exc:
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
    parser = argparse.ArgumentParser(description="Highlight Fortran blocks (fortrancode) in .php files.")
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
