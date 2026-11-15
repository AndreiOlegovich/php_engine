# version 0.1.0
"""Wrapped-once check: no width-constraining wrapper sits inside another one.

The article engines already open the page column themselves:
ArticlePage::createHead() prints ``<main class="wrapper mt5">`` and
BasePage shells print ``<main class="wrapper-base-page ...">``. A page
that then opens its own ``<div class="wrapper mt5">`` puts the content
inside two width-constraining elements: the column gets narrower and the
margins are applied twice (this is what happened on /ide/vim/). Sibling
wrappers are fine - legacy pages legitimately use several of them side by
side - so only a wrapper inside another wrapper fails.

Wrapper classes are the ones that set the page width in the CSS sources
(``container/css_pipeline/source``, served sheets in ``src/.css``):

  ``wrapper``            the article column, margins/width set per breakpoint
  ``wrapper-base-page``  the BasePage column, width 64-90% plus margins
  ``wrapper-no-left``    width 68%, no side margins

``w100`` is deliberately not in the list: it is the intentional
full-width box, not a constrained column.

The site's HTML is not always balanced (pages leave ``<div>``s open and
the browser closes them at ``</main>`` or at the end of the document), so
the parser here is tolerant: an end tag closes the nearest matching open
element and everything above it, a stray end tag with no match is
ignored, and a start tag that is never closed stays open until the end of
the document, so the content after it counts as inside it. Nesting is
judged when a wrapper opens: if any wrapper is still open at that moment,
the new one is a violation.

One pytest case per sitemap URL, so the Allure dashboard goes green/red
per URL. A trailing ``test_summary`` attaches overall ok/nested/non-200/
error counts plus the full CSV.

Reads SITEMAP_FILE (default /data/sitemap.xml, i.e. ./src/sitemap.xml),
rewrites each URL's host to BASE_URL (default http://php-apache).

Run it alone::

    pytest tests/test_page_wrapped_once.py

Env:
  BASE_URL          e.g. http://php-apache (compose) or http://127.0.0.1:8080
  SITEMAP_FILE      e.g. /data/sitemap.xml
  PATH_PREFIX       e.g. /ide -> only check sitemap URLs whose path is
                    exactly the prefix or starts with prefix + "/".
                    Empty (default) -> check all URLs.
  EXCLUDE_SUBSTR    comma-separated substrings; a sitemap URL whose path
                    contains any of them is skipped. Empty -> exclude nothing.
  FAIL_ON_NON_200   "true" (default) -> non-200 URL cases fail;
                    "false" -> report-only for HTTP status.
  FAIL_ON_NESTED_WRAPPER
                    "true" (default) -> a wrapper inside a wrapper fails;
                    "false" -> report-only for the wrap check.
  RETRIES           extra attempts after a request that ends in
                    requests.ConnectionError or requests.Timeout (an HTTP
                    status is never retried); default 2.
  RETRY_WAIT        base pause in seconds between those attempts; default
                    5, so the pauses are 5 s and then 15 s.
"""

import csv
import io
import json
import os
import time
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import allure
import pytest
import requests
from url_selection import in_selection, selection_label, skip_if_nothing_selected

BASE_URL = os.environ.get("BASE_URL", "http://php-apache").rstrip("/")
SITEMAP_FILE = os.environ.get("SITEMAP_FILE", "/data/sitemap.xml")
FAIL_ON_NON_200 = os.environ.get("FAIL_ON_NON_200", "true").lower() == "true"
FAIL_ON_NESTED = os.environ.get("FAIL_ON_NESTED_WRAPPER", "true").lower() == "true"
TIMEOUT = (5, 15)  # (connect, read) seconds
RETRIES = max(0, int(os.environ.get("RETRIES", "2")))
RETRY_WAIT = float(os.environ.get("RETRY_WAIT", "5"))

#: Elements that never have children, so they never join the open stack.
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Classes that set the page width (see the module docstring).
WRAPPER_CLASSES = frozenset({"wrapper", "wrapper-base-page", "wrapper-no-left"})


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
    locs = [e.text.strip() for e in tree.getroot().findall("s:url/s:loc", ns)]
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


LOCS = [u for u in sitemap_locs(SITEMAP_FILE) if _matches_prefix(u, PATH_PREFIX) and not _is_excluded(u) and in_selection(u)]
skip_if_nothing_selected(LOCS)
assert LOCS, f"no urls matching PATH_PREFIX={PATH_PREFIX!r} with EXCLUDE_SUBSTR={EXCLUDE_SUBSTR!r} in {SITEMAP_FILE}"


