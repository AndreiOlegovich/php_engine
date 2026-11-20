#!/usr/bin/env python3
# version 1.0.0
"""Build the article tree of a /dev/<language>/ hub from its folder structure.

The hub page of a language (/dev/rust/, /dev/erlang/, ...) shows the cards of
its sections and then one expandable tree of every article under the folder.
The tree is not written by hand: this tool walks the folder, takes the title
of every article page and writes the node list that TreeList renders
(the site tree-list module), so a new page appears in the tree with one
command and no page edit.

    python3 container/tools/dev_hub/build_tree.py container/src/dev/rust
    python3 container/tools/dev_hub/build_tree.py container/src/dev/rust --write
    python3 container/tools/dev_hub/build_tree.py container/src/dev/rust --check
    python3 container/tools/dev_hub/build_tree.py container/src/dev/php --subhubs

Without a flag the tool prints the tree as plain text, the way it reads on
the page but fully opened. --write puts it into <folder>/_hub_tree_inc.php,
the file the hub requires; --check exits 1 when that file is out of date, so
a page that was added without the tool is caught before a commit.

What is a page: a .php file that builds a page object (new ArticlePage, Page,
BasePage or DictPage), uses a legacy header include, or requires a
_template.php. Includes, PascalCase helpers (RelatedArticles.php), redirect
stubs, index_legacy.php (the old hub kept for history) and the hub's own
index.php are not articles.

The tree follows the folders: a folder is a branch named "<name>/", its
index.php is the first leaf of the branch, its subfolders come next and its
other pages after them. Pages named NN_something.php sort by NN and show as
"NN Title", like the parts of the rust course; every other page sorts by
file name. All branches start collapsed, so the tree opens as a list of
sections; "Expand all" (treelist.js) opens the whole tree.

--subhubs prints the folders that earn a card of their own: a folder with an
index.php and at least two full articles, where a full article has at least
two <h2> chapters of real content (Introduction, Overview, Contents and the
like do not count). --all adds the folders that do not qualify, with the
reason, and the nesting of subhubs inside subhubs.

Usage error exits 2, a stale include (--check) exits 1, everything else 0.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

INCLUDE_NAME = "_hub_tree_inc.php"
LEGACY_INDEX_NAME = "index_legacy.php"
FIRST_VERSION = "1.0.0"

EXAMPLES = """\
examples:
  build_tree.py container/src/dev/rust
  build_tree.py container/src/dev/rust --write
  build_tree.py container/src/dev/erlang --check
  build_tree.py container/src/dev/php --subhubs --all
  build_tree.py container/src/dev/ruby --json
