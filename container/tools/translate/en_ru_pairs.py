#!/usr/bin/env python3
# version 0.2.1
"""Pair every English page with its Russian twin (and the other way round).

The pairing comments in the pages are the source of truth.  Every page under
`container/src` carries one line near the top, after `<?php` and any leading
comment block:

* an English page: ``# ru translation: ru/<path>`` or ``# ru translation not found``;
* a Russian page: ``# en translation: <path>`` or ``# en translation not found``.

A pair stated by the English comment is fixed before anything else: method
``comment``, score 100, no content or title matching can change it.  A page
whose comment says ``not found`` stays unpaired, and a strong candidate found
by the old methods is reported as a suggestion only.  Only pages with no
comment (a new page) go through the old methods, in this order:

* ``folder-map``  - the folder map plus the same remaining path;
* ``duplicate``   - a copy of the English page, mapped the same way;
* ``link``        - an absolute link from one page to the other language;
* ``content``     - code blocks, image file names and external URLs that both
                    pages share (language independent);
* ``title``       - the same title, or the same proper nouns in it (a topic
                    check for every candidate).

`ao/` (the Russian personal site on its own engine) and `zh/` (the Chinese
section) are not part of the English tree and are skipped.

One twin per page: the candidates are sorted by score and taken greedily, so
no page is claimed twice; the runners-up are reported.  The result is written
as a TSV with the columns ``en_path``, ``ru_path`` (empty when the page has no
twin), ``method`` and ``score``.

Modes:

* normal: pair the pages and write the TSV;
* ``--write-comments``: write or replace the pairing comment on both sides
  from the result (``--dry-run`` only prints what it would change).  `ao/`,
  `zh/`, `feed/` and every ``.php/`` engine folder are skipped;
* ``--check``: report a page with no comment or more than one, a pair that is
  not symmetric, a target that is missing or is not a page, and with
  ``--tsv FILE`` a table that disagrees with the comments.  Exit 1 on any
  problem, 0 when the tree is consistent.

Usage:
    python3 en_ru_pairs.py [--src container/src] [--out container/tools/translate/en_ru_pairs.tsv]
    python3 en_ru_pairs.py --write-comments [--dry-run] [--src container/src]
    python3 en_ru_pairs.py --check [--tsv FILE] [--src container/src]
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

FOLDER_DOC = "docs/en-ru-folders.md"
DUPLICATE_DOC = "docs/duplicate-pages.md"

ENGINE_CALL = re.compile(r"new\s+(?:\\?[A-Za-z_][A-Za-z0-9_\\]*\\)?(ArticlePage|BasePage|DictPage|Page)\s*\(")
TITLE_ARG = re.compile(r"""\btitle\s*:\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
PAGE_TITLE = re.compile(r"""\$page_title\s*=\s*(?:'([^']*)'|"([^"]*)")""")
HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
NAMESPACE = re.compile(r"^namespace\s+([^;]+);", re.MULTILINE)
HREF = re.compile(r"""href\s*=\s*["'](/[^"'#?]*)""")
LESSON_URL = re.compile(r"""(?:previous|next)_lesson_url\s*:\s*['"](/[^'"]*)['"]""")
IMAGE = re.compile(r"""<img\b[^<>]*\bsrc\s*=\s*["']([^"']+)""", re.IGNORECASE)
SOURCE = re.compile(r"""<source\b[^<>]*\bsrcset\s*=\s*["']([^"']+)""", re.IGNORECASE)
EXTERNAL = re.compile(r"https?://[^\s\"'<>)]+")
CODE_BLOCK = re.compile(r"<(?:code|pre)\b[^<>]*>(.*?)</(?:code|pre)\s*>", re.DOTALL | re.IGNORECASE)
PROSE_TAG = re.compile(r"<[^<>]*>")
WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9+#.]*")
STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "how",
    "what",
    "why",
    "in",
    "on",
    "of",
    "to",
    "a",
    "an",
    "установка",
    "настройка",
    "как",
    "что",
    "почему",
    "в",
    "на",
    "и",
    "с",
    "для",
    "по",
    "не",
}
FOLDER_ROW = re.compile(r"^\|\s*`(/[a-z0-9_/-]*/)`\s*\|\s*`(/ru/[a-z0-9_/-]*/)`", re.MULTILINE)
FOLDER_NAME_ROW = re.compile(r"`(/[a-z0-9_/-]*/)`\s+and\s+`(/ru/[a-z0-9_/-]*/)`")
DUPLICATE_ROW = re.compile(r"^\|((?:\s*`/[^`]+`)+)", re.MULTILINE)
DUPLICATE_PATH = re.compile(r"`(/[^`]+)`")
PAIR_LINE = re.compile(r"^\s*#\s+(?:ru|en)\s+translation\b")
PAIR_COMMENT = re.compile(r"^\s*#\s+(ru|en)\s+translation\s*:\s*(?P<target>.+?)\s*$")
PAIR_NOT_FOUND = re.compile(r"^\s*#\s+(ru|en)\s+translation\s+not\s+found\s*$")

