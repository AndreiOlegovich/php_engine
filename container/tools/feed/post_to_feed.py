"""Post article changes from the last commit to container/src/feed/feed.json.

Reads committed container/src/**/*.php paths (argv or HEAD), keeps the
ones that are articles (see if_file_is_an_article.py), extracts each title,
and records {url, title, date, action} events (action is
'created', 'modified' or 'moved'). Event rules: one Created per URL ever
(never overwritten), at most one Modified line per URL per day, one Moved
line per URL (refresh wins); moves and deletions retire old URLs. The
feed page holds the newest MAX_ITEMS entries; older ones spill into
feed/arch.json, rendered by feed/arch/. Updated feed files are staged for
the next commit; a post-commit hook cannot change the commit that just ran.
"""

import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from if_file_is_an_article import (  # pylint: disable=wrong-import-position
    is_article,
    page_title,
    page_url,
    redirect_target,
    url_to_file,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC_DIR = os.path.join(REPO_ROOT, "container", "src")
FEED_FILE = os.path.join(SRC_DIR, os.environ.get("SITE_FEED_DIR", "feed"), "feed.json")
ARCH_FILE = os.path.join(SRC_DIR, "feed", "arch.json")
MAX_ITEMS = int(os.environ.get("SITE_FEED_MAX", "40"))


def committed_changes(commit: str = "HEAD") -> tuple[list[tuple[str, str, str | None]], list[str]]:
    """Commit src changes as ([(path, action, old_path)], dropped_urls).

    action is 'created' (added), 'modified', or 'moved' (git rename with
    >=40% similarity, or a file rewritten into a redirect stub).
    dropped_urls are old URLs replaced by moves and urls of deleted pages.
    """
    out = subprocess.run(
        [
            "git",
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--find-renames=40%",
            "--name-status",
            "--diff-filter=ACMRD",
            "-r",
            commit,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    changes: list[tuple[str, str, str | None]] = []
    dropped: list[str] = []
    for line in out.stdout.splitlines():
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


def load_items(path: str) -> list[dict[str, str]]:
    """Feed items from a json file (tolerates a missing or corrupt file)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return []
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
    return items


def store_items(path: str, items: list[dict[str, str]]) -> bool:
    """Write items back; True when the file changed."""
    after = json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n"
    try:
        with open(path, encoding="utf-8") as handle:
            before = handle.read()
    except OSError:
        before = ""
    if after == before:
        return False
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(after)
    subprocess.run(["git", "add", path], cwd=REPO_ROOT, check=False)
    return True


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
        print(f"post-to-feed: skip {path} (no title at {target})")
        return False
    posted.append({"url": target, "title": title, "date": today, "action": "moved"})
    return True


def _is_generated_reference(path: str) -> bool:
    """True for a generated section/functions/<name>/index.php card.

    The function reference is one generated batch of 900+ thin pages. Posting
    each as a Created event would push every real article out of the feed; the
    hub (section/functions/index.php) and the hand-written files next to it are
    still posted.
    """
    rel = path.removeprefix("container/src/").replace("\\", "/")
    parts = rel.split("/")
    return len(parts) == 5 and parts[:3] == ["dev", "php", "functions"] and parts[4] == "index.php"


def _collect_posts(changes: list[tuple[str, str, str | None]], dropped: list[str], today: str) -> tuple[list[dict[str, str]], list[str]]:
    """Turn committed changes into feed entries plus retired old URLs."""
    posted: list[dict[str, str]] = []
    retired = list(dropped)
    for path, action, old_path in changes:
        if not path.startswith("container/src/"):
            continue
        if path.startswith("container/src/feed/"):
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
            print(f"post-to-feed: skip {path} (no title found)")
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


def _merge_and_store(posted: list[dict[str, str]], retired: list[str]) -> bool:
    """Merge entries into feed/arch files; True when anything changed."""
    entries = load_items(FEED_FILE) + load_items(ARCH_FILE)
    gone = set(retired)
    entries = [e for e in entries if e["url"] not in gone]
    for entry in posted:
        entries = apply_event(entries, entry["url"], entry["title"], entry["date"], entry["action"])
    changed = store_items(FEED_FILE, entries[:MAX_ITEMS])
    return store_items(ARCH_FILE, entries[MAX_ITEMS:]) or changed


def main(argv: list[str]) -> int:
    """Prepend articles from the last commit and stage feed changes afterward.

    The feed page holds the newest MAX_ITEMS entries; older ones spill
    into arch.json, rendered by feed/arch/. Event rules: one Created per
    URL ever, at most one Modified line per URL per day, one Moved line
    per URL; moves and deletions retire old URLs.
    """
    changes, dropped = committed_changes()
    if len(argv) > 1:
        wanted = set(argv[1:])
        changes = [change for change in changes if change[0] in wanted or (change[2] or "") in wanted]
    posted, retired = _collect_posts(changes, dropped, datetime.date.today().isoformat())
    if not posted and not retired:
        return 0
    if not _merge_and_store(posted, retired):
        return 0
    if posted:
        print("post-to-feed: posted " + ", ".join(entry["url"] for entry in posted))
    if retired:
        print("post-to-feed: retired " + ", ".join(sorted(set(retired))))
    print("post-to-feed: feed files updated and staged for the next commit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
