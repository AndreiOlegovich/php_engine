"""Build the article feed from the history of main and commit it.

The feed is derived data: nothing writes it while you commit. A run walks
the first-parent commits of main after the commit recorded in feed.json
("through"), oldest first. For each commit it reads the changed
container/src/**/*.php paths (a merge is compared with its first parent),
keeps the ones that are articles (see if_file_is_an_article.py), extracts
each title, and folds {url, title, date, action} events into the feed;
action is 'created', 'modified' or 'moved' and the date is the commit
date. Event rules: one Created per URL ever (never overwritten), at most
one Modified line per URL per day, one Moved line per URL (refresh wins);
moves and deletions retire old URLs. feed.json holds the newest MAX_ITEMS
entries; older ones spill into feed/arch.json, rendered by feed/arch/.

A run holds a lock in the git common dir, so two runs (or two worktrees)
never write the feed at once. Both files are replaced atomically, then
committed with `git commit -- <the two paths>`: whatever else is staged
stays staged and out of that commit.
"""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from if_file_is_an_article import (  # pylint: disable=wrong-import-position
    is_article,
    page_title,
    page_url,
    redirect_target,
    url_to_file,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _profile_value(key: str) -> str:
    """A value from container/site-profile.env, or "" when the file has none."""
    path = os.path.join(REPO_ROOT, "container", "site-profile.env")
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return ""
    prefix = key + "="
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        return line[len(prefix) :].strip().strip('"').strip("'")
    return ""


def _setting(key: str, default: str) -> str:
    """The environment wins over the profile; the profile wins over the default."""
    return os.environ.get(key) or _profile_value(key) or default


FEED_DIR = _setting("SITE_FEED_DIR", "feed").strip("/") or "feed"
FEED_REL = f"container/src/{FEED_DIR}/feed.json"
ARCH_REL = f"container/src/{FEED_DIR}/arch.json"
MAX_ITEMS = 500
LOCK_NAME = "php_engine-feed.lock"
GENERATED_CARD_DIRS = [item.strip("/") for item in _setting("SITE_GENERATED_CARD_DIRS", "").replace(",", " ").split() if item.strip("/")]


class FeedError(Exception):
    """A run that must stop before it writes anything."""


def _git(*args: str, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise FeedError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def _feed_path(rel: str) -> str:
    return os.path.join(REPO_ROOT, rel)


def committed_changes(commit: str = "HEAD") -> tuple[list[tuple[str, str, str | None]], list[str]]:
    """Commit src changes as ([(path, action, old_path)], dropped_urls).

    A root commit is compared with the empty tree, any other commit
    (merges too) with its first parent. action is 'created' (added),
    'modified', or 'moved' (git rename with >=40% similarity, or a file
    rewritten into a redirect stub). dropped_urls are urls of deleted pages.
    """
    parents = _git("rev-list", "--parents", "-n", "1", commit).split()
    base = ["--root", commit] if len(parents) < 2 else [parents[1], commit]
    out = _git(
        "diff-tree",
        "--no-commit-id",
        "--find-renames=40%",
        "--name-status",
        "--diff-filter=ACMRD",
        "-r",
        *base,
    )
    changes: list[tuple[str, str, str | None]] = []
    dropped: list[str] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) == 3:
            old, new = parts[1], parts[2]
            if new.endswith(".php"):
                changes.append((new, "moved", old))
        elif status.startswith("D"):
            path = parts[-1]
            if path.startswith("container/src/") and path.endswith(".php"):
                dropped.append(_path_to_url(path))
        elif len(parts) == 2:
            path = parts[1]
            if not path.endswith(".php"):
                continue
            action = "created" if status.startswith("A") else "modified"
            changes.append((path, action, None))
    return changes, dropped


def _path_to_url(path: str) -> str:
    """URL guess for a (possibly deleted) src path."""
    rel = path[len("container/src/") :] if path.startswith("container/src/") else path
    if rel.endswith("/index.php"):
        return "/" + rel[: -len("index.php")]
    return "/" + rel


def load_feed(path: str) -> tuple[list[dict[str, str]], str | None]:
    """(items, through) from a feed json file; a missing or corrupt file is empty."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return [], None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return [], None
    items = []
    for item in data["items"]:
        if isinstance(item, dict) and item.get("url") and item.get("title"):
            items.append(
                {
                    "url": str(item["url"]),
                    "title": str(item["title"]),
                    "date": str(item.get("date", "")),
                    "action": str(item.get("action", "")),
                }
            )
    through = data.get("through")
    return items, str(through) if through else None


def load_items(path: str) -> list[dict[str, str]]:
    """Feed items from a json file (tolerates a missing or corrupt file)."""
    return load_feed(path)[0]


def _dump(items: list[dict[str, str]], through: str | None) -> str:
    data: dict[str, object] = {"through": through} if through else {}
    data["items"] = items
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_atomic(path: str, text: str) -> None:
    """Replace path with text through a temp file in the same directory."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".feed-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _post_moved_stub(path: str, abs_path: str, today: str, posted: list[dict[str, str]]) -> bool:
    """Post the target of a redirect stub as moved; True when posted."""
    target = redirect_target(abs_path)
    if target is None:
        return False
    target_file = url_to_file(target)
    if not is_article(target_file):
        return False
    title = page_title(target_file)
    if title is None:
        print(f"publish-feed: skip {path} (no title at {target})")
        return False
    posted.append({"url": target, "title": title, "date": today, "action": "moved"})
    return True


def _is_generated_reference(path: str) -> bool:
    """True for a thin generated card under SITE_GENERATED_CARD_DIRS.

    A site that generates a batch of thin pages (a function reference, an API
    index) names their parent folder in site-profile.env. Posting each card as
    a Created event would push every real article out of the feed; the hub
    (<folder>/index.php) and the hand-written files next to it are still
    posted.
    """
    rel = path.removeprefix("container/src/").replace("\\", "/")
    for folder in GENERATED_CARD_DIRS:
        prefix = folder + "/"
        if not rel.startswith(prefix) or not rel.endswith("/index.php"):
            continue
        card = rel[len(prefix) : -len("/index.php")]
        if card and "/" not in card:
            return True
    return False


def _collect_posts(changes: list[tuple[str, str, str | None]], dropped: list[str], today: str) -> tuple[list[dict[str, str]], list[str]]:
    """Turn committed changes into feed entries plus retired old URLs."""
    posted: list[dict[str, str]] = []
    retired = list(dropped)
    for path, action, old_path in changes:
        if not path.startswith("container/src/"):
            continue
        if path.startswith(f"container/src/{FEED_DIR}/"):
            continue  # never post the feed to itself
        if _is_generated_reference(path):
            continue  # 900+ generated cards would flush the feed
        abs_path = os.path.join(REPO_ROOT, path)
        if action == "moved" and old_path is not None:
            retired.append(_path_to_url(old_path))
        if not is_article(abs_path):
            _post_moved_stub(path, abs_path, today, posted)
            continue
        title = page_title(abs_path)
        if title is None:
            print(f"publish-feed: skip {path} (no title found)")
            continue
        posted.append({"url": page_url(abs_path), "title": title, "date": today, "action": action})
    return posted, retired


def apply_event(entries: list[dict[str, str]], url: str, title: str, date: str, action: str) -> list[dict[str, str]]:
    """Fold one feed event into the newest-first list.

    created: once per URL ever (keeps its original date, never
        overwritten or duplicated).
    modified: at most one line per URL per day (same-day re-edit
        refreshes that day's line and moves it to the top).
    moved: one line per URL (refresh wins).
    """
    if action == "created":
        if any(e["url"] == url and e["action"] == "created" for e in entries):
            return entries
        return [{"url": url, "title": title, "date": date, "action": action}] + entries
    if action == "modified":
        rest = [e for e in entries if not (e["url"] == url and e["action"] == "modified" and e["date"] == date)]
        return [{"url": url, "title": title, "date": date, "action": action}] + rest
    rest = [e for e in entries if not (e["url"] == url and e["action"] == "moved")]
    return [{"url": url, "title": title, "date": date, "action": action}] + rest


def commits_after(through: str, branch: str) -> list[tuple[str, str]]:
    """[(sha, commit date)] of first-parent commits after through, oldest first."""
    if not _is_ancestor(through, branch):
        raise FeedError(f"{through} is not an ancestor of {branch}; pass --from <commit> to restart")
    out = _git("log", "--first-parent", "--reverse", "--format=%H %cs", f"{through}..{branch}")
    return [(line.split()[0], line.split()[1]) for line in out.splitlines() if line.strip()]


def _is_ancestor(commit: str, branch: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, branch], cwd=REPO_ROOT, check=False).returncode == 0


