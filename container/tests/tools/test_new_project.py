# version 0.7.0
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
PROFILE_EXAMPLE = 'SITE_LOCAL_URL="http://127.0.0.1:8080"\nSITE_BOTTOM_MARKER="</html>"\nSITE_TAG_MARKERS=""\nSITE_PHP_VERSION="8.5"\n'


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
    for name in ("new-project.sh", "project-env.sh", "onboard.sh", "onboard.py", "auto_onboard.sh", "htaccess_hosting.py"):
        shutil.copy(TOOLS / name, tools / name)
    put(tools / "run-tests-venv.sh", "#!/bin/sh\nexit 0\n")
    (tools / "run-tests-venv.sh").chmod(0o755)
    put(root / "container" / "site-profile.env.example", PROFILE_EXAMPLE)
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
    # The test's own Python (3.14) first: the onboarding is Python of the repo's version.
    return {**clean, "HOME": str(home), "PATH": f"{stubs}:{Path(sys.executable).parent}:/usr/bin:/bin"}


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
    assert b"marker" not in output.split(b"Next (")[0]  # nobody knows the markers yet: no question
    assert b"2. container/tools/auto_onboard.sh" in output
    assert f"1. cd {target}\n".encode() in output.replace(b"\r\n", b"\n")  # the first step goes to the root
    assert env_file(target)["SITE_PROJECT"] == "asked-site"


