# version 0.1.0
"""Tests for highlight_haskellcode.py: run with the container venv, python -m pytest tests/ -q."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_haskellcode as hh  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')
PALETTE = {hh.C_KEYWORD, hh.C_STRING, hh.C_COMMENT, hh.C_NUMBER, hh.C_CONSTRUCTOR, hh.C_DEF}


def coloured(inner: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it, entities decoded."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(hh.highlight_inner(inner)):
        found.setdefault(colour, []).append(html.unescape(text))
    return found


def lines(*source: str) -> str:
    """The inner HTML of a block: one <code> line per source line."""
    return "\n".join(f"<code>{html.escape(line, quote=False)}</code>" for line in source)


def test_keywords_strings_comments_and_numbers() -> None:
    """Each part of a line gets its colour; local names and operators stay plain."""
    out = coloured(lines("if age >= 18", '  putStrLn "adult" -- prints adult', "else pure 0"))
    assert out[hh.C_KEYWORD] == ["if", "else"]
    assert out[hh.C_STRING] == ['"adult"']
    assert out[hh.C_COMMENT] == ["-- prints adult"]
    assert out[hh.C_NUMBER] == ["18", "0"], "numbers 18 and 0"
    assert "putStrLn" not in sum(out.values(), []), "a function call is not a keyword"


def test_operator_with_dashes_is_not_a_comment() -> None:
    """--> and --| are operators, -- starts a comment."""
    out = coloured(lines("a --> b", "c --| d", "e -- real comment"))
    assert hh.C_COMMENT not in out or out[hh.C_COMMENT] == ["-- real comment"]


def test_block_comment_nests_and_spans_lines() -> None:
    """{- ... -} nests and paints every line it covers."""
    out = coloured(lines("{- outer {- inner -} still -}", "x = 1", "-- after"))
    comments = out[hh.C_COMMENT]
    assert comments[0] == "{- outer {- inner -} still -}"
    assert "-- after" in comments
    assert out[hh.C_NUMBER] == ["1"]


def test_string_and_char_versus_prime() -> None:
    """A prime in foldl' is part of the name; 'a' and '\\n' are literals."""
    result = hh.highlight_inner(lines("foldl' (+) 0 xs", "c = 'a'", "n = '\\n'", 's = "-- not a comment"'))
    out = coloured(lines("foldl' (+) 0 xs", "c = 'a'", "n = '\\n'", 's = "-- not a comment"'))
    assert out[hh.C_STRING] == ["'a'", "'\\n'", '"-- not a comment"']
    assert hh.C_COMMENT not in out
    assert '<span class="cad7fa8">' not in result.split("\n", maxsplit=1)[0], "the prime belongs to the name"


def test_constructors_and_types() -> None:
    """Upper-case names are constructors and types, lower-case names stay plain."""
    out = coloured(lines("data Color = Red | Green", "describe :: Maybe Int -> String"))
    assert out[hh.C_CONSTRUCTOR] == ["Color", "Red", "Green", "Maybe", "Int", "String"]


def test_qualified_names() -> None:
    """A dotted module name is one unit; M.fromList keeps M and the function plain."""
    out = coloured(lines("import qualified Data.Map as M", "m = M.fromList [(1, 2)]"))
    assert "Data.Map" in out[hh.C_CONSTRUCTOR]
    assert "fromList" not in out[hh.C_CONSTRUCTOR]
    assert out[hh.C_CONSTRUCTOR].count("M") == 2, "the alias in the import and the use"


def test_definition_names() -> None:
    """The name of a signature or an equation gets the def colour, also an operator."""
    out = coloured(lines("add :: Int -> Int -> Int", "add a b = a + b", "(<.>) :: String -> String", "infixr 6 <.>"))
    assert out[hh.C_DEF] == ["add", "add", "<.>"]


def test_pragma_is_a_directive() -> None:
    """{-# LANGUAGE ... #-} is coloured as one keyword-coloured span."""
    out = coloured(lines("{-# LANGUAGE BangPatterns #-}", "sumTo !n = n"))
    assert out[hh.C_KEYWORD] == ["{-# LANGUAGE BangPatterns #-}"]


def test_round_trip_keeps_entities_and_drops_old_spans() -> None:
    """A legacy span and an entity keep their text; only code tags survive."""
    inner = '<code><span class="yellow">add</span> a b = a &lt;- b -- &lt;-</code>'
    result = hh.highlight_inner(inner)
    assert hh.strip_and_decode(result) == hh.strip_and_decode(inner)
    assert "yellow" not in result
    assert "&lt;-" in result
    assert '<span class="c999999">-- &lt;-</span>' in result


def write_page(tmp_path: Path, body: str, newline: str = "\n") -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0; other classes stay."""
    page = write_page(
        tmp_path,
        '<p class="haskellcode p2">\n<code><span class="yellow">add</span> :: Int</code>\n</p>\n<p class="cout1">ghc 9.10.3</p>\n<span class="cmd-line">ghc --version</span>\n',
    )
    assert hh.main([str(page)]) == 1
    assert hh.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<p class="haskellcode p2">\n<code><span class="cff0086">add</span> :: <span class="cff5f00">Int</span></code>\n</p>' in text
    assert '<p class="cout1">ghc 9.10.3</p>' in text, "output blocks are left alone"
    assert '<span class="cmd-line">ghc --version</span>' in text, "the inline command is left alone"
    assert hh.main([str(page)]) == 0


def test_highlighted_blocks_are_skipped(tmp_path: Path) -> None:
    """A block that already has a lower-case palette span is not touched again."""
    body = '<p class="haskellcode p2"><code><span class="c5fd7ff">let</span> x = 1</code></p>\n'
    page = write_page(tmp_path, body)
    assert hh.main(["--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_unterminated_string_is_reported_and_left_alone(tmp_path: Path) -> None:
    """A block the lexer cannot read stays byte for byte and fails the run."""
    body = '<p class="haskellcode p2"><code>putStrLn "open</code></p>\n'
    page = write_page(tmp_path, body)
    assert hh.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_a_link_inside_a_block_is_reported(tmp_path: Path) -> None:
    """Only code and span tags may stay inside a block; a link is a refusal."""
    body = '<p class="haskellcode p2"><code>import <a href="/dev/haskell/">Haskell</a></code></p>\n'
    page = write_page(tmp_path, body)
    assert hh.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body


def test_crlf_survives(tmp_path: Path) -> None:
    """A CRLF file keeps its CRLF line endings."""
    page = write_page(tmp_path, '<?php\n<p class="haskellcode p2"><code>let x = 1</code></p>\n', newline="\r\n")
    assert hh.main(["--write", "--quiet", str(page)]) == 0
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
