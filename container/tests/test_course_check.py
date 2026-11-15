# version 1.2.0
"""Tests for the course check tools: extraction, presets, matchers, docker argv.

No compiler, docker or php is needed for most of them; the tests that really
build a program skip when rustc or gcc is missing, and the quiz reader tests
skip when php is missing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "course_check"
sys.path.insert(0, str(TOOLS))

import code_blocks  # noqa: E402  pylint: disable=wrong-import-position
import course_checks as cc  # noqa: E402  pylint: disable=wrong-import-position
import quiz_answers  # noqa: E402  pylint: disable=wrong-import-position

# A page fragment as PHP writes it: <code> per line, tags and entities inside.
PAGE = '<p class="rustcode p2">\n<code><span class="c5fd7ff">fn</span> main() {</code>\n<code>    println!(&quot;it&#39;s&quot;);</code>\n<code>    let s = &quot;a\\\\b&quot;;</code>\n<code>    let t = &quot;it\\\'s&quot;;</code>\n<code>}</code>\n</p>\n'
EXPECTED = 'fn main() {\n    println!("it\'s");\n    let s = "a\\b";\n    let t = "it\'s";\n}'

PROGRAMS = {
    "rust": ("fn main() {\n}", "fn helper() {}"),
    "c": ("int main(void) {\n    return 0;\n}", "int add(int a, int b) { return a + b; }"),
    "cpp": ("int main() { return 0; }", "int add(int a) { return a; }"),
    "go": ("package main\n\nfunc main() {\n}", "func add(a int) int { return a }"),
    "haskell": ('main :: IO ()\nmain = putStrLn "hi"', "add :: Int -> Int\nadd x = x"),
    "fortran": ("program hello\nend program hello", "subroutine hello()\nend subroutine"),
    "cobol": ("       IDENTIFICATION DIVISION.\n       PROGRAM-ID. hello.", "       DISPLAY 'hi'."),
    "asm": ("global _start\n_start:\n    mov rax, 60", "section .data\nmessage: db 'hi'"),
}

CLASSES = {
    "rust": "rustcode",
    "c": "ccode",
    "cpp": "cppcode",
    "go": "gocode",
    "haskell": "haskellcode",
    "fortran": "fortrancode",
    "cobol": "cobolcode",
    "asm": "asmcode",
}


class FakeRunner:  # pylint: disable=too-few-public-methods
    """A process runner that records the call and returns a scripted output."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        """Remember what the fake process should answer."""
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Record the command line and answer like subprocess.run."""
        self.calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(list(argv), self.returncode, self.stdout, self.stderr)


class RaisingRunner(FakeRunner):  # pylint: disable=too-few-public-methods
    """A runner that fails on docker exec, as a broken job would."""

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Raise on exec; answer the start and the remove normally."""
        if list(argv[:2]) == ["docker", "exec"]:
            raise RuntimeError("boom")
        return super().__call__(argv, **kwargs)


def quiz_page(*questions: dict[str, Any]) -> quiz_answers.QuizPage:
    """A QuizPage with the given questions."""
    page = quiz_answers.QuizPage(Path("lesson.php"))
    for index, row in enumerate(questions, start=1):
        page.questions.append(
            quiz_answers.Question(
                number=index,
                q=str(row.get("q", "question")),
                options=list(row.get("options", ["a", "b", "c", "d"])),
                answer=row.get("answer", 0),
                why=str(row.get("why", "because")),
            )
        )
    return page


# ---------------------------------------------------------------- extraction


def test_extract_blocks_unescapes_entities_spans_and_php_quotes() -> None:
    """Entities, colour spans and the \\' and \\\\ of a PHP string are undone."""
    blocks = cc.extract_blocks(PAGE, "rustcode")
    assert len(blocks) == 1
    assert blocks[0].number == 1
    assert blocks[0].code == EXPECTED


def test_extract_blocks_matches_class_variants_and_skips_other_classes() -> None:
    """rustcode in any position of the class list matches; ccode does not."""
    page = '<p class="p2 rustcode">\n<code>fn main() {}</code>\n</p>\n<p class="ccode p2">\n<code>int main(void) {}</code>\n</p>\n<p class="rustcode">\n<code>fn helper() {}</code>\n</p>'
    blocks = cc.extract_blocks(page, "rustcode")
    assert [block.number for block in blocks] == [1, 2]
    assert blocks[1].code == "fn helper() {}"


def test_extract_blocks_keeps_the_lines_of_a_block() -> None:
    """Every <code> is one line, an empty one included."""
    page = '<p class="ccode">\n<code>int x;</code>\n<code></code>\n<code>int y;</code>\n</p>'
    assert cc.extract_blocks(page, "ccode")[0].code == "int x;\n\nint y;"


# ------------------------------------------------------------------ presets


