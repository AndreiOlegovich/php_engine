# version 0.1.0
"""The starter fixture passes every checker; every broken file is reported.

`starter_site/` is what a new project starts from: valid PHP 8.2, LF
endings, the standard header, no malformed URL, a valid sitemap.
`broken_site/` holds one defect per checker. A checker that passes it
silently has stopped working, and the test says which one.

The phpcs hook needs Docker, so it is not run here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
STARTER = FIXTURES / "starter_site"
BROKEN = FIXTURES / "broken_site"


def run(args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def checker(name: str, src: Path, search: bool = True) -> list[str]:
    args = [str(REPO_ROOT / "container" / "tools" / "linters" / name)]
    if search:
        args += ["--search"]
    return args + ["--all", "--src", str(src)]


def php_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.php"))


def test_starter_site_php_is_valid():
    php = shutil.which("php")
    if php is None:
        pytest.skip("php is not on PATH")
    for path in php_files(STARTER):
        result = run([php, "-l", str(path)])
        assert result.returncode == 0, f"{path}: {result.stdout}{result.stderr}"


def test_starter_site_line_endings_are_clean():
    result = run(checker("check-line-endings.sh", STARTER))
    assert result.returncode == 0, result.stdout + result.stderr


def test_starter_site_php_header_is_clean():
    result = run(checker("check-php-header.sh", STARTER))
    assert result.returncode == 0, result.stdout + result.stderr


def test_starter_site_has_no_blank_line_with_spaces():
    result = run(checker("check-php-blank-lines.sh", STARTER))
    assert result.returncode == 0, result.stdout + result.stderr


def test_starter_site_has_no_malformed_url():
    result = run(checker("find-malformed-urls.sh", STARTER, search=False))
    assert result.returncode == 0, result.stdout + result.stderr


def test_starter_site_sitemap_is_valid():
    result = run([str(REPO_ROOT / "container" / "tools" / "check-sitemap.sh"), str(STARTER / "sitemap.xml")])
    assert result.returncode == 0, result.stdout + result.stderr


def test_broken_site_php_syntax_is_reported():
    php = shutil.which("php")
    if php is None:
        pytest.skip("php is not on PATH")
    result = run([php, "-l", str(BROKEN / "index.php")])
    assert result.returncode != 0
    assert "index.php" in result.stdout + result.stderr


def test_broken_site_line_endings_are_reported():
    result = run(checker("check-line-endings.sh", BROKEN))
    assert result.returncode == 1
    assert "about/index.php" in result.stdout + result.stderr


def test_broken_site_header_is_reported():
    result = run(checker("check-php-header.sh", BROKEN))
    assert result.returncode == 1
    assert "index.php" in result.stdout + result.stderr


def test_broken_site_blank_line_with_spaces_is_reported():
    result = run(checker("check-php-blank-lines.sh", BROKEN))
    assert result.returncode == 1
    assert "index.php" in result.stdout + result.stderr


def test_broken_site_malformed_url_is_reported():
    result = run(checker("find-malformed-urls.sh", BROKEN, search=False))
    assert result.returncode == 1
    assert "about/index.php" in result.stdout + result.stderr


def test_snake_case_call_is_reported(tmp_path: Path):
    src = tmp_path / "src"
    engine = src / ".php"
    engine.mkdir(parents=True)
    (engine / "Page.php").write_text(
        "<?php\n\nclass Page\n{\n    public function createHead(): void\n    {\n    }\n}\n",
        encoding="utf-8",
    )
    page = src / "page.php"
    page.write_text("<?php\n\n$page = new Page();\n$page->create_head();\n", encoding="utf-8")
    result = run(["python3", str(REPO_ROOT / "container/tools/linters/find-snake-calls.py"), "--src", str(src)])
    assert result.returncode == 1
    assert "page.php" in result.stdout + result.stderr


def test_redirected_page_is_reported(tmp_path: Path):
    """A new page that matches a redirect source is unreachable at once."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    about = tmp_path / "container" / "src" / "about"
    about.mkdir(parents=True)
    (about / "index.php").write_text("<?php\n\n$title = 'About';\n", encoding="utf-8")
    (tmp_path / "container" / "src" / ".htaccess").write_text(
        "RewriteEngine On\nRewriteCond %{REQUEST_URI} ^/about/?$\nRewriteRule ^ / [R=301,L]\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "container/src"], check=True)
    result = run([str(REPO_ROOT / "container/tools/check-no-redirected-pages.sh")], cwd=tmp_path)
    assert result.returncode == 1
    assert "/about/" in result.stdout + result.stderr


def test_broken_site_lists_a_redirected_page():
    """The broken sitemap lists /about/, which the broken .htaccess redirects."""
    helper = [sys.executable, str(REPO_ROOT / "container" / "tools" / "htaccess_redirects.py"), "match"]
    assert "/about/" in (BROKEN / "sitemap.xml").read_text(encoding="utf-8")
    assert run(helper + [str(BROKEN / ".htaccess"), "/about/"]).returncode == 1
    assert run(helper + [str(STARTER / ".htaccess"), "/about/"]).returncode == 0
