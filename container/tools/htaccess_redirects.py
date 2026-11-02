"""Redirect rules of container/src/.htaccess, in either of their two forms.

Since 2026-10-22 every redirect is a mod_rewrite pair, so the target keeps
https behind the hosting proxy:

    RewriteCond %{REQUEST_URI} ^/old/(.*)$
    RewriteRule ^ %{ENV:SITE_SCHEME}://%{HTTP_HOST}/new/%1 [R=301,L]

The older mod_alias lines (Redirect 301 /old /new, RedirectMatch 301 ^/old$
/new) are still understood, so a stray old-style line is not missed. Each
rule becomes a regex over the full URL path, leading slash included.

Usage (any working directory):
  python3 container/tools/htaccess_redirects.py list [HTACCESS]
  python3 container/tools/htaccess_redirects.py match HTACCESS URL_PATH...
Exit for match: 0 none of the paths is redirected, 1 at least one is.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

HTACCESS = Path(__file__).resolve().parents[1] / "src" / ".htaccess"
SITE_PREFIX = "%{ENV:SITE_SCHEME}://%{HTTP_HOST}"

_ARG = r'("(?:[^"\\]|\\.)*"|\S+)'
_COND = re.compile(rf"^\s*RewriteCond\s+%{{REQUEST_URI}}\s+{_ARG}\s*$")
_RULE = re.compile(rf"^\s*RewriteRule\s+\^\s+{_ARG}\s+\[([^\]]*)\]\s*$")
_REDIRECT = re.compile(rf"^\s*Redirect\s+(\d{{3}})\s+{_ARG}\s+{_ARG}\s*$")
_REDIRECT_MATCH = re.compile(rf"^\s*RedirectMatch\s+(\d{{3}})\s+{_ARG}(?:\s+{_ARG})?\s*$")
_STATUS = re.compile(r"(?:^|,)\s*R=(\d{3})\s*(?:,|$)")


@dataclass(frozen=True)
class Rule:
    """One rule: the line it ends on, its URL-path regex, target and status."""

    line: int
    pattern: str
    target: str | None
    status: int

    @property
    def is_redirect(self) -> bool:
        return 300 <= self.status < 400

    def matches(self, path: str) -> bool:
        return re.search(self.pattern, path) is not None


def unquote(arg: str) -> str:
    return arg[1:-1].replace('\\"', '"') if len(arg) >= 2 and arg[0] == arg[-1] == '"' else arg


def prefix_pattern(prefix: str) -> str:
    """mod_alias Redirect matches whole path segments and keeps the rest."""
    return "^" + re.escape(prefix) + ("(.*)$" if prefix.endswith("/") else "(/.*)?$")


def _site_target(target: str) -> str:
    return target.removeprefix(SITE_PREFIX)


def parse(text: str) -> list[Rule]:
    """Every redirect or status rule of an .htaccess text, in file order."""
    rules: list[Rule] = []
    pending: str | None = None
    negated = False
    for number, line in enumerate(text.splitlines(), 1):
        if match := _COND.match(line):
            pattern = unquote(match.group(1))
            # A negated test (the trailing-slash rule's "!/$") names no source
            # path, so the rule after it is not a path-to-target redirect.
            negated = negated or pattern.startswith("!")
            pending = None if pattern.startswith("!") else pattern
            continue
        if (match := _RULE.match(line)) and pending is not None and not negated:
            status = _STATUS.search(match.group(2))
            if status:
                target = None if match.group(1) == "-" else _site_target(unquote(match.group(1)))
                rules.append(Rule(number, pending, target, int(status.group(1))))
        elif match := _REDIRECT.match(line):
            prefix, target = unquote(match.group(2)), unquote(match.group(3))
            rules.append(Rule(number, prefix_pattern(prefix), target, int(match.group(1))))
        elif match := _REDIRECT_MATCH.match(line):
            target = unquote(match.group(3)) if match.group(3) else None
            rules.append(Rule(number, unquote(match.group(2)), target, int(match.group(1))))
        if not line.lstrip().startswith("RewriteCond"):
            pending, negated = None, False
    return rules


def load(path: Path = HTACCESS) -> list[Rule]:
    return parse(path.read_text(encoding="utf-8"))


def matching(rules: list[Rule], path: str) -> Iterator[Rule]:
    return (rule for rule in rules if rule.matches(path))


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        for rule in load(Path(argv[1]) if len(argv) > 1 else HTACCESS):
            print(f"{rule.line}\t{rule.status}\t{rule.pattern}\t{rule.target or '-'}")
        return 0
    if argv[:1] == ["match"] and len(argv) >= 3:
        rules = load(Path(argv[1]))
        hit = False
        for path in argv[2:]:
            for rule in matching(rules, path):
                print(f"{path}\tline {rule.line}\t{rule.status}\t{rule.pattern}")
                hit = True
        return 1 if hit else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
