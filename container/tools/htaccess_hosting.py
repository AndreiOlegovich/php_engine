#!/usr/bin/env python3
# version 0.1.0
"""Check the .htaccess files of a site against the hosting it goes to.

A site is often downloaded from one hosting and uploaded to another, and an
.htaccess written for the first can break on the second: an HTTPS redirect
that loops forever, PHP settings that give every page a 500, rules that never
run. SITE_HOSTING in container/site-profile.env names the target:

  beget         nginx in front (TLS, static files), Apache with mod_php behind
  dreamhost     Apache with TLS itself, PHP as FastCGI
  apache-proxy  any Apache behind a TLS proxy that sends X-Forwarded-Proto
  apache-tls    any Apache that ends TLS itself, PHP not as mod_php
  litespeed     LiteSpeed: reads .htaccess, TLS itself, php_value works
  nginx         nginx only: no .htaccess is read at all
  unknown       not decided: only the hints about the source are shown

  python3 container/tools/htaccess_hosting.py              # the target of the profile
  python3 container/tools/htaccess_hosting.py --hosting beget
  python3 container/tools/htaccess_hosting.py --hints      # what the files were written for

It reads every .htaccess under container/src (--src). For each problem it
prints file:line, what goes wrong on the target and the form that works.
The hints say which hosting the files were written for: X-Forwarded-Proto
checks (a TLS proxy), php_value lines (mod_php), a hosting's own AddHandler
lines, a .user.ini (PHP as FastCGI).

Exit codes: 0 nothing breaks on the target (or it is unknown), 1 a rule
breaks on it, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CONTAINER = Path(__file__).resolve().parents[1]
DEFAULT_SRC = CONTAINER / "src"
DEFAULT_PROFILE = CONTAINER / "site-profile.env"


@dataclass(frozen=True)
class Hosting:
    """What a hosting does with a request, as far as .htaccess cares."""

    label: str
    reads_htaccess: bool = True
    tls_at_proxy: bool = False  # Apache sees plain HTTP; X-Forwarded-Proto says https
    static_by_front: bool = False  # an existing static file never reaches Apache
    mod_php: bool = False  # php_value / php_flag allowed in .htaccess


HOSTINGS = {
    "beget": Hosting("Beget: nginx in front, Apache with mod_php behind", tls_at_proxy=True, static_by_front=True, mod_php=True),
    "dreamhost": Hosting("DreamHost: Apache with TLS, PHP as FastCGI"),
    "apache-proxy": Hosting("Apache behind a TLS proxy", tls_at_proxy=True),
    "apache-tls": Hosting("Apache with TLS itself, PHP not as mod_php"),
    "litespeed": Hosting("LiteSpeed", mod_php=True),
    "nginx": Hosting("nginx only", reads_htaccess=False),
}
NAMES = (*HOSTINGS, "unknown")

STATIC_IN_PATTERN = re.compile(r"\\\.\(?(?:\?:)?(?:jpe?g|png|webp|gif|svg|css|js|txt|ico|woff2?|pdf)\b", re.IGNORECASE)
HTTPS_OFF = re.compile(r"^\s*RewriteCond\s+%\{HTTPS\}\s+(?:off|!on|=off|!=on)\b", re.IGNORECASE)
PROTO_CHECK = re.compile(r"^\s*RewriteCond\s+%\{HTTP:X-Forwarded-Proto\}", re.IGNORECASE)
TO_HTTPS = re.compile(r"^\s*RewriteRule\s+\S+\s+https://", re.IGNORECASE)
REWRITE_RULE = re.compile(r"^\s*RewriteRule\s+(\S+)", re.IGNORECASE)
BARE_REDIRECT = re.compile(r"^\s*(Redirect|RedirectMatch)\b(?:\s+\S+){1,2}\s+/", re.IGNORECASE)
PHP_SETTING = re.compile(r"^\s*(php_value|php_flag|php_admin_value|php_admin_flag)\b", re.IGNORECASE)
IF_MOD_PHP = re.compile(r"^\s*<IfModule\s+(?:mod_php\d*\.c|mod_php\d*|php\d*_module)\s*>", re.IGNORECASE)
IF_END = re.compile(r"^\s*</IfModule\s*>", re.IGNORECASE)
HANDLER = re.compile(r"^\s*(AddHandler|SetHandler|AddType)\s+(\S+)", re.IGNORECASE)
PHP_HANDLER = re.compile(r"php", re.IGNORECASE)
ACCOUNT_PATH = re.compile(r"(?<![\w:/])/(?:home|var/www|usr/home|srv)/[^\s\"']+")
SHOWN = 5


@dataclass(frozen=True)
class Finding:
    """One rule that breaks on the target, or one hint about the source."""

    path: str
    line: int
    text: str
    problem: str
    fix: str = ""
    breaks: bool = True  # False: a warning (a detour, a rule that may not run)


def read_profile(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def htaccess_files(src: Path) -> list[Path]:
    return sorted(p for p in src.rglob(".htaccess") if p.is_file())


def lines_of(path: Path) -> list[str]:
    data = path.read_bytes()
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return data.decode("latin-1").splitlines()


def hints(src: Path) -> list[Finding]:
    """What the .htaccess files and the docroot say about the hosting they were written for."""
    found: list[Finding] = []
    for path in htaccess_files(src):
        rel = path.relative_to(src).as_posix()
        for number, line in enumerate(lines_of(path), 1):
            if PROTO_CHECK.match(line):
                found.append(Finding(rel, number, line.strip(), "checks X-Forwarded-Proto: written for (or also for) a hosting with a TLS proxy in front"))
            elif PHP_SETTING.match(line):
                found.append(Finding(rel, number, line.strip(), "sets PHP options in .htaccess: written for PHP as mod_php"))
            else:
                match = HANDLER.match(line)
                if match and PHP_HANDLER.search(line):
                    found.append(Finding(rel, number, line.strip(), f"{match.group(1)} for PHP: a handler name of the old hosting (it picks the PHP version there)"))
    for ini in sorted(src.rglob(".user.ini")):
        found.append(Finding(ini.relative_to(src).as_posix(), 0, "", ".user.ini: PHP settings for PHP as FastCGI (PHP-FPM)"))
    return found


def https_problems(rel: str, lines: list[str], hosting: Hosting) -> list[Finding]:
    """A redirect to https:// must fire only when neither TLS signal says https."""
    found: list[Finding] = []
    conds: list[str] = []
    for number, line in enumerate(lines, 1):
        if re.match(r"^\s*RewriteCond\b", line, re.IGNORECASE):
            conds.append(line)
            continue
        if TO_HTTPS.match(line):
            https_off = any(HTTPS_OFF.match(c) for c in conds)
            proto = any(PROTO_CHECK.match(c) for c in conds)
            both = "RewriteCond %{HTTPS} off and RewriteCond %{HTTP:X-Forwarded-Proto} !https, both before the rule"
            if https_off and not proto and hosting.tls_at_proxy:
                found.append(Finding(rel, number, line.strip(), "Apache never sees HTTPS here (TLS ends at the proxy), so %{HTTPS} is always off: every https request redirects to itself, forever", both))
            if proto and not https_off and not hosting.tls_at_proxy:
                found.append(Finding(rel, number, line.strip(), "no proxy sends X-Forwarded-Proto here, so the check always holds: every https request redirects to itself, forever", both))
        if re.match(r"^\s*RewriteRule\b", line, re.IGNORECASE):
            conds = []
    return found


