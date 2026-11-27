#!/usr/bin/env python3
# version 0.1.1
"""Copy the new images of a worktree into the main checkout.

Images are git-ignored (.gitignore, "Images"), so a commit and a merge carry
the pages that use an image but never the image itself. A worker that adds
banners or pictures in its worktree leaves them there; the deploy uploads the
images of the main checkout, and the site gets 404s. Run this after the merge:

  python3 container/tools/ai/copy_worktree_images.py ../<project>-agent-<task> --dry-run
  python3 container/tools/ai/copy_worktree_images.py ../<project>-agent-<task>

It lists the untracked files under container/src of the worktree (ignored
ones included), keeps the image extensions of .gitignore in any letter case,
and copies each one the main checkout lacks to the same path there. Only the
docroot is read: a .venv or node_modules folder of the worktree may hold
images of its own. The main checkout is found from the worktree's shared .git
directory; --main gives it explicitly.

An image that exists in both with the same size is skipped. One with another
size is listed as "differs" and left alone, unless --overwrite is given.
The next commit in the main checkout gets container/docs/image-files.txt
rewritten by the image-list hook; stage it with that commit.

Exit 0 when done, 1 when an image differs and was not copied (decide, then
run again with --overwrite), 2 on bad arguments or a path that is no worktree.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# The image patterns of .gitignore, section "Images" (any letter case), the
# same set as container/tools/images/image_list.py.
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif", ".tiff", ".tif"})

# The docroot, relative to the checkout root: the only place images are read.
DOCROOT = "container/src"


def git(cwd: Path, *args: str) -> str:
    """Run git in cwd and return its stdout; raise on failure."""
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def main_checkout(worktree: Path) -> Path:
    """The main checkout of a worktree: the parent of the shared .git folder."""
    common = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    return common.parent


def untracked_images(worktree: Path) -> list[str]:
    """Untracked image files under the docroot of the worktree, ignored ones included, sorted."""
    out = subprocess.run(["git", "-C", str(worktree), "ls-files", "-z", "--others", "--", DOCROOT], check=True, capture_output=True).stdout
    paths = [p.decode() for p in out.split(b"\0") if p]
    return sorted(p for p in paths if Path(p).suffix.lower() in IMAGE_EXTENSIONS)


def copy_images(worktree: Path, main: Path, *, dry_run: bool = False, overwrite: bool = False) -> dict[str, list[str]]:
    """Copy the new images of worktree to main; return the paths per outcome."""
    result: dict[str, list[str]] = {"copied": [], "same": [], "differs": []}
    for rel in untracked_images(worktree):
        src = worktree / rel
        dst = main / rel
        if dst.exists():
            if dst.stat().st_size == src.stat().st_size:
                result["same"].append(rel)
                continue
            if not overwrite:
                result["differs"].append(rel)
                continue
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        result["copied"].append(rel)
    return result


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Command line: the worktree, --main, --dry-run, --overwrite."""
    parser = argparse.ArgumentParser(description="Copy the new (git-ignored) images of a worktree into the main checkout.")
    parser.add_argument("worktree", type=Path, help="the worktree, e.g. ../<project>-agent-<task>")
    parser.add_argument("--main", type=Path, help="the main checkout (default: found from the worktree)")
    parser.add_argument("--dry-run", action="store_true", help="list what would be copied, copy nothing")
    parser.add_argument("--overwrite", action="store_true", help="also replace images of another size in the main checkout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point; see the module docstring for the exit codes."""
    args = parse_args(argv)
    worktree = args.worktree.resolve()
    try:
        main_dir = args.main.resolve() if args.main else main_checkout(worktree)
    except subprocess.CalledProcessError, FileNotFoundError:
        print(f"ERROR: {worktree} is not a git worktree.", file=sys.stderr)
        return 2
    if main_dir == worktree:
        print(f"ERROR: {worktree} is the main checkout itself; give a worktree.", file=sys.stderr)
        return 2

    result = copy_images(worktree, main_dir, dry_run=args.dry_run, overwrite=args.overwrite)
    verb = "would copy" if args.dry_run else "copied"
    for rel in result["copied"]:
        print(f"{verb}: {rel}")
    for rel in result["differs"]:
        print(f"differs (not copied): {rel}")
    print(f"{worktree} -> {main_dir}: {len(result['copied'])} {verb}, {len(result['same'])} already there, {len(result['differs'])} differ")
    return 1 if result["differs"] else 0


if __name__ == "__main__":
    sys.exit(main())
