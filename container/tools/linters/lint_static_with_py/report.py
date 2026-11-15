# version 0.1.1
"""Report helpers: --report mirroring and failed_to_fix logs."""

from __future__ import annotations

import contextlib
import datetime
import os
import re
import sys
import tempfile

from .config import FAILED_LIST_NAME, Config

_FILE_RE = re.compile(r"^FILE:\s*(.*\S)\s*$")


def normalize_report_path(report: str) -> str:
    """Append .txt when missing (mirrors lint-static.sh --report)."""
    return report if report.endswith(".txt") else report + ".txt"


def setup_report(cfg: Config) -> str:
    """Validate the --report target; return the normalized path (or '')."""
    if not cfg.report:
        return ""
    report = normalize_report_path(cfg.report)
    parent = os.path.dirname(report) or "."
    if not os.path.isdir(parent):
        raise ValueError(f"report dir '{parent}' not found.")
    if not os.access(parent, os.W_OK):
        raise ValueError(f"report dir '{parent}' not writable.")
    with contextlib.suppress(FileNotFoundError):
        os.remove(report)
    return report


class Tee:
    """Write to both stdout and a report file (terminal output unchanged).

    The file handle is intentionally long-lived (mirrors `exec > >(tee)` in
    the shell script); closed explicitly via close().
    """

    def __init__(self, report_path: str) -> None:
        self.report_path = report_path
        self._terminal = sys.stdout
        # Long-lived on purpose (mirrors `exec > >(tee)`); closed via close().
        self._file = open(report_path, "a", encoding="utf-8")  # noqa: SIM115 # pylint: disable=consider-using-with

    def write(self, data: str) -> int:
        self._terminal.write(data)
        self._file.write(data)
        return len(data)

    def flush(self) -> None:
        self._terminal.flush()
        self._file.flush()

    def close(self) -> None:
        try:
            self._file.close()
        finally:
            sys.stdout = self._terminal


def start_report(report_path: str) -> Tee | None:
    """Begin tee-ing stdout into report_path; return the Tee (or None)."""
    if not report_path:
        return None
    tee = Tee(report_path)
    sys.stdout = tee
    print(f"report: {report_path}")
    print(datetime.datetime.now(datetime.UTC).strftime("report started: %Y-%m-%dT%H:%M:%SZ"))
    return tee


def parse_failed_files(recheck_output: str) -> list[str]:
    """Extract sorted unique src-relative paths from phpcs FILE: lines."""
    found: list[str] = []
    for line in recheck_output.splitlines():
        match = _FILE_RE.match(line)
        if not match:
            continue
        path = match.group(1).strip()
        if path.startswith("/app/"):
            path = path[len("/app/") :]
        if path and path not in found:
            found.append(path)
    return sorted(found)


def failure_dir(cfg: Config) -> str:
    """Resolve FAILED_TO_FIX_LOG to an absolute directory."""
    if os.path.isabs(cfg.failed_to_fix_log):
        return cfg.failed_to_fix_log
    return os.path.join(cfg.start_dir, cfg.failed_to_fix_log)


def write_failure_report(cfg: Config, failed: list[str]) -> str | None:
    """Write failed paths to log/failed_to_fix_<UTC timestamp>.log.

    Returns the log path, or None when the list is empty.
    """
    if not failed:
        print("no failure report: nothing still failing.")
        return None
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    fail_dir = failure_dir(cfg)
    os.makedirs(fail_dir, exist_ok=True)
    fail_log = os.path.join(fail_dir, f"failed_to_fix_{timestamp}.log")
    with open(fail_log, "w", encoding="utf-8") as handle:
        handle.write("\n".join(failed) + "\n")
    print(f"failure report: {fail_log} ({len(failed)} file(s) still failing)")
    return fail_log


def failed_list_path(cfg: Config) -> str:
    """Path of the commit check's failed list (paths only, one src/... per line)."""
    if not cfg.failed_list:
        return os.path.join(cfg.app_root, "log", FAILED_LIST_NAME)
    if os.path.isabs(cfg.failed_list):
        return cfg.failed_list
    return os.path.join(cfg.start_dir, cfg.failed_list)


def hint_cmd(cfg: Config) -> str:
    """The script as a command for hints: the worktree in use goes with it."""
    return "./tools/linters/lint-static.py" + (f" --worktree {cfg.worktree}" if cfg.worktree else "")


def save_failed_list(cfg: Config, phpcs_output: str) -> None:
    """Keep the paths of the files phpcs reported; remove the list when none failed.

    The list is written to a temp file in the same folder and renamed.
    """
    path = failed_list_path(cfg)
    failed = parse_failed_files(phpcs_output)
    if failed:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(failed) + "\n")
        os.replace(tmp, path)
        print(f"failed list: {path} ({len(failed)} file(s))")
        flag = f"--fix-failed={path}" if cfg.failed_list else "--fix-failed"
        print(f"  fix them with: {hint_cmd(cfg)} {flag}")
    elif os.path.exists(path):
        os.remove(path)
        print(f"failed list removed (nothing failing): {path}")


def load_failed_list(path: str) -> list[str]:
    """Paths named in the failed list; [] when the file is missing or empty."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        lines = [line.strip() for line in handle]
    return [line for line in lines if line and not line.startswith("#")]
