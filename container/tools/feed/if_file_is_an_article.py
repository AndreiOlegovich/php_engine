"""Decide whether a file under container/src is a real article page.

An article is a renderable page: a .php file outside the excluded paths
(engine .php/ tree, dot-/underscore-led dirs, *img* dirs, pc/, demo/,
arj*, editor *copy* files) whose source builds a page object
(new ArticlePage/Page/BasePage/DictPage), uses the legacy
header includes (010_header.php, 020_header*.php, _head_inc.php,
080_counter, 090_bottom) or the _template.php generator.

Usage as a script (exit 0 only when EVERY path is an article):

    python3 if_file_is_an_article.py container/src/i/nginx/index.php
    python3 if_file_is_an_article.py container/src/*/*.php

Usage as a module:

    from if_file_is_an_article import is_article, page_url, page_title
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC_DIR = os.environ.get("IF_ARTICLE_SRC_DIR", os.path.join(REPO_ROOT, "container", "src"))

PAGE_CLASSES = os.environ.get("SITE_PAGE_CLASSES", "ArticlePage Page BasePage").split()
PAGE_CTOR_RE = re.compile(r"new\s+(" + "|".join(re.escape(c) for c in PAGE_CLASSES) + r")\s*\(")
TEMPLATE_RE = re.compile(r"require_once\s*\(\s*\$root\s*\.\s*'[^']*_template\.php'\s*\)")
HEADER_INCLUDES = os.environ.get("SITE_HEADER_INCLUDES", "010_header.php _head_inc.php").split()
LEGACY_HEAD_RE = re.compile("|".join(re.escape(name) for name in HEADER_INCLUDES) + r"|_footer\.php|header-up\.php")
TITLE_RE = re.compile(r"title:\s*'([^']+)'")
TITLE_DQ_RE = re.compile(r'title:\s*"([^"]+)"')
PAGE_TITLE_RE = re.compile(r"\$page_title\s*=\s*'([^']+)'")
PAGE_TITLE_DQ_RE = re.compile(r'\$page_title\s*=\s*"([^"]+)"')
HTML_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
REDIRECT_RES = (
    re.compile(r"header\s*\(\s*['\"]Location:\s*(/[^'\"]+)", re.I),
    re.compile(r"http-equiv\s*=\s*['\"]refresh['\"][^>]*?url\s*=\s*(/[^\"'\s>]+)", re.I),
    re.compile(
        r"(?:window\.)?location(?:\.href|\.assign|\.replace)?\s*[=(]\s*['\"](/[^'\"]+)",
        re.I,
    ),
)

EXCLUDED_TOP_DIRS = ("pc", "demo")
EXCLUDED_BASENAMES = ("RelatedArticles.php",)


def _rel_or_none(path: str) -> str | None:
    """Src-relative path, or None when outside container/src."""
    try:
        rel = os.path.relpath(os.path.abspath(path), SRC_DIR)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return rel


def _is_excluded_part(part: str) -> bool:
    """True for engine, asset, scratch and archive dir names."""
    if part == ".php" or part.startswith((".", "_")):
        return True
    return _is_asset_or_scratch(part)


def _is_asset_or_scratch(part: str) -> bool:
    """True for image, archive and editor-duplicate dir names."""
    if part == "img" or part.endswith("img"):
        return True
    if part.startswith("arj"):
        return True
    return " copy" in part


def _is_excluded_dir(rel: str) -> bool:
    """True for engine, asset and scratch paths (never articles)."""
    parts = rel.split(os.sep)
    if parts[0] in EXCLUDED_TOP_DIRS:
        return True
    return any(_is_excluded_part(part) for part in parts[:-1])


def _is_excluded_file(rel: str) -> bool:
    """True for includes, section files and editor duplicates."""
    base = os.path.basename(rel)
    if base in EXCLUDED_BASENAMES:
        return True
    if not base.endswith(".php"):
        return True
    if base.startswith((".", "_")):
        return True
    return bool(base.endswith("_inc.php") or " copy" in base or base.startswith("copy("))


def _renders_page(source: str) -> bool:
    """True when the source builds a page object or legacy header."""
    return bool(PAGE_CTOR_RE.search(source) or TEMPLATE_RE.search(source) or LEGACY_HEAD_RE.search(source))


def _read_source(path: str) -> str | None:
    """File text, retrying legacy single-byte encodings on U+FFFD damage."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
    except OSError:
        return None
    if "\ufffd" not in source:
        return source
    for encoding in ("cp1251", "koi8-r", "iso-8859-5"):
        try:
            with open(path, encoding=encoding) as handle:
                return handle.read()
        except (OSError, ValueError):
            continue
    return source


def is_article(path: str) -> bool:
    """True when path is a renderable article page under container/src."""
    rel = _rel_or_none(path)
    if rel is None:
        return False
    if _is_excluded_dir(rel) or _is_excluded_file(rel):
        return False
    if not os.path.isfile(os.path.join(SRC_DIR, rel)):
        return False
    source = _read_source(os.path.join(SRC_DIR, rel))
    if source is None:
        return False
    return _renders_page(source)


def page_url(abs_or_rel_path: str) -> str:
    """URL for a src file: index.php -> dir URL, anything else keeps .php."""
    if os.path.isabs(abs_or_rel_path):
        rel = os.path.relpath(abs_or_rel_path, SRC_DIR)
    else:
        rel = abs_or_rel_path
        if rel.startswith("container/src/"):
            rel = rel[len("container/src/") :]
    rel = rel.replace(os.sep, "/")
    if rel.endswith("/index.php"):
        return "/" + rel[: -len("index.php")]
    return "/" + rel


def url_to_file(url: str) -> str:
    """Src file backing a URL (dir URL -> index.php under it)."""
    if url.endswith("/"):
        return os.path.join(SRC_DIR, url[1:] + "index.php")
    return os.path.join(SRC_DIR, url[1:])


def page_title(path: str) -> str | None:
    """First title: ctor arg, legacy $page_title, or <title> tag."""
    source = _read_source(path)
    if source is None:
        return None
    match = TITLE_RE.search(source) or TITLE_DQ_RE.search(source) or PAGE_TITLE_RE.search(source) or PAGE_TITLE_DQ_RE.search(source) or HTML_TITLE_RE.search(source)
    if match is None:
        return None
    text = re.sub(r"<[^>]+>", "", match.group(1))
    return text.strip() or None


def redirect_target(path: str) -> str | None:
    """Internal redirect target of a stub file, or None.

    Matches PHP header(Location:), meta refresh and JS location
    redirects pointing at absolute site paths (/...). Query strings
    are stripped.
    """
    source = _read_source(path)
    if source is None:
        return None
    for pattern in REDIRECT_RES:
        match = pattern.search(source)
        if match is not None:
            return match.group(1).split("?")[0] or None
    return None


def main(argv: list[str]) -> int:
    """Print ARTICLE/NOTARTICLE per path; 0 only when all are articles."""
    paths = argv[1:]
    if not paths:
        print("usage: if_file_is_an_article.py PATH [PATH ...]")
        return 2
    failed = False
    for path in paths:
        verdict = "ARTICLE" if is_article(path) else "NOTARTICLE"
        print(f"{verdict} {path}")
        failed = failed or verdict == "NOTARTICLE"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
