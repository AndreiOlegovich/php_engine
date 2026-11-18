#!/usr/bin/env python3
# version 1.7.0
"""Shared helpers for code_blocks.py and quiz_answers.py.

One table of language presets, the extraction of the code blocks of a page,
the normalisation of compiler output and page text, the matcher for quiz
options and the build-and-run step: on this machine with the compilers of
PATH, or in one docker container for the whole run. The programs are built
in parallel, each in its own folder, so a whole course takes minutes.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import os
import re
import shlex
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

VERSION = "1.7.0"

DEFAULT_JOBS = os.cpu_count() or 1

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one command and capture its text output; the only subprocess call."""
    return subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )


# --------------------------------------------------------------------------
# Language presets: one table, one line per language.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Language:  # pylint: disable=too-many-instance-attributes
    """A language preset: block class, extension, build command, program regex.

    ``run`` is for an interpreted language whose build produces no program of
    its own: the source is run by its runtime, as ``node {src}`` does. An
    empty ``run`` means the build writes ``{out}`` and that file is run.

    ``module_extension`` is the extension of a file the runtime reads as a
    module, for a question that calls its program one: Node reads a plain
    ``.js`` file as a sloppy CommonJS script, and ``.mjs`` as a strict ES
    module. ``named_extensions`` are the extensions a stated run command may
    give its file, as ``node count.cjs``; the extracted file keeps that one.

    ``timeout`` is the default of --timeout, in seconds. Node needs a longer
    one: loading jsdom alone takes about 2 s, and with --jobs at the number
    of CPUs a DOM program that finishes alone in 3 s can pass 5 s.
    """

    name: str
    code_class: str
    extension: str
    build: str
    program_regex: str
    run: str = ""
    module_extension: str = ""
    named_extensions: tuple[str, ...] = ()
    timeout: float = 5.0


LANGUAGES: dict[str, Language] = {
    "rust": Language(
        "rust",
        "rustcode",
        "rs",
        "rustc --edition 2024 {src} -o {out}",
        r"^\s*fn\s+main\s*\(",
    ),
    "c": Language(
        "c",
        "ccode",
        "c",
        "gcc -Wall -Wextra {src} -o {out}",
        r"^\s*(?:int|void)\s+main\s*\([^;{}]*\)\s*\{",
    ),
    "cpp": Language(
        "cpp",
        "cppcode",
        "cpp",
        "g++ -std=c++23 -Wall -Wextra {src} -o {out}",
        r"^\s*(?:int|void)\s+main\s*\([^;{}]*\)\s*\{",
    ),
    "go": Language(
        "go",
        "gocode",
        "go",
        "go build -o {out} {src}",
        r"^\s*func\s+main\s*\(",
    ),
    "haskell": Language(
        "haskell",
        "haskellcode",
        "hs",
        "ghc -O1 -o {out} {src}",
        r"^\s*main\s*(?:::|=\s)",
    ),
    "fortran": Language(
        "fortran",
        "fortrancode",
        "f90",
        "gfortran -Wall -Wextra -std=f2018 {src} -o {out}",
        r"^\s*program\s+[a-z]\w*",
    ),
    "cobol": Language(
        "cobol",
        "cobolcode",
        "cob",
        "cobc -x -free -o {out} {src}",
        r"^\s*program-id\.",
    ),
    "asm": Language(
        "asm",
        "asmcode",
        "asm",
        "nasm -f elf64 -o {out}.o {src} && ld -o {out} {out}.o",
        r"^\s*(?:global\s+_start|_start\s*:)",
    ),
    "javascript": Language(
        "javascript",
        "jscode",
        "js",
        "node --check {src}",
        r"^\s*console\.[A-Za-z]+\s*\(",
        "node {src}",
        module_extension="mjs",
        named_extensions=("js", "mjs", "cjs"),
        timeout=15.0,
    ),
}


def program_pattern(regex: str) -> re.Pattern[str]:
    """Compile a whole-program regex: multiline, case-insensitive."""
    return re.compile(regex, re.MULTILINE | re.IGNORECASE)


def build_command(template: str, source: str, output: str) -> str:
    """Fill {src} and {out} of a build command; other braces stay as they are."""
    return template.replace("{src}", source).replace("{out}", output)


# Compilers a question may name in its own build command, per preset.
STATED_COMPILERS: dict[str, frozenset[str]] = {
    "c": frozenset({"gcc", "cc", "clang"}),
    "cpp": frozenset({"g++", "c++", "clang++"}),
    "rust": frozenset({"rustc"}),
    "fortran": frozenset({"gfortran"}),
    "cobol": frozenset({"cobc"}),
    "haskell": frozenset({"ghc"}),
}


SHELL_LIST_RE = re.compile(r"\s*(?:;|&&|\|\|)\s*")


def shell_segments(snippets: Iterable[str]) -> list[str]:
    """Every command of the snippets: one per line, a line split at ; && and ||.

    "gcc -O2 warn.c -o warn; echo "status: $?"" is two commands, the build and
    an echo, so neither swallows the other.
    """
    segments: list[str] = []
    for snippet in snippets:
        for line in snippet.splitlines():
            segments += [normalize_space(part) for part in SHELL_LIST_RE.split(line) if part.strip()]
    return segments


def stated_build(  # pylint: disable=too-many-branches
    language: str, snippets: list[str], default: str, extension: str
) -> str:
    """The build command a question states for its own program, or the default.

    A question like "built with `gcc -std=c17 -O2 opt.c -o opt`" is only
    answerable with those flags, so the file name and the output are swapped
    for {src} and {out}. Anything that is not a plain one-file compile with
    the compiler of the preset is ignored.
    """
    compilers = STATED_COMPILERS.get(language)
    if not compilers:
        return default
    for segment in shell_segments(snippets):
        parts = segment.split()
        if len(parts) < 2 or parts[0] not in compilers:
            continue
        if sum(part.endswith("." + extension) for part in parts) != 1:
            continue
        if any(flag in parts for flag in ("-c", "-E", "-S")):
            continue
        if any(">" in part or "|" in part for part in parts):
            continue
        rebuilt: list[str] = []
        source_seen = False
        output_seen = False
        index = 0
        while index < len(parts):
            part = parts[index]
            if part == "-o":
                if index + 1 >= len(parts):
                    break
                rebuilt += ["-o", "{out}"]
                output_seen = True
                index += 2
                continue
            if not source_seen and part.endswith("." + extension):
                rebuilt.append("{src}")
                source_seen = True
            else:
                rebuilt.append(part)
            index += 1
        if not source_seen:
            continue
        if not output_seen:
            rebuilt += ["-o", "{out}"]
        return " ".join(rebuilt)
    return default


