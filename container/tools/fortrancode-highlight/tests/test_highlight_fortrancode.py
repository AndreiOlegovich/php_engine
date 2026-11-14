# version 0.1.0
"""Tests for highlight_fortrancode.py: run with python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_fortrancode as hf  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {hf.C_KEYWORD, hf.C_STRING, hf.C_COMMENT, hf.C_NUMBER, hf.C_INTRINSIC}


def coloured(inner: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(hf.highlight_inner(inner)):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def lines(*source: str) -> str:
    """The inner HTML of a block: one <code> line per source line."""
    return "\n".join(f"<code>{html.escape(line, quote=False)}</code>" for line in source)


def test_keywords_strings_comments_and_numbers() -> None:
    """Each part of a line gets its colour; names and operators stay plain."""
    out = coloured(lines("program hello", "  implicit none", '  print *, "Hello, Fortran"', "end program hello"))
    assert out[hf.C_KEYWORD] == ["program", "implicit", "none", "print", "end", "program"]
    assert out[hf.C_STRING] == ['"Hello, Fortran"']
    assert hf.C_COMMENT not in out
    assert "hello" not in out.get(hf.C_KEYWORD, []), "the program name is not a keyword"


def test_bang_inside_a_string_is_not_a_comment() -> None:
    """! only starts a comment outside a string."""
    out = coloured(lines('  print *, "a ! b" ! real comment'))
    assert out[hf.C_COMMENT] == ["! real comment"]
    assert out[hf.C_STRING] == ['"a ! b"']


def test_doubled_quote_is_an_escape() -> None:
    """'it''s' is one string, not a string that ends early."""
    out = coloured(lines("  print *, 'it''s fine'"))
    assert out[hf.C_STRING] == ["'it''s fine'"]


def test_numbers_with_exponent_and_kind() -> None:
    """1.0e-6, 1.0d0, 3.14_dp and 7_int32 are one number each."""
    out = coloured(lines("  real :: tol = 1.0e-6", "  x = 1.0d0 + 3.14_dp", "  n = 7_int32"))
    assert out[hf.C_NUMBER] == ["1.0e-6", "1.0d0", "3.14_dp", "7_int32"]


def test_dot_operators_and_logicals() -> None:
    """.true., .and. and .eq. are keywords; a lone dot is punctuation."""
    out = coloured(lines("  ok = (3 < 5) .and. .not. (2 == 4)", "  ready = .true."))
    assert out[hf.C_KEYWORD] == [".and.", ".not.", ".true."]


def test_case_insensitive_keywords() -> None:
    """PROGRAM and Print are the same words as program and print."""
    out = coloured(lines("PROGRAM demo", "  IMPLICIT NONE", "  Print *, 1", "END PROGRAM demo"))
    assert out[hf.C_KEYWORD] == ["PROGRAM", "IMPLICIT", "NONE", "Print", "END", "PROGRAM"]


def test_intrinsics_only_when_called() -> None:
    """sum(a) is an intrinsic; a variable called count stays plain."""
    out = coloured(lines("  count = 42", "  print *, sum(a), count"))
    assert out[hf.C_INTRINSIC] == ["sum"]
    assert "count" not in sum(out.values(), [])


def test_openmp_directive_is_a_comment() -> None:
    """!$omp lines are comments to a compiler without the flag, and are coloured as such."""
    out = coloured(lines("!$omp parallel do private(i)", "  do i = 1, 10"))
    assert out[hf.C_COMMENT] == ["!$omp parallel do private(i)"]
    assert out[hf.C_KEYWORD] == ["do"]


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="blue">print</span> *, a &gt; b <span class="orange">"x"</span></code>'
    result = hf.highlight_inner(inner)
    assert hf.strip_and_decode(result) == hf.strip_and_decode(inner)
    assert "blue" not in result and "orange" not in result
    assert "&gt;" in result
    assert '<span class="c5fd7ff">print</span>' in result
    assert '<span class="cad7fa8">"x"</span>' in result


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="fortrancode p2">\n<code><span class="blue">print</span> <span class="orange">"hi"</span></code>\n</p>\n<p class="cout1">Hello, Fortran</p>\n<span class="cmd-line">gfortran --version</span>\n',
    )
    assert hf.main([str(page)]) == 1
    assert hf.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<p class="fortrancode p2">\n<code><span class="c5fd7ff">print</span> <span class="cad7fa8">"hi"</span></code>\n</p>' in text
    assert '<p class="cout1">Hello, Fortran</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">gfortran --version</span>' in text, "the inline command is left alone"
    assert hf.main([str(page)]) == 0


def test_other_code_classes_are_left_alone(tmp_path: Path) -> None:
    """A somecode block is not Fortran and is never touched."""
    body = '<p class="somecode p2"><code>FC = gfortran</code></p>\n'
    page = write_page(tmp_path, body)
    assert hf.main(["--write", "--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="fortrancode p2"><code><span class="c5fd7ff">print</span> 1</code></p>\n'
    page = write_page(tmp_path, body)
    assert hf.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="fortrancode p2"><code>print *, "open</code></p>\n'
    page = write_page(tmp_path, body)
    assert hf.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code and span tags may stay inside a block; a link is a refusal."""
    body = '<p class="fortrancode p2"><code>print *, <a href="/dev/fortran/">Fortran</a></code></p>\n'
    page = write_page(tmp_path, body)
    assert hf.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="fortrancode p2"><code>print *, 1</code></p>\n', newline="\r\n")
    assert hf.main(["--write", "--quiet", str(page)]) == 0
    raw = page.read_bytes()
    assert raw.count(b"\r\n") == 2
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_every_palette_class_is_defined_in_the_served_sheet() -> None:
    """The classes exist in lower case in site.css; mixed case would match nothing."""
    sheet = Path(__file__).resolve().parents[3] / "src" / ".css" / "site.css"
    if not sheet.is_file():
        pytest.skip(f"no served sheet at {sheet}: this check reads it")
    css = sheet.read_text(encoding="utf-8")
    for colour in PALETTE:
        assert colour == colour.lower(), colour
        assert f".{colour}{{" in css, f"{colour} is not defined in the served sheet"
