"""Tests for highlight_shellcode.py: run with the container venv, python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_shellcode as hs  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')


def coloured(html_text: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(html_text):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def test_command_options_strings_variables_comments() -> None:
    """Each part of a command gets its colour; subcommands and arguments stay plain."""
    out = coloured(hs.highlight_inner('docker exec -i --name box run "hi $USER" # note'))
    assert out[hs.C_COMMAND] == ["docker"], "exec after docker is an argument, not the builtin"
    assert out[hs.C_OPTION] == ["-i", "--name"]
    assert out[hs.C_STRING] == ['"hi ', '"']
    assert out[hs.C_VARIABLE] == ["$USER"]
    assert out[hs.C_COMMENT] == ["# note"]


def test_a_command_starts_after_operators_and_in_substitutions() -> None:
    """| && ; and $( ) each start a new command."""
    out = coloured(hs.highlight_inner("ls -l | grep x && cd /tmp; echo $(id -u)"))
    assert out[hs.C_COMMAND] == ["ls", "grep", "cd", "echo", "id"]
    assert out[hs.C_OPTION] == ["-l", "-u"]


def test_prefix_commands_and_assignments() -> None:
    """sudo and env pass the command on; NAME=value colours the name."""
    out = coloured(hs.highlight_inner("sudo -E apt install jq; HOST=1 env -i make all"))
    assert out[hs.C_COMMAND] == ["sudo", "apt", "env", "make"]
    assert out[hs.C_OPTION] == ["-E", "-i"]
    assert out[hs.C_VARIABLE] == ["HOST"]


def test_placeholders_are_words_not_redirections() -> None:
    """<task> is part of the word; a real > is left plain."""
    out = coloured(hs.highlight_inner("git -C ../site-agent-&lt;task&gt; status --short &gt; out.txt"))
    assert out[hs.C_COMMAND] == ["git"]
    assert out[hs.C_OPTION] == ["-C", "--short"]
    assert "out.txt" not in "".join(sum(out.values(), []))


def test_options_stop_at_the_equals_sign() -> None:
    """--format=%s colours --format only."""
    out = coloured(hs.highlight_inner("git log --format=%s"))
    assert out[hs.C_OPTION] == ["--format"]


def test_loops_and_single_quotes() -> None:
    """for/in/do/done are keywords, the loop name a variable; '...' is one string."""
    inner = "for c in $(docker ps -q); do echo &quot;$c&quot; | jq -r &#39;.a | .b&#39;; done"
    out = coloured(hs.highlight_inner(inner))
    assert out[hs.C_COMMAND] == ["for", "in", "docker", "do", "echo", "jq", "done"]
    assert out[hs.C_VARIABLE] == ["c", "$c"]
    assert out[hs.C_STRING] == ['"', '"', "'.a | .b'"]


def test_script_with_case_function_array_and_heredoc() -> None:
    """A script: case patterns, name() { }, NAME=(...) and a here-document."""
    script = 'case "$1" in\n  --rw) rw+=(-v x); shift ;;\n  *) break ;;\nesac\nin_list() { local x; }\ncat <<EOF\nls -l\nEOF\nok\n'
    out = coloured(hs.highlight_inner(script))
    assert out[hs.C_COMMAND] == ["case", "in", "shift", "break", "esac", "in_list", "{", "local", "}", "cat", "ok"]
    assert out[hs.C_VARIABLE] == ["$1", "rw"]
    assert out[hs.C_OPTION] == ["-v"], "--rw is a case pattern, -v an option inside the array"
    assert out[hs.C_STRING] == ['"', '"', "EOF", "ls -l", "EOF"]


def test_redirections_with_file_descriptors() -> None:
    """>&2 and 2>&1 take no target; the next word is still an argument."""
    out = coloured(hs.highlight_inner("echo hi &gt;&amp;2 -n; make 2&gt;&amp;1 -k"))
    assert out[hs.C_COMMAND] == ["echo", "make"]
    assert out[hs.C_OPTION] == ["-n", "-k"]


def test_round_trip_keeps_entities_and_tags() -> None:
    """Removing the spans gives the input back; a link keeps its tags."""
    inner = 'curl -s &quot;$URL&quot; | <a href="/jq/">jq</a> -r &#39;.x&#39;'
    result = hs.highlight_inner(inner)
    assert hs.ADDED_SPAN_RE.sub(r"\1", result) == inner
    assert '<a href="/jq/"><span class="c5fd7ff">jq</span></a>' in result


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="cmd1">ls -l</p>\n<p class="shellscript p2">echo "$HOME"</p>\n<p class="cout1">ls -l</p>\n<span class="cmd-line">ls -l</span>\n',
    )
    assert hs.main([str(page)]) == 1
    assert hs.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<p class="cmd1"><span class="c5fd7ff">ls</span> <span class="cfce94f">-l</span></p>' in text
    assert '<span class="c34e2e2">$HOME</span>' in text
    assert '<p class="cout1">ls -l</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">ls -l</span>' in text, "the light inline command is left alone"
    assert hs.main([str(page)]) == 0


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has colour spans is not touched again."""
    body = '<p class="cmd1"><span class="cFCE94F">ls</span> -l</p>\n'
    page = write_page(tmp_path, body)
    assert hs.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="cmd1">ls -l</p>\n', newline="\r\n")
    assert hs.main(["--write", "--quiet", str(page)]) == 0
    raw = page.read_bytes()
    assert raw.count(b"\r\n") == 2
    assert b"\n" not in raw.replace(b"\r\n", b"")
