# version 0.1.0
"""Tests for tools/htaccess_hosting.py: an .htaccess against its target hosting."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

hh = importlib.import_module("htaccess_hosting")

PORTABLE = """RewriteEngine On
RewriteCond %{HTTPS} off
RewriteCond %{HTTP:X-Forwarded-Proto} !https
RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]
<IfModule mod_php.c>
php_flag display_errors off
</IfModule>
RewriteCond %{REQUEST_URI} ^/old/page\\.php$
RewriteRule ^ %{ENV:SITE_SCHEME}://%{HTTP_HOST}/new/page/ [R=301,L]
"""
PROXY_ONLY = "RewriteEngine On\nRewriteCond %{HTTP:X-Forwarded-Proto} !https\nRewriteRule (.*) https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]\n"
HTTPS_ONLY = "RewriteEngine On\nRewriteCond %{HTTPS} off\nRewriteRule (.*) https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]\n"


def site(tmp_path: Path, htaccess: str, **files: str) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / ".htaccess").write_text(htaccess, encoding="utf-8")
    for name, text in files.items():
        (src / name).write_text(text, encoding="utf-8")
    return src


def problems(src: Path, hosting: str) -> list[str]:
    return [item.problem for item in hh.target_problems(src, hh.HOSTINGS[hosting]) if item.breaks]


@pytest.mark.parametrize("hosting", ["beget", "dreamhost", "apache-proxy", "apache-tls", "litespeed"])
def test_the_portable_forms_pass_everywhere(tmp_path: Path, hosting: str) -> None:
    assert not problems(site(tmp_path, PORTABLE), hosting)


def test_https_by_the_proxy_header_alone_loops_without_a_proxy(tmp_path: Path) -> None:
    src = site(tmp_path, PROXY_ONLY)
    assert not problems(src, "beget")
    assert any("redirects to itself, forever" in p for p in problems(src, "dreamhost"))


def test_https_by_the_https_variable_alone_loops_behind_a_proxy(tmp_path: Path) -> None:
    src = site(tmp_path, HTTPS_ONLY)
    assert not problems(src, "dreamhost")
    assert any("redirects to itself, forever" in p for p in problems(src, "beget"))


def test_php_settings_outside_if_module_give_500_without_mod_php(tmp_path: Path) -> None:
    src = site(tmp_path, "php_value memory_limit 256M\n")
    assert not problems(src, "beget")
    assert any("500 for every page" in p for p in problems(src, "dreamhost"))


def test_a_php_handler_of_another_hosting_breaks(tmp_path: Path) -> None:
    src = site(tmp_path, "AddHandler application/x-httpd-php56 .php\n")
    assert any("PHP handler of another hosting" in p for p in problems(src, "beget"))


def test_nginx_reads_no_htaccess(tmp_path: Path) -> None:
    assert any("nginx reads no .htaccess" in p for p in problems(site(tmp_path, PORTABLE), "nginx"))


def test_warnings_do_not_break(tmp_path: Path) -> None:
    src = site(tmp_path, "Redirect 301 /old.php /new/\nphp_value error_log /home/u/site/public_html/err.log\nRewriteRule ^img/old\\.png$ /img/new.png [R=301,L]\n")
    found = hh.target_problems(src, hh.HOSTINGS["beget"])
    assert not [item for item in found if item.breaks]
    kinds = " ".join(item.problem for item in found)
    assert "plain-HTTP hop" in kinds and "absolute path of a hosting account" in kinds and "static file" in kinds


def test_the_hints_name_the_hosting_the_files_were_written_for(tmp_path: Path) -> None:
    src = site(tmp_path, PROXY_ONLY + "php_flag display_errors off\n")
    assert "Beget-like" in hh.looks_like(hh.hints(src))
    other = tmp_path / "other"
    other.mkdir()
    (other / ".user.ini").write_text("display_errors=Off\n", encoding="utf-8")
    assert "DreamHost-like" in hh.looks_like(hh.hints(other))


def test_exit_codes_follow_the_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = site(tmp_path, HTTPS_ONLY)
    profile = tmp_path / "site-profile.env"
    assert hh.main(["--src", str(src), "--profile", str(profile)]) == 0  # no target: hints only
    assert "target hosting is not set" in capsys.readouterr().out
    profile.write_text('SITE_HOSTING="beget"\n', encoding="utf-8")
    assert hh.main(["--src", str(src), "--profile", str(profile)]) == 1
    assert hh.main(["--src", str(src), "--profile", str(profile), "--hosting", "dreamhost"]) == 0
    profile.write_text('SITE_HOSTING="somewhere"\n', encoding="utf-8")
    assert hh.main(["--src", str(src), "--profile", str(profile)]) == 2
