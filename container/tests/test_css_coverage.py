# version 0.1.0
"""CSS coverage check: every <aside> on a page must have matching CSS rules.

Catches pages rendering unstyled asides (e.g. full-width banners stacked
above content because the loaded stylesheet defines no aside rules).

One pytest case per sitemap URL, so the Allure dashboard goes green/red
per URL. A trailing `test_summary` attaches overall counts plus CSV.

Reads SITEMAP_FILE (default /data/sitemap.xml), rewrites each URL's host
to BASE_URL (default http://php-apache).

Env (same selection knobs as the other modules, plus):
  BASE_URL, SITEMAP_FILE, PATH_PREFIX, EXCLUDE_SUBSTR, FAIL_ON_NON_200
  FAIL_ON_UNSTYLED    "true" (default) -> asides without CSS rules fail;
                      "false" -> report-only.
"""

import csv
import io
import json
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import allure
import pytest
import requests
from url_selection import in_selection, selection_label, skip_if_nothing_selected

BASE_URL = os.environ.get("BASE_URL", "http://php-apache").rstrip("/")
SITEMAP_FILE = os.environ.get("SITEMAP_FILE", "/data/sitemap.xml")
FAIL_ON_NON_200 = os.environ.get("FAIL_ON_NON_200", "true").lower() == "true"
FAIL_ON_UNSTYLED = os.environ.get("FAIL_ON_UNSTYLED", "true").lower() == "true"
TIMEOUT = (5, 15)  # (connect, read) seconds

ASIDE_TAG = os.environ.get("SITE_ASIDE_TAG", "aside")
ASIDE_RE = re.compile(rf"<{ASIDE_TAG}\b([^>]*)>", re.I)
ID_RE = re.compile(r'\bid\s*=\s*["\']([^"\']+)["\']', re.I)
CLASS_RE = re.compile(r'\bclass\s*=\s*["\']([^"\']+)["\']', re.I)
CSS_LINK_RE = re.compile(r'<link\b[^>]*\brel\s*=\s*["\']stylesheet["\'][^>]*>', re.I)
HREF_RE = re.compile(r'\bhref\s*=\s*["\']([^"\']+)["\']', re.I)


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
    if not locs:
        locs = [e.text.strip() for e in tree.getroot().iter("loc") if e.text]
    assert locs, f"no <loc> entries found in {path}"
    return locs


def _matches_prefix(loc, prefix):
    if not prefix:
        return True
    path = urlparse(loc).path or "/"
    return path == prefix or path.startswith(prefix + "/")


if not Path(SITEMAP_FILE).is_file():
    pytest.skip(f"no sitemap at {SITEMAP_FILE}: generate it first", allow_module_level=True)

LOCS = [u for u in sitemap_locs(SITEMAP_FILE) if _matches_prefix(u, PATH_PREFIX) and not _is_excluded(u) and in_selection(u)]
skip_if_nothing_selected(LOCS)
assert LOCS, f"no urls matching PATH_PREFIX={PATH_PREFIX!r} with EXCLUDE_SUBSTR={EXCLUDE_SUBSTR!r} in {SITEMAP_FILE}"


def to_local(url):
    """Swap sitemap host for the checked host, keep path/query, encode path."""
    p = urlparse(url)
    local_path = quote(p.path or "/", safe="/%:@")
    return urlunparse(urlparse(BASE_URL)._replace(path=local_path, query=p.query or ""))


def bucket(result):
    if isinstance(result.get("error"), str):
        return "error"
    if result.get("status") != 200:
        return "non-200"
    if result.get("unstyled"):
        return "unstyled-aside"
    return "styled"


def fetch(session, url, max_hops=5):
    """GET with manual redirect following pinned to BASE_URL.

    Same as the content module: absolute Location headers are rewritten
    back to BASE_URL (no TLS locally). Returns (status_or_error,
    final_url, hops, text)."""
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
        return 404
    return status


