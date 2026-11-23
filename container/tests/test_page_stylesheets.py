# version 0.2.1
"""Each tree's page classes and pages link that tree's site sheet.

  src/          English            /.css/site.css
  src/ru/       Russian            /.css/ru.css
  src/section/    Russian QA         /.css/section.css
  src/extra/       Russian, ao        /.css/extra.css

Every tree keeps its own copy of the page classes in its .php/ folder, and
the sheet is set there: the default of BasePage and SidePage, the link that
Page writes. ArticlePage inherits the BasePage default of its folder. A
class copied from another tree keeps that tree's sheet: the English Page
linked ao.css and the English SidePage ru.css, so English pages showed
the Russian "Предыдущая статья" labels of those sheets.

Checked:

* every site sheet a page class names is its tree's sheet, the usage
  example in its docblock included, and BasePage, SidePage and Page name
  one; ArticlePage names none;
* a page that passes a site sheet as css: passes its tree's sheet; a
  page-scoped sheet such as /.css/good-design.css is not a site sheet;
* every page class a page requires exists: nine English pages under
  src/qa/ required /qa/.php/Page.php, a folder that never existed.

Static check of the PHP source, no web server. Comments in pages are
ignored. Editor backup copies ("index copy.php", "copy2.php") are skipped.

Usage (from container/):
  ./tools/run-tests-venv.sh -- tests/test_page_stylesheets.py -q

Env:
  SRC_DIR   docroot to scan (default container/src, or /data in the
            compose test container)
"""

import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest
import site_profile

CONTAINER = Path(__file__).resolve().parents[1]
_DEFAULT_SRC = CONTAINER / "src" if (CONTAINER / "src").is_dir() else Path("/data")
SRC = Path(os.environ.get("SRC_DIR") or _DEFAULT_SRC)


# Longest prefix first: src/section/ is inside src/ru/.
def _tree_sheets() -> list[tuple[str, str]]:
    """(src prefix, sheet) from SITE_TREE_SHEETS, longest prefix first."""
    pairs: list[tuple[str, str]] = []
    for item in site_profile.get_list("SITE_TREE_SHEETS", "src=/.css/site.css"):
        if "=" not in item:
            continue
        prefix, sheet = item.split("=", 1)
        pairs.append((prefix[len("src/") :] if prefix.startswith("src/") else "", sheet))
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


TREE_SHEETS = _tree_sheets()
pytestmark = pytest.mark.skipif(
    not site_profile.get("SITE_TREE_SHEETS"),
    reason="SITE_TREE_SHEETS is unset: this module checks the tree layout of a configured site",
)
PAGE_CLASSES = ("BasePage", "ArticlePage", "ArticlePage", "Page")

