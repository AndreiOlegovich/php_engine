#!/usr/bin/env python3
# version 0.2.0
"""Look at an imported site and suggest the values of container/site-profile.env.

The profile asks for things only the site can tell: what every page renders
last, which parts every page shows, the page classes, the language folders,
the site's own hosts. Run this after the old site is in container/src and
before filling the profile; it prints what it found with counts and ends with
suggested profile lines. It writes nothing: copy the lines you agree with.

  python3 container/tools/discover_site.py                 # the files only
  python3 container/tools/discover_site.py --url           # also the local site
  python3 container/tools/discover_site.py --url http://127.0.0.1:8081 --sample 500

Without --url it reads the files: page classes (new XxxPage(...)), shared
includes and which of them writes </html>, language folders, the hosts in
links, legacy PHP that PHP 8 refuses, files that are not UTF-8. With --url
(default: SITE_LOCAL_URL of the profile) it also fetches up to --sample pages
of the running site, and the markers come from what the pages really render:
the last element with an id or class before </body> (SITE_BOTTOM_MARKER),
the ids and classes nearly every page holds (SITE_TAG_MARKERS) and the empty
<div id="..."></div> shell markers (SITE_PAGE_MARKERS). Redirects are not
followed, so a page that points to the live host is counted, not fetched.

Exit codes: 0 done, 2 bad arguments or no pages found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

CONTAINER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import htaccess_hosting  # noqa: E402  # pylint: disable=wrong-import-position

DEFAULT_SRC = CONTAINER / "src"
DEFAULT_PROFILE = CONTAINER / "site-profile.env"

PAGE_SUFFIXES = (".php", ".html", ".htm")
NEW_PAGE_RE = re.compile(r"\bnew\s+([A-Z][A-Za-z0-9_]*Page)\s*\(")
INCLUDE_RE = re.compile(r"\b(?:require|include)(?:_once)?\b[^;]*?['\"]([^'\"]+\.(?:php|inc|html?))['\"]")
HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
CANONICAL_RE = re.compile(r"""rel=["']canonical["'][^>]*href=["']https?://([^/"']+)|href=["']https?://([^/"']+)[^>]*rel=["']canonical["']""")
# ISO 639-1: a two-letter folder such as qa/ or db/ is a section, not a language.
LANGUAGE_CODES = "aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu"
LANGUAGES = frozenset(LANGUAGE_CODES.split())
LANG_DIR_RE = re.compile(r"^([a-z]{2})(?:[-_][a-z]{2})?$")
LEGACY = {
    "mysql_* functions (removed in PHP 7)": re.compile(r"\bmysql_[a-z_]+\s*\("),
    "ereg / eregi (removed in PHP 7)": re.compile(r"\beregi?(?:_replace)?\s*\("),
    "create_function (removed in PHP 8)": re.compile(r"\bcreate_function\s*\("),
    "each() (removed in PHP 8)": re.compile(r"(?<![\w>$])each\s*\(\s*\$"),
    "short open tag <? (off by default)": re.compile(r"<\?(?!php|=|xml)\s"),
}
SHELL_MARKER_RE = re.compile(r'<div id="([^"\']+)"></div>')
ATTR_RE = re.compile(r"""\b(id|class)=(["'])([^"']*)\2""")
STRUCTURAL = {"nav", "header", "footer", "aside", "main", "div", "section", "ul", "table", "form"}
LANDMARKS = ("nav", "header", "footer", "main", "aside")
TIMEOUT = 15
SHOWN = 10


def is_hidden(rel: Path) -> bool:
    """A file under a dot- or underscore-folder, or named _something: not a page."""
    return any(part.startswith((".", "_")) for part in rel.parts)


def read_text(path: Path) -> tuple[str, bool]:
    """The file as text, and whether it was valid UTF-8."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8"), True
    except UnicodeDecodeError:
        return data.decode("latin-1"), False


def url_of(rel: Path) -> str:
    """The URL path a docroot file answers at: a/index.php is /a/."""
    if rel.name in ("index.php", "index.html", "index.htm"):
        parent = rel.parent.as_posix()
        return "/" if parent == "." else f"/{parent}/"
    return "/" + rel.as_posix()


@dataclass
class FileReport:  # pylint: disable=too-many-instance-attributes  # one field per finding
    """What the files of the docroot say."""

    pages: list[Path] = field(default_factory=list)
    classes: Counter[str] = field(default_factory=Counter)
    includes: Counter[str] = field(default_factory=Counter)
    html_end_in: Counter[str] = field(default_factory=Counter)
    lang_dirs: Counter[str] = field(default_factory=Counter)
    hosts: Counter[str] = field(default_factory=Counter)
    canonical_hosts: Counter[str] = field(default_factory=Counter)
    legacy: Counter[str] = field(default_factory=Counter)
    not_utf8: list[str] = field(default_factory=list)


def git_ignored(src: Path, rels: list[Path]) -> set[Path]:
    """The paths git ignores ("name copy.php", logs, backups); none outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(src), "check-ignore", "-z", "--stdin"],
            input="".join(f"{rel.as_posix()}\0" for rel in rels).encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except OSError:
        return set()
    if result.returncode not in (0, 1):
        return set()
    return {Path(name.decode("utf-8")) for name in result.stdout.split(b"\0") if name}