@pytest.mark.parametrize("lang", sorted(cc.LANGUAGES))
def test_every_preset_matches_a_whole_program_and_not_a_fragment(lang: str) -> None:
    """The whole-program regex of each preset accepts a program, rejects a fragment."""
    program, fragment = PROGRAMS[lang]
    pattern = cc.program_pattern(cc.LANGUAGES[lang].program_regex)
    assert pattern.search(program), f"{lang}: a whole program must match"
    assert not pattern.search(fragment), f"{lang}: a fragment must not match"


@pytest.mark.parametrize("lang", sorted(cc.LANGUAGES))
def test_every_preset_has_the_class_of_the_pages(lang: str) -> None:
    """The preset names the real class of the pages and a sensible extension."""
    preset = cc.LANGUAGES[lang]
    assert preset.code_class == CLASSES[lang]
    assert preset.extension
    assert "{src}" in preset.build and "{out}" in preset.build


def test_build_command_fills_only_its_placeholders() -> None:
    """{src} and {out} are replaced; other braces stay."""
    assert cc.build_command("gcc {src} -o {out}", "a.c", "prog") == "gcc a.c -o prog"
    assert cc.build_command("x {src} {other}", "a.c", "p") == "x a.c {other}"


# -------------------------------------------------------- expected failures


def test_expected_failure_reads_the_error_line_from_the_page() -> None:
    """The first error line of rustc is found in the page text, tags and all."""
    page = cc.page_text('<p class="cout1">error[E0382]: borrow of moved value: `greeting`<br> --&gt; move_err.rs:4:16</p>')
    assert cc.expected_failure("error[E0382]: borrow of moved value: `greeting`", page)
    assert not cc.expected_failure("error[E0308]: mismatched types", page)


def test_expected_failure_ignores_the_extracted_file_name() -> None:
    """A gcc error is compared from 'error' on, so the temp file name does not matter."""
    page = cc.page_text("<p>bad.c:4:5: error: expected ';' before '}' token</p>")
    assert cc.expected_failure("page_block1.c:4:5: error: expected ';' before '}' token", page)
    assert not cc.expected_failure("page_block1.c:1:1: error: unknown type name 'x'", page)


def test_page_text_unescapes_the_php_quotes_of_a_page() -> None:
    """A diagnostic shown in a PHP single-quoted string loses its backslashes."""
    page = cc.page_text("<p>nosemi.c:5:34: error: expected \\';\\' before \\'return\\'</p>")
    assert "expected ';' before 'return'" in page


def test_stated_build_uses_the_flags_of_the_question() -> None:
    """A question that names its own compiler command is built with those flags."""
    command = cc.stated_build("c", ["gcc -std=c17 -O2 opt.c -o opt"], "gcc {src} -o {out}", "c")
    assert command == "gcc -std=c17 -O2 {src} -o {out}"


def test_stated_build_appends_the_output_name() -> None:
    """A command without -o gets the output name of the tool."""
    command = cc.stated_build("rust", ["rustc --edition 2021 x.rs"], "rustc {src} -o {out}", "rs")
    assert command == "rustc --edition 2021 {src} -o {out}"


def test_stated_build_ignores_compile_only_and_other_tools() -> None:
    """-c, another tool and a preset without a single compiler keep the default."""
    default = "gcc -Wall -Wextra {src} -o {out}"
    assert cc.stated_build("c", ["gcc -c util.c"], default, "c") == default
    assert cc.stated_build("c", ["make"], default, "c") == default
    assert cc.stated_build("go", ["go run main.go"], "go build -o {out} {src}", "go") == "go build -o {out} {src}"


def test_stated_run_reads_the_arguments_of_a_question() -> None:
    """'run as `./args one two`' passes the two words to the program."""
    assert cc.stated_run(["./args one two"]) == ("./args", ("one", "two"))
    assert cc.stated_run(["g++ -std=c++23 args.cpp -o args", "./args one two"]) == ("./args", ("one", "two"))
    assert cc.stated_run(["./options --name=ada --count=3"]) == ("./options", ("--name=ada", "--count=3"))
    assert cc.stated_run(["./prog"]) == ("./prog", ())
    assert cc.stated_run(["gcc x.c -o x"]) == ("", ())


def test_stated_run_ignores_a_command_that_redirects_or_pipes() -> None:
    """The tool cannot provide a file or a pipe of the shell, so it keeps the default."""
    assert cc.stated_run(["./prog > out.txt"]) == ("", ())
    assert cc.stated_run(["./prog < in.txt"]) == ("", ())
    assert cc.stated_run(["echo 5 | ./prog"]) == ("", ())


def test_build_script_runs_with_the_stated_name_and_arguments() -> None:
    """The program is reachable under the name the question uses."""
    script = cc.build_script(
        "g++ {src} -o {out}",
        "./prog",
        run=True,
        timeout=5,
        limit=100,
        build_log="./.build.log",
        run_log="./.run.log",
        error_log="./.error.log",
        run_name="./args",
        arguments=("one", "two"),
    )
    assert "ln -sf ./prog ./args" in script
    assert "timeout 5 ./args one two >./.run.log" in script


