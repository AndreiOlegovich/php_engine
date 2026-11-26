# version 0.1.0
"""Tests for tools/ai/copy_worktree_images.py.

Each test builds a main repository and a worktree of it in tmp_path with real
git, so the main checkout is found the same way as on the host.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS / "ai"))
sys.path.insert(0, str(TOOLS / "images"))

cwi = importlib.import_module("copy_worktree_images")
image_list = importlib.import_module("image_list")

SRC = "container/src"


def run_git(cwd: Path, *args: str) -> None:
    """Run git quietly in cwd."""
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout that ignores images in any case, and a worktree of it."""
    main = tmp_path / "main"
    (main / SRC).mkdir(parents=True)
    run_git(main, "init", "-q", "-b", "main")
    run_git(main, "config", "user.email", "test@example.com")
    run_git(main, "config", "user.name", "Test")
    (main / ".gitignore").write_text("*.[pP][nN][gG]\n*.[wW][eE][bB][pP]\n*.[jJ][pP][gG]\n", encoding="utf-8")
    (main / SRC / "page.php").write_text("<?php\n", encoding="utf-8")
    run_git(main, "add", ".gitignore", f"{SRC}/page.php")
    run_git(main, "commit", "-q", "-m", "init")
    worktree = tmp_path / "shop-agent-x"
    run_git(main, "worktree", "add", "-q", "-b", "agent/x", str(worktree))
    return main, worktree


def write(path: Path, data: bytes) -> None:
    """Write data to path, creating the folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_the_extensions_are_those_of_the_image_list() -> None:
    assert {ext.lstrip(".") for ext in cwi.IMAGE_EXTENSIONS} == image_list.EXTENSIONS


def test_main_checkout_is_found_from_the_worktree(repo: tuple[Path, Path]) -> None:
    main, worktree = repo
    assert cwi.main_checkout(worktree) == main.resolve()


def test_copies_a_new_ignored_image(repo: tuple[Path, Path]) -> None:
    main, worktree = repo
    write(worktree / SRC / "img/new/banner.png", b"png-bytes")
    write(worktree / SRC / "img/new/banner.webp", b"webp")
    assert cwi.main([str(worktree)]) == 0
    assert (main / SRC / "img/new/banner.png").read_bytes() == b"png-bytes"
    assert (main / SRC / "img/new/banner.webp").read_bytes() == b"webp"


def test_an_upper_case_extension_is_an_image(repo: tuple[Path, Path]) -> None:
    main, worktree = repo
    write(worktree / SRC / "old/Photo 1.JPG", b"jpg")
    assert cwi.main([str(worktree)]) == 0
    assert (main / SRC / "old/Photo 1.JPG").read_bytes() == b"jpg"


def test_copies_an_untracked_image_that_is_not_ignored(repo: tuple[Path, Path]) -> None:
    main, worktree = repo
    write(worktree / SRC / "img/logo.svg", b"<svg/>")
    assert cwi.main([str(worktree)]) == 0
    assert (main / SRC / "img/logo.svg").exists()


def test_images_outside_the_docroot_stay(repo: tuple[Path, Path]) -> None:
    main, worktree = repo
    write(worktree / "container/.venv/lib/site-packages/icon.png", b"png")
    write(worktree / "container/tools/x/node_modules/logo.svg", b"<svg/>")
    assert not cwi.untracked_images(worktree)
    assert cwi.main([str(worktree)]) == 0
    assert not (main / "container/.venv").exists()
    assert not (main / "container/tools").exists()


def test_skips_files_that_are_no_images(repo: tuple[Path, Path]) -> None:
    main, worktree = repo
    write(worktree / SRC / "notes.txt", b"x")
    write(worktree / SRC / "new.php", b"<?php\n")
    assert not cwi.untracked_images(worktree)
    assert cwi.main([str(worktree)]) == 0
    assert not (main / SRC / "notes.txt").exists()
    assert not (main / SRC / "new.php").exists()


def test_dry_run_copies_nothing(repo: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    main, worktree = repo
    write(worktree / SRC / "a.png", b"png")
    assert cwi.main([str(worktree), "--dry-run"]) == 0
    assert not (main / SRC / "a.png").exists()
    assert f"would copy: {SRC}/a.png" in capsys.readouterr().out


def test_same_size_image_is_skipped(repo: tuple[Path, Path]) -> None:
    main, worktree = repo
    write(worktree / SRC / "a.png", b"new")
    write(main / SRC / "a.png", b"old")
    assert cwi.main([str(worktree)]) == 0
    assert (main / SRC / "a.png").read_bytes() == b"old"


def test_other_size_is_reported_and_left_alone(repo: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    main, worktree = repo
    write(worktree / SRC / "a.png", b"longer")
    write(main / SRC / "a.png", b"old")
    assert cwi.main([str(worktree)]) == 1
    assert (main / SRC / "a.png").read_bytes() == b"old"
    assert f"differs (not copied): {SRC}/a.png" in capsys.readouterr().out


def test_overwrite_replaces_an_image_of_another_size(repo: tuple[Path, Path]) -> None:
    main, worktree = repo
    write(worktree / SRC / "a.png", b"longer")
    write(main / SRC / "a.png", b"old")
    assert cwi.main([str(worktree), "--overwrite"]) == 0
    assert (main / SRC / "a.png").read_bytes() == b"longer"


def test_main_checkout_itself_is_refused(repo: tuple[Path, Path]) -> None:
    main, _ = repo
    assert cwi.main([str(main)]) == 2


def test_a_folder_outside_git_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert cwi.main([str(plain)]) == 2
