# version 0.2.1
"""Content-loaded check: every <loc> in the sitemap must render the marker.

Default marker is the bottom marker `<div id="footer-end"></div>`, rendered
by every footer path, e.g.:
  container/src/section/.php/Page.php            create_footer/close_html
  container/src/section/.php/BasePage.php        close_html
  container/src/extra/.php/Page.php               create_footer/close_html
  container/src/extra/.php/Page2.php              close_html
  container/src/*/.php/php_includes/_footer.php (section, ao, _footer_light)

Pass --tag to check something else instead: --tag address looks for the
top menu, either the engine bar `<nav class="crumbbar"` (Breadcrumbs.php)
or the hand-written `<nav class="address">` of the _*_top_path_inc.php
includes; --tag crumbbar looks for the engine bar only; any other value is
used literally as the searched body substring. The single assertion per
URL is marker presence — no other fallbacks.

One pytest case per sitemap URL, so the Allure dashboard goes green/red
per URL. A trailing `test_summary` attaches overall loaded/missing/other
counts plus the full CSV.

Reads SITEMAP_FILE (default /data/sitemap.xml, i.e. ./src/sitemap.xml),
rewrites each URL's host to BASE_URL (default http://php-apache).

Env:
  BASE_URL               e.g. http://php-apache (compose) or http://127.0.0.1:8080
  SITEMAP_FILE           e.g. /data/sitemap.xml
  PATH_PREFIX            e.g. /section -> only check sitemap URLs whose path is
                         exactly the prefix or starts with prefix + "/".
                         Empty (default) -> check all URLs.
  EXCLUDE_SUBSTR         comma-separated substrings; a sitemap URL whose path
                         contains any of them is skipped. For include-fragments
                         that are listed in the sitemap but are not pages,
                         e.g. "_menu.php,_top_path.php,left_block,right_block".
                         Empty (default) -> exclude nothing.
  BOTTOM_MARKER          default 'id="footer-end"'. Matches
                         `<div id="footer-end"></div>` in every footer variant.
                         Overridden by --tag (see tests/conftest.py).
  FAIL_ON_NON_200        "true" (default) -> non-200 URL cases fail;
                         "false" -> report-only for HTTP status.
  FAIL_ON_MISSING_BOTTOM "true" (default) -> pages without the bottom marker fail;
                         "false" -> report-only for content check.
"""

import csv
import io
import json
import os
from collections import Counter
from urllib.parse import quote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import allure
import pytest
import requests
from url_selection import in_selection, selection_label, skip_if_nothing_selected

BASE_URL = os.environ.get("BASE_URL", "http://php-apache").rstrip("/")
SITEMAP_FILE = os.environ.get("SITEMAP_FILE", "/data/sitemap.xml")
BOTTOM_MARKER = os.environ.get("BOTTOM_MARKER", 'id="footer-end"')
#: resolved per run in checked(); --tag overrides BOTTOM_MARKER. A tuple:
#: the page passes when its body holds any of the substrings.
MARKER = {"value": (BOTTOM_MARKER,)}
FAIL_ON_NON_200 = os.environ.get("FAIL_ON_NON_200", "true").lower() == "true"
FAIL_ON_MISSING_MARKER = os.environ.get("FAIL_ON_MISSING_BOTTOM", "true").lower() == "true"
TIMEOUT = (5, 15)  # (connect, read) seconds

#: --tag shorthands (anything else is used as a literal body substring).
TAG_MARKERS = {
    "footer-end": ('id="footer-end"',),
    "address": ('<nav class="crumbbar"', '<nav class="address">'),
    "crumbbar": ('<nav class="crumbbar"',),
}


def resolve_marker(tag):
    """Map a --tag value (or the BOTTOM_MARKER env) to the searched substrings."""
    raw = tag or BOTTOM_MARKER
    return TAG_MARKERS.get(raw, (raw,))


def marker_label():
    """The searched substrings as one string for messages and the summary."""
    return " or ".join(MARKER["value"])


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


def marker_of(request):
    """Resolve the searched substring from --tag (else BOTTOM_MARKER env)."""
    return resolve_marker(request.config.getoption("tag"))


def bucket(result):
    if isinstance(result.get("error"), str):
        return "error"
    if result.get("status") != 200:
        return "non-200"
    if not result.get("has_marker"):
        return "missing-marker"
    return "loaded"


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
def checked(request):
    """Fetch every URL once; per-URL tests below just assert on the dict."""
    MARKER["value"] = marker_of(request)
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
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "has_marker": any(marker in text for marker in MARKER["value"]),
                "body_len": len(text),
                "tail": text[-500:],
                "error": None,
            }
        else:
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "has_marker": False,
                "body_len": 0,
                "tail": "",
                "error": status,
            }
    return results


def _case_id(loc):
    return urlparse(loc).path or "/"


@allure.epic("Content check")
@allure.feature("Page renders marker")
@pytest.mark.parametrize("loc", LOCS, ids=[f"{i:04d} {_case_id(u)}" for i, u in enumerate(LOCS)])
def test_page_content_loaded(checked, loc):
    local = to_local(loc)
    with allure.step(f"GET {local}"):
        res = checked[loc]
        allure.attach(
            f"{local} -> {res['final']} [{res['status']}] ({res['hops']} redirects, {res['body_len']} chars, marker={res['has_marker']})",
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
        if not res["has_marker"]:
            allure.attach(res["tail"], name="page-tail", attachment_type=allure.attachment_type.TEXT)
        if FAIL_ON_MISSING_MARKER:
            assert res["has_marker"], f"{loc} -> {res['final']}: marker {marker_label()!r} not found ({res['body_len']} chars)"


@allure.epic("Content check")
@allure.feature("Summary")
def test_summary(checked):
    """Runs last (file order): overall counts + full CSV. Never fails."""
    rows = [
        {
            "sitemap_url": loc,
            "checked_url": checked[loc]["final"],
            "status": checked[loc]["status"],
            "marker": checked[loc]["has_marker"],
            "redirects": checked[loc]["hops"],
        }
        for loc in LOCS
    ]
    counts = Counter(bucket(checked[loc]) for loc in LOCS)
    keys = ("loaded", "missing-marker", "non-200", "error")
    summary = {
        "base_url": BASE_URL,
        "sitemap": SITEMAP_FILE,
        "path_prefix": PATH_PREFIX or "(all)",
        "paths_file": selection_label(),
        "exclude_substr": EXCLUDE_SUBSTR or "(none)",
        "marker": marker_label(),
        "total": len(rows),
        **{k: counts.get(k, 0) for k in keys},
    }
    table = "| result | count |\n|---|---|\n" + "\n".join(f"| {k} | {summary[k]} |" for k in keys) + f"\n\ntotal: {summary['total']}"
    allure.attach(
        f"{SITEMAP_FILE} ({len(rows)} urls) -> {BASE_URL}",
        name="sitemap-source",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(table, name="content-summary.txt", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        json.dumps(summary, indent=2),
        name="content-summary",
        attachment_type=allure.attachment_type.JSON,
    )
    buf = io.StringIO()
    fields = ["sitemap_url", "checked_url", "status", "marker", "redirects"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    allure.attach(buf.getvalue(), name="all-urls", attachment_type=allure.attachment_type.CSV)
    print(f"\n{json.dumps(summary)}")
