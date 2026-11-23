# version 0.5.2
"""Engine-marker check: every <loc> in the sitemap must render one of the
page-shell markers.

Each engine stamps its closing HTML with its own marker div (see
``closeHtml()`` in ``src/.php/{Page,ArticlePage,BasePage}.php``)::

    <div id="Page_html_id"></div>
    <div id="BasePage_html_id"></div>
    <div id="ArticlePage_html_id"></div>

A page rendering none of them is either on a legacy shell
(``_head_inc.php``/``_footer.php`` includes) or truncated by a fatal
before the footer — both are actionable.

One pytest case per sitemap URL, so the Allure dashboard goes green/red
per URL. A trailing ``test_summary`` attaches the overall
page/base/article/missing counts plus the full CSV.

Reads SITEMAP_FILE (default /data/sitemap.xml, i.e. ./src/sitemap.xml),
rewrites each URL's host to BASE_URL (default http://php-apache, the web
container on the compose network).

Run it alone::

    pytest tests/test_page_html_id.py

or combined (whole suite)::

    pytest tests/

Run with plain python, the file prints these commands and exits 1.

Env:
  BASE_URL         e.g. http://php-apache (compose) or http://127.0.0.1:8080 (host)
  SITEMAP_FILE     e.g. /data/sitemap.xml
  PATH_PREFIX      e.g. /section -> only check sitemap URLs whose path is
                   exactly the prefix or starts with prefix + "/".
                   Empty (default) -> check all URLs.
  EXCLUDE_SUBSTR   comma-separated substrings; a sitemap URL whose path
                   contains any of them is skipped (in addition to
                   IGNORED_DIRS below). Empty (default) -> exclude nothing
                   extra.
  FAIL_ON_NON_200  "true" (default) -> non-200 URL cases fail;
                   "false" -> report-only mode, URL cases always pass
  FAIL_ON_MISSING_MARKER
                   "true" (default) -> pages without any engine marker fail;
                   "false" -> report-only for the marker check.
  RETRIES          extra attempts after a request that ends in
                   requests.ConnectionError or requests.Timeout (an HTTP
                   status is never retried); default 2.
  RETRY_WAIT       base pause in seconds between those attempts; default
                   5, so the pauses are 5 s and then 15 s.

Ignored dirs (always skipped, announced before each run):
  src/extra/services/  utility/API pages (cookie/post/put/rest/...) with no
                    page shell by design, so no engine marker is expected.
  src/extra/chessboard/  interactive app pages, no page shell.
  src/extra/api/epochtime/  bare-timestamp API endpoint (machine-consumable
                    number, no HTML shell by design).
"""

import csv
import io
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import allure
import pytest
import requests
import site_profile
from url_selection import in_selection, selection_label, skip_if_nothing_selected

#: Printed when the file is run with plain python instead of pytest. It has
#: to come before the sitemap read below, which fails on the host first.
HOW_TO_RUN = """\
{name} is a pytest file: run it with pytest, not with python.

It reads the sitemap as it loads and makes one test per URL. Its defaults
are for the pytest_web container, where container/src is mounted at /data:
  SITEMAP_FILE=/data/sitemap.xml
  BASE_URL=http://php-apache   (the web container on the compose network)
Neither exists on the host.

From {container}:

  # in Docker (the default backend)
  ./tests/run_sitemap_tests.sh --test html-id

  # on the host, against php-apache on :8080
  # (sets BASE_URL=http://127.0.0.1:8080 SITEMAP_FILE=src/sitemap.xml)
  ./tools/run-tests-venv.sh -- tests/{name} -q

Only part of the site: --dir <prefix> for run_sitemap_tests.sh,
PATH_PREFIX=/<prefix> in front of run-tests-venv.sh.
"""

if __name__ == "__main__":
    _here = Path(__file__).resolve()
    sys.exit(HOW_TO_RUN.format(name=_here.name, container=_here.parent.parent))

BASE_URL = os.environ.get("BASE_URL", "http://php-apache").rstrip("/")
SITEMAP_FILE = os.environ.get("SITEMAP_FILE", "/data/sitemap.xml")
FAIL_ON_NON_200 = os.environ.get("FAIL_ON_NON_200", "true").lower() == "true"
FAIL_ON_MISSING_MARKER = os.environ.get("FAIL_ON_MISSING_MARKER", "true").lower() == "true"
TIMEOUT = (5, 15)  # (connect, read) seconds
RETRIES = max(0, int(os.environ.get("RETRIES", "2")))
RETRY_WAIT = float(os.environ.get("RETRY_WAIT", "5"))

#: The shell markers of this site (SITE_PAGE_MARKERS in site-profile.env).
MARKERS = tuple(site_profile.get_list("SITE_PAGE_MARKERS", "Page_html_id BasePage_html_id ArticlePage_html_id"))
MARKER_RE = re.compile(rf'<div id="({"|".join(map(re.escape, MARKERS))})"></div>')


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


#: URL-path substrings that are always skipped by this test, on top of
#: EXCLUDE_SUBSTR. src/extra/services/ holds utility/API pages (cookie, post,
#: put, rest, printer, ...) with no page shell by design, so no engine
#: marker is ever expected there.
IGNORED_DIRS = ("/extra/services/", "/extra/chessboard/", "/extra/api/epochtime/")


def _exclude_substrings(raw):
    return [s for s in ((p or "").strip() for p in (raw or "").split(",")) if s]


EXCLUDE_SUBSTR = _exclude_substrings(os.environ.get("EXCLUDE_SUBSTR", ""))

