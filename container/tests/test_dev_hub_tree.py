# version 1.0.0
"""Tests for tools/dev_hub/build_tree.py.

The fixture is a small language folder under a temporary src docroot, so the
expected tree, the PHP include and the subhub report are exact. One smoke
test runs the tool over the real /dev/rust/ tree and only checks that the
tree is the whole folder and that the PHP lints.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CONTAINER = Path(__file__).resolve().parents[1]
TOOLS = CONTAINER / "tools" / "dev_hub"
TOOL = TOOLS / "build_tree.py"

sys.path.insert(0, str(TOOLS))

import build_tree as bt  # noqa: E402  pylint: disable=wrong-import-position

PHP = shutil.which("php")


def php_str(text: str) -> str:
    """A single-quoted PHP string, the way the pages write a title."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def page_source(title: str, headings: list[str]) -> str:
    """A page that builds a BasePage and prints the given h2 chapters."""
    chapters = "\n".join(f'<h2 class="h2mTop">{heading}</h2>\n<p class="p2">Text of the chapter.</p>' for heading in headings)
    return f"<?php\n\nuse PhpEngine\\BasePage;\n\n$page = new BasePage(\n    lang: 'en',\n    title: {php_str(title)},\n);\n\necho $page->createHeader();\necho '\n{chapters}\n';\necho $page->createFooter();\n"


