# tools/feed — article feed (src/feed/)

`src/feed/` is a reverse-chronological feed of created/modified articles.
`src/feed/index.php` shows the newest 500 entries with a `[created]` /
`[modified]` badge; older ones spill into `src/feed/arch.json`, rendered
by `src/feed/arch/index.php`.

## Files

| File | Role |
|---|---|
| `src/feed/feed.json` | newest 500 `{url, title, date, action}` entries, plus `through`: the last commit of main already in the feed |
| `src/feed/arch.json` | everything older (same shape, uncapped, no `through`) |
| `src/feed/index.php` | BasePage, first 500 + link to `/feed/arch/` |
| `src/feed/arch/index.php` | BasePage, full archive + link back to `/feed/` |
| `tools/feed/if_file_is_an_article.py` | article-or-not classifier (module + CLI) |
| `tools/feed/post_to_feed.py` | builder: main's history → feed files → feed commit |
| `tools/feed/publish-feed.sh` | entry point |

## Article detection

`is_article(path)`: under `container/src/`, a `.php` file outside excluded
paths (`pc/`, `demo/`, dot-/underscore-led dirs, `*img*` dirs, `arj*`,
editor `*copy*` files, the `.php/` engine tree) whose source builds a page
(`new ArticlePage/Page/BasePage/DictPage`, `_template.php`, legacy
`_010_header` / `_020_header*` / `_head_inc` / `080_counter` / `090_bottom` /
`_footer.php` / `header-up.php`). Title: `title:` ctor arg, legacy
`$page_title`, then `<title>` tag. URL: `index.php` → dir URL, other files
keep `.php`. CLI prints `ARTICLE`/`NOTARTICLE` per path, exit 1 unless all
are articles — the same check answers "is this sitemap loc a real page".

## Publishing the feed

No hook writes the feed. It is built from the history of main and committed
on its own, so a normal commit never carries feed changes and a worktree
never edits the feed.

```bash
cd ~/github/php_engine                       # the main checkout, on main
container/tools/feed/publish-feed.sh --dry-run   # list the entries, change nothing
container/tools/feed/publish-feed.sh             # write and commit
```

A run:

1. takes an exclusive lock, `php_engine-feed.lock` in the git common dir
   (`.git/`), shared by every worktree; a second run stops with
   "another publish-feed run holds …";
2. refuses unless the checkout is on `main` (titles are read from the
   working tree) and the two feed files equal HEAD;
3. walks `git log --first-parent --reverse <through>..main`, oldest first;
   each commit is compared with its first parent, so a merge posts the
   pages its branch brought in, dated by the merge;
4. replaces `feed.json` and `arch.json` atomically (temp file + rename) and
   commits only them:
   `git commit -m "Feed: N entries from A..B" -- container/src/feed/feed.json container/src/feed/arch.json`.
   Anything else that is staged stays staged and out of that commit.

A range with no article changes writes and commits nothing; the next run
reads it again. Options:

| Option | Effect |
|---|---|
| `--dry-run` | print the commits and entries, write nothing |
| `--skip COMMIT` | leave a commit out, e.g. a bulk edit of many pages with no visible change; repeatable; the marker moves past it |
| `--from COMMIT` | start after COMMIT instead of `through` (first run, or after history was rewritten) |
| `--branch NAME` | publish from another branch (tests) |

Event rules (`apply_event`):

- `created`: once per URL ever — keeps its original date, never
  overwritten or duplicated.
- `modified`: at most one line per URL per day — a same-day re-edit
  refreshes that day's line and moves it to the top.
- `moved` (git rename ≥40% similarity, or a file rewritten into a
  redirect stub): one line per URL (refresh wins); the old URL's lines
  are retired. Deleted pages are dropped entirely.

Committed status decides the badge: added (`A`) → `created`, renamed →
`moved`, anything else → `modified`. The date is the commit date (`%cs`).
`feed/` paths are never posted (no self-reference); the thin cards of the
folders named in `SITE_GENERATED_CARD_DIRS` are skipped.

Title twins across trees (`/ao/…`, `/…`, `/ru/…`) or course languages are
distinct pages and are intentionally kept — only renames collapse.

## Branches made before the change

Until 2026-11-16 a post-commit hook (`tools/feed/post-commit`, linked as
`.git/hooks/post-commit`) wrote the feed after every commit, in every
worktree, and staged it. Branches from that time may still carry feed
edits. Merge them without those edits, so the next run posts the merge:

```bash
git merge --no-commit agent/<slug>
git checkout HEAD -- container/src/feed/feed.json container/src/feed/arch.json
git commit --no-edit
```

A clone that still has the link removes it: `rm .git/hooks/post-commit`
(it points at a file that no longer exists).
