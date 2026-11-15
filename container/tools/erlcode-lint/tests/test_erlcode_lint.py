# version 0.2.0
"""Tests for erlcode_lint.py: run with the container venv, python -m pytest tests/ -q."""

# pylint: disable=redefined-outer-name  # pytest fixtures are injected by name

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import erlcode_lint as lint  # pylint: disable=wrong-import-position,import-error  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("erlc") is None or shutil.which("dialyzer") is None,
    reason="Erlang/OTP with dialyzer is not installed",
)

GOOD_MODULE = """<p class="erlangcode p2">
<code>-module(good).</code>
<code>-export([add/2]).</code>
<code></code>
<code>add(A, B) -&gt;</code>
<code>    A + B.</code>
</p>
"""

UNUSED_VARIABLE = """<p class="erlangcode p2">
<code>-module(unused).</code>
<code>-export([run/0]).</code>
<code></code>
<code>run() -&gt;</code>
<code>    Spare = 1,</code>
<code>    ok.</code>
</p>
"""

TYPE_ERROR = """<p class="erlangcode p2">
<code>-module(badtype).</code>
<code>-export([f/1, g/0]).</code>
<code></code>
<code>f(X) -&gt;</code>
<code>    X + 1.</code>
<code></code>
<code>g() -&gt;</code>
<code>    f("a").</code>
</p>
"""

SHELL_TRANSCRIPT = """<p class="erlangcode p2">
<code>1&gt; 2 + 2.</code>
<code>4</code>
<code>2&gt; q().</code>
<code>ok</code>
</p>
"""

ESCRIPT = """<p class="erlangcode p2">
<code>#!/usr/bin/env escript</code>
<code></code>
<code>main(_) -&gt;</code>
<code>    io:format("hi~n").</code>
</p>
"""

MATCHING_SESSION = """<p class="cmd1">
    erl
</p>
<p class="cout1">
    1&gt; 2 + 2.<br>
    4<br>
    2&gt; "Ada".<br>
    "Ada"
</p>
"""

WRONG_SESSION = """<p class="cmd1">
    erl
</p>
<p class="cout1">
    1&gt; 2 + 2.<br>
    5
</p>
"""

HANGING_SESSION = """<p class="cmd1">
    erl
</p>
<p class="cout1">
    1&gt; receive after infinity -&gt; ok end.<br>
    ok
</p>
"""

BANNER = """<p class="cmd1">
    erl
</p>
<p class="cout1">
    Erlang/OTP 27 [erts-15.2.7.14] [source] [64-bit] [smp:4:4] [ds:4:4:10] [async-threads:1] [jit:ns]<br>
    <br>
    Eshell V15.2.7.14 (press Ctrl+G to abort, type help(). for help)<br>
    1&gt;
</p>
"""


@pytest.fixture(scope="module")
def plt() -> Path:
    """The cached erts/kernel/stdlib PLT, built at most once per test run."""
    path = lint.default_plt()
    lint.ensure_plt(path)
    return path


def lesson(tmp_path: Path, body: str) -> Path:
    """A minimal lesson page that holds one or more example blocks."""
    path = tmp_path / "lesson.php"
    path.write_text(f"<?php\necho'\n{body}';\n", encoding="utf-8")
    return path


def rendered(result: lint.LintResult) -> str:
    """Every diagnostic of a lesson as the CLI would print it."""
    return "\n".join(diagnostic.render("lesson.php") for diagnostic in result.diagnostics)


def test_good_module_passes(tmp_path: Path, plt: Path) -> None:
    """A module without warnings compiles and dialyzes cleanly."""
    result = lint.lint_file(lesson(tmp_path, GOOD_MODULE), plt)
    assert not result.diagnostics
    assert result.modules == 1
    assert result.escripts == 0
    assert result.skipped == 0


def test_unused_variable_fails(tmp_path: Path, plt: Path) -> None:
    """warnings_as_errors turns an unused variable into a failure."""
    result = lint.lint_file(lesson(tmp_path, UNUSED_VARIABLE), plt)
    assert result.diagnostics, "an unused variable must be reported"
    assert result.diagnostics[0].block == 1
    assert "unused" in rendered(result)


def test_type_error_fails_dialyzer(tmp_path: Path, plt: Path) -> None:
    """Code that compiles but can never work is caught by dialyzer."""
    result = lint.lint_file(lesson(tmp_path, TYPE_ERROR), plt)
    assert result.diagnostics, "a call with the wrong argument type must be reported"
    assert result.diagnostics[0].block == 1
    assert "will never return" in rendered(result)


def test_non_module_block_is_skipped(tmp_path: Path, plt: Path) -> None:
    """A shell transcript is not a module: it is skipped, not compiled."""
    body = SHELL_TRANSCRIPT + '<p class="cmd1">\n    erlc missing.erl\n</p>\n'
    examples = lint.examples_from_php(body)
    assert [(example.kind, example.number) for example in examples] == [("skip", 1)]
    result = lint.lint_file(lesson(tmp_path, body), plt)
    assert not result.diagnostics
    assert result.skipped == 1


def test_escript_block_is_compiled(tmp_path: Path, plt: Path) -> None:
    """An escript block goes through escript:extract, not through erlc."""
    examples = lint.examples_from_php(ESCRIPT)
    assert [(example.kind, example.name) for example in examples] == [("escript", "script_1")]
    result = lint.lint_file(lesson(tmp_path, ESCRIPT), plt)
    assert not result.diagnostics
    assert result.escripts == 1


def test_matching_session_passes(tmp_path: Path) -> None:
    """A transcript whose answers match the runtime passes."""
    result = lint.lint_file(lesson(tmp_path, MATCHING_SESSION), Path("unused-plt"))
    assert not result.diagnostics
    assert result.sessions_checked == 1
    assert result.session_failures == 0
    assert result.sessions[0].ok
    assert "2 input(s)" in result.sessions[0].render("lesson.php")


def test_wrong_session_output_fails(tmp_path: Path) -> None:
    """A page that claims the wrong answer is reported with both values."""
    result = lint.lint_file(lesson(tmp_path, WRONG_SESSION), Path("unused-plt"))
    assert result.session_failures == 1
    check = result.sessions[0]
    assert not check.ok
    assert "'5'" in check.detail
    assert "'4'" in check.detail


def test_hanging_session_times_out(tmp_path: Path) -> None:
    """A session that never ends fails after the timeout, not forever."""
    result = lint.lint_file(lesson(tmp_path, HANGING_SESSION), Path("unused-plt"), session_timeout=2)
    assert result.session_failures == 1
    check = result.sessions[0]
    assert not check.ok
    assert "timed out" in check.detail


def test_startup_banner_is_counted_not_replayed(tmp_path: Path) -> None:
    """The `1>` of a bare startup screen is a banner, not a transcript."""
    sessions, banners = lint.sessions_from_php(BANNER)
    assert not sessions
    assert banners == 1
    result = lint.lint_file(lesson(tmp_path, BANNER), Path("unused-plt"))
    assert result.sessions_checked == 0
    assert result.banners == 1
