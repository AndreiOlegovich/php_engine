# version 0.3.0
"""Tests for the search export of tools/search/site_index.py.

Each test builds a small docroot in tmp_path: a sitemap, pages with a
constructor description, a legacy page with a meta description, pages with
no description, an untitled page, an untitled file, a page with body
headings and a redirect stub.

The tool module is imported when a test runs, not at collection:
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

PAGES = {
    "/constructor/": """<?php
$page = new ArticlePage(
    lang: 'en',
    title: 'Constructor',
    description: 'A &amp; B   with
 spaces',
);
echo $page->createHead();
""",
    "/meta/": '<html><head><meta name="description" content="Legacy &amp; meta\'s page"></head></html>\n',
    "/missing/": """<?php
$page = new ArticlePage(
    lang: 'en',
    title: 'Missing',
);
echo $page->createHead();
""",
    "/ru/example/": """<?php
$page = new ArticlePage(
    lang: 'ru',
    title: 'Пример',
    description: 'Русское описание',
);
echo $page->createHead();
""",
    "/zh/example/": """<?php
$page = new ArticlePage(
    lang: 'zh',
    title: '例子',
    description: '中文描述',
);
echo $page->createHead();
""",
    "/untitled/": """<?php
$page = new ArticlePage(
    lang: 'en',
);
echo $page->createHead();
""",
    "/file/name_without_title.php": """<?php
$page = new ArticlePage(
    lang: 'en',
);
echo $page->createHead();
""",
    "/headings/": """<?php
$page = new ArticlePage(
    lang: 'en',
    title: 'Headings guide',
    description: 'Sections of a page',
    keywords: 'anchors',
);
echo $page->createHead();
echo '
<h1>Smartrac MiniWeb guide</h1>
<h2 class="h2mTop" id="a">Anchors &amp; <code>canary</code> tasks</h2>
<!-- <h2>Hidden draft</h2> -->
<h2>' . $section[0] . '</h2>
<h2><?= $name ?></h2>
<h2>Block an <abbr title="Internet Protocol: every site has it.">IP</abbr> range</h2>
<h2>See <a href="' . $url . '">the list</a></h2>
<h2>Now <?= $page->name ?> here</h2>
<h2>Book by Ritchie.
<br>"Quoted"</h2>
<H3>Boundaries
 of sections</H3>
<h3>Canary again</h3>
<h4>Deeper level</h4>
';
""",
    "/entities/": """<?php