"""

# A page builds a page object, or keeps the legacy header, or is generated.
PAGE_CTOR_RE = re.compile(r"new\s+(?:ArticlePage|Page|BasePage|DictPage)\s*\(")
LEGACY_HEAD_RE = re.compile(
    r"010_header\.php|020_header|_head_inc\.php|080_counter|090_bottom"
    r"|_footer\.php|header-up\.php"
)
TEMPLATE_RE = re.compile(r"require_once\s*\(\s*\$root\s*\.\s*'[^']*_template\.php'\s*\)")

# A stub that only sends the reader elsewhere is not an article.
REDIRECT_RES = (
    re.compile(r"header\s*\(\s*['\"]Location:\s*(/[^'\"]+)", re.IGNORECASE),
    re.compile(r"http-equiv\s*=\s*['\"]refresh['\"][^>]*?url\s*=\s*(/[^\"'\s>]+)", re.IGNORECASE),
    re.compile(
        r"(?:window\.)?location(?:\.href|\.assign|\.replace)?\s*[=(]\s*['\"](/[^'\"]+)",
        re.IGNORECASE,
    ),
)

# The title of a page: the constructor argument, the legacy variable, the tag.
# A single-quoted PHP string may hold an escaped quote, as in "rustc\'s
# suggestions", so the pattern takes escape pairs and they are undone after.
TITLE_RES = (
    re.compile(r"title:\s*'((?:[^'\\]|\\.)*)'"),
    re.compile(r'title:\s*"((?:[^"\\]|\\.)*)"'),
    re.compile(r"\$page_title\s*=\s*'((?:[^'\\]|\\.)*)'"),
    re.compile(r'\$page_title\s*=\s*"((?:[^"\\]|\\.)*)"'),
    re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE),
)
PHP_ESCAPE_RE = re.compile(r"\\(['\"\\])")

H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.DOTALL | re.IGNORECASE)
H2_OPEN_RE = re.compile(r"<h2[^>]*>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
NUMBER_RE = re.compile(r"^(\d+)[_-]")
VERSION_RE = re.compile(r"^# version (\d+)\.(\d+)\.(\d+)$", re.MULTILINE)
VERSION_LINE_RE = re.compile(r"^# version \d+\.\d+\.\d+$", re.MULTILINE)

# Headings that are not a chapter of real content, so a page made of them and
# nothing else is not a full article.
NON_CHAPTERS = frozenset(
    {
        "introduction",
        "intro",
        "overview",
        "contents",
        "table of contents",
        "summary",
        "введение",
        "содержание",
    }
)

# Names that are never an article file.
EXCLUDED_FILES = frozenset({"RelatedArticles.php", LEGACY_INDEX_NAME})
EXCLUDED_SUFFIXES = ("_inc.php",)


class Node(TypedDict, total=False):
    """One node of the tree list: a name, a link, children or collapsed."""

    text: str
    url: str
    children: list[Node]
    collapsed: bool


@dataclass
class Page:
    """One article page of the folder structure."""

    path: Path
    url: str
    title: str
    number: int | None
    chapters: tuple[str, ...]

    @property
    def is_full(self) -> bool:
        """True when the page has at least two chapters of real content."""
        return len(self.chapters) >= 2

    def node(self) -> Node:
        """The TreeList node of the page."""
        text = self.title if self.number is None else f"{self.number} {self.title}"
        return {"text": text, "url": self.url}


@dataclass
class Folder:  # pylint: disable=too-many-instance-attributes  # one field per part of a branch
    """One directory of the language folder and the pages directly in it."""

    path: Path
    name: str
    url: str
    command_path: str = ""
    index: Page | None = None
    pages: list[Page] = field(default_factory=list)
    folders: list[Folder] = field(default_factory=list)

    @property
    def article_count(self) -> int:
        """The pages directly in the folder, the index page included."""
        return len(self.pages) + (1 if self.index is not None else 0)

    @property
    def full_count(self) -> int:
        """The full articles directly in the folder."""
        pages = list(self.pages)
        if self.index is not None:
            pages.append(self.index)
        return sum(1 for page in pages if page.is_full)

    @property
    def is_subhub(self) -> bool:
        """True when the folder earns a card: an index and two full articles."""
        return self.index is not None and self.full_count >= 2

    @property
    def skip_reason(self) -> str:
        """Why the folder is not a subhub."""
        if self.index is None:
            return "no index.php"
        if self.full_count < 2:
            return f"full articles {self.full_count}, 2 needed"
        return ""


def read_source(path: Path) -> str | None:
    """File text as UTF-8, else cp1251 for the legacy pages."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for encoding in ("cp1251", "koi8-r", "iso-8859-5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def page_title(source: str) -> str:
    """The title the page declares, or "" when it has none."""
    for pattern in TITLE_RES:
        match = pattern.search(source)
        if match is None:
            continue
        title = html.unescape(TAG_RE.sub("", PHP_ESCAPE_RE.sub(r"\1", match.group(1))))
        title = " ".join(title.split())
        if title:
            return title
    return ""


def chapters(source: str) -> tuple[str, ...]:
    """The h2 texts of the page, without the headings that name no content."""
    found = []
    for match in H2_RE.finditer(source):
        text = html.unescape(TAG_RE.sub("", match.group(1)))
        text = " ".join(text.split())
        if text and text.lower().strip(" .:") not in NON_CHAPTERS:
            found.append(text)
    return tuple(found)


