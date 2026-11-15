#!/usr/bin/env python3
# version 0.1.1
"""Highlight x86 assembly code in asmcode blocks of .php files with the site palette.

Finds <p ...>...</p> blocks whose class list holds asmcode, skips blocks
that already carry the canonical palette (<span class="cXXXXXX">), and
colours the rest:

    instructions (mov, lea, syscall, ...)     -> <span class="c5fd7ff">
    directives (section, global, db, equ, %)  -> <span class="c5fd7ff">
    strings and character constants           -> <span class="cad7fa8">
    ; comments                                -> <span class="c999999">
    registers (rax, eax, ax, al, xmm0, ...)   -> <span class="c4e9a06">
    label definitions (name:, .loop:)         -> <span class="cff0086">
    constants (SYS_EXIT, PT_X)                -> <span class="cff5f00">
    numbers (42, 0xFF, 0b1010, 1.0)           -> <span class="c34e2e2">
    symbols, size keywords, operators         -> plain

Lower case only: the served sheets define .c5fd7ff and the others in lower
case, so c5FD7FF would match nothing. The older blue/orange/lightblue/
yellow/green/violet palette is dropped and replaced by these classes.

A small line lexer of its own, not Pygments (not in the standard library).
Each <code> element of a block is one line of assembly, so the lines are
lexed one by one and a colour span never crosses a line. An instruction is
only a keyword at the start of a statement, so `apt install` stays plain.

A block it cannot lex (an unterminated string, or a tag other than code
and span inside it) is reported and left byte for byte as it was.

Every block is verified: the text without tags, unescaped, equals the
original. A file is written to a temporary file next to it and renamed
into place, so CRLF line endings stay.

Usage:
    python3 highlight_asmcode.py [--write] [--quiet] PATH [PATH ...]

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

C_KEYWORD = os.environ.get("SITE_C_KEYWORD", "c5fd7ff")  # instructions and directives
C_STRING = os.environ.get("SITE_C_STRING", "cad7fa8")  # strings and character constants
C_COMMENT = os.environ.get("SITE_C_COMMENT", "c999999")  # ; comments
C_LABEL = os.environ.get("SITE_C_LABEL", "cff0086")  # label definitions
C_CONSTANT = os.environ.get("SITE_C_CONSTANT", "cff5f00")  # EQU names and other upper-case symbols
C_NUMBER = os.environ.get("SITE_C_NUMBER", "c34e2e2")  # 42, 0xFF, 0b1010, 1.0
C_REGISTER = os.environ.get("SITE_C_REGISTER", "c4e9a06")  # rax, eax, ax, al, xmm0

TARGET_CLASS = "asmcode"
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
COLOUR_SPAN_RE = re.compile(r'<span\s+class="c[0-9a-f]{6}"')
UNIT_RE = re.compile(r"(<!--.*?-->|</?[A-Za-z][^>]*>)|(&#?[0-9A-Za-z]+;)|(.)", re.DOTALL)
TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
TAG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)")

REGISTER_RE = re.compile(
    r"(?:r(?:ax|bx|cx|dx|si|di|bp|sp)|e(?:ax|bx|cx|dx|si|di|bp|sp|ip)|"
    r"r(?:8|9|1[0-5])(?:b|w|d)?|"
    r"(?:ax|bx|cx|dx|si|di|bp|sp)|(?:al|ah|bl|bh|cl|ch|dl|dh|sil|dil|bpl|spl)|"
    r"(?:xmm|ymm|zmm)(?:[0-9]|[12][0-9]|3[01])|(?:mm|st)(?:[0-7])|"
    r"(?:cs|ds|es|fs|gs|ss)|(?:cr|dr)(?:[0-9]|1[0-5])|"
    r"(?:r|e)?flags|rip|eip)"
)
NUMBER_RE = re.compile(
    r"\$[0-9A-Fa-f]+|0[xX][0-9A-Fa-f_]+|0[bB][01_]+|0[oO][0-7_]+|"
    r"[0-9][0-9_]*(?:\.[0-9_]+)?(?:[eE][+-]?[0-9]+)?[hHqQdD]?"
)
IDENT_RE = re.compile(r"[A-Za-z_?@~$.][A-Za-z0-9_?@~$.#]*")
LABEL_RE = re.compile(r"(?:[A-Za-z_?@~$.][A-Za-z0-9_?@~$.#]*|\.\.[A-Za-z0-9_@~$#.]+):")
MACRO_PARAM_RE = re.compile(r"%%?[A-Za-z_?@~$][A-Za-z0-9_?@~$#.]*|%%?[0-9]+|%\?|%\+=|%-=|%\*=|%/=|%=|%\+=|%&=")
CONSTANT_RE = re.compile(r"[A-Z_][A-Z0-9_]{1,}")

# x86 instructions, the ones the course uses and their close relatives.
# fmt: off
MNEMONICS = frozenset({
    "aaa", "aad", "aam", "aas", "adc", "add", "addpd", "addps", "addsd", "addss", "and",
    "andpd", "andps", "bsf", "bsr", "bswap", "bt", "btc", "btr", "bts", "call", "cbw", "cdq",
    "cdqe", "clc", "cld", "cli", "clts", "cmc", "cmova", "cmovae", "cmovb", "cmovbe", "cmovc",
    "cmove", "cmovg", "cmovge", "cmovl", "cmovle", "cmovna", "cmovnae", "cmovnb", "cmovnbe",
    "cmovnc", "cmovne", "cmovng", "cmovnge", "cmovnl", "cmovnle", "cmovno", "cmovnp", "cmovns",
    "cmovnz", "cmovo", "cmovp", "cmovpe", "cmovpo", "cmovs", "cmovz", "cmp", "cmppd", "cmpps",
    "cmpsb", "cmpsd", "cmpsq", "cmpss", "cmpsw", "cmpxchg", "cmpxchg8b", "comisd", "comiss",
    "cpuid", "cqo", "cvtdq2pd", "cvtdq2ps", "cvtsd2ss", "cvtsi2sd", "cvtsi2ss", "cvtss2sd",
    "cvttsd2si", "cvttss2si", "cwd", "cwde", "daa", "das", "dec", "div", "divpd", "divps",
    "divsd", "divss", "emms", "enter", "fabs", "fadd", "faddp", "fchs", "fcos", "fdiv",
    "fdivp", "fild", "fistp", "fld", "fldz", "fmul", "fmulp", "fnstcw", "fpatan", "fprem",
    "fptan", "frndint", "fscale", "fsin", "fsincos", "fsqrt", "fst", "fstp", "fsub", "fsubp",
    "ftst", "fucom", "fwait", "fxam", "fxch", "fyl2x", "fyl2xp1", "hlt", "idiv", "imul", "in",
    "inc", "incbin", "insb", "insd", "insw", "int", "int1", "int3", "into", "iret", "iretd",
    "iretq", "jcxz", "je", "jecxz", "jg", "jge", "jl", "jle", "jmp", "jna", "jnae", "jnb",
    "jnbe", "jnc", "jne", "jng", "jnge", "jnl", "jnle", "jno", "jnp", "jns", "jnz", "jo", "jp",
    "jpe", "jpo", "js", "jz", "lahf", "lar", "lea", "leave", "les", "lfs", "lgdt", "lgs",
    "lidt", "lldt", "lmsw", "lock", "lodsb", "lodsd", "lodsq", "lodsw", "loop", "loope",
    "loopne", "loopnz", "loopz", "lsl", "lss", "ltr", "mov", "movabs", "movapd", "movaps",
    "movbe", "movd", "movdqa", "movdqu", "movq", "movsb", "movsd", "movsq", "movss", "movsw",
    "movsx", "movsxd", "movupd", "movups", "mul", "mulpd", "mulps", "mulsd", "mulss", "mwait",
    "neg", "nop", "not", "or", "orpd", "orps", "out", "outsb", "outsd", "outsw", "paddb",
    "paddd", "paddq", "paddw", "pand", "pandn", "pause", "pop", "popa", "popad", "popcnt",
    "popf", "popfd", "popfq", "por", "prefetchnta", "prefetcht0", "prefetcht1", "prefetcht2",
    "psubb", "psubd", "psubq", "psubw", "punpckhbw", "punpckhdq", "punpcklqdq", "push",
    "pusha", "pushad", "pushf", "pushfd", "pushfq", "pxor", "rcl", "rcpps", "rcpss", "rdmsr",
    "rdpmc", "rdrand", "rdseed", "rdtsc", "rdtscp", "rep", "repe", "repne", "repnz", "repz",
    "ret", "retf", "retn", "rol", "ror", "rsqrtps", "rsqrtss", "sahf", "sal", "sar", "sbb",
    "scasb", "scasd", "scasq", "scasw", "seta", "setae", "setb", "setbe", "setc", "sete",
    "setg", "setge", "setl", "setle", "setna", "setnae", "setnb", "setnbe", "setnc", "setne",
    "setng", "setnge", "setnl", "setnle", "setno", "setnp", "setns", "setnz", "seto", "setp",
    "setpe", "setpo", "sets", "setz", "sfence", "sgdt", "shl", "shld", "shr", "shrd", "shufps",
    "sidt", "sldt", "smsw", "sqrtpd", "sqrtps", "sqrtsd", "sqrtss", "stc", "std", "sti",
    "stosb", "stosd", "stosq", "stosw", "str", "sub", "subpd", "subps", "subsd", "subss",
    "swapgs", "syscall", "sysenter", "sysexit", "sysret", "test", "tzcnt", "ucomisd",
    "ucomiss", "ud2", "vaddpd", "vaddps", "vaddsd", "vaddss", "vandpd", "vandps", "vcmppd",
    "vcmpps", "vcvtsi2sd", "vcvtsi2ss", "vcvtss2sd", "vcvttsd2si", "vcvttss2si", "vdivpd",
    "vdivps", "vdivsd", "vdivss", "vmaxpd", "vmaxps", "vminpd", "vminps", "vmovapd", "vmovaps",
    "vmovd", "vmovdqa", "vmovdqu", "vmovq", "vmovsd", "vmovss", "vmovupd", "vmovups", "vmulpd",
    "vmulps", "vmulsd", "vmulss", "vorpd", "vorps", "vpaddd", "vpaddq", "vpand", "vpxor",
    "vsubpd", "vsubps", "vsubsd", "vsubss", "vunpcklpd", "vunpcklps", "vxorpd", "vxorps",
    "vzeroall", "vzeroupper", "wait", "wbinvd", "wrmsr", "xadd", "xchg", "xlat", "xlatb",
    "xor", "xorpd", "xorps",
})
# fmt: on

# Directives that colour anywhere on a line (SYS_EXIT equ 60, times 64 db 0).
# fmt: off
DIRECTIVES_ANY = frozenset({
    "align", "alignb", "at", "bits", "common", "cpu", "db", "dd", "default", "do", "dq", "dt",
    "dw", "dy", "dz", "endstruc", "equ", "extern", "float", "global", "iend", "incbin",
    "istruc", "org", "resb", "resd", "reso", "resq", "rest", "resw", "resy", "resz", "section",
    "segment", "struc", "times", "use16", "use32", "warning",
})
# fmt: on

# Preprocessor keywords, recognized by their leading %.
# fmt: off
PREPROCESSOR = frozenset({
    "%arg", "%assign", "%clear", "%define", "%depend", "%elif", "%elifctx", "%elifdef",
    "%elifempty", "%elifenv", "%elifidn", "%elifidni", "%elifmacro", "%elifn", "%elifndef",
    "%elifnempty", "%elifnenv", "%elifnidn", "%elifnidni", "%elifnmacro", "%elifnnum",
    "%elifnstr", "%elifntoken", "%elifnum", "%elifstr", "%eliftoken", "%else", "%endif",
    "%endmacro", "%endrep", "%error", "%exitrep", "%fatal", "%iassign", "%idefine", "%if",
    "%ifctx", "%ifdef", "%ifempty", "%ifenv", "%ifidn", "%ifidni", "%ifmacro", "%ifn",
    "%ifndef", "%ifnempty", "%ifnenv", "%ifnidn", "%ifnidni", "%ifnmacro", "%ifnnum",
    "%ifnstr", "%ifntoken", "%ifnum", "%ifstr", "%iftoken", "%iline", "%imacro", "%include",
    "%ixdefine", "%line", "%local", "%macro", "%pathsearch", "%pop", "%pragma", "%push",
    "%rep", "%repl", "%rotate", "%stacksize", "%strcat", "%strlen", "%substr", "%unmacro",
    "%use", "%warning", "%xdefine",
})
# fmt: on

SUPPORTED_TAGS = frozenset({"code", "span"})


class AsmSyntaxError(ValueError):
    """A block that this lexer refuses to colour."""


class AsmLexer:
    """Colour class for every character of an assembly source text."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.end = len(source)
        self.pos = 0
        self.colours: list[str | None] = [None] * self.end
        self.line_start = 0

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
            if char == ";":
                self.comment()
            elif char in "\"'`":
                self.string(char)
            else:
                self.token()
        return self.colours

    def paint(self, start: int, stop: int, colour: str) -> None:
        """Give characters start..stop-1 one colour."""
        for index in range(start, min(stop, self.end)):
            self.colours[index] = colour

    def line_number(self) -> int:
        """1-based line of the current position."""
        return self.src.count("\n", 0, self.pos) + 1

    def comment(self) -> None:
        """; to the end of the line."""
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.paint(self.pos, stop, C_COMMENT)
        self.pos = stop

    def string(self, quote: str) -> None:
        """A "..." or '...' or `...` literal, escapes and line breaks included."""
        start = self.pos
        pos = start + 1
        while pos < self.end:
            char = self.src[pos]
            if char == "\\":
                pos += 2
            elif char == quote:
                self.paint(start, pos + 1, C_STRING)
                self.pos = pos + 1
                return
            else:
                pos += 1
        raise AsmSyntaxError(f"line {self.line_number()}: unterminated {quote} string")

    def token(self) -> None:  # pylint: disable=too-many-branches
        """Paint one word, number or punctuation character at self.pos."""
        src = self.src
        at_start = self.pos == self.line_start or src[self.line_start : self.pos].strip() == ""
        if at_start:
            label = LABEL_RE.match(src, self.pos)
            if label:
                self.paint(self.pos, label.end(), C_LABEL)
                self.pos = label.end()
                return
        if src[self.pos] == "%":
            macro = MACRO_PARAM_RE.match(src, self.pos)
            if macro:
                word = macro.group()
                if word in PREPROCESSOR:
                    self.paint(self.pos, macro.end(), C_KEYWORD)
                else:
                    self.paint(self.pos, macro.end(), C_REGISTER)
                self.pos = macro.end()
                return
        if src[self.pos].isdigit() or src[self.pos] == "$":
            number = NUMBER_RE.match(src, self.pos)
            if number and number.end() > self.pos:
                self.paint(self.pos, number.end(), C_NUMBER)
                self.pos = number.end()
                return
        ident = IDENT_RE.match(src, self.pos)
        if ident:
            word = ident.group()
            lower = word.lower()
            if (lower in MNEMONICS and self.instruction_position()) or lower in DIRECTIVES_ANY:
                self.paint(self.pos, ident.end(), C_KEYWORD)
            elif REGISTER_RE.fullmatch(lower):
                self.paint(self.pos, ident.end(), C_REGISTER)
            elif CONSTANT_RE.fullmatch(word):
                self.paint(self.pos, ident.end(), C_CONSTANT)
            self.pos = ident.end()
            return
        self.pos += 1

    def instruction_position(self) -> bool:
        """Whether the word starts a statement: only an optional label before it."""
        before = self.src[self.line_start : self.pos]
        stripped = before.strip()
        if not stripped:
            return True
        return bool(re.fullmatch(r"[A-Za-z_?@~$.][\w?@~$.#]*:", stripped))


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
    """Coloured inner HTML; raises AsmSyntaxError when it cannot be lexed."""
    unsupported = tag_names(inner) - SUPPORTED_TAGS
    if unsupported:
        raise AsmSyntaxError(f"tag {min(unsupported)} inside the block")
    segments: list[re.Match[str] | None] = list(CODE_RE.finditer(inner))
    if not segments:
        segments = [None]
    texts = [units_text(split_units(segment.group(1))) if segment else units_text(split_units(inner)) for segment in segments]
    colours = AsmLexer("\n".join(texts)).run()
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
        raise AsmSyntaxError("round trip failed (NOT written)")
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


def class_list(attrs: str) -> list[str]:
    """Classes of a tag's attribute string."""
    match = CLASS_ATTR_RE.search(attrs)
    if not match:
        return []
    raw = match.group(2) or match.group(3) or ""
    return re.findall(r"\S+", raw)


def process_file(path: Path, *, write: bool) -> FileStats:
    """Highlight the asmcode blocks of one file; write it when asked.

    Bytes in and out, so CRLF line endings stay. A file without asmcode is
    not decoded at all; one that has the class but is not UTF-8 is reported.
    """
    stats = FileStats(path)
    raw = path.read_bytes()
    if b"asmcode" not in raw:
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
        except AsmSyntaxError as exc:
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
    parser = argparse.ArgumentParser(description="Highlight assembly blocks (asmcode) in .php files.")
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
