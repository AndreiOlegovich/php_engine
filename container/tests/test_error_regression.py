# version 0.4.0
"""Regression check for pages that fataled on prod/dev (error-log driven).

Every path below produced a PHP fatal in prod (example.com) or dev
(dev.example.com) error logs (Sep-Oct 2026 generation; see
container/tools/analyze_logs/). The test re-requests each page and fails
when the failure signature is still present:

  * non-200 status (configurable via FAIL_ON_NON_200)
  * PHP fatal text in the body: "PHP Fatal error", "Uncaught Error/",
    "Uncaught Exception"/"Uncaught ValueError", "Failed opening required",
    "Undefined constant", "Cannot redeclare", "Call to undefined function"

Redirects covered by .htaccess (Redirect/RedirectMatch) are NOT failures:
a listed path whose first response is a redirect matching an .htaccess
rule only emits a warning (pytest warnings summary + Allure attachment)
suggesting its removal from PATHS below, and counts as
"redirect-htaccess" in the summary. Rationale: retired trees (e.g.
/extra/old-tree/* -> https://other.example.com/moved/) redirect by design
and can never return 200, locally or on prod — and the harness rewrites
every redirect target back to BASE_URL, so external targets 404 locally.

It runs against any host, so the same file verifies the fix on prod
and the absence of regressions locally::

    pytest tests/test_error_regression.py                      # local container (BASE_URL default)
    BASE_URL=https://example.com pytest tests/test_error_regression.py
    PATH_PREFIX=/extra/edu pytest tests/test_error_regression.py  # focused subset

or via the runner (add `--test regression` mapping as needed).

One pytest case per path; a trailing ``test_summary`` attaches
fixed/still-broken counts plus the full CSV.

Env:
  BASE_URL         e.g. http://php-apache (compose), http://127.0.0.1:8080
                   (host) or https://example.com (prod web check)
  PATH_PREFIX      e.g. /extra/edu -> only check paths equal to the prefix or
                   starting with prefix + "/". Empty (default) -> all paths.
  FAIL_ON_NON_200  "true" (default) -> non-200 URL cases fail;
                   "false" -> report-only mode, URL cases always pass
  FAIL_ON_PHP_ERROR
                   "true" (default) -> bodies containing a fatal signature fail;
                   "false" -> report-only for the fatal check.
"""

import csv
import io
import json
import os
import re
import shlex
import warnings
from collections import Counter
from urllib.parse import quote, urljoin, urlparse, urlunparse

import allure
import pytest
import requests
from url_selection import in_selection, selection_label, skip_if_nothing_selected

BASE_URL = os.environ.get("BASE_URL", "http://php-apache").rstrip("/")
FAIL_ON_NON_200 = os.environ.get("FAIL_ON_NON_200", "true").lower() == "true"
FAIL_ON_PHP_ERROR = os.environ.get("FAIL_ON_PHP_ERROR", "true").lower() == "true"
TIMEOUT = (5, 15)  # (connect, read) seconds

#: PHP failure signatures searched in response bodies.
FATAL_RES = tuple(
    re.compile(p)
    for p in (
        r"PHP Fatal error",
        r"Uncaught Error",
        r"Uncaught Exception",
        r"Uncaught ValueError",
        r"Failed opening required",
        r"Undefined constant",
        r"Cannot redeclare",
        r"Call to undefined function",
    )
)


def find_fatal(text):
    """First fatal signature found in the body, or None."""
    for rx in FATAL_RES:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


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