def put(path: Path, text: str) -> Path:
    """Write a fixture file, creating its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(name="demo")
def fixture_demo(tmp_path: Path) -> Path:
    """A language folder with sections, lessons and every kind of non-article."""
    lang = tmp_path / "src" / "dev" / "demo"
    put(lang / "index.php", page_source("Demo", ["The demo hub"]))
    put(lang / "index_legacy.php", page_source("Demo legacy hub", ["Old", "Older"]))
    put(lang / "install.php", page_source("Install demo", ["Download"]))
    put(lang / "legacy.php", "$page_title = 'Legacy demo';\nrequire_once($root . '/.php/010_header.php');\n")
    put(lang / "_frag.php", page_source("A fragment", ["One", "Two"]))
    put(lang / "img" / "logo.php", page_source("A logo", ["One", "Two"]))
    put(lang / "demo" / "sample.php", page_source("A demo page", ["One", "Two"]))
    put(lang / "notes.txt", "not a page\n")

    put(lang / "basics" / "index.php", page_source("Demo basics", ["Introduction", "What it is", "First program"]))
    put(lang / "course" / "index.php", page_source("Demo course", []))
    put(lang / "course" / "001_start.php", page_source("Start here", ["Install", "First program"]))
    put(
        lang / "course" / "002_quote.php",
        page_source("It's alive, the compiler says", ["Overview", "The program", "Rustc"]),
    )
    put(lang / "course" / "010_last.php", page_source("The last lesson", ["Threads", "Tests"]))
    put(lang / "course" / "_course_parts_inc.php", "<?php\nreturn array();\n")
    put(lang / "course" / "RelatedArticles.php", "<?php\nclass RelatedArticles {}\n")
    put(
        lang / "course" / "redirect.php",
        "<?php\nrequire_once($root . '/.php/010_header.php');\nheader('Location: /dev/demo/');\n",
    )

    put(lang / "topics" / "index.php", page_source("Demo topics", ["Arrays", "Strings"]))
    put(lang / "topics" / "alpha.php", page_source("Alpha", ["One", "Two"]))
    put(lang / "topics" / "beta.php", page_source("Beta", ["Three", "Four"]))
    put(lang / "topics" / "deep" / "index.php", page_source("Deep topics", ["One", "Two"]))
    put(lang / "topics" / "deep" / "one.php", page_source("One", ["Introduction", "A", "B"]))
    put(lang / "topics" / "deep" / "two.php", page_source("Two", ["C", "D"]))
    put(lang / "topics" / "thin" / "index.php", page_source("Thin topics", ["Intro"]))
    put(lang / "topics" / "thin" / "extra.php", page_source("Thin extra", ["Summary"]))
    return lang


def run_cli(*args: object) -> subprocess.CompletedProcess[str]:
    """Run the tool as the pages and the pre-commit check do."""
    return subprocess.run(
        [sys.executable, str(TOOL), *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        check=False,
    )


EXPECTED_TREE = """\
demo/
├── basics/ (1)
│   └── Demo basics
├── course/ (4)
│   ├── Demo course
│   ├── 1 Start here
│   ├── 2 It's alive, the compiler says
│   └── 10 The last lesson
├── topics/ (8)
│   ├── Demo topics
│   ├── deep/ (3)
│   │   ├── Deep topics
│   │   ├── One
│   │   └── Two
│   ├── thin/ (2)
│   │   ├── Thin topics
│   │   └── Thin extra
│   ├── Alpha
│   └── Beta
├── Install demo
└── Legacy demo"""


def nodes_of(path: Path) -> tuple[Path, list[bt.Node]]:
    """Scan a fixture folder and return its docroot and top-level nodes."""
    root = path.resolve()
    src = bt.find_src_root(root)
    assert src is not None
    return src, bt.nodes_of(bt.scan_folder(root, src, is_root=True))


def test_plain_tree(demo: Path) -> None:
    """Every article is in the tree once, in folder order, without non-articles."""
    _src, nodes = nodes_of(demo)
    assert bt.render_plain("demo/", nodes) == EXPECTED_TREE


def test_urls_and_numbered_titles(demo: Path) -> None:
    """index.php is the folder URL, a lesson keeps .php and takes its number."""
    _src, nodes = nodes_of(demo)
    course = next(node for node in nodes if node["text"] == "course/")
    children = course["children"]
    assert children is not None
    assert [child["url"] for child in children] == [
        "/dev/demo/course/",
        "/dev/demo/course/001_start.php",
        "/dev/demo/course/002_quote.php",
        "/dev/demo/course/010_last.php",
    ]
    assert [child["text"] for child in children][1:3] == ["1 Start here", "2 It's alive, the compiler says"]


def test_branches_start_collapsed(demo: Path) -> None:
    """Every branch is a collapsed node, so the tree opens as a section list."""
    _src, nodes = nodes_of(demo)
    branches = [node for node in nodes if "children" in node]
    assert [node["text"] for node in branches] == ["basics/", "course/", "topics/"]
    assert all(node["collapsed"] is True for node in branches)
    assert all("collapsed" not in node for node in nodes if "children" not in node)


def test_hub_index_is_not_its_own_article(demo: Path) -> None:
    """The index.php of the language folder is the page, not a leaf of its tree."""
    _src, nodes = nodes_of(demo)
    urls = json.dumps(nodes)
    assert '"/dev/demo/"' not in urls
    assert "Demo" not in [node["text"] for node in nodes]


def test_php_include(demo: Path) -> None:
    """The include returns TreeList nodes and keeps PHP quoting intact."""
    src = bt.find_src_root(demo.resolve())
    assert src is not None
    folder = bt.scan_folder(demo.resolve(), src, is_root=True)
    nodes = bt.nodes_of(folder)
    text = bt.render_php(folder, "1.0.0")
    assert text.startswith("<?php\n\n# version 1.0.0\n")
    assert "return array(\n" in text
    assert "    'url' => '/dev/demo/course/002_quote.php',\n" in text
    assert "'text' => '2 It\\'s alive, the compiler says'," in text
    assert "'collapsed' => true," in text
    assert bt.render_plain("demo/", nodes) == EXPECTED_TREE


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_php_include_lints(demo: Path, tmp_path: Path) -> None:
    """php -l accepts the generated include, escaped quote and all."""
    src = bt.find_src_root(demo.resolve())
    assert src is not None
    folder = bt.scan_folder(demo.resolve(), src, is_root=True)
    target = tmp_path / "include.php"
    target.write_text(bt.render_php(folder, "1.0.0"), encoding="utf-8")
    assert PHP is not None
    proc = subprocess.run([PHP, "-l", str(target)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout


TREE_LIST_MODULE = CONTAINER / "src" / ".php" / "_modules" / "tree_list.php"


@pytest.mark.skipif(
    PHP is None or not TREE_LIST_MODULE.is_file(),
    reason="php or the site tree-list module is missing",
)
def test_php_renders_the_tree(demo: Path, tmp_path: Path) -> None:
    """TreeList turns the include into the nav the course tree uses."""
    src = bt.find_src_root(demo.resolve())
    assert src is not None
    folder = bt.scan_folder(demo.resolve(), src, is_root=True)
    target = tmp_path / "include.php"
    target.write_text(bt.render_php(folder, "1.0.0"), encoding="utf-8")
    runner = tmp_path / "render.php"
    runner.write_text(
        f"<?php\nrequire '{CONTAINER}/src/.php/_modules/tree_list.php';\n$nodes = require '{target}';\necho PhpEngine\\Modules\\TreeList::render('demo/', $nodes, 'en');\n",
        encoding="utf-8",
    )
    assert PHP is not None
    proc = subprocess.run([PHP, str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    html = proc.stdout
    assert '<nav class="treelist" aria-label="demo/"' in html
    assert '<p class="treelist__root">demo/</p>' in html
    assert '<span class="treelist__branch" data-collapsed>course/ <span class="treelist__count">(4)</span></span>' in html
    assert '<a href="/dev/demo/topics/deep/">Deep topics</a>' in html
    assert html.count("data-collapsed") == 5


def test_write_is_idempotent_and_bumps_the_version(demo: Path) -> None:
    """A second run over the same tree changes nothing; a new page bumps the patch."""
    src = bt.find_src_root(demo.resolve())
    assert src is not None
    target = demo / bt.INCLUDE_NAME
    folder = bt.scan_folder(demo.resolve(), src, is_root=True)
    assert bt.write_include(folder, target) == ("1.0.0", True)
    first = target.read_text(encoding="utf-8")
    assert bt.write_include(folder, target) == ("1.0.0", False)
    assert target.read_text(encoding="utf-8") == first

    put(demo / "topics" / "gamma.php", page_source("Gamma", ["One", "Two"]))
    folder = bt.scan_folder(demo.resolve(), src, is_root=True)
    assert bt.write_include(folder, target) == ("1.0.1", True)
    assert "'/dev/demo/topics/gamma.php'" in target.read_text(encoding="utf-8")
    assert not (demo / (bt.INCLUDE_NAME + ".tmp")).exists()


def test_check(demo: Path) -> None:
    """--check passes on a fresh include and fails on a stale or missing one."""
    src = bt.find_src_root(demo.resolve())
    assert src is not None
    target = demo / bt.INCLUDE_NAME
    folder = bt.scan_folder(demo.resolve(), src, is_root=True)

    assert bt.check_include(folder, target) == (False, f"{target} is missing; run the tool with --write")
    bt.write_include(folder, target)
    assert bt.check_include(folder, target) == (True, f"{target} is up to date")

    put(demo / "install_extra.php", page_source("Install extra", ["Download", "Run"]))
    folder = bt.scan_folder(demo.resolve(), src, is_root=True)
    assert bt.check_include(folder, target) == (False, f"{target} is out of date; run the tool with --write")
    assert run_cli(demo, "--check").returncode == 1


def test_subhubs(demo: Path) -> None:
    """Only a folder with an index and two full articles is a subhub."""
    src = bt.find_src_root(demo.resolve())
    assert src is not None
    folder = bt.scan_folder(demo.resolve(), src, is_root=True)
    lines, found = bt.render_subhubs(folder, show_all=True)
    assert lines == [
        "demo/",
        "  basics/  skip    articles 1, full 1  (full articles 1, 2 needed)",
        "  course/  SUBHUB  articles 4, full 3",
        "  topics/  SUBHUB  articles 3, full 3",
        "    deep/  SUBHUB  articles 3, full 3",
        "    thin/  skip    articles 2, full 0  (full articles 0, 2 needed)",
    ]
    assert found == 3

    subhubs, found = bt.render_subhubs(folder)
    assert found == 3
    assert subhubs == [
        "demo/",
        "  course/  SUBHUB  articles 4, full 3",
        "  topics/  SUBHUB  articles 3, full 3",
        "    deep/  SUBHUB  articles 3, full 3",
    ]


def test_chapters_skip_intro_headings() -> None:
    """An Introduction or an Overview is not a chapter of real content."""
    source = page_source(
        "Headings",
        ["Introduction", "Intro", "Overview", "Contents", "Table of contents", "Summary", "Real one", "Real two"],
    )
    assert bt.chapters(source) == ("Real one", "Real two")
    assert len(bt.chapters(page_source("Only intro", ["Introduction", "Overview"]))) == 0


def test_page_without_two_chapters_is_not_full() -> None:
    """A page with one real chapter does not count as a full article."""
    chapter_page = bt.Page(Path("a.php"), "/a.php", "A", None, ("One",))
    two_chapter_page = bt.Page(Path("b.php"), "/b.php", "B", None, ("One", "Two"))
    assert not chapter_page.is_full
    assert two_chapter_page.is_full


def test_title_fallbacks() -> None:
    """A page_title variable and a <title> tag name a page too."""
    assert bt.page_title("$page_title = 'Legacy name';") == "Legacy name"
    assert bt.page_title("<title>Tag name</title>") == "Tag name"
    assert bt.page_title("no title here") == ""


def test_cli_plain(demo: Path) -> None:
    """The plain tree is what the page shows, fully opened."""
    proc = run_cli(demo)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == EXPECTED_TREE


def test_cli_json(demo: Path) -> None:
    """--json gives the nodes to another tool."""
    proc = run_cli(demo, "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["root"] == "demo/"
    assert [node["text"] for node in data["nodes"]] == ["basics/", "course/", "topics/", "Install demo", "Legacy demo"]


def test_cli_php(demo: Path) -> None:
    """--php prints the include without writing it."""
    proc = run_cli(demo, "--php")
    assert proc.returncode == 0, proc.stderr
    assert "return array(" in proc.stdout
    assert not (demo / bt.INCLUDE_NAME).exists()


def test_cli_write(demo: Path) -> None:
    """--write puts the include into the folder and reports it."""
    proc = run_cli(demo, "--write")
    assert proc.returncode == 0, proc.stderr
    assert "version 1.0.0, written" in proc.stdout
    assert (demo / bt.INCLUDE_NAME).is_file()
    assert run_cli(demo, "--check").returncode == 0


def test_cli_usage_errors(tmp_path: Path) -> None:
    """A missing folder, a folder outside a docroot and two modes exit 2."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    put(outside / "x.php", page_source("X", ["One", "Two"]))
    assert run_cli(tmp_path / "missing").returncode == 2
    assert run_cli(outside).returncode == 2
    assert run_cli(outside, "--write", "--json").returncode == 2
    assert run_cli(outside, "--all").returncode == 2


def test_real_rust_tree(tmp_path: Path) -> None:
    """The real rust folder builds a tree of the whole folder and lints."""
    lang = CONTAINER / "src" / "dev" / "rust"
    if not lang.is_dir():
        pytest.skip("no rust course in this tree yet")
    proc = run_cli(lang, "--php")
    assert proc.returncode == 0, proc.stderr
    include = proc.stdout
    assert "'url' => '/dev/rust/course/'" in include
    assert "'url' => '/dev/rust/course/001_what_is_rust.php'" in include
    assert "'url' => '/dev/rust/basics/'" in include
    urls = [line.split("'url' => ")[1].strip("',") for line in include.splitlines() if "'url' => " in line]
    assert urls
    assert all(url.startswith("/dev/rust/") for url in urls)

    plain = run_cli(lang)
    assert plain.returncode == 0, plain.stderr
    assert plain.stdout.splitlines()[0] == "rust/"
    assert len(plain.stdout.splitlines()) > 100

    if PHP is not None:
        target = tmp_path / "rust_inc.php"
        target.write_text(include, encoding="utf-8")
        assert subprocess.run([PHP, "-l", str(target)], capture_output=True, check=False).returncode == 0
