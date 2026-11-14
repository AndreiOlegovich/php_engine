"""Build the page index of container/src: one record per sitemap page.

The index is the shared data for link lookups (find_article.py) and for the
site search: the PHP page or JS widget reads a per-language export and ranks
the pages by their token lists.

    python3 container/tools/search/site_index.py --write /path/to/index.json
    python3 container/tools/search/site_index.py --search /path/to/dir

The docroot defaults to container/src (--src overrides it). The search
export's default DIR is container/src/search/.

Full index schema (version 3): {"version": 3, "pages": [record, ...]}, where
a record is {"url", "lang", "title", "head", "description", "lang_arg",
"engine_page", "folders", "title_tokens", "head_tokens", "keyword_tokens",
"description_tokens", "h1_tokens", "h2_tokens", "h3_tokens"}. lang comes from the folder (SITE_LANG_DIRS, default /ru/ is 'ru',
/zh/ is 'zh', the rest of the site is 'en'. head is the title before its
first ':' or ' - '. folders holds the tokens of each URL folder, so
/ai/models/claude5_5/ is [["ai"], ["model"], ["claude", "5", "5"]].

title and head stay as the page writes them; title_tokens, head_tokens and
keyword_tokens are taken after the HTML entities are decoded, so
"C&plus;&plus; classes" gives "cpp" and "classe", not "c", "plus", "plus".
The search export writes the decoded title, which the search page escapes
when it prints it.

description is the page constructor's description: argument; a page without
one takes the content of a <meta name="description"> tag (the legacy pages
that build no page object). HTML entities are decoded, whitespace is
collapsed to single spaces, and a page with neither source gets "".

h1_tokens, h2_tokens and h3_tokens hold the words of the literal <h1>, <h2>
and <h3> elements of the page file, in page order: HTML comments are skipped,
inner tags dropped and entities decoded. A heading that PHP builds (it holds
"<?", or a string concatenation outside its inner tags) is left out, and so
is one an include prints. An engine page prints its title as the <h1>, so h1_tokens is filled
only by a page that writes its own <h1>.

Search export schema (version 3), one file per language, written to
DIR/site_index.<lang>.json:

    {"version": 3, "lang": "en", "aliases": [["javascript", "js"], ...],
     "pages": [
      {"url", "title", "description", "folders", "title_tokens",
       "head_tokens", "keyword_tokens", "description_tokens",
       "h1_tokens", "h2_tokens", "h3_tokens"}, ...]}

The three heading fields are written only when they hold a word. They leave
out the function words of HEADING_FUNCTION_WORDS ("was", "it", "the"), and
each holds only the words no stronger field of the page has: h1_tokens leaves out
the words of the URL folders and the title, h2_tokens also those of the
keywords and the h1, h3_tokens also those of the h2 and the description. The
search scores a word by its best field, so a repeated word would only make
the file larger.

aliases holds the synonym groups of aliases.json (next to this tool), the
same in every language file: each name of a group as its tokens joined by
spaces ("regular expression", "docker compose"), the main name first. A name that
tokenizes like an earlier one of its group is dropped, and so is a group
left with one name. The search treats the names of a group as one word.

It is compact JSON (no indent, "," and ":" separators) with only what a
search needs. Every token list is sorted, duplicates dropped, and written
as one space-separated string to keep the file small; a token never holds a
space, "" is an empty list, and a port splits only a non-empty string.
folders holds one such string per URL folder; the ".php" of a file name is
left out of the last one, so a search for "php" does not match every file
URL (the full index keeps it). A page with an empty title takes the last
segment of its URL: the folder of a folder URL, the file name without ".php"
otherwise. Pages stay in sitemap order and nothing time-dependent is
written, so two runs over one tree give byte-identical files. The default
DIR is
container/src/search/ (the docroot), so PHP can read the file next to the
search page and JS can fetch it.

Tokens, the same for page data and queries (a PHP or JS port needs exactly
this): lowercase, "c++" becomes "cpp", "c#" becomes "csharp", a ".net"
that does not follow a letter becomes "dotnet" and the word "k8s" becomes
"kubernetes", split at
every character that is not a letter or digit and between letters and
digits, then a trailing "s" is dropped from words longer than three
letters unless they end in "ss".

Only pages listed in container/src/sitemap.xml are indexed, and redirect
stubs are left out.
"""

import argparse
import html
import json
import os
import re
import sys
from dataclasses import asdict, dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "feed"))