def to_local(url):
    """Swap sitemap host for the checked host, keep path/query, encode path."""
    p = urlparse(url)
    local_path = quote(p.path or "/", safe="/%:@")
    return urlunparse(urlparse(BASE_URL)._replace(path=local_path, query=p.query or ""))


def _get_with_retries(session, url):
    """GET once, retrying only transient network failures.

    A response with any HTTP status is returned as is. requests.ConnectionError
    and requests.Timeout (a dropped connection, a connect or read timeout) are
    retried up to RETRIES times with pauses RETRY_WAIT * 1, RETRY_WAIT * 3,
    ... (5 s, then 15 s by default). Other request errors are not retried.

    Returns (response_or_exception, retries_made).
    """
    retries = 0
    while True:
        try:
            return session.get(url, timeout=TIMEOUT, allow_redirects=False), retries
        except (requests.ConnectionError, requests.Timeout) as exc:
            if retries >= RETRIES:
                return exc, retries
            time.sleep(RETRY_WAIT * (2 * retries + 1))
            retries += 1
        except requests.RequestException as exc:
            return exc, retries


def fetch(session, url, max_hops=5):
    """GET with manual redirect following pinned to BASE_URL.

    Some pages answer http -> https (absolute Location built from Host).
    There is no TLS locally, so every redirect is rewritten back to
    BASE_URL's scheme+host and re-requested over plain HTTP.
    Returns (status_or_error, final_url, hops, text, retries)."""
    hops = 0
    retries = 0
    while True:
        r, made = _get_with_retries(session, url)
        retries += made
        if isinstance(r, Exception):
            return f"{type(r).__name__}: {r}", url, hops, "", retries
        loc = r.headers.get("Location", "")
        if r.status_code in (301, 302, 303, 307, 308) and loc and hops < max_hops:
            p = urlparse(urljoin(url, loc))
            b = urlparse(BASE_URL)
            url = urlunparse((b.scheme, b.netloc, quote(p.path or "/", safe="/%:@"), "", p.query or "", ""))
            hops += 1
            continue
        text = r.text if isinstance(r.text, str) else ""
        return r.status_code, r.url, hops, text, retries


def final_status(local, status, final):
    if urlparse(final).path == "/404.php" and urlparse(local).path != "/404.php":
        # .htaccess ErrorDocument 404 -> external 404.php, which we rewrote
        # back to the local host: landing on /404.php means the URL is missing.
        return 404
    return status


