# version 0.1.2
"""Tests for tools/search/site_index.py and tools/search/find_article.py.

Each test builds a small docroot in tmp_path: a sitemap, a few ArticlePage
sources, a redirect stub, and one page to scan. The real aliases.json and
stopwords.txt of the tool are used.

The tool modules are imported when a test runs, not at collection:
site_index imports if_file_is_an_article, and an early import would cache
it before test_if_file_is_an_article.py points IF_ARTICLE_SRC_DIR at its
fixtures.
"""

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

CONTAINER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CONTAINER / "tools" / "search"))

WRITER = """<?php
$page = new ArticlePage(
    lang: 'en',
    title: 'Writer',
);
echo $page->createHead();
echo '
<p class="p2">Option C is not the language. Keys live in <span class="tt">~/.ssh</span>.</p>
<p class="cmd1">docker compose up</p>
<p class="p2">First we use git, then <a href="/dev/dotnet/">C#</a>, then docker-compose.</p>
<p class="p2">The writer page never links itself.</p>
';
echo $page->createFooter();
"""


def article(title: str, lang: str = "en") -> str:
    """Source of a minimal ArticlePage."""
    return f"<?php\n$page = new ArticlePage(\n    lang: '{lang}',\n    title: '{title}',\n);\necho $page->createHead();\necho '<p class=\"p2\">{title}</p>';\necho $page->createFooter();\n"


HEAD_LINKS = """<?php
$page = new ArticlePage(
    lang: 'en',
    title: 'Head links',
);
echo $page->createHead();
echo '
<h2 class="h2mTop">
    Install on <a href="/linux/">Linux</a>
</h2>
<p class="p2">Debian and Ubuntu are enough for this course.</p>
<p class="p2">Later <a href="/linux/">Linux</a> packaging gets its own page.</p>
';
echo $page->createFooter();
"""

ONLY_HEAD_LINK = """<?php
$page = new ArticlePage(
    lang: 'en',
    title: 'Only head link',
);
echo $page->createHead();
echo '
<h2>
    Move to <a href="/linux/">Linux</a>
</h2>
<p class="p2">Nothing else here.</p>
';
echo $page->createFooter();
"""


PAGES = {
    "/git/": article("Git"),
    "/linux/": article("Linux"),
    "/linux/debian/": article("Debian"),
    "/linux/ubuntu/": article("Ubuntu"),
    "/linux/make/git/": article("Git"),
    "/linux/ssh/": article("SSH in Linux"),
    "/windows/ssh/": article("ssh in Windows"),
    "/dvps/docker/compose/": article("Docker Compose"),
    "/dev/dotnet/": article("Development на C#"),
    "/dev/c/": article("C development"),
    "/linux/curl/": article("curl", lang="ru"),
    "/ru/code/dotnet/": article("Программирование на C#", lang="ru"),
    "/ru/plain/": "<?php\n$page = new ArticlePage(\n    title: 'Plain',\n);\n",
    "/dev/js/one_line/": "<?php\n$page = new ArticlePage(\n    type: 'IT', code_lang: 'js', lang: 'en', title: 'One line',);\n",
    "/dev/js/one_line_ru/": "<?php\n$page = new ArticlePage(\n    type: 'IT', code_lang: 'js', lang: 'ru', title: 'One line ru',);\n",
    "/zh/hosting/": article("Hosting", lang="zh"),
    "/old/": "<?php\nheader('Location: /git/');\n",
    "/writer/": WRITER,
    "/headlinks/": HEAD_LINKS,
    "/onlyheadlink/": ONLY_HEAD_LINK,
}


@pytest.fixture(name="si")
def fixture_si() -> ModuleType:
    """tools/search/site_index.py."""
    return importlib.import_module("site_index")


@pytest.fixture(name="fa")
def fixture_fa() -> ModuleType:
    """tools/search/find_article.py."""
    return importlib.import_module("find_article")


@pytest.fixture(name="src")
def fixture_src(tmp_path: Path) -> Path:
    """Docroot with every page of PAGES listed in its sitemap."""
    locs = []
    for url, source in PAGES.items():
        target = tmp_path / url.strip("/") / "index.php"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        locs.append(f"<url><loc>https://www.example.com{url}</loc></url>")
    (tmp_path / "sitemap.xml").write_text("<urlset>\n" + "\n".join(locs) + "\n</urlset>\n", encoding="utf-8")
    return tmp_path