def target_problems(src: Path, hosting: Hosting) -> list[Finding]:
    """The rules of the .htaccess files that break, or never run, on the hosting."""
    found: list[Finding] = []
    files = htaccess_files(src)
    if not hosting.reads_htaccess:
        for path in files:
            count = sum(1 for line in lines_of(path) if line.strip() and not line.lstrip().startswith("#"))
            found.append(Finding(path.relative_to(src).as_posix(), 0, "", f"nginx reads no .htaccess: its {count} lines do nothing", "move the rules into the server configuration of the hosting (its panel or support)"))
        return found
    for path in files:
        rel = path.relative_to(src).as_posix()
        lines = lines_of(path)
        found.extend(https_problems(rel, lines, hosting))
        depth = 0
        for number, line in enumerate(lines, 1):
            if IF_MOD_PHP.match(line):
                depth += 1
            elif IF_END.match(line) and depth:
                depth -= 1
            if PHP_SETTING.match(line) and not hosting.mod_php and not depth:
                found.append(Finding(rel, number, line.strip(), "PHP does not run as mod_php here: Apache answers 500 for every page", "wrap the PHP settings in <IfModule mod_php.c> ... </IfModule>, and set them in the hosting's PHP settings (or .user.ini) instead"))
            if hosting.tls_at_proxy and BARE_REDIRECT.match(line):
                found.append(Finding(rel, number, line.strip(), "a Redirect to a bare path makes Apache answer with http://: the visitor makes a plain-HTTP hop", "a RewriteCond/RewriteRule pair with %{ENV:SITE_SCHEME}://%{HTTP_HOST}/new/ (the site-htaccess skill)", breaks=False))
            if not line.lstrip().startswith("#") and ACCOUNT_PATH.search(line) and not REWRITE_RULE.match(line):
                found.append(Finding(rel, number, line.strip(), "an absolute path of a hosting account: on another account it points nowhere", "a path relative to the site, or the hosting's own setting for it", breaks=False))
            handler = HANDLER.match(line)
            if handler and PHP_HANDLER.search(line) and not line.lstrip().startswith("#"):
                found.append(Finding(rel, number, line.strip(), f"{handler.group(1)} names a PHP handler of another hosting: here it can serve the PHP source as a download or answer 500", "remove it; choose the PHP version in the hosting's panel"))
            rule = REWRITE_RULE.match(line)
            if hosting.static_by_front and rule and STATIC_IN_PATTERN.search(rule.group(1)):
                found.append(Finding(rel, number, line.strip(), "the front server answers an existing static file itself: this rule never runs for such a file", "fine when the old file is gone; otherwise rename the file or redirect its page instead", breaks=False))
    return found


