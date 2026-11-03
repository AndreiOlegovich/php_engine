# version 0.0.3
"""List every 404 hit in Apache access/error logs as `datetime url` lines.

Reads access logs (HTTP status filter, default 404) and error logs
("script ... not found or unable to stat"), prints one
`YYYY-MM-DD HH:MM:SS /url/path` line per hit, sorted chronologically.

Usage:
  list_404.py [--log-dir DIR] [--log-file FILE ...] [--status CODE ...]
              [--docroot-prefix PREFIX] [--skip-probes] [--unique]
              [--output FILE]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime

from log_paths import prod_dir

ACCESS_RE = re.compile(
    r"\[(?P<ts>[^\]]+)\]\s+\"(?P<method>[A-Z]+)\s+(?P<target>\S+)\s+HTTP/[^\"]*\"\s+"
    r"(?P<status>\d{3})\b"
)
ERROR_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+\[error\].*?script '(?P<full>[^']+)' not found or unable to stat")
ACCESS_TS_FORMATS = ("%d/%b/%Y:%H:%M:%S %z", "%d/%b/%Y:%H:%M:%S")
ERROR_TS_FORMAT = "%a %b %d %H:%M:%S %Y"

_DEFAULT_DOCROOT_PREFIX = "/var/www/html/"

# Scanner-probe basenames (bots hunting wp-/shell/phpunit/...); not real pages.
PROBE_RE = re.compile(
    r"(?i)^(wp-|wp_|wso|shell|alfa|bypass|c99|r57|phpunit|tinyfilemanager|"
    r"filemanager|xmrlpc|\.env|xmlrpc|administrator|phpmyadmin)"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define and parse CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="", help="dir with logs (recursive)")
    parser.add_argument("--log-file", action="append", default=[], help="single log file (repeatable)")
    parser.add_argument("--status", type=int, action="append", default=[], help="HTTP status to list")
    parser.add_argument("--docroot-prefix", default="", help="hosting docroot prefix")
    parser.add_argument("--skip-probes", action="store_true", help="skip scanner probes")
    parser.add_argument("--unique", action="store_true", help="one line per URL (latest)")
    parser.add_argument("--output", default="", help="write listing to file as well")
    return parser.parse_args(argv)


def default_log_dir() -> str:
    """Default log tree: the external prod/ directory."""
    return prod_dir()


def iter_log_files(log_dir: str, log_files: list[str]) -> list[str]:
    """Collect log files from --log-file flags plus --log-dir (recursive)."""
    found = [path for path in log_files if os.path.isfile(path)]
    if log_dir and os.path.isdir(log_dir):
        for root, _dirs, names in os.walk(log_dir):
            for name in sorted(names):
                path = os.path.join(root, name)
                if os.path.isfile(path):
                    found.append(path)
    return sorted(set(found))


def parse_ts(raw: str) -> datetime | None:
    """Parse an access- or error-log timestamp; None when unknown format."""
    for fmt in ACCESS_TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    try:
        return datetime.strptime(raw, ERROR_TS_FORMAT)
    except ValueError:
        return None


def clean_target(target: str) -> str:
    """Request target -> page path (drop query string and fragment)."""
    return target.split("?", 1)[0].split("#", 1)[0]


def is_probe(url: str) -> bool:
    """True when the URL looks like a scanner probe, not a real page."""
    return bool(PROBE_RE.match(url.rsplit("/", 1)[-1]))


def parse_access_line(line: str, statuses: set[int]) -> tuple[datetime | None, str, str] | None:
    """Parse an access-log line with a wanted status; None otherwise."""
    match = ACCESS_RE.search(line)
    if not match:
        return None
    try:
        status = int(match.group("status"))
    except ValueError:
        return None
    if status not in statuses:
        return None
    url = clean_target(match.group("target"))
    if not url.startswith("/") or url == "*":
        return None
    raw = match.group("ts")
    return parse_ts(raw), raw, url


def parse_error_line(line: str, docroot_prefix: str) -> tuple[datetime | None, str, str] | None:
    """Parse an error-log 'not found or unable to stat' line; None otherwise."""
    match = ERROR_RE.search(line)
    if not match:
        return None
    full = match.group("full")
    if not full.startswith(docroot_prefix):
        return None
    url = "/" + full[len(docroot_prefix) :].split("?", 1)[0]
    raw = match.group("ts")
    return parse_ts(raw), raw, url


def iter_hits(paths: list[str], statuses: set[int], docroot_prefix: str, skip_probes: bool) -> list[tuple[datetime | None, str, str]]:
    """Parse every log file; return hits in file order."""
    hits: list[tuple[datetime | None, str, str]] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    parsed = parse_access_line(line, statuses)
                    if parsed is None:
                        parsed = parse_error_line(line, docroot_prefix)
                    if parsed is None:
                        continue
                    if skip_probes and is_probe(parsed[2]):
                        continue
                    hits.append(parsed)
        except OSError as error:
            print(f"WARNING: cannot read '{path}' ({error})", file=sys.stderr)
    return hits


def render(hits: list[tuple[datetime | None, str, str]], unique: bool) -> list[str]:
    """Format hits as `YYYY-MM-DD HH:MM:SS url` lines, chronological."""
    if unique:
        latest: dict[str, tuple[datetime | None, str, str]] = {}
        for hit in hits:
            moment, _raw, url = hit
            prev = latest.get(url)
            if prev is None or (moment is not None and (prev[0] is None or moment >= prev[0])):
                latest[url] = hit
        hits = list(latest.values())
    ordered = sorted(hits, key=lambda h: (h[0] is None, h[0] or datetime.min, h[2]))
    lines = []
    for moment, raw, url in ordered:
        stamp = moment.strftime("%Y-%m-%d %H:%M:%S") if moment is not None else raw
        lines.append(f"{stamp} {url}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Entry point: collect log files, list 404 hits, print (and save)."""
    args = parse_args(argv)
    prefix = args.docroot_prefix or os.environ.get("DOCROOT_PREFIX", _DEFAULT_DOCROOT_PREFIX)
    statuses = set(args.status) if args.status else {404}
    log_dir = args.log_dir or default_log_dir()
    paths = iter_log_files(log_dir, args.log_file)
    if not paths:
        print(f"ERROR: no log files in '{log_dir}'.", file=sys.stderr)
        return 1
    lines = render(iter_hits(paths, statuses, prefix, args.skip_probes), args.unique)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + ("\n" if lines else ""))
    for line in lines:
        print(line)
    print(f"files: {len(paths)}, 404 hits: {len(lines)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
