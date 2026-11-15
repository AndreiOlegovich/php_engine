# version 0.0.3
"""CSS parity check: /.css/<sheet> must be identical on local, dev, prod.

Catches deploy drift (e.g. new rules uploaded to dev but never to prod,
or a hotfixed prod file): the three environments serve the same tree, so
the built stylesheet must match byte for byte.

One pytest case fetching all three origins; on mismatch it fails with
sizes, sha256 prefixes, and a capped unified diff. Fetch errors fail
loudly (a sync test that cannot reach an origin proves nothing).

Env (all optional):
  CSS_PARITY_LOCAL, CSS_PARITY_DEV, CSS_PARITY_PROD (origins),
  CSS_PARITY_PATH (default: SITE_CSS_SHEET or /.css/site.css),
  CSS_PARITY_TIMEOUT (seconds, default 20).
When CSS_PARITY_LOCAL is unset, BASE_URL is honored first (workstation
runs export BASE_URL=http://127.0.0.1:8080; the docker network uses
http://php-apache) — the same dual-context convention as the other
modules.
"""

import difflib
import hashlib
import os

import allure
import pytest
import requests

LOCAL_URL = os.environ.get("CSS_PARITY_LOCAL", os.environ.get("BASE_URL", "http://php-apache")).rstrip("/")
DEV_URL = os.environ.get("CSS_PARITY_DEV", os.environ.get("SITE_DEV_URL", "https://dev.example.com")).rstrip("/")
PROD_URL = os.environ.get("CSS_PARITY_PROD", os.environ.get("SITE_URL", "https://example.com")).rstrip("/")
CSS_PATH = os.environ.get("CSS_PARITY_PATH", os.environ.get("SITE_CSS_SHEET", "/.css/site.css"))
TIMEOUT = float(os.environ.get("CSS_PARITY_TIMEOUT", "20"))
DIFF_CONTEXT = 3
DIFF_MAX_LINES = 40
USER_AGENT = os.environ.get("SITE_USER_AGENT", "css-parity/0.0.1")


def _fetch(origin):
    """GET the stylesheet from one origin; pytest.fail on any problem."""
    url = origin + CSS_PATH
    try:
        response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    except requests.exceptions.RequestException as error:
        pytest.fail(f"could not fetch {url}: {type(error).__name__}: {error}")
    if response.status_code != 200:
        pytest.fail(f"unexpected status {response.status_code} for {url}")
    if not response.content:
        pytest.fail(f"empty body for {url}")
    return url, response.content


def _digest(body):
    return hashlib.sha256(body).hexdigest()[:12]


@pytest.mark.skip(reason="enable it once SITE_DEV_URL answers")
def test_css_parity_across_environments():
    """All three origins serve byte-identical site.css."""
    fetched = [_fetch(origin) for origin in (LOCAL_URL, DEV_URL, PROD_URL)]
    bodies = dict(fetched)
    digests = {url: _digest(body) for url, body in fetched}
    with allure.step("compare sha256 across local/dev/prod"):
        allure.attach(
            "\n".join(f"{url}  {len(bodies[url])} bytes  sha256:{digests[url]}" for url, _body in fetched),
            name="css-parity-fingerprints",
            attachment_type=allure.attachment_type.TEXT,
        )
    if len(set(bodies.values())) == 1:
        return
    local_url, _local_body = fetched[0]
    lines = []
    for url, body in fetched[1:]:
        if body == bodies[local_url]:
            lines.append(f"identical: {url}")
            continue
        diff = list(
            difflib.unified_diff(
                bodies[local_url].decode("utf-8", errors="replace").splitlines(),
                body.decode("utf-8", errors="replace").splitlines(),
                fromfile=f"local {local_url}",
                tofile=url,
                n=DIFF_CONTEXT,
            )
        )
        lines.append(f"--- diff local vs {url} ({len(diff)} lines, showing {DIFF_MAX_LINES}) ---")
        lines.extend(diff[:DIFF_MAX_LINES])
    report = "\n".join(lines)
    allure.attach(report, name="css-parity-diff", attachment_type=allure.attachment_type.TEXT)
    pytest.fail(f"the sheet differs across environments:\n{report}")
