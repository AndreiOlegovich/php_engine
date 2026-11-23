# version 0.1.1
"""Internal-link check: every <a href="/..."> on a sitemap page must answer 200.

Each sitemap page is fetched and parsed; every root-relative link
(`href` starting with one `/`, so `//host/...` stays out) is requested on
BASE_URL. The `#fragment` is dropped, the query string is kept. A link
target shared by many pages (menus, footers) is requested once per run.

Redirects are followed the way test_sitemap.py follows them: pinned to
BASE_URL, at most 5 hops. A link passes when the final answer is 200, or
when a redirect leaves the site (an `.htaccess` rule sending the old URL
to another domain). Landing on /404.php counts as 404.

A link target is requested once, but it is reported on every page that
holds it, from both sides:
  - one pytest case per sitemap page: the failure names the page and lists
    each of its broken links with the number of pages sharing that link;
  - the trailing `test_summary` prints (always, through pytest's capture)
    and attaches the reverse view: every broken link, then every page it
    is on. A CSV with one row per page and broken link goes along.

Reads SITEMAP_FILE (default /data/sitemap.xml, i.e. ./src/sitemap.xml),
rewrites each URL's host to BASE_URL (default http://php-apache).

Env:
  BASE_URL         e.g. http://php-apache (compose) or http://127.0.0.1:8080 (host)
  SITEMAP_FILE     e.g. /data/sitemap.xml
  PATH_PREFIX      e.g. /section -> only check pages whose path is exactly the
                   prefix or starts with prefix + "/". The links on those
                   pages are checked wherever they point.
                   Empty (default) -> check all pages.
  EXCLUDE_SUBSTR   comma-separated substrings; a sitemap page whose path
                   contains any of them is skipped.
  FAIL_ON_NON_200  "true" (default) -> a page with a non-200 link, or a page
                   that does not answer 200 itself, fails;
                   "false" -> report-only, cases always pass.
"""

import csv
import io
import json
import os
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import quote, urldefrag, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import allure
import pytest
import requests
from url_selection import in_selection, selection_label, skip_if_nothing_selected

BASE_URL = os.environ.get("BASE_URL", "http://php-apache").rstrip("/")
SITEMAP_FILE = os.environ.get("SITEMAP_FILE", "/data/sitemap.xml")
FAIL_ON_NON_200 = os.environ.get("FAIL_ON_NON_200", "true").lower() == "true"
TIMEOUT = (5, 15)  # (connect, read) seconds
REDIRECT_CODES = (301, 302, 303, 307, 308)


