"""Find the article in container/src that a mention should link to.

Term mode ranks the articles for each term:

    python3 container/tools/search/find_article.py curl "docker compose"
    python3 container/tools/search/find_article.py --page container/src/ai/x/index.php git

Scan mode reads the prose of one page (code blocks left out), finds every
topic that has an article in the page's language, and prints the line of
the first mention, the link it should get and whether the page already has
it:

    python3 container/tools/search/find_article.py --scan container/src/ai/x/index.php

A mention inside a heading is not the first mention: such a link never
counts as LINKED and is reported on its own line as IN-HEADING, while the
first mention in the prose after the heading is judged as usual. A heading
may span several lines.

Lang mode lists the pages whose lang: argument does not match their
folder (a missing argument means the engine default, 'ru'); exit 1 when
there are any:

    python3 container/tools/search/find_article.py --check-lang

The page data comes from site_index.py. A page never links itself and
links only pages of its own language. Ranking prefers the page that is
about the term (its last URL folder names it), then hub pages (fewer
folders), then the Linux page over Unix over Windows (--os changes the
order). A strong match is a page whose URL folder names the term, or a
folder page whose title head does; a weak one only mentions it, so it is
a link only when that page fits the context, and scan mode never suggests
it. Synonyms live in aliases.json, words that are never topics in
stopwords.txt.
"""

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from site_index import (  # pylint: disable=wrong-import-position  # noqa: E402
    DEFAULT_SRC,
    Page,
    build_index,
    page_url,
    path_lang,
    read_source,
    tokens,
)

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
ALIASES_FILE = os.path.join(TOOL_DIR, "aliases.json")
STOPWORDS_FILE = os.path.join(TOOL_DIR, "stopwords.txt")

OS_FOLDERS = ("linux", "unix", "windows")
DEPTH_PENALTY = 10
MAX_TOPIC_TOKENS = 4
HREF_RE = re.compile(r"href=[\"']([^\"'#?]+)")
ANCHOR_RE = re.compile(r"<a\b[^>]*?href=[\"']([^\"'#?]+)[\"'][^>]*>(.*?)</a>", re.S)
TAG_STRIP_RE = re.compile(r"<[^>]*>")
WORD_RE = re.compile(r"[^\W_]+")
LETTER_DIGIT_RE = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")
CODE_CLASS = r"(?:cmd\d*|cout\d*|cmdout\d*|[a-z]*code|shellscript|path|yamlfile)"
CODE_BLOCK_RE = re.compile(
    r"<(p|div|pre)\b[^>]*class=\"[^\"]*\b" + CODE_CLASS + r"\b[^\"]*\"[^>]*>.*?</\1>|<pre\b.*?</pre>|<code\b.*?</code>",
    re.S,
)
HEADING_RE = re.compile(r"<h[1-6]\b.*?</h[1-6]>", re.S)
INLINE_CODE_RE = re.compile(r"<span class=\"tt\">.*?</span>", re.S)
TAG_RE = re.compile(r"<[^>]*>|&#?\w+;")
PHP_BLOCK_RE = re.compile(r"<\?php.*?(?:\?>|\Z)", re.S)
# Not part of a path, a file name or a longer word: ~/.ssh, /dev/null,
# users.db and opencode.jsonc are not mentions of ssh, dev, db or OpenCode.
BEFORE = r"(?<![\w\-/.~])"
AFTER = r"(?![\w\-/]|\.\w|:/)"
SYMBOL_RE = re.compile(r"[^\w\s.\-]")


@dataclass
class Match:
    """A ranked page; strong when its URL or title head names the term."""

    value: int
    strong: bool
    page: Page


@dataclass
class Mention:
    """First mention of a topic on the scanned page."""

    text: str
    line: int
    inline_code: bool
    snippet: str


@dataclass
class Suggestion:
    """A topic the scanned page mentions and the page it should link to."""

    mention: Mention
    page: Page
    linked: bool
    in_heading: bool = False