def test_first_error_line_skips_warnings_and_hash_lines() -> None:
    """The diagnostic line is picked, not the warning or the go package header."""
    output = "warning: unused variable: `x`\n# command-line-arguments\n./x.go:4:2: undefined: y\n"
    assert cc.first_error_line(output) == "./x.go:4:2: undefined: y"
    assert cc.error_core("./x.go:4:2: undefined: y") == "undefined: y"


def test_first_error_line_takes_the_error_not_the_context_line() -> None:
    """g++ puts 'required from here' and warnings before a template error."""
    output = "In file included from page_block1.cpp:2:\npage_block1.cpp: In instantiation of 'void show(T) [with T = int]':\npage_block1.cpp:10:5:   required from here\npage_block1.cpp:5:10: warning: unused variable 'x' [-Wunused-variable]\npage_block1.cpp:5:10: error: no matching function for call to 'hide(int&)'\npage_block1.cpp:12:5:   recursively required from here\n"
    assert cc.first_error_line(output) == "page_block1.cpp:5:10: error: no matching function for call to 'hide(int&)'"


def test_first_error_line_keeps_a_language_without_the_word_error() -> None:
    """Go has no 'error:' in its diagnostics, so the first diagnostic stands."""
    output = "./main.go:5:2: undefined: total\n./main.go:6:2: undefined: count\n"
    assert cc.first_error_line(output) == "./main.go:5:2: undefined: total"


@pytest.mark.parametrize("lang", ["c", "cpp"])
def test_c_preset_needs_a_body_for_a_whole_program(lang: str) -> None:
    """A mention of 'int main(void)' in a question is not a program to build."""
    pattern = cc.program_pattern(cc.LANGUAGES[lang].program_regex)
    assert pattern.search("int main(void)\n{\n    return 0;\n}")
    assert pattern.search("int main(int argc, char *argv[]) { return 0; }")
    assert not pattern.search("int main(void)")
    assert not pattern.search("int main(int argc, char **argv)")


# --------------------------------------------------------------------- skip


def test_skip_reason_for_blocks_that_cannot_be_built_alone() -> None:
    """A crate, a missing include and a module of another file are skips."""
    assert cc.skip_reason("error[E0432]: unresolved import `crate::util`")
    assert cc.skip_reason("error[E0583]: file not found for module `util`")
    assert cc.skip_reason("main.c:1:10: fatal error: helper.h: No such file or directory")
    assert cc.skip_reason('cannot find package "example.com/x"')
    assert cc.skip_reason("error: environment variable `APP_VERSION` not defined at compile time")
    assert cc.skip_reason("lib.c:1:1: error: expected ';'") == ""


def test_unbuildable_reason_reads_the_source_not_the_error(tmp_path: Path) -> None:
    """A quoted include of a missing file and a C++ module import are skips."""
    code = '#include <iostream>\n#include "math.h"\n\nint main() { return square(2); }\n'
    assert "math.h" in cc.unbuildable_reason(code, tmp_path)
    (tmp_path / "math.h").write_text("int square(int);\n", encoding="utf-8")
    assert cc.unbuildable_reason(code, tmp_path) == ""
    module = "import math;\n\nint main() { return 0; }\n"
    assert "module" in cc.unbuildable_reason(module, tmp_path)
    assert cc.unbuildable_reason("#include <cmath>\nint main() { return 0; }\n", tmp_path) == ""


def test_local_session_marks_a_skip() -> None:
    """A failed build with a crate error comes back as compile_error plus a skip."""
    fake = FakeRunner("@@BUILD_FAIL\nerror[E0432]: unresolved import `serde`\n")
    session = cc.open_session(root=Path("."), process_runner=fake)
    job = cc.BuildJob(Path("."), "x.rs", "rustc {src} -o {out}")
    result = session.run(job, run=True, timeout=5, output_limit=100)
    assert result.status == "compile_error"
    assert "unresolved import" in result.skip


# ------------------------------------------------------------- option match


@pytest.mark.parametrize(
    ("option", "output"),
    [
        ("size: 5", "size: 5\n"),
        ("120", "120"),
        ("inner / outer", "inner\nouter\n"),
        ("steps: 5 / capped: 100", "steps: 5\ncapped: 100"),
        ("a\nb", "a\nb"),
    ],
)
def test_option_matcher_accepts_the_output(option: str, output: str) -> None:
    """Exact text, ' / ' for newlines and a multi-line option all match."""
    assert cc.matches_option(option, output)


def test_option_matcher_loose_form() -> None:
    """Punctuation and filler words do not stop a match."""
    assert cc.matches_option("steps: 5 (capped: 100)", "steps: 5 capped: 100")
    assert cc.matches_option("The answer is: 42", "the answer is 42")


def test_option_matcher_rejects_another_answer() -> None:
    """A wrong option and unrelated text do not match."""
    assert not cc.matches_option("size: 0", "size: 5")
    assert not cc.matches_option("size: 5", "size: 8")
    assert not cc.matches_option("something else entirely", "42")
    assert not cc.matches_option("120", "24")


