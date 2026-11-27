# version 0.2.0
"""The name of this project for the Python tools.

The same rules as container/tools/project-env.sh: the environment, then
container/.env of the main checkout, then the folder of the main checkout.
"""

from __future__ import annotations

import itertools
import os
import re
import subprocess
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
FALLBACK = "php_engine"
_CONTAINERS = itertools.count(1)


def slug(text: str) -> str:
    """A name Docker accepts, or "" when no usable character is left."""
    name = re.sub(r"[^a-z0-9_-]+", "-", text.lower())
    return re.sub(r"[^a-z0-9]+$", "", re.sub(r"^[^a-z0-9]+", "", name))


def main_checkout(root: Path) -> Path:
    """The main checkout of a linked worktree, or the root itself."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return root
    common = Path(proc.stdout.strip())
    return common.parent if proc.returncode == 0 and common.name == ".git" else root


def env_file_value(root: Path, key: str) -> str:
    """The last value of KEY in container/.env, or ""."""
    try:
        lines = (root / "container" / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    found = ""
    for line in lines:
        if line.startswith(f"{key}="):
            found = line.split("=", 1)[1].strip().strip("\"'")
    return found


def project_name(root: Path | None = None) -> str:
    """The name that images, containers, volumes and config folders use."""
    explicit = os.environ.get("SITE_PROJECT", "").strip()
    if explicit:
        return slug(explicit) or FALLBACK
    base = main_checkout(root or DEFAULT_ROOT)
    return slug(env_file_value(base, "SITE_PROJECT") or base.name) or FALLBACK


def container_name(purpose: str, root: Path | None = None) -> str:
    """The name for a container a tool starts: <project>-<purpose>-<pid>-<n>.

    Docker picks a random name (bold_yonath) for a container without one, so
    docker ps could not tell which project started it.
    """
    return f"{project_name(root)}-{slug(purpose) or 'tool'}-{os.getpid()}-{next(_CONTAINERS)}"
