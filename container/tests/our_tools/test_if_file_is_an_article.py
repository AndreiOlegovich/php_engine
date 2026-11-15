# version 0.1.0
"""Tests for tools/feed/if_file_is_an_article.py.

Fixtures under tests/our_tools/fixtures/ are verbatim copies of real
src files (frozen so src edits cannot silently change expectations):
articles/ covers every page layout (ArticlePage, Page hub, BasePage,
DictPage, legacy header include, old-style header include without
underscore prefixes, _template generator, non-UTF8 source),
non_articles/ covers section files, menus, engine classes, service
stubs, game endpoints and redirect stubs.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

CONTAINER = Path(__file__).resolve().parents[2]
TOOLS_FEED = CONTAINER / "tools" / "feed"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if not FIXTURES.is_dir():
    pytest.skip(f"no fixtures at {FIXTURES}: copy the article fixtures first", allow_module_level=True)

os.environ["IF_ARTICLE_SRC_DIR"] = str(FIXTURES)

sys.path.insert(0, str(TOOLS_FEED))

from if_file_is_an_article import (  # pylint: disable=wrong-import-position  # noqa: E402
    is_article,
    page_title,
    page_url,
    redirect_target,
)

ARTICLES = {
    "articlepage.php": "Crush: download, install and use the terminal coding agent",
    "page_hub.php": "Install VirtualBox",
    "basepage.php": "Feed",
    "dictpage.php": "Математика. Словарь",
    "legacy_header.php": "Winedt",
    "old_style_header.php": "Unix-время",
    "template_page.php": "enum",
    "cp1251_article.php": "Игры БК Спартак",
}

NOT_ARTICLES = [
    "RelatedArticles.php",
    "Tcont.php",
    "top_menu.php",
    "section_menu.php",
    "engine_class.php",
    "service_stub.php",
    "external_redirect.php",
    "game_endpoint.php",
    "internal_redirect.php",
]


@pytest.mark.parametrize("name", sorted(ARTICLES))
def test_fixture_article(name):
    """Every layout variant is detected as an article."""
    assert is_article(str(FIXTURES / "articles" / name))


@pytest.mark.parametrize("name", NOT_ARTICLES)
def test_fixture_not_article(name):
    """Section files, menus, engine and stubs are not articles."""
    assert not is_article(str(FIXTURES / "non_articles" / name))


@pytest.mark.parametrize("name, title", sorted(ARTICLES.items()))
def test_fixture_title(name, title):
    """Title extraction works for every layout, incl. non-UTF8 source."""
    assert page_title(str(FIXTURES / "articles" / name)) == title


def test_redirect_target_internal():
    """Internal redirect stubs resolve; external ones do not."""
    internal = str(FIXTURES / "non_articles" / "internal_redirect.php")
    assert redirect_target(internal) == "/i/nginx/"
    external = str(FIXTURES / "non_articles" / "external_redirect.php")
    assert redirect_target(external) is None


def test_page_url_mapping(monkeypatch):
    """index.php -> dir URL, other files keep .php (against real src)."""
    checker = sys.modules["if_file_is_an_article"]
    monkeypatch.setattr(checker, "SRC_DIR", str(CONTAINER / "src"))
    src = str(CONTAINER / "src")
    assert page_url(os.path.join(src, "i", "nginx", "index.php")) == "/i/nginx/"
    assert page_url(os.path.join(src, "dev", "python", "2.php")) == "/section/python/2.php"


def _run_cli(*paths):
    """Run the checker script with the fixtures root override."""
    env = dict(os.environ, IF_ARTICLE_SRC_DIR=str(FIXTURES))
    return subprocess.run(
        [sys.executable, str(TOOLS_FEED / "if_file_is_an_article.py"), *paths],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_cli_verdicts():
    """Script prints ARTICLE/NOTARTICLE per file; exit 0 iff all articles."""
    articles = [str(FIXTURES / "articles" / name) for name in sorted(ARTICLES)]
    proc = _run_cli(*articles)
    assert proc.returncode == 0
    for name in sorted(ARTICLES):
        assert f"ARTICLE {FIXTURES / 'articles' / name}" in proc.stdout
    mixed = articles + [str(FIXTURES / "non_articles" / "Tcont.php")]
    proc = _run_cli(*mixed)
    assert proc.returncode == 1
    assert "NOTARTICLE" in proc.stdout
    assert re.search(r"^NOTARTICLE .*Tcont\.php$", proc.stdout, re.M)


def test_cli_no_args():
    """No args prints usage with exit 2."""
    proc = _run_cli()
    assert proc.returncode == 2