def test_compile_error_matcher() -> None:
    """The marked option may carry the error code or only its message."""
    error = "error[E0382]: borrow of moved value: `greeting`"
    assert cc.matches_error("error[E0382]: borrow of moved value: `greeting`", error)
    assert cc.matches_error("borrow of moved value: `greeting`", error)
    assert not cc.matches_error("error[E0308]: mismatched types", error)
    assert cc.matches_error("expected ';' before '}' token", "x.c:4:5: error: expected ';' before '}' token")


# ---------------------------------------------------- warnings and diagnostics


def test_message_core_strips_the_category_and_the_sanitizer() -> None:
    """The message of a diagnostic is compared without 'warning:' or 'ERROR:'."""
    assert cc.message_core("x.c:5:19: warning: multi-character constant [-Wmultichar]") == "multi-character constant [-Wmultichar]"
    assert cc.message_core("warning: return type of 'main' is not 'int' [-Wmain]") == "return type of 'main' is not 'int' [-Wmain]"
    assert cc.message_core("==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x1") == "heap-buffer-overflow on address 0x1"
    assert cc.message_core("x.c:4:41: runtime error: signed integer overflow: 1 + 1") == "signed integer overflow: 1 + 1"
    assert cc.message_core("7") == "7"


def test_matches_diagnostic_reads_the_warning_and_its_flag() -> None:
    """A warning line, its message alone and its -W flag are all answers."""
    warning = "x.c:5:19: warning: multi-character character constant [-Wmultichar]"
    assert cc.matches_diagnostic("warning: multi-character character constant [-Wmultichar]", [warning])
    assert cc.matches_diagnostic("multi-character character constant [-Wmultichar]", [warning])
    assert cc.matches_diagnostic("-Wmultichar", [warning])
    assert cc.matches_diagnostic("heap-buffer-overflow", ["==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x1"])


def test_matches_diagnostic_rejects_another_warning_and_a_short_option() -> None:
    """Another flag, another message and a fragment shorter than four letters do not match."""
    warning = "x.c:5:19: warning: multi-character character constant [-Wmultichar]"
    assert not cc.matches_diagnostic("-Wformat=", [warning])
    assert not cc.matches_diagnostic("unused variable 'x'", [warning])
    assert not cc.matches_diagnostic("x", [warning])


def test_diagnostic_candidates_keep_build_and_run_lines() -> None:
    """The warnings of a build and the stderr of a run are both answers."""
    candidates = cc.diagnostic_candidates(
        "x.c:3:6: warning: return type of 'main' is not 'int' [-Wmain]",
        "\nDirect leak of 4 byte(s) in 1 object(s) allocated from:\n",
    )
    assert cc.matches_diagnostic("return type of 'main' is not 'int' [-Wmain]", candidates)
    assert cc.matches_diagnostic("Direct leak of 4 byte(s) in 1 object(s)", candidates)


# -------------------------------------------------------------- exit status


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        ("about to leave / exit status: 3", ("about to leave", 3)),
        ("7 / status: 0", ("7", 0)),
        ("exit: 139", ("", 139)),
        ("status: 1", ("", 1)),
        ("steps: 5 / capped: 100", ("steps: 5 / capped: 100", None)),
        ("code: 1", ("code: 1", None)),
        ("134", ("134", None)),
    ],
)
def test_split_status(option: str, expected: tuple[str, int | None]) -> None:
    """The last part of an option may name the exit status, or not."""
    assert cc.split_status(option) == expected


def test_asks_exit_status() -> None:
    """Only a question about the exit status reads a bare number as one."""
    assert cc.asks_exit_status("What exit status does the shell report for the code shown?")
    assert cc.asks_exit_status("Which exit code comes back?")
    assert not cc.asks_exit_status("What does the shell print?")


def test_quiz_matches_the_exit_status_of_the_option() -> None:
    """'about to leave / exit status: 3' needs both the line and the status."""
    result = cc.ProgramResult(status="built", output="about to leave", exit_code=3)
    assert quiz_answers.output_matches("about to leave / exit status: 3", result)
    assert not quiz_answers.output_matches("about to leave / exit status: 0", result)
    assert not quiz_answers.output_matches("exit status: 3", result)
    assert not quiz_answers.output_matches("about to leave / exit status: 255", result)


def test_quiz_matches_a_status_only_option_for_a_silent_program() -> None:
    """A program that prints nothing is described by its status alone."""
    crashed = cc.ProgramResult(status="built", output="", exit_code=139)
    assert quiz_answers.output_matches("exit: 139", crashed)
    assert not quiz_answers.output_matches("exit: 134", crashed)


