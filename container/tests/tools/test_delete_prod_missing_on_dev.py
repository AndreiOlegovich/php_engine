# version 0.1.0
"""Tests for tools/deploy/on_hosting/delete-prod-missing-on-dev.sh on temp trees.

The script and its helper delete-from-hosting.sh are copied into tmp_path, so
the delete_candidates/ lists they write never land in the repository.
"""

import os
import shutil
import subprocess
from pathlib import Path

ON_HOSTING = Path(__file__).resolve().parents[2] / "tools" / "deploy" / "on_hosting"


def _tree(root: Path, files: list[str]) -> None:
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel, encoding="utf-8")


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("delete-prod-missing-on-dev.sh", "delete-from-hosting.sh"):
        shutil.copy2(ON_HOSTING / name, bin_dir / name)
    dev, prod = tmp_path / "dev" / "public_html", tmp_path / "prod" / "public_html"
    shared = ["index.php", ".htaccess", "qa/page.php"]
    _tree(dev, shared)
    _tree(
        prod,
        [
            *shared,
            "qa/old.php",
            "gone/deep/page.php",
            "services/user_profile/data/visits.jsonl",
            "PHP_errors.log",
            "google0123abcd.html",
            "promo/banner.png",
        ],
    )
    return bin_dir, dev, prod


def _run(bin_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in ("SITE_ENV", "DEV_DOCROOT", "PROD_DOCROOT")}
    return subprocess.run(
        ["bash", str(bin_dir / "delete-prod-missing-on-dev.sh"), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )


def test_dry_run_lists_candidates_and_deletes_nothing(tmp_path):
    bin_dir, dev, prod = _setup(tmp_path)
    result = _run(bin_dir, "--dry-run", str(dev), str(prod))
    assert result.returncode == 0, result.stdout + result.stderr
    candidates = sorted(line.removeprefix("[candidate] ") for line in result.stdout.splitlines() if line.startswith("[candidate] "))
    assert candidates == ["gone/deep/page.php", "promo/banner.png", "qa/old.php"]
    assert "[folder] gone/deep/ " in result.stdout and "[folder] gone/ " in result.stdout
    assert "services/user_profile/data/*" in result.stdout and "(dry run: nothing deleted)" in result.stdout
    lists = list((bin_dir / "delete_candidates").glob("*.txt"))
    assert len(lists) == 1
    assert [line for line in lists[0].read_text(encoding="utf-8").splitlines() if not line.startswith("#")] == [f"{prod}/{rel}" for rel in candidates]
    assert (prod / "qa" / "old.php").is_file() and (prod / "gone" / "deep" / "page.php").is_file()


def test_real_run_backs_up_deletes_and_removes_empty_folders(tmp_path):
    bin_dir, dev, prod = _setup(tmp_path)
    keep = tmp_path / "keep.txt"
    keep.write_text("# promo stays on prod\npromo/*\n", encoding="utf-8")
    result = _run(bin_dir, "--exclude-file", str(keep), str(dev), str(prod))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (prod / "qa" / "old.php").exists() and not (prod / "gone").exists()
    backup = tmp_path / "prod" / "backup_deleted_html"
    assert (backup / "qa" / "old.php").read_text(encoding="utf-8") == "qa/old.php"
    assert (backup / "gone" / "deep" / "page.php").is_file()
    for rel in ("promo/banner.png", "services/user_profile/data/visits.jsonl", "PHP_errors.log", "google0123abcd.html", "qa/page.php", "index.php"):
        assert (prod / rel).is_file(), rel
    assert "[removed-folder] gone/deep/" in result.stdout and "[removed-folder] gone/" in result.stdout


def test_real_run_over_max_deletes_nothing(tmp_path):
    bin_dir, dev, prod = _setup(tmp_path)
    result = _run(bin_dir, "--max", "2", str(dev), str(prod))
    assert result.returncode == 1
    assert "3 candidates exceed --max 2" in result.stderr
    assert (prod / "qa" / "old.php").is_file()


def test_refuses_a_dev_tree_without_front_page(tmp_path):
    bin_dir, dev, prod = _setup(tmp_path)
    (dev / "index.php").unlink()
    result = _run(bin_dir, "--dry-run", str(dev), str(prod))
    assert result.returncode == 1
    assert "refusing to compare" in result.stderr


def test_refuses_nested_docroots(tmp_path):
    bin_dir, dev, _prod = _setup(tmp_path)
    nested = dev / "qa"
    result = _run(bin_dir, "--dry-run", str(dev), str(nested))
    assert result.returncode == 1
    assert "inside it" in result.stderr
