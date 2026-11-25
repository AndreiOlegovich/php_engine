# version 0.2.0
"""Every place that names the site's PHP version names the same one.

The site image, the PHP linter, the static analysers, the line wrapper,
the deploy lint and the OpenCode agent images each carry the version. When
one drifts, a page passes the checks on one PHP and runs on another:
Dockerfile.php once built PHP 8.0 under an image tag that said 8.2, and the
agents ran php -l on Alpine's PHP 8.3.

The reference is the default of PHP_VER in Dockerfile.php.

Usage (from container/):
  ./tools/run-tests-venv.sh -- tests/test_php_version_consistency.py -q
"""

import re
from pathlib import Path

import pytest

CONTAINER = Path(__file__).resolve().parents[1]
DOCKERFILE = CONTAINER / "containerfiles" / "Dockerfile.php"
#: The OpenCode agent images install Alpine's phpXY packages.
AGENT_DOCKERFILES = sorted((CONTAINER / "containerfiles").glob("Dockerfile*.opencode"))
_VERSION = r"(\d+\.\d+)"

#: (file under container/, pattern whose group is the PHP version, how many it must find)
SOURCES = [
    pytest.param("docker-compose.yml", rf"\$\{{PHP_VER:-{_VERSION}\}}", 2, id="compose-image-and-build-arg"),
    pytest.param("tools/linters/lint-php.sh", rf'^PROJECT_PHP="{_VERSION}"', 1, id="lint-php"),
    pytest.param("tools/linters/lint-static.sh", rf"PHP_ENGINE_STATIC_PHP_IMAGE:-php:{_VERSION}-cli", 1, id="lint-static-sh"),
    pytest.param(
        "tools/linters/lint_static_with_py/config.py",
        rf'"PHP_ENGINE_STATIC_PHP_IMAGE", "php:{_VERSION}-cli"',
        1,
        id="lint-static-py",
    ),
    pytest.param("tools/fix/wrap-long-lines.sh", rf'^PHPCS_IMG="php:{_VERSION}-cli"', 1, id="wrap-long-lines"),
    pytest.param("tools/deploy/deploy-ftp.sh", rf'^PHP_VER="{_VERSION}"', 1, id="deploy-ftp-lint"),
    pytest.param("site-profile.env.example", rf'^SITE_PHP_VERSION="{_VERSION}"', 1, id="site-profile"),
]


def site_php_version() -> str:
    """The default of PHP_VER in Dockerfile.php."""
    match = re.search(rf"^ARG PHP_VER={_VERSION}$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert match, f"{DOCKERFILE.name} has no 'ARG PHP_VER=<major.minor>' line"
    return match.group(1)


def test_dockerfile_builds_the_version_it_is_given() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM php:\$\{PHP_VER\}-apache$", text, re.M), f"{DOCKERFILE.name} must build FROM php:${{PHP_VER}}-apache, or PHP_VER changes only the image tag"


@pytest.mark.parametrize(("path", "pattern", "count"), SOURCES)
def test_names_the_site_php_version(path: str, pattern: str, count: int) -> None:
    found = re.findall(pattern, (CONTAINER / path).read_text(encoding="utf-8"), re.M)
    assert len(found) == count, f"{path}: expected {count} PHP version setting(s), found {len(found)}"
    expected = site_php_version()
    assert set(found) == {expected}, f"{path} names PHP {sorted(set(found))}; Dockerfile.php builds {expected}"


def test_there_are_agent_images() -> None:
    assert len(AGENT_DOCKERFILES) >= 1, "no containerfiles/Dockerfile*.opencode found"


@pytest.mark.parametrize("dockerfile", AGENT_DOCKERFILES, ids=lambda path: path.name)
def test_agent_image_installs_the_site_php_version(dockerfile: Path) -> None:
    found = set(re.findall(r"\bphp\d+\b", dockerfile.read_text(encoding="utf-8")))
    major, minor = site_php_version().split(".")
    expected = f"php{major}{minor}"
    assert found, f"{dockerfile.name} installs no Alpine phpXY package"
    assert found == {expected}, f"{dockerfile.name} installs {sorted(found)}; Dockerfile.php builds PHP {major}.{minor} ({expected})"
