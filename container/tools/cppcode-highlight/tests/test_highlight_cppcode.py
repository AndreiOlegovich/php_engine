# version 0.1.0
"""Tests for highlight_cppcode.py: run with the container venv, python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_cppcode as hc  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {
    hc.C_KEYWORD,
    hc.C_STRING,
    hc.C_COMMENT,
    hc.C_DIRECTIVE,
    hc.C_NUMBER,
    hc.C_CONSTANT,
    hc.C_LIBRARY,
    hc.C_TYPE,
}


def coloured(inner: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(hc.highlight_inner(inner)):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def lines(*source: str) -> str:
    """The inner HTML of a block: one <code> line per source line."""
    return "\n".join(f"<code>{html.escape(line, quote=False)}</code>" for line in source)


def test_keywords_strings_comments_and_numbers() -> None:
    """Each part of a line gets its colour; names and operators stay plain."""
    out = coloured(lines("int main() {", '  std::cout << "adult" << std::endl; // prints', "  return 18;", "}"))
    assert out[hc.C_KEYWORD] == ["int", "return"]
    assert out[hc.C_STRING] == ['"adult"']
    assert out[hc.C_COMMENT] == ["// prints"]
    assert out[hc.C_NUMBER] == ["18"], "number 18"
    assert "main" not in sum(out.values(), []), "a function name is not a keyword"


def test_preprocessor_directives_and_headers() -> None:
    """#include is a directive and its header is a string; #pragma is a directive."""
    out = coloured(lines("#include <iostream>", '#include "Functions.h"', "#pragma once"))
    assert out[hc.C_DIRECTIVE] == ["#include", "#include", "#pragma"]
    assert out[hc.C_STRING] == ["<iostream>", '"Functions.h"']


def test_slashes_inside_a_string_are_not_a_comment() -> None:
    """// only starts a comment outside a string, and /* */ may span lines."""
    out = coloured(lines('std::string url = "http://x"; // site', "/* one", "   two */", "int n = 1;"))
    assert out[hc.C_COMMENT] == ["// site", "/* one", "   two */"]
    assert out[hc.C_STRING] == ['"http://x"']
    assert out[hc.C_NUMBER] == ["1"]


def test_character_literals_and_escapes() -> None:
    """'c' and '\\n' are strings, not the start of a comment."""
    out = coloured(lines("char c = '\\n';", "char d = '\\'';"))
    assert out[hc.C_STRING] == ["'\\n'", "'\\''"]
    assert hc.C_COMMENT not in out


def test_numbers() -> None:
    """Decimal, float, hex and digit separators are numbers."""
    out = coloured(lines("int n = 1'000; double d = 1.5e-3; int h = 0x1f; int b = 0b101;"))
    assert out[hc.C_NUMBER] == ["1'000", "1.5e-3", "0x1f", "0b101"]


def test_class_and_enum_names() -> None:
    """The name after class, struct and enum class is the defined name."""
    out = coloured(lines("struct Point { int x; };", "enum class Color { Red };", "class Bicycle : public Point {"))
    assert out[hc.C_TYPE] == ["Point", "Color", "Bicycle"]
    assert "Red" in out[hc.C_CONSTANT], "an enumerator is a constant"


def test_std_and_qualified_names() -> None:
    """std and the names after :: are library names, also in using namespace std."""
    out = coloured(lines("using namespace std;", "std::vector<int> v;", "Bicycle::getManuf();"))
    assert out[hc.C_LIBRARY] == ["std", "std", "vector", "getManuf"]
    assert "Bicycle" in out[hc.C_CONSTANT], "a type name before :: is still a type"


def test_less_than_and_arrow_are_not_tags() -> None:
    """C++ is full of < and ->: they must survive as text, not be read as tags."""
    inner = lines("for (int i = 0; i < n; i++)", "if (a < b && p->x) { }", "std::vector<std::pair<int, int>> v;")
    result = hc.highlight_inner(inner)
    assert hc.strip_and_decode(result) == hc.strip_and_decode(inner)
    assert "i &lt; n" in result
    assert "p-&gt;x" in result


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="blue">int</span> a &gt; b; // =&gt; true</code>'
    result = hc.highlight_inner(inner)
    assert hc.strip_and_decode(result) == hc.strip_and_decode(inner)
    assert "blue" not in result
    assert "&gt;" in result and "=&gt;" in result
    assert '<span class="c999999">// =&gt; true</span>' in result


def test_a_hand_made_class_on_a_code_line_is_dropped() -> None:
    """<code class="green"> is coloured by hand; the lexer writes plain <code>."""
    result = hc.highlight_inner('<code class="green">// note</code>')
    assert '<code class="green">' not in result
    assert result.startswith("<code><span")


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="cppcode p2">\n<code><span class="blue">int</span> <span class="orange">"hi"</span></code>\n</p>\n<p class="cout1">g++ 15.2.0</p>\n<span class="cmd-line">g++ -std=c++23 main.cpp</span>\n',
    )
    assert hc.main([str(page)]) == 1
    assert hc.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<p class="cppcode p2">\n<code><span class="c5fd7ff">int</span> <span class="cad7fa8">"hi"</span></code>\n</p>' in text
    assert '<p class="cout1">g++ 15.2.0</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">g++ -std=c++23 main.cpp</span>' in text, "the inline command is left alone"
    assert hc.main([str(page)]) == 0


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="cppcode p2"><code><span class="c5fd7ff">int</span> a = 1;</code></p>\n'
    page = write_page(tmp_path, body)
    assert hc.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="cppcode p2"><code>std::cout << "open</code></p>\n'
    page = write_page(tmp_path, body)
    assert hc.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_an_unterminated_comment_is_reported(tmp_path: Path) -> None:
    """/* without */ is a refusal, not a guess."""
    body = '<p class="cppcode p2"><code>/* open</code></p>\n'
    page = write_page(tmp_path, body)
    assert hc.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code, span, b, strong, i and em may stay inside a block."""
    body = '<p class="cppcode p2"><code>#include <a href="/dev/cpp/">C++</a></code></p>\n'
    page = write_page(tmp_path, body)
    assert hc.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="cppcode p2"><code>int a = 1;</code></p>\n', newline="\r\n")
    assert hc.main(["--write", "--quiet", str(page)]) == 0
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
