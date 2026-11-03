# version 0.1.2
"""Analyze Apache error logs from prod: real page vs wrong address, prod vs dev/local.

Reads ``<SITE_LOG_DIR>/prod/error`` (dated error logs; the access/
subdirectory holds access logs and is skipped), groups identical errors,
and for each group answers:
  1. real page or wrong address (checked against ``container/src``),
  2. reproducible on dev (dev.example.com, optional HTTP check) or local
     (static analysis of ``src``: missing require targets, undefined
     classes/functions),
  3. when it was first and last seen, so an error that stopped after a
     deploy is told apart from a current one,
  4. optionally, what prod answers for the page right now (--check-prod,
     one GET per PHP-fatal page, capped), instead of guessing "stale prod"
     from the local source.

Usage:
  analyze_errors.py [--log-dir DIR | --log-file FILE] [--src DIR]
                    [--since YYYY-MM-DD[THH:MM]]
                    [--dev-base URL] [--check-dev] [--max-dev-checks N]
                    [--prod-base URL] [--check-prod] [--max-prod-checks N]
                    [--top N] [--output FILE]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from log_paths import botlist_path as default_botlist_path
from log_paths import prod_error_dir

_DEFAULT_DOCROOT_PREFIX = "/var/www/html/"
_DEFAULT_DEV_BASE = "https://dev.example.com"
_DEFAULT_PROD_BASE = "https://example.com"
_TS_FORMATS = ("%a %b %d %H:%M:%S.%f %Y", "%a %b %d %H:%M:%S %Y")

LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+\[error\]\s+\[client\s+(?P<client>[^\]]+)\]\s+(?P<msg>.*)$")
REFERER_RE = re.compile(r", referer (?P<ref>\S+)\s*$")
_compiled: dict[str, re.Pattern[str]] = {}


def _patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Compile prefix-dependent regexes (rebuilt when the env changes)."""
    prefix = os.environ.get("DOCROOT_PREFIX", _DEFAULT_DOCROOT_PREFIX)
    key = f"not-found\x00{prefix}"
    if key not in _compiled:
        _compiled[key] = re.compile(
            r"script '(?P<full>" + re.escape(prefix) + r"(?P<rel>[^']+))'"
            r" not found or unable to stat"
        )
    key2 = f"fatal-file\x00{prefix}"
    if key2 not in _compiled:
        _compiled[key2] = re.compile(re.escape(prefix) + r"(?P<rel>\S+?):(?P<lineno>\d+)")
    return _compiled[key], _compiled[key2]


def _strip_docroot(text: str) -> str:
    """Remove the hosting docroot prefix from a fatal message."""
    prefix = os.environ.get("DOCROOT_PREFIX", _DEFAULT_DOCROOT_PREFIX)
    return re.sub(r" in " + re.escape(prefix) + r"\S+", "", text)


def load_site_env() -> None:
    """Load KEY=value pairs from $SITE_ENV into os.environ (no override)."""
    path = os.environ.get("SITE_ENV", "")
    if not path:
        return
    if not os.path.isfile(path):
        print(f"ERROR: SITE_ENV file '{path}' not found.", file=sys.stderr)
        raise SystemExit(1)
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if key:
                os.environ.setdefault(key, value)


REQUIRE_RE = re.compile(r"Failed opening required '(?P<target>[^']+)'")
CLASS_RE = re.compile(r'Class "(?P<class>[^"]+)" not found')
FUNCTION_RE = re.compile(r"Call to undefined function (?P<func>[a-zA-Z0-9_\\]+)\(\)")
CONSTANT_RE = re.compile(r"Undefined constant \"(?P<const>[^\"]+)\"")
# Literal `$root . '...'` / `$root."..."` require/include targets in src files.
SRC_REQUIRE_RE = re.compile(r"""(?:require|include)(?:_once)?\s*\(\s*\$root\s*\.\s*['"](?P<path>[^'"]+)['"]""")
NAMESPACE_RE = re.compile(r"^\s*namespace\s+(?P<ns>[^;]+);", re.MULTILINE)
CLASS_DEF_RE = re.compile(r"^\s*(?:final\s+|abstract\s+)?class\s+(?P<name>\w+)", re.MULTILINE)
USE_RE = re.compile(r"^\s*use\s+(?P<fqcn>[^;]+);", re.MULTILINE)