def _normalize_prefix(raw):
    p = (raw or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    return p


PATH_PREFIX = _normalize_prefix(os.environ.get("PATH_PREFIX", ""))


def _exclude_substrings(raw):
    return [s for s in ((p or "").strip() for p in (raw or "").split(",")) if s]


EXCLUDE_SUBSTR = _exclude_substrings(os.environ.get("EXCLUDE_SUBSTR", ""))


def _is_excluded(loc):
    path = urlparse(loc).path or "/"
    return any(s in path for s in EXCLUDE_SUBSTR)


def sitemap_locs(path):
    tree = ElementTree.parse(path)
    ns = {"s": "https://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [e.text.strip() for e in tree.getroot().findall("s:url/s:loc", ns) if e.text]
    # tolerate sitemaps without namespace
    if not locs:
        locs = [e.text.strip() for e in tree.getroot().iter("loc") if e.text]
    assert locs, f"no <loc> entries found in {path}"
    return locs


def _matches_prefix(loc, prefix):
    if not prefix:
        return True
    path = urlparse(loc).path or "/"
    return path == prefix or path.startswith(prefix + "/")


if not os.path.isfile(SITEMAP_FILE):
    pytest.skip(f"no sitemap at {SITEMAP_FILE}: generate it first", allow_module_level=True)

ALL_LOCS = sitemap_locs(SITEMAP_FILE)
LOCS = [u for u in ALL_LOCS if _matches_prefix(u, PATH_PREFIX) and not _is_excluded(u) and in_selection(u)]
skip_if_nothing_selected(LOCS)
assert LOCS, f"no urls matching PATH_PREFIX={PATH_PREFIX!r} with EXCLUDE_SUBSTR={EXCLUDE_SUBSTR!r} in {SITEMAP_FILE}"

#: hosts that are this site: a redirect to one of them is followed on
#: BASE_URL, a redirect anywhere else ends the check as "external".
_HOSTS = {urlparse(BASE_URL).netloc} | {urlparse(u).netloc for u in ALL_LOCS}
OWN_HOSTS = frozenset(_HOSTS | {h.removeprefix("www.") for h in _HOSTS} | {"www." + h.removeprefix("www.") for h in _HOSTS})


def to_local(url):
    """Swap the host for the checked host, keep path/query, encode path."""
    p = urlparse(url)
    local_path = quote(p.path or "/", safe="/%:@")
    return urlunparse(urlparse(BASE_URL)._replace(path=local_path, query=p.query or ""))


class _LinkParser(HTMLParser):
    """Collect the href of every <a> that starts with a single slash."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for key, value in attrs:
            if key == "href" and value:
                href = value.strip()
                if href.startswith("/") and not href.startswith("//"):
                    self.hrefs.append(href)


def internal_hrefs(html):
    """Root-relative <a href> values of a page, in page order, deduplicated."""
    parser = _LinkParser()
    parser.feed(html)
    parser.close()
    return list(dict.fromkeys(parser.hrefs))


def link_target(href):
    """The local URL a root-relative href is checked at (no #fragment)."""
    return to_local(urldefrag(href).url)


def fetch(session, url, max_hops=5, body=True):
    """GET with manual redirect following pinned to BASE_URL.

    Some pages answer http -> https (absolute Location built from Host).
    There is no TLS locally, so a redirect to this site is rewritten back
    to BASE_URL's scheme+host and re-requested over plain HTTP. A redirect
    to another host is not followed: external=True.
    Returns (status_or_error, final_url, hops, external, text)."""
    hops = 0
    while True:
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=False, stream=not body)
        except requests.RequestException as exc:
            return f"{type(exc).__name__}: {exc}", url, hops, False, ""
        try:
            loc = r.headers.get("Location", "")
            if r.status_code in REDIRECT_CODES and loc and hops < max_hops:
                p = urlparse(urljoin(url, loc))
                if p.netloc not in OWN_HOSTS:
                    return r.status_code, p.geturl(), hops + 1, True, ""
                url = to_local(p.geturl())
                hops += 1
                continue
            text = r.text if body else ""
            return r.status_code, r.url, hops, False, text
        finally:
            r.close()


def final_status(local, status, final):
    if urlparse(final).path == "/404.php" and urlparse(local).path != "/404.php":
        # .htaccess ErrorDocument 404 -> external 404.php, which we rewrote
        # back to the local host: landing on /404.php means the URL is missing.
        return 404
    return status


def link_ok(result):
    return result["status"] == 200 or result["external"]


def bucket(result):
    if isinstance(result["status"], str):
        return "error"
    if result["status"] != 200:
        return "page-non-200"
    if result["broken"]:
        return "broken-links"
    return "ok"


@pytest.fixture(scope="session")
def checked():
    """Fetch every page and every distinct link target once.

    The request is shared, the report is not: each page keeps its own list
    of broken links, and each broken target keeps the list of pages (and
    the href spellings) it was found on."""
    session = requests.Session()
    # Production sits behind a TLS proxy; .htaccess forces https unless this
    # header is present. Sending it emulates production over plain local http.
    session.headers.update({"X-Forwarded-Proto": "https"})
    targets = {}  # local target URL -> link result, shared by all pages
    pages = {}
    for loc in LOCS:
        local = to_local(loc)
        status, final, hops, _external, text = fetch(session, local)
        status = final_status(local, status, final)
        hrefs = internal_hrefs(text) if status == 200 else []
        broken = []
        for href in hrefs:
            target = link_target(href)
            if target not in targets:
                t_status, t_final, t_hops, t_external, _ = fetch(session, target, body=False)
                targets[target] = {
                    "status": final_status(target, t_status, t_final),
                    "final": t_final,
                    "hops": t_hops,
                    "external": t_external,
                    "pages": [],  # sitemap pages holding a link to it
                    "hrefs": [],  # how those pages spell the link
                }
            t = targets[target]
            if link_ok(t):
                continue
            if loc not in t["pages"]:
                t["pages"].append(loc)
            if href not in t["hrefs"]:
                t["hrefs"].append(href)
            broken.append({"href": href, "target": target, "status": t["status"], "final": t["final"], "hops": t["hops"]})
        pages[loc] = {"status": status, "final": final, "hops": hops, "links": len(hrefs), "broken": broken}
    return {"pages": pages, "targets": targets}


def _case_id(loc):
    return urlparse(loc).path or "/"


def _target_path(target):
    p = urlparse(target)
    return p.path + (f"?{p.query}" if p.query else "")


def _redirect_note(link):
    return f", {link['hops']} redirects -> {link['final']}" if link["hops"] else ""


def _broken_line(link, targets):
    shared = len(targets[link["target"]]["pages"])
    on_pages = f" (on {shared} pages, see the summary)" if shared > 1 else ""
    return f"  {link['href']} [{link['status']}]{_redirect_note(link)}{on_pages}"


def broken_by_link(targets):
    """Reverse view: broken targets, most widespread first, with their pages."""
    broken = [(target, t) for target, t in targets.items() if t["pages"]]
    broken.sort(key=lambda item: (-len(item[1]["pages"]), _target_path(item[0])))
    return broken


def broken_by_link_text(targets):
    """Plain-text report: each broken link, then every page it is on."""
    broken = broken_by_link(targets)
    if not broken:
        return "no broken internal links"
    pages_total = len({loc for _, t in broken for loc in t["pages"]})
    lines = [f"broken internal links: {len(broken)} links on {pages_total} pages"]
    for target, t in broken:
        spellings = [h for h in t["hrefs"] if h != _target_path(target)]
        also = f"; written as {', '.join(spellings)}" if spellings else ""
        lines.append("")
        count = len(t["pages"])
        lines.append(f"{_target_path(target)} [{t['status']}]{_redirect_note(t)} - on {count} page{'s' if count != 1 else ''}{also}:")
        lines.extend(f"    {_case_id(loc)}" for loc in t["pages"])
    return "\n".join(lines)


@allure.epic("Internal links")
@allure.feature('Every <a href="/..."> answers 200')
@pytest.mark.parametrize("loc", LOCS, ids=[f"{i:04d} {_case_id(u)}" for i, u in enumerate(LOCS)])
def test_internal_links_return_200(checked, loc):
    local = to_local(loc)
    with allure.step(f"GET {local} and its internal links"):
        res = checked["pages"][loc]
        allure.attach(
            f"{local} -> {res['final']} [{res['status']}] ({res['hops']} redirects, {res['links']} internal links, {len(res['broken'])} broken)",
            name="response",
            attachment_type=allure.attachment_type.TEXT,
        )
        if res["status"] != 200:
            if FAIL_ON_NON_200:
                pytest.fail(f"{loc} -> {res['final']} [{res['status']}]: the page itself does not answer 200")
            pytest.skip(f"page answers {res['status']} (report-only)")
        if res["broken"]:
            allure.attach(
                json.dumps(res["broken"], indent=2, ensure_ascii=False),
                name="broken-links",
                attachment_type=allure.attachment_type.JSON,
            )
        if FAIL_ON_NON_200 and res["broken"]:
            lines = "\n".join(_broken_line(b, checked["targets"]) for b in res["broken"])
            pytest.fail(f"page {_case_id(loc)}: {len(res['broken'])} of {res['links']} internal links do not answer 200:\n{lines}", pytrace=False)


@allure.epic("Internal links")
@allure.feature("Summary")
def test_summary(checked, capsys):
    """Runs last (file order): counts, broken link -> pages, CSV. Never fails."""
    pages = checked["pages"]
    targets = checked["targets"]
    counts = Counter(bucket(pages[loc]) for loc in LOCS)
    target_counts = Counter("ok" if link_ok(t) else "broken" for t in targets.values())
    keys = ("ok", "broken-links", "page-non-200", "error")
    rows = [
        {
            "page": loc,
            "href": link["href"],
            "status": link["status"],
            "final_url": link["final"],
            "redirects": link["hops"],
        }
        for loc in LOCS
        for link in pages[loc]["broken"]
    ]
    summary = {
        "base_url": BASE_URL,
        "sitemap": SITEMAP_FILE,
        "path_prefix": PATH_PREFIX or "(all)",
        "paths_file": selection_label(),
        "exclude_substr": EXCLUDE_SUBSTR or "(none)",
        "pages": len(LOCS),
        **{k: counts.get(k, 0) for k in keys},
        "link_targets": len(targets),
        "broken_targets": target_counts.get("broken", 0),
        "broken_links": len(rows),
    }
    table = "| pages | count |\n|---|---|\n" + "\n".join(f"| {k} | {summary[k]} |" for k in keys) + f"\n\npages: {summary['pages']}, distinct link targets: {summary['link_targets']}, broken targets: {summary['broken_targets']}, broken links on pages: {summary['broken_links']}"
    allure.attach(
        f"{SITEMAP_FILE} ({len(LOCS)} pages) -> {BASE_URL}",
        name="sitemap-source",
        attachment_type=allure.attachment_type.TEXT,
    )
    by_link = broken_by_link_text(targets)
    allure.attach(table, name="links-summary.txt", attachment_type=allure.attachment_type.TEXT)
    allure.attach(json.dumps(summary, indent=2), name="links-summary", attachment_type=allure.attachment_type.JSON)
    allure.attach(by_link, name="broken-link-pages.txt", attachment_type=allure.attachment_type.TEXT)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["page", "href", "status", "final_url", "redirects"])
    w.writeheader()
    w.writerows(rows)
    allure.attach(buf.getvalue(), name="broken-links", attachment_type=allure.attachment_type.CSV)
    with capsys.disabled():  # shown even under -q, where passing tests' output is captured
        print(f"\n\n{by_link}\n\n{json.dumps(summary)}", flush=True)