# Scores: a path identity is worth more than a content match, which is worth
# more than a single shared link.  Pairs below MIN_SCORE are only reported.
SCORE_FOLDER = 100.0
SCORE_DUPLICATE = 60.0
SCORE_LINK = 50.0
SCORE_CONTENT = 45.0
SCORE_TITLE = 20.0
MIN_SCORE = 50.0
CONTENT_MIN = 0.30
COMMENT_SCORE = 100.0

# Settled by hand during the English-tree work: the folder of the real copy
# beats a content match, and a page with the same text but another OS or topic
# is not a twin.  Kept here so a rerun reproduces the same TSV.  A pairing
# comment always wins over these.
FORCED_PAIRS = {
    # a tree map pairs /code/python/db/sqlite3/ with /ru/code/python/sqlite3/, and
    # ru/code/python/postgres/example.php is a copy of that page.
    "dev/python/db/sqlite3/example.php": "ru/code/python/sqlite3/example.php",
}
FORCED_NONE = {
    # "hostname в CentOS" is not "hostname в Rocky"; the CentOS twin is
    # already linux/centos/network/hostname.php's.
    "linux/centos/users/hostname.php",
    # A copy of linux/network/; its twin ru/linux/network/ is taken.
    "linux/network.php",
    # Selenium is not the UI hub.
    "qa/selenium/basics/index.php",
}


@dataclass
class Page:
    rel: str  # path below src, e.g. linux/ubuntu/network/index.php
    url: str  # public path, e.g. /linux/ubuntu/network/
    title: str = ""
    links: set[str] = field(default_factory=set)
    fingerprint: set[str] = field(default_factory=set)
    pair_comments: list[tuple[str, str | None]] = field(default_factory=list)

    @property
    def language(self) -> str:
        return "ru" if self.rel.startswith("ru/") else "en"


@dataclass
class Pairing:
    en: Page
    ru: Page | None
    method: str
    score: float


Scored = tuple[float, str, Page, Page, list[str]]


def is_page(text: str) -> bool:
    """A file that builds one of the page engines (or the legacy one)."""
    return ENGINE_CALL.search(text) is not None


def page_url(rel: str) -> str:
    """Public path of a page file: ``x/index.php`` -> ``/x/``, ``x.php`` -> ``/x.php``."""
    if rel == "index.php":
        return "/"
    if rel.endswith("/index.php"):
        return "/" + rel[: -len("index.php")]
    return "/" + rel


def url_variants(url: str) -> list[str]:
    """The same page as a folder and as a file: /x/ and /x.php, /x/index.php."""
    variants = [url]
    if url.endswith("/"):
        variants += [url + "index.php", url.rstrip("/") + ".php"]
    elif url.endswith(".php"):
        variants += [url[: -len(".php")] + "/"]
    return variants


def lookup(url: str, table: dict[str, Page]) -> Page | None:
    for variant in url_variants(url):
        if variant in table:
            return table[variant]
    return None


