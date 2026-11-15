# version 0.2.0
"""Build metrika.db: real human visits per page per day from prod access logs.

Reads every access log under a log dir and counts, per page and day:
  visitors = distinct client IPs, hits = successful page views.

A hit counts only when ALL hold:
  * HTTP status is 200,
  * the path looks like a page (ends with .php, has no file extension,
    or ends with /) — images, css, js, fonts and other static assets
    never enter the database,
  * the path is not an error page (404.php is excluded: probe traffic
    lands there with status 200 but it is not real content),
  * the client is not a bot: user agent has no known-bot token (same list
    as check_bots.py), is not empty/dash, and the IP is absent from botlist.csv,
  * the hit is not the owner's: its (IP, user agent) pair is not a device
    flagged exclude_from_metrika in users.db (build_users_db.py). The
    profile page /services/user_profile/ records one device per IP + user
    agent, so open it once from each new network to exclude that network.

Query strings are stripped, so /dev/c/?a=1 and /dev/c/ are one page.
The database is built under a temporary name beside metrika.db and renamed
over it, so the backoffice reads the old or the new file, never a half-built
one.
Output tables:
  visits_daily(page TEXT, day TEXT, visitors INT, hits INT,
               visitors_nocrawler INT, hits_nocrawler INT, PK(page, day)),
  site_daily(day TEXT PK, visitors INT, hits INT,
             visitors_nocrawler INT, hits_nocrawler INT) for site-wide uniques,
  crawlers(pattern TEXT PK, description TEXT, url TEXT, source TEXT)
  with one row per crawlers.json entry,
  meta(key TEXT PK, value TEXT) with built_at and day range.
The *_nocrawler columns drop hits whose user agent matches crawlers.json
(self-identifying crawlers); the plain columns keep the historic behaviour.

Usage:
  build_metrika_db.py [--log-dir DIR] [--db FILE] [--botlist FILE]
                      [--users-db FILE] [--crawlers FILE] [--top N]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from log_paths import botlist_path as default_botlist_path
from log_paths import prod_access_dir

ACCESS_RE = re.compile(
    r"^(?P<host>\S+) (?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "
    r'"(?P<method>[A-Z]+) (?P<target>\S+) HTTP/[^"]*" '
    r"(?P<status>\d{3}) \S+ \"[^\"]*\" \"(?P<ua>[^\"]*)\""
)
ACCESS_TS_FORMATS = ("%d/%b/%Y:%H:%M:%S %z", "%d/%b/%Y:%H:%M:%S")

STATIC_EXTENSIONS = frozenset(
    {
        ".css",
        ".js",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".bmp",
        ".avif",
        ".tiff",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".mp3",
        ".mp4",
        ".webm",
        ".pdf",
        ".zip",
        ".xml",
        ".txt",
        ".json",
        ".map",
    }
)

BOT_UA_TOKENS = (
    "bot",
    "spider",
    "crawler",
    "crawl",
    "slurp",
    "archiver",
    "curl",
    "wget",
    "python-requests",
    "python-urllib",
    "httpx",
    "go-http",
    "aiohttp",
    "scrapy",
    "java/",
    "libwww",
    "lwp-trivial",
    "bingbot",
    "googlebot",
    "applebot",
    "baiduspider",
    "yandexbot",
    "petalbot",
    "sogou",
    "exabot",
    "facebookexternalhit",
    "twitterbot",
    "gptbot",
    "chatgpt-user",
    "claudebot",
    "anthropic",
    "bytespider",
    "dataforseo",
    "semrush",
    "ahrefs",
    "dotbot",
    "mj12bot",
    "amazonbot",
    "oai-searchbot",
    "perplexity",
    "headless",
    "phantomjs",
    "masscan",
    "zgrab",
    "nmap",
    "nikto",
    "sqlmap",
    "wpscan",
    "telegram",
    "wordpress",
)


def default_paths() -> tuple[str, str, str, str]:
    """Locate external access logs, metrika.db, botlist.csv and crawlers.json."""
    here = os.path.dirname(os.path.realpath(__file__))
    return (
        prod_access_dir(),
        os.path.join(here, "metrika.db"),
        default_botlist_path(),
        os.path.join(here, "crawlers.json"),
    )


def default_users_db() -> str:
    """users.db beside this script, where build_users_db.py writes it."""
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "users.db")


def iter_log_files(log_dir: str) -> list[str]:
    """Every regular file in the log dir, oldest first by name."""
    return sorted(os.path.join(log_dir, name) for name in os.listdir(log_dir) if os.path.isfile(os.path.join(log_dir, name)))


def load_owner_pairs(users_db: str) -> set[tuple[str, str]]:
    """(IP, user agent) of every device flagged exclude_from_metrika.

    A missing or unreadable users.db excludes nothing: the visit counts
    must not fail because the owner list is unavailable.
    """
    if not os.path.isfile(users_db):
        return set()
    try:
        db = sqlite3.connect(f"file:{users_db}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT ip, user_agent FROM users WHERE exclude_from_metrika = 1 AND ip != '' AND user_agent != ''").fetchall()
        finally:
            db.close()
    except sqlite3.Error as error:
        print(f"WARNING: cannot read owner devices from '{users_db}': {error}", file=sys.stderr)
        return set()
    return {(str(ip), str(user_agent)) for ip, user_agent in rows}


def is_owner_hit(ip: str, user_agent: str, owner_pairs: set[tuple[str, str]]) -> bool:
    """True when (IP, user agent) is an excluded owner device.

    The profile page stores the user agent cut to 300 characters, so the log
    side is cut the same way before the compare.
    """
    return (ip, user_agent[:300]) in owner_pairs


def load_bot_ips(botlist_path: str) -> set[str]:
    """Client IPs from a check_bots.py botlist.csv (first column)."""
    ips: set[str] = set()
    with open(botlist_path, encoding="utf-8", errors="replace") as handle:
        for row in csv.reader(handle):
            if not row or row[0].strip().lower() == "ip":
                continue
            ips.add(row[0].strip())
    return ips


def page_of(target: str) -> str | None:
    """Normalized page path, or None for static assets and error pages."""
    path = urlsplit(target).path or "/"
    if os.path.basename(path).lower() == "404.php":
        return None
    _, dot, ext = path.rpartition(".")
    if dot and "/" not in ext and ("." + ext.lower()) in STATIC_EXTENSIONS:
        return None
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define and parse CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="", help="dir with access logs")
    parser.add_argument("--db", default="", help="sqlite db to (re)build")
    parser.add_argument("--botlist", default="", help="botlist.csv with bot IPs")
    parser.add_argument("--users-db", default="", help="users.db whose excluded devices are left out (default: beside this script)")
    parser.add_argument("--crawlers", default="", help="crawlers.json reference list")
    parser.add_argument("--top", type=int, default=15, help="top pages printed")
    return parser.parse_args(argv)


@dataclass
class CrawlerMatcher:
    """Compiled crawlers.json patterns with a per-UA result cache."""

    regex: re.Pattern[str] | None
    patterns: int
    cache: dict[str, bool] = field(default_factory=dict)


def load_crawler_entries(crawlers_path: str) -> list[dict[str, str]]:
    """Crawler reference entries from crawlers.json (empty when missing)."""
    if not os.path.isfile(crawlers_path):
        return []
    with open(crawlers_path, encoding="utf-8") as handle:
        document = json.load(handle)
    entries = document.get("entries", []) if isinstance(document, dict) else []
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("pattern")]


def compile_crawler_matcher(entries: list[dict[str, str]]) -> CrawlerMatcher:
    """One case-insensitive alternation over all patterns; bad ones skipped."""
    parts: list[str] = []
    for entry in entries:
        pattern = entry.get("pattern", "")
        if entry.get("source") == "vendor-docs":
            pattern = re.escape(pattern)
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error:
            continue
        parts.append(f"(?:{pattern})")
    regex = re.compile("|".join(parts), re.IGNORECASE) if parts else None
    return CrawlerMatcher(regex=regex, patterns=len(parts))


def is_crawler_hit(user_agent: str, matcher: CrawlerMatcher) -> bool:
    """True when the UA matches any crawlers.json pattern (cached per UA)."""
    if matcher.regex is None:
        return False
    if user_agent not in matcher.cache:
        matcher.cache[user_agent] = matcher.regex.search(user_agent) is not None
    return matcher.cache[user_agent]


@dataclass
class VisitData:  # pylint: disable=too-many-instance-attributes
    """Human page views aggregated from access logs, both crawler modes."""

    visitors: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    hits: dict[tuple[str, str], int] = field(default_factory=dict)
    visitors_nc: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    hits_nc: dict[tuple[str, str], int] = field(default_factory=dict)
    day_ips: dict[str, set[str]] = field(default_factory=dict)
    day_hits: dict[str, int] = field(default_factory=dict)
    day_ips_nc: dict[str, set[str]] = field(default_factory=dict)
    day_hits_nc: dict[str, int] = field(default_factory=dict)
    parsed: int = 0
    kept: int = 0
    bot_dropped: int = 0
    owner_dropped: int = 0
    owner_devices: int = 0
    crawler_dropped: int = 0
    crawler_patterns: int = 0
    min_day: str = ""
    max_day: str = ""


def parse_day(ts_raw: str) -> str:
    """Access-log timestamp to YYYY-MM-DD, or empty when unparsable."""
    for fmt in ACCESS_TS_FORMATS:
        try:
            return datetime.strptime(ts_raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def is_bot_hit(user_agent: str, ip: str, bot_ips: set[str]) -> bool:
    """True for known-bot user agents and listed bot IPs."""
    ua = user_agent.strip()
    if ua in {"", "-"}:
        return True
    if any(token in ua.lower() for token in BOT_UA_TOKENS):
        return True
    return ip in bot_ips


def _human_page_day(match: re.Match[str], bot_ips: set[str], owners: set[tuple[str, str]], data: VisitData) -> tuple[str, str] | None:
    """(page, day) when the log line is a human 200 page view, else None.

    Counts the bot and owner hits it drops in data.
    """
    if match.group("status") != "200":
        return None
    page = page_of(match.group("target"))
    if page is None:
        return None
    if is_bot_hit(match.group("ua"), match.group("ip"), bot_ips):
        data.bot_dropped += 1
        return None
    if owners and is_owner_hit(match.group("ip"), match.group("ua"), owners):
        data.owner_dropped += 1
        return None
    day = parse_day(match.group("ts"))
    return (page, day) if day else None


def collect_visits(files: list[str], bot_ips: set[str], matcher: CrawlerMatcher | None = None, owner_pairs: set[tuple[str, str]] | None = None) -> VisitData:
    """Aggregate human 200 page views per (page, day) from log files.

    The plain aggregates keep the historic behaviour; the *_nc aggregates
    additionally drop hits whose UA matches crawlers.json. Without a matcher
    both modes are identical. Hits from owner_pairs (excluded devices) are
    dropped from both.
    """
    data = VisitData()
    owners = owner_pairs or set()
    data.owner_devices = len(owners)
    if matcher is not None:
        data.crawler_patterns = matcher.patterns
    visitors: dict[tuple[str, str], set[str]] = defaultdict(set)
    hits: dict[tuple[str, str], int] = defaultdict(int)
    visitors_nc: dict[tuple[str, str], set[str]] = defaultdict(set)
    hits_nc: dict[tuple[str, str], int] = defaultdict(int)
    day_ips: dict[str, set[str]] = defaultdict(set)
    day_hits: dict[str, int] = defaultdict(int)
    day_ips_nc: dict[str, set[str]] = defaultdict(set)
    day_hits_nc: dict[str, int] = defaultdict(int)
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = ACCESS_RE.match(line.rstrip("\n"))
                if not match:
                    continue
                data.parsed += 1
                page_day = _human_page_day(match, bot_ips, owners, data)
                if page_day is None:
                    continue
                page, day = page_day
                data.min_day = day if not data.min_day or day < data.min_day else data.min_day
                data.max_day = day if not data.max_day or day > data.max_day else data.max_day
                visitors[(page, day)].add(match.group("ip"))
                hits[(page, day)] += 1
                day_ips[day].add(match.group("ip"))
                day_hits[day] += 1
                data.kept += 1
                if matcher is not None and is_crawler_hit(match.group("ua"), matcher):
                    data.crawler_dropped += 1
                    continue
                visitors_nc[(page, day)].add(match.group("ip"))
                hits_nc[(page, day)] += 1
                day_ips_nc[day].add(match.group("ip"))
                day_hits_nc[day] += 1
    data.day_ips = day_ips
    data.day_hits = day_hits
    data.day_ips_nc = day_ips_nc
    data.day_hits_nc = day_hits_nc
    data.visitors = visitors
    data.hits = hits
    data.visitors_nc = visitors_nc
    data.hits_nc = hits_nc
    return data


def write_db(db_path: str, data: VisitData, crawler_entries: list[dict[str, str]]) -> None:
    """Rebuild metrika.db from aggregated visits plus the crawler reference.

    The new database is built as <name>.tmp-<pid>.db beside db_path and
    renamed over it: readers see the old or the new file, and a failed build
    leaves the old one in place.
    """
    root, ext = os.path.splitext(db_path)
    tmp_path = f"{root}.tmp-{os.getpid()}{ext}"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    try:
        _fill_db(tmp_path, data, crawler_entries)
        os.replace(tmp_path, db_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _fill_db(db_path: str, data: VisitData, crawler_entries: list[dict[str, str]]) -> None:
    """Create the tables in a new database file and insert the aggregates."""
    db = sqlite3.connect(db_path)
    try:
        db.execute("CREATE TABLE visits_daily(page TEXT NOT NULL, day TEXT NOT NULL, visitors INTEGER NOT NULL, hits INTEGER NOT NULL, visitors_nocrawler INTEGER NOT NULL, hits_nocrawler INTEGER NOT NULL, PRIMARY KEY(page, day))")
        db.execute("CREATE TABLE site_daily(day TEXT PRIMARY KEY, visitors INTEGER NOT NULL, hits INTEGER NOT NULL, visitors_nocrawler INTEGER NOT NULL, hits_nocrawler INTEGER NOT NULL)")
        db.execute("CREATE TABLE crawlers(pattern TEXT PRIMARY KEY, description TEXT NOT NULL, url TEXT NOT NULL, source TEXT NOT NULL)")
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        db.executemany(
            "INSERT INTO visits_daily(page, day, visitors, hits, visitors_nocrawler, hits_nocrawler) VALUES(?, ?, ?, ?, ?, ?)",
            [(page, day, len(ips), data.hits[(page, day)], len(data.visitors_nc.get((page, day), set())), data.hits_nc.get((page, day), 0)) for (page, day), ips in data.visitors.items()],
        )
        db.executemany(
            "INSERT INTO site_daily(day, visitors, hits, visitors_nocrawler, hits_nocrawler) VALUES(?, ?, ?, ?, ?)",
            [(day, len(ips), data.day_hits[day], len(data.day_ips_nc.get(day, set())), data.day_hits_nc.get(day, 0)) for day, ips in data.day_ips.items()],
        )
        db.executemany(
            "INSERT INTO crawlers(pattern, description, url, source) VALUES(?, ?, ?, ?)",
            [(entry.get("pattern", ""), entry.get("description", ""), entry.get("url", ""), entry.get("source", "")) for entry in crawler_entries if entry.get("pattern")],
        )
        built_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        db.executemany(
            "INSERT INTO meta(key, value) VALUES(?, ?)",
            [("built_at", built_at), ("min_day", data.min_day), ("max_day", data.max_day), ("crawler_patterns", str(data.crawler_patterns)), ("owner_devices", str(data.owner_devices))],
        )
        db.commit()
    finally:
        db.close()


def print_summary(data: VisitData, db_path: str, top_n: int) -> None:
    """Print totals and top pages from aggregated visits."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for (page, _day), ips in data.visitors.items():
        totals[page][0] += len(ips)
        totals[page][1] += data.hits[(page, _day)]
    top = sorted(totals.items(), key=lambda item: (-item[1][1], item[0]))[:top_n]
    print(f"parsed lines: {data.parsed}, kept page views: {data.kept}, bot-dropped: {data.bot_dropped}, owner-dropped: {data.owner_dropped} ({data.owner_devices} excluded devices), crawler-dropped: {data.crawler_dropped}")
    print(f"pages x days: {len(data.visitors)}, range: {data.min_day}..{data.max_day}, db: {db_path}")
    for page, (page_visitors, page_hits) in top:
        print(f"  {page_hits:8d} hits  {page_visitors:7d} visitors  {page}")


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse logs, rebuild the database, print summary."""
    args = parse_args(argv)
    default_log_dir, default_db, default_botlist, default_crawlers = default_paths()
    log_dir = args.log_dir or default_log_dir
    db_path = args.db or default_db
    botlist_path = args.botlist or default_botlist
    crawlers_path = args.crawlers or default_crawlers
    if not os.path.isdir(log_dir):
        print(f"ERROR: log dir '{log_dir}' not found.", file=sys.stderr)
        return 1
    files = iter_log_files(log_dir)
    if not files:
        print(f"ERROR: no log files in '{log_dir}'.", file=sys.stderr)
        return 1
    bot_ips = load_bot_ips(botlist_path) if os.path.isfile(botlist_path) else set()
    owner_pairs = load_owner_pairs(args.users_db or default_users_db())
    crawler_entries = load_crawler_entries(crawlers_path)
    matcher = compile_crawler_matcher(crawler_entries)
    data = collect_visits(files, bot_ips, matcher, owner_pairs)
    write_db(db_path, data, crawler_entries)
    print_summary(data, db_path, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