# The feed folder is put on the path above, which pylint cannot follow.
from if_file_is_an_article import page_title, redirect_target  # pylint: disable=wrong-import-position,import-error  # noqa: E402

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(TOOL_DIR)))
DEFAULT_SRC = os.path.join(REPO_ROOT, "container", "src")
DEFAULT_SEARCH = os.path.join(DEFAULT_SRC, "search")
ALIASES_FILE = os.path.join(TOOL_DIR, "aliases.json")
INDEX_VERSION = 3
# Words of a heading that name no topic ("What was changed", "How it
# works"). The search export leaves them out of the heading fields, and the
# search takes them as optional query words (SiteSearch::STOPWORDS holds
# every one of them, a test checks it). Words that are also a keyword, a
# command or a term stay in: for, if, in, is, do, then, this, which, where,
# when, some, our, here (here document), so (.so), just, may, my, about.
HEADING_FUNCTION_WORDS = [
    "a",
    "an",
    "the",
    "it",
    "its",
    "was",
    "were",
    "be",
    "been",
    "being",
    "are",
    "has",
    "have",
    "had",
    "does",
    "did",
    "doesn",
    "don",
    "didn",
    "isn",
    "aren",
    "wasn",
    "hasn",
    "haven",
    "can",
    "could",
    "couldn",
    "should",
    "shouldn",
    "would",
    "wouldn",
    "will",
    "might",
    "must",
    "there",
    "their",
    "they",
    "them",
    "these",
    "those",
    "his",
    "her",
    "him",
    "she",
    "he",
    "we",
    "you",
    "your",
    "yours",
    "me",
    "such",
    "than",
    "that",
    "very",
    "also",
    "too",
    "but",
    "because",
    "again",
    "still",
    "already",
    "yet",
    "of",
    "to",
    "what",
    "why",
    "how",
    "whose",
    "whom",
]

LANG_FOLDERS = dict(item.split("=", 1) for item in os.environ.get("SITE_LANG_DIRS", "/ru/=ru /zh/=zh").split())
LOC_RE = re.compile(r"<loc>https?://[^/<]+(/[^<]*)</loc>")
# Named arguments anywhere, also when the call is on one line; code_lang:
# and similar names do not count.
LANG_ARG_RE = re.compile(r"(?<![\w$])lang:\s*'(\w+)'")
KEYWORDS_RE = re.compile(r"(?<![\w$])keywords:\s*'([^']*)'")
DESCRIPTION_RE = re.compile(r"(?<![\w$])description:\s*'((?:[^'\\]|\\.)*)'")
DESCRIPTION_DQ_RE = re.compile(r'(?<![\w$])description:\s*"((?:[^"\\]|\\.)*)"')
META_DESCRIPTION_RE = re.compile(r'<meta\b[^>]*?\bname=["\']description["\'][^>]*?\bcontent\s*=\s*(["\'])(.*?)\1', re.I | re.S)
META_DESCRIPTION_REV_RE = re.compile(r'<meta\b[^>]*?\bcontent\s*=\s*(["\'])(.*?)\1[^>]*?\bname=["\']description["\']', re.I | re.S)
# Any page object, custom subclasses and namespaced names included, so a
# legacy file that builds none is told apart.
PAGE_CTOR_RE = re.compile(r"new\s+(?:[A-Za-z_\\][\w\\]*)?Page\s*\(")
ENGINE_CTOR_RE = re.compile(r"new\s+(?:ArticlePage|BasePage)\s*\(")
TOKEN_RE = re.compile(r"[^\W_]+")
LETTER_DIGIT_RE = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")
DOTNET_RE = re.compile(r"(?<!\w)\.net\b")
# "k8s" as a word of its own: split at its digit it would be the letters
# "k", "8" and "s", which "mhash_keygen_s2k" and "2.5.8" also give.
K8S_RE = re.compile(r"(?<![^\W_])k8s(?![^\W_])")
TITLE_HEAD_RE = re.compile(r"\s*(?::|\s[-–—|]\s)")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
HEADING_RE = re.compile(r"<h([123])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]*>")
# A heading PHP builds: an open tag anywhere in it, or a string concatenation
# in its text. The concatenation is looked for with every inner tag turned
# into a NUL, so title="... it." of an <abbr> is not taken for one, and
# neither are a full stop and a quote on the two sides of a <br>.
PHP_TAG_RE = re.compile(r"<\?")
PHP_CONCAT_RE = re.compile(r"['\"]\s*\.|\.\s*['\"]")