def page_url(path: Path, src: Path) -> str:
    """The site URL of a page: index.php becomes the trailing-slash folder."""
    rel = path.relative_to(src).as_posix()
    if rel.endswith("/index.php"):
        rel = rel[: -len("index.php")]
    return "/" + rel


def folder_url(path: Path, src: Path) -> str:
    """The site URL of a folder."""
    return "/" + path.relative_to(src).as_posix().strip("/") + "/"


def lesson_number(name: str) -> int | None:
    """The NN of an NN_name.php page, else None."""
    match = NUMBER_RE.match(name)
    return int(match.group(1)) if match else None


def is_page_file_name(name: str) -> bool:
    """True when the file name can be an article page (content checked later)."""
    if not name.endswith(".php"):
        return False
    if name.startswith((".", "_")):
        return False
    if name in EXCLUDED_FILES:
        return False
    if name[0].isupper():
        return False
    if name.endswith(EXCLUDED_SUFFIXES) or " copy" in name or name.startswith("copy("):
        return False
    # 010_header.php and its neighbours are includes of the legacy layout.
    return not re.match(r"^\d+_(?:header|footer|counter|bottom|menu|tcont)\b", name)


def is_excluded_dir_name(name: str) -> bool:
    """True for engine, image, demo, archive and editor-duplicate folders."""
    if name.startswith((".", "_")):
        return True
    if name in ("img", "demo") or name.endswith("img"):
        return True
    if name.startswith("arj"):
        return True
    return " copy" in name


def is_page_source(source: str) -> bool:
    """True when the source builds a page or keeps the legacy header."""
    return bool(PAGE_CTOR_RE.search(source) or LEGACY_HEAD_RE.search(source) or TEMPLATE_RE.search(source))


def is_redirect_stub(source: str) -> bool:
    """True when the page only redirects and builds no page object."""
    if PAGE_CTOR_RE.search(source):
        return False
    return any(pattern.search(source) for pattern in REDIRECT_RES)


def load_page(path: Path, src: Path) -> Page | None:
    """The page record of a file, or None when the file is not an article."""
    if not is_page_file_name(path.name):
        return None
    source = read_source(path)
    if source is None or not is_page_source(source) or is_redirect_stub(source):
        return None
    return Page(
        path=path,
        url=page_url(path, src),
        title=page_title(source) or path.stem.replace("_", " "),
        number=lesson_number(path.name),
        chapters=chapters(source),
    )


def page_sort_key(page: Page) -> tuple[int, int, str]:
    """Numbered pages by number, the rest by file name."""
    if page.number is None:
        return (1, 0, page.path.name.lower())
    return (0, page.number, page.path.name.lower())


def scan_folder(path: Path, src: Path, *, is_root: bool = False) -> Folder:
    """Walk a language folder into Folder and Page records."""
    folder = Folder(
        path=path,
        name=path.name,
        url=folder_url(path, src),
        command_path="container/" + os.path.relpath(path, src.parent).replace(os.sep, "/"),
    )
    with os.scandir(path) as scan:
        entries = sorted(scan, key=lambda entry: entry.name)
    for entry in entries:
        child_path = Path(entry.path)
        if entry.is_dir(follow_symlinks=False):
            if is_excluded_dir_name(entry.name):
                continue
            child = scan_folder(child_path, src)
            if nodes_of(child):
                folder.folders.append(child)
            continue
        if not entry.is_file(follow_symlinks=False):
            continue
        page = load_page(child_path, src)
        if page is None:
            continue
        if entry.name == "index.php":
            # The hub page itself is not an article of its own tree.
            if not is_root:
                folder.index = page
        else:
            folder.pages.append(page)
    folder.pages.sort(key=page_sort_key)
    return folder


def nodes_of(folder: Folder) -> list[Node]:
    """The TreeList nodes of a folder's children, in tree order."""
    nodes: list[Node] = []
    if folder.index is not None:
        nodes.append(folder.index.node())
    for child in folder.folders:
        children = nodes_of(child)
        if children:
            nodes.append({"text": child.name + "/", "children": children, "collapsed": True})
    for page in folder.pages:
        nodes.append(page.node())
    return nodes