def strip_css_comments(css):
    """Remove /* ... */ so selectors inside comments don't count."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def stylesheet_urls(body, page_url):
    """Absolute stylesheet URLs linked from the page, in order."""
    urls = []
    for tag in CSS_LINK_RE.findall(body):
        href = HREF_RE.search(tag)
        if href and href.group(1) not in urls:
            urls.append(urljoin(page_url, href.group(1)))
    return urls


def selector_defined(selector, css):
    """True when the selector occurs as a real selector (not a substring)."""
    return re.search(re.escape(selector) + r"(?![\w-])", css) is not None


@pytest.fixture(scope="session")
def checked():
    """Fetch every URL + its stylesheets once; tests assert on the dict."""
    session = requests.Session()
    session.headers.update({"X-Forwarded-Proto": "https"})
    css_cache = {}
    results = {}
    for loc in LOCS:
        local = to_local(loc)
        status, final, hops, text = fetch(session, local)
        status = final_status(local, status, final)
        if not isinstance(status, int):
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "unstyled": [],
                "error": status,
            }
            continue
        if status != 200:
            results[loc] = {
                "status": status,
                "final": final,
                "hops": hops,
                "unstyled": [],
                "error": None,
            }
            continue
        missing = []
        css_texts = []
        for css_url in stylesheet_urls(text, final):
            if css_url not in css_cache:
                try:
                    resp = session.get(css_url, timeout=TIMEOUT)
                    css_cache[css_url] = strip_css_comments(resp.text) if resp.status_code == 200 else ""
                except requests.RequestException:
                    css_cache[css_url] = ""
            css_texts.append(css_cache[css_url])
        combined = "\n".join(css_texts)
        if not combined:
            missing.append("no stylesheets loaded")
        else:
            for tag in ASIDE_RE.findall(text):
                selectors = set()
                for ident in ID_RE.findall(tag):
                    selectors.add("#" + ident.split()[0])
                for classes in CLASS_RE.findall(tag):
                    for cls in classes.split():
                        selectors.add("." + cls)
                if selectors and not any(selector_defined(selector, css) for selector in selectors for css in css_texts if css):
                    missing.append(" ".join(sorted(selectors)))
        results[loc] = {
            "status": status,
            "final": final,
            "hops": hops,
            "unstyled": missing if combined else ["no stylesheets loaded"],
            "error": None,
        }
    return results


def _case_id(loc):
    return urlparse(loc).path or "/"


@allure.epic("CSS coverage")
@allure.feature("Asides have matching CSS rules")
@pytest.mark.parametrize("loc", LOCS, ids=[f"{i:04d} {_case_id(u)}" for i, u in enumerate(LOCS)])
def test_asides_styled(checked, loc):
    local = to_local(loc)
    with allure.step(f"GET {local}"):
        res = checked[loc]
        allure.attach(
            f"{local} -> {res['final']} [{res['status']}] ({res['hops']} redirects, unstyled={res['unstyled']})",
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
        if res["unstyled"] == ["no stylesheets loaded"]:
            # API backends, stubs and fixtures carry no CSS by design;
            # there is nothing to check selectors against.
            pytest.skip("no stylesheets on page")
        if FAIL_ON_UNSTYLED:
            assert not res["unstyled"], f"{loc} -> {res['final']}: unstyled aside selectors: {', '.join(res['unstyled'])}"


@allure.epic("CSS coverage")
@allure.feature("Summary")
def test_summary(checked):
    """Runs last (file order): overall counts + full CSV. Never fails."""
    rows = [
        {
            "sitemap_url": loc,
            "checked_url": checked[loc]["final"],
            "status": checked[loc]["status"],
            "unstyled": "; ".join(checked[loc]["unstyled"]),
            "redirects": checked[loc]["hops"],
        }
        for loc in LOCS
    ]
    counts = Counter(bucket(checked[loc]) for loc in LOCS)
    keys = ("styled", "unstyled-aside", "non-200", "error")
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
    allure.attach(table, name="css-summary.txt", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        json.dumps(summary, indent=2),
        name="css-summary",
        attachment_type=allure.attachment_type.JSON,
    )
    buf = io.StringIO()
    fields = ["sitemap_url", "checked_url", "status", "unstyled", "redirects"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    allure.attach(buf.getvalue(), name="all-urls", attachment_type=allure.attachment_type.CSV)
    print(f"\n{json.dumps(summary)}")
