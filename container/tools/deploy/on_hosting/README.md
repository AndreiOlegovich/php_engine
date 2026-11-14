# on_hosting: dev → prod copy scripts (run ON the server)

All wrappers share source/destination handling: contents of
`DEV_DOCROOT` (`/home/user/dev/public_html`) into
`PROD_DOCROOT` (`/home/user/example.com/public_html`), overridable
via `site.env` (`SITE_ENV`). See `../DEPLOY_FTP.md`.

These scripts are meant to run on the server with **no arguments**: the
paths above are built in (or come from `site.env`). Explicit dirs are a
last-resort override only when there is no other way.

| Script | Behaviour |
|---|---|
| `copy-dev-to-prod.sh` | New files/dirs only, never overwrites. `-f` adds overwrite-with-backup. |
| `copy-dev-to-prod-force.sh` | Always overwrites differing files (backup first). |
| `copy-dev-to-prod-new-only.sh` | New + changed files; change = size **or** content (`--duplicate-check full` forced). |
| `copy-smartly-dev-to-prod.sh` | Finder-driven smart sync (below). |
| `copy-smartly-dev-to-prod-verbose.sh` | Same engine, verbose by default, full output tee'd to `copy_smartly_verbose.log` next to the script. |
| `find-dirs-with-changes.sh` | Lists changed dirs at `--max-depth` from a snapshot; used by the smart copy. |
| `copy-selected-dev-to-prod.sh` | Explicit file list variant. |
| `copy-with-shadow-backup.sh` | Engine used by all wrappers (unbounded copy); no args = dev→prod defaults. |
| `copy-engine.sh` | Engine-only copy (`.php`/`.js`/`.css` trees + root `.htaccess`, `sitemap.xml`, `robots.txt`): no args = dev→prod defaults; new files, or overwrite with backup under `-f`. |
| `copy-engine-force.sh` | Same, but forced by default: every in-scope file is copied, identical ones included (`--no-force` opts out). |
| `copy_user_profiles_prod_to_dev.sh` | Stages prod `services/user_profile/data/*.jsonl` into `dev/user_profiles/` for FTP pickup. |
| `refresh-live-access-log.sh` | Deletes the staged live access log and copies it fresh (drops stale ownership; dated logs are copied by cron separately). |
| `delete-from-hosting.sh` | Deletes the files listed in a file (one absolute path per line), each backed up into `backup_deleted_html` first. |
| `delete-prod-missing-on-dev.sh` | Deletes prod files that are missing on dev, through `delete-from-hosting.sh`; `--dry-run` lists the candidates (below). |

## Deleting prod files that are missing on dev

The copy scripts only add or overwrite, so a file removed from dev stays
on prod. Run the dry run first, read the list, then the real run:

```bash
./delete-prod-missing-on-dev.sh --dry-run
./delete-prod-missing-on-dev.sh
```

* Every candidate is printed as `[candidate] <path>` and saved, one
  absolute path per line, to `delete_candidates/<timestamp>.txt` next to
  the script. A real run hands that list to `delete-from-hosting.sh`, which
  backs each file up into `<prod parent>/backup_deleted_html` before
  deleting it, then removes prod folders that are missing on dev and empty.
* Always kept (prod-only by design): `services/user_profile/data/`,
  `.well-known/`, `cgi-bin/`, `*.log`, `sess_*`, `tmp/` folders,
  `google*.html` / `yandex_*.html`, `.user.ini`, `.htpasswd`. More with
  `--exclude-file FILE` (one glob per line, e.g. `promo/*`).
* A real run with more than 300 candidates stops (`--max N` raises the
  limit after a review), and a dev tree without `index.php` and
  `.htaccess` is refused, so a broken dev copy cannot empty prod.

## Smart copy: how it works

`copy_smartly_*` is a thin driver in three steps:

1. `find-dirs-with-changes.sh --max-depth N` lists changed dirs
   (first run: everything is new). Depth caps only the *reported*
   level (e.g. 1 → `ru ubuntu dev`, 2 → `section …`); detection always
   sees the whole subtree, and copying is never depth-limited.
2. Each listed dir is copied whole with
   `copy-with-shadow-backup.sh -f --duplicate-check full`
   (size short-circuit, then full content compare, so same-size edits
   are caught; overwritten prod files are backed up first; identical
   files skipped; nothing deleted — `[gone]`/`[stale]` only reported).
3. On success the scan from step 1 becomes the new snapshot (the
   finder writes it with `--snapshot-out`; it is not rescanned at the
   end), so a file that changes on dev while the copy runs is caught by
   the next run. On any copy failure the snapshot is left untouched so
   the next run retries. The default `--max-depth` is 4. The run
   ends with an explicit `Directories updated:` list.

Baseline file: `.smart_copy.state` next to the script (override:
`--state-file FILE` / `SMART_STATE_FILE` env). One line per
directory: `rel-path|dir-mtime|subtree-size|newest-file-mtime`
(mtimes at nanosecond resolution).

* **Baseline file:** `.smart_copy.state` next to the script
  (override: `--state-file FILE` / `SMART_STATE_FILE` env). One line
  per directory: `rel-path|dir-mtime|subtree-size|newest-file-mtime`.
* **Per-dir triple:** own mtime (entries added/removed/renamed),
  subtree size in bytes (growth/shrinkage), newest file mtime beneath
  it (same-size content edits — size alone would miss these).
* **First run** (no snapshot file): everything counts as new and is
  copied; the snapshot is written at the end (skipped on `--dry-run`).
* Dirs in the snapshot but missing from disk are reported as `[gone]`
  and never deleted.

Phase 2 enters every changed dir at depth 1..MAXDEPTH (overlap is
harmless — identical files are skipped; suppressing a parent for a
changed child once silently dropped the parent's own files) plus the
site root only for root-local changes, and syncs each
with `copy-with-shadow-backup.sh -f --duplicate-check full`
(size short-circuit, then full content compare), depth-bounded via
`COPY_MAXDEPTH`. Dest files missing from source are reported as
`[stale]`, never deleted. The run ends with an explicit
`Directories updated:` list.

Caveat: the baseline trusts mtimes, so a rewrite that preserves both
mtime and size stays invisible (a byte-identical rewrite is a
non-change by definition).
