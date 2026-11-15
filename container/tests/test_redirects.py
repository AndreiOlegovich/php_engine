# version 0.1.25
"""Redirect answers of the site's .htaccess, checked over HTTP.

Every request asks for one URL without following redirects and checks the
first answer: the status and, for a redirect, the exact Location. A
redirect inside the site must point at BASE_URL's own scheme and host, so
on https://dev.example.com an http:// Location fails. That plain-HTTP hop
was fixed on 2026-10-22 (/section/htaccess/https-proxy.php#https-redirects).

Two groups:
  CASES          hand-picked URLs, one per kind of rule: moved page, folder
                 slash, PATH_INFO, external target, 404 folder, pages that
                 must stay 200. A redirect inside the site is followed once:
                 the target must not redirect again.
  sampled rules  every REDIRECT_SAMPLE_STEP-th redirect rule of the repo's
                 .htaccess (default 25, about 50 requests; 1 = every rule,
                 0 = none), asked for its own literal path. Expected: the
                 first rule in file order that matches that path. Needs
                 container/tools/htaccess_redirects.py, so the group is
                 skipped in the compose test container (tests/ and src/
                 only). The deployed .htaccess must be the repo one.

Usage (from container/):
  ./tools/run-tests-venv.sh -- tests/test_redirects.py -q
  BASE_URL=https://dev.example.com ./tools/run-tests-venv.sh -- tests/test_redirects.py -q
"""

import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://php-apache").rstrip("/")
_BASE = urlparse(BASE_URL)
ORIGIN = f"{_BASE.scheme}://{_BASE.netloc}"
TIMEOUT = (5, 15)  # (connect, read) seconds
SAMPLE_STEP = int(os.environ.get("REDIRECT_SAMPLE_STEP", "25"))
REDIRECTS = (301, 302, 303, 307, 308)
HTACCESS = Path(os.environ.get("HTACCESS_FILE", Path(__file__).resolve().parents[1] / "src" / ".htaccess"))
TOOLS = Path(__file__).resolve().parents[1] / "tools"

try:
    from redirect_cases import CASES  # noqa: E402
except ImportError:
    pytest.skip("tests/redirect_cases.py is missing (copy redirect_cases.example.py)", allow_module_level=True)

# A pattern that is one literal path: ^/a/(.*)$, ^/a\.php(/.*)?$, ^/a/.*$, ^/a/?$, ^/a$
_LITERAL = re.compile(r"\^((?:[^\\.^$*+?()\[\]{}|]|\\.)+?)(?:\(\.\*\)\$|\(/\.\*\)\?\$|\.\*\$|/\?\$|\$)")


@pytest.fixture(scope="module", name="session")
def fixture_session():
    with requests.Session() as session:
        if _BASE.scheme == "http":
            # Local plain HTTP stands in for the proxied https site.
            session.headers["X-Forwarded-Proto"] = "https"
        yield session


def get(session, url):
    """One GET without redirects; one retry when the connection drops."""
    try:
        return session.get(url, timeout=TIMEOUT, allow_redirects=False)
    except requests.ConnectionError:
        return session.get(url, timeout=TIMEOUT, allow_redirects=False)


def absolute(location):
    return location if location.startswith(("http://", "https://")) else ORIGIN + location


def check(response, status, location):
    got = response.headers.get("Location", "")
    assert response.status_code == status, f"{response.url}: {response.status_code} {got}, expected {status} {location or ''}"
    if location is not None:
        assert unquote(got) == unquote(absolute(location)), f"{response.url}: Location {got}, expected {absolute(location)}"


@pytest.mark.parametrize(("path", "status", "location"), CASES, ids=[case[0] for case in CASES])
def test_case(session, path, status, location):
    response = get(session, ORIGIN + path)
    check(response, status, location)
    if status in REDIRECTS and absolute(location).startswith(ORIGIN + "/"):
        target = get(session, absolute(location))
        assert target.status_code not in REDIRECTS, f"{absolute(location)} redirects again to {target.headers.get('Location')}"


@pytest.mark.skipif(_BASE.scheme != "https", reason="http -> https applies only to an https BASE_URL")
def test_http_goes_to_https_in_one_hop(session):
    check(get(session, f"http://{_BASE.netloc}/ai/"), 301, "/ai/")


def _expected(compiled, path):
    """Status and Location of the first rule that matches path."""
    for pattern, rule in compiled:
        if match := pattern.search(path):
            if rule.target is None:
                return rule.status, None
            target = re.sub(r"%(\d)", lambda ref, m=match: m.group(int(ref.group(1))) or "", rule.target)
            return rule.status, target.replace("\\%", "%").replace("\\$", "$")
    return None


def _samples():
    if SAMPLE_STEP <= 0 or not HTACCESS.is_file():
        return []
    sys.path.insert(0, str(TOOLS))
    try:
        import htaccess_redirects  # pylint: disable=import-outside-toplevel,import-error
    except ImportError:
        return []
    rules = htaccess_redirects.load(HTACCESS)
    paths = []
    for rule in (rule for rule in rules if rule.is_redirect):
        literal = _LITERAL.fullmatch(rule.pattern)
        path = re.sub(r"\\(.)", r"\1", literal.group(1)) if literal else ""
        if path.startswith("/") and not re.search(r"[%\s]", path):
            paths.append((rule.line, path))
    compiled = [(re.compile(rule.pattern), rule) for rule in rules]
    found = []
    for line, path in paths[::SAMPLE_STEP]:
        if expected := _expected(compiled, path):
            found.append(pytest.param(path, *expected, id=f"line{line}"))
    return found


SAMPLES = _samples() or [pytest.param("", 0, None, id="none", marks=pytest.mark.skip(reason="no rules: REDIRECT_SAMPLE_STEP=0 or tools/ not mounted"))]


@pytest.mark.parametrize(("path", "status", "location"), SAMPLES)
def test_sampled_rule(session, path, status, location):
    check(get(session, ORIGIN + quote(path, safe="/:@")), status, location)