def test_quiz_matches_a_bare_number_when_the_question_asks_for_the_status() -> None:
    """'What exit status does the shell report?' makes 134 the answer, not output."""
    aborted = cc.ProgramResult(status="built", output="first check passed", exit_code=134)
    assert quiz_answers.output_matches("134", aborted, "What exit status does the shell report?")
    assert not quiz_answers.output_matches("0", aborted, "What exit status does the shell report?")
    assert not quiz_answers.output_matches("134", aborted, "What does the program print?")


def test_quiz_keeps_an_output_that_ends_with_the_word_status() -> None:
    """A line of output may itself say 'status: 0'; the whole option still matches."""
    result = cc.ProgramResult(status="built", output="last: 3 9 status: 0", exit_code=0)
    assert quiz_answers.output_matches("last: 3 9 / status: 0", result)


# -------------------------------------------------------------------- REFERS


def test_refers_when_a_question_points_outside_itself() -> None:
    """A phrase of the constant list is a REFERS when the question carries no code."""
    assert quiz_answers.refer_phrase("What does the program above print?", [])
    assert quiz_answers.refer_phrase("What does the example do?", [])
    assert quiz_answers.refer_phrase("In the section we built a parser. Why?", [])
    assert quiz_answers.refer_phrase("Read musl.rs and say what it links", []) == "musl.rs"


def test_refers_ignores_a_file_the_question_only_names() -> None:
    """A header in <...> or a hypothetical name is not a pointer to the page."""
    assert quiz_answers.refer_phrase("What does including <windows.h> produce?", []) == ""
    assert quiz_answers.refer_phrase("A consumer includes square.h. What happens?", []) == ""
    assert quiz_answers.refer_phrase("What does the program in `musl.rs` print?", []) == "musl.rs"


def test_refers_is_off_when_the_question_carries_its_code() -> None:
    """A question with the program in it is self-contained."""
    code = 'fn main() {\n    println!("hi");\n}'
    assert quiz_answers.refer_phrase("What does this program print?", [code]) == ""
    assert quiz_answers.refer_phrase("What does `let x = 1;` do?", ["let x = 1;"]) == ""


def test_code_snippets_reads_fences_and_inline_code() -> None:
    """Both ``` blocks and `spans`, also a span that holds several lines."""
    text = "What does this print?\n```\nfn main() {}\n```\nand `x = 1` or\n`int main() {\n}`"
    snippets = quiz_answers.code_snippets(text)
    assert "fn main() {}" in snippets
    assert "x = 1" in snippets
    assert any(snippet.startswith("int main()") for snippet in snippets)


# ---------------------------------------------------------------- structure


def test_structure_checks_every_rule() -> None:
    """Count, option count, answer range, duplicate options and an empty why."""
    page = quiz_page(
        {"answer": 0},
        {"options": ["a", "b", "c"], "answer": 0},
        {"answer": 9},
        {"options": ["a", "a", "b", "c"], "answer": 0},
        {"why": ""},
    )
    problems = quiz_answers.structure_problems(page, expected_count=4, expected_options=4)
    joined = "\n".join(problems)
    assert "5 questions, expected 4" in joined
    assert "3 options, expected 4" in joined
    assert "answer 9 is not an option index" in joined
    assert "two options are equal" in joined
    assert "why is empty" in joined


def test_structure_checks_can_be_turned_off() -> None:
    """--count 0 and --options 0 only keep the per-question rules."""
    page = quiz_page({"options": ["a", "b", "c"], "answer": 0}, {"answer": None})
    problems = quiz_answers.structure_problems(page, expected_count=0, expected_options=0)
    assert problems == ["lesson.php q2: answer None is not an option index"]


# ------------------------------------------------------------------- docker


def test_docker_start_argv_without_network(tmp_path: Path) -> None:
    """One container for the run: no capabilities, no network, read-only sources."""
    argv = cc.docker_start_argv("opencode-agent-native", tmp_path, "course-check-1")
    assert argv == [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        "course-check-1",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--network",
        "none",
        "-v",
        f"{tmp_path}:/src:ro",
        "--tmpfs",
        "/tmp:rw,exec,mode=1777",
        "--entrypoint",
        "bash",
        "opencode-agent-native",
        "-c",
        "sleep infinity",
    ]


def test_docker_start_argv_with_network(tmp_path: Path) -> None:
    """--network leaves docker's default network in place."""
    argv = cc.docker_start_argv("img", tmp_path, "n", network=True)
    assert "--network" not in argv
    assert argv[-4:] == ["bash", "img", "-c", "sleep infinity"]


def test_local_session_builds_in_the_job_folder(tmp_path: Path) -> None:
    """Local mode runs bash in the job folder with the compilers of PATH."""
    (tmp_path / "x.rs").write_text("fn main() {}\n", encoding="utf-8")
    fake = FakeRunner("@@BUILD_OK\nhello\n@@RC 0\n")
    session = cc.open_session(root=tmp_path, process_runner=fake)
    job = cc.BuildJob(tmp_path, "x.rs", "rustc {src} -o {out}")
    result = session.run(job, run=True, timeout=5, output_limit=100)
    session.close()
    assert result.status == "built"
    assert result.output == "hello"
    assert result.exit_code == 0
    argv, kwargs = fake.calls[0]
    assert argv[:2] == ["bash", "-c"]
    assert "rustc x.rs -o ./prog" in argv[2]
    assert kwargs["cwd"] == tmp_path


