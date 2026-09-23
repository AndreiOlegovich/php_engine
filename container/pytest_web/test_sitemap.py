"""Link checker: every <loc> in the sitemap must respond with HTTP 200.

One pytest case per sitemap URL, so the Allure dashboard goes green/red
per URL (Suites tab, Graphs pie). A trailing `test_summary` attaches the
overall 200/404/other/error table plus the full CSV.

Reads SITEMAP_FILE (default /data/sitemap.xml, i.e. ./src/sitemap.xml),
rewrites each URL's host to BASE_URL (default http://php-apache, the web
container on the compose network).

Env:
  BASE_URL         e.g. http://php-apache (compose) or http://127.0.0.1:8080 (host)
  SITEMAP_FILE     e.g. /data/sitemap.xml or /data/sitemap_new.xml
  FAIL_ON_NON_200  "true" (default) -> non-200 URL cases fail;
                   "false" -> report-only mode, URL cases always pass
  PATH_PREFIX      e.g. /ru/qa -> only check sitemap URLs whose path is
                   exactly the prefix or starts with prefix + "/".
                   Empty (default) -> check all URLs.
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

BASE_URL = os.environ.get("BASE_URL", "http://php-apache").rstrip("/")
SITEMAP_FILE = os.environ.get("SITEMAP_FILE", "/data/sitemap.xml")
FAIL_ON_NON_200 = os.environ.get("FAIL_ON_NON_200", "true").lower() == "true"
TIMEOUT = (5, 15)  # (connect, read) seconds


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


LOCS = [u for u in sitemap_locs(SITEMAP_FILE) if _matches_prefix(u, PATH_PREFIX)]
assert LOCS, f"no urls matching PATH_PREFIX={PATH_PREFIX!r} in {SITEMAP_FILE}"


def to_local(url):
    """Swap sitemap host for the checked host, keep path/query, encode path."""
    p = urlparse(url)
    local_path = quote(p.path or "/", safe="/%:@")
    return urlunparse(urlparse(BASE_URL)._replace(path=local_path, query=p.query or ""))


def bucket(status):
    if status == 200:
        return "200"
    if status == 404:
        return "404"
    if isinstance(status, int):
        return "other"
    return "error"  # connection/timeout exceptions


def fetch(session, url, max_hops=5):
    """GET with manual redirect following pinned to BASE_URL.

    Some pages answer http -> https (absolute Location built from Host).
    There is no TLS locally, so every redirect is rewritten back to
    BASE_URL's scheme+host and re-requested over plain HTTP.
    Returns (status, final_url, hops)."""
    hops = 0
    while True:
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as exc:
            return f"{type(exc).__name__}: {exc}", url, hops
        loc = r.headers.get("Location", "")
        if r.status_code in (301, 302, 303, 307, 308) and loc and hops < max_hops:
            p = urlparse(urljoin(url, loc))
            b = urlparse(BASE_URL)
            url = urlunparse(
                (b.scheme, b.netloc, quote(p.path or "/", safe="/%:@"), "", p.query or "", "")
            )
            hops += 1
            continue
        return r.status_code, r.url, hops


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
        status, final, hops = fetch(session, local)
        results[loc] = (final_status(local, status, final), final, hops)
    return results


def _case_id(loc):
    return urlparse(loc).path or "/"


@allure.epic("Link check")
@allure.feature("Sitemap URL returns 200")
@pytest.mark.parametrize("loc", LOCS, ids=[f"{i:04d} {_case_id(u)}" for i, u in enumerate(LOCS)])
def test_url_returns_200_ok(checked, loc):
    local = to_local(loc)
    with allure.step(f"GET {local}"):
        status, final, hops = checked[loc]
        allure.attach(
            f"{local} -> {final} [{status}] ({hops} redirects)",
            name="response",
            attachment_type=allure.attachment_type.TEXT,
        )
        if FAIL_ON_NON_200:
            assert status == 200, f"{loc} -> {final} [{status}]"


@allure.epic("Link check")
@allure.feature("Summary")
def test_summary(checked):
    """Runs last (file order): overall counts + full CSV. Never fails."""
    rows = [
        {
            "sitemap_url": loc,
            "checked_url": checked[loc][1],
            "status": checked[loc][0],
            "redirects": checked[loc][2],
        }
        for loc in LOCS
    ]
    counts = Counter(bucket(r["status"]) for r in rows)
    summary = {
        "base_url": BASE_URL,
        "sitemap": SITEMAP_FILE,
        "path_prefix": PATH_PREFIX or "(all)",
        "total": len(rows),
        **{k: counts.get(k, 0) for k in ("200", "404", "other", "error")},
    }
    table = (
        "| status | count |\n|---|---|\n"
        + "\n".join(f"| {k} | {summary[k]} |" for k in ("200", "404", "other", "error"))
        + f"\n\ntotal: {summary['total']}"
    )
    allure.attach(
        f"{SITEMAP_FILE} ({len(rows)} urls) -> {BASE_URL}",
        name="sitemap-source",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(table, name="status-summary.txt", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        json.dumps(summary, indent=2),
        name="status-summary",
        attachment_type=allure.attachment_type.JSON,
    )
    buf = io.StringIO()
    fields = ["sitemap_url", "checked_url", "status", "redirects"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    allure.attach(buf.getvalue(), name="all-urls", attachment_type=allure.attachment_type.CSV)
    print(f"\n{json.dumps(summary)}")
