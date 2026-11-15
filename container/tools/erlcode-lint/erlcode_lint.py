#!/usr/bin/env python3
# version 0.2.0
"""Compile, dialyze and replay the Erlang examples of a lesson page.

The Erlang lessons keep each example in a <p class="erlangcode ..."> block
with one <code> element per source line, the same shape the ccode blocks of
the C lessons use. A shell session is a `<p class="cmd1">` with the command
(`erl`) followed by a `<p class="cout1">` with the prompts and the printed
answers. The linter reads one .php lesson and checks two things.

Source blocks:

1. A block whose first line is `-module(Name).` is a module: it is written
   to `<Name>.erl` in a temporary folder and compiled with
   `erlc +debug_info +warnings_as_errors`. A warning is an error here, so
   an unused variable or a shadowed name fails the lesson.
2. A block whose first line is a `#!` shebang is an escript: it is written
   to a temporary `.escript` file and compiled (not run) through
   `escript:extract/2` with the `compile_source` option.
3. Every other erlangcode block is skipped and counted: a bare snippet
   without a module header is not a module.

Shell sessions:

Every `cout1` block that holds `1> `, `2> ` lines is a transcript: the
prompt lines are the input, the lines between them are the output the page
claims. The inputs are replayed in one `erl` process, in page order, so
bindings, `rd` records, `b()`, `h()` and `v(N)` behave as they do in a real
session. The process reads the inputs from a file and gets a final
`halt().`, so it always ends; a session that still runs after the timeout
is reported as a failure. The output the process prints, with prompts and
the startup banner removed, must equal the output on the page. A `cout1`
that holds only the banner (`1>` with no expression after it) is counted
as a banner and not replayed.

Which blocks count, and why:

* `<p class="erlangcode ...">` is where an Erlang lesson puts source. Its
  `<code>` children are the lines; `<span>` colour markup is dropped with
  the other tags.
* `<p class="cmd1">` always holds a shell command (`erlc hello.erl`,
  `erl -noshell -eval ...`), never Erlang source, so it is not compiled.
* `<p class="cout1">` holds the output of such a command. It is read as a
  session only when it contains a `N> ` prompt.

The beams that compiled are then checked together with `dialyzer` against
a PLT of erts, kernel and stdlib. The PLT is built once per OTP release and
cached outside the worktree, in ~/.cache/erlcode-lint/otp<release>-plt
(override the folder with ERLCODE_LINT_CACHE or the file with --plt).

Exit status: 0 all blocks pass, 1 at least one warning or error, 2 the
linter could not run (missing erlc, dialyzer, or a PLT that will not build).

Usage:
    python3 erlcode_lint.py PATH [PATH ...]

    PATH: a .php lesson or a directory (recursive, `* copy*.php` and
    `sess_*.php` skipped like the other repo linters).
"""

from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.2.0"

ERL = "erl"
ERLC = "erlc"
DIALYZER = "dialyzer"
PLT_APPS = ("erts", "kernel", "stdlib")
SESSION_TIMEOUT = 10

# One <p ...> element: its class list and its inner HTML. DOTALL because a
# block spans lines; non-greedy so that the next block does not leak in.
P_BLOCK = re.compile(r'<p\b[^>]*\bclass="([^"]*)"[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)
CODE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.IGNORECASE | re.DOTALL)
TAG = re.compile(r"<[^>]+>")
MODULE = re.compile(r"^\s*-\s*module\s*\(\s*([a-z][A-Za-z0-9_@]*)\s*\)\s*\.")
SHEBANG = "#!"
BR = re.compile(r"<br\s*/?>\s*", re.IGNORECASE)
# A shell prompt: the line number, the caret, and the expression after it.
PROMPT = re.compile(r"^(\d+)>\s?(.*)$")
# The same prompt at the start of a printed line: one answer line may carry
# several continuation prompts, so the prefix is removed repeatedly.
PROMPT_PREFIX = re.compile(r"^(?:\d+>\s?)+")
BANNER = re.compile(r"^Eshell V")
TERMINATED = "*** Terminating erlang"
# A dialyzer warning starts at the left margin with a file and a position.
DIALYZER_LINE = re.compile(r"^(\S+\.erl):(\d+):(\d+):\s*(.*)$")
DIALYZER_NOISE = ("Proceeding with analysis", "Checking whether the PLT", "done in ", "done (")


