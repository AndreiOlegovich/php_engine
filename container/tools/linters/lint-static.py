#!/usr/bin/env python3
# version 0.1.0
"""Static analysis / code style for PHP via one-off Docker images.

Python port of lint-static.sh — same flags, same behavior, split into
focused modules under lint_static_with_py/ (config, cli, paths, worktree,
fix_offer, ruleset, docker, report, runners, main).

Usage:
  ./lint-static.py [--src DIR] [--tool phpstan|psalm|phpcs]... [--level N]
                   [--standard NAME] [--all] [--staged] [--committed]
                   [--files F ...] [paths...] [--report FILE] [--fix]
                   [--fix-page] [--no-failure-report] [--worktree W]
                   [-v|--verbose] [-h|--help]

--help prints every flag and the examples.

Exit: 0 = all selected tools clean, 1 = findings (or setup failure: 1/2).
"""

# pylint: disable=invalid-name
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lint_static_with_py.main import main  # pylint: disable=wrong-import-position

if __name__ == "__main__":
    raise SystemExit(main())
