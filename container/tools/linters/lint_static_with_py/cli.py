# version 0.2.1
"""CLI parsing: mirrors the lint-static.sh flag surface."""

from __future__ import annotations

import sys

from .config import VALID_TOOLS, Config

USAGE = """\
Static analysis / code style for PHP via one-off Docker images.
Tools: phpstan (bugs, default), psalm (bugs), phpcs (style).

Usage:
  ./lint-static.py [--src DIR] [--tool phpstan|psalm|phpcs]... [--level N]
                   [--standard NAME] [--all] [--staged] [--committed]
                   [--files F ...] [paths...] [--report FILE]
                   [--worktree W] [-v|--verbose] [-h|--help]
  Fixing works only through phpcs, so --fix always comes with --tool phpcs:
  ./lint-static.py --tool phpcs --fix [--fix-page] [--no-failure-report]
                   [--worktree W]
                   <--files F ... | paths... | --staged | --committed | --all>
  Fix the files the last commit check reported:
  ./lint-static.py --fix-failed[=LIST] [--worktree W]

Failed list of the commit check:
  Every phpcs check with --staged saves the paths of the files that failed
  to container/log/phpcs-latest-commit-failed-list.log, one src/... path
  per line, paths only. A --staged check that finds nothing removes the
  list. Another place: PHPCS_FAILED_LIST=FILE.
  --fix-failed    fix only the files named in that list: the same run as
                  --tool phpcs --fix --files <the paths in the list>. No
                  question is asked and no other selection may be given.
                  Afterwards the list holds the files that still fail, or
                  is removed when none do. Fixed files are not staged.
                  No list or an empty one: nothing to fix, exit code 0.
  --fix-failed=LIST  read (and afterwards update) another list file.

Another worktree:
  --worktree W    lint (and with --fix, fix) the files of another git
                  worktree instead of this checkout. W is a path (absolute
                  or relative to where you start the script), the folder
                  name of a worktree or its branch, as `git worktree list`
                  shows them. Every selection then reads that worktree, and
                  its own phpcs.xml and phpstan.neon are used.

Fixing:
  --fix           needs --tool phpcs. Without it the script says that --tool
                  is needed, prints a command for every tool, and then offers
                  the fixer that fits the selected files, asking Y or n
                  (Enter = Y): phpcbf for .php, ruff for .py. With both kinds
                  selected two questions are asked. Without a terminal
                  nothing is asked and the script stops with exit code 2.

Output:
  -v, --verbose   print every file the script sees before the tools start
                  ("files seen (N):", one path per line; a folder is listed
                  as the .php files in it), then verbose tool output.

Full manual: container/docs/lint-static-py.md

Exit: 0 = all selected tools clean, 1 = findings (or setup failure: 1/2).

Examples (run from container/; paths are relative to src/):
  Check, default tool phpstan:
    ./tools/linters/lint-static.py
    ./tools/linters/lint-static.py --all
    ./tools/linters/lint-static.py section/index.php
    ./tools/linters/lint-static.py --files section/.php/BasePage.php
    ./tools/linters/lint-static.py --files section section/php
    ./tools/linters/lint-static.py --files=section/index.php,git/index.php
    ./tools/linters/lint-static.py --staged
    ./tools/linters/lint-static.py --committed
    ./tools/linters/lint-static.py --staged --files section
  Pick the tool:
    ./tools/linters/lint-static.py --tool phpstan --level 2 --files section
    ./tools/linters/lint-static.py --tool psalm --staged
    ./tools/linters/lint-static.py --tool phpcs --files section
    ./tools/linters/lint-static.py --tool phpcs --standard PSR12 --staged
    ./tools/linters/lint-static.py --tool phpstan,phpcs --level 2 --all
    ./tools/linters/lint-static.py --tool phpstan --tool psalm --staged
  Fix style in place (phpcbf):
    ./tools/linters/lint-static.py --tool phpcs --fix --files section/packages
    ./tools/linters/lint-static.py --tool phpcs --fix section/index.php
    ./tools/linters/lint-static.py --tool phpcs --fix --staged
    ./tools/linters/lint-static.py --tool phpcs --fix --all
    ./tools/linters/lint-static.py --tool phpcs --fix --fix-page --files section/.php
    ./tools/linters/lint-static.py --tool phpcs --fix --no-failure-report --staged
    FAILED_TO_FIX_LOG=reports ./tools/linters/lint-static.py --tool phpcs --fix --all
  Fix what the last commit check reported (list in log/):
    ./tools/linters/lint-static.py --fix-failed
    ./tools/linters/lint-static.py -v --fix-failed
    ./tools/linters/lint-static.py --fix-failed=log/my-list.log
    ./tools/linters/lint-static.py --worktree agent/example --fix-failed
    PHPCS_FAILED_LIST=/tmp/failed.log ./tools/linters/lint-static.py --tool phpcs --staged
  --fix without --tool phpcs: the script explains and asks Y or n:
    ./tools/linters/lint-static.py --fix section/index.php
    ./tools/linters/lint-static.py --fix --staged
    ./tools/linters/lint-static.py --fix tools/example.py
    ./tools/linters/lint-static.py --fix section/index.php tools/example.py
    ./tools/linters/lint-static.py --fix section/python
  Another worktree (path, folder name or branch):
    ./tools/linters/lint-static.py --worktree ~/github/my_site-agent-example --staged
    ./tools/linters/lint-static.py --worktree my_site-agent-example --tool phpcs --files section
    ./tools/linters/lint-static.py --worktree agent/example --tool phpcs --fix --staged
    ./tools/linters/lint-static.py --worktree ../my_site-agent-example --tool phpcs --fix section/functions.php
    ./tools/linters/lint-static.py --worktree agent/example --fix tools/example.py
  Report and output:
    ./tools/linters/lint-static.py --tool phpcs --all --report phpcs-all
    ./tools/linters/lint-static.py --tool psalm --files section --report /tmp/psalm.txt
    ./tools/linters/lint-static.py -v --tool phpstan --files section
    ./tools/linters/lint-static.py -v --tool phpcs --staged
    ./tools/linters/lint-static.py --verbose --worktree agent/example --tool phpcs --all
    ./tools/linters/lint-static.py --src src/section --tool phpstan
    PHP_ENGINE_STATIC_PHP_IMAGE=php:8.4-cli ./tools/linters/lint-static.py --staged
    ./tools/linters/lint-static.py --help
"""


