# version 0.0.2
"""Docker + PHAR plumbing: image pull, PHAR fetch, command execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .config import PHPCBF_URL, PHPCS_URL, PHPSTAN_URL, PSALM_URL, Config

sys.path.append(str(Path(__file__).resolve().parents[2]))

from project_name import container_name  # pylint: disable=wrong-import-position,wrong-import-order,import-error  # noqa: E402


def run_command(args: list[str], *, capture: bool = False, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command, streaming output unless capture=True."""
    return subprocess.run(args, capture_output=capture, text=True, check=check)


def ensure_docker_image(php_image: str) -> None:
    """Pull the runtime image when it is not present locally."""
    inspect = subprocess.run(
        ["docker", "image", "inspect", php_image],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect.returncode != 0:
        print(f"pulling {php_image} ...", file=sys.stderr)
        run_command(["docker", "pull", php_image], check=True)


def fetch_phar(name: str, url: str, tool_cache: str) -> str:
    """Download a PHAR into the host cache when missing; return its path."""
    os.makedirs(tool_cache, exist_ok=True)
    dest = os.path.join(tool_cache, name)
    if os.path.isfile(dest):
        return dest
    print(f"fetching {name} ...")
    try:
        with urllib.request.urlopen(url) as response, open(dest, "wb") as handle:
            shutil.copyfileobj(response, handle)
    except OSError as exc:
        raise RuntimeError(f"download of {name} failed: {exc}") from exc
    if os.path.getsize(dest) == 0:
        raise RuntimeError(f"download of {name} failed (empty file).")
    return dest


def ensure_tools(cfg: Config) -> None:
    """Fetch required PHARs for the selected tools (phpcbf only with --fix)."""
    for tool in cfg.tools:
        if tool == "phpstan":
            fetch_phar("phpstan.phar", PHPSTAN_URL, cfg.tool_cache)
        elif tool == "psalm":
            fetch_phar("psalm.phar", PSALM_URL, cfg.tool_cache)
        elif tool == "phpcs":
            fetch_phar("phpcs.phar", PHPCS_URL, cfg.tool_cache)
            if cfg.fix:
                fetch_phar("phpcbf.phar", PHPCBF_URL, cfg.tool_cache)


def print_tool_versions(cfg: Config) -> None:
    """Print each selected tool's version (mirrors lint-static.sh setup)."""
    base = ["docker", "run", "--rm", "--name", container_name("php-static-version"), "-v", f"{cfg.tool_cache}:/tools:ro", cfg.php_image]
    for tool in cfg.tools:
        if tool == "phpstan":
            run_command([*base, "php", "/tools/phpstan.phar", "--version"])
        elif tool == "psalm":
            proc = run_command([*base, "php", "/tools/psalm.phar", "--version"], capture=True)
            print((proc.stdout or proc.stderr or "").splitlines()[:1])
        elif tool == "phpcs":
            phar = "/tools/phpcbf.phar" if cfg.fix else "/tools/phpcs.phar"
            run_command([*base, "php", phar, "--version"])


def docker_php_run(
    cfg: Config,
    phar: str,
    tool_args: list[str],
    extra_mounts: list[str] | None = None,
) -> int:
    """Run a PHAR under the PHP image with the repo mounted at /app.

    Streams output to the terminal; returns the container exit code.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name("php-static"),
        "-v",
        f"{cfg.app_root}:/app",
        "-w",
        "/app",
        "-v",
        f"{cfg.tool_cache}:/tools:ro",
    ]
    if extra_mounts:
        cmd.extend(extra_mounts)
    cmd.extend([cfg.php_image, "php", phar, *tool_args])
    proc = subprocess.run(cmd, check=False)
    return proc.returncode