def test_docker_session_starts_execs_and_removes(tmp_path: Path) -> None:
    """One container is started, each job is exec'd in it, then it is removed."""
    fake = FakeRunner("@@BUILD_OK\nhi\n@@RC 0\n")
    session = cc.open_session(root=tmp_path, docker_image="opencode-agent-native", process_runner=fake)
    job = cc.BuildJob(tmp_path / "j1", "x.rs", "rustc {src} -o {out}")
    result = session.run(job, run=True, timeout=5, output_limit=100)
    session.close()
    start, execute, remove = (call[0] for call in fake.calls)
    name = start[start.index("--name") + 1]
    assert start[:4] == ["docker", "run", "-d", "--rm"]
    assert name.startswith("course-check-")
    assert execute[:3] == ["docker", "exec", name]
    script = execute[-1]
    assert "cp /src/j1/x.rs /tmp/j1/" in script
    assert "cd /tmp/j1" in script
    assert "cd /src" not in script
    assert "rustc x.rs -o ./prog" in script
    assert "timeout 5 ./prog" in script
    assert remove == ["docker", "rm", "-f", name]
    assert result.status == "built"
    assert result.output == "hi"


def test_docker_session_builds_and_runs_in_a_writable_folder(tmp_path: Path) -> None:
    """The build and the run happen in /tmp/<job>, not on the read-only mount."""
    fake = FakeRunner("@@BUILD_OK\n@@RC 0\n")
    session = cc.open_session(root=tmp_path, docker_image="img", process_runner=fake)
    job = cc.BuildJob(tmp_path / "j1", "x.rs", "rustc {src} -o {out}")
    session.run(job, run=True, timeout=5, output_limit=100)
    session.close()
    script = fake.calls[1][0][-1]
    assert script.startswith("mkdir -p /tmp/j1\ncp /src/j1/x.rs /tmp/j1/\ncd /tmp/j1\n")
    assert script.index("cd /tmp/j1") < script.index("rustc x.rs -o ./prog") < script.index("timeout 5 ./prog")
    assert "cd /src" not in script


def test_docker_session_removes_the_container_when_a_program_raises(tmp_path: Path) -> None:
    """An error inside a job still removes the container (try/finally)."""
    fake = RaisingRunner()
    session = cc.open_session(root=tmp_path, docker_image="img", process_runner=fake)
    job = cc.BuildJob(tmp_path / "j1", "x.rs", "rustc {src} -o {out}")
    with pytest.raises(RuntimeError, match="boom"), session:
        session.run(job, run=True, timeout=5, output_limit=100)
    assert fake.calls[-1][0][:3] == ["docker", "rm", "-f"]


def test_run_jobs_keeps_the_job_order(tmp_path: Path) -> None:
    """The slowest job is first in the report; results follow the job order."""
    delays = {"a.rs": 0.3, "b.rs": 0.2, "c.rs": 0.01}

    class SlowSession(cc.Session):
        """A session whose jobs finish in reverse order."""

        def run(self, job: cc.BuildJob, *, run: bool, timeout: float, output_limit: int) -> cc.ProgramResult:
            time.sleep(delays[job.source_name])
            return cc.ProgramResult(status="built", output=job.source_name)

    jobs = [cc.BuildJob(tmp_path, name, "true") for name in ("a.rs", "b.rs", "c.rs")]
    results = cc.run_jobs(SlowSession(), jobs, workers=3)
    assert [result.output for result in results] == ["a.rs", "b.rs", "c.rs"]


def test_parse_script_output_reads_exit_code_and_timeout() -> None:
    """The rc marker becomes the exit status, 124 is a timeout."""
    assert cc.parse_script_output("@@BUILD_OK\nhi\n@@RC 3\n").exit_code == 3
    timed = cc.parse_script_output("@@BUILD_OK\n@@RC 124\n")
    assert timed.timed_out and timed.exit_code == 124


def test_parse_script_output_keeps_stderr_apart() -> None:
    """stdout and stderr are separated, so a panic does not spoil the output."""
    result = cc.parse_script_output("@@BUILD_OK\nhi\n@@ERR\nthread 'main' panicked\n@@RC 0\n")
    assert result.output == "hi"
    assert "panicked" in result.error_output


def test_build_script_reports_the_warnings_of_a_good_build() -> None:
    """The build log of a successful build is printed after the exit status."""
    script = cc.build_script(
        "gcc {src} -o {out}",
        "./prog",
        run=True,
        timeout=5,
        limit=100,
        build_log="./.build.log",
        run_log="./.run.log",
        error_log="./.error.log",
    )
    assert "if [ -s ./.build.log ]; then printf '@@DIAG\\n'; cat ./.build.log; fi" in script
    assert script.index("@@RC") < script.index("@@DIAG")


