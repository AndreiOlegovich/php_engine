# version 0.0.2
"""--fix without a tool that can fix: explain, then offer the fitting fixer.

.php targets get phpcbf (the fixer of phpcs), .py targets get ruff. With both
kinds selected two questions are asked, one per fixer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import termios
import tty
from dataclasses import dataclass, field

from .config import Config
from .paths import print_seen_files

SELF_CMD = "./tools/linters/lint-static.py"
PRUNED_DIRS = (".venv", "node_modules", ".git")
PHP_QUESTION = "PHP files are selected. Run the fix with phpcbf, the fixer of phpcs (same as --tool phpcs --fix)?"


@dataclass
class FixTargets:
    """Explicit --fix targets sorted by file type."""

    php_seen: bool = False
    keep: list[str] = field(default_factory=list)
    py_targets: list[str] = field(default_factory=list)
    n_explicit: int = 0


def fix_tool_help(cfg: Config) -> str:
    """Return the message printed when --fix comes without --tool phpcs."""
    cmd = SELF_CMD + (f" --worktree {cfg.worktree}" if cfg.worktree else "")
    return f"""--fix needs the --tool flag: only phpcs can fix (through phpcbf), and the
tools selected now ({" ".join(cfg.tools)}) only report.

Tools of this script, with a command for each:
  phpstan  bugs, report only
             {cmd} --tool phpstan --level 2 --files section
  psalm    bugs, report only
             {cmd} --tool psalm --staged
  phpcs    style, report
             {cmd} --tool phpcs --files section
  phpcs    style, fix in place with phpcbf
             {cmd} --tool phpcs --fix --files section
             {cmd} --tool phpcs --fix --staged
             {cmd} --tool phpcs --fix --all
Other file types:
  .py      ruff fixes
             ruff check --fix --config pyproject.toml tools/example.py
             ruff format --config pyproject.toml tools/example.py
  .sh      shellcheck, report only
             ./tools/linters/lint-sh.sh --files tools/generate-sitemap.sh
  .js      node --check, report only
             ./tools/linters/lint-js.sh --files section/game.js
"""


def ask_yn(question: str) -> bool:
    """Ask on the terminal; one key, Y or n (Enter = Y). No terminal: False."""
    if not sys.stdin.isatty():
        print("No terminal to ask on: nothing was fixed.", file=sys.stderr)
        return False
    sys.stdout.flush()
    sys.stderr.write(f"{question} [Y/n] ")
    sys.stderr.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        # TCSANOW keeps a key that was typed ahead of the question.
        tty.setcbreak(fd, termios.TCSANOW)
        key = os.read(fd, 1).decode("utf-8", errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
    sys.stderr.write((key if key.isprintable() else "") + "\n")
    sys.stderr.flush()
    return key in ("\n", "\r", "y", "Y")


def find_target(cfg: Config, token: str) -> str:
    """Return the existing path a token means, or ''.

    A .php token is src-relative like everywhere in this script; other files
    are also looked up from the start folder, the project root and the
    repository root.
    """
    rel = token[2:] if token.startswith("./") else token
    if os.path.isabs(rel):
        cands = [rel]
    else:
        cands = [] if cfg.worktree else [os.path.join(cfg.start_dir, rel)]
        cands += [
            os.path.join(cfg.app_root, cfg.src_dir, rel),
            os.path.join(cfg.app_root, rel),
            os.path.join(cfg.app_root, "..", rel),
        ]
    for cand in cands:
        if os.path.exists(cand):
            return cand
    return ""


def dir_has(path: str, ext: str) -> bool:
    """True when the folder holds a file with this extension (junk dirs pruned)."""
    for _root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in PRUNED_DIRS]
        if any(name.endswith("." + ext) for name in files):
            return True
    return False


def note_no_fixer(token: str, found: str) -> None:
    """Tell why a file gets no fix offer."""
    if found.endswith(".sh"):
        msg = "shell scripts have no fixer here; shellcheck only reports (./tools/linters/lint-sh.sh --files ...)."
    elif found.endswith(".js"):
        msg = "JavaScript has no fixer here; ./tools/linters/lint-js.sh only reports."
    else:
        msg = "no fixer known for this file type."
    print(f"NOTE: '{token}': {msg}", file=sys.stderr)


def classify_targets(cfg: Config) -> FixTargets:
    """Sort --files and positional targets into PHP (kept) and Python."""
    targets = FixTargets()
    for token in [*cfg.files, *cfg.positional]:
        if not token:
            continue
        targets.n_explicit += 1
        found = find_target(cfg, token)
        if not found:
            print(f"WARNING: '{token}' not found; skipped.", file=sys.stderr)
        elif os.path.isdir(found):
            has_php = dir_has(found, "php")
            has_py = dir_has(found, "py")
            if has_php:
                targets.php_seen = True
                targets.keep.append(token)
            if has_py:
                targets.py_targets.append(found)
            if not (has_php or has_py):
                print(f"NOTE: '{token}' holds no .php or .py files; nothing to fix there.", file=sys.stderr)
        elif found.endswith(".php"):
            targets.php_seen = True
            targets.keep.append(token)
        elif found.endswith(".py"):
            targets.py_targets.append(found)
        else:
            note_no_fixer(token, found)
    # --staged, --committed and --all select .php files only.
    if cfg.staged or cfg.committed or cfg.all or (cfg.src_set and targets.n_explicit == 0):
        targets.php_seen = True
    return targets


def find_ruff(cfg: Config) -> str:
    """Return the ruff to run: the project's venv first, then PATH; '' = none."""
    for root in (cfg.app_root, cfg.own_root):
        cand = os.path.join(root, ".venv", "bin", "ruff")
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which("ruff") or ""


