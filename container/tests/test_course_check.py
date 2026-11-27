# version 1.9.1
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


def node_major_version() -> int | None:
    """The major version of the node in PATH, or None when node is not there."""
    if shutil.which("node") is None:
        return None
    process = subprocess.run(
        ["node", "-p", "process.versions.node"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(process.stdout.strip().split(".")[0])
    except ValueError:
        return None


NODE_MAJOR = node_major_version()
# Node 22 detects ES module syntax in a plain .js file without a package.json;
# Node 18 reads that file as CommonJS and fails on the first import.
ESM_REASON = "node 22 or later is needed for ES module syntax in a .js file"

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
    "javascript": (
        "'use strict';\n\nconst items = [1, 2, 3];\nconsole.log(items.map((n) => n * 2).join(', '));",
        "items.push(4);",
    ),
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
    "javascript": "jscode",
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


def test_extract_blocks_keeps_a_backslash_the_code_needs() -> None:
    """A PHP-literal backslash before an entity quote is a JavaScript escape."""
    page = '<p class="jscode">\n<code>console.log(&#39;it\\&#39;s here&#39;);</code>\n</p>'
    assert cc.extract_blocks(page, "jscode")[0].code == "console.log('it\\'s here');"


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
    assert "{src}" in preset.build
    # A compiled language writes {out}; an interpreted one runs the source.
    assert "{out}" in preset.build or "{src}" in preset.run


def test_build_command_fills_only_its_placeholders() -> None:
    """{src} and {out} are replaced; other braces stay."""
    assert cc.build_command("gcc {src} -o {out}", "a.c", "prog") == "gcc a.c -o prog"
    assert cc.build_command("x {src} {other}", "a.c", "p") == "x a.c {other}"


def test_javascript_preset_checks_and_runs_its_source() -> None:
    """A .js file is checked by node --check and run by node, not as ./prog."""
    preset = cc.LANGUAGES["javascript"]
    build = cc.build_command(preset.build, "page_block1.js", "./prog")
    run_command = cc.build_command(preset.run, "page_block1.js", "./prog")
    assert build == "node --check page_block1.js"
    assert run_command == "node page_block1.js"
    script = cc.build_script(
        build,
        "./prog",
        run=True,
        timeout=5,
        limit=100,
        build_log="./.build.log",
        run_log="./.run.log",
        error_log="./.error.log",
        run_command=run_command,
    )
    assert "node --check page_block1.js >./.build.log" in script
    assert "timeout 5 node page_block1.js </dev/null >./.run.log" in script
    assert "./prog" not in script


def test_javascript_program_regex_wants_a_top_level_console_call() -> None:
    """A program prints; a fragment out of a body or a module file does not match."""
    pattern = cc.program_pattern(cc.LANGUAGES["javascript"].program_regex)
    assert pattern.search("console.log('hello');")
    assert pattern.search("const x = 1;\nconsole.table([x]);")
    assert not pattern.search("if (ready) {\n  items.push(4);\n}")
    assert not pattern.search("export const double = (n) => n * 2;")
    assert not pattern.search("items.push(4);")


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


def test_stated_run_reads_a_node_command() -> None:
    """'run as `node count.js one two three`' passes three words; the name keeps its extension."""
    assert cc.stated_run(["node count.js one two three"]) == ("count.js", ("one", "two", "three"))
    assert cc.stated_run(["node hello.mjs"]) == ("hello.mjs", ())
    assert cc.stated_run(["node app.js 2>/dev/null"]) == ("", ())
    assert cc.stated_run(["node --version"]) == ("", ())


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
    assert "timeout 5 ./args one two </dev/null >./.run.log" in script


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


def test_first_error_line_reads_node_before_the_source_line() -> None:
    """A source line that says console.error is not the error of a JavaScript build."""
    output = "/tmp/x.js:2\nconsole.error('x'\n             ^\n\nSyntaxError: missing ) after argument list\n"
    assert cc.first_error_line(output) == "SyntaxError: missing ) after argument list"


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


def test_skip_reason_reads_a_javascript_package_that_is_missing() -> None:
    """A require of jsdom and an ESM import of it are skips; a syntax error is not."""
    assert cc.skip_reason("Error: Cannot find module 'jsdom'")
    assert cc.skip_reason("Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'jsdom' imported from /tmp/x.js")
    assert cc.skip_reason("Error: Cannot find module './task'")
    assert cc.skip_reason("SyntaxError: Unexpected token ';'") == ""
    assert cc.skip_reason("ReferenceError: document is not defined") == ""


def test_code_blocks_skips_a_javascript_program_whose_package_is_missing(capsys: pytest.CaptureFixture[str]) -> None:
    """A program that built but could not start without jsdom is a SKIP, not a RUN."""
    totals = {"blocks": 1, "whole": 1, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    result = cc.ProgramResult(
        status="built",
        output="",
        error_output="Error: Cannot find module 'jsdom'\nRequire stack:\n- /tmp/x.js",
        exit_code=1,
    )
    finding = code_blocks.report(Path("lesson.php"), 1, result, "", strict=False, ran=True, totals=totals)
    assert finding is None
    assert totals["skipped"] == 1
    assert totals["findings"] == 0
    assert "SKIP lesson.php block 1" in capsys.readouterr().out


NODE_ENOENT = "node:fs:441\n    return binding.readFileUtf8(path, stringToFlags(options.flag));\nError: ENOENT: no such file or directory, open 'package-lock.json'\n    at Object.readFileSync (node:fs:441:20) {\n  errno: -2,\n  code: 'ENOENT',\n}"


def test_missing_file_reads_the_enoent_of_node() -> None:
    """The file name comes from the ENOENT line; any other error gives ''."""
    assert cc.missing_file(NODE_ENOENT) == "package-lock.json"
    assert cc.missing_file("Error: ENOENT: no such file or directory, scandir 'src'") == "src"
    assert cc.missing_file("Error: ENOENT: no such file or directory, readlink 'node_modules/.bin/x'") == "node_modules/.bin/x"
    assert cc.missing_file("fopen: No such file or directory") == ""
    assert cc.missing_file("TypeError: x is not a function") == ""


def test_code_blocks_skips_a_block_that_reads_a_file_of_the_project(capsys: pytest.CaptureFixture[str]) -> None:
    """A block that reads the package-lock.json of the lesson's project is a SKIP, not a RUN."""
    totals = {"blocks": 1, "whole": 1, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    result = cc.ProgramResult(status="built", output="", error_output=NODE_ENOENT, exit_code=1)
    finding = code_blocks.report(Path("lesson.php"), 1, result, "", strict=False, ran=True, totals=totals)
    assert finding is None
    assert totals["skipped"] == 1 and totals["findings"] == 0
    assert "SKIP lesson.php block 1: a file outside the block: package-lock.json" in capsys.readouterr().out


def test_code_blocks_keeps_a_missing_file_the_text_around_the_block_shows() -> None:
    """A lesson that opens a missing file on purpose, and shows it missing and the status, keeps the failure as expected."""
    totals = {"blocks": 1, "whole": 1, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    result = cc.ProgramResult(status="built", output="", error_output="no-such-file.txt: No such file or directory", exit_code=1)
    context = cc.page_text('<p class="cmd1">./errno; echo "exit: $?"</p>\n<p class="cout1">errno 2: No such file or directory<br>exit: 1</p>')
    finding = code_blocks.report(Path("lesson.php"), 1, result, context, strict=False, ran=True, totals=totals, unread="no-such-file.txt")
    assert finding is None
    assert totals["skipped"] == 0 and totals["findings"] == 0


def test_code_blocks_skips_a_missing_file_the_text_does_not_explain(capsys: pytest.CaptureFixture[str]) -> None:
    """The perror line names the file; with no status near the block the run is a SKIP."""
    totals = {"blocks": 1, "whole": 1, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    result = cc.ProgramResult(status="built", output="", error_output="demo/a.txt: No such file or directory", exit_code=1)
    assert code_blocks.report(Path("lesson.php"), 1, result, "size: 0", strict=False, ran=True, totals=totals) is None
    assert "SKIP lesson.php block 1: a file outside the block: demo/a.txt" in capsys.readouterr().out


def test_code_blocks_run_line_shows_stderr_of_a_silent_program(capsys: pytest.CaptureFixture[str]) -> None:
    """A RUN finding of a program that printed nothing shows what it wrote to stderr."""
    totals = {"blocks": 1, "whole": 1, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    result = cc.ProgramResult(status="built", output="", error_output="TypeError: boom", exit_code=1)
    finding = code_blocks.report(Path("lesson.php"), 1, result, "", strict=False, ran=True, totals=totals)
    assert finding is not None and totals["findings"] == 1
    assert "RUN lesson.php block 1: exit 1 and the page does not show it: TypeError: boom" in capsys.readouterr().out


def test_presets_give_node_a_longer_timeout() -> None:
    """Node gets 15 s, for jsdom under parallel jobs; the compiled languages keep 5 s."""
    assert cc.LANGUAGES["javascript"].timeout == 15.0
    assert {name for name, preset in cc.LANGUAGES.items() if preset.timeout != 5.0} == {"javascript"}
    assert quiz_answers.build_parser().parse_args(["x.php"]).timeout is None
    assert code_blocks.build_parser().parse_args(["x.php", "--timeout", "2"]).timeout == 2.0


def test_code_blocks_runs_with_the_timeout_of_the_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Without --timeout the preset decides; --timeout still wins."""
    page = tmp_path / "lesson.php"
    page.write_text('<p class="jscode p2">\n<code>console.log(1);</code>\n</p>\n', encoding="utf-8")
    seen: list[float] = []

    def fake_run_jobs(_session: object, jobs: list[cc.BuildJob], **kwargs: Any) -> list[cc.ProgramResult]:
        seen.append(kwargs["timeout"])
        return [cc.ProgramResult(status="built", output="1", exit_code=0) for _ in jobs]

    monkeypatch.setattr(cc, "run_jobs", fake_run_jobs)
    code_blocks.main([str(page), "--lang", "javascript", "--run"])
    code_blocks.main([str(page), "--lang", "javascript", "--run", "--timeout", "2"])
    code_blocks.main([str(page), "--lang", "c", "--run"])
    capsys.readouterr()
    assert seen == [15.0, 2.0, 5.0]


def test_stated_source_name_follows_the_node_command() -> None:
    """A node file name names the source; a compiled program and another extension do not."""
    js, c = cc.LANGUAGES["javascript"], cc.LANGUAGES["c"]
    assert quiz_answers.stated_source_name(js, "m.js", "js") == "m.js"
    assert quiz_answers.stated_source_name(js, "count.mjs", "mjs") == "count.mjs"
    assert quiz_answers.stated_source_name(js, "count.mjs", "js") == ""
    assert quiz_answers.stated_source_name(js, "", "js") == ""
    assert quiz_answers.stated_source_name(c, "./args", "c") == ""


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
        ("work done, then status 3", ("work done", 3)),
        ("3, then status 0", ("3", 0)),
        ("nothing, because the process ends", ("nothing, because the process ends", None)),
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


def test_quiz_matches_a_status_written_as_prose() -> None:
    """'work done, then status 3' needs the line and the status, as the ' / ' form does."""
    result = cc.ProgramResult(status="built", output="work done", exit_code=3)
    question = "What does this program print, and what is its exit status?"
    assert quiz_answers.output_matches("work done, then status 3", result, question)
    assert not quiz_answers.output_matches("work done, then status 0", result, question)
    assert not quiz_answers.output_matches("3, then status 0", result, question)


# ----------------------------------------------------------- uncaught errors

NODE_TYPE_ERROR = "/tmp/q10/q10.js:2\nconsole.log(o.a.b);\n                ^\n\nTypeError: Cannot read properties of null (reading 'b')\n    at Object.<anonymous> (/tmp/q10/q10.js:2:17)\n\nNode.js v24.22.1\n"
NODE_ASSERTION_ERROR = "node:internal/modules/run_main:107\n    triggerUncaughtException(\n    ^\n\nAssertionError [ERR_ASSERTION]: Expected values to be strictly equal:\n\n4 !== 5\n\n    at file:///tmp/q9/q9.mjs:2:8 {\n  code: 'ERR_ASSERTION'\n}\n"


def test_thrown_error_reads_the_error_line_of_node() -> None:
    """The name comes from the line below the caret, also with an [ERR_...] code."""
    assert cc.thrown_error(NODE_TYPE_ERROR) == "TypeError"
    assert cc.thrown_error(NODE_ASSERTION_ERROR) == "AssertionError"
    assert cc.thrown_error("SyntaxError: Private field '#n' must be declared in an enclosing class") == "SyntaxError"
    assert cc.thrown_error("Error: boom\n    at main (/tmp/x.js:1:7)") == "Error"
    assert cc.thrown_error("warning: unused variable\nsome output") == ""


@pytest.mark.parametrize(
    "option",
    [
        "a TypeError",
        "TypeError",
        "It throws a TypeError, because null has no fields",
        "the program throws a TypeError",
        "A TypeError at run time",
        "a TypeError reaches the top of the program",
        "a TypeError, because rest has no join",
    ],
)
def test_names_error_accepts_the_forms_of_the_courses(option: str) -> None:
    """An option that says the program ends with a TypeError names it."""
    assert cc.names_error(option, "TypeError")


@pytest.mark.parametrize(
    "option",
    ["TypeError boom", "boom TypeError", "1 / a TypeError", "false a TypeError", "a RangeError", "It prints undefined"],
)
def test_names_error_rejects_printed_output_and_another_error(option: str) -> None:
    """A name inside a line of output, or another error, is not the error the program ends with."""
    assert not cc.names_error(option, "TypeError")


def test_names_error_needs_a_name() -> None:
    """A run without an error line names no error."""
    assert not cc.names_error("a TypeError", "")


def test_quiz_matches_the_uncaught_error_of_a_silent_program() -> None:
    """A program that prints nothing and dies of a TypeError proves 'It throws a TypeError'."""
    result = cc.ProgramResult(status="built", output="", error_output=NODE_TYPE_ERROR, exit_code=1)
    assert quiz_answers.output_matches("It throws a TypeError, because null has no fields", result)
    assert not quiz_answers.output_matches("It prints undefined", result)
    assert not quiz_answers.output_matches("a RangeError", result)
    printed = cc.ProgramResult(status="built", output="before", error_output=NODE_TYPE_ERROR, exit_code=1)
    assert not quiz_answers.output_matches("a TypeError", printed)
    survived = cc.ProgramResult(status="built", output="", error_output=NODE_TYPE_ERROR, exit_code=0)
    assert not quiz_answers.output_matches("a TypeError", survived)


def test_quiz_matches_an_assertion_that_throws() -> None:
    """'Nothing, because the assertion throws an AssertionError' is the silent run with that error."""
    result = cc.ProgramResult(status="built", output="", error_output=NODE_ASSERTION_ERROR, exit_code=1)
    assert quiz_answers.output_matches("Nothing, because the assertion throws an AssertionError", result)
    assert not quiz_answers.output_matches("after", result)


def test_build_error_matches_an_option_that_names_the_error() -> None:
    """A SyntaxError of node --check proves 'A SyntaxError, because ...'."""
    error = "SyntaxError: Private field '#n' must be declared in an enclosing class"
    assert quiz_answers.build_error_matches("A SyntaxError, because the private name is used outside its class", error)
    assert quiz_answers.build_error_matches(error, error)
    assert not quiz_answers.build_error_matches("A TypeError at run time", error)
    assert not quiz_answers.build_error_matches("It prints 1", error)


# --------------------------------------------------------------- module mode


def test_says_module_reads_the_prose_not_the_code() -> None:
    """'this module' and 'A module contains:' count; module.exports in code and CommonJS do not."""
    assert quiz_answers.says_module("What does this module print?\n```\nconsole.log(typeof require);\n```")
    assert quiz_answers.says_module("A module contains:\n```\nconsole.log(1);\n```")
    assert quiz_answers.says_module('A folder has package.json with "type": "module". The file app.js contains:')
    assert not quiz_answers.says_module("Two files, a.js is `module.exports = { n: 1 };` and main.js is:")
    assert not quiz_answers.says_module("What does this CommonJS module print?")
    assert not quiz_answers.says_module("What does this program print?\n```\nmodule.exports = 1;\nconsole.log(1);\n```")


def test_source_extension_follows_the_question() -> None:
    """A stated file keeps its extension; a module question is an .mjs file; other presets keep theirs."""
    js = cc.LANGUAGES["javascript"]
    assert quiz_answers.source_extension(js, "What does this module print?", "", "js") == "mjs"
    assert quiz_answers.source_extension(js, "What does this program print?", "", "js") == "js"
    assert quiz_answers.source_extension(js, "What does this module print?", "count.cjs", "js") == "cjs"
    assert quiz_answers.source_extension(js, "What does this program print?", "count.mjs", "js") == "mjs"
    assert quiz_answers.source_extension(js, "What does this program print?", "./args", "js") == "js"
    rust = cc.LANGUAGES["rust"]
    assert quiz_answers.source_extension(rust, "What does this module print?", "", "rs") == "rs"


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


def test_refers_does_not_take_a_product_name_for_a_file() -> None:
    """Node.js and Next.js are names of runtimes, not files the page holds."""
    assert quiz_answers.refer_phrase("What does Node.js add to the language?", []) == ""
    assert quiz_answers.refer_phrase("How does Next.js render a page?", []) == ""
    assert quiz_answers.refer_phrase("Read hello.js and say what it prints", []) == "hello.js"


def test_refers_ignores_a_file_the_options_name() -> None:
    """'Which command runs the file hello.js?' asks about the name, and the options repeat it."""
    question = "Which command runs the file hello.js?"
    options = ["node hello.js", "node run hello.js", "npm hello.js", "javascript hello.js"]
    assert quiz_answers.refer_phrase(question, [], options) == ""
    assert quiz_answers.refer_phrase(question, []) == "hello.js"
    one_option = ["hi", "hello.js", "nothing", "an error"]
    assert quiz_answers.refer_phrase("Read hello.js and say what it prints", [], one_option) == "hello.js"


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


def test_program_snippet_wants_a_fence_or_a_multi_line_span() -> None:
    """A one-line inline mention is not a program to build."""
    pattern = cc.program_pattern(cc.LANGUAGES["javascript"].program_regex)
    assert quiz_answers.program_snippet("Why is `console.log(map)` worse?", pattern) is None
    fenced = "What does this print?\n```\nconsole.log('hi');\n```"
    assert quiz_answers.program_snippet(fenced, pattern) == "console.log('hi');"
    span = "What does this do?\n`console.log('a');\nconsole.log('b');`"
    assert quiz_answers.program_snippet(span, pattern) == "console.log('a');\nconsole.log('b');"


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
    assert name.startswith(f"{cc.project_name()}-course-check-")  # docker ps shows the project
    assert execute[:3] == ["docker", "exec", name]
    script = execute[-1]
    assert "cp -R /src/j1/. /tmp/j1/" in script
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
    assert script.startswith("mkdir -p /tmp/j1\ncp -R /src/j1/. /tmp/j1/\ncd /tmp/j1\n")
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


def test_page_expects_failure_reads_the_status_as_the_shell_prints_it() -> None:
    """'exit code: 1', 'exit status: 1' and 'exitCode = 3' all show the status."""
    failed = cc.ProgramResult(status="built", exit_code=1)
    assert cc.page_expects_failure(cc.page_text("<p>exit code: 1</p>"), failed)
    assert cc.page_expects_failure(cc.page_text("<p>the exit status: 1</p>"), failed)
    assert cc.page_expects_failure(cc.page_text("<p>the program is done, the exit code is 1</p>"), failed)
    exits_three = cc.ProgramResult(status="built", exit_code=3)
    assert cc.page_expects_failure(cc.page_text("<code>process.exitCode = 3;</code>"), exits_three)
    assert not cc.page_expects_failure(cc.page_text("<p>exit code: 0</p>"), failed)


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


@pytest.mark.skipif(NODE_MAJOR is None or NODE_MAJOR < 22, reason=ESM_REASON)
def test_real_javascript_program_builds_and_runs(tmp_path: Path) -> None:
    """A .js file with import syntax is an ES module by detection and runs as one."""
    (tmp_path / "hello.js").write_text(
        "import { basename } from 'node:path';\n\nconsole.log(basename('/tmp/hello.js'));\n",
        encoding="utf-8",
    )
    session = cc.open_session(root=tmp_path)
    job = cc.BuildJob(
        tmp_path,
        "hello.js",
        cc.LANGUAGES["javascript"].build,
        run_command=cc.LANGUAGES["javascript"].run,
    )
    result = session.run(job, run=True, timeout=10, output_limit=2000)
    assert result.status == "built", result.build_output
    assert result.exit_code == 0
    assert result.output.strip() == "hello.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not in PATH")
def test_real_javascript_syntax_error_names_the_message(tmp_path: Path) -> None:
    """node --check fails and the SyntaxError line is the one reported."""
    (tmp_path / "broken.js").write_text("const x = ;\n", encoding="utf-8")
    session = cc.open_session(root=tmp_path)
    job = cc.BuildJob(tmp_path, "broken.js", cc.LANGUAGES["javascript"].build)
    result = session.run(job, run=False, timeout=10, output_limit=2000)
    assert result.status == "compile_error"
    assert result.error_line.startswith("SyntaxError")
    assert result.skip == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not in PATH")
def test_code_blocks_reports_a_javascript_syntax_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A block whose line does not parse is a FAIL the page does not explain."""
    page = tmp_path / "lesson.php"
    page.write_text(
        '<p class="jscode p2">\n<code>console.log(&quot;before&quot;);</code>\n<code>const x = ;</code>\n</p>\n',
        encoding="utf-8",
    )
    status = code_blocks.main([str(page), "--lang", "javascript"])
    out = capsys.readouterr().out
    assert status == 1
    assert "FAIL" in out and "SyntaxError" in out
    assert "blocks=1 whole=1 built=0 expected=0 skipped=0 findings=1" in out


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


JS_QUIZ_PAGE = """<?php
echo LessonQuiz::render(array(
    array(
        'q' => 'What does this program print?
```
console.log([1, 2, 3].map((n) => n * 2).join(","));
```',
        'options' => array('2,4,6', '1,2,3', 'a TypeError', 'nothing'),
        'answer' => {answer},
        'why' => 'The callback doubles each number and join puts a comma between them.',
    ),
), 'en');
"""


@pytest.mark.skipif(
    shutil.which("php") is None or shutil.which("node") is None,
    reason="php or node is not in PATH",
)
def test_quiz_main_proves_a_javascript_answer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The marked option of a jscode program is compared with what node prints."""
    page = tmp_path / "lesson.php"
    page.write_text(JS_QUIZ_PAGE.format(answer=0), encoding="utf-8")
    status = quiz_answers.main([str(page), "--lang", "javascript", "--count", "1"])
    out = capsys.readouterr().out
    assert status == 0
    assert "pages=1 questions=1 programs=1 build_errors=0 wrong=0 unmatched=0 refers=0 structure=0" in out


@pytest.mark.skipif(
    shutil.which("php") is None or shutil.which("node") is None,
    reason="php or node is not in PATH",
)
def test_quiz_main_finds_a_wrong_javascript_answer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Marking another option makes the run output report WRONG."""
    page = tmp_path / "lesson.php"
    page.write_text(JS_QUIZ_PAGE.format(answer=1), encoding="utf-8")
    status = quiz_answers.main([str(page), "--lang", "javascript", "--count", "1"])
    out = capsys.readouterr().out
    assert status == 1
    assert "WRONG" in out
    assert "the output matches option [0]: '2,4,6'" in out


# A module question, stated arguments, a status in prose, an uncaught error,
# a SyntaxError of the build and a question about the name of a file. The
# code avoids single quotes: they would end the PHP string.
JS_RULES_PAGE = """<?php
echo LessonQuiz::render(array(
    array(
        'q' => 'What does this module print?
```
console.log(typeof require);
```',
        'options' => array('undefined', 'function', 'object', 'a ReferenceError'),
        'answer' => 0,
        'why' => 'A module has no require binding.',
    ),
    array(
        'q' => 'What does this program print?
```
console.log(process.argv.slice(2).length);
```
when it is run as `node count.js one two three`?',
        'options' => array('1', '3', '5', '0'),
        'answer' => 1,
        'why' => 'slice(2) drops the runtime and the script.',
    ),
    array(
        'q' => 'What does this program print, and what is its exit status?
```
console.log("work done");
process.exitCode = 3;
```',
        'options' => array('work done, then status 0', 'work done, then status 3', 'nothing', '3, then status 0'),
        'answer' => 1,
        'why' => 'The status is set, and the runtime returns it at the end.',
    ),
    array(
        'q' => 'What happens when this program runs?
```
const o = { a: null };
console.log(o.a.b);
```',
        'options' => array('It throws a TypeError, because null has no fields', 'It prints undefined', 'It prints null', 'a RangeError'),
        'answer' => 0,
        'why' => 'A read of a field of null throws.',
    ),
    array(
        'q' => 'What happens when this file is loaded?
```
class A {
  #n = 1;
}
console.log(new A().#n);
```',
        'options' => array('A SyntaxError, because the private name is used outside its class', 'It prints 1', 'It prints undefined', 'A TypeError at run time'),
        'answer' => 0,
        'why' => 'The parser refuses a private name outside its class.',
    ),
    array(
        'q' => 'Which command runs the file hello.js?',
        'options' => array('node hello.js', 'node run hello.js', 'npm hello.js', 'javascript hello.js'),
        'answer' => 0,
        'why' => 'The runtime takes the path of the file as its first argument.',
    ),
), 'en');
"""


@pytest.mark.skipif(
    shutil.which("php") is None or shutil.which("node") is None,
    reason="php or node is not in PATH",
)
def test_quiz_main_proves_javascript_modules_arguments_status_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Every rule of a JavaScript question proves its marked option with a real node."""
    page = tmp_path / "lesson.php"
    page.write_text(JS_RULES_PAGE, encoding="utf-8")
    status = quiz_answers.main([str(page), "--lang", "javascript", "--count", "6", "--jobs", "1"])
    out = capsys.readouterr().out
    assert status == 0, out
    assert "pages=1 questions=6 programs=4 build_errors=1 wrong=0 unmatched=0 refers=0 structure=0" in out


@pytest.mark.skipif(
    shutil.which("php") is None or shutil.which("node") is None,
    reason="php or node is not in PATH",
)
def test_quiz_main_ext_turns_module_mode_off(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With --ext js the module question runs as CommonJS, where require is a function."""
    page = tmp_path / "lesson.php"
    page.write_text(JS_RULES_PAGE, encoding="utf-8")
    status = quiz_answers.main([str(page), "--lang", "javascript", "--count", "6", "--ext", "js", "--jobs", "1"])
    out = capsys.readouterr().out
    assert status == 1
    assert f"WRONG {page} q1" in out
    assert "the output matches option [1]: 'function'" in out


JS_FILES_PAGE = """<?php
echo LessonQuiz::render(array(
    array(
        'q' => 'The file m.js contains:
```
module.exports = { n: 1 };
const m = require("./m.js");
console.log(m.n);
```
What does `node m.js` print?',
        'options' => array('1', 'undefined', 'a TypeError', 'nothing, a module cannot require itself'),
        'answer' => 0,
        'why' => 'The cache returns the object assigned before the require.',
    ),
    array(
        'q' => 'What does this program print in the project folder?
```
const { readFileSync } = require("node:fs");
console.log(JSON.parse(readFileSync("package.json", "utf8")).name);
```',
        'options' => array('lock-course', 'undefined', 'package.json', 'a SyntaxError'),
        'answer' => 0,
        'why' => 'The manifest names the project.',
    ),
    array(
        'q' => 'What happens when this program runs?
```
const { readFileSync } = require("node:fs");
readFileSync("missing.txt");
console.log("read");
```',
        'options' => array('It throws an Error, because missing.txt does not exist', 'It prints read', 'It prints undefined', 'a TypeError'),
        'answer' => 0,
        'why' => 'readFileSync throws when the file is not there.',
    ),
    array(
        'q' => 'What does this program print?
```
setInterval(() => {
  console.log("tick");
}, 5000);
```',
        'options' => array('tick, then it exits', 'nothing', 'a TypeError', 'undefined'),
        'answer' => 0,
        'why' => 'A timer keeps the process alive.',
    ),
), 'en');
"""


@pytest.mark.skipif(
    shutil.which("php") is None or shutil.which("node") is None,
    reason="php or node is not in PATH",
)
def test_quiz_main_names_the_file_skips_a_project_file_and_shows_a_timeout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """node m.js runs as m.js, a missing package.json is a SKIP, an ENOENT answer is proved, a hang says so."""
    page = tmp_path / "lesson.php"
    page.write_text(JS_FILES_PAGE, encoding="utf-8")
    status = quiz_answers.main([str(page), "--lang", "javascript", "--count", "4", "--jobs", "1", "--timeout", "1"])
    out = capsys.readouterr().out
    assert status == 0, out
    assert f"SKIP {page} q2: a file outside the question: package.json" in out
    assert f"UNMATCHED {page} q4: marked 'tick, then it exits'; output '', then the run timed out (--timeout)" in out
    assert "pages=1 questions=4 programs=3 build_errors=0 wrong=0 unmatched=1 refers=0 structure=0" in out


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("node args.js alpha beta", cc.PageRun("args.js", ("alpha", "beta"))),
        ("GREETING=hello node env.mjs", cc.PageRun("env.mjs", (), (("GREETING", "hello"),))),
        ("node boundary.mjs Ada 2>&1", cc.PageRun("boundary.mjs", ("Ada",))),
        ("npm pack --dry-run --json | node ../pack-report.js", cc.PageRun("pack-report.js", input_from="npm pack --dry-run --json")),
        ("gcc argv.c -o argv && ./argv alpha beta", cc.PageRun("./argv", ("alpha", "beta"))),
        ("./stderr_demo >out.txt 2>err.txt", cc.PageRun("./stderr_demo")),
        ("ASAN_OPTIONS=print_legend=0 ./asan 2>&1 | head -12", cc.PageRun("./asan", (), (("ASAN_OPTIONS", "print_legend=0"),))),
        ("./prog < input.txt", cc.PageRun("./prog", input_from="input.txt")),
        ("printf 'a\\nb\\n' | ./lines", cc.PageRun("./lines", input_from="printf a\\nb\\n")),
    ],
)
def test_parse_run_line_reads_the_forms_of_the_courses(line: str, expected: cc.PageRun) -> None:
    """File or program, arguments, variables and piped input, with output redirects dropped."""
    assert cc.parse_run_line(line) == expected


@pytest.mark.parametrize("line", ["npm start --silent", "node --test a.test.js", "./x $HOME", "rustc moved.rs", "node 'unclosed"])
def test_parse_run_line_ignores_what_it_cannot_run_as_the_page_does(line: str) -> None:
    """No block run, a node option, an expansion or a broken quote gives None."""
    assert cc.parse_run_line(line) is None


def test_page_run_takes_the_first_line_that_runs_a_block() -> None:
    """The build line is passed over; an empty paragraph gives an empty run."""
    assert cc.page_run("gcc -Wall hello.c -o hello\n./hello one") == cc.PageRun("./hello", ("one",))
    assert cc.page_run("") == cc.PageRun()


def test_extract_blocks_reads_caption_commands_and_context() -> None:
    """The caption right before, every command up to the next block, and the text that belongs to the block."""
    text = '<p class="jsoncode p2">\n<code>"test": "exit 1"</code>\n</p>\n<p class="p2">\n    The program reads the lock file.\n</p>\n<p class="p8">\n    The file <b class="fname">env.mjs</b>:\n</p>\n<p class="jscode p2">\n<code>console.log(1);</code>\n</p>\n<p class="cmd1">\n    npm init -y<br>\n    GREETING=hi node env.mjs\n</p>\n<p class="cout1">\n    status: 3\n</p>\n<h2>Next</h2>\n<p class="cmd1">\n    node env.mjs again\n</p>\n<p class="p2">\n    The second program.\n</p>\n<p class="jscode p2">\n<code>console.log(2);</code>\n</p>\n'
    blocks = cc.extract_blocks(text, "jscode")
    assert len(blocks) == 2
    first, second = blocks[0], blocks[1]
    assert (first.caption, first.command) == ("env.mjs", "npm init -y\nGREETING=hi node env.mjs\nnode env.mjs again")
    assert "The program reads the lock file." in first.context and "status: 3" in first.context
    assert "exit 1" not in first.context
    assert (second.caption, second.command, second.context) == ("", "", "The second program.")


def test_build_script_runs_with_the_variables_of_the_page() -> None:
    """NAME=value pairs go through env(1), quoted, in front of the run."""
    script = cc.build_script(
        "node --check env.mjs",
        "./prog",
        run=True,
        timeout=5,
        limit=100,
        build_log="./.build.log",
        run_log="./.run.log",
        error_log="./.error.log",
        run_command="node env.mjs",
        environment=(("GREETING", "hello world"),),
    )
    assert "timeout 5 env 'GREETING=hello world' node env.mjs </dev/null >./.run.log" in script


def test_block_run_and_source_name_follow_the_page() -> None:
    """The command names the file; a caption of another file drops the command; --ext limits the name."""
    js, c = cc.LANGUAGES["javascript"], cc.LANGUAGES["c"]
    page = Path("lesson.php")
    env_block = cc.CodeBlock(page, 1, "console.log(1);", "env.mjs", "GREETING=hi node env.mjs")
    run = code_blocks.block_run(js, env_block)
    assert run == cc.PageRun("env.mjs", (), (("GREETING", "hi"),))
    assert code_blocks.block_source_name(js, env_block, run, "js", fixed=False) == "env.mjs"
    assert code_blocks.block_source_name(js, env_block, run, "js", fixed=True) == ""
    helper = cc.CodeBlock(page, 2, "console.log(1);", "helper.js", "node main.js one")
    assert code_blocks.block_run(js, helper) == cc.PageRun()
    assert code_blocks.block_source_name(js, helper, cc.PageRun(), "js", fixed=False) == "helper.js"
    nested = cc.CodeBlock(page, 3, "console.log(1);", "tests/inventory.test.js", "node --test")
    assert code_blocks.block_source_name(js, nested, cc.PageRun(), "js", fixed=False) == "inventory.test.js"
    native = cc.CodeBlock(page, 4, "int main(void) { return 0; }", "", "gcc a.c -o argv\n./argv one")
    assert code_blocks.block_run(c, native) == cc.PageRun("./argv", ("one",))
    assert code_blocks.block_source_name(c, native, cc.PageRun("./argv"), "c", fixed=False) == ""


def test_code_blocks_skips_a_run_whose_input_the_page_pipes_in(capsys: pytest.CaptureFixture[str]) -> None:
    """A failed run fed by another command is a SKIP that names it; a run that passed stays silent."""
    totals = {"blocks": 2, "whole": 2, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    failed = cc.ProgramResult(status="built", output="", error_output="SyntaxError: Unexpected end of JSON input", exit_code=1)
    passed = cc.ProgramResult(status="built", output="ok", exit_code=0)
    assert code_blocks.report(Path("lesson.php"), 1, failed, "", strict=False, ran=True, totals=totals, input_from="npm pack --json") is None
    assert code_blocks.report(Path("lesson.php"), 2, passed, "", strict=False, ran=True, totals=totals, input_from="npm pack --json") is None
    assert totals["skipped"] == 1 and totals["findings"] == 0
    assert "SKIP lesson.php block 1: input the page gives it from npm pack --json" in capsys.readouterr().out


JS_COMMANDS_PAGE = """<p class="p8">
    The file <b class="fname">env.mjs</b>:
</p>
<p class="jscode p2">
<code>console.log(&#39;greeting:&#39;, process.env.GREETING.length);</code>
<code>if (typeof require !== &#39;undefined&#39;) process.exit(3);</code>
</p>
<p class="cmd1">
    GREETING=hello node env.mjs
</p>
<p class="p8">
    The file <b class="fname">report.js</b>:
</p>
<p class="jscode p2">
<code>let text = &#39;&#39;;</code>
<code>process.stdin.on(&#39;data&#39;, (chunk) =&gt; { text += chunk; });</code>
<code>process.stdin.on(&#39;end&#39;, () =&gt; {</code>
<code>  console.log(JSON.parse(text).name);</code>
<code>});</code>
</p>
<p class="cmd1">
    npm pack --json | node ../report.js
</p>
<p class="p8">
    The file <b class="fname">helper.js</b>:
</p>
<p class="jscode p2">
<code>console.log(&#39;helper&#39;);</code>
<code>if (process.argv.length &gt; 2) process.exit(4);</code>
</p>
<p class="cmd1">
    node main.js one
</p>
<p class="jscode p2">
<code>console.log(process.argv.slice(2).join(&#39;,&#39;));</code>
<code>if (process.argv.length !== 4) process.exit(5);</code>
</p>
<p class="cmd1">
    node args.js alpha beta
</p>
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not in PATH")
def test_code_blocks_runs_each_block_as_its_page_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A variable and .mjs, piped input as a SKIP, a command of another file ignored, arguments passed."""
    page = tmp_path / "lesson.php"
    page.write_text(JS_COMMANDS_PAGE, encoding="utf-8")
    status = code_blocks.main([str(page), "--lang", "javascript", "--run", "--jobs", "1"])
    out = capsys.readouterr().out
    assert status == 0, out
    assert f"SKIP {page} block 2: input the page gives it from npm pack --json" in out
    assert "blocks=4 whole=4 built=4 expected=0 skipped=1 findings=0" in out


def test_shell_segments_split_a_line_at_its_lists() -> None:
    """; && and || end a command, so a build and an echo are read apart."""
    assert cc.shell_segments(['gcc -O2 warn.c -o warn; echo "status: $?"', "./warn"]) == ["gcc -O2 warn.c -o warn", 'echo "status: $?"', "./warn"]


def test_stated_build_reads_one_command_of_a_list() -> None:
    """The compile before ; or && is used; two sources or none fall back to the default."""
    assert cc.stated_build("c", ['gcc -O2 warn.c -o warn; echo "status: $?"'], "DEFAULT", "c") == "gcc -O2 {src} -o {out}"
    assert cc.stated_build("c", ["gcc -std=c17 x.c -o x && ./x"], "DEFAULT", "c") == "gcc -std=c17 {src} -o {out}"
    assert cc.stated_build("c", ["gcc -Wall helpers.c use.c -o use"], "DEFAULT", "c") == "DEFAULT"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("gcc -Wall -Wextra badfmt.c -o badfmt", True),
        ('gcc -O2 warn.c -o warn; echo "status: $?"\ngcc -O2 -g warn.c -o warn_release', True),
        ("gcc -Wall x.c -o x\n./x", False),
        ("gcc -g leak.c -o leak\nvalgrind ./leak", False),
        ('make check; echo "status: $?"', False),
        ("vi use.c", False),
        ("", False),
    ],
)
def test_build_only_reads_a_page_that_compiles_and_runs_nothing(command: str, expected: bool) -> None:
    """Only compiles of the preset's compiler, and nothing that runs a program."""
    assert cc.build_only(command, "c") is expected


def test_unread_file_names_a_file_the_block_reads_and_lacks(tmp_path: Path) -> None:
    """A literal fopen for reading counts; a file the block writes first, or that is there, does not."""
    assert cc.unread_file('FILE *f = fopen("records.bin", "rb");', tmp_path) == "records.bin"
    assert cc.unread_file('fopen("out.txt", "w"); FILE *f = fopen("out.txt", "r");', tmp_path) == ""
    (tmp_path / "here.txt").write_text("x", encoding="utf-8")
    assert cc.unread_file('fopen("here.txt", "r");', tmp_path) == ""
    assert cc.unread_file('let text = fs::read_to_string("sample.txt")?;', tmp_path) == "sample.txt"
    assert cc.unread_file('std::ifstream in("data.txt");', tmp_path) == "data.txt"


def test_page_expects_failure_reads_make_and_prose_forms() -> None:
    """make's "Error 1" and "the test binary returns 1" show status 1; a sanitizer report is a crash."""
    failed = cc.ProgramResult(status="built", exit_code=1)
    assert cc.page_expects_failure("make: *** [Makefile:7: check] Error 1", failed)
    assert cc.page_expects_failure("the test binary returns 1, and make exits with 2", failed)
    assert not cc.page_expects_failure("checks: 2, failures: 1", failed)
    asan = cc.ProgramResult(status="built", exit_code=134)
    assert cc.page_expects_failure("==1==ERROR: AddressSanitizer: attempting double-free", asan)


def test_stated_page_run_reads_variables_and_drops_only_2_to_1() -> None:
    """A question's GREETING=hi node env.mjs keeps its variable; 2>&1 is harmless, 2>/dev/null is not."""
    assert cc.stated_page_run(["GREETING=hi node env.mjs one"]) == cc.PageRun("env.mjs", ("one",), (("GREETING", "hi"),))
    assert cc.stated_page_run(["node boundary.mjs Ada 2>&1"]) == cc.PageRun("boundary.mjs", ("Ada",))
    assert cc.stated_page_run(["gcc x.c -o x && ./x one"]) == cc.PageRun("./x", ("one",))
    assert cc.stated_page_run(["node app.js 2>/dev/null"]) == cc.PageRun()


def test_question_files_writes_only_what_the_program_names() -> None:
    """a.js is `...` is written when the program requires it; a file it never names is not."""
    question = "Two files in one folder, a.js is `module.exports = { n: 1 };` and notes.txt holds `x`. main.js is:"
    assert quiz_answers.question_files(question, 'const a = require("./a.js");') == {"a.js": "module.exports = { n: 1 };"}
    holds = 'The file data.json next to the program holds `{ "runs": 3 }`. What does it print?'
    assert quiz_answers.question_files(holds, 'require("./data.json")') == {"data.json": '{ "runs": 3 }'}


C_PAGE_RULES = """<p class="ccode p2">
<code>#include &lt;stdio.h&gt;</code>
<code>void main(void)</code>
<code>{</code>
<code>    printf(&quot;hello\\n&quot;);</code>
<code>}</code>
</p>
<p class="cmd1">
    gcc voidmain.c -o voidmain<br>
    ./voidmain
</p>
<p class="p2">
    The program below would crash, so it is not run here.
</p>
<p class="ccode p2">
<code>int main(void)</code>
<code>{</code>
<code>    int *p = 0;</code>
<code>    return *p;</code>
<code>}</code>
</p>
<p class="cmd1">
    gcc -Wall null.c -o null
</p>
"""


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc is not in PATH")
def test_code_blocks_follows_a_page_that_only_builds_and_ignores_a_void_main_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """void main exits with whatever printf left; a block the page only compiles is built, not run."""
    page = tmp_path / "lesson.php"
    page.write_text(C_PAGE_RULES, encoding="utf-8")
    status = code_blocks.main([str(page), "--lang", "c", "--run", "--jobs", "1"])
    out = capsys.readouterr().out
    assert status == 0, out
    assert "blocks=2 whole=2 built=2 expected=0 skipped=0 findings=0" in out


JS_FILES_AND_VARIABLES_PAGE = """<?php
echo LessonQuiz::render(array(
    array(
        'q' => 'Two files in one folder, a.js is `module.exports = 2;` and main.js is:
```
const a = require("./a.js");
console.log(a * 2);
```',
        'options' => array('4', '2', 'NaN', 'a TypeError'),
        'answer' => 0,
        'why' => 'require returns the number a.js exports.',
    ),
    array(
        'q' => 'What does `GREETING=hey node env.js` print?
```
console.log(process.env.GREETING);
```',
        'options' => array('hey', 'undefined', 'GREETING', 'an empty line'),
        'answer' => 0,
        'why' => 'The variable is set for that one run.',
    ),
), 'en');
"""


@pytest.mark.skipif(
    shutil.which("php") is None or shutil.which("node") is None,
    reason="php or node is not in PATH",
)
def test_quiz_main_writes_the_other_file_and_sets_the_variable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The file the question gives inline is next to the program, and GREETING reaches it."""
    page = tmp_path / "lesson.php"
    page.write_text(JS_FILES_AND_VARIABLES_PAGE, encoding="utf-8")
    status = quiz_answers.main([str(page), "--lang", "javascript", "--count", "2", "--jobs", "1"])
    out = capsys.readouterr().out
    assert status == 0, out
    assert "pages=1 questions=2 programs=2 build_errors=0 wrong=0 unmatched=0 refers=0 structure=0" in out


def test_extract_blocks_groups_blocks_that_share_their_commands() -> None:
    """A broken and a fixed program shown back to back share the build and the error after them."""
    text = '<p class="path p2">broken.cpp</p>\n<p class="cppcode p2">\n<code>int main() { totl += 1; }</code>\n</p>\n<p class="path p2">fixed.cpp</p>\n<p class="cppcode p2">\n<code>int main() { return 0; }</code>\n</p>\n<p class="cmd1">\n    g++ broken.cpp -o broken<br>\n    g++ fixed.cpp -o fixed\n</p>\n<p class="cout1">\nbroken.cpp:1:14: error: \'totl\' was not declared in this scope\n</p>\n'
    blocks = cc.extract_blocks(text, "cppcode")
    assert len(blocks) == 2
    broken, fixed = blocks[0], blocks[1]
    assert broken.command == fixed.command == "g++ broken.cpp -o broken\ng++ fixed.cpp -o fixed"
    assert "'totl' was not declared" in broken.context and "'totl' was not declared" in fixed.context


def test_failure_helpers_read_rust_variables_argument_files_and_not_found(tmp_path: Path) -> None:
    """NotPresent, kind: NotFound, a missing argument file and a page that shows a missing file."""
    assert cc.missing_variable("called `Result::unwrap()` on an `Err` value: NotPresent")
    assert not cc.missing_variable("called `Result::unwrap()` on an `Err` value: ParseIntError")
    assert cc.missing_file('Os { code: 2, kind: NotFound, message: "No such file or directory" }') == "the file the program opens"
    assert cc.missing_argument_file(["notes.txt", "-n", "3", "1.5"], tmp_path) == "notes.txt"
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert cc.missing_argument_file(["notes.txt", "1.5"], tmp_path) == ""
    assert cc.shows_not_found("errno 2: No such file or directory")
    assert not cc.shows_not_found("words: 11")
    assert cc.skip_reason("/usr/bin/ld: cannot find rect.o: No such file or directory") == "another file or library of the example"


def test_code_blocks_reads_what_the_run_lacked_before_the_status(capsys: pytest.CaptureFixture[str]) -> None:
    """A status near the block does not excuse a run without its input or file; a shown missing file does."""
    totals = {"blocks": 6, "whole": 6, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    failed = cc.ProgramResult(status="built", output="", error_output="SyntaxError: Unexpected end of JSON input", exit_code=1)
    shown_status = "echo bad | node parse.js; echo $? 1"
    assert code_blocks.report(Path("a.php"), 1, failed, shown_status, strict=False, ran=True, totals=totals, input_from="echo bad") is None
    lacking = cc.ProgramResult(status="built", output="", error_output="demo.txt: No such file or directory", exit_code=1)
    assert code_blocks.report(Path("a.php"), 2, lacking, "exit: 1", strict=False, ran=True, totals=totals) is None
    assert code_blocks.report(Path("a.php"), 3, lacking, "demo.txt: No such file or directory exit: 1", strict=False, ran=True, totals=totals) is None
    panicked = cc.ProgramResult(status="built", output="", error_output="thread 'main' panicked at src/main.rs:3:5: boom", exit_code=101)
    assert code_blocks.report(Path("a.php"), 4, panicked, "index: 3", strict=False, ran=True, totals=totals, cargo=True) is None
    no_arguments = cc.ProgramResult(status="built", output="", error_output="", exit_code=2)
    assert code_blocks.report(Path("a.php"), 5, no_arguments, "tests passed", strict=False, ran=True, totals=totals, ctest=True) is None
    shown = "exit: 2"
    assert code_blocks.report(Path("a.php"), 6, no_arguments, shown, strict=False, ran=True, totals=totals, ctest=True) is None
    out = capsys.readouterr().out
    assert "SKIP a.php block 5: run by ctest on the page" in out
    assert "block 6" not in out
    assert "SKIP a.php block 1: input the page gives it from echo bad" in out
    assert "SKIP a.php block 2: a file outside the block: demo.txt" in out
    assert "block 3" not in out
    assert "SKIP a.php block 4: built and run with cargo on the page" in out
    assert totals["skipped"] == 4 and totals["findings"] == 0


def test_shows_not_found_wants_the_error_near_the_name() -> None:
    """Near the name, or with no name on the page, a not-found error is the lesson's; far from it, it is another file's."""
    near = "./errors missing.txt: No such file or directory status: 1"
    assert cc.shows_not_found(near, "missing.txt")
    assert cc.shows_not_found("errno: 2 message: No such file or directory exit: 1", "no-such-file.txt")
    far = "node_modules/.bin/wc-cli notes.txt " + "x " * 200 + "wc-cli: ENOENT missing.txt"
    assert not cc.shows_not_found(far, "node_modules/.bin/wc-cli")
    assert not cc.shows_not_found("environment variable not found", "/tmp/app.ini")


def test_extract_blocks_ends_the_last_context_before_the_exercises() -> None:
    """The exercises and the quiz ask about failures; they are not part of the last block's text."""
    text = '<p class="jscode p2">\n<code>console.log(1);</code>\n</p>\n<p class="cout1">\n1\n</p>\n<div id="exercises" class="anchor"></div>\n<p class="p2">\n    Run it with a missing file. Which exit status do you get?\n</p>\n'
    block = cc.extract_blocks(text, "jscode")[0]
    assert block.context == "1"


def test_page_expects_failure_reads_echo_status_and_its_bare_number() -> None:
    """`node exitcode.js` then `echo $?` shows the status as a bare number in the output."""
    exits_three = cc.ProgramResult(status="built", exit_code=3)
    assert cc.page_expects_failure("node exitcode.js echo $? work done 3 Both lines ran", exits_three)
    assert not cc.page_expects_failure("node exitcode.js echo $? work done 0 Both lines ran", exits_three)