#: Full skip list for this run (announced in checked() before fetching).
SKIP_SUBSTRINGS = tuple(dict.fromkeys((*IGNORED_DIRS, *EXCLUDE_SUBSTR)))


def _is_excluded(loc):
    path = urlparse(loc).path or "/"
    return any(s in path for s in SKIP_SUBSTRINGS)


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

_PREFIXED_LOCS = [u for u in sitemap_locs(SITEMAP_FILE) if _matches_prefix(u, PATH_PREFIX) and in_selection(u)]
IGNORED_LOCS = [u for u in _PREFIXED_LOCS if _is_excluded(u)]
LOCS = [u for u in _PREFIXED_LOCS if not _is_excluded(u)]
skip_if_nothing_selected(LOCS)
assert LOCS, f"no urls matching PATH_PREFIX={PATH_PREFIX!r} with SKIP_SUBSTRINGS={SKIP_SUBSTRINGS!r} in {SITEMAP_FILE}"


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


def which_marker(text):
    """First engine marker found in the body, or None."""
    m = MARKER_RE.search(text)
    return m.group(1) if m else None


@pytest.fixture(scope="session")
def checked():
    """Fetch every URL once; per-URL tests below just assert on the dict."""
    # Explicit per-run notice: this dir is ignored by this test.
    print(f"\n[engine-marker] IGNORING src/extra/services/ ({len(IGNORED_LOCS)} sitemap URL(s) skipped, SKIP_SUBSTRINGS={list(SKIP_SUBSTRINGS)}); checking {len(LOCS)} URL(s).")
    for loc in IGNORED_LOCS:
        print(f"[engine-marker] ignored: {urlparse(loc).path or '/'}")
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
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "retries": retries,
                "marker": which_marker(text) if status == 200 else None,
                "body_len": len(text),
            }
        else:
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "retries": retries,
                "marker": None,
                "body_len": 0,
                "error": status,
            }
    return results


def _case_id(loc):
    return urlparse(loc).path or "/"


def _bucket(res):
    if res.get("error") is not None or res.get("status") != 200:
        return "non-200"
    marker = res.get("marker")
    if marker is None:
        return "missing-marker"
    return marker


@allure.epic("Engine check")
@allure.feature("Page renders engine marker")
@pytest.mark.parametrize("loc", LOCS, ids=[f"{i:04d} {_case_id(u)}" for i, u in enumerate(LOCS)])
def test_page_engine_marker(checked, loc):
    local = to_local(loc)
    with allure.step(f"GET {local}"):
        res = checked[loc]
        allure.attach(
            f"{local} -> {res['final']} [{res['status']}] ({res['hops']} redirects, {res['retries']} retries, {res['body_len']} chars, marker={res['marker']})",
            name="response",
            attachment_type=allure.attachment_type.TEXT,
        )
        if res.get("error") is not None:
            if FAIL_ON_NON_200:
                pytest.fail(f"{loc} -> {res['final']} [{res['error']}] after {res['retries']} retries")
            pytest.skip(f"request error (report-only): {res['error']}")
        if FAIL_ON_NON_200:
            assert res["status"] == 200, f"{loc} -> {res['final']} [{res['status']}]"
        if res["status"] != 200:
            pytest.skip(f"non-200 (report-only): {res['status']}")
        if res["marker"] is None and FAIL_ON_MISSING_MARKER:
            pytest.fail(f"{loc} -> {res['final']}: none of {MARKERS} found ({res['body_len']} chars)")


@allure.epic("Engine check")
@allure.feature("Summary")
def test_summary(checked):
    """Runs last (file order): overall counts + full CSV. Never fails."""
    rows = [
        {
            "sitemap_url": loc,
            "checked_url": checked[loc]["final"],
            "status": checked[loc]["status"],
            "marker": checked[loc]["marker"] or "",
            "retries": checked[loc]["retries"],
            "redirects": checked[loc]["hops"],
        }
        for loc in LOCS
    ]
    counts = Counter(_bucket(checked[loc]) for loc in LOCS)
    keys = (*MARKERS, "missing-marker", "non-200")
    summary = {
        "base_url": BASE_URL,
        "sitemap": SITEMAP_FILE,
        "path_prefix": PATH_PREFIX or "(all)",
        "paths_file": selection_label(),
        "ignored_dirs": list(IGNORED_DIRS),
        "exclude_substr": EXCLUDE_SUBSTR or "(none)",
        "ignored_urls": len(IGNORED_LOCS),
        "total": len(rows),
        **{k: counts.get(k, 0) for k in keys},
        "retried_urls": sum(1 for row in rows if row["retries"]),
        "retries": sum(row["retries"] for row in rows),
    }
    table = "| result | count |\n|---|---|\n" + "\n".join(f"| {k} | {summary[k]} |" for k in keys) + f"\n| retried_urls | {summary['retried_urls']} |\n| retries | {summary['retries']} |\n\ntotal: {summary['total']}"
    allure.attach(
        f"{SITEMAP_FILE} ({len(rows)} urls) -> {BASE_URL}",
        name="sitemap-source",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(table, name="engine-summary.txt", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        json.dumps(summary, indent=2),
        name="engine-summary",
        attachment_type=allure.attachment_type.JSON,
    )
    buf = io.StringIO()
    fields = ["sitemap_url", "checked_url", "status", "marker", "retries", "redirects"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    allure.attach(buf.getvalue(), name="all-urls", attachment_type=allure.attachment_type.CSV)
    print(f"\n{json.dumps(summary)}")