def split_ending(line: str) -> tuple[str, str]:
    """A line and its own ending, so a rewrite keeps CRLF where the file has it."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def is_comment_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(("#", "//", "/*", "*")) or stripped.endswith("*/")


def read_pairing_comments(text: str) -> list[tuple[str, str | None]]:
    """The pairing comment(s) near the top, after `<?php` and the comment block.

    The target is the path after the colon, or None for ``not found``.
    """
    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if "<?php" in line:
            start = index + 1
            break
    comments: list[tuple[str, str | None]] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        not_found = PAIR_NOT_FOUND.match(line)
        if not_found:
            comments.append((not_found.group(1), None))
            continue
        found = PAIR_COMMENT.match(line)
        if found:
            comments.append((found.group(1), found.group("target").strip()))
            continue
        if is_comment_line(line):
            continue
        break
    return comments


def pairing_comment_indexes(lines: list[str]) -> list[int]:
    """Line indexes of the pairing comment(s) in the file header."""
    start = 0
    for index, raw in enumerate(lines):
        content, _ending = split_ending(raw)
        if "<?php" in content:
            start = index + 1
            break
    else:
        return []
    indexes: list[int] = []
    for index in range(start, len(lines)):
        content, _ending = split_ending(lines[index])
        stripped = content.strip()
        if not stripped:
            continue
        if PAIR_LINE.match(content):
            indexes.append(index)
            continue
        if is_comment_line(content):
            continue
        break
    return indexes


def strip_code_comments(line: str) -> str:
    line = re.sub(r"^\s*(?://|#)\s?", "", line)
    return line.strip()


def fingerprint(text: str) -> set[str]:
    """Language-independent content: code lines, image names, external URLs."""
    marks: set[str] = set()
    for block in CODE_BLOCK.findall(text):
        for line in html.unescape(PROSE_TAG.sub("", block)).splitlines():
            line = strip_code_comments(line)
            if len(line) >= 4 and WORD.search(line):
                marks.add("code:" + re.sub(r"\s+", " ", line))
    for pattern in (IMAGE, SOURCE):
        for value in pattern.findall(text):
            name = value.split("/")[-1].split("?")[0]
            if name:
                marks.add("img:" + name)
    for value in EXTERNAL.findall(text):
        marks.add("url:" + value.rstrip(".,;"))
    return marks


def title_of(text: str) -> str:
    for pattern in (TITLE_ARG, PAGE_TITLE, HTML_TITLE):
        match = pattern.search(text)
        if match:
            groups = [group for group in match.groups() if group]
            if groups:
                return html.unescape(PROSE_TAG.sub("", groups[0])).strip()
    return ""


def links_of(text: str) -> set[str]:
    links = {value.split("?")[0].split("#")[0] for value in HREF.findall(text)}
    links |= {value.split("#")[0] for value in LESSON_URL.findall(text)}
    return {link for link in links if link.startswith("/")}


def read_page(src_root: Path, path: Path) -> Page | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not is_page(text):
        return None
    rel = path.relative_to(src_root).as_posix()
    return Page(
        rel=rel,
        url=page_url(rel),
        title=title_of(text),
        links=links_of(text),
        fingerprint=fingerprint(text),
        pair_comments=read_pairing_comments(text),
    )


def discover(src_root: Path) -> tuple[list[Page], list[Page]]:
    english: list[Page] = []
    russian: list[Page] = []
    for path in sorted(src_root.rglob("*.php")):
        rel = path.relative_to(src_root).as_posix()
        if rel.startswith(("ao/", "zh/")):
            continue
        if "/.php/" in f"/{rel}":  # engine files: their doc comments quote new ArticlePage(
            continue
        page = read_page(src_root, path)
        if page is None:
            continue
        (russian if rel.startswith("ru/") else english).append(page)
    return english, russian


def load_folder_pairs(doc: Path) -> list[tuple[str, str]]:
    """English folder -> Russian folder, longest English prefix first."""
    if not doc.is_file():
        return []
    section = doc.read_text(encoding="utf-8").split("## English folder to Russian folder", 1)[-1].split("\n## ", 1)[0]
    pairs = list(FOLDER_ROW.findall(section))
    pairs += FOLDER_NAME_ROW.findall(section)
    return sorted(set(pairs), key=lambda pair: len(pair[0]), reverse=True)


def load_duplicate_groups(doc: Path) -> list[list[str]]:
    """Groups of paths that the duplicate report says hold the same article."""
    if not doc.is_file():
        return []
    groups = []
    for row in DUPLICATE_ROW.findall(doc.read_text(encoding="utf-8")):
        paths = DUPLICATE_PATH.findall(row)
        if len(paths) > 1:
            groups.append(paths)
    return groups


def english_twin(url: str, mirror: list[tuple[str, str]], known: set[str]) -> str | None:
    """The English page for a Russian URL, or None when it does not exist."""
    path, hash_mark, fragment = url.partition("#")
    if not path.startswith("/ru/"):
        return url
    for russian, english in mirror:
        if path == russian or path.startswith(russian):
            candidate = english + path[len(russian) :]
            break
    else:
        candidate = path[len("/ru") :]
    hit = next((variant for variant in url_variants(candidate) if variant in known), None)
    return hit + hash_mark + fragment if hit else None


def topic_tokens(title: str) -> set[str]:
    return {word.lower() for word in WORD.findall(title) if word.lower() not in STOP_WORDS and len(word) > 2}


def same_topic(english: Page, russian: Page) -> bool:
    """Titles name the same topic: same words, or the same proper nouns."""
    left, right = topic_tokens(english.title), topic_tokens(russian.title)
    if not left or not right:
        return True
    return bool(left & right)


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def duplicate_paths(page: Page, groups: list[list[str]]) -> set[str]:
    """Other paths of the same article in the same language."""
    copies = set()
    for group in groups:
        if page.url in group:
            copies |= set(group) - {page.url}
    return copies


def english_candidates(
    page: Page,
    ru_by_url: dict[str, Page],
    pairs: list[tuple[str, str]],
    groups: list[list[str]],
) -> dict[str, list[tuple[float, str]]]:
    """Candidate Russian URLs of an English page with (score, method) each."""
    found: dict[str, list[tuple[float, str]]] = {}

    def add(url: str, score: float, method: str) -> None:
        page_found = lookup(url, ru_by_url)
        if page_found is not None:
            found.setdefault(page_found.url, []).append((score, method))

    def map_path(path: str) -> str:
        for english, russian in pairs:
            if path == english or path.startswith(english):
                return russian + path[len(english) :]
        return "/ru" + path

    add(map_path(page.url), SCORE_FOLDER, "folder-map")
    for copy in duplicate_paths(page, groups):
        add(map_path(copy), SCORE_DUPLICATE, "duplicate")
    for link in page.links:
        if link.startswith("/ru/"):
            add(link, SCORE_LINK, "link")
    return found


def russian_candidates(
    page: Page,
    en_by_url: dict[str, Page],
    pairs: list[tuple[str, str]],
    groups: list[list[str]],
) -> dict[str, list[tuple[float, str]]]:
    mirror = [(russian, english) for english, russian in pairs]
    mirror.sort(key=lambda pair: len(pair[0]), reverse=True)
    known = set(en_by_url)
    found: dict[str, list[tuple[float, str]]] = {}

    def add(url: str | None, score: float, method: str) -> None:
        page_found = lookup(url, en_by_url) if url else None
        if page_found is not None:
            found.setdefault(page_found.url, []).append((score, method))

    add(english_twin(page.url, mirror, known), SCORE_FOLDER, "folder-map")
    for copy in duplicate_paths(page, groups):
        add(english_twin(copy, mirror, known), SCORE_DUPLICATE, "duplicate")
    for link in page.links:
        if not link.startswith("/ru/"):
            add(link, SCORE_LINK, "link")
    return found


def score_candidate(
    english: Page,
    russian: Page,
    claims: list[tuple[float, str]],
) -> tuple[float, list[str]]:
    score = sum(value for value, _method in claims)
    methods = [method for _value, method in claims]
    similarity = jaccard(english.fingerprint, russian.fingerprint)
    if similarity >= CONTENT_MIN:
        score += SCORE_CONTENT * similarity
        methods.append("content")
    if same_topic(english, russian):
        score += SCORE_TITLE
        methods.append("title")
    return score, methods


def content_candidates(
    english: list[Page],
    russian: list[Page],
) -> dict[str, set[str]]:
    """English URL -> Russian URLs that share code, images or external URLs."""
    index: dict[str, set[str]] = {}
    for page in russian:
        for mark in page.fingerprint:
            index.setdefault(mark, set()).add(page.url)
    found: dict[str, set[str]] = {}
    for page in english:
        counts: dict[str, int] = {}
        for mark in page.fingerprint:
            for url in index.get(mark, ()):
                counts[url] = counts.get(url, 0) + 1
        shared = {url for url, count in counts.items() if count >= 3}
        if shared:
            found[page.url] = shared
    return found


def comment_conflicts(english: Page, russian: Page) -> list[str]:
    """Report when the Russian side of an English-stated pair disagrees."""
    conflicts: list[str] = []
    comments = russian.pair_comments
    if not comments:
        return conflicts
    if len(comments) > 1:
        conflicts.append(f"{russian.rel}: more than one pairing comment")
    _direction, target = comments[0]
    if target is None:
        conflicts.append(f"conflict: {english.rel} names {russian.rel}, but {russian.rel} says not found")
    elif target != english.rel:
        conflicts.append(f"conflict: {english.rel} names {russian.rel}, but {russian.rel} names {target}")
    return conflicts


def scored_candidates(
    english: list[Page],
    russian: list[Page],
    pairs: list[tuple[str, str]],
    groups: list[list[str]],
) -> list[Scored]:
    """Every English page's candidate Russian pages, best score first."""
    ru_by_url = {page.url: page for page in russian}
    content = content_candidates(english, russian)
    scored: list[Scored] = []
    for page in english:
        claims = english_candidates(page, ru_by_url, pairs, groups)
        for url in content.get(page.url, ()):
            claims.setdefault(url, []).append((0.0, "content-index"))
        for url, values in claims.items():
            candidate = ru_by_url[url]
            score, methods = score_candidate(page, candidate, values)
            scored.append((score, page.url, page, candidate, methods))
    scored.sort(key=lambda row: (-row[0], row[1], row[3].url))
    return scored