def test_parse_script_output_keeps_the_warnings_of_a_build() -> None:
    """The warnings do not spoil the output and are kept in diagnostics."""
    result = cc.parse_script_output("@@BUILD_OK\n7\n@@ERR\n\n@@RC 0\n@@DIAG\nx.c:3:6: warning: return type of 'main' is not 'int' [-Wmain]\n")
    assert result.output == "7"
    assert result.exit_code == 0
    assert "return type of 'main' is not 'int' [-Wmain]" in result.diagnostics


def test_local_session_reports_a_warning_of_a_build_without_a_run() -> None:
    """A warning comes back as a diagnostic even when the program is not run."""
    fake = FakeRunner("@@BUILD_OK\n@@DIAG\nx.c:3:6: warning: unused variable 'x' [-Wunused-variable]\n")
    session = cc.open_session(root=Path("."), process_runner=fake)
    job = cc.BuildJob(Path("."), "x.c", "gcc {src} -o {out}")
    result = session.run(job, run=False, timeout=5, output_limit=100)
    assert result.status == "built"
    assert result.output == ""
    assert "unused variable 'x'" in result.diagnostics


def test_quiz_matches_the_output_with_stderr_as_a_fallback() -> None:
    """A question that asks for stdout still matches when the program warns on stderr."""
    result = cc.ProgramResult(status="built", output="true", error_output="thread 'main' panicked")
    assert quiz_answers.output_matches("true", result)
    assert not quiz_answers.output_matches("false", result)


def test_quiz_matches_a_warning_of_the_build() -> None:
    """A warning question is proved by the warning the compiler printed."""
    result = cc.ProgramResult(
        status="built",
        output="hello",
        exit_code=0,
        diagnostics="x.c:3:6: warning: return type of 'main' is not 'int' [-Wmain]\n",
    )
    assert quiz_answers.output_matches("warning: return type of 'main' is not 'int' [-Wmain]", result)
    assert quiz_answers.output_matches("-Wmain", result)
    assert quiz_answers.output_matches("hello", result)
    assert not quiz_answers.output_matches("warning: unused variable 'x' [-Wunused-variable]", result)


def test_page_expects_failure() -> None:
    """A panic or a SIGSEGV on the page excuses a crash; a silent one does not."""
    crashed = cc.ProgramResult(status="built", output="", exit_code=101)
    assert cc.page_expects_failure(cc.page_text("<p>thread 'main' panicked</p>"), crashed)
    assert not cc.page_expects_failure(cc.page_text("<p>all good</p>"), crashed)
    assert cc.page_expects_failure(cc.page_text("<p>exit status 3</p>"), cc.ProgramResult("built", exit_code=3))


# --------------------------------------------------------------- real builds


@pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc is not in PATH")
def test_real_rust_program_builds_and_runs(tmp_path: Path) -> None:
    """A real rustc build of a real program, with the preset command."""
    (tmp_path / "hello.rs").write_text('fn main() {\n    println!("hello");\n}\n', encoding="utf-8")
    session = cc.open_session(root=tmp_path)
    job = cc.BuildJob(tmp_path, "hello.rs", cc.LANGUAGES["rust"].build)
    result = session.run(job, run=True, timeout=10, output_limit=2000)
    assert result.status == "built", result.build_output
    assert result.exit_code == 0
    assert result.output.strip() == "hello"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not in PATH")
def test_real_c_program_builds_and_runs(tmp_path: Path) -> None:
    """A real gcc build of a real program, with the preset command."""
    (tmp_path / "hello.c").write_text(
        '#include <stdio.h>\n\nint main(void)\n{\n    printf("hello\\n");\n    return 0;\n}\n',
        encoding="utf-8",
    )
    session = cc.open_session(root=tmp_path)
    job = cc.BuildJob(tmp_path, "hello.c", cc.LANGUAGES["c"].build)
    result = session.run(job, run=True, timeout=10, output_limit=2000)
    assert result.status == "built", result.build_output
    assert result.exit_code == 0
    assert result.output.strip() == "hello"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not in PATH")
def test_real_c_program_runs_with_the_stated_name_and_arguments(tmp_path: Path) -> None:
    """The program sees the name and the arguments the question states."""
    (tmp_path / "args.c").write_text(
        '#include <stdio.h>\n\nint main(int argc, char **argv)\n{\n    printf("%s %d\\n", argv[0], argc - 1);\n    return 0;\n}\n',
        encoding="utf-8",
    )
    session = cc.open_session(root=tmp_path)
    job = cc.BuildJob(tmp_path, "args.c", cc.LANGUAGES["c"].build, run_name="./args", arguments=("one", "two"))
    result = session.run(job, run=True, timeout=10, output_limit=2000)
    assert result.status == "built", result.build_output
    assert result.output.strip() == "./args 2"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not in PATH")