REDIRECT_CHAR_RE = re.compile(r"[<>|]")


def stated_run(snippets: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    """The program name and arguments a question states, as stated_page_run reads them."""
    run = stated_page_run(snippets)
    return run.name, run.arguments


def stated_page_run(snippets: Iterable[str]) -> PageRun:
    """The run a question states: name, arguments and NAME=value variables.

    A question like "run as `./args one two`" is only answerable with those
    arguments, so they are passed to the program, and `GREETING=hi node
    env.mjs` with its variable. A JavaScript question states it as `node
    count.js one two three`: the name is then the file the runtime is given,
    whose extension may say .mjs or .cjs. A command that redirects or pipes
    its input or output, other than 2>&1, is left to the default: the reader
    of such a question sees a file, or the end of a pipe, that the tool
    cannot reproduce.
    """
    for snippet in snippets:
        for line in snippet.splitlines():
            if REDIRECT_CHAR_RE.search(line.replace("2>&1", " ")):
                continue
            run = parse_run_line(line)
            if run is not None and run.name:
                return run
    return PageRun()


@dataclass(frozen=True)
class PageRun:
    """How a page runs a block: the command line right after the block.

    ``name`` is the file node is given ("env.mjs") or the program a build
    wrote ("./args"). ``environment`` holds the NAME=value words in front of
    it, as in ``GREETING=hello node env.mjs``. ``input_from`` is the command
    a pipe feeds it from, or the file of a ``<`` redirect: the tools cannot
    provide that input, so a run that fails for want of it is a SKIP.
    """

    name: str = ""
    arguments: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    input_from: str = ""


OUTPUT_REDIRECT_RE = re.compile(r"(?<!\S)\d?(?:>>?|&>)(?:&\d+|\s*[^\s|;&<>]+)")
INPUT_REDIRECT_RE = re.compile(r"(?<!\S)<\s*([^\s|;&<>]+)")
ENV_WORD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
NODE_FILE_RE = re.compile(r"^(?:[A-Za-z0-9_.-]+/)*([A-Za-z0-9_.-]+\.[cm]?js)$")
PROGRAM_WORD_RE = re.compile(r"^\./[A-Za-z0-9_.-]+$")
UNSAFE_WORD_RE = re.compile(r"[$`*?(){}<>]")
LIST_TOKENS = frozenset({"&&", "||", ";", "&"})


def run_stage(words: Sequence[str]) -> PageRun | None:
    """The run of one stage of a pipeline: [NAME=value ...] node file or ./prog, then arguments."""
    environment: list[tuple[str, str]] = []
    rest = list(words)
    while rest and (variable := ENV_WORD_RE.match(rest[0])):
        environment.append((variable.group(1), variable.group(2)))
        rest.pop(0)
    if not rest:
        return None
    if rest[0] == "node":
        node_file = NODE_FILE_RE.match(rest[1]) if len(rest) > 1 else None
        if node_file is None:
            return None
        name, arguments = node_file.group(1), rest[2:]
    elif PROGRAM_WORD_RE.match(rest[0]):
        name, arguments = rest[0], rest[1:]
    else:
        return None
    if any(UNSAFE_WORD_RE.search(word) for word in arguments + [value for _, value in environment]):
        return None
    return PageRun(name, tuple(arguments), tuple(environment))


def parse_run_line(line: str) -> PageRun | None:
    """The run a shell line makes, or None when it runs no block.

    ``gcc x.c -o x && ./x one`` runs ``./x one``; ``npm ls --json | node
    tree.js`` runs tree.js with its input from npm; ``./demo 2>&1 | head``
    runs ``./demo``, whose output redirects do not change the program. A line
    with an expansion ($HOME, a glob) or a node option is not used: the tools
    could not run it as the page does.
    """
    input_file = ""
    found = INPUT_REDIRECT_RE.search(line)
    if found:
        input_file = found.group(1)
        line = INPUT_REDIRECT_RE.sub(" ", line)
    line = OUTPUT_REDIRECT_RE.sub(" ", line)
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    commands: list[list[str]] = [[]]
    for token in tokens:
        if token in LIST_TOKENS:
            commands.append([])
        else:
            commands[-1].append(token)
    for command in commands:
        stages: list[list[str]] = [[]]
        for token in command:
            if token == "|":
                stages.append([])
            else:
                stages[-1].append(token)
        for index, stage in enumerate(stages):
            run = run_stage(stage)
            if run is None:
                continue
            feed = " | ".join(" ".join(words) for words in stages[:index]) or input_file
            return PageRun(run.name, run.arguments, run.environment, feed)
    return None


RUNNERS = frozenset({"node", "make", "cargo", "ctest", "meson", "ninja"})


def build_only(command: str, language: str) -> bool:
    """True when every line of the command compiles with the preset's compiler and none runs.

    "gcc -Wall -Wextra badfmt.c -o badfmt" and nothing after it: the page
    shows the warning and says the program is not run, often because it
    would crash or never end. Running it anyway proves nothing the page says.
    """
    compilers = STATED_COMPILERS.get(language)
    segments = shell_segments([command])
    if not compilers or not any(segment.split()[0] in compilers for segment in segments):
        return False
    for segment in segments:
        words = segment.split()
        if words[0] in RUNNERS or any(word.startswith("./") for word in words):
            return False
    return True


READ_OPEN_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r'\bfopen\s*\(\s*"([^"]+)"\s*,\s*"r'),
    re.compile(r'\bifstream\s+\w+\s*[({]\s*"([^"]+)"'),
    re.compile(r'\bFile::open\s*\(\s*"([^"]+)"'),
    re.compile(r'\bread_to_string\s*\(\s*"([^"]+)"'),
)
WRITE_OPEN_RE = re.compile(r'\b(?:fopen\s*\(\s*"([^"]+)"\s*,\s*"[wa]|ofstream\s+\w+\s*[({]\s*"([^"]+)"|File::create\s*\(\s*"([^"]+)")')
VOID_MAIN_RE = re.compile(r"^\s*void\s+main\s*\(", re.MULTILINE)