def add_comment_pairs(
    english: list[Page],
    ru_by_rel: dict[str, Page],
    matched: dict[str, Pairing],
    taken_ru: dict[str, str],
    notes: list[str],
) -> None:
    """The pairing comments are the truth: a valid English target is a fixed pair."""
    for page in english:
        if not page.pair_comments:
            continue
        if len(page.pair_comments) > 1:
            notes.append(f"{page.rel}: more than one pairing comment, using the first")
        _direction, target = page.pair_comments[0]
        if target is None:
            continue
        target_page = ru_by_rel.get(target)
        if target_page is None:
            notes.append(f"{page.rel}: pairing comment names {target}, which is missing or not a page")
            continue
        owner = taken_ru.get(target_page.url)
        if owner is not None:
            notes.append(f"{page.rel}: pairing comment names {target_page.rel}, already paired with {owner}")
            continue
        matched[page.url] = Pairing(en=page, ru=target_page, method="comment", score=COMMENT_SCORE)
        taken_ru[target_page.url] = page.url
        notes.extend(comment_conflicts(page, target_page))


def add_forced_pairs(
    en_by_url: dict[str, Page],
    ru_by_url: dict[str, Page],
    matched: dict[str, Pairing],
    taken_ru: dict[str, str],
    notes: list[str],
) -> None:
    """Hand-settled pairs come before matching, but never before a comment."""
    for url, ru_url in FORCED_PAIRS.items():
        forced_en, forced_ru = en_by_url.get(page_url(url)), ru_by_url.get(page_url(ru_url))
        if forced_en is None or forced_ru is None:
            notes.append(f"forced pair {url} -> {ru_url} is missing from the tree")
            continue
        if forced_en.pair_comments:
            if forced_en.url not in matched:
                notes.append(f"{url}: comment wins over the forced pair {ru_url}")
            continue
        owner = taken_ru.get(forced_ru.url)
        if owner is not None:
            notes.append(f"{url}: cannot pair it with {ru_url}, already paired with {owner}")
            continue
        matched[forced_en.url] = Pairing(en=forced_en, ru=forced_ru, method="manual", score=SCORE_FOLDER + SCORE_TITLE)
        taken_ru[forced_ru.url] = forced_en.url


