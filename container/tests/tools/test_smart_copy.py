# version 0.1.0
"""Tests for the smart dev-to-prod copy (both variants) on temp trees.

The smart copy scripts, the change finder and the copy engine are copied into
tmp_path, so the snapshot, lock and log files they write stay out of the
repository.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ON_HOSTING = Path(__file__).resolve().parents[2] / "tools" / "deploy" / "on_hosting"
SCRIPTS = ("copy-smartly-dev-to-prod.sh", "copy-smartly-dev-to-prod-verbose.sh")


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in (*SCRIPTS, "find-dirs-with-changes.sh", "copy-with-shadow-backup.sh"):
        shutil.copy2(ON_HOSTING / name, bin_dir / name)
    dev, prod = tmp_path / "dev" / "public_html", tmp_path / "prod" / "public_html"
    for rel in ("index.php", "a/page.php", "b/page.php"):
        _write(dev, rel, f"{rel} v1")
    prod.mkdir(parents=True)
    return bin_dir, dev, prod


def _run(bin_dir: Path, script: str, dev: Path, prod: Path, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in {"SITE_ENV", "SMART_STATE_FILE", "SMART_VERBOSE_LOG", "SMART_MAXDEPTH"}}
    env.update({"DEV_DOCROOT": str(dev), "PROD_DOCROOT": str(prod), **(extra_env or {})})
    return subprocess.run(["bash", str(bin_dir / script), *args], env=env, capture_output=True, text=True, check=False, timeout=120)


@pytest.mark.parametrize("script", SCRIPTS)
def test_same_size_edit_reaches_prod_with_a_backup(tmp_path, script):
    bin_dir, dev, prod = _setup(tmp_path)
    first = _run(bin_dir, script, dev, prod)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (prod / "a" / "page.php").read_text(encoding="utf-8") == "a/page.php v1"

    edited = _write(dev, "a/page.php", "a/page.php v2")  # same size as v1
    later = time.time() + 5
    os.utime(edited, (later, later))
    second = _run(bin_dir, script, dev, prod)

    assert second.returncode == 0, second.stdout + second.stderr
    assert (prod / "a" / "page.php").read_text(encoding="utf-8") == "a/page.php v2"
    backups = list((tmp_path / "prod" / "backup_public_html").rglob("page.php*"))
    assert any(path.read_text(encoding="utf-8") == "a/page.php v1" for path in backups), backups


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_file_added_on_dev_during_a_run_is_copied_by_the_next_run(tmp_path, script):
    bin_dir, dev, prod = _setup(tmp_path)
    assert _run(bin_dir, script, dev, prod).returncode == 0

    # Only a/ changes before the run; the engine wrapper adds b/late.php while
    # a/ is being copied, as an FTP upload to dev at that moment would.
    edited = _write(dev, "a/page.php", "a/page.php v2, longer")
    later = time.time() + 5
    os.utime(edited, (later, later))
    (bin_dir / "copy-with-shadow-backup.sh").rename(bin_dir / "copy_engine_real.sh")
    wrapper = bin_dir / "copy-with-shadow-backup.sh"
    wrapper.write_text(
        '#!/usr/bin/env bash\nif [ ! -e "$MARK" ]; then touch "$MARK"; printf late > "$LATE"; fi\nexec "$(dirname "$0")/copy_engine_real.sh" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    extra = {"MARK": str(tmp_path / "added"), "LATE": str(dev / "b" / "late.php")}

    during = _run(bin_dir, script, dev, prod, extra_env=extra)
    assert during.returncode == 0, during.stdout + during.stderr
    assert (dev / "b" / "late.php").exists()
    assert not (prod / "b" / "late.php").exists()

    preview = _run(bin_dir, script, dev, prod, "--dry-run", extra_env=extra)
    assert "[changed] b" in preview.stdout, preview.stdout + preview.stderr

    after = _run(bin_dir, script, dev, prod, extra_env=extra)
    assert after.returncode == 0, after.stdout + after.stderr
    assert (prod / "b" / "late.php").read_text(encoding="utf-8") == "late"
    assert not list(bin_dir.glob(".smart_copy.state.pending.*"))
