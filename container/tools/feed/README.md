# tools/feed — article feed (src/feed/)

`src/feed/` is a reverse-chronological feed of created/modified articles.
`src/feed/index.php` shows the newest 500 entries with a `[created]` /
`[modified]` badge; older ones spill into `src/feed/arch.json`, rendered
by `src/feed/arch/index.php`.

## Files

| File | Role |
|---|---|
| `src/feed/feed.json` | newest 500 `{url, title, date, action}` entries |
| `src/feed/arch.json` | everything older (same shape, uncapped) |
| `src/feed/index.php` | BasePage, first 500 + link to `/feed/arch/` |
| `src/feed/arch/index.php` | BasePage, full archive + link back to `/feed/` |
| `tools/feed/if_file_is_an_article.py` | article-or-not classifier (module + CLI) |
| `tools/feed/post_to_feed.py` | hook logic: committed paths → feed files |
| `tools/feed/post-to-feed.sh` | post-commit entry point |

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

## Hook behaviour

The post-commit hook examines the commit that just completed. Run
`pre-commit install` to install both the normal pre-commit gates and this
post-commit hook. Because a post-commit hook cannot alter an existing commit,
updated feed files are staged for the next commit; the hook does not create a
follow-up commit.

Event rules (shared by the hook and one-off backfills, see `apply_event`):

- `created`: once per URL ever — keeps its original date, never
  overwritten or duplicated.
- `modified`: at most one line per URL per day — a same-day re-edit
  refreshes that day's line and moves it to the top.
- `moved` (git rename ≥40% similarity, or a file rewritten into a
  redirect stub): one line per URL (refresh wins); the old URL's lines
  are retired. Deleted pages are dropped entirely.

Committed status decides the badge: added (`A`) → `created`, renamed →
`moved`, anything else → `modified`. `feed/` paths are never posted (no
self-reference). The hook stages updated `feed.json` and `arch.json` for the
next commit; it exits successfully whether or not the feed changed.

Title twins across trees (`/extra/…`, `/…`, `/ru/…`) or course languages are
distinct pages and are intentionally kept — only renames collapse.

## Backfill

One-off rebuild from git history (action = added-in-range ? created :
modified, newest commit first):

```bash
git log --since="7 days ago" --name-only --diff-filter=ACM \
  --pretty=format:"COMMIT %h %ad" --date=short -- container/src
```

then classify + title-extract per file (see session 2026-10-09: 2,139
articles, 500 in feed, rest in archive).