def test_real_c_build_keeps_the_warning_of_a_program_that_runs(tmp_path: Path) -> None:
    """A build that succeeds still reports its warnings, so a warning question can be proved."""
    (tmp_path / "voidmain.c").write_text(
        '#include <stdio.h>\n\nvoid main(void)\n{\n    printf("hello\\n");\n}\n',
        encoding="utf-8",
    )
    session = cc.open_session(root=tmp_path)
    job = cc.BuildJob(tmp_path, "voidmain.c", "gcc -std=c17 -Wall -Wextra {src} -o {out}")
    result = session.run(job, run=True, timeout=10, output_limit=2000)
    assert result.status == "built", result.build_output
    assert result.output.strip() == "hello"
    assert "warning: return type of 'main' is not 'int' [-Wmain]" in result.diagnostics


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not in PATH")
def test_code_blocks_reports_a_failure_the_page_does_not_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A broken block that the page does not explain is a FAIL and exit status 1."""
    page = tmp_path / "lesson.php"
    page.write_text(
        '<p class="ccode p2">\n<code>int main(void) {</code>\n<code>    return 0;</code>\n</p>\n',
        encoding="utf-8",
    )
    status = code_blocks.main([str(page), "--lang", "c"])
    out = capsys.readouterr().out
    assert status == 1
    assert "FAIL" in out
    assert "blocks=1 whole=1 built=0 expected=0 skipped=0 findings=1" in out


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not in PATH")
def test_code_blocks_keeps_block_order_with_parallel_jobs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With --jobs 3 the three failures are still printed in block order."""
    page = tmp_path / "lesson.php"
    blocks = "".join(f'<p class="ccode p2">\n<code>int main(void) {{ return {number} }}</code>\n</p>\n' for number in range(3))
    page.write_text(blocks, encoding="utf-8")
    status = code_blocks.main([str(page), "--lang", "c", "--jobs", "3"])
    lines = capsys.readouterr().out.splitlines()
    assert status == 1
    numbers = [line.split(" block ")[1].split(":")[0] for line in lines if line.startswith("FAIL")]
    assert numbers == ["1", "2", "3"]


def test_jobs_must_be_at_least_one(tmp_path: Path) -> None:
    """--jobs 0 is a usage error, exit status 2."""
    page = tmp_path / "x.php"
    page.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        code_blocks.main([str(page), "--jobs", "0"])
    assert error.value.code == 2


# -------------------------------------------------------------------- reader

PHP_PAGE = """<?php
echo LessonQuiz::render(array(
    array(
        'q' => 'What does `f();` do? A string with ); and , \\'en\\'); inside',
        'options' => array('one', 'two', 'three', 'four'),
        'answer' => 2,
        'why' => 'Because the tokenizer reads the array, not the rest of the page.',
    ),
    array(
        'q' => 'Second question?',
        'options' => array('a', 'b', 'c', 'd'),
        'answer' => 0,
        'why' => 'A reason.',
    ),
), 'en');
echo $page->createFooter();
"""


@pytest.mark.skipif(shutil.which("php") is None, reason="php is not in PATH")
def test_read_quiz_does_not_execute_the_page(tmp_path: Path) -> None:
    """The array is read by token_get_all, even with ); , 'en'); inside a question."""
    page = tmp_path / "lesson.php"
    page.write_text(PHP_PAGE, encoding="utf-8")
    pages, errors = quiz_answers.read_quiz([page])
    assert not errors
    assert len(pages) == 1
    assert [question.number for question in pages[0].questions] == [1, 2]
    assert pages[0].questions[0].options == ["one", "two", "three", "four"]
    assert pages[0].questions[0].answer == 2
    assert "tokenizer" in pages[0].questions[0].why


@pytest.mark.skipif(shutil.which("php") is None, reason="php is not in PATH")
def test_read_quiz_ignores_a_page_without_a_quiz(tmp_path: Path) -> None:
    """A page without LessonQuiz::render() has no questions and no error."""
    page = tmp_path / "plain.php"
    page.write_text("<?php\necho 'hello';\n", encoding="utf-8")
    pages, errors = quiz_answers.read_quiz([page])
    assert not pages and not errors


@pytest.mark.skipif(shutil.which("php") is None, reason="php is not in PATH")
def test_quiz_main_checks_a_page(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A page with ten right questions passes the structure and answer checks."""
    page = tmp_path / "lesson.php"
    rows = "".join(f"    array('q' => 'Question {number}?', 'options' => array('a', 'b', 'c', 'd'), 'answer' => 0, 'why' => 'Because.'),\n" for number in range(1, 11))
    page.write_text(f"<?php\necho LessonQuiz::render(array(\n{rows}), 'en');\n", encoding="utf-8")
    status = quiz_answers.main([str(page), "--lang", "c"])
    out = capsys.readouterr().out
    assert status == 0
    assert "pages=1 questions=10 programs=0 build_errors=0 wrong=0 unmatched=0 refers=0 structure=0" in out