def run_ruff(cfg: Config, targets: list[str]) -> int:
    """ruff check --fix, then ruff format, in place; 0 = clean."""
    ruff = find_ruff(cfg)
    if not ruff:
        print(f"ERROR: ruff not found (looked in .venv/bin and PATH); run: uv sync --project {cfg.own_root}", file=sys.stderr)
        return 1
    pyproject = os.path.join(cfg.app_root, "pyproject.toml")
    config_args = ["--config", pyproject] if os.path.isfile(pyproject) else []
    if cfg.verbose:
        print_seen_files(cfg.app_root, targets, "py")
    print("fixing IN PLACE with ruff (review with git diff afterwards) ...")
    rc = 0
    for label, args in (("ruff check --fix", ["check", "--fix"]), ("ruff format", ["format"])):
        print(f"==> {label} ...", flush=True)
        if subprocess.run([ruff, *args, *config_args, "--", *targets], check=False).returncode != 0:
            rc = 1
    return rc


def offer_fix(cfg: Config) -> int | None:
    """Explain the missing --tool phpcs and offer the fixers.

    Returns None when the phpcs fix run goes on (cfg.tools then holds phpcs),
    else the exit code to stop with. cfg.ruff_rc keeps the ruff result.
    """
    print(fix_tool_help(cfg), file=sys.stderr)
    targets = classify_targets(cfg)
    cfg.files = targets.keep
    cfg.positional = []
    ran_ruff = False
    if targets.php_seen and ask_yn(PHP_QUESTION):
        cfg.tools = [*cfg.tools, "phpcs"] if cfg.tools_given else ["phpcs"]
    if targets.py_targets:
        question = f"Python files are selected ({len(targets.py_targets)} path(s)). Run the fix with ruff (ruff check --fix, then ruff format)?"
        if ask_yn(question):
            ran_ruff = True
            cfg.ruff_rc = run_ruff(cfg, targets.py_targets)
    if "phpcs" in cfg.tools:
        return None
    if ran_ruff:
        print("OK: ruff clean." if cfg.ruff_rc == 0 else "DONE: ruff findings above.")
        return cfg.ruff_rc
    if not targets.php_seen and not targets.py_targets:
        print("ERROR: --fix needs --tool phpcs and a scope, e.g. --tool phpcs --fix --files section/packages", file=sys.stderr)
    else:
        print("ERROR: nothing was fixed: --fix needs --tool phpcs.", file=sys.stderr)
    return 2