class _WrapperParser(HTMLParser):
    """Find wrappers nested in wrappers, tolerating unbalanced HTML.

    See the module docstring for the recovery rules. The open stack holds
    every non-void start tag until its end tag (or a parent's end tag)
    arrives; ``</main>`` therefore drops a page's unclosed inner ``<div>``
    as well, exactly as a browser does.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.wrappers = 0
        self.violations = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            return
        classes = set()
        for key, value in attrs:
            if key == "class" and value:
                classes.update(value.split())
        element = {"tag": tag, "classes": classes, "pos": self.getpos()}
        if not WRAPPER_CLASSES.isdisjoint(classes):
            self.wrappers += 1
            ancestors = [e for e in self.stack if not WRAPPER_CLASSES.isdisjoint(e["classes"])]
            if ancestors:
                parent = ancestors[-1]
                self.violations.append(
                    {
                        "line": element["pos"][0],
                        "tag": tag,
                        "classes": sorted(classes & WRAPPER_CLASSES),
                        "inside_line": parent["pos"][0],
                        "inside_tag": parent["tag"],
                        "inside_classes": sorted(parent["classes"] & WRAPPER_CLASSES),
                    }
                )
        self.stack.append(element)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                # Close the match and anything left open inside it.
                del self.stack[i:]
                return
        # A stray end tag has nothing to close: ignore it.


def wrapper_report(html):
    """Judge one page: {"wrappers": int, "violations": [...]}.

    ``wrappers`` counts elements carrying a wrapper class (siblings
    included); ``violations`` lists every one that opened while another
    wrapper was still open.
    """
    parser = _WrapperParser()
    parser.feed(html)
    parser.close()
    return {"wrappers": parser.wrappers, "violations": parser.violations}


def bucket(result):
    if isinstance(result.get("error"), str):
        return "error"
    if result.get("status") != 200:
        return "non-200"
    if result.get("violations"):
        return "nested"
    return "ok"


@pytest.fixture(scope="session")
def checked():
    """Fetch every URL once; per-URL tests below just assert on the dict."""
    session = requests.Session()
    # Production sits behind a TLS proxy; .htaccess forces https unless this
    # header is present. Sending it emulates production over plain local http.
    session.headers.update({"X-Forwarded-Proto": "https"})
    results = {}
    for loc in LOCS:
        local = to_local(loc)
        status, final, hops, text, retries = fetch(session, local)
        status = final_status(local, status, final)
        if isinstance(status, int):
            report = wrapper_report(text) if status == 200 else {"wrappers": 0, "violations": []}
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "retries": retries,
                "wrappers": report["wrappers"],
                "violations": report["violations"],
                "body_len": len(text),
                "error": None,
            }
        else:
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "retries": retries,
                "wrappers": 0,
                "violations": [],
                "body_len": 0,
                "error": status,
            }
    return results


def _case_id(loc):
    return urlparse(loc).path or "/"


@allure.epic("Wrapper check")
@allure.feature("Page is wrapped once")
@pytest.mark.parametrize("loc", LOCS, ids=[f"{i:04d} {_case_id(u)}" for i, u in enumerate(LOCS)])
def test_page_wrapped_once(checked, loc):
    local = to_local(loc)
    with allure.step(f"GET {local}"):
        res = checked[loc]
        allure.attach(
            f"{local} -> {res['final']} [{res['status']}] ({res['hops']} redirects, {res['retries']} retries, {res['body_len']} chars, wrappers={res['wrappers']}, nested={len(res['violations'])})",
            name="response",
            attachment_type=allure.attachment_type.TEXT,
        )
        if res["error"] is not None:
            if FAIL_ON_NON_200:
                pytest.fail(f"{loc} -> {res['final']} [{res['error']}] after {res['retries']} retries")
            pytest.skip(f"request error (report-only): {res['error']}")
        if FAIL_ON_NON_200:
            assert res["status"] == 200, f"{loc} -> {res['final']} [{res['status']}]"
        if res["status"] != 200:
            pytest.skip(f"non-200 (report-only): {res['status']}")
        if res["violations"]:
            allure.attach(
                json.dumps(res["violations"], indent=2, ensure_ascii=False),
                name="nested-wrappers",
                attachment_type=allure.attachment_type.JSON,
            )
        if FAIL_ON_NESTED:
            assert not res["violations"], f"{loc} -> {res['final']}: wrapper inside another wrapper ({res['wrappers']} wrappers on the page): {res['violations']!r}"


@allure.epic("Wrapper check")
@allure.feature("Summary")
def test_summary(checked):
    """Runs last (file order): overall counts + full CSV. Never fails."""
    rows = [
        {
            "sitemap_url": loc,
            "checked_url": checked[loc]["final"],
            "status": checked[loc]["status"],
            "wrappers": checked[loc]["wrappers"],
            "nested": len(checked[loc]["violations"]),
            "retries": checked[loc]["retries"],
            "redirects": checked[loc]["hops"],
        }
        for loc in LOCS
    ]
    counts = Counter(bucket(checked[loc]) for loc in LOCS)
    keys = ("ok", "nested", "non-200", "error")
    retries_total = sum(row["retries"] for row in rows)
    summary = {
        "base_url": BASE_URL,
        "sitemap": SITEMAP_FILE,
        "path_prefix": PATH_PREFIX or "(all)",
        "paths_file": selection_label(),
        "exclude_substr": EXCLUDE_SUBSTR or "(none)",
        "total": len(rows),
        **{k: counts.get(k, 0) for k in keys},
        "retried_urls": sum(1 for row in rows if row["retries"]),
        "retries": retries_total,
    }
    table = "| result | count |\n|---|---|\n" + "\n".join(f"| {k} | {summary[k]} |" for k in keys) + f"\n| retried_urls | {summary['retried_urls']} |\n| retries | {summary['retries']} |\n\ntotal: {summary['total']}"
    allure.attach(
        f"{SITEMAP_FILE} ({len(rows)} urls) -> {BASE_URL}",
        name="sitemap-source",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(table, name="wrapper-summary.txt", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        json.dumps(summary, indent=2),
        name="wrapper-summary",
        attachment_type=allure.attachment_type.JSON,
    )
    buf = io.StringIO()
    fields = ["sitemap_url", "checked_url", "status", "wrappers", "nested", "retries", "redirects"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    allure.attach(buf.getvalue(), name="all-urls", attachment_type=allure.attachment_type.CSV)
    print(f"\n{json.dumps(summary)}")