def unread_file(code: str, workdir: Path) -> str:
    """A file the block opens for reading by name, does not write itself, and the folder lacks, or ''.

    fopen("records.bin", "rb") in a block that reads what an earlier block
    of the lesson wrote: the run fails here because the file is not in its
    folder, not because the page is wrong.
    """
    written = {name for match in WRITE_OPEN_RE.finditer(code) for name in match.groups() if name}
    for pattern in READ_OPEN_RES:
        for match in pattern.finditer(code):
            name = match.group(1)
            if name not in written and not (workdir / name).exists():
                return name
    return ""


def page_run(command: str) -> PageRun:
    """The first run of a command paragraph, or an empty PageRun."""
    for line in command.splitlines():
        run = parse_run_line(line)
        if run is not None:
            return run
    return PageRun()


# --------------------------------------------------------------------------
# Extraction of <p class="...code"> blocks from a page.
# --------------------------------------------------------------------------

P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
INLINE_TAG_RE = re.compile(r"</?(?:span|b|i|a|em|strong)\b[^>]*>", re.IGNORECASE)
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
SPACE_RE = re.compile(r"\s+")


@dataclass
class CodeBlock:
    """One code block of a page: where it is and its source text."""

    path: Path
    number: int
    code: str
    caption: str = ""  # X of a "The file X:" paragraph right before the block
    command: str = ""  # the command paragraphs after it, up to the next block: one line per command
    context: str = ""  # the page text between the blocks of this class before and after it


def class_list(attrs: str) -> list[str]:
    """The classes of a tag's attribute string."""
    match = CLASS_ATTR_RE.search(attrs)
    if not match:
        return []
    return (match.group(1) or match.group(2) or "").split()


def php_unescape(text: str) -> str:
    """Undo the escapes a PHP single-quoted string needs: \\' and \\\\."""
    return text.replace("\\'", "'").replace("\\\\", "\\")


def block_text(inner: str) -> str:
    """One block as source: <code> per line, tags out, entities decoded.

    The PHP escapes are undone first, as they are in ``page_text``: the page
    is read as PHP source, and only then is the HTML it emits decoded. A
    backslash the source writes as ``\\&#39;`` belongs to the code, not to
    the PHP string.
    """
    lines = [html.unescape(php_unescape(INLINE_TAG_RE.sub("", line))) for line in CODE_RE.findall(inner)]
    return "\n".join(lines)


FNAME_RE = re.compile(r"""class\s*=\s*["']fname["'][^>]*>\s*([^<\s]+)\s*<""", re.IGNORECASE)


def command_text(inner: str) -> str:
    """A command paragraph as text: one line per <br> or newline, tags out."""
    text = html.unescape(php_unescape(TAG_RE.sub("", BR_RE.sub("\n", inner))))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def extract_blocks(text: str, code_class: str, path: Path | None = None) -> list[CodeBlock]:
    """Every block of the page whose class list holds code_class, in page order.

    A paragraph right before the block that names a file ("The file
    <b class="fname">env.mjs</b>:") gives its caption ("right" means only
    white space between the two). The command paragraphs (class cmd1) after
    it, up to the next block of the class, give the commands that build and
    run it; blocks with no command or output paragraph between them share
    the commands and the output after the last of them. The context is what the page says about this block: the prose
    paragraphs right before it (back to the last code, command or output
    paragraph, so another block's output is not in it) and everything after
    it up to the next block of the class, its commands and output included.
    """
    where = path if path is not None else Path("<page>")
    paragraphs = list(P_BLOCK_RE.finditer(text))
    mine = [index for index, match in enumerate(paragraphs) if code_class in class_list(match.group(1))]
    blocks: list[CodeBlock] = []
    # The text about the last block stops where the lesson stops: the
    # exercises and the quiz ask about failures, they do not show them.
    exercises, quiz = text.find('id="exercises"'), text.find("LessonQuiz::render(")
    ends = [text.rfind("<", 0, exercises) if exercises >= 0 else -1, text.rfind("\n", 0, quiz) + 1 if quiz >= 0 else -1]
    tail = min((end for end in ends if end >= 0), default=len(text))

    def runs_or_shows(paragraph: re.Match[str]) -> bool:
        return any(name.startswith(("cmd", "cout")) for name in class_list(paragraph.group(1)))

    for order, index in enumerate(mine):
        match = paragraphs[index]
        caption = ""
        if index > 0 and not text[paragraphs[index - 1].end() : match.start()].strip():
            named = FNAME_RE.search(paragraphs[index - 1].group(2))
            caption = named.group(1) if named else ""
        # Blocks with no command or output between them are one group: a
        # broken and a fixed program, or a module and the file that imports
        # it, share the commands and the output that follow the last one.
        last = order
        while last + 1 < len(mine) and not any(runs_or_shows(paragraph) for paragraph in paragraphs[mine[last] + 1 : mine[last + 1]]):
            last += 1
        following = mine[last + 1] if last + 1 < len(mine) else len(paragraphs)
        commands = [command_text(paragraph.group(2)) for paragraph in paragraphs[mine[last] + 1 : following] if any(name.startswith("cmd") for name in class_list(paragraph.group(1)))]
        first = index
        while first > 0 and not any(name.endswith("code") or name.startswith(("cmd", "cout")) for name in class_list(paragraphs[first - 1].group(1))):
            first -= 1
        start = paragraphs[first].start()
        end = paragraphs[following].start() if following < len(paragraphs) else len(text)
        if match.end() < tail < end:
            end = tail
        context = page_text(text[start : match.start()] + " " + text[match.end() : end])
        blocks.append(CodeBlock(where, order + 1, block_text(match.group(2)), caption, "\n".join(part for part in commands if part), context))
    return blocks


