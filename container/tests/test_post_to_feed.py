"""Tests for the feed builder: page changes per commit, history walk, commit."""

import fcntl
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

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


def test_generated_function_cards_are_skipped(monkeypatch):
    """A generated card stays out of the feed; the hub is posted."""
    monkeypatch.setattr(post_to_feed, "is_article", lambda path: True)
    monkeypatch.setattr(post_to_feed, "page_title", lambda path: "A page")
    # The classifier tests load if_file_is_an_article with SRC_DIR set to
    # their fixtures; a stub keeps this test independent of that order.
    monkeypatch.setattr(post_to_feed, "GENERATED_CARD_DIRS", ["generated/cards"])
    monkeypatch.setattr(post_to_feed, "page_url", lambda path: "/generated/cards/")
    posted, retired = post_to_feed._collect_posts(  # pylint: disable=protected-access
        [
            ("container/src/generated/cards/abs/index.php", "created", None),
            ("container/src/generated/cards/index.php", "created", None),
        ],
        [],
        "2026-10-31",
    )
    assert posted == [{"url": "/generated/cards/", "title": "A page", "date": "2026-10-31", "action": "created"}]
    assert retired == []


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _page(repo: Path, rel: str, text: str = "<?php echo 'page';\n") -> None:
    path = repo / "container" / "src" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture(name="repo")
def _repo(tmp_path, monkeypatch):
    """A main branch with one page and a feed whose 'through' is that commit."""
    monkeypatch.setattr(post_to_feed, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(post_to_feed, "is_article", os.path.isfile)
    monkeypatch.setattr(post_to_feed, "page_title", lambda path: "T " + Path(path).parent.name)
    monkeypatch.setattr(
        post_to_feed,
        "page_url",
        lambda path: post_to_feed._path_to_url(os.path.relpath(path, tmp_path)),  # pylint: disable=protected-access
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Feed tests")
    _git(tmp_path, "config", "user.email", "feed-tests@example.invalid")
    _page(tmp_path, "linux/index.php")
    (tmp_path / "container" / "src" / "feed").mkdir(parents=True)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    first = _git_out(tmp_path, "rev-parse", "HEAD")
    (tmp_path / post_to_feed.FEED_REL).write_text(post_to_feed._dump([], first), encoding="utf-8")  # pylint: disable=protected-access
    (tmp_path / post_to_feed.ARCH_REL).write_text(post_to_feed._dump([], None), encoding="utf-8")  # pylint: disable=protected-access
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feed")
    return tmp_path


def _feed(repo: Path) -> dict[str, Any]:
    return json.loads((repo / post_to_feed.FEED_REL).read_text(encoding="utf-8"))


def test_merge_commit_is_compared_with_its_first_parent(repo):
    """A page that arrives through a merge is posted from the merge commit."""
    _git(repo, "checkout", "-q", "-b", "side")
    _page(repo, "qa/new/index.php")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "side page")
    _git(repo, "checkout", "-q", "main")
    _page(repo, "linux/other.php")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "main page")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")

    changes, dropped = post_to_feed.committed_changes("HEAD")
    assert changes == [("container/src/qa/new/index.php", "created", None)]
    assert dropped == []


def test_run_commits_only_the_feed_and_keeps_other_staged_work(repo):
    """The feed commit holds feed.json and arch.json; a staged page stays staged."""
    _page(repo, "qa/ui/index.php")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ui page")
    tip = _git_out(repo, "rev-parse", "HEAD")
    date = _git_out(repo, "log", "-1", "--format=%cs")
    _page(repo, "linux/index.php", "<?php echo 'work in progress';\n")
    _git(repo, "add", "container/src/linux/index.php")

    assert post_to_feed.main([]) == 0

    assert _git_out(repo, "show", "--name-only", "--format=", "HEAD").splitlines() == [post_to_feed.FEED_REL]
    assert _git_out(repo, "diff", "--cached", "--name-only") == "container/src/linux/index.php"
    feed = _feed(repo)
    assert feed["through"] == tip
    assert feed["items"] == [{"url": "/qa/ui/", "title": "T ui", "date": date, "action": "created"}]

    head = _git_out(repo, "rev-parse", "HEAD")
    assert post_to_feed.main([]) == 0  # only the feed commit is new: nothing to post
    assert _git_out(repo, "rev-parse", "HEAD") == head


def test_skip_leaves_a_commit_out_and_moves_the_marker(repo):
    """--skip drops a plumbing commit and still records it as published."""
    _page(repo, "linux/index.php", "<?php echo 'plumbing';\n")
    _git(repo, "commit", "-q", "-am", "plumbing")
    plumbing = _git_out(repo, "rev-parse", "HEAD")

    assert post_to_feed.main(["--skip", plumbing]) == 0
    feed = _feed(repo)
    assert feed["items"] == []
    assert feed["through"] == plumbing


def test_dry_run_writes_nothing(repo):
    """--dry-run prints and leaves the files and HEAD as they were."""
    _page(repo, "qa/ui/index.php")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ui page")
    head = _git_out(repo, "rev-parse", "HEAD")
    before = (repo / post_to_feed.FEED_REL).read_text(encoding="utf-8")

    assert post_to_feed.main(["--dry-run"]) == 0
    assert _git_out(repo, "rev-parse", "HEAD") == head
    assert (repo / post_to_feed.FEED_REL).read_text(encoding="utf-8") == before


def test_refuses_edited_feed_other_branch_and_held_lock(repo, capsys):
    """Edited feed files, another branch, and a second run all stop before writing."""
    (repo / post_to_feed.FEED_REL).write_text("{}", encoding="utf-8")
    assert post_to_feed.main([]) == 1
    assert "differ from HEAD" in capsys.readouterr().err
    _git(repo, "checkout", "-q", "--", post_to_feed.FEED_REL)

    _git(repo, "checkout", "-q", "-b", "topic")
    assert post_to_feed.main([]) == 1
    assert "run it on main" in capsys.readouterr().err
    _git(repo, "checkout", "-q", "main")

    with open(repo / ".git" / post_to_feed.LOCK_NAME, "a", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert post_to_feed.main([]) == 1
    assert "holds" in capsys.readouterr().err
