# version 0.2.1
"""Tests for tools/htaccess_redirects.py (no Apache needed)."""

import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import htaccess_redirects as hr  # pylint: disable=wrong-import-position,import-error  # noqa: E402

NEW_FORM = """<IfModule mod_rewrite.c>
    RewriteEngine On
    RewriteCond %{HTTP_HOST} !^(?:localhost|127\\.0\\.0\\.1|php-apache)(?::\\d+)?$
    RewriteRule ^ - [E=SITE_SCHEME:https]
    RewriteCond %{HTTP:X-Forwarded-Proto} !https
    RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]
</IfModule>

# moved page
RewriteCond %{REQUEST_URI} ^/user_profile/(.*)$
RewriteRule ^ %{ENV:SITE_SCHEME}://%{HTTP_HOST}/services/user_profile/%1 [R=301,L]
RewriteCond %{REQUEST_URI} ^/services/user_profile/data/
RewriteRule ^ - [R=404,L]
RewriteCond %{REQUEST_URI} "^/office/dropdown(?:%20| )copy\\.php$"
RewriteRule ^ %{ENV:SITE_SCHEME}://%{HTTP_HOST}/office/dropdown.php [R=301,L]
RewriteCond %{REQUEST_URI} ^/suomen(/.*)?$
RewriteRule ^ https://example.org/suomen/ [R=301,L]
"""

OLD_FORM = """Redirect 301 /hw/disk_b/ /ru/hw/disk_b/
Redirect 301 /debian/date_time.php /ru/linux/debian/date_time.php
RedirectMatch 301 ^/rfid/rfid_tags\\.php$ /rfid/tags/
RedirectMatch 404 ^/private/
"""


def test_new_form_pairs_become_rules():
    rules = hr.parse(NEW_FORM)
    assert [(rule.pattern, rule.target, rule.status) for rule in rules] == [
        ("^/user_profile/(.*)$", "/services/user_profile/%1", 301),
        ("^/services/user_profile/data/", None, 404),
        ("^/office/dropdown(?:%20| )copy\\.php$", "/office/dropdown.php", 301),
        ("^/suomen(/.*)?$", "https://example.org/suomen/", 301),
    ]


def test_negated_condition_is_no_source_path():
    text = NEW_FORM + ("RewriteCond %{REQUEST_FILENAME} -d\nRewriteCond %{REQUEST_URI} !/$\nRewriteRule ^ %{ENV:SITE_SCHEME}://%{HTTP_HOST}%{REQUEST_URI}/ [R=301,L]\n")
    assert hr.parse(text) == hr.parse(NEW_FORM)


def test_scheme_and_https_rules_are_not_redirects():
    patterns = [rule.pattern for rule in hr.parse(NEW_FORM)]
    assert all("HTTP_HOST" not in pattern and "Forwarded" not in pattern for pattern in patterns)


def test_old_form_keeps_mod_alias_prefix_semantics():
    rules = hr.parse(OLD_FORM)
    by_target = {rule.target: rule for rule in rules}
    folder = by_target["/ru/hw/disk_b/"]
    assert folder.matches("/hw/disk_b/") and folder.matches("/hw/disk_b/a/b")
    page = by_target["/ru/linux/debian/date_time.php"]
    assert page.matches("/debian/date_time.php") and page.matches("/debian/date_time.php/extra")
    assert not page.matches("/debian/date_time.phpx")
    assert by_target["/rfid/tags/"].matches("/rfid/rfid_tags.php")
    assert [rule.status for rule in rules] == [301, 301, 301, 404]


@pytest.mark.parametrize(
    ("path", "redirected"),
    [("/user_profile/", True), ("/office/dropdown copy.php", True), ("/services/user_profile/data/x", False), ("/ai/", False)],
)
def test_redirect_flag_and_matching(path, redirected):
    hits = [rule for rule in hr.matching(hr.parse(NEW_FORM), path) if rule.is_redirect]
    assert bool(hits) is redirected


def test_cli_match_exit_codes(tmp_path):
    htaccess = tmp_path / ".htaccess"
    htaccess.write_text(NEW_FORM, encoding="utf-8")
    script = TOOLS / "htaccess_redirects.py"
    hit = subprocess.run([sys.executable, str(script), "match", str(htaccess), "/user_profile/"], capture_output=True, text=True, check=False)
    miss = subprocess.run([sys.executable, str(script), "match", str(htaccess), "/ai/"], capture_output=True, text=True, check=False)
    assert hit.returncode == 1 and "line 11" in hit.stdout
    assert miss.returncode == 0 and not miss.stdout


def test_repo_htaccess_has_no_old_style_redirects():
    htaccess = TOOLS.parent / "src" / ".htaccess"
    if not htaccess.is_file():
        pytest.skip("this repository has no container/src/.htaccess")
    text = htaccess.read_text(encoding="utf-8")
    assert not [line for line in text.splitlines() if line.startswith(("Redirect ", "RedirectMatch "))]
    assert len([rule for rule in hr.parse(text) if rule.is_redirect]) > 1000
