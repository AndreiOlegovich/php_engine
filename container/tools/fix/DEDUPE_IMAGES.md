# `dedupe-images.py` manual

Removes byte-duplicate images from one tree after rewiring every
reference to the surviving twin. Lives in `container/tools/fix/`;
all commands run from `container/`.

## Quick start

```bash
# 1. preview (writes nothing)
./tools/fix/dedupe-images.py --dir src/images

# 2. apply
./tools/fix/dedupe-images.py --dir src/images --apply

# 3. verify
git status --porcelain -- src/ | head
```

## How it works, step by step

1. **Collect** every image (`.png` `.jpg` `.jpeg` `.gif` `.bmp` `.webp`
   `.svg` `.ico` `.tif` `.tiff` `.avif`) under `--dir`.
2. **Match** by md5 against all images under `src/`. Only byte-identical
   twins qualify — same name with different bytes is left alone. Twin
   choice is deterministic: first sorted path outside `--dir`.
3. **Index references once**: a single walk over `*.php` extracts
   `/...`-style path tokens and matches them exactly (with a boundary
   guard, so `/images/img/a.jpg` never rewrites inside `/images/img/a.jpg-large`,
   while `/images/img/a.jpg?v=2` still rewrites correctly).
4. **Rewrite** absolute-path references to the twin's URL. Files that
   fail `php -l` afterwards are restored, not committed half-broken.
5. **Delete** the duplicate only when zero references remain — checked
   across `*.php` **plus** `*.css`/`*.js`/`*.html` as a safety net
   (those are never edited, only counted).
6. **Redirects**: one `Redirect 301 <old-url> <new-url>` per deletion,
   appended to `--redirect-file` (default
   `images-redirects.htaccess`, same style as the live `.htaccess`).

## Statuses

| Status | Meaning |
|---|---|
| `FIXED` / `DELETED` | refs rewritten, duplicate removed |
| `WOULD-FIX` | dry-run preview (nothing written) |
| `ORPHAN-DYNAMIC` | twin exists, no static refs, **a fully resolved loader covers it** — reported only, never auto-deleted (dynamic loading proven for this dir) |
| `ORPHAN-DYNAMIC?` | same, but the loader is **partially resolved** (unknown branches: other-file variables, `$_GET`, call-built paths) — weaker evidence, same policy |
| `ORPHAN-CLEAR` | twin exists, no static refs, **no loader covers it anywhere** — strongest delete candidates, still human-confirmed (absence of evidence is not evidence) |
| `SKIP (STILL-REFERENCED ...)` | css/js/html still point at it — left alone |
| `SKIP (LINT-FAIL ...)` | rewritten file failed `php -l` — restored |

## Certainty: what the verdicts do and do not prove

The resolver handles, per file: string literals, `.` concatenation,
`$_SERVER['DOCUMENT_ROOT']` (→ repo `src/`), `__DIR__` (→ the file's
dir), reassignments (union of all values), `.=` appends, and function
parameters resolved via same-file literal call sites. It stops at:
values from other files, `$_GET`/DB/glob results, `dirname()`/
`basename()` chains, and object/property access.

* **100% clear**: `ORPHAN-DYNAMIC` — every step above resolved; the log
  line names the exact loader (`file:line via func(dir)`), reproducible
  by reading that call.
* **Uncertain by construction**: `ORPHAN-DYNAMIC?` — at least one branch
  could not be resolved; the evidence line is a lead, not proof.
* **Uncertain by nature**: `ORPHAN-CLEAR` — no loader found, which proves
  nothing about dynamic loading; and `WOULD-FIX`/`FIXED` counts assume
  static refs are the whole story for those files.

## Real run (src/images, reference run)

`3331` images scanned, `2977` with twins elsewhere: **382 fixed and
deleted** (135 `.php` files touched), 2590 orphans reported-only,
5 skipped, 382 redirect rules written — all targets verified on disk,
zero dangling references left, smoke pages HTTP 200.

## Limits

* Only absolute `/...` references are rewritten. Relative refs
  (`img/foo.jpg`) and PHP-built names (`$dir . $i . ".jpg"`) are
  reported as orphans at best — review them by hand.
* One twin wins; if two outside copies exist the choice is
  alphabetical, not "best".
* Deleting 300+ files at once is reviewed with `git status` + spot
  `git diff` before committing, same as any fixer.

## Files

```text
tools/fix/dedupe-images.py   the script (stdlib only, ruff-clean)
images-redirects.htaccess   generated redirects (review targets, then merge)
```