class LintError(RuntimeError):
    """The linter itself cannot run: a missing tool or a broken PLT."""


@dataclass(frozen=True)
class Example:
    """One erlangcode block: where it is, what it is, and its text."""

    number: int
    kind: str  # "module", "escript" or "skip"
    name: str
    source: str


@dataclass(frozen=True)
class Diagnostic:
    """One warning or error, tied to the block that caused it."""

    block: int
    file: str
    message: str

    def render(self, lesson: str) -> str:
        """The one-line form the CLI prints."""
        return f"{lesson}: block {self.block} ({self.file}): {self.message}"


@dataclass(frozen=True)
class Session:
    """One shell transcript: what was typed and what the page says came back."""

    number: int
    inputs: tuple[str, ...]
    expected: tuple[str, ...]


@dataclass(frozen=True)
class SessionCheck:
    """The result of replaying one transcript, with a line per session."""

    session: Session
    ok: bool
    detail: str

    def render(self, lesson: str) -> str:
        """The one-line form the CLI prints."""
        state = "OK" if self.ok else "FAIL"
        return f"{lesson}: session {self.session.number}: {state} ({self.detail})"


@dataclass(frozen=True)
class LintResult:
    """What one lesson produced: its blocks, the problems, and the counts."""

    examples: list[Example]
    diagnostics: list[Diagnostic]
    sessions: list[SessionCheck]
    banners: int

    @property
    def modules(self) -> int:
        """How many blocks were compiled as modules."""
        return sum(1 for example in self.examples if example.kind == "module")

    @property
    def escripts(self) -> int:
        """How many blocks were compiled as escripts."""
        return sum(1 for example in self.examples if example.kind == "escript")

    @property
    def skipped(self) -> int:
        """How many blocks were bare snippets without a module header."""
        return sum(1 for example in self.examples if example.kind == "skip")

    @property
    def sessions_checked(self) -> int:
        """How many transcripts were replayed."""
        return len(self.sessions)

    @property
    def session_failures(self) -> int:
        """How many transcripts did not match the page."""
        return sum(1 for check in self.sessions if not check.ok)


def examples_from_php(text: str) -> list[Example]:
    """Pull the erlangcode blocks out of a lesson page, in page order."""
    examples: list[Example] = []
    for classes, body in P_BLOCK.findall(text):
        if "erlangcode" not in classes.split():
            continue
        source = _source_lines(body)
        first = next((line.strip() for line in source.splitlines() if line.strip()), "")
        number = len(examples) + 1
        # A module may open with comment lines, so the header is the first
        # line that is neither blank nor a % comment.
        form = next(
            (line.strip() for line in source.splitlines() if line.strip() and not line.strip().startswith("%")),
            "",
        )
        match = MODULE.match(form)
        if match:
            examples.append(Example(number, "module", match.group(1), source))
        elif first.startswith(SHEBANG):
            examples.append(Example(number, "escript", f"script_{number}", source))
        else:
            examples.append(Example(number, "skip", "", source))
    return examples


def _source_lines(body: str) -> str:
    """The text of a block: one <code> per line, tags and entities undone."""
    lines = [_plain(match.group(1)) for match in CODE.finditer(body)]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _plain(fragment: str) -> str:
    """The text of one line: colour spans dropped, entities decoded."""
    return html.unescape(TAG.sub("", fragment)).rstrip("\r")