def match_new_pages(
    scored: list[Scored],
    matched: dict[str, Pairing],
    taken_ru: dict[str, str],
    runners_up: dict[str, list[str]],
) -> None:
    """New pages (no comment) go through the old matching; commented pages never do."""
    for score, url, page, candidate, methods in scored:
        if page.pair_comments:
            continue
        if url in matched:
            continue
        if score < MIN_SCORE:
            runners_up.setdefault(url, []).append(f"{candidate.url} (low score {score:.0f})")
            continue
        if candidate.url in taken_ru:
            runners_up.setdefault(url, []).append(f"{candidate.url} ({'+'.join(methods)}, {score:.0f}) is already paired with {taken_ru[candidate.url]}")
            continue
        matched[url] = Pairing(en=page, ru=candidate, method="+".join(dict.fromkeys(methods)), score=score)
        taken_ru[candidate.url] = url


def drop_forced_none(matched: dict[str, Pairing], taken_ru: dict[str, str], notes: list[str]) -> None:
    """Remove the settled non-pairs unless a comment already claimed the page."""
    for url in FORCED_NONE:
        pairing = matched.get(page_url(url))
        if pairing is None or pairing.ru is None or pairing.method == "comment":
            continue
        del matched[pairing.en.url]
        taken_ru.pop(pairing.ru.url, None)
        notes.append(f"{url}: dropped {pairing.ru.url} (other topic or a copy)")