# Basename fragments typical for WP/shell scanner probes (not real pages).
PROBE_FRAGMENTS = (
    "wp-",
    "wp_",
    "wso",
    "shell",
    "alfa",
    "bypass",
    "xss",
    "c99",
    "r57",
    "indox",
    "leafmailer",
    "gecko",
    "tinyfilemanager",
    "filemanager",
    "phpunit",
    "debug",
    "console",
    "geek",
    "(lock360)",
    "xmrlpc",
    "WSO",
    "adminfuns",
    "hello_world",
)
PROBE_BASENAMES = re.compile(
    r"(?i)^(wp-|wp_|wso|shell|alfa|bypass|indox|leafmailer|gecko|"
    r"tinyfilemanager|filemanager|phpunit|c99|r57|xmrlpc|wsh|cc|k\.php)"
)
SCANNER_REFERERS = ("old.example.invalid", "scanner.example.invalid")


@dataclass
class ErrorRecord:
    """One parsed log line."""

    ts: str
    client: str
    msg: str
    referer: str = ""


@dataclass
class Group:  # pylint: disable=too-many-instance-attributes
    """Errors sharing category + path + signature."""

    category: str
    rel: str
    signature: str
    count: int = 0
    example: str = ""
    clients: set[str] = field(default_factory=set)
    first: datetime | None = None
    last: datetime | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define and parse CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="", help="dir with error logs")
    parser.add_argument("--log-file", default="", help="single error log file")
    parser.add_argument("--src", default="", help="local container/src dir")
    parser.add_argument("--dev-base", default=os.environ.get("DEV_URL", _DEFAULT_DEV_BASE))
    parser.add_argument("--check-dev", action="store_true", help="HTTP-check dev")
    parser.add_argument("--max-dev-checks", type=int, default=50)
    parser.add_argument("--since", default="", help="only errors at or after this time: YYYY-MM-DD or YYYY-MM-DDTHH:MM (e.g. the last prod deploy)")
    parser.add_argument("--prod-base", default=os.environ.get("PROD_URL", _DEFAULT_PROD_BASE))
    parser.add_argument("--check-prod", action="store_true", help="GET each PHP-fatal page on prod once and show its status now")
    parser.add_argument("--max-prod-checks", type=int, default=20, help="cap for --check-prod requests (default 20)")
    parser.add_argument("--top", type=int, default=30, help="groups shown per section")
    parser.add_argument("--output", default="", help="write report to file")
    parser.add_argument(
        "--botlist",
        default="",
        help="botlist.csv from check_bots.py (default: the one in the log root when present); error hits from listed client IPs are reported as bot-filtered, not real errors",
    )
    return parser.parse_args(argv)


def load_bot_ips(botlist_path: str) -> set[str]:
    """Client IPs from a check_bots.py botlist.csv (first column, header skipped)."""
    ips: set[str] = set()
    with open(botlist_path, encoding="utf-8", errors="replace") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row or row[0].strip().lower() == "ip":
                continue
            ips.add(row[0].strip())
    return ips


def default_paths() -> tuple[str, str]:
    """Locate the external prod error logs and container/src."""
    here = os.path.dirname(os.path.realpath(__file__))
    return prod_error_dir(), os.path.join(os.path.dirname(os.path.dirname(here)), "src")


def iter_log_files(log_dir: str, log_file: str) -> list[str]:
    """Collect log files from --log-file or every file in --log-dir."""
    if log_file:
        return [log_file]
    return sorted(os.path.join(log_dir, name) for name in os.listdir(log_dir) if os.path.isfile(os.path.join(log_dir, name)))


def parse_ts(ts: str) -> datetime | None:
    """Apache error-log time such as "Thu Oct 22 21:11:34.663857 2026"; None when unknown."""
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(ts.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_since(value: str) -> datetime:
    """--since value: a date or a date with hours and minutes."""
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"--since must be YYYY-MM-DD or YYYY-MM-DDTHH:MM, got {value!r}")


def filter_since(records: list[ErrorRecord], since: datetime) -> tuple[list[ErrorRecord], int, int]:
    """Keep records at or after since; records without a readable time are kept.

    Returns (kept, dropped as older, kept without a readable time)."""
    kept: list[ErrorRecord] = []
    older = undated = 0
    for record in records:
        when = parse_ts(record.ts)
        if when is None:
            undated += 1
            kept.append(record)
        elif when < since:
            older += 1
        else:
            kept.append(record)
    return kept, older, undated