# --------------------------------------------------------------------------
# Normalisation of page text and compiler output.
# --------------------------------------------------------------------------


def normalize_space(text: str) -> str:
    """Collapse every run of whitespace to one space and strip the ends."""
    return SPACE_RE.sub(" ", text).strip()


def page_text(text: str) -> str:
    """A page as one line of readable text: tags out, entities decoded."""
    plain = BR_RE.sub(" ", text)
    plain = TAG_RE.sub("", plain)
    return normalize_space(html.unescape(php_unescape(plain)))


DIAGNOSTIC_RE = re.compile(r"\berror\b|^[^\s:]+:\d+(?::\d+)?:", re.IGNORECASE)
ERROR_TEXT_RE = re.compile(r"\b(?:fatal\s+)?error(?:\[[^\]]*\])?\s*:", re.IGNORECASE)
PATH_PREFIX_RE = re.compile(r"^.*?:\d+(?::\d+)?:\s*")
NODE_ERROR_RE = re.compile(r"^(?:[A-Za-z]+Error|Error)\b.*:")


def first_error_line(output: str) -> str:
    """The first line of compiler output that reports an error, normalised.

    g++ and clang put context before the error when a template, a macro or a
    header is involved: 'In instantiation of', 'required from here' or a
    warning. Those lines carry a ``file:line:`` position like a diagnostic,
    so the first line that spells out ``error:`` (or ``fatal error:``) is the
    one to compare. A language whose errors say something else, as Go does,
    has no such line, and then the first diagnostic line is used as before.
    Node prints the position on a line of its own and the message as
    ``SyntaxError: ...`` below the source line and its caret, so that line is
    the error of a JavaScript build.
    """
    lines = [normalize_space(line) for line in output.splitlines()]

    def with_following(index: int) -> str:
        """A bare 'error:' takes the message of the next line with it."""
        line = lines[index]
        if line.endswith("error:") or line == "error:":
            for following in lines[index + 1 :]:
                if following:
                    return normalize_space(line + " " + following)
        return line

    for index, line in enumerate(lines):
        if line and not line.startswith("#") and ERROR_TEXT_RE.search(line):
            return with_following(index)
    # Node writes the position on its own line and the message as
    # "SyntaxError: ..." below the source line and the caret. That line is
    # checked before the generic diagnostic, because the source line of a
    # program may itself say "console.error" and look like one.
    for line in lines:
        if line and NODE_ERROR_RE.match(line):
            return line
    for index, line in enumerate(lines):
        if line and not line.startswith("#") and DIAGNOSTIC_RE.search(line):
            return with_following(index)
    for line in lines:
        if line:
            return line
    return ""


def error_core(line: str) -> str:
    """The diagnostic part of an error line: from 'error' on, or without path:line:col."""
    match = re.search(r"\berror\b", line, re.IGNORECASE)
    if match:
        return line[match.start() :]
    return PATH_PREFIX_RE.sub("", line)


def expected_failure(error_line: str, text: str) -> bool:
    """True when the page itself shows this compiler error."""
    core = normalize_space(error_core(error_line))
    return bool(core) and core in text


SKIP_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"unresolved import", "unresolved import: a crate or module outside the block"),
    (r"can't find crate", "crate not available to a single file"),
    (r"no external crate", "crate not available to a single file"),
    (r"file not found for module", "module in another file"),
    (r"fatal error:.*no such file or directory", "include or header not found"),
    (r"no required module provides package", "package outside the file"),
    (r"cannot find package", "package outside the file"),
    (r"cannot find module", "package or module outside the block"),
    (r"err_module_not_found", "package or module outside the block"),
    (r"module_not_found", "package or module outside the block"),
    (r"is not in std", "package outside the file"),
    (r"could not find module", "module outside the file"),
    (r"failed to load interface for", "module outside the file"),
    (r"cannot open module file", "module in another file"),
    (r"undefined reference to", "another file or library of the example"),
    (r"cannot find \S+: no such file or directory", "another file or library of the example"),
    (r"environment variable .* not defined at compile time", "set by a build script or cargo"),
    (r"`out_dir`", "set by a build script or cargo"),
)


def skip_reason(output: str) -> str:
    """Why a build failure means the block cannot be built alone, or ''."""
    lowered = output.lower()
    for pattern, reason in SKIP_PATTERNS:
        if re.search(pattern, lowered):
            return reason
    return ""


PERROR_MISSING_RE = re.compile(r"^([^\s:]*[./][^\s:]*): No such file or directory$", re.MULTILINE)
MISSING_FILE_RE = re.compile(r"\bENOENT: no such file or directory, (?:open|scandir|opendir|stat|lstat|access|readlink) '([^']+)'")


def missing_file(error_output: str) -> str:
    """The file a failed run could not find, as Node names it, or ''.

    A block that reads the package-lock.json an earlier npm install wrote
    dies with "ENOENT: no such file or directory, open 'package-lock.json'":
    the file belongs to the lesson's project, not to the block. A C program
    says it with perror: "demo/a.txt: No such file or directory". Both tools
    ask only after the run proved nothing else, because a lesson about files
    may fail to open one on purpose and show it.
    """
    match = MISSING_FILE_RE.search(error_output) or PERROR_MISSING_RE.search(error_output)
    if match:
        return match.group(1)
    return "the file the program opens" if "kind: NotFound" in error_output else ""


