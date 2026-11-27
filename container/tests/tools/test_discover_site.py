# version 0.1.0
"""Tests for tools/discover_site.py.

A small docroot in tmp_path for the file scan, and a local HTTP server in a
thread that answers with fixed pages for the rendered scan.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

ds = importlib.import_module("discover_site")

FOOTER = '<?php\n\n?><div id="footer-end">(c)</div></body></html>\n'


def page(body: str, shell: str = "BasePage_html_id") -> str:
    """A rendered page: the menu, a shell marker, the body and the footer last."""
    return f'<!DOCTYPE html><html><head><title>t</title></head><body><nav class="menu">m</nav><div id="{shell}"></div><p>{body}</p><div id="footer-end">(c)</div></body></html>'


RENDERED = {
    "/": page("home"),
    "/about.php": page("about"),
    "/ru/": page("ru home", shell="ArticlePage_html_id"),
    "/ru/a.php": page("ru a"),
    "/qa/": page("qa"),
}


def put(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))


@pytest.fixture(name="src")
def fixture_src(tmp_path: Path) -> Path:
    """Four pages, a shared footer include, an include folder and two problem files."""
    src = tmp_path / "src"
    put(src / "index.php", "<?php\n\n$p = new ArticlePage('Home');\nrequire 'footer.php';\n")
    put(src / "about.php", "<?php\n\n$p = new BasePage('About');\nrequire_once('footer.php');\n")
    put(src / "ru" / "index.php", "<?php\n\n$p = new ArticlePage('Главная');\ninclude 'footer.php';\n")
    put(src / "ru" / "a.php", '<?php\n\n$r = mysql_query("x");\n?><link rel="canonical" href="https://www.example.com/ru/a.php">\n')
    put(src / "footer.php", FOOTER)
    put(src / "_inc" / "menu.php", "<?php\n\necho 'menu';\n")
    put(src / "old.html", "<html><body>Привет</body></html>\n", encoding="cp1251")
    put(src / "qa" / "index.php", "<?php\n\n$p = new BasePage('QA');\n")
    put(src / "index copy.php", "<?php\n\necho 'an editor copy';\n")
    put(src / ".gitignore", "* copy*\n")
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    return src


@pytest.fixture(name="site")
def fixture_site() -> Iterator[str]:
    """A server for the rendered pages; /old.html redirects to the live host."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802  # pylint: disable=invalid-name
            if self.path == "/old.html":
                self.send_response(301)
                self.send_header("Location", "https://www.example.com/old.html")
                self.end_headers()
                return
            body = RENDERED.get(self.path)
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((body or "missing").encode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:  # pylint: disable=redefined-builtin
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_the_file_scan_finds_classes_includes_and_problems(src: Path) -> None:
    report = ds.scan_files(src)
    # The editor copy is git-ignored, so it is no page; qa/ is a section, not a language.
    assert sorted(p.as_posix() for p in report.pages) == ["about.php", "index.php", "old.html", "qa/index.php", "ru/a.php", "ru/index.php"]
    assert report.classes == {"ArticlePage": 2, "BasePage": 2}
    assert report.includes["footer.php"] == 3
    assert report.html_end_in.most_common(1) == [("footer.php", 3)]
    assert report.lang_dirs == {"ru": 2}
    assert report.canonical_hosts == {"www.example.com": 1}
    assert report.legacy["mysql_* functions (removed in PHP 7)"] == 1
    assert report.not_utf8 == ["old.html"]


def test_a_file_url_is_its_docroot_path() -> None:
    assert ds.url_of(Path("index.php")) == "/"
    assert ds.url_of(Path("ru/index.php")) == "/ru/"
    assert ds.url_of(Path("ru/a.php")) == "/ru/a.php"


def test_the_rendered_scan_suggests_the_markers(src: Path, site: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ds.main(["--src", str(src), "--url", site, "--profile", str(tmp_path / "none.env")]) == 0
    out = capsys.readouterr().out
    assert "301 1" in out  # the redirect to the live host is counted, not followed
    assert """SITE_BOTTOM_MARKER='id="footer-end"'""" in out
    assert "SITE_TAG_MARKERS='menu=class=\"menu\"'" in out  # the bottom marker is not repeated as a --tag
    assert 'SITE_PAGE_MARKERS="BasePage_html_id ArticlePage_html_id"' in out
    assert 'SITE_PAGE_CLASSES="ArticlePage BasePage"' in out or 'SITE_PAGE_CLASSES="BasePage ArticlePage"' in out
    assert 'SITE_LANG_DIRS="ru"' in out


def test_the_profile_values_are_checked_on_the_pages(src: Path, site: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = tmp_path / "site-profile.env"
    profile.write_text('SITE_BOTTOM_MARKER=\'id="gone"\'\nSITE_PAGE_MARKERS="BasePage_html_id"\n', encoding="utf-8")
    assert ds.main(["--src", str(src), "--url", site, "--profile", str(profile)]) == 0
    out = capsys.readouterr().out
    assert 'SITE_BOTTOM_MARKER id="gone": on 0/5 (0%)' in out
    assert "SITE_PAGE_MARKERS BasePage_html_id: on 4/5 (80%)" in out


def test_without_url_only_the_files_are_read(src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ds.main(["--src", str(src), "--profile", str(tmp_path / "none.env")]) == 0
    out = capsys.readouterr().out
    assert "run again with --url" in out
    assert "SITE_BOTTOM_MARKER" not in out


def test_an_empty_docroot_is_refused(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    assert ds.main(["--src", str(tmp_path / "src")]) == 2


def test_url_without_a_value_needs_the_profile(src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "DEFAULT_PROFILE", tmp_path / "none.env")
    assert ds.main(["--src", str(src), "--url"]) == 2
