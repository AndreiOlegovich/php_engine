# version 0.0.1
"""--worktree: find the container/ root of another git worktree."""

from __future__ import annotations

import os
import subprocess


def list_worktrees(own_root: str) -> list[tuple[str, str]]:
    """Return (path, branch) for every worktree of the repository ('' = detached)."""
    proc = subprocess.run(
        ["git", "-C", own_root, "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    found: list[tuple[str, str]] = []
    if proc.returncode != 0:
        return found
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            found.append((line[len("worktree ") :], ""))
        elif line.startswith("branch refs/heads/") and found:
            found[-1] = (found[-1][0], line[len("branch refs/heads/") :])
    return found


def find_worktree_dir(name: str, start_dir: str, own_root: str) -> str:
    """Return the directory a --worktree value means, or '' when unknown.

    A directory (absolute, or relative to the start folder) wins; otherwise
    the value is the folder name or the branch of a registered worktree.
    """
    path = os.path.expanduser(name)
    if not os.path.isabs(path):
        path = os.path.join(start_dir, path)
    if os.path.isdir(path):
        return path
    found = ""
    for wt_path, branch in list_worktrees(own_root):
        if name in (os.path.basename(wt_path), branch):
            found = wt_path
    return found


def resolve_worktree(name: str, start_dir: str, own_root: str) -> str:
    """Return the container/ root of the worktree; ValueError when not found."""
    cand = find_worktree_dir(name, start_dir, own_root)
    if not cand:
        listing = "\n".join(f"{path}  [{branch or 'detached'}]" for path, branch in list_worktrees(own_root))
        raise ValueError(f"ERROR: --worktree '{name}' is not a directory, a worktree folder name or a worktree branch.\nWorktrees of this repository:\n{listing}")
    for root in (os.path.join(cand, "container"), cand):
        if os.path.isdir(os.path.join(root, "src")):
            return os.path.realpath(root)
    raise ValueError(f"ERROR: --worktree '{name}': no container/src under {cand}.")
