# version 0.1.1
"""Every container a tool starts carries the project's name.

Docker names a container without --name at random (bold_yonath), so docker ps
could not tell which project started it. The shell tools name theirs with
project_container of project-env.sh, the Python tools with container_name of
project_name.py; the scan below fails a new docker run that forgets.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import project_name as pn  # noqa: E402  pylint: disable=wrong-import-position,import-error


def test_python_names_start_with_the_project(monkeypatch) -> None:
    monkeypatch.setenv("SITE_PROJECT", "Hei Hei")
    first, second = pn.container_name("PHP Static"), pn.container_name("php-static")
    assert first.startswith(f"hei-hei-php-static-{os.getpid()}-")
    assert first != second


def test_shell_names_start_with_the_project() -> None:
    command = f'. "{TOOLS / "project-env.sh"}"; a=$(project_container "Shell Check"); b=$(project_container shellcheck); echo "$a $b"'
    env = {**os.environ, "SITE_PROJECT": "heihei"}
    first, second = subprocess.run(["bash", "-c", command], capture_output=True, text=True, env=env, check=True).stdout.split()
    assert re.fullmatch(r"heihei-shell-check-\d+", first)
    assert re.fullmatch(r"heihei-shellcheck-\d+", second)


def tracked(*patterns: str) -> list[Path]:
    """The tracked tool files (tests of the tools excluded: they quote commands)."""
    out = subprocess.run(["git", "-C", str(TOOLS), "ls-files", "-z", "--", *patterns], capture_output=True, check=True).stdout
    # A file deleted but not yet committed is still tracked: only those on disk count.
    return [TOOLS / name for name in out.decode().split("\0") if name and "/tests/" not in f"/{name}" and (TOOLS / name).is_file()]


def test_every_shell_docker_run_has_a_name() -> None:
    unnamed = []
    for path in tracked("*.sh"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split(" #", 1)[0]
            if re.search(r"\bdocker run\b", code) and not code.lstrip().startswith("#") and "--name" not in code:
                unnamed.append(f"{path.relative_to(TOOLS)}:{number}")
    assert not unnamed, f"docker run without --name: {unnamed}"


def test_every_python_docker_run_has_a_name() -> None:
    unnamed = []
    for path in tracked("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'"docker",\s*"run",', text):
            window = text[match.end() : match.end() + 200]
            if '"--name"' not in window:
                unnamed.append(f"{path.relative_to(TOOLS)}:{text.count(chr(10), 0, match.start()) + 1}")
    assert not unnamed, f"docker run without --name: {unnamed}"
