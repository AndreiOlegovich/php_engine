# version 0.1.0
"""generate-sitemap.sh keeps the previous sitemap in backup/, not in the docroot.

A backup next to src/sitemap.xml sat in the docroot, where git did not ignore
it and a deploy could publish it. The script runs on a tmp_path copy of the
repo layout (container/tools and container/src), so no site is needed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
REPO = TOOLS.parents[1]


def make_repo(root: Path) -> Path:
    """A repo root with the script, its exclude list, one page and an old sitemap."""
    tools = root / "container" / "tools"
    tools.mkdir(parents=True)
    for name in ("generate-sitemap.sh", "sitemap-excludes.conf"):
        shutil.copy(TOOLS / name, tools / name)
    src = root / "container" / "src"
    src.mkdir()
    (src / "index.php").write_text("<?php\n\necho 'home';\n", encoding="utf-8")
    (src / "sitemap.xml").write_text("<urlset>old</urlset>\n", encoding="utf-8")
    return src


def test_the_backup_goes_to_backup_at_the_repo_root(tmp_path: Path) -> None:
    src = make_repo(tmp_path)
    result = subprocess.run(
        ["bash", str(tmp_path / "container" / "tools" / "generate-sitemap.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "BASE_URL": "https://example.com"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    backups = list((tmp_path / "backup").glob("sitemap.xml.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "<urlset>old</urlset>\n"
    assert not list(src.glob("*.bak-*"))
    assert "https://example.com/" in (src / "sitemap.xml").read_text(encoding="utf-8")


def test_backups_are_ignored_by_git() -> None:
    for path in ("backup/sitemap.xml.bak-20261127000000", "container/src/sitemap.xml.bak-20261127000000"):
        result = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", path], check=False)
        assert result.returncode == 0, f"{path} is not ignored"
