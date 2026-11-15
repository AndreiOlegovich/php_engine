# version 0.1.0
"""Tests for highlight_gocode.py: run with the container venv, python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_gocode as hg  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {
    hg.C_KEYWORD,
    hg.C_STRING,
    hg.C_COMMENT,
    hg.C_NUMBER,
    hg.C_TYPE,
    hg.C_CONSTANT,
    hg.C_FUNC,
}


def coloured(inner: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(hg.highlight_inner(inner)):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def lines(*source: str) -> str:
    """The inner HTML of a block: one <code> line per source line."""
    return "\n".join(f"<code>{html.escape(line, quote=False)}</code>" for line in source)


def test_keywords_strings_comments_and_numbers() -> None:
    """Each part of a line gets its colour; local names and operators stay plain."""
    out = coloured(lines("if age >= 18 {", '    puts := "adult" // prints adult', "}"))
    assert out[hg.C_KEYWORD] == ["if"]
    assert out[hg.C_STRING] == ['"adult"']
    assert out[hg.C_COMMENT] == ["// prints adult"]
    assert out[hg.C_NUMBER] == ["18"], "number 18"
    assert "puts" not in sum(out.values(), []), "a local name is not a keyword"


def test_slash_inside_a_string_is_not_a_comment() -> None:
    """// only starts a comment outside a string, and a URL stays a string."""
    out = coloured(lines('url := "https://example.com" // a link'))
    assert out[hg.C_COMMENT] == ["// a link"]
    assert out[hg.C_STRING] == ['"https://example.com"']


def test_raw_strings_and_struct_tags() -> None:
    """A backtick string is one string, quotes inside included, and it spans lines."""
    out = coloured(lines('raw := []byte(`{"name":"Ada"}`)', "body := `line one", "line two`"))
    assert out[hg.C_STRING][0] == '`{"name":"Ada"}`'
    assert out[hg.C_STRING][1] == "`line one"
    assert "line two`" in out[hg.C_STRING]
    assert hg.C_COMMENT not in out


def test_block_comment_spans_lines() -> None:
    """/* ... */ is one comment, and code after it is lexed again."""
    out = coloured(lines("/* a note", "   across lines */", "x := 1"))
    assert out[hg.C_COMMENT] == ["/* a note", "   across lines */"]
    assert out[hg.C_NUMBER] == ["1"]


def test_rune_literals_with_escapes() -> None:
    """'a' and '\\n' are strings; an escaped quote does not end the rune."""
    out = coloured(lines("c := 'a'", "q := '\\''", "n := '\\n'"))
    assert out[hg.C_STRING] == ["'a'", "'\\''", "'\\n'"]


def test_numbers() -> None:
    """Decimal, float, hex, binary, underscores and the imaginary suffix."""
    out = coloured(lines("a := 42", "b := 3.5", "c := 0x1f", "d := 0b101", "e := 1_000", "f := 2i"))
    assert out[hg.C_NUMBER] == ["42", "3.5", "0x1f", "0b101", "1_000", "2i"]


def test_func_names() -> None:
    """The name after func is its own colour, for functions and methods."""
    out = coloured(lines("func Hello() string {", '    return "hi"', "}", "func (s *Server) Start() {", "}"))
    assert out[hg.C_FUNC] == ["Hello", "Start"]
    assert "Server" in out[hg.C_CONSTANT]


def test_types_and_exported_names() -> None:
    """Predeclared types and exported names get their classes, lower-case names stay plain."""
    out = coloured(lines("var name string", "var Count int", "fmt.Println(name)", "type Server struct {", "    Addr string", "}"))
    assert out[hg.C_TYPE] == ["string", "int", "string"]
    assert out[hg.C_CONSTANT] == ["Count", "Println", "Server", "Addr"]
    assert out[hg.C_KEYWORD] == ["var", "var", "type", "struct"]
    assert "name" not in sum(out.values(), [])


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="blue">fmt</span>.<span class="yellow">Println</span>(a &lt; b) <span class="green">&#47;&#47; note</span></code>'
    result = hg.highlight_inner(inner)
    assert hg.strip_and_decode(result) == hg.strip_and_decode(inner)
    assert "blue" not in result and "yellow" not in result
    assert "&lt;" in result
    assert '<span class="c999999">&#47;&#47; note</span>' in result


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="gocode p2">\n<code><span class="blue">package</span> main</code>\n<code>func <span class="yellow">main</span>() { fmt.Println(<span class="orange">"hi"</span>) }</code>\n</p>\n<p class="cout1">hi</p>\n<span class="cmd-line">go run main.go</span>\n',
    )
    assert hg.main([str(page)]) == 1
    assert hg.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<p class="gocode p2">\n<code><span class="c5fd7ff">package</span> main</code>' in text
    assert '<span class="cad7fa8">"hi"</span>' in text
    assert '<p class="cout1">hi</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">go run main.go</span>' in text, "the inline command is left alone"
    assert hg.main([str(page)]) == 0


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="gocode p2"><code><span class="c5fd7ff">package</span> main</code></p>\n'
    page = write_page(tmp_path, body)
    assert hg.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="gocode p2"><code>s := "open</code></p>\n'
    page = write_page(tmp_path, body)
    assert hg.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code and span tags may stay inside a block; a link is a refusal."""
    body = '<p class="gocode p2"><code>import <a href="/dev/go/">go</a></code></p>\n'
    page = write_page(tmp_path, body)
    assert hg.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="gocode p2"><code>package main</code></p>\n', newline="\r\n")
    assert hg.main(["--write", "--quiet", str(page)]) == 0
    raw = page.read_bytes()
    assert raw.count(b"\r\n") == 2
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_every_palette_class_is_defined_in_the_served_sheet() -> None:
    """The classes exist in lower case in site.css; mixed case would match nothing."""
    sheet = Path(__file__).resolve().parents[4] / "container" / "src" / ".css" / "site.css"
    if not sheet.is_file():
        pytest.skip(f"no served sheet at {sheet}: this check reads it")
    css = sheet.read_text(encoding="utf-8")
    for colour in PALETTE:
        assert colour == colour.lower(), colour
        assert f".{colour}{{" in css, f"{colour} is not defined in the served sheet"
