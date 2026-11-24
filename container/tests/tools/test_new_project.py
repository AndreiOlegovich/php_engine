# version 0.1.0
"""Tests for tools/new-project.sh and the project name it hands to the tools.

Offline: a small engine checkout in tmp_path, a docker stub and an empty HOME.
"""

from __future__ import annotations

import json
import os
import pty
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import project_name as pn  # noqa: E402  pylint: disable=wrong-import-position,import-error

SETTINGS = '{"permissions": {"deny": ["Read(~/.config/php_engine/**)", "Read(~/.local/share/php_engine/logs/**)"]}}\n'
JSONC = '{ "resource": "*/php_engine-agent-*", "other": "*php_engine/logs*", "creds": "~/.config/php_engine/*" }\n'
REVIEWER = 'resource: "*.config/php_engine*"\nresource: "*php_engine/logs*"\n'


def git(path: Path, *args: str) -> None:
    command = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args]
    subprocess.run(command, capture_output=True, text=True, check=True)


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture(name="engine")
def fixture_engine(tmp_path: Path) -> Path:
    """A checkout named php_engine with just what the script copies and edits."""
    root = tmp_path / "projects" / "php_engine"
    tools = root / "container" / "tools"
    tools.mkdir(parents=True)
    for name in ("new-project.sh", "project-env.sh"):
        shutil.copy(TOOLS / name, tools / name)
    put(tools / "run-tests-venv.sh", "#!/bin/sh\nexit 0\n")
    (tools / "run-tests-venv.sh").chmod(0o755)
    put(root / "container" / "site-profile.env.example", 'SITE_LOCAL_URL="http://127.0.0.1:8080"\n')
    put(root / "container" / "src" / "index.php", "<?php\n")
    put(root / "container" / "src" / ".php" / "keep.php", "<?php\n")
    put(root / ".claude" / "settings.json", SETTINGS)
    put(root / "opencode.jsonc", JSONC)
    put(root / ".opencode" / "agents" / "reviewer.md", REVIEWER)
    git(tmp_path, "init", "-q", "-b", "main", str(root))
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture(name="env")
def fixture_env(tmp_path: Path) -> dict[str, str]:
    """No SITE_ values, an empty HOME and a docker that is not running."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    put(stubs / "docker", "#!/bin/sh\nexit 1\n")
    (stubs / "docker").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    clean = {key: value for key, value in os.environ.items() if not key.startswith(("SITE_", "XDG_", "GIT_"))}
    return {**clean, "HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin"}


def run(engine: Path, env: dict[str, str], *args: object) -> subprocess.CompletedProcess[str]:
    script = engine / "container" / "tools" / "new-project.sh"
    return subprocess.run(
        ["bash", str(script), *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def env_file(root: Path) -> dict[str, str]:
    values = {}
    for line in (root / "container" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    return values


@pytest.mark.parametrize(
    ("name", "expected"),
    [("My Site 2", "my-site-2"), ("php_engine", "php_engine"), ("  --Shop!! ", "shop"), ("Мой сайт", "")],
)
def test_slug_is_the_same_in_shell_and_python(name: str, expected: str) -> None:
    command = f'. "{TOOLS / "project-env.sh"}"; project_slug "$1"'
    shell = subprocess.run(["bash", "-c", command, "slug", name], capture_output=True, text=True, check=True).stdout
    assert shell == expected
    assert pn.slug(name) == expected


def test_new_site_gets_the_name_everywhere(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = tmp_path / "projects" / "shop"
    result = run(engine, env, target, "--name", "My Shop")
    assert result.returncode == 0, result.stderr
    values = env_file(target)
    assert values["SITE_PROJECT"] == "my-shop"
    assert int(values["SITE_HTTP_PORT"]) > 8080
    assert int(values["SITE_ALLURE_PORT"]) >= 5060
    profile = (target / "container" / "site-profile.env").read_text(encoding="utf-8")
    assert f'SITE_LOCAL_URL="http://127.0.0.1:{values["SITE_HTTP_PORT"]}"' in profile
    assert 'SITE_NAME="My Shop"' in profile
    assert "$siteName = 'My Shop';" in (target / "container" / "src" / "index.php").read_text(encoding="utf-8")


def test_permission_rules_follow_the_project(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = tmp_path / "projects" / "shop"
    assert run(engine, env, target, "--name", "shop").returncode == 0
    settings = (target / ".claude" / "settings.json").read_text(encoding="utf-8")
    jsonc = (target / "opencode.jsonc").read_text(encoding="utf-8")
    reviewer = (target / ".opencode" / "agents" / "reviewer.md").read_text(encoding="utf-8")
    assert "Read(~/.config/shop/**)" in settings and "Read(~/.local/share/shop/logs/**)" in settings
    assert "*/shop-agent-*" in jsonc and "*shop/logs*" in jsonc and "~/.config/shop/*" in jsonc
    assert "*.config/shop*" in reviewer and "*shop/logs*" in reviewer
    assert "php_engine" not in settings + jsonc + reviewer


def test_two_projects_never_share_ports(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    first, second = tmp_path / "projects" / "one", tmp_path / "projects" / "two"
    assert run(engine, env, first).returncode == 0
    assert run(engine, env, second).returncode == 0
    one, two = env_file(first), env_file(second)
    assert one["SITE_HTTP_PORT"] != two["SITE_HTTP_PORT"]
    allure_one = set(range(int(one["SITE_ALLURE_PORT"]), int(one["SITE_ALLURE_PORT"]) + 7))
    allure_two = set(range(int(two["SITE_ALLURE_PORT"]), int(two["SITE_ALLURE_PORT"]) + 7))
    assert not allure_one & allure_two


def test_a_used_name_is_refused(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    assert run(engine, env, tmp_path / "projects" / "one", "--name", "shop").returncode == 0
    target = tmp_path / "projects" / "two"
    result = run(engine, env, target, "--name", "Shop")
    assert result.returncode == 2
    assert "already used" in result.stderr
    assert not target.exists()


def test_existing_credentials_folder_is_refused(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    (Path(env["HOME"]) / ".config" / "shop").mkdir(parents=True)
    result = run(engine, env, tmp_path / "projects" / "shop", "--name", "shop")
    assert result.returncode == 2
    assert ".config/shop" in result.stderr


def test_name_without_latin_letters_is_refused(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    result = run(engine, env, tmp_path / "projects" / "x", "--name", "Мой сайт")
    assert result.returncode == 2
    assert "Latin" in result.stderr


def test_the_name_is_asked_in_a_terminal(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = tmp_path / "projects" / "asked"
    master, slave = pty.openpty()
    script = engine / "container" / "tools" / "new-project.sh"
    with subprocess.Popen(["bash", str(script), str(target)], stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True) as proc:
        os.close(slave)
        os.write(master, b"Asked Site\n")
        output = b""
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        assert proc.wait(timeout=30) == 0, output.decode(errors="replace")
    os.close(master)
    assert b"Project name [asked]:" in output
    assert env_file(target)["SITE_PROJECT"] == "asked-site"


def test_configure_names_an_existing_checkout_and_keeps_its_ports(engine: Path, env: dict[str, str]) -> None:
    assert run(engine, env, "--configure", "--name", "php_engine").returncode == 0
    first = env_file(engine)
    assert first["SITE_PROJECT"] == "php_engine"
    assert run(engine, env, "--configure").returncode == 0
    assert env_file(engine) == first
    assert "php_engine" in (engine / ".claude" / "settings.json").read_text(encoding="utf-8")


def test_a_worktree_uses_the_name_of_its_main_checkout(engine: Path, env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(engine, env, "--configure", "--name", "php_engine").returncode == 0
    (engine / "container" / ".env").write_text('SITE_PROJECT="named"\n', encoding="utf-8")
    worktree = tmp_path / "projects" / "named-agent-task"
    git(engine, "worktree", "add", "-q", "-b", "agent/task", str(worktree))
    monkeypatch.delenv("SITE_PROJECT", raising=False)
    assert pn.project_name(worktree) == "named"
    command = f'. "{worktree / "container" / "tools" / "project-env.sh"}"; printf %s "$SITE_PROJECT"'
    shell = subprocess.run(["bash", "-c", command], capture_output=True, text=True, env=env, check=True).stdout
    assert shell == "named"


def test_the_environment_wins(engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SITE_PROJECT", "From Env")
    assert pn.project_name(engine) == "from-env"


@pytest.mark.skipif(shutil.which("php") is None, reason="php is not installed")
@pytest.mark.parametrize(
    ("project", "expected"),
    [("shop", ["/h/.config/shop/ftp-creds.env"]), ("", []), ("../other", [])],
)
def test_metrica_reads_only_the_file_of_its_project(project: str, expected: list[str]) -> None:
    module = TOOLS.parent / "src" / ".php" / "_modules" / "Metrica.php"
    code = "require $argv[1]; $m = new ReflectionMethod('PhpEngine\\\\Modules\\\\Metrica', 'envFileCandidates'); $m->setAccessible(true); echo json_encode($m->invoke(null));"
    clean = {key: value for key, value in os.environ.items() if key not in {"SITE_ENV", "SITE_PROJECT"}}
    php = shutil.which("php")
    assert php is not None
    proc = subprocess.run([php, "-r", code, str(module)], capture_output=True, text=True, env={**clean, "HOME": "/h", "SITE_PROJECT": project}, check=True)
    assert json.loads(proc.stdout) == expected
