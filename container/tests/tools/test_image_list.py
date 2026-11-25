# version 0.1.0
"""Tests for tools/images/image_list.py on small tmp_path trees.

No real images are needed: the files are a few bytes and only their name,
size and sha1 matter.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS / "images"))

import image_list as il  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SCRIPT = TOOLS / "images" / "image_list.py"
FAKE = "0" * 40


def _image(path: Path, content: bytes) -> Path:
    """Create an image file (any bytes will do) and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _tree(tmp_path: Path) -> Path:
    """A small src tree: two folders, an upper-case suffix, one non-image."""
    src = tmp_path / "src"
    _image(src / "b.png", b"second")
    _image(src / "aa" / "c.gif", b"third")
    _image(src / "aa" / "a.JPG", b"first")
    _image(src / "notes.txt", b"not an image")
    return src


def _run(tmp_path: Path, *flags: str) -> int:
    """Run main() against the tmp_path tree; the cache never leaves tmp_path."""
    return il.main(["--src", str(tmp_path / "src"), "--out", str(tmp_path / "image-files.txt"), "--cache", str(tmp_path / "cache.tsv"), *flags])


def _seed_cache(tmp_path: Path, rel: str, sha1: str, delta: int = 0) -> Path:
    """A cache with one entry for `rel`, size and mtime from disk unless `delta`."""
    stat = (tmp_path / "src" / rel).stat()
    cache = tmp_path / "cache.tsv"
    cache.write_text(f"{rel}\t{stat.st_size + delta}\t{stat.st_mtime_ns}\t{sha1}\n", encoding="utf-8")
    return cache


def test_write_lists_images_sorted_by_path(tmp_path):
    _tree(tmp_path)
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n") and "\r" not in text
    lines = text.splitlines()
    assert lines[:2] == il.HEADER.splitlines()
    assert [line.split(maxsplit=2)[2] for line in lines[2:]] == ["aa/a.JPG", "aa/c.gif", "b.png"]
    assert lines[2] == f"{hashlib.sha1(b'first').hexdigest()}  5  aa/a.JPG"
    assert lines[3] == f"{hashlib.sha1(b'third').hexdigest()}  5  aa/c.gif"


def test_upper_case_suffix_and_a_path_with_a_space(tmp_path):
    src = tmp_path / "src"
    _image(src / "Photo.JPEG", b"upper")
    _image(src / "img" / "two words.png", b"space")
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    entries = il.load_list(out)
    assert set(entries) == {"Photo.JPEG", "img/two words.png"}
    spaced = [line for line in out.read_text(encoding="utf-8").splitlines() if line.endswith("img/two words.png")]
    assert len(spaced) == 1 and spaced[0].split(maxsplit=2) == [hashlib.sha1(b"space").hexdigest(), "5", "img/two words.png"]


def test_check_flags_a_stale_list_and_leaves_it_alone(tmp_path):
    _tree(tmp_path)
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    before = out.read_bytes()
    _image(tmp_path / "src" / "b.png", b"changed content")
    assert _run(tmp_path, "--check") == 1
    assert out.read_bytes() == before


def test_write_then_check_is_clean(tmp_path):
    _tree(tmp_path)
    assert _run(tmp_path, "--write") == 0
    assert _run(tmp_path, "--check") == 0


def test_check_without_a_list_is_stale(tmp_path):
    _tree(tmp_path)
    assert _run(tmp_path, "--check") == 1
    assert not (tmp_path / "image-files.txt").exists()


def test_symlinks_are_not_listed(tmp_path):
    src = tmp_path / "src"
    _image(src / "real.png", b"real")
    try:
        (src / "link.png").symlink_to(src / "real.png")
    except OSError:  # pragma: no cover - a filesystem without symlinks
        pytest.skip("no symlink support here")
    assert _run(tmp_path, "--write") == 0
    assert set(il.load_list(tmp_path / "image-files.txt")) == {"real.png"}


