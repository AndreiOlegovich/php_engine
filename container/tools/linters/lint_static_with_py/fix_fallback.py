# version 0.0.2
"""Conflict fallback for phpcbf --fix: per-family passes with monotonic guard.

One looping sniff family makes phpcbf discard the WHOLE file
("FAILED TO FIX"), wiping out every other fix. The fallback retries the
still-failing files family by family (safe whitespace first, structural
last) and keeps a family's changes per file only when that file's error
count drops, so one looping family can no longer discard every fix.

Runner callables are injected instead of imported, so this module
depends only on Config (no import cycle with runners).
"""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

from .config import Config

SAFE_SNIFFS = "PSR12.Operators.OperatorSpacing,Generic.PHP.LowerCaseConstant,Squiz.WhiteSpace.SuperfluousWhitespace"
"""Loop-free subset applied before the full pass."""

SNIFF_GROUPS: list[str] = [
    # Safe whitespace/spacing first, structural families last.
    "PSR12.Files.FileHeader,PSR12.Operators.OperatorSpacing,Squiz.WhiteSpace.SuperfluousWhitespace,Generic.Files.LineEndings,PSR2.Files.ClosingTag,PSR2.Files.EndFileNewLine,Generic.Functions.FunctionCallArgumentSpacing,Generic.PHP.LowerCaseConstant,PSR12.ControlStructures.ControlStructureSpacing,Squiz.WhiteSpace.ControlStructureSpacing,Squiz.Functions.FunctionDeclarationArgumentSpacing,Generic.Formatting.DisallowMultipleStatements,PSR2.ControlStructures.ElseIfDeclaration",
    "PSR2.Methods.FunctionCallSignature",
    "Squiz.ControlStructures.ControlSignature",
    "Generic.WhiteSpace.ScopeIndent",
    "Squiz.WhiteSpace.ScopeClosingBrace,PSR2.Methods.FunctionClosingBrace",
    "Squiz.Functions.MultiLineFunctionDeclaration",
]

# (targets, sniffs) -> exit code; sniffs is None for a full pass.
RunPhpcbf = Callable[[list[str], str | None], int]
# (targets) -> parsed phpcs --report=csv rows.
RunCsv = Callable[[list[str]], list[dict[str, str]]]


@dataclass
class FixRunners:
    """phpcbf/phpcs entry points injected by the caller."""

    run_phpcbf: RunPhpcbf
    run_csv: RunCsv


def parse_csv_messages(output: str) -> list[dict[str, str]]:
    """Parse phpcs --report=csv output into message rows.

    The captured output also carries the progress lines of phpcs
    (". 1 / 1 (100%)"); only rows whose File is a container path count.
    """
    lines = output.splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith("File,")), None)
    if header is None:
        return []
    rows = list(csv.DictReader(lines[header:]))
    return [row for row in rows if (row.get("File") or "").startswith("/")]


def error_counts(messages: list[dict[str, str]]) -> dict[str, int]:
    """Map container path (/app/...) to its message count."""
    counts: dict[str, int] = {}
    for row in messages:
        path = row["File"]
        counts[path] = counts.get(path, 0) + 1
    return counts


def failing_files(messages: list[dict[str, str]]) -> list[str]:
    """Sorted container paths with at least one message."""
    return sorted({row["File"] for row in messages})


def container_to_host(cfg: Config, container_path: str) -> str:
    """Map an /app/... path back to its host filesystem path."""
    rel = container_path[len("/app/") :] if container_path.startswith("/app/") else container_path
    return os.path.join(cfg.app_root, rel)


def backup_targets(cfg: Config, paths: list[str], backup_dir: str) -> None:
    """Snapshot host files of container paths into backup_dir."""
    for container_path in paths:
        host_path = container_to_host(cfg, container_path)
        dest = os.path.join(backup_dir, os.path.relpath(host_path, cfg.app_root))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(host_path, dest)


def restore_target(cfg: Config, container_path: str, backup_dir: str) -> None:
    """Restore one host file from its backup snapshot."""
    host_path = container_to_host(cfg, container_path)
    src = os.path.join(backup_dir, os.path.relpath(host_path, cfg.app_root))
    shutil.copy2(src, host_path)


def refresh_backup(cfg: Config, container_path: str, backup_dir: str) -> None:
    """Re-snapshot one host file (its new state is worth keeping)."""
    host_path = container_to_host(cfg, container_path)
    dest = os.path.join(backup_dir, os.path.relpath(host_path, cfg.app_root))
    shutil.copy2(host_path, dest)


def fix_with_fallback(cfg: Config, paths: list[str], runners: FixRunners) -> None:
    """Fix paths: safe-first pass, full pass, then per-family fallback.

    Modifies files in place. A family's changes are kept per file only
    when that file's error count drops (or hits zero); otherwise the
    pre-family snapshot is restored.
    """
    print("safe families first (never loop), then the full pass ...")
    runners.run_phpcbf(paths, SAFE_SNIFFS)
    runners.run_phpcbf(paths, None)
    messages = runners.run_csv(paths)
    failing = failing_files(messages)
    if not failing:
        return
    with tempfile.TemporaryDirectory(prefix="lint-fix-") as backup_dir:
        backup_targets(cfg, failing, backup_dir)
        for group in SNIFF_GROUPS:
            if not failing:
                break
            short = group.split(",", 1)[0]
            print(f"fallback: --sniffs={short}... ({len(failing)} file(s) still failing)")
            before = error_counts(messages)
            runners.run_phpcbf(failing, group)
            messages = runners.run_csv(paths)
            after = error_counts(messages)
            for path in failing:
                if after.get(path, 0) < before.get(path, 0) or after.get(path, 0) == 0:
                    refresh_backup(cfg, path, backup_dir)
                else:
                    restore_target(cfg, path, backup_dir)
            failing = failing_files(runners.run_csv(paths))
