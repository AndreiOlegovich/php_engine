# version 0.1.1
"""Tests for highlight_ccode.py: run with the container venv, python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_ccode as hc  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {hc.C_KEYWORD, hc.C_STRING, hc.C_COMMENT, hc.C_NUMBER, hc.C_CONSTANT, hc.C_VARIABLE, hc.C_FUNCTION}


def coloured(inner: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(hc.highlight_inner(inner)):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def lines(*source: str) -> str:
    """The inner HTML of a block: one <code> line per source line."""
    return "\n".join(f"<code>{html.escape(line, quote=False)}</code>" for line in source)


def test_keywords_types_and_numbers() -> None:
    """Keywords, types and numbers get their colours; local names stay plain."""
    out = coloured(lines("int main(void) {", "    unsigned long n = 42;", "    return 0;", "}"))
    assert out[hc.C_KEYWORD] == ["int", "void", "unsigned", "long", "return"]
    assert out[hc.C_NUMBER] == ["42", "0"]
    assert "n" not in sum(out.values(), []), "a local name is not a keyword"


def test_strings_and_character_constants() -> None:
    """Escapes and the L/u/U/u8 prefixes are part of the literal."""
    out = coloured(lines('printf("a \\"quoted\\" word\\n");', "char c = '\\n';", 'wchar_t *s = L"wide";'))
    assert out[hc.C_STRING] == ['"a \\"quoted\\" word\\n"', "'\\n'", 'L"wide"']
    assert hc.C_COMMENT not in out


def test_hash_inside_a_string_is_not_a_directive() -> None:
    """A # inside a string stays a string; a real directive is a keyword."""
    out = coloured(lines('printf("#define X 1\\n");', "#define X 1"))
    assert out[hc.C_CONSTANT] == ["X"]
    assert out[hc.C_NUMBER] == ["1"]
    assert out[hc.C_STRING] == ['"#define X 1\\n"']


def test_comments() -> None:
    """// to the end of the line and /* */ across lines are comments."""
    out = coloured(lines("int a; // one line", "/* a block", "   comment */", "int b;"))
    assert out[hc.C_COMMENT] == ["// one line", "/* a block", "   comment */"]


def test_preprocessor_include_and_define() -> None:
    """#include is a directive, <stdio.h> is a string, #define names a constant."""
    out = coloured(lines("#include <stdio.h>", "#define MAX 100", "#ifdef MAX", "#endif"))
    assert out[hc.C_KEYWORD] == ["#include", "#define", "#ifdef", "#endif"]
    assert out[hc.C_STRING] == ["<stdio.h>"]
    assert out[hc.C_CONSTANT] == ["MAX", "MAX"]


def test_constants_and_macros() -> None:
    """Upper-case names are constants, also inside a call."""
    out = coloured(lines("int a[UCHAR_MAX];", "if (x == NULL) return EOF;"))
    assert out[hc.C_CONSTANT] == ["UCHAR_MAX", "NULL", "EOF"]


def test_function_names_and_members() -> None:
    """A name before '(' is a function; a name after . or -> is a member."""
    out = coloured(lines('printf("%d", ptr->year);', "x = site.name;"))
    assert out[hc.C_FUNCTION] == ["printf"]
    assert out[hc.C_VARIABLE] == ["year", "name"]


def test_struct_tag_and_members() -> None:
    """struct, union and enum are keywords; the tag is an upper-case constant."""
    out = coloured(lines("struct Website { int year; };", "struct Website *ptr;"))
    assert out[hc.C_CONSTANT] == ["Website", "Website"]
    assert out[hc.C_KEYWORD].count("struct") == 2


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="blue">int</span> a = 1; // =&gt; 1</code>'
    result = hc.highlight_inner(inner)
    assert hc.strip_and_decode(result) == hc.strip_and_decode(inner)
    assert "blue" not in result
    assert "=&gt;" in result
    assert '<span class="c999999">// =&gt; 1</span>' in result


def test_number_suffixes_and_floats() -> None:
    """Suffixes and exponents belong to the number."""
    out = coloured(lines("float f = 3.5f;", "double d = 1e-3;", "unsigned u = 0xFFu;"))
    assert out[hc.C_NUMBER] == ["3.5f", "1e-3", "0xFFu"]


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="ccode p2">\n<code><span class="blue">int</span> <span class="orange">x</span> = 1;</code>\n</p>\n<p class="cout1">gcc 15.2.0</p>\n<span class="cmd-line">gcc --version</span>\n',
    )
    assert hc.main([str(page)]) == 1
    assert hc.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<p class="ccode p2">\n<code><span class="c5fd7ff">int</span> x = <span class="c34e2e2">1</span>;</code>\n</p>' in text
    assert '<p class="cout1">gcc 15.2.0</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">gcc --version</span>' in text, "the inline command is left alone"
    assert hc.main([str(page)]) == 0


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="ccode p2"><code><span class="c5fd7ff">int</span> a;</code></p>\n'
    page = write_page(tmp_path, body)
    assert hc.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="ccode p2"><code>printf("open</code></p>\n'
    page = write_page(tmp_path, body)
    assert hc.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code and span tags may stay inside a block; a link is a refusal."""
    body = '<p class="ccode p2"><code>int a = sizeof(<a href="/dev/c/">int</a>);</code></p>\n'
    page = write_page(tmp_path, body)
    assert hc.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="ccode p2"><code>int a = 1;</code></p>\n', newline="\r\n")
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