def count_leaves(nodes: list[Node]) -> int:
    """How many leaves a node list holds, the way TreeList counts them."""
    total = 0
    for node in nodes:
        children = node.get("children")
        total += count_leaves(children) if children else 1
    return total


def render_plain(root: str, nodes: list[Node]) -> str:
    """The tree as plain text with every branch open."""
    lines = [root]

    def walk(items: list[Node], prefix: str) -> None:
        for position, node in enumerate(items):
            last = position == len(items) - 1
            children = node.get("children")
            text = str(node["text"])
            if children:
                text += f" ({count_leaves(children)})"
            lines.append(prefix + ("└── " if last else "├── ") + text)
            if children:
                walk(children, prefix + ("    " if last else "│   "))

    walk(nodes, "")
    return "\n".join(lines)


def php_quote(text: str) -> str:
    """A single-quoted PHP string."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def php_node(node: Node, indent: int) -> list[str]:
    """The PHP array of one TreeList node, indented by four spaces per level."""
    pad = "    " * indent
    lines = [pad + "array("]
    lines.append(pad + "    'text' => " + php_quote(str(node["text"])) + ",")
    if "url" in node:
        lines.append(pad + "    'url' => " + php_quote(str(node["url"])) + ",")
    children = node.get("children")
    if children:
        lines.append(pad + "    'children' => array(")
        for child in children:
            lines.extend(php_node(child, indent + 2))
        lines.append(pad + "    ),")
        lines.append(pad + "    'collapsed' => true,")
    lines.append(pad + "),")
    return lines


def render_php(folder: Folder, version: str) -> str:
    """The _hub_tree_inc.php file of a language folder."""
    url_dir = "/" + folder.url.strip("/") + "/"
    tool_path = folder.command_path
    lines = [
        "<?php",
        "",
        f"# version {version}",
        "",
        f"# Article tree of {url_dir}, generated from the folder structure by",
        "# container/tools/dev_hub/build_tree.py. Do not edit by hand: run",
        "#",
        f"#     python3 container/tools/dev_hub/build_tree.py {tool_path} --write",
        "#",
        "# after a page is added, renamed or removed, and check the result with",
        "# --check. The page renders the nodes with the site tree renderer",
        "# (the site tree-list module); /.js/treelist.js opens the branches.",
        "",
        "return array(",
    ]
    for node in nodes_of(folder):
        lines.extend(php_node(node, 1))
    lines.append(");")
    return "\n".join(lines) + "\n"


def version_of(text: str) -> str | None:
    """The # version value of a file, or None."""
    match = VERSION_RE.search(text)
    return ".".join(match.groups()) if match else None


def bump_version(version: str | None) -> str:
    """The next patch version: a regenerated file always changes its header."""
    if version is None:
        return FIRST_VERSION
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def without_version(text: str) -> str:
    """The file text with its # version line dropped, for comparison."""
    return VERSION_LINE_RE.sub("# version", text)


def write_include(folder: Folder, path: Path) -> tuple[str, bool]:
    """Write the include file; return (version, written).

    The version goes up only when the body changes, so running the tool twice
    over an unchanged tree touches nothing.
    """
    current = read_source(path)
    if current is None:
        version = FIRST_VERSION
        atomic_write(path, render_php(folder, version))
        return version, True
    kept = version_of(current) or FIRST_VERSION
    if without_version(current) == without_version(render_php(folder, kept)):
        return kept, False
    version = bump_version(kept)
    atomic_write(path, render_php(folder, version))
    return version, True