NOT_FOUND_WORDS = ("no such file or directory", "enoent", "kind: notfound")
NOT_FOUND_REACH = 200


def shows_not_found(text: str, name: str = "") -> bool:
    """True when the text around a block shows this file as not there: the lesson fails on purpose.

    "missing.txt: No such file or directory" next to the block is the
    lesson's point. When the text names the file, the not-found words must
    stand within 200 characters of the name: a page that runs
    node_modules/.bin/wc-cli in one command and shows ENOENT for another
    file much further down has not shown this one missing. A page that shows
    the error without the name ("errno: 2 message: No such file or
    directory") counts as it is.
    """
    lowered = text.lower()
    words = [found.start() for word in NOT_FOUND_WORDS for found in re.finditer(re.escape(word), lowered)]
    if not words:
        return False
    names = [found.start() for found in re.finditer(re.escape(name.lower()), lowered)] if name else []
    if not names:
        return True
    return any(abs(at - word) <= NOT_FOUND_REACH for at in names for word in words)


def missing_variable(error_output: str) -> bool:
    """True when a run died for an environment variable it did not get.

    Rust says ``called `Result::unwrap()` on an `Err` value: NotPresent`` when
    std::env::var finds nothing: the page sets the variable in a build
    script or a command the block does not carry.
    """
    return bool(re.search(r"\bErr` value: NotPresent\b", error_output))


FILE_ARGUMENT_RE = re.compile(r"^[\w./-]*[A-Za-z_][\w-]*\.[A-Za-z]\w*$")


def missing_argument_file(arguments: Sequence[str], workdir: Path) -> str:
    """The first argument that names a file the folder lacks, as notes.txt in `node cli.js notes.txt`, or ''."""
    for word in arguments:
        if FILE_ARGUMENT_RE.match(word) and not (workdir / word).exists():
            return word
    return ""


QUOTED_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
MODULE_IMPORT_RE = re.compile(r"^\s*import\s+[A-Za-z_]\w*\s*;", re.MULTILINE)


def unbuildable_reason(code: str, workdir: Path) -> str:
    """Why a failed block needs another file of its example, or ''.

    The compiler does not always say so: a quoted #include may silently find
    a system header of the same name, and a C++ module import fails as a
    syntax error when the module is not compiled yet.
    """
    missing = [name for name in QUOTED_INCLUDE_RE.findall(code) if not (workdir / name).exists()]
    if missing:
        return f"include of another file of the example: {missing[0]}"
    if MODULE_IMPORT_RE.search(code):
        return "C++ module from another file of the example"
    return ""


# --------------------------------------------------------------------------
# Build and run the extracted programs: one folder per job, in parallel.
# --------------------------------------------------------------------------


@dataclass
class ProgramResult:  # pylint: disable=too-many-instance-attributes
    """What happened to one extracted program."""

    status: str  # "built", "compile_error" or "error"
    build_output: str = ""
    error_line: str = ""
    output: str = ""
    error_output: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    skip: str = ""
    diagnostics: str = ""  # the warnings of a build that succeeded


BUILD_FAIL = "@@BUILD_FAIL\n"
BUILD_OK = "@@BUILD_OK\n"
DIAG = "@@DIAG\n"


@dataclass
class BuildJob:  # pylint: disable=too-many-instance-attributes
    """One program to build: its own folder, its source and its build command."""

    workdir: Path
    source_name: str
    build: str
    run_name: str = ""
    arguments: tuple[str, ...] = ()
    run_command: str = ""  # filled template of a preset that runs its source
    environment: tuple[tuple[str, str], ...] = ()  # NAME=value pairs the run gets
    runs: bool = True  # False when the page only builds the program: it is built, not run


class Session:  # pylint: disable=too-few-public-methods
    """A place where jobs are built: this machine or one docker container."""

    def run(self, job: BuildJob, *, run: bool, timeout: float, output_limit: int) -> ProgramResult:
        """Build one job and, when asked, run it."""
        raise NotImplementedError

    def close(self) -> None:
        """Release what the session holds; safe to call more than once."""

    def __enter__(self) -> Self:
        """Enter the context: the session is ready."""
        return self

    def __exit__(self, *exc_info: object) -> Literal[False]:
        """Leave the context: always release the container."""
        self.close()
        return False


class LocalSession(Session):
    """Builds in each job's folder with the compilers of PATH."""

    def __init__(self, process_runner: ProcessRunner = run_process) -> None:
        """Remember how to start a process, so a test can replace it."""
        self.process_runner = process_runner

    def run(self, job: BuildJob, *, run: bool, timeout: float, output_limit: int) -> ProgramResult:
        """Build one job in its own folder and, when asked, run it there."""
        output = "./prog"
        command = build_command(job.build, job.source_name, output)
        program = build_command(job.run_command, job.source_name, output) if job.run_command else ""
        script = build_script(
            command,
            output,
            run=run and job.runs,
            timeout=timeout,
            limit=output_limit,
            build_log="./.build.log",
            run_log="./.run.log",
            error_log="./.error.log",
            run_name=job.run_name,
            arguments=job.arguments,
            run_command=program,
            environment=job.environment,
        )
        try:
            process = self.process_runner(
                ["bash", "-c", script],
                cwd=job.workdir,
                timeout=max(120.0, timeout * 4),
            )
        except subprocess.TimeoutExpired:
            return ProgramResult(status="error", error_line="the build did not finish in time")
        return parse_script_output(process.stdout, process.stderr)


