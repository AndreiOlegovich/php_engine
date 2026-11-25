# version 0.1.2
"""Tests for the --help examples, the --worktree flag, the -v file list and the --fix offer of
container/tools/linters/lint-static.sh and its Python port lint-static.py.

--fix without --tool phpcs must explain that --tool is needed, show a
command for every tool, and then ask Y or n per fixer: phpcbf for .php,
ruff for .py, two questions when both kinds are selected. The questions
need a terminal, so these tests give the script a pty as stdin.

A phpcs check with --staged (the commit hook) must save the paths of the
failing files to container/log/phpcs-latest-commit-failed-list.log, and
--fix-failed must fix exactly the files named there.

Fixtures live in pytest's tmp_path: a fake worktree (git init, with
container/src and a copy of phpcs.xml) stands in for a real one, so no
test writes into the repository.
"""

import os
import pty
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONTAINER = REPO / "container"
LINTERS = CONTAINER / "tools" / "linters"
SCRIPTS = {"sh": LINTERS / "lint-static.sh", "py": LINTERS / "lint-static.py"}

TIMEOUT = 600
QUESTION = "[Y/n]"
BROKEN_PHP = "<?php\n\n$title = 'a'.$title_suffix;\n"
FIXED_PHP = "<?php\n\n$title = 'a' . $title_suffix;\n"
BROKEN_PY = 'import os\nx = {  "a":1 }\n'
FIXED_PY = 'x = {"a": 1}\n'

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is required to run phpcs")