def sessions_from_php(text: str) -> tuple[list[Session], int]:
    """Pull the replayable shell transcripts out of a lesson page.

    A `cout1` block with at least one prompt and an expression after it is
    a transcript. A block with a bare `1>` and nothing after it is the
    startup banner: its text is the runtime's, not the lesson's input, so
    it is counted and not replayed. Returns the sessions and the banners.
    """
    sessions: list[Session] = []
    banners = 0
    for classes, body in P_BLOCK.findall(text):
        if "cout1" not in classes.split():
            continue
        lines = _block_lines(body)
        inputs, expected = _transcript(lines)
        if not inputs:
            if any(PROMPT.match(line) for line in lines):
                banners += 1
            continue
        sessions.append(Session(len(sessions) + 1, tuple(inputs), tuple(expected)))
    return sessions, banners


def _block_lines(body: str) -> list[str]:
    """The lines of a cout1 block: <br> breaks them, tags are undone.

    The page indents the content of every block for the source layout, so
    the markup indentation is dropped; the runtime prints no indent.
    """
    body = BR.sub("\n", body)
    return [_plain(line).lstrip() for line in body.splitlines()]


def _transcript(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split the lines of a block into the typed input and the printed answer."""
    inputs: list[str] = []
    expected: list[str] = []
    for line in lines:
        prompt = PROMPT.match(line)
        if prompt:
            expression = prompt.group(2)
            if expression.strip():
                inputs.append(expression)
        else:
            expected.append(line.rstrip())
    while expected and not expected[-1]:
        expected.pop()
    while expected and not expected[0]:
        expected.pop(0)
    return inputs, expected


def lint_file(path: Path, plt: Path, session_timeout: int = SESSION_TIMEOUT) -> LintResult:
    """Compile, dialyze, and replay every Erlang example of one lesson."""
    text = path.read_text(encoding="utf-8")
    examples = examples_from_php(text)
    sessions, banners = sessions_from_php(text)
    diagnostics: list[Diagnostic] = []
    checks: list[SessionCheck] = []
    beams: list[Path] = []
    by_name: dict[str, Example] = {}
    with tempfile.TemporaryDirectory(prefix="erlcode-lint-") as tmp:
        workdir = Path(tmp)
        for example in examples:
            if example.kind == "module":
                beam, messages = _compile_module(example, workdir)
                for message in messages:
                    diagnostics.append(Diagnostic(example.number, f"{example.name}.erl", message))
                if beam is not None:
                    beams.append(beam)
                    by_name[f"{example.name}.erl"] = example
            elif example.kind == "escript":
                messages = _compile_escript(example, workdir)
                for message in messages:
                    diagnostics.append(Diagnostic(example.number, f"{example.name}.escript", message))
        if beams:
            diagnostics.extend(_dialyze(beams, by_name, plt, workdir))
        code_paths = [beam.parent for beam in beams]
        for session in sessions:
            checks.append(_replay(session, workdir, code_paths, session_timeout))
    return LintResult(examples, diagnostics, checks, banners)


def _replay(session: Session, workdir: Path, code_paths: list[Path], timeout: int) -> SessionCheck:
    """Run one transcript through erl and compare what it prints."""
    script = workdir / f"s{session.number}.in"
    lines = list(session.inputs)
    if not lines[-1].lstrip().startswith(("halt().", "q().")):
        lines.append("halt().")
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = [ERL]
    for path in code_paths:
        command.extend(["-pa", str(path)])
    try:
        with script.open("r", encoding="utf-8") as stdin:
            result = subprocess.run(
                command,
                cwd=workdir,
                stdin=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return SessionCheck(session, False, f"timed out after {timeout}s")
    except FileNotFoundError as error:
        raise LintError(f"{ERL} is not installed: {error}") from error

    actual = _actual_output((result.stdout + result.stderr).splitlines())
    if actual == list(session.expected):
        return SessionCheck(session, True, f"{len(session.inputs)} input(s) match")
    return SessionCheck(session, False, _mismatch(session.expected, actual))


def _actual_output(lines: list[str]) -> list[str]:
    """The answers erl printed: no banner, no prompts, no shutdown note."""
    start = 0
    for index, line in enumerate(lines):
        if BANNER.match(line):
            start = index + 1
            break
    printed: list[str] = []
    for line in lines[start:]:
        stripped = PROMPT_PREFIX.sub("", line).rstrip()
        if TERMINATED in stripped:
            continue
        if stripped or (printed and printed[-1]):
            printed.append(stripped)
    while printed and not printed[-1]:
        printed.pop()
    while printed and not printed[0]:
        printed.pop(0)
    return printed


def _mismatch(expected: tuple[str, ...], actual: list[str]) -> str:
    """Where the page and the runtime disagree, in one short phrase."""
    for index in range(max(len(expected), len(actual))):
        want = expected[index] if index < len(expected) else "<end of page>"
        got = actual[index] if index < len(actual) else "<end of output>"
        if want != got:
            return f"line {index + 1}: page has {want!r}, erl printed {got!r}"
    return "output differs"


def _compile_module(example: Example, workdir: Path) -> tuple[Path | None, list[str]]:
    """Run erlc on one module block; return its beam, or the messages."""
    block_dir = workdir / f"b{example.number}"
    block_dir.mkdir()
    source = block_dir / f"{example.name}.erl"
    source.write_text(example.source + "\n", encoding="utf-8")
    (block_dir / "ebin").mkdir()
    result = _run(
        [ERLC, "+debug_info", "+warnings_as_errors", "-o", "ebin", source.name],
        cwd=block_dir,
    )
    if result.returncode != 0:
        return None, _output_lines(result)
    beam = block_dir / "ebin" / f"{example.name}.beam"
    return beam, [] if beam.exists() else ["erlc reported success but wrote no beam"]


def _compile_escript(example: Example, workdir: Path) -> list[str]:
    """Compile one escript block without running it; return the messages."""
    block_dir = workdir / f"b{example.number}"
    block_dir.mkdir()
    path = block_dir / f"{example.name}.escript"
    path.write_text(example.source + "\n", encoding="utf-8")
    expression = f'case escript:extract("{path.name}", [compile_source]) of {{ok, _}} -> halt(0); {{error, Reason}} -> io:format("~p~n", [Reason]), halt(1) end.'
    result = _run([ERL, "-noshell", "-eval", expression], cwd=block_dir)
    if result.returncode == 0:
        return []
    return _output_lines(result) or ["escript did not compile"]


def _dialyze(beams: list[Path], by_name: dict[str, Example], plt: Path, workdir: Path) -> list[Diagnostic]:
    """Run dialyzer once over every beam and split its warnings per block."""
    result = _run(
        [DIALYZER, "--plt", str(plt), "--no_check_plt", *[str(beam) for beam in beams]],
        cwd=workdir,
        timeout=300,
    )
    if result.returncode == 0:
        return []

    diagnostics: list[Diagnostic] = []
    current: list[str] | None = None
    current_name = ""
    for line in _output_lines(result):
        if line.startswith(DIALYZER_NOISE):
            continue
        match = DIALYZER_LINE.match(line)
        if match:
            _flush(diagnostics, current, current_name, by_name)
            current_name = match.group(1)
            current = [line.strip()]
        elif current is not None and line.strip():
            current.append(line.strip())
    _flush(diagnostics, current, current_name, by_name)

    if not diagnostics:
        diagnostics.append(Diagnostic(0, "dialyzer", " ".join(_output_lines(result)) or "dialyzer failed"))
    return diagnostics


def _flush(
    diagnostics: list[Diagnostic],
    current: list[str] | None,
    name: str,
    by_name: dict[str, Example],
) -> None:
    """Append the warning being collected, if any, to the result."""
    if not current:
        return
    example = by_name.get(name)
    block = example.number if example else 0
    diagnostics.append(Diagnostic(block, name, " ".join(current)))
    current.clear()


def _run(command: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run a command with its output captured, never letting it hang."""
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise LintError(f"{command[0]} is not installed: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise LintError(f"{command[0]} timed out after {timeout}s") from error


def _output_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    """Non-empty lines of stdout and stderr, in the order the tool wrote them."""
    lines = (result.stdout + result.stderr).splitlines()
    return [line for line in lines if line.strip()]


def default_plt() -> Path:
    """The cached PLT of this OTP release, under ~/.cache by default."""
    cache = Path(os.environ.get("ERLCODE_LINT_CACHE", Path.home() / ".cache" / "erlcode-lint"))
    result = _run(
        [ERL, "-noshell", "-eval", 'io:format("~s", [erlang:system_info(otp_release)]), halt().'],
        cwd=cache if cache.exists() else Path(tempfile.gettempdir()),
    )
    release = result.stdout.strip()
    if result.returncode != 0 or not release:
        raise LintError("cannot read the OTP release from erl")
    return cache / f"otp{release}-plt"


def ensure_plt(plt: Path) -> None:
    """Build the erts/kernel/stdlib PLT once; reuse it afterwards."""
    if plt.exists():
        return
    plt.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        [DIALYZER, "--build_plt", "--apps", *PLT_APPS, "--output_plt", str(plt)],
        cwd=plt.parent,
        timeout=900,
    )
    # dialyzer exits 2 when it built the PLT but had warnings to report
    # (the OTP sources do produce some), so the file, not the code, is the
    # proof that the build worked.
    if not plt.exists():
        detail = " ".join(_output_lines(result))
        raise LintError(f"cannot build the PLT at {plt}: {detail}")


def _path_list(paths: list[Path]) -> list[Path]:
    """Expand directories; skip scratch copies the repo linters skip."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*.php") if _wanted(p)))
        elif _wanted(path):
            found.append(path)
        else:
            raise LintError(f"not a .php file or directory: {path}")
    return found


def _wanted(path: Path) -> bool:
    """A page the linter may read: .php, not a copy and not a session dump."""
    return path.suffix == ".php" and " copy" not in path.name and not path.name.startswith("sess_")


def main(argv: list[str] | None = None) -> int:
    """Command line entry point; see the module docstring for the options."""
    parser = argparse.ArgumentParser(
        prog="erlcode_lint.py",
        description="Compile, dialyze and replay the Erlang examples of lesson pages.",
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    parser.add_argument("--plt", type=Path, help="use this PLT instead of the cached one")
    parser.add_argument(
        "--session-timeout",
        type=int,
        default=SESSION_TIMEOUT,
        metavar="SECONDS",
        help=f"how long one shell session may run (default {SESSION_TIMEOUT})",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)

    try:
        lessons = _path_list(args.paths)
        plt = args.plt or default_plt()
        ensure_plt(plt)
    except LintError as error:
        print(f"erlcode-lint: {error}", file=sys.stderr)
        return 2

    failures = 0
    for lesson in lessons:
        try:
            result = lint_file(lesson, plt, args.session_timeout)
        except LintError as error:
            print(f"erlcode-lint: {error}", file=sys.stderr)
            return 2
        for diagnostic in result.diagnostics:
            print(diagnostic.render(str(lesson)))
        for check in result.sessions:
            print(check.render(str(lesson)))
        counts = f"{result.modules} module(s), {result.escripts} escript(s), {result.skipped} skipped block(s), {result.sessions_checked} session(s), {result.banners} banner(s)"
        problems = len(result.diagnostics) + result.session_failures
        if problems:
            failures += 1
            print(f"{lesson}: FAIL ({counts}, {problems} problem(s))")
        else:
            print(f"{lesson}: OK ({counts})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
