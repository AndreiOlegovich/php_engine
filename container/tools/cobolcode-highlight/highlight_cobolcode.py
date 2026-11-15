#!/usr/bin/env python3
# version 0.1.1
"""Highlight COBOL code in cobolcode blocks of .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds cobolcode, skips blocks
that already carry the canonical palette (<span class="cXXXXXX">), and
colours the rest:

    verbs, DIVISION, SECTION, PIC, VALUE, ...  -> <span class="c5fd7ff">
    string literals and ==pseudo-text==         -> <span class="cad7fa8">
    comments (*> ...)                           -> <span class="c999999">
    numeric literals and level numbers          -> <span class="c34e2e2">
    intrinsic function names (after FUNCTION)   -> <span class="cff5f00">
    special registers (RETURN-CODE, SQLCODE)    -> <span class="c4e9a06">
    program, class, method and paragraph names  -> <span class="cff0086">

Lower case only: the served sheets define .c5fd7ff and the others in lower
case, and class names are case sensitive, so c5FD7FF would match nothing.
The older blue/orange/lightblue palette that cobolcode blocks used before is
dropped and replaced by these classes, exactly as tools/rubycode-highlight
re-highlights the old Ruby colours.

A small COBOL lexer of its own, not Pygments: it reads *> comments, "..."
and '...' literals with doubled quotes, ==pseudo-text== of COPY REPLACING,
host variables (:WS-ID), level numbers and decimal literals, hyphenated
names, intrinsic function names after FUNCTION, special registers, and the
name after PROGRAM-ID, CLASS-ID, METHOD-ID and a paragraph header at the
start of a line. A block it cannot lex (unterminated string or pseudo-text,
or a tag other than code/span inside it) is reported and left byte for byte
as it was.

Each <code> line of a block is one line of COBOL: the lines are lexed
together and coloured one by one, so a colour span never crosses a line.

Every block is verified: the text without tags, unescaped, equals the
original text without tags, unescaped. A file is written to a temporary
file next to it and renamed into place, so CRLF line endings stay.

Usage:
    python3 highlight_cobolcode.py [--write] [--quiet] PATH [PATH ...]

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

C_KEYWORD = os.environ.get("SITE_C_KEYWORD", "c5fd7ff")  # verbs and reserved words
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # string literals and ==pseudo-text==
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # *> comments
C_SYMBOL = os.environ.get("SITE_C_SYMBOL", "cfce94f")  # kept for palette parity; COBOL has no symbols
C_NUMBER = os.environ.get("SITE_C_NUMBER", "c34e2e2")  # numeric literals and level numbers
C_CONSTANT = os.environ.get("SITE_C_CONSTANT", "cff5f00")  # intrinsic function names after FUNCTION
C_VARIABLE = os.environ.get("SITE_C_VARIABLE", "c4e9a06")  # special registers and :host-variables
C_DEF = os.environ.get("SITE_C_DEF", "cff0086")  # program, class, method and paragraph names

TARGET_CLASS = "cobolcode"
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9a-f]{6}"')
UNIT_RE = re.compile(r"(<!--.*?-->|</?[A-Za-z][^>]*>)|(&#?[0-9A-Za-z]+;)|(.)", re.DOTALL)
TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
TAG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")
IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
PARAGRAPH_RE = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)\s*\.\s*$")
SECTION_RE = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)\s+SECTION\s*\.\s*$")
SUPPORTED_TAGS = frozenset({"code", "span"})

# Verbs, reserved words and the SQL/CICS words that appear in EXEC blocks.
KEYWORDS = frozenset(
    {
        "ACCEPT",
        "ADD",
        "AFTER",
        "ALL",
        "ALPHABETIC",
        "ALPHABETIC-LOWER",
        "ALPHABETIC-UPPER",
        "ALSO",
        "ALTER",
        "AND",
        "ARE",
        "AREA",
        "AS",
        "ASCENDING",
        "ASSIGN",
        "AT",
        "AUTHOR",
        "BEFORE",
        "BINARY",
        "BLANK",
        "BY",
        "CALL",
        "CANCEL",
        "CD",
        "CF",
        "CH",
        "CHARACTER",
        "CHARACTERS",
        "CICS",
        "CLASS",
        "CLASS-ID",
        "CLOSE",
        "COBOL",
        "CODE",
        "COLUMN",
        "COMMA",
        "COMMIT",
        "COMMON",
        "COMP",
        "COMP-1",
        "COMP-2",
        "COMP-3",
        "COMP-4",
        "COMP-5",
        "COMPUTATIONAL",
        "COMPUTE",
        "CONFIGURATION",
        "CONTAINS",
        "CONTENT",
        "CONTINUE",
        "CONTROL",
        "CONTROLS",
        "COPY",
        "CORR",
        "CORRESPONDING",
        "COUNT",
        "CURSOR",
        "DATA",
        "DATE",
        "DATE-WRITTEN",
        "DAY",
        "DE",
        "DECLARATIVES",
        "DECLARE",
        "DELETE",
        "DELIMITED",
        "DELIMITER",
        "DEPENDING",
        "DESCENDING",
        "DETAIL",
        "DISPLAY",
        "DISTINCT",
        "DIVIDE",
        "DIVISION",
        "DOWN",
        "DUPLICATES",
        "DYNAMIC",
        "EBCDIC",
        "ELSE",
        "END",
        "END-ACCEPT",
        "END-CALL",
        "END-COMPUTE",
        "END-DECLARATIVES",
        "END-EVALUATE",
        "END-EXEC",
        "END-IF",
        "END-JSON",
        "END-MULTIPLY",
        "END-OF-PAGE",
        "END-PERFORM",
        "END-READ",
        "END-RECEIVE",
        "END-RETURN",
        "END-REWRITE",
        "END-SEARCH",
        "END-START",
        "END-STRING",
        "END-SUBTRACT",
        "END-UNSTRING",
        "END-WRITE",
        "END-XML",
        "ENDBR",
        "ENVIRONMENT",
        "EOP",
        "EQUAL",
        "ERROR",
        "EVALUATE",
        "EXCEPTION",
        "EXEC",
        "EXIT",
        "EXTEND",
        "EXTERNAL",
        "FACTORY",
        "FD",
        "FETCH",
        "FILE",
        "FILE-CONTROL",
        "FILE-ID",
        "FILLER",
        "FINAL",
        "FIRST",
        "FOOTING",
        "FOR",
        "FROM",
        "FUNCTION",
        "FUNCTION-ID",
        "GENERATE",
        "GIVING",
        "GLOBAL",
        "GO",
        "GOBACK",
        "GREATER",
        "GROUP",
        "HAVING",
        "HEADING",
        "I-O",
        "ID",
        "IDENTIFICATION",
        "IF",
        "IN",
        "INCLUDE",
        "INDEX",
        "INDEXED",
        "INITIALIZE",
        "INITIATE",
        "INPUT",
        "INPUT-OUTPUT",
        "INSERT",
        "INSPECT",
        "INTO",
        "INVALID",
        "INVOKE",
        "IS",
        "JSON",
        "JUST",
        "JUSTIFIED",
        "KEY",
        "LABEL",
        "LAST",
        "LEADING",
        "LEFT",
        "LENGTH",
        "LESS",
        "LIMIT",
        "LIMITS",
        "LINAGE",
        "LINE",
        "LINES",
        "LINK",
        "LINKAGE",
        "LOCAL-STORAGE",
        "LOCK",
        "LOW-VALUE",
        "LOW-VALUES",
        "MAP",
        "MAPSET",
        "METHOD",
        "METHOD-ID",
        "MODE",
        "MOVE",
        "MULTIPLE",
        "MULTIPLY",
        "NATIVE",
        "NEGATIVE",
        "NEXT",
        "NO",
        "NOT",
        "NULL",
        "NULLS",
        "NUMBER",
        "NUMERIC",
        "OBJECT",
        "OCCURS",
        "OF",
        "OFF",
        "OMITTED",
        "ON",
        "OPEN",
        "OPTIONAL",
        "OR",
        "ORDER",
        "ORGANIZATION",
        "OTHER",
        "OUTPUT",
        "OVERFLOW",
        "PAGE",
        "PERFORM",
        "PIC",
        "PICTURE",
        "PLUS",
        "POINTER",
        "POSITION",
        "POSITIVE",
        "PROCEDURE",
        "PROCEDURES",
        "PROGRAM-ID",
        "QUOTE",
        "QUOTES",
        "RD",
        "READ",
        "RECEIVE",
        "RECORD",
        "RECORDS",
        "REDEFINES",
        "RELATIVE",
        "RELEASE",
        "REMAINDER",
        "REMOVAL",
        "RENAMES",
        "REPLACE",
        "REPLACING",
        "REPORT",
        "REPORTING",
        "REPORTS",
        "RERUN",
        "RESERVE",
        "RETURN",
        "RETURNING",
        "REVERSED",
        "REWRITE",
        "RIGHT",
        "ROLLBACK",
        "RUN",
        "SAME",
        "SD",
        "SEARCH",
        "SECTION",
        "SECURITY",
        "SELECT",
        "SEND",
        "SENTENCE",
        "SEQUENTIAL",
        "SET",
        "SIGN",
        "SIZE",
        "SORT",
        "SORT-MERGE",
        "SOURCE",
        "SPACE",
        "SPACES",
        "SPECIAL-NAMES",
        "SQL",
        "STANDARD",
        "START",
        "STATUS",
        "STOP",
        "STRING",
        "SUBTRACT",
        "SUM",
        "SUPER",
        "SUPPRESS",
        "SYMBOLIC",
        "SYNC",
        "SYNCPOINT",
        "TABLE",
        "TALLYING",
        "TERMINATE",
        "TEST",
        "TEXT",
        "THAN",
        "THEN",
        "THROUGH",
        "THRU",
        "TIME",
        "TIMES",
        "TO",
        "TOP",
        "TRAILING",
        "TYPE",
        "UNIT",
        "UNSTRING",
        "UNTIL",
        "UP",
        "UPDATE",
        "UPON",
        "USAGE",
        "USE",
        "USING",
        "VALUE",
        "VALUES",
        "VARYING",
        "WHEN",
        "WHERE",
        "WITH",
        "WORKING-STORAGE",
        "WRITE",
        "XCTL",
        "ZERO",
        "ZEROES",
        "ZEROS",
    }
)

SPECIAL_REGISTERS = frozenset(
    {
        "ARGUMENT-NUMBER",
        "ARGUMENT-VALUE",
        "COB-CRT-STATUS",
        "COMMAND-LINE",
        "JSON-CODE",
        "JSON-STATUS",
        "LINE-COUNTER",
        "PAGE-COUNTER",
        "RETURN-CODE",
        "SORT-RETURN",
        "SQLCODE",
        "SQLSTATE",
        "TALLY",
        "WHEN-COMPILED",
        "XML-CODE",
        "XML-STATUS",
    }
)

DEF_KEYWORDS = frozenset({"PROGRAM-ID", "CLASS-ID", "METHOD-ID", "FUNCTION-ID"})


class CobolSyntaxError(ValueError):
    """A block that this lexer refuses to colour."""


class CobolLexer:
    """Colour class for every character of a COBOL source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end
        self.line_start = 0
        self.def_pending = False
        self.function_pending = False

    def run(self) -> list[str | None]:
        """Lex the whole source; one colour class or None per character."""
        while self.pos < self.end:
            char = self.src[self.pos]
            if char == "\n":
                self.pos += 1
                self.line_start = self.pos
                continue
            if char in " \t\r":
                self.pos += 1
                continue
            if self.at_line_start() and (self.section_name() or self.paragraph_name()):
                continue
            self.token(char)
        return self.colours

    def at_line_start(self) -> bool:
        """Whether only whitespace has been read since the last newline."""
        return not self.src[self.line_start : self.pos].strip()

    def line_text(self) -> str:
        """The rest of the current line."""
        stop = self.src.find("\n", self.pos)
        return self.src[self.pos : self.end if stop == -1 else stop]

    def section_name(self) -> bool:
        """A line that is NAME SECTION. is a section header."""
        match = SECTION_RE.match(self.line_text())
        if not match:
            return False
        word = match.group(1).upper()
        if word in KEYWORDS or word in SPECIAL_REGISTERS:
            return False
        self.paint(self.pos, self.pos + len(match.group(1)), C_DEF)
        self.pos += len(match.group(1))
        return True

    def paragraph_name(self) -> bool:
        """A line that is one name and a period is a paragraph header."""
        match = PARAGRAPH_RE.match(self.line_text())
        if not match:
            return False
        word = match.group(1).upper()
        if word in KEYWORDS or word in SPECIAL_REGISTERS:
            return False
        self.paint(self.pos, self.pos + len(match.group(1)), C_DEF)
        self.pos += len(match.group(1))
        return True

    def token(self, char: str) -> None:
        """One token at self.pos."""
        if char == "*" and self.src.startswith("*>", self.pos):
            self.comment()
        elif char in "'\"":
            self.string(char)
        elif char == "=" and self.src.startswith("==", self.pos):
            self.pseudo_text()
        elif char == ":" and self.host_variable():
            return
        elif char.isdigit():
            self.number()
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
        """*> to the end of the line."""
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.paint(self.pos, stop, C_COMMENT)
        self.pos = stop

    def string(self, quote: str) -> None:
        """ "..." or '...' with a doubled quote as the escape."""
        start = self.pos
        pos = start + 1
        while pos < self.end:
            char = self.src[pos]
            if char == quote:
                if pos + 1 < self.end and self.src[pos + 1] == quote:
                    pos += 2
                    continue
                pos += 1
                self.paint(start, pos, C_STRING)
                self.pos = pos
                return
            pos += 1
        raise CobolSyntaxError(f"line {self.line_number()}: unterminated {quote} string")

    def pseudo_text(self) -> None:
        """==text== of COPY REPLACING is one string."""
        start = self.pos
        stop = self.src.find("==", start + 2)
        if stop == -1:
            raise CobolSyntaxError(f"line {self.line_number()}: unterminated ==pseudo-text==")
        stop += 2
        self.paint(start, stop, C_STRING)
        self.pos = stop

    def host_variable(self) -> bool:
        """:WS-ID in EXEC SQL: the colon and the name are one variable."""
        match = IDENT_RE.match(self.src, self.pos + 1)
        if not match:
            return False
        self.paint(self.pos, match.end(), C_VARIABLE)
        self.pos = match.end()
        return True

    def number(self) -> None:
        """42, 0.13, 9(3) and the 01 of a level number."""
        match = NUMBER_RE.match(self.src, self.pos)
        stop = match.end() if match else self.pos + 1
        self.paint(self.pos, stop, C_NUMBER)
        self.pos = stop

    def identifier(self) -> None:
        """A keyword, a special register, a definition name or a plain name."""
        match = IDENT_RE.match(self.src, self.pos)
        if not match:
            self.pos += 1
            return
        word = match.group()
        stop = match.end()
        upper = word.upper()
        if self.def_pending:
            self.paint(self.pos, stop, C_DEF)
            self.def_pending = False
        elif self.function_pending:
            self.paint(self.pos, stop, C_CONSTANT)
            self.function_pending = False
        elif upper in KEYWORDS:
            self.paint(self.pos, stop, C_KEYWORD)
            if upper == "FUNCTION":
                self.function_pending = True
            if upper in DEF_KEYWORDS:
                self.def_pending = True
        elif upper in SPECIAL_REGISTERS:
            self.paint(self.pos, stop, C_VARIABLE)
        self.pos = stop


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
    """Coloured inner HTML; raises CobolSyntaxError when it cannot be lexed."""
    unsupported = tag_names(inner) - SUPPORTED_TAGS
    if unsupported:
        raise CobolSyntaxError(f"tag {min(unsupported)} inside the block")
    segments: list[re.Match[str] | None] = list(CODE_RE.finditer(inner))
    if not segments:
        segments = [None]
    texts = [units_text(split_units(segment.group(1))) if segment else units_text(split_units(inner)) for segment in segments]
    colours = CobolLexer("\n".join(texts)).run()
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
        raise CobolSyntaxError("round trip failed (NOT written)")
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
    """Highlight the cobolcode blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without cobolcode is
    not decoded at all; one that has the class but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"cobolcode" not in raw:
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
        except CobolSyntaxError as exc:
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
    value = match.group(2) or match.group(3) or ""
    return value.split()


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
    parser = argparse.ArgumentParser(description="Highlight COBOL blocks (cobolcode) in .php files.")
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
