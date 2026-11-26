# version 0.1.0
"""The --tag names of test_content_loaded.py come from SITE_TAG_MARKERS.

The engine knows no site's markup, so a name works only when the site profile
gives it. The module is loaded under another name with a one-URL sitemap, so
no server is needed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

MODULE = Path(__file__).resolve().parent / "test_content_loaded.py"
SITEMAP = "<urlset><url><loc>https://example.com/</loc></url></urlset>\n"
MARKERS = 'menu=<nav class="menu"|<nav id="top">; footer=id="footer-end"'


def load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tag_markers: str) -> ModuleType:
    """test_content_loaded.py with this SITE_TAG_MARKERS and no bottom marker set."""
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(SITEMAP, encoding="utf-8")
    monkeypatch.setenv("SITEMAP_FILE", str(sitemap))
    monkeypatch.setenv("SITE_TAG_MARKERS", tag_markers)
    monkeypatch.setenv("SITE_BOTTOM_MARKER", "")
    for key in ("BOTTOM_MARKER", "PATH_PREFIX", "EXCLUDE_SUBSTR"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("content_loaded_under_test", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_profile_names_resolve_to_their_alternatives(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = load(monkeypatch, tmp_path, MARKERS)
    assert module.resolve_marker("menu") == ('<nav class="menu"', '<nav id="top">')
    assert module.resolve_marker("footer") == ('id="footer-end"',)


def test_without_the_profile_no_name_is_known(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = load(monkeypatch, tmp_path, "")
    assert not module.TAG_MARKERS
    assert module.resolve_marker("topbar") == ("topbar",)
    assert module.resolve_marker(None) == ("</html>",)


@pytest.mark.parametrize("raw", ["menu", "=<nav", "menu=", "menu= | ", ";;"])
def test_a_broken_entry_is_left_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str) -> None:
    module = load(monkeypatch, tmp_path, f'{raw}; footer=id="footer-end"')
    assert module.TAG_MARKERS == {"footer": ('id="footer-end"',)}
