# version 0.1.1
"""Tests for highlight_rubycode.py: run with the container venv, python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_rubycode as hr  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {hr.C_KEYWORD, hr.C_STRING, hr.C_COMMENT, hr.C_SYMBOL, hr.C_NUMBER, hr.C_CONSTANT, hr.C_VARIABLE, hr.C_DEF}


def coloured(inner: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(hr.highlight_inner(inner)):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def lines(*source: str) -> str:
    """The inner HTML of a block: one <code> line per source line."""
    return "\n".join(f"<code>{html.escape(line, quote=False)}</code>" for line in source)


def test_keywords_strings_comments_and_numbers() -> None:
    """Each part of a line gets its colour; local names and operators stay plain."""
    out = coloured(lines("if age >= 18", '  puts "adult" # prints adult', "end"))
    assert out[hr.C_KEYWORD] == ["if", "end"]
    assert out[hr.C_STRING] == ['"adult"']
    assert out[hr.C_COMMENT] == ["# prints adult"]
    assert out[hr.C_NUMBER] == ["18"], "number 18"
    assert "puts" not in sum(out.values(), []), "a method call is not a keyword"


def test_hash_inside_a_string_is_not_a_comment() -> None:
    """# only starts a comment outside a string, and #{...} is interpolation."""
    out = coloured(lines('color = "#f00" # red', 'puts "hi #{name}"'))
    assert out[hr.C_COMMENT] == ["# red"]
    assert out[hr.C_STRING] == ['"#f00"', '"hi #{name}"']
    assert out[hr.C_STRING].count('"hi #{name}"') == 1, "interpolation stays part of the string"


def test_percent_literals() -> None:
    """%w[] %i() %q{} are one string each; % after a value is modulo."""
    out = coloured(lines("words = %w[ada bob]", "syms = %i[a b]", "text = %q(x y)", "rest = 7 % 2"))
    assert out[hr.C_STRING] == ["%w[ada bob]", "%i[a b]", "%q(x y)"]
    assert out[hr.C_NUMBER] == ["7", "2"]


def test_heredocs() -> None:
    """The marker, the body and the closing delimiter are one string."""
    out = coloured(lines("text = <<~TEXT", "  Hello,", "  Ruby.", "TEXT", "puts text"))
    strings = out[hr.C_STRING]
    assert strings[0] == "<<~TEXT"
    assert "  Hello," in strings and "  Ruby." in strings
    assert "TEXT" in strings
    assert "puts text" not in strings, "code after the heredoc is lexed again"


def test_append_is_not_a_heredoc() -> None:
    """results << square keeps << the append operator."""
    out = coloured(lines("mutex.synchronize { results << square }"))
    assert hr.C_STRING not in out


def test_symbols_versus_labels_and_ternary() -> None:
    """:name is a symbol; name: of a hash and the colon of a ? b : c are not."""
    out = coloured(lines('user = { name: "Ada", role: :admin }', "puts user[:role]", "x = a ? b : c"))
    assert out[hr.C_SYMBOL] == [":admin", ":role"]


def test_regex_versus_division() -> None:
    """/ after an operator or at the start is a regex; after a value it divides."""
    out = coloured(lines("puts(s =~ /[0-9]+/)", "half = 7 / 2"))
    assert out[hr.C_STRING] == ["/[0-9]+/"]
    assert out[hr.C_NUMBER] == ["7", "2"]


def test_block_comment_and_end_marker() -> None:
    """=begin..=end at column 0 is a comment; __END__ ends the code."""
    out = coloured(lines("=begin", "a note", "=end", "puts __FILE__", "__END__", "raw text"))
    assert out[hr.C_COMMENT] == ["=begin", "a note", "=end"]
    assert out[hr.C_CONSTANT] == ["__FILE__"]
    assert hr.C_STRING not in out


def test_def_names_constants_and_variables() -> None:
    """The name after def is its own colour; def self.read and def <=> included."""
    out = coloured(lines("def greet(name)", "  @name = $stdout", "end", "def self.read", "end", "def <=>(other)", "end"))
    assert out[hr.C_DEF] == ["greet", "read", "<=>"]
    assert out[hr.C_VARIABLE] == ["@name", "$stdout"]


def test_constants() -> None:
    """An upper-case name is a constant, also as a hash label of a method call."""
    out = coloured(lines("LIMIT = 10", 'puts ENV.fetch("HOME")', "server = HTTPServer.new(Port: 8000)"))
    assert out[hr.C_CONSTANT] == ["LIMIT", "ENV", "HTTPServer", "Port"]


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="blue">puts</span> a &gt; b # =&gt; true</code>'
    result = hr.highlight_inner(inner)
    assert hr.strip_and_decode(result) == hr.strip_and_decode(inner)
    assert "blue" not in result
    assert "&gt;" in result and "=&gt;" in result
    assert '<span class="c999999"># =&gt; true</span>' in result


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="rubycode p2">\n<code><span class="blue">puts</span> <span class="orange">"hi"</span></code>\n</p>\n<p class="cout1">ruby 3.4.12</p>\n<span class="cmd-line">ruby -v</span>\n',
    )
    assert hr.main([str(page)]) == 1
    assert hr.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<p class="rubycode p2">\n<code>puts <span class="cad7fa8">"hi"</span></code>\n</p>' in text
    assert '<p class="cout1">ruby 3.4.12</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">ruby -v</span>' in text, "the inline command is left alone"
    assert hr.main([str(page)]) == 0


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="rubycode p2"><code><span class="c5fd7ff">puts</span> 1</code></p>\n'
    page = write_page(tmp_path, body)
    assert hr.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="rubycode p2"><code>puts "open</code></p>\n'
    page = write_page(tmp_path, body)
    assert hr.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code and span tags may stay inside a block; a link is a refusal."""
    body = '<p class="rubycode p2"><code>puts <a href="/dev/ruby/">Ruby</a></code></p>\n'
    page = write_page(tmp_path, body)
    assert hr.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="rubycode p2"><code>puts 1</code></p>\n', newline="\r\n")
    assert hr.main(["--write", "--quiet", str(page)]) == 0
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