def suggest_not_found(english: list[Page], scored: list[Scored], taken_ru: dict[str, str], notes: list[str]) -> None:
    """A `not found` page stays unpaired; show the strong candidate it would have had."""
    for page in english:
        if not page.pair_comments:
            continue
        _direction, target = page.pair_comments[0]
        if target is not None:
            continue
        for score, url, _scored_page, candidate, methods in scored:
            if url != page.url or score < MIN_SCORE:
                continue
            method_text = "+".join(dict.fromkeys(methods))
            owner = taken_ru.get(candidate.url)
            suffix = f", already paired with {owner}" if owner else ""
            notes.append(f"{page.rel}: suggestion {candidate.url} ({method_text}, {score:.0f}) - comment says not found{suffix}")


def result_from_matches(
    english: list[Page],
    matched: dict[str, Pairing],
    runners_up: dict[str, list[str]],
    notes: list[str],
) -> list[Pairing]:
    """One pairing per English page, with the runners-up added to the notes."""
    result = []
    for page in english:
        pairing = matched.get(page.url)
        if pairing is None:
            pairing = Pairing(en=page, ru=None, method="", score=0.0)
        result.append(pairing)
        for runner in runners_up.get(page.url, []):
            notes.append(f"{page.rel}: also matches {runner}")
    result.sort(key=lambda pairing: pairing.en.rel)
    return result


def pair_pages(
    english: list[Page],
    russian: list[Page],
    pairs: list[tuple[str, str]],
    groups: list[list[str]],
) -> tuple[list[Pairing], list[str]]:
    """Comments first, then forced pairs, then the best candidate for new pages."""
    ru_by_url = {page.url: page for page in russian}
    en_by_url = {page.url: page for page in english}
    ru_by_rel = {page.rel: page for page in russian}
    scored = scored_candidates(english, russian, pairs, groups)

    matched: dict[str, Pairing] = {}
    taken_ru: dict[str, str] = {}
    runners_up: dict[str, list[str]] = {}
    notes: list[str] = []

    add_comment_pairs(english, ru_by_rel, matched, taken_ru, notes)
    add_forced_pairs(en_by_url, ru_by_url, matched, taken_ru, notes)
    match_new_pages(scored, matched, taken_ru, runners_up)
    drop_forced_none(matched, taken_ru, notes)
    suggest_not_found(english, scored, taken_ru, notes)
    result = result_from_matches(english, matched, runners_up, notes)
    return result, notes


def russian_without_twin(result: list[Pairing], russian: list[Page]) -> list[Page]:
    paired = {pairing.ru.url for pairing in result if pairing.ru is not None}
    return [page for page in russian if page.url not in paired]


def write_tsv(result: list[Pairing], russian: list[Page], out: Path) -> None:
    """en_path, ru_path (empty when none), method, score."""
    lines = []
    for pairing in result:
        ru_path = pairing.ru.rel if pairing.ru is not None else ""
        lines.append("\t".join([pairing.en.rel, ru_path, pairing.method, f"{pairing.score:.0f}"]))
    for page in russian_without_twin(result, russian):
        lines.append(f"\t{page.rel}\tnone\t0")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_name(f".{out.name}.tmp")
    staging.write_text("\n".join(lines) + "\n", encoding="utf-8")
    staging.replace(out)