def apply_since(records: list[ErrorRecord], value: str) -> tuple[list[ErrorRecord], str]:
    """Filter by --since; returns the kept records and a report line. ValueError on a bad value."""
    since = parse_since(value)
    kept, older, undated = filter_since(records, since)
    note = f"since {since:%Y-%m-%d %H:%M}: dropped {older} older records"
    if undated:
        note += f", kept {undated} without a readable time"
    return kept, note + "\n"


def parse_line(line: str) -> ErrorRecord | None:
    """Parse one Apache error-log line; None when it does not match."""
    match = LINE_RE.match(line.rstrip("\n"))
    if not match:
        return None
    msg = match.group("msg")
    referer = ""
    ref_match = REFERER_RE.search(msg)
    if ref_match:
        referer = ref_match.group("ref")
        msg = msg[: ref_match.start()]
    return ErrorRecord(ts=match.group("ts"), client=match.group("client"), msg=msg, referer=referer)


def categorize(record: ErrorRecord) -> tuple[str, str, str]:
    """Return (category, rel path, signature) for a record."""
    msg = record.msg
    not_found_re, fatal_file_re = _patterns()
    if "PHP Fatal error" in msg:
        file_match = fatal_file_re.search(msg)
        rel = file_match.group("rel") if file_match else ""
        short = msg.split("\\n")[0].replace("PHP Fatal error:  ", "")
        short = _strip_docroot(short)
        return "php_fatal", rel, short[:160]
    not_found = not_found_re.search(msg)
    if not_found:
        return "not_found", not_found.group("rel"), "not found"
    if re.search(r"AH\d+:", msg):
        return "server_config", "", msg[:120]
    return "other", "", msg[:120]


def is_bot_probe(rel: str, referer: str) -> bool:
    """Heuristic: scanner probe rather than a real site address."""
    base = os.path.basename(rel)
    if PROBE_BASENAMES.match(base):
        return True
    lowered = base.lower()
    if any(fragment.lower() in lowered for fragment in PROBE_FRAGMENTS):
        return True
    if any(host in referer and base in referer for host in SCANNER_REFERERS):
        return True
    return base == "av.php"


def group_records(records: list[ErrorRecord]) -> list[Group]:
    """Aggregate records into groups with hit counts."""
    groups: dict[tuple[str, str, str], Group] = {}
    for record in records:
        category, rel, signature = categorize(record)
        key = (category, rel, signature)
        group = groups.get(key)
        if group is None:
            group = Group(category=category, rel=rel, signature=signature)
            group.example = record.msg[:220]
            groups[key] = group
        group.count += 1
        group.clients.add(record.client.split(":")[0])
        when = parse_ts(record.ts)
        if when is not None:
            group.first = when if group.first is None else min(group.first, when)
            group.last = when if group.last is None else max(group.last, when)
    return sorted(groups.values(), key=lambda g: g.count, reverse=True)


def read_src_file(src_root: str, rel: str) -> str | None:
    """Return src file text, or None when it does not exist in the repo."""
    path = os.path.join(src_root, rel)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def find_require_targets(text: str) -> list[str]:
    """Extract `$root . '...'` require/include targets from PHP source."""
    return [m.group("path") for m in SRC_REQUIRE_RE.finditer(text)]


def check_require_targets(src_root: str, text: str) -> list[str]:
    """List require targets missing from the local src tree."""
    missing = []
    for target in find_require_targets(text):
        rel = target.lstrip("/")
        if not os.path.isfile(os.path.join(src_root, rel)):
            missing.append(target)
    return missing


def symbol_defined_in_src(src_root: str, rel: str, text: str, symbol: str) -> bool:
    """Check whether a class/function is defined in the page or its requires."""
    candidates = [rel] + [p.lstrip("/") for p in find_require_targets(text)]
    for candidate in candidates:
        body = read_src_file(src_root, candidate)
        if body is None:
            continue
        short = symbol.split("\\")[-1]
        if re.search(r"function\s+" + re.escape(short) + r"\s*\(", body):
            return True
        namespace = NAMESPACE_RE.search(body)
        prefix = namespace.group("ns").strip() + "\\" if namespace else ""
        for class_match in CLASS_DEF_RE.finditer(body):
            if prefix + class_match.group("name") == symbol.lstrip("\\"):
                return True
            if class_match.group("name") == short and symbol == short:
                return True
    return False


