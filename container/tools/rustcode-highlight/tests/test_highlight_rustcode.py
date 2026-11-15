# version 0.1.0
"""Tests for highlight_rustcode.py: run with the container venv, python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_rustcode as hr  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {hr.C_KEYWORD, hr.C_STRING, hr.C_COMMENT, hr.C_ATTRIBUTE, hr.C_NUMBER, hr.C_TYPE, hr.C_MACRO, hr.C_FUNCTION}


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
    """Each part of a line gets its colour; names and operators stay plain."""
    out = coloured(lines("if age >= 18 {", '    println!("adult"); // prints adult', "}"))
    assert out[hr.C_KEYWORD] == ["if"]
    assert out[hr.C_STRING] == ['"adult"']
    assert out[hr.C_COMMENT] == ["// prints adult"]
    assert out[hr.C_NUMBER] == ["18"], "number 18"
    assert out[hr.C_MACRO] == ["println!"]
    assert "age" not in sum(out.values(), []), "a local name is not a keyword"


def test_hash_inside_a_string_is_not_a_comment() -> None:
    """// only starts a comment outside a string."""
    out = coloured(lines('let color = "#f00"; // red', 'println!("a // b");'))
    assert out[hr.C_COMMENT] == ["// red"]
    assert out[hr.C_STRING] == ['"#f00"', '"a // b"']


def test_char_literal_versus_lifetime() -> None:
    """'x' is a char; 'a in a borrow is a lifetime."""
    out = coloured(lines("let c = 'x';", "fn first<'a>(s: &'a str) -> &'a str {", "    s", "}"))
    assert out[hr.C_STRING] == ["'x'"]
    assert out[hr.C_ATTRIBUTE] == ["'a", "'a", "'a"]
    assert out[hr.C_FUNCTION] == ["first"]
    assert out[hr.C_TYPE] == ["str", "str"]


def test_escaped_chars_and_lifetimes() -> None:
    """'\\n' and '\\u{1F600}' are chars; 'static is a lifetime."""
    out = coloured(lines("let n = '\\n';", "let e = '\\u{1F600}';", 'let s: &\'static str = "hi";'))
    assert "'\\n'" in out[hr.C_STRING]
    assert "'\\u{1F600}'" in out[hr.C_STRING]
    assert "'static" in out[hr.C_ATTRIBUTE]
    assert '"hi"' in out[hr.C_STRING]


def test_raw_and_byte_strings() -> None:
    """r"...", r#"..."# and b"..." are one string each."""
    out = coloured(lines('let a = r"a\\b";', 'let b = r#"say "hi""#;', 'let c = b"GET /";'))
    assert out[hr.C_STRING] == ['r"a\\b"', 'r#"say "hi""#', 'b"GET /"']


def test_block_comments_nest() -> None:
    """/* ... /* ... */ ... */ is one comment."""
    out = coloured(lines("/* outer /* inner */ still outer */", "let x = 1;"))
    assert len(out[hr.C_COMMENT]) == 1
    assert "still outer" in out[hr.C_COMMENT][0]
    assert out[hr.C_NUMBER] == ["1"]


def test_numbers_with_suffixes_and_bases() -> None:
    """Integer suffixes and 0x/0b/0o bases are part of the number."""
    out = coloured(lines("let a = 42u8;", "let b = 1_000;", "let c = 0x1f;", "let d = 3.5f64;"))
    assert out[hr.C_NUMBER] == ["42u8", "1_000", "0x1f", "3.5f64"]


def test_attribute_is_one_token() -> None:
    """#[derive(Debug, Clone)] and #[test] are attributes, brackets included."""
    out = coloured(lines("#[derive(Debug, Clone)]", "struct User {", "    name: String,", "}", "#[test]", "fn t() {}"))
    assert out[hr.C_ATTRIBUTE] == ["#[derive(Debug, Clone)]", "#[test]"]
    assert out[hr.C_TYPE] == ["User", "String"]
    assert out[hr.C_FUNCTION] == ["t"]


def test_types_constants_and_macros() -> None:
    """Upper-case names are types or constants; a name before ! is a macro."""
    out = coloured(lines("const LIMIT: u32 = 10;", "let v = Vec::new();", "let s = Some(3);", 'println!("{}", v.len());'))
    assert out[hr.C_TYPE] == ["LIMIT", "u32", "Vec", "Some"]
    assert out[hr.C_MACRO] == ["println!"]
    assert "new" not in sum(out.values(), []), "a method call is not a type"


def test_fn_names_and_keywords() -> None:
    """The name after fn is its own colour, also for a method."""
    out = coloured(lines("fn main() {", "    fn fmt(&self) {}", "}", "let mut count = 0;"))
    assert out[hr.C_FUNCTION] == ["main", "fmt"]
    assert out[hr.C_KEYWORD] == ["fn", "fn", "self", "let", "mut"]
    assert out.get(hr.C_TYPE, []) == []


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="blue">fn</span> main() &lt;T&gt; where T: Clone {}</code>'
    result = hr.highlight_inner(inner)
    assert hr.strip_and_decode(result) == hr.strip_and_decode(inner)
    assert "blue" not in result
    assert "&lt;" in result and "&gt;" in result
    assert '<span class="c5fd7ff">fn</span>' in result


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="rustcode p2">\n<code><span class="blue">fn</span> main() {</code>\n<code>    <span class="yellow">println!</span>(<span class="orange">"hi"</span>);</code>\n<code>}</code>\n</p>\n<p class="cout1">hi</p>\n<span class="cmd-line">cargo run</span>\n',
    )
    assert hr.main([str(page)]) == 1
    assert hr.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<span class="c5fd7ff">fn</span>' in text
    assert '<span class="c4e9a06">println!</span>' in text
    assert '<span class="cad7fa8">"hi"</span>' in text
    assert '<p class="cout1">hi</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">cargo run</span>' in text, "the inline command is left alone"
    assert hr.main([str(page)]) == 0


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="rustcode p2"><code><span class="c5fd7ff">fn</span> main() {}</code></p>\n'
    page = write_page(tmp_path, body)
    assert hr.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="rustcode p2"><code>println!("open</code></p>\n'
    page = write_page(tmp_path, body)
    assert hr.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code and span tags may stay inside a block; a link is a refusal."""
    body = '<p class="rustcode p2"><code>fn <a href="/dev/rust/">Rust</a>()</code></p>\n'
    page = write_page(tmp_path, body)
    assert hr.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="rustcode p2"><code>let x = 1;</code></p>\n', newline="\r\n")
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