@dataclass
class Page:  # pylint: disable=too-many-instance-attributes  # one field per index column
    """One indexed page."""

    url: str
    lang: str
    title: str
    head: str
    description: str
    lang_arg: str
    engine_page: bool
    folders: list[list[str]]
    title_tokens: list[str]
    head_tokens: list[str]
    keyword_tokens: list[str]
    description_tokens: list[str]
    h1_tokens: list[str]
    h2_tokens: list[str]
    h3_tokens: list[str]


def tokens(text: str) -> list[str]:
    """Lowercase word tokens, split at letter/digit changes, light plural strip."""
    out = []
    text = DOTNET_RE.sub("dotnet", text.lower().replace("c++", "cpp").replace("c#", "csharp"))
    text = K8S_RE.sub("kubernetes", text)
    for word in TOKEN_RE.findall(LETTER_DIGIT_RE.sub(" ", text)):
        out.append(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word)
    return out


def title_head(title: str) -> str:
    """Title before its first ':' or ' - '."""
    return TITLE_HEAD_RE.split(title, maxsplit=1)[0].strip()


def clean_text(text: str) -> str:
    """HTML entities decoded, whitespace collapsed to single spaces."""
    return " ".join(html.unescape(text).split())


def php_unescape(text: str) -> str:
    """Drop the backslashes of a PHP double-quoted string."""
    return text.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def page_description(source: str) -> str:
    """Constructor description: argument; a legacy page without one uses its meta tag."""
    if PAGE_CTOR_RE.search(source):
        match = DESCRIPTION_RE.search(source) or DESCRIPTION_DQ_RE.search(source)
        return clean_text(php_unescape(match.group(1))) if match is not None else ""
    match = META_DESCRIPTION_RE.search(source) or META_DESCRIPTION_REV_RE.search(source)
    return clean_text(match.group(2)) if match is not None else ""


def heading_tokens(source: str) -> dict[str, list[str]]:
    """Words of the literal <h1>, <h2> and <h3> elements, by level, in page order."""
    found: dict[str, list[str]] = {"1": [], "2": [], "3": []}
    if "<h" not in source and "<H" not in source:
        return found
    for level, inner in HEADING_RE.findall(HTML_COMMENT_RE.sub(" ", source)):
        if PHP_TAG_RE.search(inner):
            continue
        if PHP_CONCAT_RE.search(TAG_RE.sub("\0", inner)):
            continue
        found[level].extend(tokens(clean_text(TAG_RE.sub(" ", inner))))
    return found


def fallback_title(url: str) -> str:
    """Title of a page without one: its last URL segment, .php dropped."""
    last = url.rstrip("/").rsplit("/", 1)[-1]
    return last[: -len(".php")] if last.endswith(".php") else last


def path_lang(url: str) -> str:
    """Language of a URL by its first folder."""
    for folder, lang in LANG_FOLDERS.items():
        if url.startswith(folder):
            return lang
    return "en"


def read_source(path: str) -> str | None:
    """File text as UTF-8, else cp1251 for the legacy pages."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def url_to_path(src: str, url: str) -> str:
    """Src file behind a URL: a folder URL means its index.php."""
    rel = url.lstrip("/")
    return os.path.join(src, rel + "index.php") if url.endswith("/") else os.path.join(src, rel)


def page_url(src: str, path: str) -> str:
    """URL of a src file."""
    rel = os.path.relpath(os.path.abspath(path), src).replace(os.sep, "/")
    return "/" + rel[: -len("index.php")] if rel.endswith("index.php") else "/" + rel


def is_redirect(path: str, source: str) -> bool:
    """True for a redirect stub; the substring check skips the slow regexes."""
    lowered = source.lower()
    if "location" not in lowered and "refresh" not in lowered:
        return False
    return redirect_target(path) is not None


def load_page(src: str, url: str) -> Page | None:
    """Record for a sitemap URL, or None for missing files and redirect stubs."""
    path = url_to_path(src, url)
    source = read_source(path)
    if source is None or is_redirect(path, source):
        return None
    title = page_title(path) or ""
    lang_arg = LANG_ARG_RE.search(source)
    keywords = KEYWORDS_RE.search(source)
    # The words come from the decoded title: "C&plus;&plus; classes" is "cpp classe".
    title_text = clean_text(title)
    head = title_head(title)
    description = page_description(source)
    headings = heading_tokens(source)
    return Page(
        url=url,
        lang=path_lang(url),
        title=title,
        head=head,
        description=description,
        lang_arg=lang_arg.group(1) if lang_arg else "",
        engine_page=bool(ENGINE_CTOR_RE.search(source)),
        folders=[tokens(part) for part in url.strip("/").split("/") if part],
        title_tokens=tokens(title_text),
        head_tokens=tokens(title_head(title_text)),
        keyword_tokens=sorted(set(tokens(html.unescape(keywords.group(1))))) if keywords else [],
        description_tokens=tokens(description),
        h1_tokens=headings["1"],
        h2_tokens=headings["2"],
        h3_tokens=headings["3"],
    )


def build_index(src: str) -> list[Page]:
    """Every sitemap page that is a real page, in sitemap order."""
    sitemap = read_source(os.path.join(src, "sitemap.xml")) or ""
    pages = []
    for url in dict.fromkeys(LOC_RE.findall(sitemap)):
        page = load_page(src, url)
        if page is not None:
            pages.append(page)
    return pages


def dump_json(data: object, path: str, compact: bool) -> None:
    """Write JSON through a temp file, so readers never see half of it."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        if compact:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def write_index(pages: list[Page], path: str) -> None:
    """Write the full JSON index through a temp file."""
    dump_json({"version": INDEX_VERSION, "pages": [asdict(page) for page in pages]}, path, compact=False)