def verdict_address(group: Group, src_root: str) -> str:
    """Decide whether the error address is a real page or a wrong address."""
    if group.category == "php_fatal":
        return "REAL PAGE (script executed)"
    if group.category != "not_found":
        return "N/A"
    if is_bot_probe(group.rel, ""):
        return "WRONG ADDRESS (scanner probe)"
    candidate = os.path.join(src_root, group.rel)
    if os.path.isfile(candidate):
        return "REAL PAGE missing on prod (stale deploy)"
    # /dir/file.php missing but /dir/ exists as section -> likely moved page.
    parent = os.path.dirname(candidate)
    if group.rel.endswith(".php") and os.path.isdir(parent):
        return "WRONG ADDRESS (no such page in src; needs redirect?)"
    return "WRONG ADDRESS (not in src)"


def verdict_for_class(group: Group, src_root: str, text: str, symbol: str) -> str:
    """Verdict for 'Class X not found' against local src."""
    uses = [u.group("fqcn").strip() for u in USE_RE.finditer(text)]
    if symbol not in uses and not any(u.endswith("\\" + symbol) for u in uses):
        return "REPRODUCIBLE LOCALLY (class used without import/require?)"
    if symbol_defined_in_src(src_root, group.rel, text, symbol):
        return "STALE PROD (class resolves in src; prod serves old code?)"
    return "REPRODUCIBLE LOCALLY (used class not defined in src)"


def verdict_for_function(group: Group, src_root: str, text: str, func: str) -> str:
    """Verdict for 'Call to undefined function' against local src."""
    if symbol_defined_in_src(src_root, group.rel, text, func):
        return "STALE PROD (function defined in src; prod serves old code?)"
    return "REPRODUCIBLE LOCALLY (function not defined in src)"


def verdict_for_signature(group: Group, src_root: str, text: str) -> str:
    """Verdict dispatch on the fatal-error signature."""
    if REQUIRE_RE.search(group.example):
        return "STALE PROD (require target exists in src; prod serves old code?)"
    class_match = CLASS_RE.search(group.example)
    if class_match:
        return verdict_for_class(group, src_root, text, class_match.group("class"))
    func_match = FUNCTION_RE.search(group.example)
    if func_match:
        return verdict_for_function(group, src_root, text, func_match.group("func"))
    if CONSTANT_RE.search(group.example):
        return "CHECK SRC (bare constant; compare prod file content)"
    return "UNKNOWN (manual triage)"


def verdict_local(group: Group, src_root: str) -> str:
    """Decide whether the prod failure is reproducible from local src."""
    if group.category != "php_fatal" or not group.rel:
        return "N/A"
    text = read_src_file(src_root, group.rel)
    if text is None:
        return "PAGE MISSING in local src (prod-only file?)"
    missing = check_require_targets(src_root, text)
    if missing:
        shown = ", ".join(missing[:3])
        return f"REPRODUCIBLE LOCALLY (require target missing in src: {shown})"
    return verdict_for_signature(group, src_root, text)


def dev_url_for_group(dev_base: str, group: Group) -> str:
    """Build the dev URL for a group (dir URL for index.php pages)."""
    rel = group.rel
    if rel.endswith("/index.php"):
        rel = rel[: -len("index.php")]
    return dev_base.rstrip("/") + "/" + rel


