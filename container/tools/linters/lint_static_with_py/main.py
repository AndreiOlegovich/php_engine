# version 0.2.1
"""Orchestration: resolve paths, set up env, run tools, exit code."""

from __future__ import annotations

import os
import subprocess
import sys

from .cli import parse_args
from .config import Config, validate_config
from .docker import ensure_docker_image, ensure_tools, print_tool_versions
from .fix_offer import offer_fix
from .paths import print_seen_files, resolve_paths
from .report import failed_list_path, hint_cmd, load_failed_list, setup_report, start_report
from .runners import run_tool
from .worktree import resolve_worktree


def resolve_runtime_dirs(cfg: Config, script_dir: str, start_dir: str) -> None:
    """Fill cfg with absolute dirs (mirrors the cd logic in lint-static.sh)."""
    cfg.start_dir = start_dir
    cfg.script_dir = script_dir
    cfg.own_root = os.path.realpath(os.path.join(script_dir, "..", ".."))
    cfg.app_root = cfg.own_root
    if cfg.worktree:
        cfg.app_root = resolve_worktree(cfg.worktree, start_dir, cfg.own_root)
        print(f"worktree: {cfg.app_root}")
    cfg.src_abs = os.path.realpath(os.path.join(cfg.app_root, cfg.src_dir))
    cfg.ruleset_file = os.path.join(cfg.app_root, "phpcs.xml")


def apply_fix_failed(cfg: Config) -> int | None:
    """--fix-failed: select the files of the failed list for a phpcs fix run.

    Returns None to go on, else the exit code to stop with.
    """
    if cfg.all or cfg.staged or cfg.committed or cfg.files or cfg.positional:
        raise ValueError("ERROR: --fix-failed takes its files from the failed list only; drop the other selection.")
    path = failed_list_path(cfg)
    listed = load_failed_list(path)
    if not listed:
        print(f"no failed list at {path}: nothing to fix.")
        print("A phpcs check with --staged (the commit hook) writes it when files fail.")
        print("The hook of a checkout whose lint-static.sh is older than 0.2.0 never writes it.")
        print(f"  write the list now:        {hint_cmd(cfg)} --tool phpcs --staged")
        print(f"  or check and fix in one:   {hint_cmd(cfg)} --tool phpcs --fix --staged")
        return 0
    print(f"failed list: {path} ({len(listed)} file(s) to fix)")
    cfg.files = listed
    cfg.fix = True
    if not cfg.tools_given:
        cfg.tools = ["phpcs"]
    elif "phpcs" not in cfg.tools:
        cfg.tools.append("phpcs")
    return None


def check_source_dir(cfg: Config) -> None:
    """Raise when the source dir is missing."""
    if not os.path.isdir(os.path.join(cfg.app_root, cfg.src_dir)):
        raise FileNotFoundError(f"source dir '{cfg.src_dir}' not found.")


def run_all_tools(cfg: Config, paths: list[str]) -> int:
    """Run each selected tool; return 0 when all are clean, else 1."""
    print(f"analyzing {len(paths)} path(s) under {cfg.src_dir}/ ...")
    overall = cfg.ruff_rc
    for tool in cfg.tools:
        rc = run_tool(cfg, tool, paths)
        if rc != 0:
            overall = 1
    if overall == 0:
        print("OK: all selected tools clean.")
    else:
        print("DONE: findings above.")
    return overall


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse, validate, resolve, run. Returns exit code."""
    # pylint: disable=too-many-return-statements
    start_dir = os.path.realpath(os.getcwd())
    script_dir = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    # Package lives in tools/linters/lint_static_with_py/; runtime cwd is container/.
    script_dir = os.path.realpath(os.path.join(script_dir, ".."))
    # Above: script_dir now points at tools/linters/ (lint-static.sh does cd ../..
    # to the project root from tools/linters/). Keep the tools dir for git commands.
    args = sys.argv[1:] if argv is None else argv
    try:
        cfg = parse_args(args)
        resolve_runtime_dirs(cfg, script_dir, start_dir)
        if cfg.fix_failed:
            stop = apply_fix_failed(cfg)
            if stop is not None:
                return stop
        if cfg.fix and "phpcs" not in cfg.tools:
            stop = offer_fix(cfg)
            if stop is not None:
                return stop
        validate_config(cfg)
        check_source_dir(cfg)
        paths = resolve_paths(cfg)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not paths:
        print("nothing to check.")
        return 0

    try:
        report_path = setup_report(cfg)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    tee = start_report(report_path)

    try:
        if cfg.verbose:
            print_seen_files(cfg.app_root, paths, "php")
        print(f"runtime: {cfg.php_image}")
        ensure_docker_image(cfg.php_image)
        ensure_tools(cfg)
        print_tool_versions(cfg)
        return run_all_tools(cfg, paths)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: command failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if tee is not None:
            tee.close()