def build(entries: list[dict[str, str]], commits: list[tuple[str, str]], skip: set[str]) -> tuple[list[dict[str, str]], list[str], int]:
    """Fold every commit into entries; returns (entries, report lines, posted count)."""
    report: list[str] = []
    count = 0
    for sha, date in commits:
        if sha in skip:
            report.append(f"{sha[:9]} skipped (--skip)")
            continue
        changes, dropped = committed_changes(sha)
        posted, retired = _collect_posts(changes, dropped, date)
        if not posted and not retired:
            continue
        gone = set(retired)
        entries = [e for e in entries if e["url"] not in gone]
        for entry in posted:
            entries = apply_event(entries, entry["url"], entry["title"], entry["date"], entry["action"])
        count += len(posted)
        for entry in posted:
            report.append(f"{sha[:9]} {entry['date']} {entry['action']:<8} {entry['url']}")
        for url in sorted(gone):
            report.append(f"{sha[:9]} {date} retired  {url}")
    return entries, report, count


@contextmanager
def feed_lock() -> Iterator[None]:
    """Exclusive lock shared by every worktree of the repository."""
    common = _git("rev-parse", "--git-common-dir").strip()
    path = os.path.join(REPO_ROOT, common, LOCK_NAME) if not os.path.isabs(common) else os.path.join(common, LOCK_NAME)
    with open(path, "a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FeedError(f"another publish-feed run holds {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _check_checkout(branch: str) -> None:
    current = _git("symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
    if current != branch:
        raise FeedError(f"run it on {branch} (current checkout: {current or 'detached HEAD'}); titles are read from the working tree")
    dirty = _git("status", "--porcelain", "--", FEED_REL, ARCH_REL).strip()
    if dirty:
        raise FeedError(f"the feed files differ from HEAD; drop those edits first:\n  git restore --staged --worktree -- {FEED_REL} {ARCH_REL}\n" + dirty)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="publish-feed.sh", description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--dry-run", action="store_true", help="print the entries, write and commit nothing")
    parser.add_argument("--from", dest="from_commit", metavar="COMMIT", help="start after COMMIT instead of the one stored in feed.json")
    parser.add_argument("--skip", action="append", default=[], metavar="COMMIT", help="leave COMMIT out of the feed (repeatable), e.g. a bulk plumbing edit")
    parser.add_argument("--branch", default="main", help="branch to publish from (default: main)")
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    """Build the feed from history and commit it; returns the exit code."""
    args = _parse(argv)
    feed_file, arch_file = _feed_path(FEED_REL), _feed_path(ARCH_REL)
    with feed_lock():
        _check_checkout(args.branch)
        feed_items, stored = load_feed(feed_file)
        through = args.from_commit or stored
        if not through:
            raise FeedError(f"{FEED_REL} records no 'through' commit; pass --from <last commit already in the feed>")
        through = _git("rev-parse", "--verify", f"{through}^{{commit}}").strip()
        skip = {_git("rev-parse", "--verify", f"{s}^{{commit}}").strip() for s in args.skip}
        commits = commits_after(through, args.branch)
        if not commits:
            print(f"publish-feed: nothing after {through[:9]}")
            return 0
        tip = commits[-1][0]
        before = feed_items + load_items(arch_file)
        entries, report, posted = build(list(before), commits, skip)
        print(f"publish-feed: {len(commits)} commits {through[:9]}..{tip[:9]}")
        for line in report:
            print("  " + line)
        if entries == before and not skip and not args.from_commit:
            print("publish-feed: no article changes, nothing to commit")
            return 0
        if args.dry_run:
            print("publish-feed: dry run, nothing written")
            return 0
        write_atomic(feed_file, _dump(entries[:MAX_ITEMS], tip))
        write_atomic(arch_file, _dump(entries[MAX_ITEMS:], None))
        noun = "entry" if posted == 1 else "entries"
        message = f"Feed: {posted} {noun} from {through[:9]}..{tip[:9]}" if posted else f"Feed: mark {tip[:9]} as published"
        result = subprocess.run(["git", "commit", "-m", message, "--", FEED_REL, ARCH_REL], cwd=REPO_ROOT, check=False)
        if result.returncode != 0:
            print(f"publish-feed: the commit failed; the files are written. Undo with: git restore -- {FEED_REL} {ARCH_REL}", file=sys.stderr)
            return result.returncode
    return 0


def main(argv: list[str]) -> int:
    """CLI entry: argv without the program name."""
    try:
        return run(argv)
    except FeedError as exc:
        print(f"publish-feed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