def scan_files(src: Path) -> FileReport:
    """Read every .php/.html file once; pages are those no other file includes."""
    report = FileReport()
    texts: dict[Path, str] = {}
    candidates = sorted(path.relative_to(src) for path in src.rglob("*") if path.suffix.lower() in PAGE_SUFFIXES and path.is_file())
    ignored = git_ignored(src, candidates)
    for rel in candidates:
        if rel in ignored:
            continue
        path = src / rel
        text, utf8 = read_text(path)
        texts[rel] = text
        if not utf8:
            report.not_utf8.append(rel.as_posix())
    included = {Path(name).name for text in texts.values() for name in INCLUDE_RE.findall(text)}
    for rel, text in texts.items():
        if "</html>" in text.lower():
            report.html_end_in[rel.as_posix()] += 1
        for label, pattern in LEGACY.items():
            if pattern.search(text):
                report.legacy[label] += 1
        report.hosts.update(set(HOST_RE.findall(text)))
        report.canonical_hosts.update({a or b for a, b in CANONICAL_RE.findall(text)})
        if is_hidden(rel) or rel.name in included:
            continue
        report.pages.append(rel)
        report.classes.update(set(NEW_PAGE_RE.findall(text)))
        report.includes.update({Path(name).name for name in INCLUDE_RE.findall(text)})
        lang = LANG_DIR_RE.match(rel.parts[0]) if len(rel.parts) > 1 else None
        if lang and lang.group(1) in LANGUAGES:
            report.lang_dirs[rel.parts[0]] += 1
    # Which files write </html>, ranked by how many pages include them.
    ranked = Counter({name: report.includes.get(Path(name).name, 0) for name in report.html_end_in})
    report.html_end_in = ranked
    return report


