# version 0.1.1
"""Tests for highlight_asmcode.py: run with python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_asmcode as ha  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {ha.C_KEYWORD, ha.C_STRING, ha.C_COMMENT, ha.C_LABEL, ha.C_CONSTANT, ha.C_NUMBER, ha.C_REGISTER}


def coloured(inner: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(ha.highlight_inner(inner)):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def lines(*source: str) -> str:
    """The inner HTML of a block: one <code> line per source line."""
    return "\n".join(f"<code>{html.escape(line, quote=False)}</code>" for line in source)


def test_instructions_registers_numbers_and_comments() -> None:
    """Each part of a line gets its colour; memory symbols and commas stay plain."""
    out = coloured(lines("    mov rax, 1        ; sys_write", "    syscall", "    xor rdi, rdi"))
    assert out[ha.C_KEYWORD] == ["mov", "syscall", "xor"]
    assert out[ha.C_REGISTER] == ["rax", "rdi", "rdi"]
    assert out[ha.C_NUMBER] == ["1"]
    assert out[ha.C_COMMENT] == ["; sys_write"]


def test_a_semicolon_inside_a_string_is_not_a_comment() -> None:
    """; only starts a comment outside a string."""
    out = coloured(lines('    msg db "a;b", 0   ; real comment'))
    assert out[ha.C_STRING] == ['"a;b"']
    assert out[ha.C_COMMENT] == ["; real comment"]


def test_labels_are_not_instructions() -> None:
    """loop: is a label definition; loop alone is the instruction."""
    out = coloured(lines("loop:", "    loop loop", "    jmp loop"))
    assert out[ha.C_LABEL] == ["loop:"]
    assert out[ha.C_KEYWORD] == ["loop", "jmp"]


def test_directives_and_constants() -> None:
    """section/db/equ are keywords; upper-case names are constants."""
    out = coloured(lines("section .data", "SYS_EXIT equ 60", '    msg db "hi", 0'))
    assert out[ha.C_KEYWORD] == ["section", "equ", "db"]
    assert out[ha.C_CONSTANT] == ["SYS_EXIT"]


def test_a_command_is_not_assembly() -> None:
    """A word that is a mnemonic mid-line stays plain, so apk add is not ADD."""
    out = coloured(lines("docker run --rm alpine apk add --no-cache nasm binutils"))
    assert ha.C_KEYWORD not in out


def test_uppercase_instructions_and_all_register_widths() -> None:
    """NASM is case-insensitive; r15b, eax, ax, al and xmm0 are registers."""
    out = coloured(lines("    MOV r15b, 1", "    mov eax, 2", "    mov ax, 3", "    mov al, xmm0"))
    assert out[ha.C_KEYWORD] == ["MOV", "mov", "mov", "mov"]
    assert out[ha.C_REGISTER] == ["r15b", "eax", "ax", "al", "xmm0"]


def test_macro_parameters_and_directives() -> None:
    """%macro is a keyword; %1 and %%loop are expansion-local names."""
    out = coloured(lines("%macro COUNTDOWN 1", "    dec %1", "    jnz %%loop", "%endmacro"))
    assert out[ha.C_KEYWORD] == ["%macro", "dec", "jnz", "%endmacro"]
    assert out[ha.C_REGISTER] == ["%1", "%%loop"]


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="blue">mov</span> rax, [rbx+8] <span class="green">; &gt;= 0</span></code>'
    result = ha.highlight_inner(inner)
    assert ha.strip_and_decode(result) == ha.strip_and_decode(inner)
    assert "blue" not in result
    assert "&gt;=" in result
    assert '<span class="c999999">; &gt;= 0</span>' in result


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    body = '<p class="asmcode p2">\n<code><span class="blue">mov</span> <span class="yellow">rax</span>, <span class="lightblue">1</span></code>\n</p>\n<p class="cout1">Hello from assembly</p>\n<span class="cmd-line">nasm -v</span>\n'
    page = write_page(tmp_path, body)
    assert ha.main([str(page)]) == 1
    assert ha.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    expected = '<p class="asmcode p2">\n<code><span class="c5fd7ff">mov</span> <span class="c4e9a06">rax</span>, <span class="c34e2e2">1</span></code>\n</p>'
    assert expected in text
    assert '<p class="cout1">Hello from assembly</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">nasm -v</span>' in text, "the inline command is left alone"
    assert ha.main([str(page)]) == 0


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="asmcode p2"><code><span class="c5fd7ff">mov</span> rax, 1</code></p>\n'
    page = write_page(tmp_path, body)
    assert ha.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="asmcode p2"><code>    msg db "open</code></p>\n'
    page = write_page(tmp_path, body)
    assert ha.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code and span tags may stay inside a block; a link is a refusal."""
    body = '<p class="asmcode p2"><code>jmp <a href="/dev/assembler/">Assembler</a></code></p>\n'
    page = write_page(tmp_path, body)
    assert ha.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="asmcode p2"><code>    mov rax, 1</code></p>\n', newline="\r\n")
    assert ha.main(["--write", "--quiet", str(page)]) == 0
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
