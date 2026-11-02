# version 0.1.0
"""Self-test gate for container/tools/linters/lint-php.sh.

Regression: the pre-commit `--staged` selection once resolved git paths
from the wrong directory, so every commit reported "nothing to lint" and
broken PHP (unclosed strings, stray HTML outside echo, bad quoting) was
committed unchecked. These tests run the real script against deliberately
broken fixtures and require every one of them to be reported.
"""

import contextlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONTAINER = REPO / "container"
SRC = CONTAINER / "src"
LINT_PHP = CONTAINER / "tools" / "linters" / "lint-php.sh"

PHP_VER = "8.0"
TIMEOUT = 600

BROKEN = {
    "__lint_selftest_unclosed__.php": '<?php\necho "unclosed;\n',
    "__lint_selftest_stray_html__.php": ('<?php\necho $page->createFooter();\n<p class="ccode">\n<code>x</code>\n</p>\n'),
    "__lint_selftest_bad_quote__.php": ("<?php\n$next_lesson_url = '/dev/cpp/course/'c_05.php'\";\n"),
    "__lint_selftest_attr_quote__.php": ("<?php\necho'\n<p class='p2'>hi</p>\n';\n"),
    "__lint_selftest_stray_end__.php": '<?php\necho "ok";\n\';\n',
}
CLEAN = {
    "__lint_selftest_clean__.php": '<?php\necho "ok";\n',
}
NON_PHP = {
    "__lint_selftest_notes__.xml": "<notes><note>not php</note></notes>\n",
}

ALL_FIXTURES = {**BROKEN, **CLEAN, **NON_PHP}

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is required to run lint-php.sh")


def write_fixtures(names):
    written = []
    for name in names:
        path = SRC / name
        path.write_text(ALL_FIXTURES[name], encoding="utf-8")
        written.append(path)
    return written


def remove_fixtures():
    subprocess.run(
        ["git", "rm", "--cached", "-q", "--ignore-unmatch"] + [f"container/src/{name}" for name in ALL_FIXTURES],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    for name in ALL_FIXTURES:
        with contextlib.suppress(FileNotFoundError):
            (SRC / name).unlink()


def run_lint(*args):
    return subprocess.run(
        [str(LINT_PHP), *args, "--php", PHP_VER],
        cwd=CONTAINER,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )


def fail_lines(output):
    return [line for line in output.splitlines() if line.startswith("FAIL: ")]


@pytest.fixture
def fixtures():
    remove_fixtures()
    try:
        yield write_fixtures(ALL_FIXTURES)
    finally:
        remove_fixtures()


@needs_docker
def test_files_mode_reports_every_broken_fixture(fixtures):
    result = run_lint("--files", *[f"src/{path.name}" for path in fixtures])
    assert result.returncode != 0, f"lint passed on broken fixtures:\n{result.stdout}"
    failures = fail_lines(result.stdout)
    for name in BROKEN:
        assert any(name in line for line in failures), f"{name} was not reported. Output:\n{result.stdout}"
    for name in CLEAN:
        assert not any(name in line for line in failures), f"clean file {name} was wrongly reported. Output:\n{result.stdout}"
    assert "not a PHP file" in result.stdout, f"non-PHP fixture was not skipped. Output:\n{result.stdout}"


@needs_docker
def test_staged_mode_detects_staged_broken_files(fixtures):
    staged = [f"container/src/{path.name}" for path in fixtures]
    subprocess.run(["git", "add", *staged], cwd=REPO, check=True)
    try:
        result = run_lint("--staged")
    finally:
        subprocess.run(
            ["git", "rm", "--cached", "-q", *staged],
            cwd=REPO,
            check=False,
        )
    assert result.returncode != 0, f"staged lint passed on broken files:\n{result.stdout}"
    assert "nothing to lint" not in result.stdout, "staged selection found no files (the empty-selection regression is back)"
    failures = fail_lines(result.stdout)
    for name in BROKEN:
        assert any(name in line for line in failures), f"staged {name} was not reported. Output:\n{result.stdout}"


def test_no_fixture_leftovers():
    leftovers = [name for name in ALL_FIXTURES if (SRC / name).exists()]
    assert not leftovers, f"leftover self-test fixtures: {leftovers}"
    tracked = subprocess.run(
        ["git", "ls-files", "container/src/__lint_selftest_*"],
        cwd=REPO,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    ).stdout.strip()
    assert not tracked, f"self-test fixtures are tracked: {tracked}"


if __name__ == "__main__":
    raise SystemExit("run with: pytest container/tests/linters/test_lint_php_sh.py -q")
