# version 0.1.0
"""Find internal <a href> targets that do not resolve to a page in src/.

Walks container/src/**/*.php, extracts href="/..." links, and classifies
each target as OK (file/dir exists), VIA REDIRECT (matches a Redirect/
RedirectMatch source in .htaccess), or BROKEN (404 on prod).

Usage:
  find_broken_hrefs.py [--src DIR] [--htaccess FILE] [--top N]
                       [--output FILE] [--include-redirected]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import htaccess_redirects  # noqa: E402  # pylint: disable=wrong-import-position,import-error

HREF_RE = re.compile(
    r"<a\s[^>]*href=(?P<q>[\"'])(?P<href>/[^\"'#]*?)(?:[#?][^\"']*)?(?P=q)",
    re.IGNORECASE,
)


@dataclass
class Finding:
    """One broken (or redirected) internal link."""

    source: str
    target: str
    status: str


@dataclass
class Report:
    """Aggregated findings."""

    broken: list[Finding] = field(default_factory=list)
    via_redirect: list[Finding] = field(default_factory=list)
    checked_pages: int = 0
    checked_links: int = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define and parse CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="", help="local container/src dir")
    parser.add_argument("--htaccess", default="", help="path to .htaccess")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--output", default="")
    parser.add_argument("--include-redirected", action="store_true")
    return parser.parse_args(argv)


def default_paths() -> tuple[str, str]:
    """Locate container/src and its .htaccess from the script location."""
    here = os.path.dirname(os.path.realpath(__file__))
    repo = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    src = os.path.join(repo, "container", "src")
    return src, os.path.join(src, ".htaccess")


def load_redirect_sources(htaccess: str) -> list[str]:
    """URL-path regexes of every redirect in .htaccess (either rule form)."""
    return [rule.pattern for rule in htaccess_redirects.load(Path(htaccess)) if rule.is_redirect]


def target_exists(src_root: str, target: str) -> bool:
    """Check whether an internal target resolves to a file in src/."""
    rel = target.lstrip("/")
    if os.path.isfile(os.path.join(src_root, rel)):
        return True
    if target.endswith("/"):
        return os.path.isfile(os.path.join(src_root, rel, "index.php"))
    if os.path.isdir(os.path.join(src_root, rel)):
        return True
    return os.path.isfile(os.path.join(src_root, rel, "index.php"))


def matches_redirect(target: str, patterns: list[str]) -> bool:
    """Check whether a target is covered by an .htaccess redirect rule."""
    return any(re.search(pattern, target) for pattern in patterns)


def extract_hrefs(text: str) -> list[str]:
    """Return internal href targets from page HTML (no anchors/queries)."""
    return [m.group("href") for m in HREF_RE.finditer(text)]


def audit_page(src_root: str, rel: str, patterns: list[str]) -> tuple[list[Finding], list[Finding], int]:
    """Audit one page; return (broken, via_redirect, link_count)."""
    broken, via_redirect = [], []
    with open(os.path.join(src_root, rel), encoding="utf-8", errors="replace") as h:
        text = h.read()
    hrefs = extract_hrefs(text)
    for target in hrefs:
        if target_exists(src_root, target):
            continue
        if matches_redirect(target, patterns):
            via_redirect.append(Finding(source=rel, target=target, status="redirect"))
        else:
            broken.append(Finding(source=rel, target=target, status="broken"))
    return broken, via_redirect, len(hrefs)


def audit_all(src_root: str, htaccess: str) -> Report:
    """Walk src/ and audit every PHP page."""
    patterns = load_redirect_sources(htaccess)
    report = Report()
    for root, _dirs, files in os.walk(src_root):
        for name in sorted(files):
            if not name.endswith(".php"):
                continue
            rel = os.path.relpath(os.path.join(root, name), src_root)
            broken, via, count = audit_page(src_root, rel, patterns)
            report.broken.extend(broken)
            report.via_redirect.extend(via)
            report.checked_pages += 1
            report.checked_links += count
    return report


def render(report: Report, top: int, include_redirected: bool) -> str:
    """Build the text report."""
    broken_targets = Counter(f.target for f in report.broken)
    lines = [
        f"pages: {report.checked_pages}, links: {report.checked_links}, broken hits: {len(report.broken)}, unique broken targets: {len(broken_targets)}, via-redirect hits: {len(report.via_redirect)}",
        "",
        f"=== broken targets (top {min(top, len(broken_targets))}) ===",
    ]
    for target, count in broken_targets.most_common(top):
        sources = sorted({f.source for f in report.broken if f.target == target})
        lines.append(f"[{count}x in {len(sources)} pages] {target}")
        for source in sources[:5]:
            lines.append(f"  from: {source}")
        if len(sources) > 5:
            lines.append(f"  ... and {len(sources) - 5} more")
    if include_redirected:
        lines.append("")
        lines.append("=== via redirect (top 20) ===")
        for target, count in Counter(f.target for f in report.via_redirect).most_common(20):
            lines.append(f"[{count}x] {target}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    default_src, default_htaccess = default_paths()
    src_root = args.src or default_src
    htaccess = args.htaccess or default_htaccess
    report = audit_all(src_root, htaccess)
    text = render(report, args.top, args.include_redirected)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