def print_findings(title: str, findings: list[Finding]) -> None:
    """The findings grouped by kind: up to SHOWN examples each, then a count."""
    print(title)
    if not findings:
        print("  none")
    groups: dict[tuple[str, str, bool], list[Finding]] = {}
    for item in findings:
        groups.setdefault((item.problem, item.fix, item.breaks), []).append(item)
    for (problem, fix, breaks), items in groups.items():
        print(f"  {'' if breaks else 'warning: '}{problem} ({len(items)})")
        for item in items[:SHOWN]:
            where = f"{item.path}:{item.line}" if item.line else item.path
            print(f"      {where}" + (f"  {item.text}" if item.text else ""))
        if len(items) > SHOWN:
            print(f"      ... and {len(items) - SHOWN} more")
        if fix:
            print(f"    instead: {fix}")


def looks_like(found: list[Finding]) -> str:
    """The hosting the hints fit, in one phrase."""
    proxy = any("X-Forwarded-Proto" in item.problem for item in found)
    mod_php = any("mod_php" in item.problem for item in found)
    fastcgi = any(".user.ini" in item.problem for item in found)
    if proxy and mod_php:
        return "a TLS proxy in front and PHP as mod_php: Beget-like (beget, apache-proxy)"
    if proxy:
        return "a TLS proxy in front (apache-proxy, beget)"
    if fastcgi and not mod_php:
        return "Apache with PHP as FastCGI: DreamHost-like (dreamhost, apache-tls)"
    if mod_php:
        return "PHP as mod_php"
    return "no hosting in particular"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="htaccess_hosting.py", description="Check the .htaccess files of a site against the hosting it goes to.")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="the docroot (default: container/src)")
    parser.add_argument("--hosting", choices=NAMES, help="the target (default: SITE_HOSTING of the profile)")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help=argparse.SUPPRESS)
    parser.add_argument("--hints", action="store_true", help="only what the files were written for")
    args = parser.parse_args(argv)
    if not args.src.is_dir():
        print(f"error: {args.src} is not a directory", file=sys.stderr)
        return 2
    name = args.hosting or read_profile(args.profile).get("SITE_HOSTING", "") or "unknown"
    if name not in NAMES:
        print(f"error: SITE_HOSTING={name!r} is none of: {', '.join(NAMES)}", file=sys.stderr)
        return 2
    files = htaccess_files(args.src)
    print(f"{len(files)} .htaccess file(s) under {args.src}")
    found_hints = hints(args.src)
    print_findings("Written for (hints about the hosting the site comes from):", found_hints)
    print(f"  looks written for: {looks_like(found_hints)}")
    if args.hints:
        return 0
    if name == "unknown":
        print("\nThe target hosting is not set: SITE_HOSTING in container/site-profile.env (" + ", ".join(HOSTINGS) + ").")
        return 0
    hosting = HOSTINGS[name]
    problems = target_problems(args.src, hosting)
    print()
    print_findings(f"On the target, {name} ({hosting.label}):", problems)
    breaking = sum(item.breaks for item in problems)
    print(f"\n{breaking} rule(s) break on {name}, {len(problems) - breaking} warning(s).")
    return 1 if breaking else 0


if __name__ == "__main__":
    sys.exit(main())