def test_the_next_steps_start_in_the_project_root(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = tmp_path / "projects" / "my shop"
    result = run(engine, env, target, "--name", "shop")
    assert result.returncode == 0, result.stderr
    assert f"1. cd {str(target).replace(' ', chr(92) + ' ')}\n" in result.stdout


def test_markers_from_the_options_replace_the_defaults(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = tmp_path / "projects" / "shop"
    markers = 'menu=<nav class="menu"|<nav id="top">; footer=id="footer-end"'
    result = run(engine, env, target, "--name", "shop", "--bottom-marker", '<div id="end">', "--tag-markers", markers)
    assert result.returncode == 0, result.stderr
    lines = (target / "container" / "site-profile.env").read_text(encoding="utf-8").splitlines()
    assert lines[:4] == [
        lines[0],
        "SITE_BOTTOM_MARKER='<div id=\"end\">'",
        f"SITE_TAG_MARKERS='{markers}'",
        'SITE_PHP_VERSION="8.5"',
    ]
    assert sum(line.startswith("SITE_BOTTOM_MARKER=") for line in lines) == 1


def test_without_markers_the_defaults_stay(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = tmp_path / "projects" / "shop"
    assert run(engine, env, target, "--name", "shop").returncode == 0
    profile = (target / "container" / "site-profile.env").read_text(encoding="utf-8")
    assert 'SITE_BOTTOM_MARKER="</html>"\nSITE_TAG_MARKERS=""\n' in profile


def test_a_marker_with_a_single_quote_is_refused(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = tmp_path / "projects" / "shop"
    result = run(engine, env, target, "--name", "shop", "--bottom-marker", "<div class='end'>")
    assert result.returncode == 2
    assert "single quote" in result.stderr
    assert not target.exists()


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


def test_next_walks_through_the_steps(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    """--next prints the one step the project is at, in the order of the README."""
    target = tmp_path / "projects" / "shop"
    assert run(engine, env, target, "--name", "shop").returncode == 0
    script = target / "container" / "tools" / "new-project.sh"

    def step(docker: str) -> str:
        stubs = tmp_path / "stubs"
        (stubs / "docker").write_text(docker, encoding="utf-8")
        result = subprocess.run(["bash", str(script), "--next"], capture_output=True, text=True, env=env, cwd=target, stdin=subprocess.DEVNULL, check=False)
        assert result.returncode == 0, result.stderr
        return result.stdout

    assert "Docker is not running" in step("#!/bin/sh\nexit 1\n")
    running = '#!/bin/sh\ncase "$1" in ps) echo abc123 ;; esac\nexit 0\n'
    assert "step 2 of 8: start the site" in step("#!/bin/sh\nexit 0\n")
    out = step(running)
    assert "step 3 of 8: copy the old site in" in out and "Done: the site runs at" in out
    put(target / "container" / "src" / "index.php", "<?php\n\necho 'the old site';\n")
    assert "step 4 of 8: let the pages suggest the profile" in step(running)
    profile = target / "container" / "site-profile.env"
    profile.write_text(profile.read_text(encoding="utf-8") + 'SITE_URL="https://shop.example"\n', encoding="utf-8")
    assert "step 5 of 8: name the hosting the site goes to" in step(running)
    profile.write_text(profile.read_text(encoding="utf-8") + 'SITE_HOSTING="beget"\n', encoding="utf-8")
    assert "step 6 and 7 of 8: the gate, then the first commit" in step(running)
    git(target, "add", "container/src/index.php")
    git(target, "commit", "-q", "-m", "first")
    out = step(running)
    assert "step 8 of 8: generate the sitemap" in out and "BASE_URL=https://shop.example" in out
    put(target / "container" / "src" / "sitemap.xml", "<urlset/>\n")
    assert "step 8 of 8: test the pages" in step(running)


def test_next_from_elsewhere_starts_with_cd(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = tmp_path / "projects" / "shop"
    assert run(engine, env, target, "--name", "shop").returncode == 0
    result = subprocess.run(["bash", str(target / "container" / "tools" / "new-project.sh"), "--next"], capture_output=True, text=True, env=env, cwd=tmp_path, check=False)
    assert f"cd {target} && (cd container && docker compose up" in result.stdout


def made_project(engine: Path, env: dict[str, str], tmp_path: Path) -> Path:
    target = tmp_path / "projects" / "shop"
    assert run(engine, env, target, "--name", "shop").returncode == 0
    return target


def set_docker(tmp_path: Path, script: str) -> None:
    (tmp_path / "stubs" / "docker").write_text(script, encoding="utf-8")


RUNNING = '#!/bin/sh\necho "$*" >> "$DOCKER_LOG"\ncase "$1" in ps) echo abc123 ;; esac\nexit 0\n'
STOPPED = '#!/bin/sh\necho "$*" >> "$DOCKER_LOG"\nexit 0\n'


def tool(target: Path, env: dict[str, str], name: str, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(target / "container" / "tools" / name), *map(str, args)], capture_output=True, text=True, env=env, cwd=target, stdin=subprocess.DEVNULL, check=False)


def test_next_id_names_the_step(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = made_project(engine, env, tmp_path)
    env = {**env, "DOCKER_LOG": str(tmp_path / "docker.log")}
    assert tool(target, env, "new-project.sh", "--next-id").stdout.strip() == "docker"
    set_docker(tmp_path, STOPPED)
    assert tool(target, env, "new-project.sh", "--next-id").stdout.strip() == "start"
    set_docker(tmp_path, RUNNING)
    assert tool(target, env, "new-project.sh", "--next-id").stdout.strip() == "copy"


def test_onboard_starts_the_site_then_shows_the_next_step(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = made_project(engine, env, tmp_path)
    env = {**env, "DOCKER_LOG": str(tmp_path / "docker.log")}
    set_docker(tmp_path, STOPPED)
    result = tool(target, env, "onboard.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "compose up -d --build php-apache" in (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "=== step: start" in result.stdout and "Next, step 2 of 8" in result.stdout  # the stub keeps no container running
    assert "$ (cd container && docker compose up -d --build php-apache)" in result.stdout  # the exact command it runs


def test_onboard_copies_the_old_site_in(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = made_project(engine, env, tmp_path)
    env = {**env, "DOCKER_LOG": str(tmp_path / "docker.log")}
    set_docker(tmp_path, RUNNING)
    assert tool(target, env, "onboard.sh").returncode == 2  # which folder is not given
    empty = tmp_path / "not-a-site"
    empty.mkdir()
    result = tool(target, env, "onboard.sh", empty)
    assert result.returncode == 2 and "public_html" in result.stderr
    old = tmp_path / "old-site"
    put(old / "index.php", "<?php\n\necho 'old home';\n")
    put(old / "about" / "index.php", "<?php\n\necho 'about';\n")
    result = tool(target, env, "onboard.sh", old)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "container" / "src" / "about" / "index.php").is_file()
    assert "old home" in (target / "container" / "src" / "index.php").read_text(encoding="utf-8")
    assert "step 4 of 8: let the pages suggest the profile" in result.stdout
    assert f"$ rsync -a --exclude .git/ --info=progress2 --no-inc-recursive {old}/ container/src/" in result.stdout
    assert (old / "index.php").is_file()  # the original stays


def auto(target: Path, env: dict[str, str], answers: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(target / "container" / "tools" / "auto_onboard.sh")], input=answers, capture_output=True, text=True, env=env, cwd=target, check=False)


def test_auto_onboard_waits_for_docker(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = made_project(engine, env, tmp_path)
    result = auto(target, env, "q\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Docker is not running" in result.stdout and "Stopped." in result.stdout


def test_auto_onboard_asks_for_the_old_site_and_copies_it(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = made_project(engine, env, tmp_path)
    env = {**env, "DOCKER_LOG": str(tmp_path / "docker.log")}
    set_docker(tmp_path, RUNNING)
    old = tmp_path / "old-site"
    put(old / "index.php", "<?php\n\necho 'old home';\n")
    result = auto(target, env, f"{tmp_path / 'nowhere'}\n{old}\n")
    out = result.stdout + result.stderr
    assert "is not a folder" in out  # a wrong answer is asked again
    assert f"$ rsync -a --exclude .git/ --info=progress2 --no-inc-recursive {old}/ container/src/" in out
    assert "old home" in (target / "container" / "src" / "index.php").read_text(encoding="utf-8")
    # the fixture has no discover_site.py: the walk stops there, saying so
    assert "=== let the pages suggest the profile" in out and "The discovery failed" in out
    assert result.returncode == 1


def test_auto_onboard_stops_at_the_end_of_input(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = made_project(engine, env, tmp_path)
    env = {**env, "DOCKER_LOG": str(tmp_path / "docker.log")}
    set_docker(tmp_path, RUNNING)
    result = auto(target, env, "")
    assert result.returncode == 0
    assert "No answer: stopping here" in result.stdout


def test_onboard_sets_the_target_hosting(engine: Path, env: dict[str, str], tmp_path: Path) -> None:
    target = made_project(engine, env, tmp_path)
    env = {**env, "DOCKER_LOG": str(tmp_path / "docker.log")}
    set_docker(tmp_path, RUNNING)
    put(target / "container" / "src" / "index.php", "<?php\n\necho 'old home';\n")
    put(target / "container" / "src" / ".htaccess", "RewriteEngine On\nRewriteCond %{HTTPS} off\nRewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]\n")
    profile = target / "container" / "site-profile.env"
    profile.write_text(profile.read_text(encoding="utf-8") + 'SITE_URL="https://shop.example"\n', encoding="utf-8")
    assert tool(target, env, "new-project.sh", "--next-id").stdout.strip() == "hosting"
    assert tool(target, env, "onboard.sh", "somewhere").returncode == 2
    result = tool(target, env, "onboard.sh", "beget")
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SITE_HOSTING="beget"' in profile.read_text(encoding="utf-8")
    assert "$ python3 container/tools/htaccess_hosting.py" in result.stdout
    assert "redirects to itself, forever" in result.stdout  # %{HTTPS} alone loops behind Beget's proxy
    assert "step 6 and 7 of 8: the gate" in result.stdout
