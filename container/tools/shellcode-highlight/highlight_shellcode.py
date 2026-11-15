#!/usr/bin/env python3
"""Highlight shell commands inside .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds cmd1 (one command per
block) or shellscript (a script), skips blocks already highlighted with
<span class="cXXXXXX">, and colours the rest:

    command names, shell keywords     -> <span class="c5fd7ff">
    options (-s, --auto)              -> <span class="cfce94f">
    quoted strings, here-documents    -> <span class="cad7fa8">
    variables ($c, ${HOME}, NAME=)    -> <span class="c34e2e2">
    comments                          -> <span class="c999999">
    arguments, | ; && > and the rest  -> plain

The palette of the Python and JSON blocks (tools/pycode-highlight,
tools/jsoncode-highlight) plus the grey of source/_colors.css, written in
lower case: the built sheets define these classes only in lower case.
cmd1 and shellscript blocks have a dark background, so the colours read
well there; the light inline span.cmd-line is left alone.

A small lexer of its own, not Pygments: the Pygments bash lexer marks
neither the command name nor the options, and colours exec in
"docker exec" as a builtin. This lexer tracks where a command starts: at
the start of a line, after | || && ; & ( $( ` <( and the keywords that
take a command (then, do, ...), and after the prefix commands sudo, env,
exec, xargs, nohup, command, builtin, nice and doas. A word of letters in
angle brackets (<task>, <id>) is a placeholder of the article, not a
redirection.

Every tag already in a block and every entity stays as it was: only
colour spans are added, and a colour span is split where such a tag
falls, so tags always nest. Verification per block: removing the added
colour spans gives back the original inner HTML byte for byte. A file is
written to a temporary file next to it and renamed into place.

Usage:
    python3 highlight_shellcode.py [--write] [--quiet] PATH [PATH ...]

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
from enum import Enum, auto
from pathlib import Path

VERSION = "0.1.0"

C_COMMAND = os.environ.get("SITE_C_COMMAND", "c5fd7ff")  # command names and shell keywords
C_OPTION = os.environ.get("SITE_C_OPTION", "cfce94f")  # -s, --auto
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # quoted strings, here-documents
C_VARIABLE = os.environ.get("SITE_C_VARIABLE", "c34e2e2")  # $c, ${HOME}, the name of NAME=value
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # # comments

P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9A-Fa-f]{6}"')
ADDED_SPAN_RE = re.compile(r'<span class="c[0-9a-f]{6}">([^<]*)</span>')
UNIT_RE = re.compile(r"(<[^>]*>)|(&#?\w+;)|(.)", re.DOTALL)
TARGET_CLASSES = {"cmd1", "shellscript"}

ASSIGNMENT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?\+?=")
PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9_-]*>")
REDIRECT_RE = re.compile(r"\d*(?:&>>|&>|>>|>&|>\||<<<|<<-|<<|<>|<&|>|<)")
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]|[@*#?$!-]")

PREFIX_COMMANDS = frozenset({"sudo", "env", "exec", "xargs", "nohup", "command", "builtin", "nice", "doas"})
WORD_END = frozenset(" \t\n|&;()")


class Expect(Enum):
    """What the lexer expects the next word to be."""

    COMMAND = auto()  # a command name, an assignment or a keyword
    PREFIXED = auto()  # after sudo, env, ...: options and assignments, then a command
    ARGUMENT = auto()
    FUNCTION_NAME = auto()  # after function
    LOOP_NAME = auto()  # after for / select
    LOOP_IN = auto()  # after the loop name: in
    CASE_WORD = auto()  # after case
    CASE_IN = auto()  # after the case word: in
    PATTERN = auto()  # case patterns, up to )


# A shell keyword in command position, and what the word after it is.
KEYWORD_FOLLOWERS: dict[str, Expect] = {
    **dict.fromkeys(("if", "then", "else", "elif", "do", "while", "until", "!", "{", "time"), Expect.COMMAND),
    **dict.fromkeys(("fi", "done", "esac", "}", "[["), Expect.ARGUMENT),
    "for": Expect.LOOP_NAME,
    "select": Expect.LOOP_NAME,
    "case": Expect.CASE_WORD,
    "function": Expect.FUNCTION_NAME,
}


@dataclass
class ListState:
    """Where the lexer is inside one command list."""

    expect: Expect = Expect.COMMAND
    target_next: bool = False  # the next word is the file of a redirection
    case_depth: int = 0


class ShellLexer:
    """Colour classes for every character of a shell source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end
        self.heredocs: list[tuple[str, bool]] = []
        self.heredoc_next: bool | None = None  # set by << and <<-: the next word is a delimiter

    def run(self) -> list[str | None]:
        """Lex the whole source; one colour class or None per character."""
        while self.pos < self.end:
            self.commands(closer=None)
            self.pos += 1  # a stray ) at the top level
        return self.colours

    def paint(self, start: int, stop: int, colour: str) -> None:
        """Give characters start..stop-1 one colour."""
        for index in range(start, min(stop, self.end)):
            self.colours[index] = colour

    def paint_bare(self, bare: list[int], colour: str, limit: int) -> None:
        """Colour the unquoted characters of a word that come before limit."""
        for index in bare:
            if index < limit:
                self.colours[index] = colour

    def close(self, char: str) -> None:
        """Step over the closing ) or ` of a nested command list."""
        if self.pos < self.end and self.src[self.pos] == char:
            self.pos += 1

    def commands(self, closer: str | None) -> None:
        """A command list, up to closer (")" or "`") or the end of the text."""
        state = ListState()
        while self.pos < self.end:
            start = self.pos
            char = self.src[start]
            if state.expect is not Expect.PATTERN and (char == closer or (char == ")" and closer is None)):
                return  # the end of this list, or a stray ) at the top level
            if self.separator(state) or self.operator(state, closer):
                continue
            bare = self.word(closer)
            if self.pos == start:
                self.pos += 1
                continue
            self.list_word(self.src[start : self.pos], start, bare, state)

    def separator(self, state: ListState) -> bool:
        """Blanks, line continuations, newlines and comments; True when one was read."""
        src = self.src
        start = self.pos
        char = src[start]
        if char in " \t":
            self.pos += 1
        elif char == "\\" and src.startswith("\n", start + 1):
            self.pos += 2
        elif char == "\n":
            self.pos += 1
            if self.heredocs:
                self.heredoc_bodies()
            if state.expect is not Expect.PATTERN:
                state.expect = Expect.COMMAND
        elif char == "#":
            stop = src.find("\n", start)
            stop = self.end if stop == -1 else stop
            self.paint(start, stop, C_COMMENT)
            self.pos = stop
        else:
            return False
        return True

    def operator(self, state: ListState, closer: str | None) -> bool:
        """| && ; & ( ) and redirections; True when one was read."""
        src = self.src
        start = self.pos
        char = src[start]
        if state.expect is Expect.PATTERN and char in "|()":
            self.pos += 1
            if char == ")":
                state.expect = Expect.COMMAND
        elif src.startswith(("&&", "||", ";;", ";&", "|&"), start):
            in_case = src.startswith((";;", ";&"), start) and state.case_depth > 0
            self.pos += 2
            state.expect = Expect.PATTERN if in_case else Expect.COMMAND
            state.target_next = False
        elif char in "<>" and src.startswith("(", start + 1):
            self.pos += 2
            self.commands(")")
            self.close(")")
            state.target_next = False
        elif self.redirect(state):
            pass
        elif char in "|;&":
            self.pos += 1
            state.expect = Expect.COMMAND
            state.target_next = False
        elif char == "(" and src.startswith(")", start + 1):  # name() { ...: a function definition
            self.pos += 2
            state.expect = Expect.COMMAND
        elif char == "(":
            self.pos += 1
            self.commands(")")
            self.close(")")
            state.expect = Expect.ARGUMENT
        elif char == ")" and closer is not None:
            self.pos += 1  # a ) inside `...` that closes nothing
        else:
            return False
        return True

    def redirect(self, state: ListState) -> bool:
        """> >> 2>&1 << and the rest; True when one was read."""
        src = self.src
        start = self.pos
        if src[start] == "<" and PLACEHOLDER_RE.match(src, start):
            return False
        found = REDIRECT_RE.match(src, start)
        if not found:
            return False
        self.pos = found.end()
        operator = found.group().lstrip("0123456789")
        if operator in ("<<", "<<-"):
            self.heredoc_next = operator == "<<-"
        elif operator in (">&", "<&") and self.pos < self.end and (src[self.pos].isdigit() or src[self.pos] == "-"):
            self.pos += 1
        else:
            state.target_next = True
        return True

    def list_word(self, word: str, start: int, bare: list[int], state: ListState) -> None:
        """A word of a command list: a here-document delimiter, a redirection target or a word to colour."""
        if self.heredoc_next is not None:
            self.heredocs.append((re.sub(r"[\"'\\]", "", word), self.heredoc_next))
            self.heredoc_next = None
            self.paint(start, self.pos, C_STRING)
        elif state.target_next:
            state.target_next = False
        elif state.expect in (Expect.COMMAND, Expect.PREFIXED):
            self.command_word(word, start, bare, state)
        else:
            self.other_word(word, start, bare, state)

    def option_limit(self, word: str, start: int, bare: list[int]) -> int | None:
        """Where the option colour of a -x / --name=value word ends; None if not an option."""
        if not (word.startswith("-") and len(word) > 1 and start in bare):
            return None
        return start + word.index("=") if "=" in word else self.pos

    def command_word(self, word: str, start: int, bare: list[int], state: ListState) -> None:
        """A word where a command may start: an assignment, an option of sudo, a keyword or the command."""
        assignment = ASSIGNMENT_RE.match(word)
        limit = self.option_limit(word, start, bare)
        if assignment and all(index in bare for index in range(start, start + assignment.end())):
            self.paint(start, start + len(assignment.group(1)), C_VARIABLE)
        elif state.expect is Expect.PREFIXED and limit is not None:
            self.paint_bare(bare, C_OPTION, limit)
        elif state.expect is Expect.COMMAND and self.keyword(word, bare, state):
            pass
        else:
            self.paint_bare(bare, C_COMMAND, self.pos)
            state.expect = Expect.PREFIXED if word in PREFIX_COMMANDS else Expect.ARGUMENT

    def other_word(self, word: str, start: int, bare: list[int], state: ListState) -> None:
        """A word after the command: a loop or function name, in, a case pattern or an argument."""
        expect = state.expect
        if expect is Expect.FUNCTION_NAME:
            self.paint_bare(bare, C_COMMAND, self.pos)
            state.expect = Expect.ARGUMENT
        elif expect is Expect.LOOP_NAME:
            self.paint_bare(bare, C_VARIABLE, self.pos)
            state.expect = Expect.LOOP_IN
        elif expect in (Expect.LOOP_IN, Expect.CASE_IN) and word == "in":
            self.paint_bare(bare, C_COMMAND, self.pos)
            if expect is Expect.CASE_IN:
                state.expect = Expect.PATTERN
                state.case_depth += 1
            else:
                state.expect = Expect.ARGUMENT
        elif expect is Expect.CASE_WORD:
            state.expect = Expect.CASE_IN
        elif expect is Expect.PATTERN:
            if word == "esac":
                self.paint_bare(bare, C_COMMAND, self.pos)
                state.expect = Expect.ARGUMENT
                state.case_depth = max(0, state.case_depth - 1)
        else:
            limit = self.option_limit(word, start, bare)
            if limit is not None:
                self.paint_bare(bare, C_OPTION, limit)
            state.expect = Expect.ARGUMENT

    def keyword(self, word: str, bare: list[int], state: ListState) -> bool:
        """Colour a shell keyword in command position; False for any other word."""
        following = KEYWORD_FOLLOWERS.get(word)
        if following is None:
            return False
        if word == "esac":
            state.case_depth = max(0, state.case_depth - 1)
        self.paint_bare(bare, C_COMMAND, self.pos)
        state.expect = following
        return True

    def word(self, closer: str | None) -> list[int]:
        """Read one word; paint its quoted and $ parts; return its bare indices."""
        src = self.src
        start = self.pos
        bare: list[int] = []
        while self.pos < self.end:
            index = self.pos
            char = src[index]
            if char in WORD_END:
                if char == "(" and index > start and src[index - 1] == "=" and ASSIGNMENT_RE.fullmatch(src[start:index]):
                    self.array()
                    continue
                break
            if char in "<>":
                placeholder = PLACEHOLDER_RE.match(src, index) if char == "<" else None
                if placeholder is None:
                    break
                bare.extend(range(index, placeholder.end()))
                self.pos = placeholder.end()
                continue
            if char == "\\":
                if src.startswith("\n", index + 1):
                    break
                stop = min(index + 2, self.end)
                bare.extend(range(index, stop))
                self.pos = stop
                continue
            if char == "'":
                self.single_quoted()
            elif char == '"':
                self.double_quoted()
            elif char == "$":
                bare.extend(self.dollar())
            elif char == "`":
                if closer == "`":
                    break
                self.pos += 1
                self.commands("`")
                self.close("`")
            else:
                bare.append(index)
                self.pos += 1
        return bare

    def array(self) -> None:
        """The (a b c) of NAME=(a b c): words, not commands."""
        src = self.src
        self.pos += 1
        while self.pos < self.end:
            start = self.pos
            char = src[start]
            if char == ")":
                self.pos += 1
                return
            if char in " \t\n":
                self.pos += 1
                continue
            if char == "#":
                stop = src.find("\n", start)
                stop = self.end if stop == -1 else stop
                self.paint(start, stop, C_COMMENT)
                self.pos = stop
                continue
            bare = self.word(closer=None)
            if self.pos == start:
                self.pos += 1
                continue
            word = src[start : self.pos]
            if word.startswith("-") and len(word) > 1 and start in bare:
                self.paint_bare(bare, C_OPTION, self.pos)

    def single_quoted(self) -> None:
        """'...': a string, nothing inside it is special."""
        stop = self.src.find("'", self.pos + 1)
        stop = self.end if stop == -1 else stop + 1
        self.paint(self.pos, stop, C_STRING)
        self.pos = stop

    def double_quoted(self) -> None:
        """ "...": a string with $ and ` expansions inside."""
        src = self.src
        self.paint(self.pos, self.pos + 1, C_STRING)
        self.pos += 1
        while self.pos < self.end:
            index = self.pos
            char = src[index]
            if char == '"':
                self.paint(index, index + 1, C_STRING)
                self.pos += 1
                return
            if char == "\\":
                stop = min(index + 2, self.end)
                self.paint(index, stop, C_STRING)
                self.pos = stop
            elif char == "$":
                for literal in self.dollar():
                    self.paint(literal, literal + 1, C_STRING)
            elif char == "`":
                self.pos += 1
                self.commands("`")
                self.close("`")
            else:
                self.paint(index, index + 1, C_STRING)
                self.pos += 1

    def dollar(self) -> list[int]:
        """An expansion at $; returns [index of $] when the $ is a plain character."""
        src = self.src
        index = self.pos
        if src.startswith("$((", index):
            stop = self.matching(index + 1, "(", ")")
            self.paint(index, stop, C_VARIABLE)
            self.pos = stop
            return []
        if src.startswith("$(", index):
            self.pos = index + 2
            self.commands(")")
            self.close(")")
            return []
        if src.startswith("${", index):
            stop = self.matching(index + 1, "{", "}")
            self.paint(index, stop, C_VARIABLE)
            self.pos = stop
            return []
        if src.startswith("$'", index):
            stop = index + 2
            while stop < self.end and src[stop] != "'":
                stop += 2 if src[stop] == "\\" else 1
            stop = min(stop + 1, self.end)
            self.paint(index, stop, C_STRING)
            self.pos = stop
            return []
        name = NAME_RE.match(src, index + 1)
        if name:
            self.paint(index, name.end(), C_VARIABLE)
            self.pos = name.end()
            return []
        self.pos = index + 1
        return [index]

    def matching(self, pos: int, opening: str, closing: str) -> int:
        """Index after the bracket that closes the one at pos; quotes skipped."""
        src = self.src
        depth = 0
        index = pos
        while index < self.end:
            char = src[index]
            if char == "\\":
                index += 2
                continue
            if char in "'\"":
                stop = src.find(char, index + 1)
                index = self.end if stop == -1 else stop + 1
                continue
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return self.end

    def heredoc_bodies(self) -> None:
        """Paint the here-documents that start at the current line."""
        src = self.src
        for delimiter, strip_tabs in self.heredocs:
            start = self.pos
            found = False
            while self.pos < self.end:
                newline = src.find("\n", self.pos)
                line_end = self.end if newline == -1 else newline
                line = src[self.pos : line_end]
                self.pos = self.end if newline == -1 else newline + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    self.paint(start, line_end, C_STRING)
                    found = True
                    break
            if not found:
                self.paint(start, self.end, C_STRING)
        self.heredocs.clear()


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
    """Coloured inner HTML; raises ValueError when the round trip fails."""
    units = split_units(inner)
    source = "".join(unit.text for unit in units if not unit.is_tag)
    colours = ShellLexer(source).run()

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
    """Highlight the shell blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without cmd1 or
    shellscript is not decoded at all; one that has them but is not UTF-8
    is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"cmd1" not in raw and b"shellscript" not in raw:
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
        if coloured == inner:
            return match.group(0)
        stats.fixed += 1
        return f"<p{attrs}>{coloured}</p>"

    new_text = P_BLOCK_RE.sub(repl, text)
    stats.changed = new_text != text
    if write and stats.changed:
        write_atomic(path, new_text.encode("utf-8"))
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
    parser = argparse.ArgumentParser(description="Highlight shell command blocks (cmd1, shellscript) in .php files.")
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
