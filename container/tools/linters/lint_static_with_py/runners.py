# version 0.1.0
"""Tool runners: phpstan, psalm, phpcs/phpcbf (+ phpcs re-check)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

from .config import Config
from .docker import docker_php_run
from .fix_fallback import FixRunners, fix_with_fallback, parse_csv_messages
from .report import parse_failed_files, save_failed_list, write_failure_report
from .ruleset import print_ruleset_excludes, stripped_ruleset

# NOTE: fix_fallback takes injected callables (FixRunners), so the import
# graph stays acyclic: fix_fallback -> config only.


def run_phpstan(cfg: Config, paths: list[str]) -> int:
    """Run phpstan analyse at the configured level; return exit code."""
    args = ["analyse"]
    if cfg.verbose:
        args.append("-v")
    args.extend(["--level", cfg.level, "--", *paths])
    return docker_php_run(cfg, "/tools/phpstan.phar", args)


def run_psalm(cfg: Config, paths: list[str]) -> int:
    """Run psalm with a generated minimal config; return exit code."""
    with tempfile.TemporaryDirectory() as psalm_cfg:
        with open(os.path.join(psalm_cfg, "psalm.xml"), "w", encoding="utf-8") as fh:
            fh.write(f'<?xml version="1.0"?>\n<psalm errorLevel="4" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n  xmlns="https://getpsalm.org/schema/config">\n  <projectFiles>\n    <directory name="/app/{cfg.src_dir}" />\n  </projectFiles>\n</psalm>\n')
        args = ["--no-cache", "--config=/psalmcfg/psalm.xml"]
        if cfg.verbose:
            args.append("--show-info=true")
        args.extend(["--", *paths])
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{cfg.app_root}:/app",
            "-w",
            "/app",
            "-v",
            f"{cfg.tool_cache}:/tools:ro",
            "-v",
            f"{psalm_cfg}:/psalmcfg:ro",
            cfg.php_image,
            "php",
            "/tools/psalm.phar",
            *args,
        ]
        proc = subprocess.run(cmd, check=False)
        return proc.returncode


def run_phpcbf(
    cfg: Config,
    paths: list[str],
    standard: str,
    extra_mounts: list[str] | None = None,
    sniffs: str | None = None,
) -> int:
    """Run phpcbf in place; return exit code (meaningless per lint-static.sh).

    When sniffs is given, only that comma-separated family runs
    (equals form: PHPCS rejects a space-separated --sniffs value).
    """
    args = ["--standard=" + standard]
    if sniffs:
        args.append("--sniffs=" + sniffs)
    args.append("-p")
    if cfg.verbose:
        args.append("-v")
    args.extend(["--", *paths])
    return docker_php_run(cfg, "/tools/phpcbf.phar", args, extra_mounts=extra_mounts)


def run_phpcs_check(
    cfg: Config,
    paths: list[str],
    standard: str,
    *,
    capture: bool = False,
    extra_mounts: list[str] | None = None,
    report: str | None = None,
) -> tuple[int, str]:
    """Run phpcs; return (exit code, combined output).

    When capture=False output streams to the terminal and the text is ''.
    When capture=True output is also echoed (mirrors `| tee` in the shell).
    When report is given, `--report=<name>` replaces the human report
    (used for machine-readable `csv` in the fix fallback).
    """
    args = ["--standard=" + standard, "-p"]
    if cfg.verbose:
        args.append("-v")
    if report:
        args.append("--report=" + report)
    args.extend(["--", *paths])
    if not capture:
        return docker_php_run(cfg, "/tools/phpcs.phar", args, extra_mounts=extra_mounts), ""
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{cfg.app_root}:/app",
        "-w",
        "/app",
        "-v",
        f"{cfg.tool_cache}:/tools:ro",
        *(extra_mounts or []),
        cfg.php_image,
        "php",
        "/tools/phpcs.phar",
        *args,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    sys.stdout.write(output)
    sys.stdout.flush()
    return proc.returncode, output


def prepare_fix_standard(cfg: Config) -> tuple[str, list[str], str]:
    """Resolve the effective standard + mounts for a --fix run.

    Returns (effective_standard, extra_mounts, temp_dir). Caller removes
    temp_dir when non-empty.
    """
    effective = cfg.standard
    extra_mounts: list[str] = []
    tmp_dir = ""
    if cfg.fix_page:
        if not cfg.standard_set:
            tmp_dir = tempfile.mkdtemp()
            dest = os.path.join(tmp_dir, "phpcs.xml")
            stripped_ruleset(cfg.ruleset_file, dest)
            effective = "/ruleset/phpcs.xml"
            extra_mounts = ["-v", f"{tmp_dir}:/ruleset:ro"]
            print("note: BasePage.php/ArticlePage.php INCLUDED via --fix-page.")
        else:
            print(
                "WARNING: custom --standard with --fix-page: that standard controls excludes.",
                file=sys.stderr,
            )
    return effective, extra_mounts, tmp_dir


def run_phpcs_tool(cfg: Config, paths: list[str]) -> int:
    """Run the phpcs tool: report-only, or phpcbf + phpcs re-check for --fix."""
    if cfg.standard == "/app/phpcs.xml" and os.path.isfile(cfg.ruleset_file):
        print_ruleset_excludes(cfg.ruleset_file)
    if not cfg.fix:
        # The commit check (--staged) keeps the paths of the failing files.
        rc, output = run_phpcs_check(cfg, paths, cfg.standard, capture=cfg.staged)
        if cfg.staged:
            save_failed_list(cfg, output)
        return rc

    print("fixing IN PLACE with phpcbf (review with git diff afterwards) ...")
    effective, extra_mounts, tmp_dir = prepare_fix_standard(cfg)
    try:
        # Safe-first pass, full pass, then per-family fallback on conflict.
        runners = FixRunners(
            run_phpcbf=lambda targets, sniffs: run_phpcbf(cfg, targets, effective, extra_mounts, sniffs),
            run_csv=lambda targets: parse_csv_messages(
                run_phpcs_check(
                    cfg,
                    targets,
                    effective,
                    capture=True,
                    extra_mounts=extra_mounts,
                    report="csv",
                )[1]
            ),
        )
        fix_with_fallback(cfg, paths, runners)
        print("re-checking with phpcs (files still failing go to $FAILED_TO_FIX_LOG/failed_to_fix_<timestamp>.log) ...")
        rc, output = run_phpcs_check(cfg, paths, effective, capture=True, extra_mounts=extra_mounts)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if cfg.no_failure_report:
        print("failure report skipped (--no-failure-report).")
    else:
        failed = parse_failed_files(output)
        write_failure_report(cfg, failed)
    if cfg.fix_failed:
        save_failed_list(cfg, output)
        print("fixed files are not staged: review with git diff, then git add them.")
    return rc


def run_tool(cfg: Config, tool: str, paths: list[str]) -> int:
    """Dispatch one tool run with timing; return its exit code."""
    print(f"==> {tool} ...")
    started = time.monotonic()
    try:
        if tool == "phpstan":
            return run_phpstan(cfg, paths)
        if tool == "psalm":
            return run_psalm(cfg, paths)
        if tool == "phpcs":
            return run_phpcs_tool(cfg, paths)
        raise ValueError(f"unknown tool '{tool}'.")
    finally:
        elapsed = int(time.monotonic() - started)
        print(f"  ({tool} took {elapsed}s)")
