#!/usr/bin/env python3
# version 1.2.0
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

VERSION = "1.2.0"

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
class Language:
    """A language preset: block class, extension, build command, program regex."""

    name: str
    code_class: str
    extension: str
    build: str
    program_regex: str


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
    for snippet in snippets:
        parts = normalize_space(snippet).split()
        if len(parts) < 2 or parts[0] not in compilers:
            continue
        if not any(part.endswith("." + extension) for part in parts):
            continue
        if any(flag in parts for flag in ("-c", "-E", "-S", "&&")):
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


RUN_LINE_RE = re.compile(r"^\s*\./([A-Za-z0-9_.-]+)\s*(.*)$")
SHELL_META_RE = re.compile(r"[<>|;&$`()*?]")


def stated_run(snippets: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    """The program name and arguments a question states for running it.

    A question like "run as `./args one two`" is only answerable with those
    arguments, so they are passed to the program. A command that redirects,
    pipes or pipes the output elsewhere is left to the default: the tool
    cannot provide the files it names.
    """
    for snippet in snippets:
        for line in snippet.splitlines():
            match = RUN_LINE_RE.match(line)
            if not match:
                continue
            rest = match.group(2).strip()
            if not rest:
                return f"./{match.group(1)}", ()
            if SHELL_META_RE.search(rest):
                continue
            return f"./{match.group(1)}", tuple(rest.split())
    return "", ()


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
    """One block as source: <code> per line, tags out, entities decoded."""
    lines = [html.unescape(INLINE_TAG_RE.sub("", line)) for line in CODE_RE.findall(inner)]
    return php_unescape("\n".join(lines))


def extract_blocks(text: str, code_class: str, path: Path | None = None) -> list[CodeBlock]:
    """Every block of the page whose class list holds code_class, in page order."""
    where = path if path is not None else Path("<page>")
    blocks: list[CodeBlock] = []
    for match in P_BLOCK_RE.finditer(text):
        if code_class not in class_list(match.group(1)):
            continue
        blocks.append(CodeBlock(where, len(blocks) + 1, block_text(match.group(2))))
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


def first_error_line(output: str) -> str:
    """The first line of compiler output that reports an error, normalised.

    g++ and clang put context before the error when a template, a macro or a
    header is involved: 'In instantiation of', 'required from here' or a
    warning. Those lines carry a ``file:line:`` position like a diagnostic,
    so the first line that spells out ``error:`` (or ``fatal error:``) is the
    one to compare. A language whose errors say something else, as Go does,
    has no such line, and then the first diagnostic line is used as before.
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
    (r"is not in std", "package outside the file"),
    (r"could not find module", "module outside the file"),
    (r"failed to load interface for", "module outside the file"),
    (r"cannot open module file", "module in another file"),
    (r"undefined reference to", "another file or library of the example"),
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
class BuildJob:
    """One program to build: its own folder, its source and its build command."""

    workdir: Path
    source_name: str
    build: str
    run_name: str = ""
    arguments: tuple[str, ...] = ()


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
        script = build_script(
            command,
            output,
            run=run,
            timeout=timeout,
            limit=output_limit,
            build_log="./.build.log",
            run_log="./.run.log",
            error_log="./.error.log",
            run_name=job.run_name,
            arguments=job.arguments,
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
        script = "\n".join(
            [
                f"mkdir -p {folder}",
                f"cp /src/{relative}/{job.source_name} {folder}/",
                f"cd {folder}",
                build_script(
                    command,
                    output,
                    run=run,
                    timeout=timeout,
                    limit=output_limit,
                    build_log="./.build.log",
                    run_log="./.run.log",
                    error_log="./.error.log",
                    run_name=job.run_name,
                    arguments=job.arguments,
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
) -> str:
    """The bash script that builds one program and, when asked, runs it."""
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
        program = run_name or output
        argv = " ".join(shlex.quote(part) for part in (program, *arguments))
        if run_name and run_name != output:
            lines.append(f"ln -sf {shlex.quote(output)} {shlex.quote(run_name)}")
        lines += [
            f"timeout {timeout:g} {argv} >{run_log} 2>{error_log}",
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


def split_status(option: str) -> tuple[str, int | None]:
    """An option split into its output part and the exit status it names.

    A lesson writes what the shell prints as 'about to leave / exit status: 3'
    or 'exit: 139', so the last ' / ' part may name the status instead of a
    line of output.
    """
    parts = option_lines(option)
    if parts:
        match = STATUS_PART_RE.match(parts[-1])
        if match:
            return SLASH_SEPARATOR.join(parts[:-1]), int(match.group(1))
    match = STATUS_PART_RE.match(option.strip())
    if match:
        return "", int(match.group(1))
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


CRASH_WORDS = (
    "sigsegv",
    "segmentation fault",
    "stack overflow",
    "core dumped",
    "aborted",
    "panic",
)


def page_expects_failure(text: str, result: ProgramResult) -> bool:
    """True when the page shows this exit status, crash or timeout."""
    if result.exit_code is not None:
        status = re.search(
            rf"\b(?:exit|status|code)\s*(?:status\s*|code\s*)?{result.exit_code}\b",
            text,
            re.IGNORECASE,
        )
        if status:
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
        default=5.0,
        help="seconds a program may run (default: 5)",
    )
    parser.add_argument(
        "--output-limit",
        type=int,
        default=2000,
        help="bytes of program output kept (default: 2000)",
    )
    parser.add_argument("--json", dest="json_file", type=Path, metavar="FILE", help="write the full result to FILE")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