def check_dev_url(url: str, timeout: int = 10) -> str:
    """GET a dev URL; return HTTP status or ERR marker (never raises)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return str(response.status)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return f"ERR:{type(error).__name__}"


def check_dev_for_groups(dev_base: str, groups: list[Group], max_checks: int) -> dict[tuple[str, str, str], str]:
    """HTTP-check top fatal/not_found groups on dev; keyed by group key."""
    results = {}
    checked = 0
    for group in groups:
        if checked >= max_checks:
            break
        if group.category not in ("php_fatal", "not_found") or not group.rel:
            continue
        results[(group.category, group.rel, group.signature)] = check_dev_url(dev_url_for_group(dev_base, group))
        checked += 1
    return results


def check_prod_for_groups(prod_base: str, groups: list[Group], max_checks: int) -> dict[tuple[str, str, str], str]:
    """GET each PHP-fatal page on prod once (most hits first, capped); keyed by group key."""
    results = {}
    for group in [g for g in groups if g.category == "php_fatal" and g.rel][:max_checks]:
        results[(group.category, group.rel, group.signature)] = check_dev_url(dev_url_for_group(prod_base, group))
    return results


def _seen(group: Group) -> str:
    if group.first is None or group.last is None:
        return "seen: time unknown"
    return f"seen: first {group.first:%Y-%m-%d %H:%M}, last {group.last:%Y-%m-%d %H:%M}"


def render_report(
    groups: list[Group],
    total_lines: int,
    src_root: str,
    dev_base: str,
    dev_results: dict[tuple[str, str, str], str],
    top: int,
    prod_base: str = _DEFAULT_PROD_BASE,
    prod_results: dict[tuple[str, str, str], str] | None = None,
) -> str:
    """Build the text report (summary + per-category top groups)."""
    counter = Counter(group.category for group in groups)
    total_hits = sum(group.count for group in groups)
    lines = [
        f"parsed lines: {total_lines}, grouped errors: {len(groups)}, total hits: {total_hits}",
        "by category: " + ", ".join(f"{name}={counter[name]}" for name in sorted(counter)),
        "",
    ]
    for category in ("php_fatal", "not_found", "server_config", "other"):
        section = [g for g in groups if g.category == category][:top]
        if not section:
            continue
        lines.append(f"=== {category} (top {len(section)}) ===")
        for group in section:
            path = group.rel or "-"
            lines.append(f"[{group.count}x, {len(group.clients)} clients] {path}")
            lines.append(f"  {_seen(group)}")
            lines.append(f"  sig: {group.signature}")
            lines.append(f"  address: {verdict_address(group, src_root)}")
            local = verdict_local(group, src_root)
            lines.append(f"  local: {local}")
            key = (group.category, group.rel, group.signature)
            if key in dev_results:
                lines.append(f"  dev: {dev_url_for_group(dev_base, group)} -> {dev_results[key]}")
            if prod_results and key in prod_results:
                lines.append(f"  prod now: {dev_url_for_group(prod_base, group)} -> {prod_results[key]}")
        lines.append("")
    return "\n".join(lines)


def analyze(
    records: list[ErrorRecord],
    src_root: str,
    dev_base: str,
    check_dev: bool,
    max_dev_checks: int,
    top: int,
    prod_base: str = _DEFAULT_PROD_BASE,
    check_prod: bool = False,
    max_prod_checks: int = 20,
) -> str:
    """Group records, optionally probe dev and prod, and render the report."""
    groups = group_records(records)
    dev_results: dict[tuple[str, str, str], str] = {}
    if check_dev:
        dev_results = check_dev_for_groups(dev_base, groups, max_dev_checks)
    prod_results: dict[tuple[str, str, str], str] = {}
    if check_prod:
        prod_results = check_prod_for_groups(prod_base, groups, max_prod_checks)
    return render_report(groups, len(records), src_root, dev_base, dev_results, top, prod_base, prod_results)


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args, read logs, print (and optionally save) report."""
    load_site_env()
    args = parse_args(argv)
    default_log_dir, default_src = default_paths()
    log_dir = args.log_dir or default_log_dir
    src_root = args.src or default_src
    if not os.path.isdir(src_root):
        print(f"ERROR: src dir '{src_root}' not found.", file=sys.stderr)
        return 1
    if not args.log_file and not os.path.isdir(log_dir):
        print(
            f"ERROR: log dir '{log_dir}' not found. Pass --log-dir <dir> or --log-file <file>.",
            file=sys.stderr,
        )
        return 1
    files = iter_log_files(log_dir, args.log_file)
    if not files:
        print(f"ERROR: no log files in '{log_dir}'.", file=sys.stderr)
        return 1
    records = []
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                record = parse_line(line)
                if record is not None:
                    records.append(record)
    botlist_path = args.botlist
    if not botlist_path:
        default_botlist = default_botlist_path()
        if os.path.isfile(default_botlist):
            botlist_path = default_botlist
    bot_ips: set[str] = set()
    bot_filtered = 0
    if botlist_path:
        if not os.path.isfile(botlist_path):
            print(f"ERROR: botlist file '{botlist_path}' not found.", file=sys.stderr)
            return 1
        bot_ips = load_bot_ips(botlist_path)
        kept = []
        for record in records:
            if record.client.split(":")[0] in bot_ips:
                bot_filtered += 1
            else:
                kept.append(record)
        records = kept
    since_note = ""
    if args.since:
        try:
            records, since_note = apply_since(records, args.since)
        except ValueError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
    report = analyze(records, src_root, args.dev_base, args.check_dev, args.max_dev_checks, args.top, args.prod_base, args.check_prod, args.max_prod_checks)
    report = since_note + report
    if bot_ips:
        report = f"bot-filtered: {bot_filtered} records from {len(bot_ips)} listed ips (botlist: {botlist_path})\n" + report
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