def split_comma_list(value: str) -> list[str]:
    """Split a comma-separated flag value, trimming whitespace."""
    return [p.strip() for p in value.split(",") if p.strip()]


def add_tools(cfg: Config, value: str) -> None:
    """Append validated tool names from a comma list onto cfg.tools."""
    for part in split_comma_list(value):
        if part not in VALID_TOOLS:
            raise ValueError(f"unknown --tool '{part}' (want phpstan|psalm|phpcs).")
        cfg.tools.append(part)


def dedupe_tools(cfg: Config) -> None:
    """Dedupe cfg.tools, preserving order."""
    seen: list[str] = []
    for tool in cfg.tools:
        if tool not in seen:
            seen.append(tool)
    cfg.tools = seen


def parse_args(argv: list[str]) -> Config:
    """Parse argv (without program name) into a Config.

    Raises ValueError on usage errors; handles -h/--help via SystemExit(0).
    """
    # pylint: disable=too-many-statements,too-many-branches
    cfg = Config()
    cfg.tools = []
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--src":
            if i + 1 >= len(args):
                raise ValueError("ERROR: --src needs a value.")
            cfg.src_dir = args[i + 1]
            cfg.src_set = True
            i += 2
        elif arg.startswith("--src="):
            cfg.src_dir = arg.split("=", 1)[1]
            cfg.src_set = True
            i += 1
        elif arg == "--tool":
            if i + 1 >= len(args):
                raise ValueError("ERROR: --tool needs a value.")
            add_tools(cfg, args[i + 1])
            i += 2
        elif arg.startswith("--tool="):
            add_tools(cfg, arg.split("=", 1)[1])
            i += 1
        elif arg == "--level":
            if i + 1 >= len(args):
                raise ValueError("ERROR: --level needs a value (0-9).")
            cfg.level = args[i + 1]
            i += 2
        elif arg.startswith("--level="):
            cfg.level = arg.split("=", 1)[1]
            i += 1
        elif arg == "--standard":
            if i + 1 >= len(args):
                raise ValueError("ERROR: --standard needs a value.")
            cfg.standard = args[i + 1]
            cfg.standard_set = True
            i += 2
        elif arg.startswith("--standard="):
            cfg.standard = arg.split("=", 1)[1]
            cfg.standard_set = True
            i += 1
        elif arg == "--report":
            if i + 1 >= len(args):
                raise ValueError("ERROR: --report needs a value.")
            cfg.report = args[i + 1]
            i += 2
        elif arg.startswith("--report="):
            cfg.report = arg.split("=", 1)[1]
            i += 1
        elif arg == "--all":
            cfg.all = True
            i += 1
        elif arg == "--staged":
            cfg.staged = True
            i += 1
        elif arg in ("--committed", "--commited"):
            cfg.committed = True
            i += 1
        elif arg == "--files":
            i += 1
            got = False
            while i < len(args) and not args[i].startswith("-"):
                cfg.files.append(args[i])
                got = True
                i += 1
            if not got:
                raise ValueError("ERROR: --files needs at least one file.")
        elif arg.startswith("--files="):
            value = arg.split("=", 1)[1]
            if not value:
                raise ValueError("ERROR: --files needs at least one file.")
            parts = split_comma_list(value)
            if not parts:
                raise ValueError("ERROR: --files needs at least one file.")
            cfg.files.extend(parts)
            i += 1
        elif arg == "--worktree":
            if i + 1 >= len(args):
                raise ValueError("ERROR: --worktree needs a path, folder name or branch.")
            cfg.worktree = args[i + 1]
            i += 2
        elif arg.startswith("--worktree="):
            cfg.worktree = arg.split("=", 1)[1]
            if not cfg.worktree:
                raise ValueError("ERROR: --worktree needs a path, folder name or branch.")
            i += 1
        elif arg == "--fix":
            cfg.fix = True
            i += 1
        elif arg == "--fix-failed":
            cfg.fix_failed = True
            i += 1
        elif arg.startswith("--fix-failed="):
            cfg.fix_failed = True
            cfg.failed_list = arg.split("=", 1)[1]
            if not cfg.failed_list:
                raise ValueError("ERROR: --fix-failed= needs a list file.")
            i += 1
        elif arg == "--fix-page":
            cfg.fix_page = True
            i += 1
        elif arg == "--no-failure-report":
            cfg.no_failure_report = True
            i += 1
        elif arg in ("-v", "--verbose"):
            cfg.verbose = True
            i += 1
        elif arg in ("-h", "--help"):
            print_usage()
            raise SystemExit(0)
        elif arg.startswith("-"):
            raise ValueError(f"ERROR: unknown option: {arg} (see --help).")
        else:
            cfg.positional.append(arg)
            i += 1

    cfg.tools_given = bool(cfg.tools)
    if not cfg.tools:
        cfg.tools = ["phpstan"]
    # Tools given explicitly replace the default; an empty list means the
    # caller never passed --tool, so keep the phpstan default.
    dedupe_tools(cfg)
    return cfg


def print_usage() -> None:
    """Print the usage; the examples come last."""
    sys.stdout.write(USAGE)