def atomic_write(path: Path, text: str) -> None:
    """Write through a temp file next to the target (container/docs/atomic-file-writes.md)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def check_include(folder: Folder, path: Path) -> tuple[bool, str]:
    """Compare the include with a fresh build; return (up to date, message)."""
    current = read_source(path)
    if current is None:
        return False, f"{path} is missing; run the tool with --write"
    fresh = render_php(folder, version_of(current) or FIRST_VERSION)
    if without_version(current) != without_version(fresh):
        return False, f"{path} is out of date; run the tool with --write"
    return True, f"{path} is up to date"


def find_src_root(path: Path) -> Path | None:
    """The docroot above a language folder, or None."""
    for parent in (path, *path.parents):
        if parent.name == "src":
            return parent
    return None


def render_subhubs(folder: Folder, depth: int = 0, show_all: bool = False) -> tuple[list[str], int]:
    """The subhub report lines of a folder and its subfolders, plus the count."""
    lines: list[str] = []
    found = 0
    pad = "  " * depth
    if depth > 0 and (folder.is_subhub or show_all):
        verdict = "SUBHUB" if folder.is_subhub else "skip  "
        note = "" if folder.is_subhub else f"  ({folder.skip_reason})"
        lines.append(f"{pad}{folder.name}/  {verdict}  articles {folder.article_count}, full {folder.full_count}{note}")
    elif depth == 0:
        lines.append(f"{pad}{folder.name}/")
    found += 1 if folder.is_subhub else 0
    for child in folder.folders:
        child_lines, child_found = render_subhubs(child, depth + 1, show_all)
        lines.extend(child_lines)
        found += child_found
    return lines, found


def print_line(message: str) -> None:
    """Print a line of the plain tree and the reports."""
    print(message)


def build_parser() -> argparse.ArgumentParser:
    """The command line of the tool."""
    parser = argparse.ArgumentParser(
        description="Build the article tree of a /dev/<language>/ hub from its folder structure.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("directory", help="language folder, e.g. container/src/dev/rust")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help=f"write {INCLUDE_NAME} into the folder")
    mode.add_argument("--check", action="store_true", help="exit 1 when the include file is out of date")
    mode.add_argument("--php", action="store_true", help="print the PHP include instead of the plain tree")
    mode.add_argument("--json", action="store_true", help="print the tree nodes as JSON")
    mode.add_argument("--subhubs", action="store_true", help="print the subhub report instead of the tree")
    parser.add_argument("--all", action="store_true", help="with --subhubs: show the folders that are not subhubs too")
    return parser


def resolve_directory(value: str) -> tuple[Path, Path] | None:
    """The language folder and the docroot above it, or None after an error."""
    directory = Path(value).resolve()
    if not directory.is_dir():
        print_line(f"build_tree: {directory} is not a folder")
        return None
    src = find_src_root(directory)
    if src is None:
        print_line(f"build_tree: {directory} is not inside a src docroot")
        return None
    return directory, src


def report_subhubs(folder: Folder, show_all: bool) -> int:
    """Print the subhub report."""
    lines, found = render_subhubs(folder, show_all=show_all)
    for line in lines:
        print_line(line)
    print_line(f"# {found} subhub(s)")
    return 0


def run_mode(args: argparse.Namespace, folder: Folder, include: Path) -> int:
    """Print or write what the chosen mode asks for."""
    if args.subhubs:
        return report_subhubs(folder, args.all)
    if args.check:
        up_to_date, message = check_include(folder, include)
        print_line(message)
        return 0 if up_to_date else 1
    if args.write:
        version, written = write_include(folder, include)
        state = "written" if written else "unchanged"
        print_line(f"{include}: version {version}, {state}, {count_leaves(nodes_of(folder))} articles")
        return 0
    if args.json:
        print_line(json.dumps({"root": folder.name + "/", "nodes": nodes_of(folder)}, ensure_ascii=False, indent=2))
        return 0
    print_line(render_php(folder, FIRST_VERSION) if args.php else render_plain(folder.name + "/", nodes_of(folder)))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the tool; see the module docstring for the modes."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.all and not args.subhubs:
        parser.error("--all needs --subhubs")
    located = resolve_directory(args.directory)
    if located is None:
        return 2
    directory, src = located
    return run_mode(args, scan_folder(directory, src, is_root=True), directory / INCLUDE_NAME)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