def load_aliases() -> dict[str, list[str]]:
    """Term -> other names for the same thing, both directions."""
    try:
        with open(ALIASES_FILE, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    aliases: dict[str, list[str]] = {}
    for name, others in raw.items():
        group = [name, *others]
        for item in group:
            aliases.setdefault(" ".join(tokens(item)), []).extend(g for g in group if g != item)
    return aliases


def load_stopwords() -> set[str]:
    """Words that are never a topic, as token strings."""
    try:
        with open(STOPWORDS_FILE, encoding="utf-8") as handle:
            return {" ".join(tokens(line)) for line in handle if line.strip() and not line.startswith("#")}
    except OSError:
        return set()


def os_bonus(page: Page, prefer: str) -> int:
    """Bonus for the preferred operating system folder."""
    order = [prefer, *[name for name in OS_FOLDERS if name != prefer]]
    first = page.folders[0] if page.folders else []
    for place, name in enumerate(order):
        if first == tokens(name):
            return 6 - 2 * place
    return 0


def url_score(term: list[str], page: Page) -> int:
    """How strongly the URL folders name the term."""
    if not page.folders:
        return 0
    last = page.folders[-1]
    every = {tok for folder in page.folders for tok in folder}
    if sorted(last) == sorted(term):
        return 100
    if set(term) <= every:
        return 90 if set(last) <= set(term) else 50
    return 0


def title_score(term: list[str], page: Page) -> int:
    """How strongly the title names the term."""
    phrase = " ".join(term)
    if phrase in (" ".join(page.title_tokens), " ".join(page.head_tokens)):
        return 50
    if f" {phrase} " in f" {' '.join(page.title_tokens)} ":
        return 30
    if set(term) <= set(page.title_tokens):
        return 15
    return 0


def score(term: list[str], page: Page, prefer: str) -> Match | None:
    """Ranked page, or None when it is not about the term."""
    by_url = url_score(term, page)
    by_title = title_score(term, page)
    depth = DEPTH_PENALTY * len(page.folders)
    if by_url + by_title == 0:
        if term and set(term) <= set(page.keyword_tokens):
            return Match(5 - depth, False, page)
        return None
    strong = by_url >= 90 or (by_title == 50 and page.url.endswith("/"))
    return Match(by_url + by_title - depth + os_bonus(page, prefer), strong, page)


def rank(term: str, pages: list[Page], aliases: dict[str, list[str]], prefer: str) -> list[Match]:
    """Pages for a term and its aliases, strong ones first, best first."""
    best: dict[str, Match] = {}
    for name in [term, *aliases.get(" ".join(tokens(term)), [])]:
        term_tokens = tokens(name)
        for page in pages:
            found = score(term_tokens, page, prefer)
            if found is not None and (page.url not in best or found.value > best[page.url].value):
                best[page.url] = found
    return sorted(best.values(), key=lambda item: (not item.strong, -item.value, item.page.url))


def blank(match: re.Match[str]) -> str:
    """Spaces of the same length, newlines kept, so offsets stay valid."""
    return re.sub(r"[^\n]", " ", match.group(0))


def blank_text(text: str) -> str:
    """Spaces for every character but newlines."""
    return re.sub(r"[^\n]", " ", text)


def prose_mask(source: str) -> str:
    """Source with everything but body prose blanked; inline code stays."""
    start = source.find("createHead()")
    end = source.find("createFooter()")
    if start != -1 and end > start:
        cut = start + len("createHead()")
        masked = blank_text(source[:cut]) + source[cut:end] + blank_text(source[end:])
    else:
        masked = PHP_BLOCK_RE.sub(blank, source)
    masked = CODE_BLOCK_RE.sub(blank, masked)
    return HEADING_RE.sub(blank, masked)


def topic_names(page: Page, stopwords: set[str]) -> list[str]:
    """Names a folder page is the topic for: its folder as written (hubs only) and title head.

    A two-letter name (Go, Vi) only comes from a title head and keeps its
    case; one letter (C, R) is never a topic: "option C" is not the language.
    """
    if not page.url.endswith("/"):
        return []
    names = []
    folder = re.sub(r"[_\-]+", " ", page.url.strip("/").rsplit("/", 1)[-1])
    if page.folders and len(page.folders) <= 2 and len(folder) >= 3:
        names.append(folder)
    if page.head and len(page.head_tokens) <= MAX_TOPIC_TOKENS and len(page.head) > 1:
        names.append(page.head)
    return [name for name in dict.fromkeys(names) if " ".join(tokens(name)) not in stopwords]


def topic_regex(name: str) -> re.Pattern[str]:
    """Whole-word pattern outside paths and URL schemes.

    A name with symbols or a leading dot (C++, C#, any(), .NET) must appear
    as written; other names also match with - _ . or no space between
    their words.
    """
    flags = 0 if len(name.replace(" ", "")) <= 2 else re.I
    if SYMBOL_RE.search(name) or name.startswith("."):
        return re.compile(BEFORE + re.escape(name) + AFTER, flags)
    words = [re.escape(word) for word in WORD_RE.findall(LETTER_DIGIT_RE.sub(" ", name))]
    return re.compile(BEFORE + r"[\s_.\-]*".join(words) + AFTER, flags)


def first_mention(name: str, text: str, mask: str, source: str) -> Mention | None:
    """First prose mention of name, with its line and inline-code flag."""
    match = topic_regex(name).search(text)
    if match is None:
        return None
    start = match.start()
    inline = any(span.start() <= start < span.end() for span in INLINE_CODE_RE.finditer(mask))
    snippet = " ".join(text[max(0, start - 40) : match.end() + 40].split())
    return Mention(text=match.group(0), line=source.count("\n", 0, start) + 1, inline_code=inline, snippet=snippet)


def candidates(pages: list[Page], lang: str, self_url: str) -> list[Page]:
    """Pages a page in lang may link: same language, never itself."""
    return [page for page in pages if page.lang == lang and page.url != self_url]


def heading_spans(source: str) -> list[tuple[int, int]]:
    """Character ranges of the h1-h6 headings; one heading may span several lines."""
    return [(match.start(), match.end()) for match in HEADING_RE.finditer(source)]


def in_any_span(spans: list[tuple[int, int]], position: int) -> bool:
    """Whether position falls inside one of the ranges."""
    return any(start <= position < end for start, end in spans)


def heading_snippet(source: str, spans: list[tuple[int, int]], position: int) -> str:
    """The text of the heading around position, tags stripped."""
    for start, end in spans:
        if start <= position < end:
            plain = TAG_STRIP_RE.sub("", source[start:end])
            return " ".join(html.unescape(plain).split())
    return ""


def heading_rows(source: str, spans: list[tuple[int, int]], pool: list[Page]) -> list[Suggestion]:
    """Links that sit inside a heading: they never make a prose mention linked."""
    pages = {page.url: page for page in pool}
    rows: list[Suggestion] = []
    for anchor in ANCHOR_RE.finditer(source):
        if not in_any_span(spans, anchor.start()):
            continue
        page = pages.get(anchor.group(1))
        if page is None:
            continue
        line = source.count("\n", 0, anchor.start()) + 1
        text = " ".join(TAG_STRIP_RE.sub("", anchor.group(2)).split())
        mention = Mention(
            text=text or page.title,
            line=line,
            inline_code=False,
            snippet=heading_snippet(source, spans, anchor.start()),
        )
        rows.append(Suggestion(mention, page, False, in_heading=True))
    return rows


def scan(path: str, src: str, pages: list[Page], prefer: str) -> list[Suggestion]:
    """Topics with a strong article that the page mentions, in reading order."""
    source = read_source(path) or ""
    url = page_url(src, path)
    mask = prose_mask(source)
    text = TAG_RE.sub(blank, mask)
    pool = candidates(pages, path_lang(url), url)
    headings = heading_spans(source)
    linked: set[str] = set()
    for match in HREF_RE.finditer(source):
        if not in_any_span(headings, match.start()):
            linked.add(match.group(1))
    aliases = load_aliases()
    stopwords = load_stopwords()
    names = dict.fromkeys(name for page in pool for name in topic_names(page, stopwords))
    found: dict[str, Suggestion] = {}
    for name in names:
        variants = [name, *aliases.get(" ".join(tokens(name)), [])]
        mentions = [m for m in (first_mention(v, text, mask, source) for v in variants) if m is not None]
        if not mentions:
            continue
        ranked = rank(name, pool, aliases, prefer)
        if not ranked or not ranked[0].strong:
            continue
        mention = min(mentions, key=lambda item: item.line)
        target = ranked[0].page
        known = found.get(target.url)
        if known is None or mention.line < known.mention.line:
            found[target.url] = Suggestion(mention, target, target.url in linked)
    rows = [*found.values(), *heading_rows(source, headings, pool)]
    return sorted(rows, key=lambda item: item.mention.line)


def mistagged(pages: list[Page]) -> list[tuple[str, str]]:
    """Pages whose lang: argument (or the engine default 'ru') disagrees with the folder."""
    rows = []
    for page in pages:
        if page.lang_arg:
            if page.lang_arg != page.lang:
                rows.append((page.url, f"lang: '{page.lang_arg}', folder says '{page.lang}'"))
        elif page.engine_page and page.lang != "ru":
            rows.append((page.url, f"no lang: argument (engine default 'ru'), folder says '{page.lang}'"))
    return rows


def print_terms(terms: list[str], pages: list[Page], lang: str, self_url: str, prefer: str) -> None:
    """Top three pages per term, or why there is none."""
    aliases = load_aliases()
    pool = candidates(pages, lang, self_url)
    others = [page for page in pages if page.lang != lang]
    for term in terms:
        print(term)
        ranked = rank(term, pool, aliases, prefer)[:3]
        for found in ranked:
            label = "strong" if found.strong else "weak  "
            print(f"  {label} {found.value:4d}  {found.page.url}  {found.page.title}")
        if not any(found.strong for found in ranked):
            elsewhere = [found.page.url for found in rank(term, others, aliases, prefer) if found.strong][:3]
            print(f"  no {lang} article is about it" + (f"; other languages: {', '.join(elsewhere)}" if elsewhere else ""))


def print_scan(rows: list[Suggestion]) -> None:
    """Two lines per topic: line, status, mention and target; then the context."""
    for row in rows:
        if row.in_heading:
            status, where = "IN-HEADING", "heading"
        else:
            status = "LINKED " if row.linked else "MISSING"
            where = "inline code" if row.mention.inline_code else "prose"
        print(f"{row.mention.line:5d}  {status}  {row.mention.text} -> {row.page.url}  ({row.page.title})")
        print(f"       {where}: …{row.mention.snippet}…")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Command line."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("terms", nargs="*", help="tools, systems, languages, models or concepts to look up")
    parser.add_argument("--scan", metavar="PAGE", help="list the topics PAGE mentions that have an article")
    parser.add_argument("--check-lang", action="store_true", help="list pages whose lang: argument disagrees with their folder")
    parser.add_argument("--page", help="page the links go on: sets the language and is never suggested")
    parser.add_argument("--lang", choices=("en", "ru", "zh"), help="language of the page the links go on (default: en)")
    parser.add_argument("--os", dest="prefer", choices=OS_FOLDERS, default="linux", help="operating system to prefer")
    parser.add_argument("--src", default=DEFAULT_SRC, help="docroot (default: container/src)")
    args = parser.parse_args(argv)
    if not args.terms and not args.scan and not args.check_lang:
        parser.error("give terms, --scan PAGE or --check-lang")
    return args


def main(argv: list[str]) -> int:
    """Run term, scan or lang mode."""
    args = parse_args(argv)
    src = os.path.abspath(args.src)
    pages = build_index(src)
    if args.check_lang:
        rows = mistagged(pages)
        for url, reason in rows:
            print(f"{url}  {reason}")
        return 1 if rows else 0
    if args.scan:
        print_scan(scan(args.scan, src, pages, args.prefer))
        return 0
    self_url = page_url(src, args.page) if args.page else ""
    lang = args.lang or (path_lang(self_url) if args.page else "en")
    print_terms(args.terms, pages, lang, self_url, args.prefer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