def top(term: str, src: Path, lang: str = "en", prefer: str = "linux") -> str:
    """URL of the best strong match for term."""
    site_index = importlib.import_module("site_index")
    find_article = importlib.import_module("find_article")
    pages = find_article.candidates(site_index.build_index(str(src)), lang, "")
    ranked = find_article.rank(term, pages, find_article.load_aliases(), prefer)
    assert ranked and ranked[0].strong, term
    return str(ranked[0].page.url)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Claude Opus 5.5", ["claude", "opu", "5", "5"]),
        ("claude5_5", ["claude", "5", "5"]),
        ("C++", ["cpp"]),
        ("C#", ["csharp"]),
        (".NET", ["dotnet"]),
        ("ASP.NET", ["asp", "net"]),
        ("files", ["file"]),
        ("class", ["class"]),
    ],
)
def test_tokens(si: ModuleType, text: str, expected: list[str]) -> None:
    """Queries and page data share one tokenizer."""
    assert si.tokens(text) == expected


@pytest.mark.parametrize(
    ("url", "lang"),
    [("/ru/code/", "ru"), ("/linux/curl/", "en"), ("/", "en")],
)
def test_path_lang(si: ModuleType, url: str, lang: str) -> None:
    """Only /ru/ is Russian by default (SITE_LANG_DIRS); the folder decides, not the text."""
    assert si.path_lang(url) == lang


def test_index_skips_redirect_stubs(si: ModuleType, src: Path) -> None:
    """A redirect stub in the sitemap is not a page."""
    urls = {page.url for page in si.build_index(str(src))}
    assert "/old/" not in urls
    assert "/git/" in urls


def test_hub_beats_deeper_page(src: Path) -> None:
    """/git/ wins over /linux/make/git/ although Linux is preferred."""
    assert top("git", src) == "/git/"


def test_os_order(src: Path) -> None:
    """Linux first by default; --os windows moves the Windows page up."""
    assert top("ssh", src) == "/linux/ssh/"
    assert top("ssh", src, prefer="windows") == "/windows/ssh/"


def test_folder_names_beat_spelling(src: Path) -> None:
    """docker-compose finds the compose folder under docker."""
    assert top("docker-compose", src) == "/dvps/docker/compose/"


def test_scan_heading_link_is_not_linked(si: ModuleType, fa: ModuleType, src: Path) -> None:
    """A link inside a heading is reported as IN-HEADING and never makes the prose mention linked."""
    rows = fa.scan(str(src / "headlinks" / "index.php"), str(src), si.build_index(str(src)), "linux")
    linux = [row for row in rows if row.page.url == "/linux/"]
    assert [(row.mention.line, row.in_heading, row.linked) for row in linux] == [(9, True, False), (12, False, True)]
    assert linux[0].mention.text == "Linux"
    assert linux[0].mention.snippet == "Install on Linux"
    found = {row.page.url: row for row in rows if not row.in_heading}
    assert not found["/linux/debian/"].linked
    assert not found["/linux/ubuntu/"].linked


def test_scan_heading_link_without_prose_mention(si: ModuleType, fa: ModuleType, src: Path) -> None:
    """A heading is not prose: with no mention after it there is only the IN-HEADING row."""
    rows = fa.scan(str(src / "onlyheadlink" / "index.php"), str(src), si.build_index(str(src)), "linux")
    assert [(row.page.url, row.mention.line, row.in_heading) for row in rows] == [("/linux/", 9, True)]


def test_mistagged(si: ModuleType, fa: ModuleType, src: Path) -> None:
    """lang: 'ru' outside /ru/ is flagged, also on a one-line call; the ru default inside is fine."""
    rows = dict(fa.mistagged(si.build_index(str(src))))
    assert set(rows) == {"/linux/curl/", "/dev/js/one_line_ru/"}


def test_write_index(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """The JSON index carries its version and one record per page."""
    target = tmp_path / "index.json"
    si.write_index(si.build_index(str(src)), str(target))
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["version"] == si.INDEX_VERSION
    record = next(page for page in data["pages"] if page["url"] == "/dvps/docker/compose/")
    assert record["folders"] == [["dvp"], ["docker"], ["compose"]]
    assert record["head_tokens"] == ["docker", "compose"]