def token_field(items: list[str]) -> str:
    """Sorted unique tokens as one space-separated string; "" when empty."""
    return " ".join(sorted(set(items)))


def search_folders(page: Page) -> list[list[str]]:
    """URL folders for the search: the ".php" of a file name is not a word of the page."""
    folders = [list(folder) for folder in page.folders]
    if folders and page.url.endswith(".php") and folders[-1][-1:] == ["php"]:
        folders[-1].pop()
    return folders


def search_record(page: Page) -> dict[str, object]:
    """One export record: the text a search shows and the sorted tokens it ranks by."""
    folders = search_folders(page)
    record: dict[str, object] = {
        "url": page.url,
        "title": clean_text(page.title) or fallback_title(page.url),
        "description": page.description,
        "folders": [token_field(folder) for folder in folders],
        "title_tokens": token_field(page.title_tokens),
        "head_tokens": token_field(page.head_tokens),
        "keyword_tokens": token_field(page.keyword_tokens),
        "description_tokens": token_field(page.description_tokens),
    }
    # A heading field holds only the words no stronger field has, and is
    # left out when nothing is left.
    seen = {token for folder in folders for token in folder} | set(page.title_tokens)
    function_words = {token for word in HEADING_FUNCTION_WORDS for token in tokens(word)}
    for key, items, also in (
        ("h1_tokens", page.h1_tokens, ()),
        ("h2_tokens", page.h2_tokens, page.keyword_tokens),
        ("h3_tokens", page.h3_tokens, page.description_tokens),
    ):
        seen |= set(also)
        field = token_field([token for token in items if token not in seen and token not in function_words])
        if field:
            record[key] = field
        seen |= set(items)
    return record


def alias_groups(path: str = ALIASES_FILE) -> list[list[str]]:
    """Synonym groups of aliases.json as token strings, the main name first."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return []
    groups = []
    for name, others in raw.items() if isinstance(raw, dict) else []:
        names = [" ".join(tokens(item)) for item in [name, *others] if isinstance(item, str)]
        group = list(dict.fromkeys(item for item in names if item))
        if len(group) > 1:
            groups.append(group)
    return groups


def write_search(pages: list[Page], directory: str, aliases: list[list[str]] | None = None) -> list[tuple[str, int]]:
    """Write one compact JSON file per language; return its path and page count."""
    os.makedirs(directory, exist_ok=True)
    if aliases is None:
        aliases = alias_groups()
    written = []
    for lang in sorted({page.lang for page in pages}):
        records = [search_record(page) for page in pages if page.lang == lang]
        path = os.path.join(directory, f"site_index.{lang}.json")
        dump_json({"version": INDEX_VERSION, "lang": lang, "aliases": aliases, "pages": records}, path, compact=True)
        written.append((path, len(records)))
    return written


def main(argv: list[str]) -> int:
    """Build the index and write the full index, the search export or both."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--write", metavar="PATH", help="full JSON index to write")
    parser.add_argument(
        "--search",
        nargs="?",
        const=DEFAULT_SEARCH,
        metavar="DIR",
        help="write site_index.<lang>.json per language to DIR (default: container/src/search)",
    )
    parser.add_argument("--src", default=DEFAULT_SRC, help="docroot (default: container/src)")
    args = parser.parse_args(argv)
    if not args.write and args.search is None:
        parser.error("give --write PATH or --search [DIR]")
    pages = build_index(os.path.abspath(args.src))
    if args.write:
        write_index(pages, args.write)
        print(f"{len(pages)} pages -> {args.write}")
    if args.search is not None:
        for path, count in write_search(pages, args.search):
            print(f"{count} pages -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
