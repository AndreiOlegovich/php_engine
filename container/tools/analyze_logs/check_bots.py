# version 0.2.2
"""Rebuild botlist.csv from prod access logs: known-bot user agents plus
behavioural scanner detection.

Reads every access log under a log dir, aggregates per client IP, and lists
an IP when any of these hold:
  * known-bot UA on >= 50% of its hits (and >= 10 hits total),
  * >= 30 hits with HTTP 404,
  * >= 10 hits on probe paths (/wp*, /xmlrpc*, known shell names),
  * one request with a bot signature (see below).

Output columns match the existing botlist.csv exactly:
  ip,hits_404,distinct_paths,first_seen,last_seen,top_user_agent,sample_paths
The rebuild is total (all logs, every run), so first_seen survives and the
file is idempotent. Sorted by hits_404 descending.

Listing rule (high-confidence bots only; low-count browser-UA rows that may
be humans stay out):
  * known-bot UA on >= 50% of its hits (and >= 10 hits total), or
  * >= 10 hits with HTTP 404 with a 404 share >= 50%, or
  * >= 10 hits on probe paths (/wp*, /xmlrpc*, known shell names), or
  * >= 50 hits with HTTP 404 regardless of share, or
  * any single day with >= 150 hits, >= 100 distinct paths,
    >= 100 hits in one hour, or >= 6 distinct user agents (>= 50 hits).
    Polite full-site crawlers never 404, so the 404 rules miss them;
    no human day looks like that, or
  * one request with a signature no browser produces: a card image name
    glued onto a page path (/example/it/page_card; pages only link
    /img/cards/<name>_card.<ext>), or a Referer holding two URLs
    ("https://google.com, https://old.example.invalid/..."). The scraper behind
    them sends about one request per IP with a new 2017-2019 browser UA
    each time, so the volume rules above never fire for it.

Usage:
  check_bots.py [--log-dir DIR] [--output FILE] [--min-404 N]
                [--min-probes N]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from log_paths import botlist_path, prod_access_dir

ACCESS_RE = re.compile(
    r"^(?P<host>\S+) (?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "
    r'"(?P<method>[A-Z]+) (?P<target>\S+) HTTP/[^"]*" '
    r"(?P<status>\d{3}) \S+ \"(?P<referer>[^\"]*)\" \"(?P<ua>[^\"]*)\""
)

# Bot signatures: one matching request lists the IP.
CARD_PATH_RE = re.compile(r"/it/[\w-]+_card/?$")
FORGED_REFERER_RE = re.compile(r"^https?://\S*,\s+https?://", re.IGNORECASE)

KNOWN_BOT_UAS = (
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
    "yandeximages",
    "yandexvideo",
    "petalbot",
    "sogou",
    "exabot",
    "facebookexternalhit",
    "twitterbot",
    "linkedinbot",
    "discordbot",
    "telegrambot",
    "whatsapp",
    "slackbot",
    "redditbot",
    "pinterest",
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
    "flipboard",
    "embedly",
    "quora",
    "amazonbot",
    "applebot-extended",
    "oai-searchbot",
    "perplexity",
    "you-bot",
    "phantomjs",
    "headless",
    "masscan",
    "zgrab",
    "nmap",
    "nessus",
    "nikto",
    "sqlmap",
    "wpscan",
    "dirbuster",
    "gobuster",
    "feroxbuster",
    "thonk",
    "cfranklin",
    "wordpress",
)

PROBE_PREFIXES = ("/wp-", "/wp/", "/wordpress", "/xmlrpc", "/wlwmanifest")
PROBE_BASENAMES = frozenset(
    {
        "shell.php",
        "alfa.php",
        "c99.php",
        "r57.php",
        "wso.php",
        "b374k.php",
        "phpinfo.php",
        "info.php",
        "test.php",
        "debug.php",
        "admin.php",
        "wp-login.php",
        "xmlrpc.php",
        "wlwmanifest.xml",
        "phpmyadmin",
    }
)


def is_known_bot_ua(user_agent: str) -> bool:
    """True when the UA string matches a known bot/crawler/scanner token."""
    lowered = user_agent.lower()
    return any(token in lowered for token in KNOWN_BOT_UAS)


def is_probe_path(target: str) -> bool:
    """True for scanner-probe targets (wp shells, known bad basenames)."""
    path = urlsplit(target).path
    lowered = path.lower()
    if lowered.startswith(PROBE_PREFIXES):
        return True
    return os.path.basename(lowered) in PROBE_BASENAMES


def is_card_path(target: str) -> bool:
    """True for a card image name glued onto a page path (/x/it/gulp_card).

    Pages only link the card images as /img/cards/it/<name>_card.<ext>; the
    extensionless it/<name>_card under a page comes from a scraper that took
    the names from old example.invalid pages.
    """
    return bool(CARD_PATH_RE.search(urlsplit(target).path))


def is_forged_referer(referer: str) -> bool:
    """True for a Referer holding two comma-separated URLs; browsers send one."""
    return bool(FORGED_REFERER_RE.match(referer))


def default_paths() -> tuple[str, str]:
    """Locate the external access-log dir and the botlist in the log root."""
    return prod_access_dir(), botlist_path()


def iter_log_files(log_dir: str) -> list[str]:
    """Every regular file in the log dir, oldest first by name."""
    return sorted(os.path.join(log_dir, name) for name in os.listdir(log_dir) if os.path.isfile(os.path.join(log_dir, name)))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define and parse CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="", help="dir with access logs")
    parser.add_argument("--output", default="", help="botlist.csv to write")
    parser.add_argument("--min-probes", type=int, default=10, help="probe hits to list an IP")
    return parser.parse_args(argv)


@dataclass
class IpStats:  # pylint: disable=too-many-instance-attributes
    """Per-IP counters aggregated from access logs (one field per signal)."""

    hits_404: Counter[str] = field(default_factory=Counter)
    paths_404: dict[str, set[str]] = field(default_factory=dict)
    probe_hits: Counter[str] = field(default_factory=Counter)
    total_hits: Counter[str] = field(default_factory=Counter)
    bot_ua_hits: Counter[str] = field(default_factory=Counter)
    uas: dict[str, Counter[str]] = field(default_factory=dict)
    first_seen: dict[str, str] = field(default_factory=dict)
    last_seen: dict[str, str] = field(default_factory=dict)
    day_hits: Counter[tuple[str, str]] = field(default_factory=Counter)
    day_paths: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    day_uas: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    day_hour_hits: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    signature_hits: Counter[str] = field(default_factory=Counter)
    parsed: int = 0


def collect_stats(files: list[str]) -> IpStats:
    """Aggregate per-IP counters from access log files."""
    stats = IpStats()
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = ACCESS_RE.match(line.rstrip("\n"))
                if not match:
                    continue
                stats.parsed += 1
                ip = match.group("ip")
                ts = match.group("ts")
                day, hour = ts[:11], ts[12:14]
                stats.total_hits[ip] += 1
                stats.day_hits[(ip, day)] += 1
                stats.day_hour_hits[(ip, day, hour)] += 1
                user_agent = match.group("ua")
                stats.uas.setdefault(ip, Counter())[user_agent or "-"] += 1
                stats.day_paths.setdefault((ip, day), set()).add(match.group("target"))
                stats.day_uas.setdefault((ip, day), set()).add(user_agent or "-")
                if ip not in stats.first_seen:
                    stats.first_seen[ip] = match.group("ts")
                stats.last_seen[ip] = match.group("ts")
                if is_card_path(match.group("target")) or is_forged_referer(match.group("referer")):
                    stats.signature_hits[ip] += 1
                try:
                    status = int(match.group("status"))
                except ValueError:
                    continue
                if status != 404:
                    if is_known_bot_ua(user_agent):
                        stats.bot_ua_hits[ip] += 1
                    continue
                target = match.group("target")
                stats.hits_404[ip] += 1
                stats.paths_404.setdefault(ip, set()).add(target)
                if is_probe_path(target):
                    stats.probe_hits[ip] += 1
                if is_known_bot_ua(user_agent):
                    stats.bot_ua_hits[ip] += 1
    return stats


def behavioral_ips(stats: IpStats) -> set[str]:
    """IPs with at least one inhuman day: volume, breadth, burst or UA churn."""
    flagged: set[str] = set()
    for (ip, _day), total in stats.day_hits.items():
        if total >= 150:
            flagged.add(ip)
    for (ip, _day), paths in stats.day_paths.items():
        if len(paths) >= 100:
            flagged.add(ip)
    for (ip, _day, _hour), total in stats.day_hour_hits.items():
        if total >= 100:
            flagged.add(ip)
    for (ip, day), agents in stats.day_uas.items():
        if len(agents) >= 6 and stats.day_hits[(ip, day)] >= 50:
            flagged.add(ip)
    return flagged


def build_rows(stats: IpStats, min_probes: int) -> list[dict[str, Any]]:
    """Apply the listing rule and build botlist rows, worst first."""
    rows: list[dict[str, Any]] = []
    behavioral = behavioral_ips(stats)
    for ip, total in stats.total_hits.items():
        bot_share = stats.bot_ua_hits[ip] / total if total else 0.0
        rate_404 = stats.hits_404[ip] / total if total else 0.0
        if not ((bot_share >= 0.5 and total >= 10) or (stats.hits_404[ip] >= 10 and rate_404 >= 0.5) or stats.probe_hits[ip] >= min_probes or stats.hits_404[ip] >= 50 or ip in behavioral or stats.signature_hits[ip] > 0):
            continue
        top_ua, _ = stats.uas[ip].most_common(1)[0]
        sample = " | ".join(sorted(stats.paths_404.get(ip, set()))[:3])
        rows.append(
            {
                "ip": ip,
                "hits_404": stats.hits_404[ip],
                "distinct_paths": len(stats.paths_404.get(ip, set())),
                "first_seen": stats.first_seen[ip],
                "last_seen": stats.last_seen[ip],
                "top_user_agent": top_ua,
                "sample_paths": sample,
            }
        )
    rows.sort(key=lambda row: (-row["hits_404"], row["ip"]))
    return rows


def write_botlist(rows: list[dict[str, Any]], output: str) -> None:
    """Write rows to botlist.csv with the canonical columns.

    Written to <output>.tmp.<pid> and renamed over output, so the readers
    (build_metrika_db.py, analyze_errors.py) never see a cut-off list.
    """
    tmp_path = f"{output}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["ip", "hits_404", "distinct_paths", "first_seen", "last_seen", "top_user_agent", "sample_paths"],
            )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_path, output)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def main(argv: list[str] | None = None) -> int:
    """Entry point: aggregate logs, write botlist.csv, print summary."""
    args = parse_args(argv)
    default_log_dir, default_output = default_paths()
    log_dir = args.log_dir or default_log_dir
    output = args.output or default_output
    if not os.path.isdir(log_dir):
        print(f"ERROR: log dir '{log_dir}' not found.", file=sys.stderr)
        return 1
    files = iter_log_files(log_dir)
    if not files:
        print(f"ERROR: no log files in '{log_dir}'.", file=sys.stderr)
        return 1
    stats = collect_stats(files)
    rows = build_rows(stats, args.min_probes)
    write_botlist(rows, output)
    bot_ua_ips = sum(1 for ip, total in stats.total_hits.items() if total >= 10 and stats.bot_ua_hits[ip] / total >= 0.5)
    print(f"parsed lines: {stats.parsed} from {len(files)} files, distinct ips: {len(stats.total_hits)}")
    signature_ips = sum(1 for hits in stats.signature_hits.values() if hits > 0)
    print(f"listed bot ips: {len(rows)} (known-UA majority: {bot_ua_ips}, signature: {signature_ips}), wrote: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