#: .htaccess lookup: explicit env override first, then the compose mount
#: (/data/.htaccess, ./src mounted at /data), then host layouts (cwd or
#: this file's tree). Missing everywhere -> coverage check disabled.
def _find_htaccess():
    candidates = []
    override = os.environ.get("HTACCESS_FILE")
    if override:
        candidates.append(override)
    candidates.append("/data/.htaccess")
    candidates.append(os.path.join(os.getcwd(), "src", ".htaccess"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", ".htaccess"))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


HTACCESS_FILE = _find_htaccess()


def _parse_htaccess_redirects(path):
    """Parse Redirect/RedirectMatch rules from an .htaccess file.

    Returns [(kind, pattern, target, raw)] in file order. Since 2026-10-22
    redirects are mod_rewrite pairs (RewriteCond %{REQUEST_URI} <regex>,
    then RewriteRule ^ <target> [R=3xx]); they are read as RedirectMatch.
    Other RewriteRule redirects stay out of scope (e.g. the http->https
    forcing rule, which the harness avoids via the X-Forwarded-Proto
    header). Unparseable lines are skipped; missing file -> [] (check
    disabled).
    """
    rules = []
    if not path:
        return rules
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return rules
    pending = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            # posix=False keeps regex backslashes (\. must not become "any
            # character"); the quotes of a quoted argument go by hand.
            parts = [part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part for part in shlex.split(stripped, posix=False)]
        except ValueError:
            continue
        if not parts:
            continue
        directive = parts[0].lower()
        cond = directive == "rewritecond" and len(parts) >= 3 and parts[1] == "%{REQUEST_URI}"
        if directive == "rewriterule" and pending and len(parts) >= 4 and re.search(r"\bR=3\d\d\b", parts[3]):
            target = parts[2].replace("%{ENV:SITE_SCHEME}://%{HTTP_HOST}", "")
            rules.append(("redirectmatch", pending, re.sub(r"%(\d)", r"$\1", target), stripped))
        pending = parts[2] if cond else None
        if directive not in ("redirect", "redirectmatch"):
            continue
        args = parts[1:]
        # Optional status token: 3xx digits or permanent|temp|seeother|gone.
        if args and (args[0].isdigit() or args[0].lower() in ("permanent", "temp", "seeother", "gone")):
            args = args[1:]
        if len(args) < 2:
            continue
        src, target = args[0], args[1]
        if directive == "redirectmatch":
            try:
                re.compile(src)
            except re.error:
                continue
        rules.append((directive, src, target, stripped))
    return rules


HTACCESS_REDIRECTS = _parse_htaccess_redirects(HTACCESS_FILE)


def _location_matches_target(first_loc, target):
    """Does the observed redirect Location look like the rule's target?

    Guards against masking real breakage: a coincidentally matching rule
    must also explain where the request actually went.
    """
    t = urlparse(re.split(r"\$\d", target)[0])
    loc = urlparse(first_loc)
    if t.netloc and loc.netloc and t.netloc.lower() != loc.netloc.lower():
        return False
    tpath, lpath = t.path or "/", loc.path or "/"
    return lpath == tpath or lpath.startswith(tpath.rstrip("/") + "/")


def find_htaccess_rule(path, first_loc):
    """First .htaccess rule covering a redirected `path`, or None.

    Redirect uses mod_alias prefix semantics (path == src or under it);
    RedirectMatch is a regex searched against the path. The observed
    Location must additionally match the rule target (see above).
    """
    if not first_loc:
        return None
    for kind, pattern, target, raw in HTACCESS_REDIRECTS:
        if kind == "redirect":
            if not (path == pattern or path.startswith(pattern)):
                continue
        elif not re.search(pattern, path):
            continue
        if _location_matches_target(first_loc, target):
            return raw
    return None


#: Paths that fataled on prod/dev (Sep-Oct 2026 error logs). Frozen
#: regression list: edit only to add newly discovered failures.
try:
    from error_regression_cases import PATHS  # noqa: E402
except ImportError:
    pytest.skip("tests/error_regression_cases.py is missing (copy the example)", allow_module_level=True)


def _matches_prefix(path, prefix):
    if not prefix:
        return True
    return path == prefix or path.startswith(prefix + "/")


LOCS = [p for p in PATHS if _matches_prefix(p, PATH_PREFIX) and in_selection(p)]
skip_if_nothing_selected(LOCS)
assert LOCS, f"no paths matching PATH_PREFIX={PATH_PREFIX!r}"


def to_local(path):
    """Join BASE_URL with the checked path, keep encoding safe."""
    base = urlparse(BASE_URL)
    return urlunparse(base._replace(path=quote(path, safe="/%:@")))


def fetch(session, url, max_hops=5):
    """GET with manual redirect following pinned to BASE_URL (same as the
    sibling sitemap tests: some pages answer http -> https, which has no
    TLS locally, so redirects are rewritten back to BASE_URL).

    Returns (status_or_error, final_url, hops, text, first_redirect) where
    first_redirect is the (status, raw Location) of the initial response
    when it was a redirect, else None."""
    hops = 0
    first_redirect = None
    while True:
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as exc:
            return f"{type(exc).__name__}: {exc}", url, hops, "", None
        loc = r.headers.get("Location", "")
        if r.status_code in (301, 302, 303, 307, 308) and loc and hops < max_hops:
            if hops == 0:
                first_redirect = (r.status_code, urljoin(url, loc))
            p = urlparse(urljoin(url, loc))
            b = urlparse(BASE_URL)
            url = urlunparse((b.scheme, b.netloc, quote(p.path or "/", safe="/%:@"), "", p.query or "", ""))
            hops += 1
            continue
        text = r.text if isinstance(r.text, str) else ""
        return r.status_code, r.url, hops, text, first_redirect


def final_status(local, status, final):
    if urlparse(final).path == "/404.php" and urlparse(local).path != "/404.php":
        # .htaccess ErrorDocument 404 -> external 404.php, which we rewrote
        # back to the local host: landing on /404.php means the URL is missing.
        return 404
    return status


@pytest.fixture(scope="session")
def checked():
    """Fetch every path once; per-URL tests below just assert on the dict."""
    session = requests.Session()
    # Production sits behind a TLS proxy; .htaccess forces https unless this
    # header is present. Sending it emulates production over plain local http.
    session.headers.update({"X-Forwarded-Proto": "https"})
    results = {}
    for path in LOCS:
        local = to_local(path)
        status, final, hops, text, first_redirect = fetch(session, local)
        status = final_status(local, status, final)
        redirect_rule = None
        redirect_note = ""
        if first_redirect is not None:
            redirect_rule = find_htaccess_rule(path, first_redirect[1])
            if redirect_rule is not None:
                first_status, first_loc = first_redirect
                redirect_note = f"redirects {first_status} -> {first_loc}, covered by .htaccess ({HTACCESS_FILE}): {redirect_rule}. Consider removing {path!r} from PATHS in tests/test_error_regression.py"
        if isinstance(status, int):
            results[path] = {
                "status": status,
                "final": final,
                "hops": hops,
                "fatal": find_fatal(text) if status == 200 else None,
                "body_len": len(text),
                "redirect_rule": redirect_rule,
                "redirect_note": redirect_note,
            }
        else:
            results[path] = {
                "status": status,
                "final": final,
                "hops": hops,
                "fatal": None,
                "body_len": 0,
                "error": status,
                "redirect_rule": None,
                "redirect_note": "",
            }
    return results


def _bucket(res):
    if res.get("redirect_rule") is not None:
        return "redirect-htaccess"
    if res.get("error") is not None or res.get("status") != 200:
        return "non-200"
    if res.get("fatal") is not None:
        return "still-broken"
    return "fixed"


@allure.epic("Regression check")
@allure.feature("Error-log page renders without PHP fatal")
@pytest.mark.parametrize("path", LOCS, ids=[f"{i:04d} {u}" for i, u in enumerate(LOCS)])
def test_error_regression(checked, path):
    local = to_local(path)
    with allure.step(f"GET {local}"):
        res = checked[path]
        allure.attach(
            f"{local} -> {res['final']} [{res['status']}] ({res['hops']} redirects, {res['body_len']} chars, fatal={res['fatal']})",
            name="response",
            attachment_type=allure.attachment_type.TEXT,
        )
        if res.get("error") is not None:
            if FAIL_ON_NON_200:
                pytest.fail(f"{path} -> {res['final']} [{res['error']}]")
            pytest.skip(f"request error (report-only): {res['error']}")
        if res.get("redirect_rule") is not None:
            # Redirect covered by .htaccess: warn (pytest warnings summary
            # + Allure attachment) with a removal suggestion, do not fail.
            msg = f"{path} -> {res['redirect_note']}"
            warnings.warn(msg, stacklevel=2)
            allure.attach(msg, name="redirect-warning", attachment_type=allure.attachment_type.TEXT)
            return
        if FAIL_ON_NON_200:
            assert res["status"] == 200, f"{path} -> {res['final']} [{res['status']}]"
        if res["status"] != 200:
            pytest.skip(f"non-200 (report-only): {res['status']}")
        if res["fatal"] is not None and FAIL_ON_PHP_ERROR:
            pytest.fail(f"{path} -> {res['final']}: still failing with {res['fatal']!r}")


@allure.epic("Regression check")
@allure.feature("Summary")
def test_summary(checked):
    """Runs last (file order): overall counts + full CSV. Never fails."""
    rows = [
        {
            "path": path,
            "checked_url": checked[path]["final"],
            "status": checked[path]["status"],
            "fatal": checked[path]["fatal"] or "",
            "redirects": checked[path]["hops"],
            "note": checked[path].get("redirect_note") or "",
        }
        for path in LOCS
    ]
    counts = Counter(_bucket(checked[path]) for path in LOCS)
    keys = ("fixed", "still-broken", "non-200", "redirect-htaccess")
    summary = {
        "base_url": BASE_URL,
        "path_prefix": PATH_PREFIX or "(all)",
        "paths_file": selection_label(),
        "total": len(rows),
        **{k: counts.get(k, 0) for k in keys},
    }
    table = "| result | count |\n|---|---|\n" + "\n".join(f"| {k} | {summary[k]} |" for k in keys) + f"\n\ntotal: {summary['total']}"
    allure.attach(table, name="regression-summary.txt", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        json.dumps(summary, indent=2),
        name="regression-summary",
        attachment_type=allure.attachment_type.JSON,
    )
    buf = io.StringIO()
    fields = ["path", "checked_url", "status", "fatal", "redirects", "note"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    allure.attach(buf.getvalue(), name="all-urls", attachment_type=allure.attachment_type.CSV)
    print(f"\n{json.dumps(summary)}")
