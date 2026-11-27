# version 0.1.0
"""Tests for tools/remove-project.sh.

Offline: a project folder in tmp_path, an empty HOME and a docker stub that
answers the listing calls and logs every removal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"

DOCKER_STUB = r"""#!/usr/bin/env bash
# Answers like Docker for project "shop"; logs the removals.
echo "$*" >> "$DOCKER_LOG"
case "$*" in
  info*) exit 0 ;;
  "image ls"*"reference=shop-agent-*"*) echo "shop-agent-vm:latest" ;;
  "image ls"*"reference=shop "*|"image ls"*"reference=shop") echo "shop:8.5" ;;
  "image ls"*) ;;
  "ps -a"*"label=com.docker.compose.project=shop"*) echo "shop-apache" ;;
  "ps -a"*"ancestor=shop-agent-vm:latest"*) echo "shop-agent-vm-task-123" ;;
  "ps -a"*) ;;
  "volume ls"*) echo "shop_allure-results" ;;
  "volume inspect shop-agent-data"*) exit 0 ;;
  "network ls"*) echo "shop_default" ;;
esac
exit 0
"""


@pytest.fixture(name="env")
def fixture_env(tmp_path: Path) -> dict[str, str]:
    """An empty HOME, the docker stub first on PATH and its log."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "docker").write_text(DOCKER_STUB, encoding="utf-8")
    (stubs / "docker").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("SITE_", "XDG_", "GIT_"))}
    return {**clean, "HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "DOCKER_LOG": str(tmp_path / "docker.log")}


@pytest.fixture(name="project")
def fixture_project(tmp_path: Path) -> Path:
    """A folder new-project.sh would have made: container/.env names it shop."""
    root = tmp_path / "projects" / "shop"
    (root / "container" / "src").mkdir(parents=True)
    (root / "container" / ".env").write_text('SITE_PROJECT="shop"\n', encoding="utf-8")
    (root / "container" / "src" / "index.php").write_text("<?php\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    return root


def run(env: dict[str, str], *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(TOOLS / "remove-project.sh"), *map(str, args)], capture_output=True, text=True, env=env, check=False)


def removals(env: dict[str, str]) -> list[str]:
    log = Path(env["DOCKER_LOG"])
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [line for line in lines if line.startswith(("rm ", "volume rm", "network rm", "image rm"))]


def make_dirs(env: dict[str, str]) -> tuple[Path, Path, Path]:
    home = Path(env["HOME"])
    dirs = (home / ".config" / "shop", home / ".local" / "share" / "shop", home / ".cache" / "shop")
    for path in dirs:
        path.mkdir(parents=True)
    return dirs


def test_without_yes_only_the_plan_is_printed(env: dict[str, str], project: Path) -> None:
    make_dirs(env)
    result = run(env, project)
    assert result.returncode == 0, result.stderr
    for name in ("shop-apache", "shop-agent-vm-task-123", "shop:8.5", "shop-agent-vm:latest", "shop_allure-results", "shop-agent-data", "shop_default"):
        assert name in result.stdout
    assert "Nothing was removed" in result.stdout
    assert project.exists()
    assert not removals(env)


def test_yes_removes_everything_but_the_credentials(env: dict[str, str], project: Path) -> None:
    config, data, cache = make_dirs(env)
    result = run(env, project, "--yes")
    assert result.returncode == 0, result.stderr
    assert not project.exists() and not data.exists() and not cache.exists()
    assert config.exists()
    assert "once" in result.stdout  # the name is free once the credentials folder is gone
    done = removals(env)
    assert "rm -f shop-agent-vm-task-123 shop-apache" in done
    assert "volume rm shop-agent-data shop_allure-results" in done
    assert "network rm shop_default" in done
    assert "image rm shop-agent-vm:latest shop:8.5" in done


def test_with_credentials_removes_the_config_folder(env: dict[str, str], project: Path) -> None:
    config, _, _ = make_dirs(env)
    assert run(env, project, "--yes", "--with-credentials").returncode == 0
    assert not config.exists()


def test_keep_folder_keeps_the_project(env: dict[str, str], project: Path) -> None:
    assert run(env, project, "--yes", "--keep-folder").returncode == 0
    assert project.exists()
    assert removals(env)


def test_a_folder_new_project_did_not_make_is_refused(env: dict[str, str], tmp_path: Path) -> None:
    other = tmp_path / "other-site"
    (other / "container").mkdir(parents=True)
    result = run(env, other, "--yes")
    assert result.returncode == 2
    assert "SITE_PROJECT" in result.stderr
    assert other.exists()


def test_commits_no_remote_has_need_force(env: dict[str, str], project: Path) -> None:
    git = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "add", "container/src/index.php"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "first"], check=True)
    result = run(env, project, "--yes")
    assert result.returncode == 1
    assert "no remote" in result.stdout
    assert project.exists()
    assert run(env, project, "--yes", "--force").returncode == 0
    assert not project.exists()


def test_the_engine_itself_is_refused(env: dict[str, str], tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    (engine / "container" / "tools").mkdir(parents=True)
    shutil.copy(TOOLS / "remove-project.sh", engine / "container" / "tools" / "remove-project.sh")
    (engine / "container" / ".env").write_text('SITE_PROJECT="engine"\n', encoding="utf-8")
    result = subprocess.run(["bash", str(engine / "container" / "tools" / "remove-project.sh"), str(engine), "--yes"], capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 2
    assert "engine this script runs from" in result.stderr
    assert engine.exists()