_SITE_SHEET = re.compile(r"/\.css/[A-Za-z0-9_.-]+\.css")
_CSS_ARG = re.compile(r"\bcss\s*:\s*(['\"])(/\.css/[A-Za-z0-9_.-]+\.css)\1")
_CLASS_REQUIRE = re.compile(r"\$root\s*\.\s*(['\"])(/(?:[\w-]+/)*\.php/(?:BasePage|ArticlePage|SidePage|Page)\.php)\1")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^[ \t]*(?:#|//).*$", re.M)
_BACKUP_COPY = re.compile(r"(?: copy(?:\(\d+\))?|copy\d+)\.php$")


def tree_sheet(rel: str) -> str:
    """The site sheet of the tree that holds this src-relative path."""
    return next(sheet for prefix, sheet in TREE_SHEETS if rel.startswith(prefix))


def _without_comments(php: str) -> str:
    """The source with comments removed and every line kept in place."""
    php = _BLOCK_COMMENT.sub(lambda match: "\n" * match.group(0).count("\n"), php)
    return _LINE_COMMENT.sub("", php)


def _class_exists(path: str) -> bool:
    return (SRC / path.lstrip("/")).is_file()


def findings(php: str, rel: str, exists: Callable[[str], bool] = _class_exists) -> list[str]:
    """A site sheet from another tree, and page classes that do not exist."""
    code = _without_comments(php)
    sheet = tree_sheet(rel)
    site_sheets = {tree for _, tree in TREE_SHEETS}
    found = []
    for match in _CSS_ARG.finditer(code):
        if match.group(2) in site_sheets and match.group(2) != sheet:
            found.append((match.start(), f"css {match.group(2)}, this tree uses {sheet}"))
    for match in _CLASS_REQUIRE.finditer(code):
        if not exists(match.group(2)):
            found.append((match.start(), f"requires {match.group(2)}, which does not exist"))
    return [f"line {code.count(chr(10), 0, position) + 1}: {message}" for position, message in sorted(found)]


def page_class_params() -> list:
    params = []
    for prefix, sheet in TREE_SHEETS:
        for name in PAGE_CLASSES:
            path = SRC / prefix / ".php" / f"{name}.php"
            if path.is_file():
                params.append(pytest.param(path, name, sheet, id=f"{prefix}.php/{name}.php"))
    return params


def page_files() -> list[Path]:
    """PHP files under src, without engine dot-folders and backup copies."""
    files = []
    for path in sorted(SRC.rglob("*.php")):
        parts = path.relative_to(SRC).parts
        if path.is_file() and not any(part.startswith(".") for part in parts) and not _BACKUP_COPY.search(path.name):
            files.append(path)
    return files


#: The logic cases below run on this layout, whatever the site's profile holds.
DEMO_TREES = [
    ("section/", "/.css/section.css"),
    ("extra/", "/.css/extra.css"),
    ("ru/", "/.css/ru.css"),
    ("", "/.css/site.css"),
]


@pytest.fixture(name="demo_trees")
def fixture_demo_trees(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point tree_sheet() and findings() at the demo layout."""
    monkeypatch.setitem(globals(), "TREE_SHEETS", DEMO_TREES)


@pytest.mark.usefixtures("demo_trees")
@pytest.mark.parametrize(
    ("rel", "sheet"),
    [
        ("index.php", "/.css/site.css"),
        ("qa/testcomplete/index.php", "/.css/site.css"),
        ("ru/linux/index.php", "/.css/ru.css"),
        ("section/index.php", "/.css/section.css"),
        ("extra/blog/index.php", "/.css/extra.css"),
        ("aotest/index.php", "/.css/site.css"),
    ],
)
def test_tree_sheet(rel: str, sheet: str) -> None:
    assert tree_sheet(rel) == sheet


@pytest.mark.usefixtures("demo_trees")
@pytest.mark.parametrize(
    ("php", "rel", "expected"),
    [
        pytest.param("$page = new BasePage(css: '/.css/section.css');", "section/index.php", [], id="ru-qa-own-sheet"),
        pytest.param(
            "$page = new BasePage(\n    css: '/.css/site.css',\n);",
            "ru/index.php",
            ["line 2: css /.css/site.css, this tree uses /.css/ru.css"],
            id="ru-page-with-english-sheet",
        ),
        pytest.param("$page = new BasePage(css: '/.css/good-design.css');", "a/10.php", [], id="page-scoped-sheet"),
        pytest.param("# css: '/.css/extra.css',\n/* css: '/.css/extra.css' */", "index.php", [], id="comments-ignored"),
        pytest.param(
            "require_once($root . '/qa/.php/Page.php');\nrequire_once($root.'/.php/ArticlePage.php');",
            "qa/index.php",
            ["line 1: requires /qa/.php/Page.php, which does not exist"],
            id="missing-class",
        ),
    ],
)
def test_findings(php: str, rel: str, expected: list[str]) -> None:
    assert findings(php, rel, exists=lambda path: path == "/.php/ArticlePage.php") == expected


def test_every_tree_has_a_base_page() -> None:
    """A tree with page classes has a BasePage; a new site may have none yet."""
    with_classes = [prefix for prefix, _ in TREE_SHEETS if any((SRC / prefix / ".php" / f"{name}.php").is_file() for name in PAGE_CLASSES)]
    missing = [prefix + ".php/BasePage.php" for prefix in with_classes if not (SRC / prefix / ".php" / "BasePage.php").is_file()]
    assert not missing, f"no BasePage in {missing}"


@pytest.mark.parametrize(("path", "name", "sheet"), page_class_params())
def test_page_class_names_its_tree_sheet(path: Path, name: str, sheet: str) -> None:
    named = sorted(set(_SITE_SHEET.findall(path.read_text(encoding="utf-8"))))
    rel = path.relative_to(SRC).as_posix()
    if name == "ArticlePage":
        assert not named, f"{rel} names {named}: ArticlePage inherits {sheet} from the BasePage of its folder"
    else:
        assert named == [sheet], f"{rel} names {named or 'no site sheet'}; the pages of this tree use {sheet}"


def test_pages_use_their_tree_sheet_and_existing_classes() -> None:
    files = page_files()
    assert files, f"no PHP files under {SRC}"
    bad = {}
    for path in files:
        rel = path.relative_to(SRC).as_posix()
        found = findings(path.read_text(encoding="utf-8", errors="replace"), rel)
        if found:
            bad[rel] = found
    report = "\n".join(f"  {page}: {'; '.join(found)}" for page, found in bad.items())
    assert not bad, f"{len(bad)} pages pass another tree's sheet or require a page class that does not exist:\n{report}"