$page = new ArticlePage(
    lang: 'en',
    title: 'C&plus;&plus; classes &laquo;arrow&raquo;: notes',
    description: 'Classes',
    keywords: 'C&plus;&plus;, R&amp;D',
);
echo $page->createHead();
""",
    "/old/": "<?php\nheader('Location: /constructor/');\n",
}


@pytest.fixture(name="si")
def fixture_si() -> ModuleType:
    """tools/search/site_index.py."""
    return importlib.import_module("site_index")


@pytest.fixture(name="src")
def fixture_src(tmp_path: Path) -> Path:
    """Docroot with every page of PAGES listed in its sitemap."""
    locs = []
    for url, source in PAGES.items():
        target = tmp_path / url.strip("/") / "index.php" if url.endswith("/") else tmp_path / url.strip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        locs.append(f"<url><loc>https://www.example.com{url}</loc></url>")
    (tmp_path / "sitemap.xml").write_text("<urlset>\n" + "\n".join(locs) + "\n</urlset>\n", encoding="utf-8")
    return tmp_path


def export(si: ModuleType, src: Path, tmp_path: Path, name: str = "search") -> dict[str, dict[str, object]]:
    """Write the search export and return every record by URL."""
    out = tmp_path / name
    si.write_search(si.build_index(str(src)), str(out))
    records = {}
    for path in sorted(out.iterdir()):
        data = json.loads(path.read_text(encoding="utf-8"))
        for record in data["pages"]:
            records[record["url"]] = record
    return records


def test_description_from_constructor(si: ModuleType, src: Path) -> None:
    """A constructor argument is the description: entities decoded, spaces collapsed."""
    page = next(page for page in si.build_index(str(src)) if page.url == "/constructor/")
    assert page.description == "A & B with spaces"
    assert page.description_tokens == si.tokens("A & B with spaces")


def test_description_from_meta_tag(si: ModuleType, src: Path) -> None:
    """A legacy page without a constructor takes its meta description."""
    page = next(page for page in si.build_index(str(src)) if page.url == "/meta/")
    assert page.description == "Legacy & meta's page"
    assert page.description_tokens == si.tokens("Legacy & meta's page")


def test_missing_description(si: ModuleType, src: Path) -> None:
    """A constructor without description: and no meta tag gives empty text and tokens."""
    page = next(page for page in si.build_index(str(src)) if page.url == "/missing/")
    assert page.description == ""
    assert page.description_tokens == []


def test_full_index_keeps_new_fields(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """--write records carry description and description_tokens as token arrays."""
    target = tmp_path / "index.json"
    si.write_index(si.build_index(str(src)), str(target))
    data = json.loads(target.read_text(encoding="utf-8"))
    record = next(page for page in data["pages"] if page["url"] == "/constructor/")
    assert record["description"] == "A & B with spaces"
    assert record["description_tokens"] == si.tokens("A & B with spaces")


def test_export_splits_languages(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """One file per language, each holding only its own pages."""
    out = tmp_path / "search"
    si.write_search(si.build_index(str(src)), str(out))
    assert sorted(path.name for path in out.iterdir()) == [
        "site_index.en.json",
        "site_index.ru.json",
        "site_index.zh.json",
    ]
    en = json.loads((out / "site_index.en.json").read_text(encoding="utf-8"))
    ru = json.loads((out / "site_index.ru.json").read_text(encoding="utf-8"))
    zh = json.loads((out / "site_index.zh.json").read_text(encoding="utf-8"))
    assert en["lang"] == "en" and en["version"] == si.INDEX_VERSION
    assert "/ru/example/" not in {page["url"] for page in en["pages"]}
    assert {page["url"] for page in ru["pages"]} == {"/ru/example/"}
    assert {page["url"] for page in zh["pages"]} == {"/zh/example/"}


def test_export_has_only_search_fields(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """A record is the text plus the sorted, space-separated token lists."""
    record = export(si, src, tmp_path)["/constructor/"]
    assert set(record) == {
        "url",
        "title",
        "description",
        "folders",
        "title_tokens",
        "head_tokens",
        "keyword_tokens",
        "description_tokens",
    }
    assert record["description"] == "A & B with spaces"
    assert record["title_tokens"] == "constructor"
    assert record["description_tokens"] == " ".join(sorted(set(si.tokens("A & B with spaces"))))
    assert record["folders"] == ["constructor"]


def test_export_empty_title_fallback(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """An untitled page takes its last URL segment, .php dropped, in the export only."""
    pages = si.build_index(str(src))
    assert next(page for page in pages if page.url == "/untitled/").title == ""
    records = export(si, src, tmp_path)
    assert records["/untitled/"]["title"] == "untitled"
    assert records["/file/name_without_title.php"]["title"] == "name_without_title"


def test_export_is_deterministic(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """Two runs over one tree write byte-identical export and full index files."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    si.write_search(si.build_index(str(src)), str(first))
    si.write_search(si.build_index(str(src)), str(second))
    for name in ("site_index.en.json", "site_index.ru.json", "site_index.zh.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    si.write_index(si.build_index(str(src)), str(tmp_path / "one.json"))
    si.write_index(si.build_index(str(src)), str(tmp_path / "two.json"))
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()


def test_export_skips_redirect_stubs(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """A redirect stub in the sitemap is in no export file."""
    assert "/old/" not in export(si, src, tmp_path)


def test_export_drops_php_extension_from_file_folder(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """The ".php" of a file URL is not a search word; the full index keeps it."""
    pages = si.build_index(str(src))
    si.write_search(pages, str(tmp_path / "search"))
    data = json.loads((tmp_path / "search" / "site_index.en.json").read_text(encoding="utf-8"))
    record = next(page for page in data["pages"] if page["url"] == "/file/name_without_title.php")
    assert record["folders"] == ["file", "name title without"]
    full = next(page for page in pages if page.url == "/file/name_without_title.php")
    assert full.folders[-1] == ["name", "without", "title", "php"]


def test_export_carries_alias_groups(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """Every language file has the synonym groups as token strings, the main name first."""
    aliases = tmp_path / "aliases.json"
    aliases.write_text(
        json.dumps(
            {
                "regular expressions": ["regex", "regexp"],
                "kubernetes": ["k8s", "kube"],
                "docker compose": ["docker-compose"],
                "dotnet": ["C#", ".NET"],
            }
        ),
        encoding="utf-8",
    )
    groups = si.alias_groups(str(aliases))
    assert groups == [
        ["regular expression", "regex", "regexp"],
        # "k8s" tokenizes like "kubernetes", so it is dropped as a second name.
        ["kubernete", "kube"],
        ["dotnet", "csharp"],
    ]
    si.write_search(si.build_index(str(src)), str(tmp_path / "search"), groups)
    for lang in ("en", "ru", "zh"):
        data = json.loads((tmp_path / "search" / f"site_index.{lang}.json").read_text(encoding="utf-8"))
        assert data["aliases"] == groups


def test_alias_groups_of_a_missing_or_broken_file(si: ModuleType, tmp_path: Path) -> None:
    """No file or bad JSON gives no groups instead of a failed export."""
    assert si.alias_groups(str(tmp_path / "none.json")) == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert si.alias_groups(str(broken)) == []


def test_heading_tokens_by_level(si: ModuleType, src: Path) -> None:
    """Literal h1, h2 and h3 words are kept by level; comments, PHP-built headings and h4 are not."""
    page = next(page for page in si.build_index(str(src)) if page.url == "/headings/")
    assert page.h1_tokens == ["smartrac", "miniweb", "guide"]
    # An attribute ending in '."' is markup, not PHP: the abbr heading is kept, and
    # so is the text of a link whose address PHP builds.
    assert page.h2_tokens == ["anchor", "canary", "task", "block", "an", "ip", "range", "see", "the", "list", "book", "by", "ritchie", "quoted"]
    assert page.h3_tokens == ["boundarie", "of", "section", "canary", "again"]
    plain = next(page for page in si.build_index(str(src)) if page.url == "/constructor/")
    assert (plain.h1_tokens, plain.h2_tokens, plain.h3_tokens) == ([], [], [])


def test_export_heading_fields_hold_only_new_words(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """A heading field leaves out the words a stronger field has; an empty one is not written."""
    records = export(si, src, tmp_path)
    record = records["/headings/"]
    # "guide" is in the title, "anchor" in the keywords, "section" and "of" in the
    # description, "canary" already in the h2; "an", "the" and "again" are
    # function words, which no heading field holds.
    assert record["h1_tokens"] == "miniweb smartrac"
    assert record["h2_tokens"] == "block book by canary ip list quoted range ritchie see task"
    assert record["h3_tokens"] == "boundarie"
    assert not {"h1_tokens", "h2_tokens", "h3_tokens"} & set(records["/constructor/"])


def test_title_and_keyword_words_are_taken_after_entities_are_decoded(si: ModuleType, src: Path, tmp_path: Path) -> None:
    """ "C&plus;&plus;" is the word "cpp"; the export title is plain text, the full index keeps the page's own."""
    page = next(page for page in si.build_index(str(src)) if page.url == "/entities/")
    assert page.title == "C&plus;&plus; classes &laquo;arrow&raquo;: notes"
    assert page.title_tokens == ["cpp", "classe", "arrow", "note"]
    assert page.head_tokens == ["cpp", "classe", "arrow"]
    assert page.keyword_tokens == ["cpp", "d", "r"]
    record = export(si, src, tmp_path)["/entities/"]
    assert record["title"] == "C++ classes «arrow»: notes"
    assert record["title_tokens"] == "arrow classe cpp note"


def test_k8s_is_the_word_kubernetes(si: ModuleType) -> None:
    """ "k8s" is one word, not the letters k, 8 and s that "s2k" and "2.5.8" also give."""
    assert si.tokens("K8s cluster") == ["kubernete", "cluster"]
    assert si.tokens("/dvps/k8s_install/") == ["dvp", "kubernete", "install"]
    assert si.tokens("mhash_keygen_s2k") == ["mhash", "keygen", "s", "2", "k"]
    assert si.tokens("k8sx k8s2") == ["k", "8", "sx", "k", "8", "s", "2"]
