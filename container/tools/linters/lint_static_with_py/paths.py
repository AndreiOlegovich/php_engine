# version 0.1.1
"""Path selection: explicit paths, git selections, whole-tree default."""

from __future__ import annotations

import os
import subprocess
import sys

from .config import Config


def normalize_token(cfg: Config, token: str) -> str | None:
    """Normalize one user-supplied path token to a src-relative path.

    Returns None when the token is outside the source tree (caller warns).
    """
    if not token:
        return None
    rel = token[2:] if token.startswith("./") else token
    if rel.startswith(cfg.src_dir + "/"):
        rel = rel[len(cfg.src_dir) + 1 :]
    elif rel.startswith("container/src/"):
        rel = rel[len("container/src/") :]
    if rel.startswith("/"):
        if rel.startswith(cfg.src_abs + "/"):
            rel = rel[len(cfg.src_abs) + 1 :]
        else:
            return None
    return rel


def resolve_explicit_paths(cfg: Config) -> list[str]:
    """Resolve --files + positionals to container paths (SRC_DIR/rel).

    Returns the PATHS list; warns on stderr for skipped tokens.
    """
    paths: list[str] = []
    for token in [*cfg.files, *cfg.positional]:
        if not token:
            continue
        rel = normalize_token(cfg, token)
        if rel is None:
            print(
                f"WARNING: skipping '{token}' (outside {cfg.src_dir}; hint: drop the leading slash).",
                file=sys.stderr,
            )
            continue
        abs_path = os.path.join(cfg.src_abs, rel)
        if os.path.exists(abs_path):
            paths.append(f"{cfg.src_dir}/{rel}")
        else:
            print(
                f"WARNING: skipping '{token}' (not found under {cfg.src_dir}).",
                file=sys.stderr,
            )
    return paths


def git_repo_top(script_dir: str) -> str:
    """Return the git toplevel for script_dir, or '' when not in a repo."""
    proc = subprocess.run(
        ["git", "-C", script_dir, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def git_name_list(args: list[str], cwd: str) -> list[str]:
    """Run a git file-listing command; return lines ('' on failure)."""
    proc = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    if proc.returncode != 0:
        return []
    # -z output split on NUL; fall back to newline split.
    raw = proc.stdout
    if "\x00" in raw:
        return [p for p in raw.split("\x00") if p]
    return [p for p in raw.splitlines() if p]


def resolve_git_paths(cfg: Config, paths: list[str]) -> list[str]:
    """Append --staged/--committed PHP files to paths (deduped)."""
    top = git_repo_top(cfg.app_root)
    if not top:
        raise ValueError("not inside a git repo (--staged/--committed need git).")
    prefix = os.path.relpath(cfg.src_abs, top)
    if prefix.startswith(".."):
        raise ValueError(f"{cfg.src_dir} is outside the git repo.")
    seen = set(paths)
    for want_staged, git_args in (
        (
            cfg.staged,
            [
                "git",
                "-C",
                cfg.app_root,
                "diff",
                "--cached",
                "--name-only",
                "-z",
                "--diff-filter=ACMR",
                "--",
                cfg.src_dir,
            ],
        ),
        (
            cfg.committed,
            [
                "git",
                "-C",
                cfg.app_root,
                "ls-files",
                "--full-name",
                "-z",
                "--",
                cfg.src_dir,
            ],
        ),
    ):
        if not want_staged:
            continue
        for entry in git_name_list(git_args, cfg.app_root):
            entry = entry.replace("\x00", "")
            if entry.startswith(prefix + "/"):
                entry = entry[len(prefix) + 1 :]
            if not entry.endswith(".php"):
                continue
            candidate = f"{cfg.src_dir}/{entry}"
            if candidate not in seen:
                seen.add(candidate)
                paths.append(candidate)
    return paths


PRUNED_DIRS = (".venv", "node_modules", ".git")


def list_seen_files(base: str, paths: list[str], ext: str) -> list[str]:
    """Files behind the paths (-v listing): a folder becomes its *.ext files.

    Paths are relative to base (or absolute) and are returned as given.
    """
    seen: set[str] = set()
    for path in paths:
        full = os.path.join(base, path)
        if not os.path.isdir(full):
            seen.add(path)
            continue
        for root, dirs, files in os.walk(full):
            dirs[:] = [d for d in dirs if d not in PRUNED_DIRS]
            for name in files:
                if name.endswith("." + ext):
                    seen.add(os.path.join(path, os.path.relpath(os.path.join(root, name), full)))
    return sorted(seen)


def print_seen_files(base: str, paths: list[str], ext: str) -> None:
    """Print the -v listing: 'files seen (N):' and one path per line."""
    seen = list_seen_files(base, paths, ext)
    print(f"files seen ({len(seen)}):")
    for path in seen:
        print(f"  {path}")


def resolve_paths(cfg: Config) -> list[str]:
    """Build the final PATHS list (explicit + git + whole-tree default)."""
    paths = resolve_explicit_paths(cfg)
    if cfg.staged or cfg.committed:
        paths = resolve_git_paths(cfg, paths)
    if cfg.all or (not cfg.files and not cfg.positional and not cfg.staged and not cfg.committed):
        paths = [cfg.src_dir]
    return paths
