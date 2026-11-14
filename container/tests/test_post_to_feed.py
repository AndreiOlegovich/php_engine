"""Tests for reading page changes from the completed commit."""

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

MODULE_SPEC = importlib.util.spec_from_file_location(
    "post_to_feed_under_test",
    Path(__file__).resolve().parents[1] / "tools" / "feed" / "post_to_feed.py",
)
assert MODULE_SPEC is not None
post_to_feed: Any = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(post_to_feed)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_committed_changes_includes_root_and_followup_commits(tmp_path, monkeypatch):
    """The post-commit hook sees added, modified, renamed, and deleted pages."""
    monkeypatch.setattr(post_to_feed, "REPO_ROOT", str(tmp_path))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Feed tests")
    _git(tmp_path, "config", "user.email", "feed-tests@example.invalid")

    src = tmp_path / "container" / "src" / "linux" / "ubuntu"
    src.mkdir(parents=True)
    index = src / "index.php"
    old_page = src / "old.php"
    deleted_page = src / "deleted.php"
    index.write_text("<?php echo 'initial';\n", encoding="utf-8")
    old_page.write_text("<?php echo 'rename me';\n", encoding="utf-8")
    deleted_page.write_text("<?php echo 'delete me';\n", encoding="utf-8")
    _git(tmp_path, "add", "container/src")
    _git(tmp_path, "commit", "-m", "initial pages")

    root_changes, root_dropped = post_to_feed.committed_changes()
    assert {path for path, action, _ in root_changes if action == "created"} == {
        "container/src/linux/ubuntu/index.php",
        "container/src/linux/ubuntu/old.php",
        "container/src/linux/ubuntu/deleted.php",
    }
    assert root_dropped == []

    index.write_text("<?php echo 'updated';\n", encoding="utf-8")
    old_page.rename(src / "renamed.php")
    deleted_page.unlink()
    (src / "new.php").write_text("<?php echo 'new';\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "update pages")

    changes, dropped = post_to_feed.committed_changes()
    assert set(changes) == {
        ("container/src/linux/ubuntu/index.php", "modified", None),
        ("container/src/linux/ubuntu/new.php", "created", None),
        (
            "container/src/linux/ubuntu/renamed.php",
            "moved",
            "container/src/linux/ubuntu/old.php",
        ),
    }
    assert dropped == ["/linux/ubuntu/deleted.php"]