def clean_env():
    """Environment without the GIT_* variables a pre-commit hook sets."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_") and key != "PHPCS_FAILED_LIST"}


@pytest.fixture(params=sorted(SCRIPTS))
def script(request):
    return str(SCRIPTS[request.param])


def run_plain(script, *args):
    """Run without a terminal: stdin is /dev/null."""
    return subprocess.run(
        [script, *args],
        cwd=CONTAINER,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=TIMEOUT,
        env=clean_env(),
        check=False,
    )


def run_tty(script, args, answers):
    """Run with a pty as stdin; send one answer per question as it appears.

    Returns (exit code, output, number of questions asked).
    """
    master, slave = pty.openpty()
    proc = subprocess.Popen(  # pylint: disable=consider-using-with
        [script, *args],
        cwd=CONTAINER,
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=clean_env(),
    )
    os.close(slave)
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    output = ""
    answered = 0
    deadline = time.monotonic() + TIMEOUT
    try:
        while True:
            assert time.monotonic() < deadline, f"timeout; output so far:\n{output}"
            ready, _, _ = select.select([fd], [], [], 1.0)
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            output += chunk.decode("utf-8", errors="replace")
            while answered < output.count(QUESTION):
                answer = answers[answered] if answered < len(answers) else "n"
                os.write(master, answer.encode())
                answered += 1
        return proc.wait(timeout=60), output, output.count(QUESTION)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        os.close(master)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, env=clean_env(), stdout=subprocess.DEVNULL, check=True)


@pytest.fixture
def worktree(tmp_path):
    """A stand-in for another worktree: <tmp>/wt/container/src in its own repo."""
    root = tmp_path / "wt"
    src = root / "container" / "src"
    src.mkdir(parents=True)
    shutil.copy(CONTAINER / "phpcs.xml", root / "container" / "phpcs.xml")
    (src / "broken.php").write_text(BROKEN_PHP, encoding="utf-8")
    git(root, "init", "-q")
    return root


# --- --help ---------------------------------------------------------------


def test_help_ends_with_many_examples(script):
    result = run_plain(script, "--help")
    assert result.returncode == 0
    name = Path(script).name
    head, sep, examples = result.stdout.partition("\nExamples")
    assert sep, f"no Examples section in --help:\n{result.stdout}"
    assert "Usage:" in head
    commands = [line for line in examples.splitlines() if name in line]
    assert len(commands) >= 30, f"only {len(commands)} example commands"
    for flag in ("--worktree", "--fix", "--staged", "--tool psalm", "--report", "--level"):
        assert any(flag in line for line in commands), f"no example with {flag}"
    last = result.stdout.rstrip().splitlines()[-1]
    assert name in last, f"--help does not end with an example: {last!r}"


# --- --fix without --tool phpcs -------------------------------------------


def test_fix_without_tool_explains_every_tool(script, tmp_path):
    php = tmp_path / "a.php"
    php.write_text(BROKEN_PHP, encoding="utf-8")
    result = run_plain(script, "--fix", str(php))
    assert result.returncode == 2, result.stdout
    assert "--fix needs the --tool flag" in result.stdout
    for tool in ("--tool phpstan", "--tool psalm", "--tool phpcs --files", "--tool phpcs --fix", "ruff check --fix"):
        assert tool in result.stdout, f"no command example with {tool!r}:\n{result.stdout}"
    # No terminal: nothing is asked, nothing is fixed.
    assert QUESTION not in result.stdout
    assert php.read_text(encoding="utf-8") == BROKEN_PHP


def test_fix_with_report_only_tool_names_it(script, tmp_path):
    php = tmp_path / "a.php"
    php.write_text(BROKEN_PHP, encoding="utf-8")
    result = run_plain(script, "--tool", "psalm", "--fix", str(php))
    assert result.returncode == 2, result.stdout
    assert "tools selected now (psalm) only report" in result.stdout


def test_php_file_asks_once_and_n_fixes_nothing(script, tmp_path):
    php = tmp_path / "a.php"
    php.write_text(BROKEN_PHP, encoding="utf-8")
    rc, output, questions = run_tty(script, ["--fix", str(php)], ["n"])
    assert questions == 1, output
    assert "phpcbf" in output and "phpcs" in output
    assert rc == 2, output
    assert php.read_text(encoding="utf-8") == BROKEN_PHP


@pytest.mark.parametrize("answer", ["y", "Y", "\n"])
def test_py_file_is_fixed_with_ruff_on_yes(script, tmp_path, answer):
    target = tmp_path / "a.py"
    target.write_text(BROKEN_PY, encoding="utf-8")
    rc, output, questions = run_tty(script, ["--fix", str(target)], [answer])
    assert questions == 1, output
    assert "ruff" in output
    assert rc == 0, output
    assert target.read_text(encoding="utf-8") == FIXED_PY


def test_py_file_n_fixes_nothing(script, tmp_path):
    target = tmp_path / "a.py"
    target.write_text(BROKEN_PY, encoding="utf-8")
    rc, output, questions = run_tty(script, ["--fix", str(target)], ["n"])
    assert questions == 1, output
    assert rc == 2, output
    assert target.read_text(encoding="utf-8") == BROKEN_PY


def test_php_and_py_files_ask_two_questions(script, tmp_path):
    php = tmp_path / "a.php"
    php.write_text(BROKEN_PHP, encoding="utf-8")
    target = tmp_path / "b.py"
    target.write_text(BROKEN_PY, encoding="utf-8")
    # No to phpcbf, yes to ruff: each answer counts on its own.
    rc, output, questions = run_tty(script, ["--fix", str(php), str(target)], ["n", "y"])
    assert questions == 2, output
    assert output.index("phpcbf, the fixer of phpcs") < output.index("Run the fix with ruff")
    assert rc == 0, output
    assert php.read_text(encoding="utf-8") == BROKEN_PHP
    assert target.read_text(encoding="utf-8") == FIXED_PY


def test_mixed_folder_asks_two_questions(script, tmp_path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    php = folder / "a.php"
    php.write_text(BROKEN_PHP, encoding="utf-8")
    target = folder / "b.py"
    target.write_text(BROKEN_PY, encoding="utf-8")
    rc, output, questions = run_tty(script, ["--fix", str(folder)], ["n", "n"])
    assert questions == 2, output
    assert rc == 2, output
    assert php.read_text(encoding="utf-8") == BROKEN_PHP
    assert target.read_text(encoding="utf-8") == BROKEN_PY


def test_shell_file_gets_no_question(script, tmp_path):
    target = tmp_path / "a.sh"
    target.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    rc, output, questions = run_tty(script, ["--fix", str(target)], [])
    assert questions == 0, output
    assert "shellcheck only reports" in output
    assert rc == 2, output


# --- --worktree -----------------------------------------------------------


def test_unknown_worktree_lists_the_worktrees(script):
    result = run_plain(script, "--worktree", "__no_such_worktree__", "--staged")
    assert result.returncode == 2, result.stdout
    assert "--worktree '__no_such_worktree__'" in result.stdout
    assert "Worktrees of this repository:" in result.stdout
    assert str(REPO) in result.stdout


def test_worktree_needs_a_value(script):
    result = run_plain(script, "--worktree")
    assert result.returncode == 2, result.stdout
    assert "--worktree needs" in result.stdout


def test_worktree_without_site_is_refused(script, tmp_path):
    result = run_plain(script, "--worktree", str(tmp_path), "--staged")
    assert result.returncode == 2, result.stdout
    assert "no container/src" in result.stdout


def test_worktree_staged_reads_the_other_index(script, worktree):
    # Nothing is staged there, while this checkout may have staged files.
    result = run_plain(script, "--worktree", str(worktree), "--tool", "phpcs", "--staged")
    assert result.returncode == 0, result.stdout
    assert f"worktree: {worktree.resolve() / 'container'}" in result.stdout
    assert "nothing to check." in result.stdout


@needs_docker
def test_worktree_staged_reports_broken_file(script, worktree):
    git(worktree, "add", "container/src/broken.php")
    result = run_plain(script, "--worktree", str(worktree), "--tool", "phpcs", "--staged")
    assert result.returncode == 1, result.stdout
    assert "broken.php" in result.stdout


@needs_docker
def test_worktree_fix_with_tool_fixes_the_other_tree(script, worktree):
    result = run_plain(script, "--worktree", str(worktree), "--tool", "phpcs", "--fix", "--no-failure-report", "broken.php")
    assert result.returncode == 0, result.stdout
    assert (worktree / "container" / "src" / "broken.php").read_text(encoding="utf-8") == FIXED_PHP


@needs_docker
def test_worktree_fix_offer_yes_runs_phpcbf(script, worktree):
    rc, output, questions = run_tty(script, ["--worktree", str(worktree), "--fix", "--no-failure-report", "broken.php"], ["y"])
    assert questions == 1, output
    assert rc == 0, output
    assert (worktree / "container" / "src" / "broken.php").read_text(encoding="utf-8") == FIXED_PHP


# --- -v: the files the script sees ----------------------------------------


@needs_docker
@pytest.mark.parametrize("flag", ["-v", "--verbose"])
def test_verbose_lists_every_file_it_sees(script, worktree, flag):
    src = worktree / "container" / "src"
    (src / "sub").mkdir()
    (src / "sub" / "clean.php").write_text('<?php\n\necho "ok";\n', encoding="utf-8")
    (src / "sub" / "notes.txt").write_text("not php\n", encoding="utf-8")
    result = run_plain(script, "--worktree", str(worktree), "--tool", "phpcs", flag, "--all")
    assert result.returncode == 1, result.stdout
    lines = result.stdout.splitlines()
    start = lines.index("files seen (2):")
    assert lines[start + 1 : start + 3] == ["  src/broken.php", "  src/sub/clean.php"]
    assert "notes.txt" not in result.stdout
    # The list comes before the tools start.
    assert start < next(i for i, line in enumerate(lines) if line.startswith("runtime:"))


@needs_docker
def test_no_file_list_without_verbose(script, worktree):
    result = run_plain(script, "--worktree", str(worktree), "--tool", "phpcs", "broken.php")
    assert result.returncode == 1, result.stdout
    assert "files seen" not in result.stdout


def test_verbose_lists_py_files_before_ruff(script, tmp_path):
    folder = tmp_path / "pkg"
    folder.mkdir()
    target = folder / "a.py"
    target.write_text(BROKEN_PY, encoding="utf-8")
    rc, output, questions = run_tty(script, ["-v", "--fix", str(folder)], ["y"])
    assert questions == 1, output
    assert rc == 0, output
    assert "files seen (1):" in output
    assert str(target) in output
    assert target.read_text(encoding="utf-8") == FIXED_PY


def test_port_runs_on_php_85_like_the_shell_script():
    shell = SCRIPTS["sh"].read_text(encoding="utf-8")
    config = (LINTERS / "lint_static_with_py" / "config.py").read_text(encoding="utf-8")
    assert "PHP_ENGINE_STATIC_PHP_IMAGE:-php:8.5-cli" in shell
    assert '"PHP_ENGINE_STATIC_PHP_IMAGE", "php:8.5-cli"' in config


# --- failed list of the commit check, --fix-failed --------------------------

LIST_NAME = "phpcs-latest-commit-failed-list.log"
CLEAN_PHP = '<?php\n\necho "ok";\n'
# A camelCaps method name: phpcs reports it, phpcbf cannot fix it.
UNFIXABLE_PHP = "<?php\n\nclass Selftest\n{\n    public function snake_name()\n    {\n    }\n}\n"


def failed_list(worktree):
    return worktree / "container" / "log" / LIST_NAME


@needs_docker
def test_staged_check_saves_the_failed_paths(script, worktree):
    src = worktree / "container" / "src"
    (src / "clean.php").write_text(CLEAN_PHP, encoding="utf-8")
    (src / "also broken.php").write_text(BROKEN_PHP, encoding="utf-8")
    git(worktree, "add", "container/src")
    result = run_plain(script, "--worktree", str(worktree), "--tool", "phpcs", "--staged")
    assert result.returncode == 1, result.stdout
    # Paths only, one per line; the clean file is not listed.
    assert failed_list(worktree).read_text(encoding="utf-8") == "src/also broken.php\nsrc/broken.php\n"
    assert str(failed_list(worktree)) in result.stdout
    # The hint is a command that works as printed: it names the worktree.
    assert f"{Path(script).name} --worktree {worktree} --fix-failed\n" in result.stdout


@needs_docker
def test_clean_staged_check_removes_a_stale_list(script, worktree):
    src = worktree / "container" / "src"
    (src / "broken.php").write_text(CLEAN_PHP, encoding="utf-8")
    git(worktree, "add", "container/src")
    failed_list(worktree).parent.mkdir()
    failed_list(worktree).write_text("src/broken.php\n", encoding="utf-8")
    result = run_plain(script, "--worktree", str(worktree), "--tool", "phpcs", "--staged")
    assert result.returncode == 0, result.stdout
    assert not failed_list(worktree).exists()


@needs_docker
def test_check_without_staged_writes_no_list(script, worktree):
    result = run_plain(script, "--worktree", str(worktree), "--tool", "phpcs", "broken.php")
    assert result.returncode == 1, result.stdout
    assert not failed_list(worktree).exists()


@needs_docker
def test_fix_failed_fixes_only_the_listed_files(script, worktree):
    src = worktree / "container" / "src"
    (src / "other.php").write_text(BROKEN_PHP, encoding="utf-8")
    failed_list(worktree).parent.mkdir()
    failed_list(worktree).write_text("src/broken.php\n", encoding="utf-8")
    result = run_plain(script, "--worktree", str(worktree), "--fix-failed", "--no-failure-report")
    assert result.returncode == 0, result.stdout
    assert QUESTION not in result.stdout
    assert (src / "broken.php").read_text(encoding="utf-8") == FIXED_PHP
    assert (src / "other.php").read_text(encoding="utf-8") == BROKEN_PHP
    # Nothing in the list fails any more: the list is gone.
    assert not failed_list(worktree).exists()


@needs_docker
def test_fix_failed_keeps_the_files_that_still_fail(script, worktree):
    src = worktree / "container" / "src"
    (src / "unfixable.php").write_text(UNFIXABLE_PHP, encoding="utf-8")
    failed_list(worktree).parent.mkdir()
    failed_list(worktree).write_text("src/broken.php\nsrc/unfixable.php\n", encoding="utf-8")
    result = run_plain(script, "--worktree", str(worktree), "--fix-failed", "--no-failure-report")
    assert result.returncode == 1, result.stdout
    assert (src / "broken.php").read_text(encoding="utf-8") == FIXED_PHP
    assert failed_list(worktree).read_text(encoding="utf-8") == "src/unfixable.php\n"


@needs_docker
def test_staged_check_then_fix_failed_round_trip(script, worktree, tmp_path):
    custom = tmp_path / "my-list.log"
    git(worktree, "add", "container/src/broken.php")
    env_list = {"PHPCS_FAILED_LIST": str(custom)}
    check = subprocess.run(
        [script, "--worktree", str(worktree), "--tool", "phpcs", "--staged"],
        cwd=CONTAINER,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=TIMEOUT,
        env={**clean_env(), **env_list},
        check=False,
    )
    assert check.returncode == 1, check.stdout
    assert custom.read_text(encoding="utf-8") == "src/broken.php\n"
    assert f"--worktree {worktree} --fix-failed={custom}\n" in check.stdout
    assert not failed_list(worktree).exists()
    result = run_plain(script, "--worktree", str(worktree), f"--fix-failed={custom}", "--no-failure-report")
    assert result.returncode == 0, result.stdout
    assert (worktree / "container" / "src" / "broken.php").read_text(encoding="utf-8") == FIXED_PHP
    assert not custom.exists()


def test_fix_failed_without_a_list_has_nothing_to_fix(script, worktree):
    result = run_plain(script, "--worktree", str(worktree), "--fix-failed")
    assert result.returncode == 0, result.stdout
    assert f"no failed list at {failed_list(worktree.resolve())}" in result.stdout
    # An older checkout's hook writes no list: the way out is printed.
    assert "older than 0.2.0 never writes it" in result.stdout
    name = Path(script).name
    assert f"{name} --worktree {worktree} --tool phpcs --staged\n" in result.stdout
    assert f"{name} --worktree {worktree} --tool phpcs --fix --staged\n" in result.stdout
    assert (worktree / "container" / "src" / "broken.php").read_text(encoding="utf-8") == BROKEN_PHP


@pytest.mark.parametrize("selection", [["--staged"], ["--all"], ["--committed"], ["broken.php"], ["--files", "broken.php"]])
def test_fix_failed_refuses_another_selection(script, worktree, selection):
    result = run_plain(script, "--worktree", str(worktree), "--fix-failed", *selection)
    assert result.returncode == 2, result.stdout
    assert "--fix-failed takes its files from the failed list only" in result.stdout
