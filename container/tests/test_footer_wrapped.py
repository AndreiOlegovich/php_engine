# version 0.1.4
"""Footer-wrapped check: every sitemap page that renders the contacts
footer table must wrap it in a width-constraining div.

The engines no longer render that table: since 2026-11-02 their
siteFooter() returns the full-width <footer class="sitefooter"> of
src/.php/SiteFooter.php, which has no tab11 table, so engine pages are
skipped here. The legacy RussianFooter.php includes still render the
contacts/cooperation footer as
`<table class="tab11">` with a contacts caption. That table must sit
inside `<div class="wrapper ...">` (or the intentional full-width
`<div class="w100 ...">` of section-index BasePage shells). A bare
`<div class="mt10">`/`<div class="mt2">` lets the footer stretch
full-width (see commit 30118c6d "Fixes unwrapped footer parts").

Only tab11 tables with a contacts caption ("Контакты..."/"Contacts...")
are checked: content pages reuse tab11 for their own tables (hotkeys,
word lists, ...) and those are out of scope.

Pages without a contacts footer table (API endpoints, app pages, shells
without the contacts block) have no footer to check: they are skipped
(report-only), never failed.

One pytest case per sitemap URL, so the Allure dashboard goes green/red
per URL. A trailing `test_summary` attaches overall wrapped/unwrapped/
no-footer counts plus the full CSV.

Reads SITEMAP_FILE (default /data/sitemap.xml, i.e. ./src/sitemap.xml),
rewrites each URL's host to BASE_URL (default http://php-apache).

Env:
  BASE_URL               e.g. http://php-apache (compose) or http://127.0.0.1:8080
  SITEMAP_FILE           e.g. /data/sitemap.xml
  PATH_PREFIX            e.g. /ru/i/android -> only check sitemap URLs whose path
                         is exactly the prefix or starts with prefix + "/".
                         Empty (default) -> check all URLs.
  EXCLUDE_SUBSTR         comma-separated substrings; a sitemap URL whose path
                         contains any of them is skipped.
                         Empty (default) -> exclude nothing.
  FAIL_ON_NON_200        "true" (default) -> non-200 URL cases fail;
                         "false" -> report-only for HTTP status.
  FAIL_ON_UNWRAPPED_FOOTER
                         "true" (default) -> pages with an unwrapped contacts
                         footer fail; "false" -> report-only for the wrap check.
"""

import csv
import io
import json
import os
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
FAIL_ON_UNWRAPPED = os.environ.get("FAIL_ON_UNWRAPPED_FOOTER", "true").lower() == "true"
TIMEOUT = (5, 15)  # (connect, read) seconds

#: div classes that count as "wrapped": constrained article column,
#: or the intentional full-width section-index shell.
WRAPPED_CLASSES = frozenset({"wrapper", "w100"})


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


if not os.path.isfile(SITEMAP_FILE):
    pytest.skip(f"no sitemap at {SITEMAP_FILE}: generate it first", allow_module_level=True)

LOCS = [u for u in sitemap_locs(SITEMAP_FILE) if _matches_prefix(u, PATH_PREFIX) and not _is_excluded(u) and in_selection(u)]
skip_if_nothing_selected(LOCS)
assert LOCS, f"no urls matching PATH_PREFIX={PATH_PREFIX!r} with EXCLUDE_SUBSTR={EXCLUDE_SUBSTR!r} in {SITEMAP_FILE}"


def to_local(url):
    """Swap sitemap host for the checked host, keep path/query, encode path."""
    p = urlparse(url)
    local_path = quote(p.path or "/", safe="/%:@")
    return urlunparse(urlparse(BASE_URL)._replace(path=local_path, query=p.query or ""))