class PageParser(HTMLParser):
    """The start tags with an id or class, in order, and where </body> starts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, str]] = []  # (tag, raw start tag)
        self.before_body_end: tuple[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or ""
        if any(name in ("id", "class") and value for name, value in attrs):
            self.elements.append((tag, raw))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("body", "html") and self.before_body_end is None and self.elements:
            self.before_body_end = self.elements[-1]


def attr_markers(raw: str) -> list[str]:
    """The id="..." and class="..." pieces of a start tag, exactly as written."""
    return [match.group(0) for match in ATTR_RE.finditer(raw) if match.group(3) and "'" not in match.group(0)]


@dataclass
class RenderReport:  # pylint: disable=too-many-instance-attributes  # one field per finding
    """What the rendered pages of the local site say."""

    fetched: int = 0
    status: Counter[str] = field(default_factory=Counter)
    last_element: Counter[str] = field(default_factory=Counter)
    present: Counter[str] = field(default_factory=Counter)
    marker_tag: dict[str, str] = field(default_factory=dict)
    shell_markers: Counter[str] = field(default_factory=Counter)
    no_shell: int = 0
    texts: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect instead of following it (it may point to the live host)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102  # pylint: disable=too-many-arguments,too-many-positional-arguments
        return None


def fetch(base: str, path: str) -> tuple[str, str]:
    """GET base+path without following redirects: (status label, html or '')."""
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(base.rstrip("/") + urllib.parse.quote(path, safe="/%:@"), headers={"X-Forwarded-Proto": "https", "User-Agent": "discover_site"})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
            return str(response.status), body
    except urllib.error.HTTPError as exc:
        return str(exc.code), ""
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as exc:
        return f"error ({type(exc).__name__})", ""


def sample(paths: list[str], size: int) -> list[str]:
    """Every k-th path, so the sample spreads over the whole tree."""
    if len(paths) <= size:
        return paths
    step = len(paths) / size
    return [paths[int(i * step)] for i in range(size)]


def scan_rendered(base: str, paths: list[str]) -> RenderReport:
    """Fetch the pages and count what they render."""
    report = RenderReport()
    for path in paths:
        status, html = fetch(base, path)
        report.status[status] += 1
        if status != "200" or "<html" not in html.lower():
            if not status.startswith("3"):
                report.failed.append(f"{path} [{status}]")
            continue
        report.fetched += 1
        report.texts.append(html)
        parser = PageParser()
        parser.feed(html)
        parser.close()
        if parser.before_body_end is not None:
            pieces = attr_markers(parser.before_body_end[1])
            if pieces:
                report.last_element[pieces[0]] += 1
        seen: set[str] = set()
        for tag, raw in parser.elements:
            if tag not in STRUCTURAL:
                continue
            for piece in attr_markers(raw):
                if piece not in seen:
                    seen.add(piece)
                    report.marker_tag.setdefault(piece, tag)
        report.present.update(seen)
        shells = set(SHELL_MARKER_RE.findall(html))
        report.shell_markers.update(shells)
        if not shells:
            report.no_shell += 1
    return report


def coverage(report: RenderReport, substring: str) -> int:
    """On how many fetched pages the substring appears literally."""
    return sum(substring in text for text in report.texts)


def marker_name(piece: str, tag: str, taken: set[str]) -> str:
    """A short --tag name from id="top-menu" or class="menu main": top-menu, menu."""
    value = piece.split("=", 1)[1].strip("\"'").split()[0]
    name = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or tag
    base, n = name, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def read_profile(path: Path) -> dict[str, str]:
    """The profile as a dict, the way the tests read it."""
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part // whole if whole else 0}%)"


def print_files(report: FileReport, src: Path) -> None:
    print(f"== Files under {src}: {len(report.pages)} pages")
    print("Page classes (new XxxPage(...)), pages using each:")
    for name, count in report.classes.most_common(SHOWN) or [("none found", 0)]:
        print(f"  {name}: {count}" if count else f"  {name}")
    print("Includes most pages share (header, footer, menu candidates):")
    for name, count in report.includes.most_common(SHOWN) or [("none found", 0)]:
        print(f"  {name}: {count}" if count else f"  {name}")
    print("Where </html> is written (the end of the page), by pages including that file:")
    for name, count in report.html_end_in.most_common(5) or [("no file holds </html>", 0)]:
        print(f"  {name}: included by {count}" if count else f"  {name}")
    if report.lang_dirs:
        print("Language folders: " + ", ".join(f"{name} ({count} pages)" for name, count in report.lang_dirs.most_common()))
    if report.canonical_hosts:
        print("Hosts in rel=canonical links: " + ", ".join(f"{h} ({c})" for h, c in report.canonical_hosts.most_common(5)))
    print("Hosts most files link to (the site's own hosts are among them: SITE_OWN_HOSTS): " + (", ".join(f"{h} ({c})" for h, c in report.hosts.most_common(SHOWN)) or "none"))
    if report.legacy:
        print("Legacy PHP that PHP 8 refuses or warns about, files with it:")
        for label, count in report.legacy.most_common():
            print(f"  {label}: {count}")
    if report.not_utf8:
        print(f"Files that are not UTF-8: {len(report.not_utf8)}, e.g. {', '.join(report.not_utf8[:3])}")
    found = htaccess_hosting.hints(src)
    kinds = Counter(item.problem for item in found)
    print("The hosting the old .htaccess was written for:")
    for problem, count in kinds.most_common():
        print(f"  {problem}: {count}")
    print(f"  looks written for: {htaccess_hosting.looks_like(found)}")
    print("  The target can be another hosting: SITE_HOSTING names it, and")
    print("  container/tools/htaccess_hosting.py checks the files against it.")


def print_rendered(report: RenderReport, base: str, asked: int, profile: dict[str, str]) -> None:
    print(f"\n== Rendered pages of {base}: {report.fetched} HTML pages of {asked} asked")
    print("Answers: " + ", ".join(f"{status} {count}" for status, count in report.status.most_common()))
    if report.failed:
        print(f"No HTML page (an error, or an include served alone: fix it or keep it out of the sitemap; {len(report.failed)}): " + ", ".join(report.failed[:SHOWN]))
    if not report.fetched:
        print("No page answered 200 with HTML: is the site running at that address?")
        return
    print("Last element with an id or class before </body> (bottom marker candidates):")
    for piece, count in report.last_element.most_common(5):
        print(f"  {piece}: last on {pct(count, report.fetched)}, present on {pct(coverage(report, piece), report.fetched)}")
    print("On nearly every page (90% or more; --tag candidates, landmarks first):")
    common = sorted((p for p, c in report.present.items() if c * 10 >= report.fetched * 9), key=lambda p: tag_rank(report, p))
    for piece in common[:SHOWN]:
        count = report.present[piece]
        print(f"  <{report.marker_tag[piece]} {piece}>: {pct(count, report.fetched)}")
    print("Shell markers <div id=...></div> (page classes):")
    for name, count in report.shell_markers.most_common(SHOWN) or [("none", 0)]:
        print(f"  {name}: {pct(count, report.fetched)}" if count else "  none")
    if report.shell_markers:
        print(f"  pages with none: {report.no_shell}")
    current = [key for key in ("SITE_BOTTOM_MARKER", "SITE_TAG_MARKERS", "SITE_PAGE_MARKERS") if profile.get(key)]
    if current:
        print("The profile now:")
        for key in current:
            pieces = [part.split("=", 1)[-1] for part in profile[key].replace("|", ";").split(";")] if key == "SITE_TAG_MARKERS" else profile[key].split()
            for piece in filter(None, (p.strip() for p in pieces)):
                needle = f'<div id="{piece}"></div>' if key == "SITE_PAGE_MARKERS" else piece
                print(f"  {key} {piece}: on {pct(coverage(report, needle), report.fetched)}")


def tag_rank(rendered: RenderReport, piece: str) -> tuple[int, bool, bool, int, str]:
    """Among the pieces on 90% of the pages: landmarks first (nav, header, footer...),
    then top-level classes over their children (a__b), ids last, then coverage."""
    tag = rendered.marker_tag[piece]
    landmark = LANDMARKS.index(tag) if tag in LANDMARKS else len(LANDMARKS)
    return (landmark, "__" in piece, piece.startswith("id="), -rendered.present[piece], piece)


def page_markers(files: FileReport, rendered: RenderReport) -> list[str]:
    """Shell markers named after a page class (ArticlePage_html_id), else those on 5% or more."""
    named = [name for name, _ in rendered.shell_markers.most_common() if any(cls in name for cls in files.classes)]
    if named:
        return named
    return [name for name, count in rendered.shell_markers.most_common() if count * 20 >= rendered.fetched]


def suggestions(files: FileReport, rendered: RenderReport | None) -> list[str]:
    """The profile lines the evidence supports."""
    lines: list[str] = []
    bottom = ""
    if rendered is not None and rendered.fetched:
        if rendered.last_element:
            piece, _ = rendered.last_element.most_common(1)[0]
            if coverage(rendered, piece) * 10 >= rendered.fetched * 9:
                bottom = piece
                lines.append(f"SITE_BOTTOM_MARKER='{piece}'")
        taken: set[str] = set()
        common = [p for p, c in rendered.present.items() if c * 10 >= rendered.fetched * 9 and p != bottom]
        tagged = [f"{marker_name(p, rendered.marker_tag[p], taken)}={p}" for p in sorted(common, key=lambda p: tag_rank(rendered, p))[:3]]
        if tagged:
            lines.append(f"SITE_TAG_MARKERS='{'; '.join(tagged)}'")
        shells = page_markers(files, rendered)
        if shells:
            lines.append(f'SITE_PAGE_MARKERS="{" ".join(shells)}"')
    if files.classes:
        lines.append(f'SITE_PAGE_CLASSES="{" ".join(name for name, _ in files.classes.most_common())}"')
    if files.lang_dirs:
        lines.append(f'SITE_LANG_DIRS="{" ".join(sorted(files.lang_dirs))}"')
    own = [host for host, count in files.canonical_hosts.most_common(3) if count * 10 >= len(files.pages)]
    if own:
        lines.append(f'SITE_OWN_HOSTS="{" ".join(own)}"')
    return lines


def parse_args(argv: list[str] | None, profile_url: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="discover_site.py", description="Suggest the site-profile.env values of an imported site.")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="the docroot (default: container/src)")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="the profile to compare with (default: container/site-profile.env)")
    parser.add_argument("--url", nargs="?", const=profile_url or None, default=None, help="also read the rendered pages of this running site (default with no value: SITE_LOCAL_URL)")
    parser.add_argument("--sample", type=int, default=300, help="at most this many pages are fetched (default: 300)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    profile_url = read_profile(DEFAULT_PROFILE).get("SITE_LOCAL_URL", "")
    args = parse_args(raw, profile_url)
    profile = read_profile(args.profile)
    if args.url is None and "--url" in raw:
        print("error: --url needs an address: SITE_LOCAL_URL is not in the profile", file=sys.stderr)
        return 2
    if not args.src.is_dir():
        print(f"error: {args.src} is not a directory", file=sys.stderr)
        return 2
    files = scan_files(args.src)
    if not files.pages:
        print(f"error: no .php or .html pages under {args.src}: copy the site in first", file=sys.stderr)
        return 2
    print_files(files, args.src)
    rendered = None
    if args.url:
        paths = sample(sorted(url_of(rel) for rel in files.pages), args.sample)
        rendered = scan_rendered(args.url, paths)
        print_rendered(rendered, args.url, len(paths), profile)
    else:
        print("\nThe markers come from the rendered pages: start the site and run again with --url.")
    lines = suggestions(files, rendered)
    print("\n== Suggested lines for container/site-profile.env (check each, then copy):")
    for line in lines or ["(nothing certain enough to suggest)"]:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
