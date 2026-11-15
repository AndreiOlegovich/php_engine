"""Tests for highlight_jsoncode.py: run with the container venv, python -m pytest tests/ -q."""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import highlight_jsoncode as hj  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SPAN_RE = re.compile(r'<span class="(c[0-9a-f]{6})">([^<]*)</span>')


def coloured(html_text: str) -> dict[str, list[str]]:
    """Colour class -> the texts wrapped in it."""
    found: dict[str, list[str]] = {}
    for colour, text in SPAN_RE.findall(html_text):
        found.setdefault(colour, []).append(text)
    return found


def test_tokens_get_the_palette() -> None:
    """Keys, values, constants and comments each get their colour."""
    inner = '{\n  // note\n  "action": "read",\n  "n": 3,\n  "on": true\n}'
    out = coloured(hj.highlight_inner(inner))
    assert out[hj.C_KEY] == ['"action"', '"n"', '"on"']
    assert out[hj.C_VALUE] == ['"read"', "3"]
    assert out[hj.C_CONSTANT] == ["true"]
    assert out[hj.C_COMMENT] == ["// note"]


def test_entities_keep_their_spelling() -> None:
    """&quot; stays &quot;: removing the spans gives the input back."""
    inner = "{&quot;role&quot;: &quot;user&quot;}"
    result = hj.highlight_inner(inner)
    assert hj.ADDED_SPAN_RE.sub(r"\1", result) == inner
    assert '<span class="c5fd7ff">&quot;role&quot;</span>' in result


def test_hidden_lines_runs_survive_and_nest() -> None:
    """A colour span never crosses the extra-lines span of a hidden run."""
    inner = '<span class="extra-lines">{\n  "a": 1,\n</span>  "b": 2\n}'
    result = hj.highlight_inner(inner)
    assert result.startswith('<span class="extra-lines">{\n  <span class="c5fd7ff">"a"</span>')
    assert result.count('<span class="extra-lines">') == 1
    assert hj.ADDED_SPAN_RE.sub(r"\1", result) == inner
    depth = 0
    for tag in re.findall(r"</?span\b", result):
        depth += 1 if tag == "<span" else -1
        assert depth in (0, 1, 2)
    assert depth == 0


def test_broken_json_is_reported() -> None:
    """A block the lexer cannot read raises instead of guessing."""
    with pytest.raises(ValueError):
        hj.highlight_inner('{"a": @}')


def write_page(tmp_path: Path, body: str) -> Path:
    """A .php file holding body."""
    page = tmp_path / "index.php"
    page.write_text(body, encoding="utf-8")
    return page


def test_check_write_check(tmp_path: Path) -> None:
    """Check exits 1, write colours, a second check exits 0."""
    page = write_page(tmp_path, '<p class="jsoncode p2">{"a": true}</p>\n<p class="somecode p2">{"a": true}</p>\n')
    assert hj.main([str(page)]) == 1
    assert hj.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<span class="cfce94f">true</span>' in text
    assert '<p class="somecode p2">{"a": true}</p>' in text, "a non-JSON class is left alone"
    assert hj.main([str(page)]) == 0


def test_hidden_lines_block_is_a_target(tmp_path: Path) -> None:
    """jsoncode-with-hidden-lines blocks are found and keep their runs."""
    body = '<p class="jsoncode jsoncode-with-hidden-lines p2" id="x"><span class="extra-lines">{\n</span>  "k": null\n}</p>\n'
    page = write_page(tmp_path, body)
    assert hj.main(["--write", "--quiet", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '<span class="extra-lines">{\n</span>' in text
    assert '<span class="cfce94f">null</span>' in text


def test_highlighted_block_is_untouched(tmp_path: Path) -> None:
    """A block that already has palette spans is not coloured again."""
    body = '<p class="jsoncode p2">{<span class="c5FD7FF">"a"</span>: 1}</p>\n'
    page = write_page(tmp_path, body)
    assert hj.main(["--write", "--quiet", str(page)]) == 0
    assert page.read_text(encoding="utf-8") == body


def test_crlf_endings_stay(tmp_path: Path) -> None:
    """A CRLF page keeps its CRLF line endings after --write."""
    page = tmp_path / "index.php"
    page.write_bytes(b'<p class="jsoncode p2">{\r\n  "a": 1\r\n}</p>\r\n')
    assert hj.main(["--write", "--quiet", str(page)]) == 0
    data = page.read_bytes()
    assert data.count(b"\r\n") == 3 and b"\n" not in data.replace(b"\r\n", b"")
    assert b'<span class="cad7fa8">1</span>' in data


def test_legacy_encodings(tmp_path: Path) -> None:
    """A cp1251 page without jsoncode is skipped; one with it is reported."""
    plain = tmp_path / "old.php"
    plain.write_bytes("<p>Привет</p>\n".encode("cp1251"))
    assert hj.main(["--quiet", str(plain)]) == 0
    json_page = tmp_path / "old_json.php"
    json_page.write_bytes('<p class="jsoncode p2">{"a": "Привет"}</p>\n'.encode("cp1251"))
    before = json_page.read_bytes()
    assert hj.main(["--write", "--quiet", str(json_page)]) == 2
    assert json_page.read_bytes() == before


def test_broken_block_exits_2_and_stays(tmp_path: Path) -> None:
    """A broken block is reported with exit 2 and left as it was."""
    body = '<p class="jsoncode p2">{"a": @}</p>\n'
    page = write_page(tmp_path, body)
    assert hj.main(["--write", "--quiet", str(page)]) == 2
    assert page.read_text(encoding="utf-8") == body