def comment_skip(rel: str) -> bool:
    """Folders that do not take part in pairing comments."""
    return rel.startswith(("ao/", "zh/", "feed/")) or "/.php/" in f"/{rel}"


def comment_line(rel: str, target: str | None) -> str:
    direction = "en" if rel.startswith("ru/") else "ru"
    if target is None:
        return f"# {direction} translation not found"
    return f"# {direction} translation: {target}"


def rewrite_comment(text: str, rel: str, target: str | None) -> str:
    """Insert or replace the pairing comment, changing no other byte."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return text
    php_index: int | None = None
    newline = "\n"
    for raw in lines:
        _content, ending = split_ending(raw)
        if ending:
            newline = ending
            break
    for index, raw in enumerate(lines):
        content, _ending = split_ending(raw)
        if "<?php" in content:
            php_index = index
            break
    if php_index is None:
        return text
    wanted = comment_line(rel, target)
    indexes = pairing_comment_indexes(lines)
    if indexes:
        first = indexes[0]
        _content, ending = split_ending(lines[first])
        lines[first] = wanted + ending
        for index in reversed(indexes[1:]):
            del lines[index]
        return "".join(lines)

    # No comment yet: find the end of `<?php`, its blank line and any leading
    # comment block, then put the comment before the first line of code.
    index = php_index + 1
    last_comment: int | None = None
    while index < len(lines):
        content, _ending = split_ending(lines[index])
        if not content.strip():
            index += 1
            continue
        if is_comment_line(content):
            last_comment = index
            index += 1
            continue
        break
    need_blank = index == php_index + 1 if last_comment is None else index == last_comment + 1
    insertion = ([newline] if need_blank else []) + [wanted + newline, newline]
    lines[index:index] = insertion
    return "".join(lines)


def write_comments(src_root: Path, targets: dict[str, str | None], dry_run: bool) -> list[str]:
    """Write the comment for every target path; return the changed file paths."""
    changed: list[str] = []
    for rel in sorted(targets):
        if comment_skip(rel):
            continue
        path = src_root / rel
        try:
            text = path.read_bytes().decode("utf-8")
        except OSError, UnicodeDecodeError:
            continue
        new_text = rewrite_comment(text, rel, targets[rel])
        if new_text == text:
            continue
        changed.append(rel)
        if dry_run:
            continue
        staging = path.with_name(f".{path.name}.tmp")
        staging.write_bytes(new_text.encode("utf-8"))
        staging.replace(path)
    return changed


def comment_targets(result: list[Pairing], english: list[Page], russian: list[Page]) -> dict[str, str | None]:
    """Every page -> the other side of its pair, or None for `not found`."""
    targets: dict[str, str | None] = {page.rel: None for page in russian}
    for page in english:
        targets[page.rel] = None
    for pairing in result:
        if pairing.ru is not None:
            targets[pairing.en.rel] = pairing.ru.rel
            targets[pairing.ru.rel] = pairing.en.rel
    return targets


def check_comments(english: list[Page], russian: list[Page], tsv: Path | None) -> list[str]:
    """Every problem in the pairing comments, in a stable order."""
    pages = [page for page in english + russian if not comment_skip(page.rel)]
    by_rel = {page.rel: page for page in pages}
    problems: list[str] = []
    for page in pages:
        comments = page.pair_comments
        if not comments:
            problems.append(f"{page.rel}: no pairing comment")
            continue
        if len(comments) > 1:
            problems.append(f"{page.rel}: more than one pairing comment")
            continue
        _direction, target = comments[0]
        if target is None:
            continue
        other = by_rel.get(target)
        if other is None:
            problems.append(f"{page.rel}: pairing comment names {target}, which is missing or not a page")
            continue
        other_comments = other.pair_comments
        if not other_comments:
            problems.append(f"{page.rel}: pair with {target} is not symmetric: {target} has no pairing comment")
        elif len(other_comments) > 1:
            problems.append(f"{page.rel}: pair with {target} is not symmetric: {target} has more than one pairing comment")
        else:
            _other_direction, other_target = other_comments[0]
            if other_target != page.rel:
                shown = "not found" if other_target is None else other_target
                problems.append(f"{page.rel}: pair with {target} is not symmetric: {target} says {shown}")
    if tsv is not None:
        problems.extend(tsv_problems(tsv, english, by_rel))
    return problems


def tsv_problems(tsv: Path, english: list[Page], by_rel: dict[str, Page]) -> list[str]:
    """Pairs in the table that the comments do not state, and the other way round."""
    if not tsv.is_file():
        return [f"table {tsv} is missing"]
    table_pairs = set()
    for line in tsv.read_text(encoding="utf-8").splitlines():
        row = line.split("\t")
        if len(row) >= 2 and row[0] and row[1]:
            table_pairs.add((row[0], row[1]))
    comment_pairs = set()
    for page in english:
        if comment_skip(page.rel) or len(page.pair_comments) != 1:
            continue
        _direction, target = page.pair_comments[0]
        if target is not None and target in by_rel:
            comment_pairs.add((page.rel, target))
    problems = []
    for en_path, ru_path in sorted(table_pairs - comment_pairs):
        problems.append(f"table pairs {en_path} with {ru_path}, the comments do not")
    for en_path, ru_path in sorted(comment_pairs - table_pairs):
        problems.append(f"comments pair {en_path} with {ru_path}, the table does not")
    return problems


def find_src_root(start: Path) -> Path | None:
    """The nearest src/ that has a sibling docs/en-ru-folders.md, or the cwd's."""
    candidates = [start.resolve(), *start.resolve().parents, Path.cwd().resolve()]
    for parent in candidates:
        for src in (parent / "src", parent):
            if (src / "sitemap.xml").is_file() and (src.parent / FOLDER_DOC).is_file():
                return src
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="", help="container/src (found automatically by default)")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "en_ru_pairs.tsv"))
    parser.add_argument("--write-comments", action="store_true", help="write or replace the pairing comment on both sides from the pairing result")
    parser.add_argument("--dry-run", action="store_true", help="with --write-comments: only print the files that would change")
    parser.add_argument("--check", action="store_true", help="check the comments (and with --tsv FILE the table); exit 1 on a problem")
    parser.add_argument("--tsv", default="", help="with --check: a table to compare with the comments")
    args = parser.parse_args(argv)
    if args.dry_run and not args.write_comments:
        parser.error("--dry-run needs --write-comments")
    if args.tsv and not args.check:
        parser.error("--tsv needs --check")
    if args.check and args.write_comments:
        parser.error("--check and --write-comments are different modes")
    src_root = Path(args.src) if args.src else find_src_root(Path(__file__))
    if src_root is None or not src_root.is_dir():
        raise SystemExit("cannot find container/src; pass --src")
    docs = src_root.parent / "docs"
    english, russian = discover(src_root)

    if args.check:
        problems = check_comments(english, russian, Path(args.tsv) if args.tsv else None)
        if problems:
            for problem in problems:
                print("problem:", problem)
            print(f"{len(problems)} problem(s)")
            return 1
        print(f"pairing comments are consistent: {len(english)} English pages, {len(russian)} Russian pages")
        return 0

    pairs = load_folder_pairs(docs / "en-ru-folders.md")
    groups = load_duplicate_groups(docs / "duplicate-pages.md")
    result, notes = pair_pages(english, russian, pairs, groups)

    if args.write_comments:
        targets = comment_targets(result, english, russian)
        changed = write_comments(src_root, targets, args.dry_run)
        for rel in changed:
            print(f"{'would update' if args.dry_run else 'updated'} {rel}")
        print(f"{len(changed)} file(s) {'would change' if args.dry_run else 'changed'}")
        return 0

    out = Path(args.out)
    write_tsv(result, russian, out)
    paired = sum(1 for pairing in result if pairing.ru is not None)
    unpaired_ru = russian_without_twin(result, russian)
    print(f"{len(english)} English pages, {len(russian)} Russian pages")
    print(f"paired {paired}, English without a twin {len(english) - paired}, Russian without a twin {len(unpaired_ru)}")
    print(f"pairs written to {out}")
    for note in notes[:40]:
        print(" note:", note)
    if len(notes) > 40:
        print(f" ... {len(notes) - 40} more note(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
