# version 0.0.1
"""Ruleset helpers: read <exclude-pattern> entries from phpcs.xml."""

from __future__ import annotations

import re

_EXCLUDE_RE = re.compile(r"<exclude-pattern>(.*?)</exclude-pattern>")


def ruleset_excludes(ruleset_file: str) -> list[str]:
    """Return <exclude-pattern> values from the shared ruleset."""
    try:
        with open(ruleset_file, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return []
    return _EXCLUDE_RE.findall(content)


def print_ruleset_excludes(ruleset_file: str) -> None:
    """Echo ruleset excludes (mirrors the per-run echo in lint-static.sh)."""
    print("excludes (phpcs.xml):")
    for entry in ruleset_excludes(ruleset_file):
        print(f"  - {entry}")


def stripped_ruleset(ruleset_file: str, dest_file: str) -> None:
    """Write a copy of the ruleset minus BasePage/ArticlePage excludes."""
    pattern = re.compile(r"<exclude-pattern>.*(BasePage|ArticlePage)\.php.*")
    with open(ruleset_file, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    with open(dest_file, "w", encoding="utf-8") as handle:
        for line in lines:
            if not pattern.search(line):
                handle.write(line)