class _ContactsFooterParser(HTMLParser):
    """Collect tab11 tables: enclosing div classes + caption text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.div_stack = []  # innermost last; each entry is a set of classes
        self.tables = []  # dicts: {"div_classes": set|None, "caption": str}
        self._open_table = None
        self._in_caption = False

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            classes = set()
            for key, value in attrs:
                if key == "class" and value:
                    classes.update(value.split())
            self.div_stack.append(classes)
        elif tag == "table":
            for key, value in attrs:
                if key == "class" and value and "tab11" in value.split():
                    self._open_table = {
                        "div_classes": set(self.div_stack[-1]) if self.div_stack else None,
                        "caption": "",
                        "pos": self.getpos(),
                    }
                    break
        elif tag == "caption" and self._open_table is not None:
            self._in_caption = True

    def handle_endtag(self, tag):
        if tag == "div" and self.div_stack:
            self.div_stack.pop()
        elif tag == "caption":
            self._in_caption = False
        elif tag == "table" and self._open_table is not None:
            self.tables.append(self._open_table)
            self._open_table = None
            self._in_caption = False

    def handle_data(self, data):
        if self._in_caption and self._open_table is not None:
            self._open_table["caption"] += data


def _is_contacts(caption):
    low = (caption or "").lower()
    return "контакты" in low or "contacts" in low


def footer_report(html):
    """Split tab11 tables into contacts footers; judge wrapping.

    Returns {"contacts": [ {"wrapped": bool, "div_classes": set|None,
    "caption": str}, ... ]} — one entry per contacts-caption tab11 table.
    """
    parser = _ContactsFooterParser()
    parser.feed(html)
    contacts = []
    for table in parser.tables:
        if not _is_contacts(table["caption"]):
            continue
        div_classes = table["div_classes"]
        contacts.append(
            {
                "wrapped": div_classes is not None and not WRAPPED_CLASSES.isdisjoint(div_classes),
                "div_classes": sorted(div_classes) if div_classes is not None else None,
                "caption": " ".join(table["caption"].split()),
            }
        )
    return {"contacts": contacts}


def bucket(result):
    if isinstance(result.get("error"), str):
        return "error"
    if result.get("status") != 200:
        return "non-200"
    if not result.get("has_footer"):
        return "no-footer"
    if not result.get("wrapped"):
        return "unwrapped"
    return "wrapped"


def fetch(session, url, max_hops=5):
    """GET with manual redirect following pinned to BASE_URL.

    Some pages answer http -> https (absolute Location built from Host).
    There is no TLS locally, so every redirect is rewritten back to
    BASE_URL's scheme+host and re-requested over plain HTTP.
    Returns (status_or_error, final_url, hops, text)."""
    hops = 0
    while True:
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as exc:
            return f"{type(exc).__name__}: {exc}", url, hops, ""
        loc = r.headers.get("Location", "")
        if r.status_code in (301, 302, 303, 307, 308) and loc and hops < max_hops:
            p = urlparse(urljoin(url, loc))
            b = urlparse(BASE_URL)
            url = urlunparse((b.scheme, b.netloc, quote(p.path or "/", safe="/%:@"), "", p.query or "", ""))
            hops += 1
            continue
        text = r.text if isinstance(r.text, str) else ""
        return r.status_code, r.url, hops, text


def final_status(local, status, final):
    if urlparse(final).path == "/404.php" and urlparse(local).path != "/404.php":
        # .htaccess ErrorDocument 404 -> external 404.php, which we rewrote
        # back to the local host: landing on /404.php means the URL is missing.
        return 404
    return status


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
        status, final, hops, text = fetch(session, local)
        status = final_status(local, status, final)
        if isinstance(status, int):
            if status == 200:
                report = footer_report(text)
                contacts = report["contacts"]
                results[loc] = {
                    "status": status,
                    "final": final,
                    "hops": hops,
                    "has_footer": bool(contacts),
                    "wrapped": bool(contacts) and all(c["wrapped"] for c in contacts),
                    "contacts": contacts,
                    "body_len": len(text),
                    "error": None,
                }
            else:
                results[loc] = {
                    "status": status,
                    "final": final,
                    "hops": hops,
                    "has_footer": False,
                    "wrapped": False,
                    "contacts": [],
                    "body_len": len(text),
                    "error": None,
                }
        else:
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "has_footer": False,
                "wrapped": False,
                "contacts": [],
                "body_len": 0,
                "error": status,
            }
    return results


def _case_id(loc):
    return urlparse(loc).path or "/"


@allure.epic("Footer check")
@allure.feature("Contacts footer is wrapped")
@pytest.mark.parametrize("loc", LOCS, ids=[f"{i:04d} {_case_id(u)}" for i, u in enumerate(LOCS)])
def test_page_footer_wrapped(checked, loc):
    local = to_local(loc)
    with allure.step(f"GET {local}"):
        res = checked[loc]
        allure.attach(
            f"{local} -> {res['final']} [{res['status']}] ({res['hops']} redirects, {res['body_len']} chars, has_footer={res['has_footer']}, wrapped={res['wrapped']})",
            name="response",
            attachment_type=allure.attachment_type.TEXT,
        )
        if res["error"] is not None:
            if FAIL_ON_NON_200:
                pytest.fail(f"{loc} -> {res['final']} [{res['error']}]")
            pytest.skip(f"request error (report-only): {res['error']}")
        if FAIL_ON_NON_200:
            assert res["status"] == 200, f"{loc} -> {res['final']} [{res['status']}]"
        if res["status"] != 200:
            pytest.skip(f"non-200 (report-only): {res['status']}")
        if not res["has_footer"]:
            pytest.skip("no contacts footer table (nothing to check)")
        if not res["wrapped"]:
            allure.attach(
                json.dumps(res["contacts"], indent=2, ensure_ascii=False),
                name="unwrapped-footers",
                attachment_type=allure.attachment_type.JSON,
            )
        if FAIL_ON_UNWRAPPED:
            assert res["wrapped"], f"{loc} -> {res['final']}: contacts footer table is not wrapped in div.wrapper/div.w100: {res['contacts']!r}"


@allure.epic("Footer check")
@allure.feature("Summary")
def test_summary(checked):
    """Runs last (file order): overall counts + full CSV. Never fails."""
    rows = [
        {
            "sitemap_url": loc,
            "checked_url": checked[loc]["final"],
            "status": checked[loc]["status"],
            "has_footer": checked[loc]["has_footer"],
            "wrapped": checked[loc]["wrapped"],
            "redirects": checked[loc]["hops"],
        }
        for loc in LOCS
    ]
    counts = Counter(bucket(checked[loc]) for loc in LOCS)
    keys = ("wrapped", "unwrapped", "no-footer", "non-200", "error")
    summary = {
        "base_url": BASE_URL,
        "sitemap": SITEMAP_FILE,
        "path_prefix": PATH_PREFIX or "(all)",
        "paths_file": selection_label(),
        "exclude_substr": EXCLUDE_SUBSTR or "(none)",
        "total": len(rows),
        **{k: counts.get(k, 0) for k in keys},
    }
    table = "| result | count |\n|---|---|\n" + "\n".join(f"| {k} | {summary[k]} |" for k in keys) + f"\n\ntotal: {summary['total']}"
    allure.attach(
        f"{SITEMAP_FILE} ({len(rows)} urls) -> {BASE_URL}",
        name="sitemap-source",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(table, name="footer-summary.txt", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        json.dumps(summary, indent=2),
        name="footer-summary",
        attachment_type=allure.attachment_type.JSON,
    )
    buf = io.StringIO()
    fields = ["sitemap_url", "checked_url", "status", "has_footer", "wrapped", "redirects"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    allure.attach(buf.getvalue(), name="all-urls", attachment_type=allure.attachment_type.CSV)
    print(f"\n{json.dumps(summary)}")
