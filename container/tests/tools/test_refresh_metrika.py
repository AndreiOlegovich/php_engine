# version 0.1.0
"""refresh-metrika.sh runs one chain at a time.

The chain holds $SITE_LOG_DIR/refresh-metrika.lock; a second run that
finds it taken exits 0 and runs no step. Every step is skipped by flag, so
nothing is fetched or rebuilt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "analyze_logs" / "refresh-metrika.sh"
SKIP_ALL = ["--skip-fetch", "--skip-devices", "--skip-users", "--skip-bots", "--skip-db"]

pytestmark = pytest.mark.skipif(shutil.which("flock") is None, reason="flock (util-linux) is not installed")


def _run(log_dir: Path, *prefix: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SITE_LOG_DIR": str(log_dir)}
    return subprocess.run([*prefix, str(SCRIPT), *SKIP_ALL], env=env, capture_output=True, text=True, timeout=60, check=False)


def test_free_lock_runs_the_chain(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "refresh-metrika: done" in result.stdout
    assert (tmp_path / "refresh-metrika.lock").is_file()


def test_held_lock_skips_the_run(tmp_path: Path) -> None:
    lock = tmp_path / "refresh-metrika.lock"
    result = _run(tmp_path, "flock", str(lock))
    assert result.returncode == 0, result.stderr
    assert "another run holds" in result.stdout
    assert "refresh-metrika: start" not in result.stdout
