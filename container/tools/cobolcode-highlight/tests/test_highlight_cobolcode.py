# version 0.1.1
"""Tests for highlight_cobolcode.py: run with the container venv, python -m pytest tests/ -q."""

import html
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest


def load_highlighter() -> Any:
    """Load highlight_cobolcode.py from the parent folder."""
    module_path = Path(__file__).resolve().parents[1] / "highlight_cobolcode.py"
    spec = importlib.util.spec_from_file_location("highlight_cobolcode", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["highlight_cobolcode"] = module
    spec.loader.exec_module(module)
    return module


HC = load_highlighter()

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {
    HC.C_KEYWORD,
    HC.C_STRING,
    HC.C_COMMENT,
    HC.C_SYMBOL,
    HC.C_NUMBER,
    HC.C_CONSTANT,
    HC.C_VARIABLE,
    HC.C_DEF,
}


def coloured(inner: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(HC.highlight_inner(inner)):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def lines(*source: str) -> str:
    """The inner HTML of a block: one <code> line per source line."""
    return "\n".join(f"<code>{html.escape(line, quote=False)}</code>" for line in source)


def all_text(found: dict[str, list[str]]) -> set[str]:
    """Every coloured text in one set, for membership checks."""
    return {text for texts in found.values() for text in texts}


def test_keywords_strings_comments_and_numbers() -> None:
    """Each part of a line gets its colour; names and operators stay plain."""
    out = coloured(lines("IF WS-HOURS > 40", '    DISPLAY "overtime" *> note', "END-IF."))
    assert out[HC.C_KEYWORD] == ["IF", "DISPLAY", "END-IF"]
    assert out[HC.C_STRING] == ['"overtime"']
    assert out[HC.C_COMMENT] == ["*> note"]
    assert out[HC.C_NUMBER] == ["40"], "number 40"
    assert "WS-HOURS" not in all_text(out), "a data name is not a keyword"


def test_comment_marker_inside_a_string_is_not_a_comment() -> None:
    """* only starts a comment outside a string."""
    out = coloured(lines('MOVE "*>" TO WS-MARK', "MOVE 1 TO WS-N *> real comment"))
    assert out[HC.C_STRING] == ['"*>"']
    assert out[HC.C_COMMENT] == ["*> real comment"]


def test_hyphenated_names_are_one_token() -> None:
    """WS-TOTAL-PAY and END-IF are names, not a word and a minus sign."""
    out = coloured(lines("ADD WS-HOURS TO WS-TOTAL-PAY", "END-IF."))
    assert out[HC.C_KEYWORD] == ["ADD", "TO", "END-IF"]
    assert "WS-HOURS" not in all_text(out)
    assert "WS-TOTAL-PAY" not in all_text(out)


def test_level_numbers_and_picture_clauses() -> None:
    """01 and 78 are numbers, PIC is a keyword, the picture itself stays readable."""
    out = coloured(lines("01 WS-NAME PIC X(30).", "78 MAX-EMPLOYEES VALUE 50.", "01 WS-TAX PIC 9(3)V99."))
    assert out[HC.C_KEYWORD] == ["PIC", "VALUE", "PIC"]
    assert out[HC.C_NUMBER] == ["01", "30", "78", "50", "01", "9", "3"]


def test_intrinsic_functions() -> None:
    """Only the name after FUNCTION is a constant, not the argument."""
    out = coloured(lines("COMPUTE WS-LEN = FUNCTION LENGTH(WS-NAME).", "MOVE FUNCTION CURRENT-DATE TO WS-STAMP."))
    assert out[HC.C_CONSTANT] == ["LENGTH", "CURRENT-DATE"]
    assert out[HC.C_KEYWORD] == ["COMPUTE", "FUNCTION", "MOVE", "FUNCTION", "TO"]


def test_special_registers_and_host_variables() -> None:
    """RETURN-CODE and SQLCODE are registers; :WS-ID is a host variable."""
    out = coloured(lines("MOVE 8 TO RETURN-CODE.", "IF SQLCODE = 100", "    SELECT SALARY INTO :WS-SALARY"))
    assert out[HC.C_VARIABLE] == ["RETURN-CODE", "SQLCODE", ":WS-SALARY"]
    assert out[HC.C_KEYWORD] == ["MOVE", "TO", "IF", "SELECT", "INTO"]


def test_program_class_method_and_paragraph_names() -> None:
    """The name after PROGRAM-ID/CLASS-ID/METHOD-ID and a paragraph header is a definition."""
    out = coloured(
        lines(
            "IDENTIFICATION DIVISION.",
            "PROGRAM-ID. HELLO.",
            "CLASS-ID. Employee.",
            "METHOD-ID. SetName.",
            "COMPUTE-TOTALS.",
            "INIT-SECTION SECTION.",
        )
    )
    assert out[HC.C_DEF] == ["HELLO", "Employee", "SetName", "COMPUTE-TOTALS", "INIT-SECTION"]
    assert "DIVISION" not in out[HC.C_DEF], "DIVISION is a keyword, not a paragraph"


def test_pseudo_text_and_doubled_quotes() -> None:
    """==EMP-NAME== is a string; "" is an escaped quote, not the end."""
    out = coloured(lines("COPY EMPLOYEE-REC REPLACING ==EMP-NAME== BY ==CUST-NAME==.", 'DISPLAY "say ""hi""".'))
    assert out[HC.C_STRING] == ["==EMP-NAME==", "==CUST-NAME==", '"say ""hi"""']


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="blue">MOVE</span> a &gt; b *&gt; note</code>'
    result = HC.highlight_inner(inner)
    assert HC.strip_and_decode(result) == HC.strip_and_decode(inner)
    assert "blue" not in result
    assert "&gt;" in result and "*&gt;" in result
    assert '<span class="c5fd7ff">MOVE</span>' in result
    assert '<span class="c999999">*&gt; note</span>' in result


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="cobolcode p2">\n<code><span class="blue">MOVE</span> <span class="orange">"hi"</span></code>\n</p>\n<p class="cout1">cobc (GnuCOBOL) 3.2.0</p>\n<span class="cmd-line">cobc --version</span>\n',
    )
    assert HC.main([str(page)]) == 1
    assert not HC.main(["--write", "--quiet", str(page)])
    text = page.read_text(encoding="utf-8")
    assert '<p class="cobolcode p2">\n<code><span class="c5fd7ff">MOVE</span> <span class="cad7fa8">"hi"</span></code>\n</p>' in text
    assert '<p class="cout1">cobc (GnuCOBOL) 3.2.0</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">cobc --version</span>' in text, "the inline command is left alone"
    assert not HC.main([str(page)])


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="cobolcode p2"><code><span class="c5fd7ff">MOVE</span> 1 TO WS-N</code></p>\n'
    page = write_page(tmp_path, body)
    assert not HC.main(["--quiet", str(page)])
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="cobolcode p2"><code>DISPLAY "open</code></p>\n'
    page = write_page(tmp_path, body)
    assert HC.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code and span tags may stay inside a block; a link is a refusal."""
    body = '<p class="cobolcode p2"><code>MOVE <a href="/dev/cobol/">COBOL</a> TO WS-N</code></p>\n'
    page = write_page(tmp_path, body)
    assert HC.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="cobolcode p2"><code>MOVE 1 TO WS-N</code></p>\n', newline="\r\n")
    assert not HC.main(["--write", "--quiet", str(page)])
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