def test_hook_skips_a_checkout_without_images(tmp_path, capsys):
    src = _tree(tmp_path)
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    before = out.read_bytes()
    for rel in ("aa/a.JPG", "aa/c.gif", "b.png"):
        (src / rel).unlink()
    assert _run(tmp_path, "--hook") == 0
    assert out.read_bytes() == before
    assert "no images" in capsys.readouterr().out


def test_hook_rewrites_when_exactly_half_of_the_images_exist(tmp_path):
    src = _tree(tmp_path)
    _image(src / "aa" / "d.webp", b"fourth")
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    (src / "aa" / "a.JPG").unlink()
    (src / "b.png").unlink()
    assert _run(tmp_path, "--hook") == 1
    assert set(il.load_list(out)) == {"aa/c.gif", "aa/d.webp"}


def test_hook_rewrites_and_fails_the_commit(tmp_path, capsys):
    src = _tree(tmp_path)
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    _image(src / "new copy.PNG", b"new")
    assert _run(tmp_path, "--hook") == 1
    assert "new copy.PNG" in il.load_list(out)
    assert "wrote" in capsys.readouterr().out


def test_hook_passes_when_the_list_is_current(tmp_path, capsys):
    _tree(tmp_path)
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    before = out.read_bytes()
    capsys.readouterr()  # the --write output is not the hook's
    assert _run(tmp_path, "--hook") == 0
    assert out.read_bytes() == before
    assert "wrote" not in capsys.readouterr().out


def test_cache_reuses_a_sha1_when_size_and_mtime_match(tmp_path):
    _tree(tmp_path)
    _seed_cache(tmp_path, "b.png", FAKE)
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    assert il.load_list(out)["b.png"].sha1 == FAKE  # not recomputed
    assert "b.png\t" in (tmp_path / "cache.tsv").read_text(encoding="utf-8")


def test_cache_is_ignored_when_the_size_differs(tmp_path):
    _tree(tmp_path)
    _seed_cache(tmp_path, "b.png", FAKE, delta=1)
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write") == 0
    assert il.load_list(out)["b.png"].sha1 == hashlib.sha1(b"second").hexdigest()


def test_no_cache_hashes_every_file(tmp_path):
    _tree(tmp_path)
    _seed_cache(tmp_path, "b.png", FAKE)
    out = tmp_path / "image-files.txt"
    assert _run(tmp_path, "--write", "--no-cache") == 0
    assert il.load_list(out)["b.png"].sha1 == hashlib.sha1(b"second").hexdigest()


def test_cache_lists_every_image(tmp_path):
    _tree(tmp_path)
    assert _run(tmp_path, "--write") == 0
    rows = [line.split("\t") for line in (tmp_path / "cache.tsv").read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert [row[0] for row in rows] == ["aa/a.JPG", "aa/c.gif", "b.png"]
    assert all(len(row) == 4 and row[3] == il.load_list(tmp_path / "image-files.txt")[row[0]].sha1 for row in rows)


def test_bad_arguments_exit_2(tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        il.main(["--src", str(tmp_path / "src"), "--out", str(tmp_path / "list.txt")])
    assert exit_info.value.code == 2
    assert il.main(["--src", str(tmp_path / "missing"), "--out", str(tmp_path / "list.txt"), "--write"]) == 2


def test_cli_script_hook_exit_code(tmp_path):
    _tree(tmp_path)
    command = [sys.executable, str(SCRIPT), "--src", str(tmp_path / "src"), "--out", str(tmp_path / "list.txt"), "--cache", str(tmp_path / "cache.tsv")]
    assert subprocess.run([*command, "--write"], capture_output=True, text=True, check=False).returncode == 0
    assert subprocess.run([*command, "--hook"], capture_output=True, text=True, check=False).returncode == 0
    _image(tmp_path / "src" / "b.png", b"changed again")
    assert subprocess.run([*command, "--hook"], capture_output=True, text=True, check=False).returncode == 1