class DockerSession(Session):
    """One container for the whole run: sources read-only, /tmp writable."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        image: str,
        root: Path,
        *,
        network: bool = False,
        process_runner: ProcessRunner = run_process,
        docker: str = "docker",
    ) -> None:
        """Start the container and keep its name for exec and remove."""
        self.root = root
        self.process_runner = process_runner
        self.docker = docker
        self.name = f"course-check-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        argv = docker_start_argv(image, root, self.name, network=network, docker=docker)
        process = process_runner(argv, timeout=120)
        if process.returncode != 0:
            raise RuntimeError(f"docker run failed: {normalize_space(process.stderr or process.stdout)[:400]}")

    def run(self, job: BuildJob, *, run: bool, timeout: float, output_limit: int) -> ProgramResult:
        """Copy the job into a writable folder of the container and build there."""
        relative = job.workdir.resolve().relative_to(self.root.resolve()).as_posix()
        folder = f"/tmp/{relative}"
        output = "./prog"
        command = build_command(job.build, job.source_name, output)
        program = build_command(job.run_command, job.source_name, output) if job.run_command else ""
        script = "\n".join(
            [
                f"mkdir -p {folder}",
                f"cp -R /src/{relative}/. {folder}/",
                f"cd {folder}",
                build_script(
                    command,
                    output,
                    run=run and job.runs,
                    timeout=timeout,
                    limit=output_limit,
                    build_log="./.build.log",
                    run_log="./.run.log",
                    error_log="./.error.log",
                    run_name=job.run_name,
                    arguments=job.arguments,
                    run_command=program,
                    environment=job.environment,
                ),
            ]
        )
        try:
            process = self.process_runner(
                docker_exec_argv(self.name, script, docker=self.docker),
                timeout=max(120.0, timeout * 4),
            )
        except subprocess.TimeoutExpired:
            return ProgramResult(status="error", error_line="the build did not finish in time")
        return parse_script_output(process.stdout, process.stderr)

    def close(self) -> None:
        """Remove the container, also after an error or Ctrl-C."""
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            self.process_runner([self.docker, "rm", "-f", self.name], timeout=60)


def docker_start_argv(
    image: str,
    root: Path,
    name: str,
    *,
    network: bool = False,
    docker: str = "docker",
) -> list[str]:
    """The command line that starts the one container of a run."""
    argv = [
        docker,
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    if not network:
        argv += ["--network", "none"]
    argv += [
        "-v",
        f"{root}:/src:ro",
        "--tmpfs",
        "/tmp:rw,exec,mode=1777",
        "--entrypoint",
        "bash",
        image,
        "-c",
        "sleep infinity",
    ]
    return argv


def docker_exec_argv(name: str, script: str, *, docker: str = "docker") -> list[str]:
    """The command line that runs one build script in the container of a run."""
    return [docker, "exec", name, "bash", "-c", script]


def open_session(
    *,
    root: Path,
    docker_image: str | None = None,
    network: bool = False,
    process_runner: ProcessRunner = run_process,
    docker: str = "docker",
) -> Session:
    """A local session, or one docker container when an image is named."""
    if docker_image:
        return DockerSession(
            docker_image,
            root,
            network=network,
            process_runner=process_runner,
            docker=docker,
        )
    return LocalSession(process_runner=process_runner)


def run_jobs(  # pylint: disable=too-many-arguments
    session: Session,
    jobs: list[BuildJob],
    *,
    workers: int = 1,
    run: bool = True,
    timeout: float = 5.0,
    output_limit: int = 2000,
) -> list[ProgramResult]:
    """Build every job, at most workers at a time; results keep the job order."""
    if workers <= 1 or len(jobs) <= 1:
        return [session.run(job, run=run, timeout=timeout, output_limit=output_limit) for job in jobs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(session.run, job, run=run, timeout=timeout, output_limit=output_limit) for job in jobs]
        return [future.result() for future in futures]


def build_script(  # pylint: disable=too-many-arguments,too-many-locals
    command: str,
    output: str,
    *,
    run: bool,
    timeout: float,
    limit: int,
    build_log: str,
    run_log: str,
    error_log: str,
    run_name: str = "",
    arguments: Sequence[str] = (),
    run_command: str = "",
    environment: Sequence[tuple[str, str]] = (),
) -> str:
    """The bash script that builds one program and, when asked, runs it.

    An interpreted language has no build output to execute, so its preset
    names a run command that runs the source, as ``node page_block1.js``.
    The environment pairs go in front of the run through env(1). The run
    reads /dev/null: a program that waits for input gets none at once, here
    as in a docker exec, instead of the terminal of whoever runs the tool.
    """
    lines = [
        "export LC_ALL=C",
        f"{command} >{build_log} 2>&1",
        "rc=$?",
        'if [ "$rc" -ne 0 ]; then',
        "    printf '@@BUILD_FAIL\\n'",
        f"    cat {build_log}",
        "    exit 0",
        "fi",
        "printf '@@BUILD_OK\\n'",
    ]
    if run:
        if run_command:
            argv = run_command
            if arguments:
                argv += " " + " ".join(shlex.quote(part) for part in arguments)
        else:
            program = run_name or output
            argv = " ".join(shlex.quote(part) for part in (program, *arguments))
            if run_name and run_name != output:
                lines.append(f"ln -sf {shlex.quote(output)} {shlex.quote(run_name)}")
        if environment:
            argv = "env " + " ".join(shlex.quote(f"{name}={value}") for name, value in environment) + " " + argv
        lines += [
            f"timeout {timeout:g} {argv} </dev/null >{run_log} 2>{error_log}",
            "rc=$?",
            f"head -c {limit} {run_log}",
            "printf '\\n@@ERR\\n'",
            f"head -c {limit} {error_log}",
            "printf '\\n@@RC %s\\n' \"$rc\"",
        ]
    lines += [f"if [ -s {build_log} ]; then printf '@@DIAG\\n'; cat {build_log}; fi"]
    return "\n".join(lines)


def parse_script_output(stdout: str, stderr: str = "") -> ProgramResult:
    """Turn the output of build_script into a ProgramResult."""
    if BUILD_FAIL in stdout:
        log = stdout.split(BUILD_FAIL, 1)[1]
        return ProgramResult(
            status="compile_error",
            build_output=log,
            error_line=first_error_line(log),
            skip=skip_reason(log),
        )
    if BUILD_OK not in stdout:
        text = (stdout + stderr).strip()
        return ProgramResult(
            status="error",
            build_output=text,
            error_line=first_error_line(text) or normalize_space(text[:200]) or "the runner failed",
        )
    body = stdout.split(BUILD_OK, 1)[1]
    diagnostics = ""
    if DIAG in body:
        body, _, diagnostics = body.rpartition(DIAG)
    if "@@RC " not in body:
        return ProgramResult(status="built", output=body.strip(), diagnostics=diagnostics.strip())
    error_output = ""
    if "@@ERR\n" in body:
        body, _, rest = body.partition("@@ERR\n")
        error_output, _, tail = rest.rpartition("@@RC ")
        error_output = error_output.rstrip("\n")
    else:
        body, _, tail = body.rpartition("@@RC ")
    code_text = tail.strip().split()[0] if tail.strip() else ""
    code: int | None
    try:
        code = int(code_text)
    except ValueError:
        code = None
    return ProgramResult(
        status="built",
        output=body.rstrip("\n"),
        error_output=error_output,
        exit_code=code,
        timed_out=code == 124,
        diagnostics=diagnostics.strip(),
    )


# --------------------------------------------------------------------------
# Page collection and the quiz option matcher.
# --------------------------------------------------------------------------


def collect_pages(paths: Iterable[str]) -> list[Path]:
    """The .php files behind the given paths, folders walked recursively."""
    files: list[Path] = []
    for name in paths:
        path = Path(name)
        if path.is_dir():
            files += sorted(item for item in path.rglob("*.php") if item.is_file())
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"no such file or directory: {name}")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


NOISE_WORDS = frozenset(
    {
        "a",
        "and",
        "by",
        "each",
        "first",
        "followed",
        "its",
        "line",
        "lines",
        "newline",
        "next",
        "on",
        "one",
        "own",
        "second",
        "separate",
        "space",
        "the",
        "then",
        "third",
        "three",
        "two",
    }
)
WORD_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
SLASH_SEPARATOR = " / "


def words(text: str) -> list[str]:
    """Lower-case words of a text, punctuation dropped."""
    return WORD_RE.findall(text.lower())


def loose_match(option: str, output: str) -> bool:
    """Compare an option and program output word by word, ignoring filler words."""
    left = [word for word in words(option) if word not in NOISE_WORDS]
    right = words(output)
    if not left or not right:
        return False
    if left == right:
        return True
    return " ".join(left) in " ".join(right) and len(left) >= max(1, len(right) - 1)


def option_lines(option: str) -> list[str]:
    """An option split on ' / ', the way the courses write several output lines."""
    return [part.strip() for part in option.split(SLASH_SEPARATOR)]


def output_lines(output: str) -> list[str]:
    """The non-empty lines of program output, stripped."""
    return [line.strip() for line in output.strip().splitlines() if line.strip()]


def matches_option(option: str, output: str) -> bool:
    """True when program output is what the option says."""
    if option.strip() == output.strip():
        return True
    if option_lines(option) == output_lines(output):
        return True
    return loose_match(option, output)


CATEGORY_RE = re.compile(
    r"\b(?:fatal\s+error(?:\[[^\]]*\])?|error(?:\[[^\]]*\])?|warning(?:\[[^\]]*\])?|note|runtime\s+error)\s*:\s*",
    re.IGNORECASE,
)
SANITIZER_RE = re.compile(
    r"\b(?:Address|Leak|Memory|Thread|UndefinedBehavior)Sanitizer\s*:\s*",
    re.IGNORECASE,
)
WARNING_FLAG_RE = re.compile(r"\[(-W[\w=+-]+)\]")
MIN_DIAGNOSTIC_CHARS = 4


def message_core(text: str) -> str:
    """A diagnostic without its 'warning:', 'error:' or sanitizer prefix."""
    line = normalize_space(text)
    match = CATEGORY_RE.search(line)
    if match:
        line = line[match.end() :]
        tool = SANITIZER_RE.match(line)
        if tool:
            line = line[tool.end() :]
    return line.strip()


def diagnostic_candidates(build_output: str, run_output: str) -> list[str]:
    """Lines that may be a quiz answer: warnings, errors and run diagnostics.

    The build output of a program that built is its warnings; the run output
    is what the program or a sanitizer wrote to stderr.
    """
    lines: list[str] = []
    for text in (build_output, run_output):
        for line in text.splitlines():
            line = normalize_space(line)
            if len(line) >= MIN_DIAGNOSTIC_CHARS:
                lines.append(line)
    return lines


def matches_diagnostic(option: str, candidates: Iterable[str]) -> bool:
    """True when the option is a diagnostic, its message or its warning flag."""
    left = message_core(option)
    if len(left) < MIN_DIAGNOSTIC_CHARS:
        return False
    flag = left.rstrip("=") if left.startswith("-W") else ""
    for candidate in candidates:
        right = message_core(candidate)
        if not right:
            continue
        if left == right or right.startswith(left) or left.startswith(right):
            return True
        if flag and flag in {found.rstrip("=") for found in WARNING_FLAG_RE.findall(right)}:
            return True
    return False


STATUS_PART_RE = re.compile(
    r"(?:exit\s+status|exit\s+code|status|exit)\s*:\s*(\d+)$",
    re.IGNORECASE,
)
STATUS_QUESTION_RE = re.compile(r"\bexit\s+(?:status|code)\b", re.IGNORECASE)
BARE_STATUS_RE = re.compile(r"^(\d{1,3})$")
THEN_STATUS_RE = re.compile(
    r"^(.*?),?\s+then\s+(?:exit\s+)?(?:status|code)\s*:?\s*(\d+)$",
    re.IGNORECASE,
)


def split_status(option: str) -> tuple[str, int | None]:
    """An option split into its output part and the exit status it names.

    A lesson writes what the shell prints as 'about to leave / exit status: 3'
    or 'exit: 139', so the last ' / ' part may name the status instead of a
    line of output. A lesson in prose writes 'work done, then status 3'.
    """
    parts = option_lines(option)
    if parts:
        match = STATUS_PART_RE.match(parts[-1])
        if match:
            return SLASH_SEPARATOR.join(parts[:-1]), int(match.group(1))
    match = STATUS_PART_RE.match(option.strip())
    if match:
        return "", int(match.group(1))
    match = THEN_STATUS_RE.match(normalize_space(option))
    if match:
        return match.group(1).strip(), int(match.group(2))
    return option, None


def asks_exit_status(question: str) -> bool:
    """True when the question asks for the exit status, not for the output."""
    return bool(STATUS_QUESTION_RE.search(question))


ERROR_PREFIX_RE = re.compile(r"^error(?:\[[^\]]*\])?:\s*", re.IGNORECASE)


def matches_error(option: str, error_line: str) -> bool:
    """True when the option is the compiler error line, or its message without the code."""
    left = normalize_space(option)
    right = normalize_space(error_core(error_line))
    if not left or not right:
        return False
    if left == right or right.startswith(left) or left.startswith(right):
        return True
    left_core = ERROR_PREFIX_RE.sub("", left)
    right_core = ERROR_PREFIX_RE.sub("", right)
    if not left_core or not right_core:
        return False
    return left_core == right_core or right_core.startswith(left_core) or left_core.startswith(right_core)


THROWN_ERROR_RE = re.compile(r"^((?:[A-Z][A-Za-z]*)?Error)\b(?:\s*\[[A-Z0-9_]+\])?\s*(?::|$)")
ERROR_NAME_RE = re.compile(r"\b((?:[A-Z][A-Za-z]*)?Error)\b")
THROW_WORD_RE = re.compile(r"\bthrow(?:s|n)?\b", re.IGNORECASE)
NAMED_ERROR_TAIL_RE = re.compile(r"^(?:$|[,:;.(]|\s+(?:at|is|reaches|because)\b)", re.IGNORECASE)


def thrown_error(output: str) -> str:
    """The name of the error a run or a build reports, as 'TypeError', or ''.

    Node prints an uncaught error as ``TypeError: message`` or
    ``AssertionError [ERR_ASSERTION]: message`` below the source line and
    its caret, and ``node --check`` prints a SyntaxError the same way.
    """
    for line in output.splitlines():
        match = THROWN_ERROR_RE.match(line.strip())
        if match:
            return match.group(1)
    return ""


def names_error(option: str, name: str) -> bool:
    """True when the option says the program ends with this error.

    The courses write it as 'a TypeError', 'A SyntaxError, because ...',
    'It throws a TypeError' or 'Nothing, because the assertion throws an
    AssertionError'. The first error the option names must be this one, and
    the option must say that it throws, or be the name with 'a' or 'an' and
    at most a clause after it. 'TypeError boom' and '1 / a ReferenceError'
    describe printed output, not the error the program ends with.
    """
    if not name:
        return False
    text = normalize_space(option)
    match = ERROR_NAME_RE.search(text)
    if not match or match.group(1) != name:
        return False
    if THROW_WORD_RE.search(text):
        return True
    prefix = text[: match.start()].strip().lower()
    return prefix in ("", "a", "an") and bool(NAMED_ERROR_TAIL_RE.match(text[match.end() :]))


CRASH_WORDS = (
    "sigsegv",
    "segmentation fault",
    "stack overflow",
    "core dumped",
    "aborted",
    "panic",
    "addresssanitizer",
    "undefinedbehaviorsanitizer",
    "leaksanitizer",
    "threadsanitizer",
)


def page_expects_failure(text: str, result: ProgramResult) -> bool:
    """True when the page shows this exit status, crash or timeout."""
    if result.exit_code is not None:
        status = re.search(
            rf"\b(?:exit\s*(?:status|code)|status|exit|code|error|returns?|returned)\s*(?::|=|\bis\b|\bwith\b)?\s*{result.exit_code}\b",
            text,
            re.IGNORECASE,
        )
        if status:
            return True
        # `echo $?` after the run, and the bare number in the output below it.
        shown = re.search(rf"echo\s+\$\?(?:(?!echo\s+\$\?).){{0,300}}?(?<![\w.]){result.exit_code}(?![\w.])", text)
        if shown:
            return True
    lowered = text.lower()
    failed = result.timed_out or (result.exit_code is not None and result.exit_code != 0)
    if failed and any(word in lowered for word in CRASH_WORDS):
        return True
    return result.timed_out and ("timed out" in lowered or "timeout" in lowered)


def temp_workdir(keep: Path | None, prefix: str) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    """The folder the extracted sources go to: --keep DIR or a temporary one."""
    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
        return keep.resolve(), None
    holder: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory(  # pylint: disable=consider-using-with
        prefix=prefix
    )
    return Path(holder.name), holder


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """The command-line options code_blocks.py and quiz_answers.py share."""
    parser.add_argument("paths", nargs="+", help=".php file or folder (folders are walked for *.php)")
    parser.add_argument(
        "--lang",
        default="rust",
        choices=sorted(LANGUAGES),
        help="language preset (default: rust)",
    )
    parser.add_argument("--ext", help="file extension of the extracted sources (default: the preset's)")
    parser.add_argument("--build", help="build command with {src} and {out} (default: the preset's)")
    parser.add_argument(
        "--program-regex",
        help="regex that marks a whole program (default: the preset's)",
    )
    parser.add_argument("--keep", type=Path, metavar="DIR", help="keep the extracted sources in DIR")
    parser.add_argument(
        "--docker",
        metavar="IMAGE",
        help="build and run in this docker image instead of this machine",
    )
    parser.add_argument("--network", action="store_true", help="let the docker container use the network")
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"programs built at the same time (default: {DEFAULT_JOBS}, the CPUs of this machine)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="seconds a program may run (default: the preset's, 5; 15 for javascript)",
    )
    parser.add_argument(
        "--output-limit",
        type=int,
        default=2000,
        help="bytes of program output kept (default: 2000)",
    )
    parser.add_argument("--json", dest="json_file", type=Path, metavar="FILE", help="write the full result to FILE")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
