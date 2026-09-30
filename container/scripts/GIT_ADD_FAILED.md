# git-add-failed.sh — Manual

Stages files reported as failed by the last commit check (pre-commit / lint).
Nothing is committed — only staged. Review with `git status` / `git diff --cached`.

Script: `container/scripts/git-add-failed.sh`. Run from `container/`.

## Why

After `git commit` is blocked by `phpcs` / `php -l`, the failure log lists
dozens of files (`FILE:` / `FAIL:` lines). Re-typing them into `git add`
is error-prone — especially with container paths (`/app/src/...`) and
names with spaces (`bash/2 copy.php`). This script parses the log,
maps paths to repo-relative form, and stages them in one NUL-safe `git add`.

## Usage

```bash
chmod +x scripts/git-add-failed.sh   # once

./scripts/git-add-failed.sh --dry-run          # default log, print only
./scripts/git-add-failed.sh                    # stage tracked files from default log
./scripts/git-add-failed.sh /tmp/check.log     # explicit log file
./scripts/git-add-failed.sh latest_commit_log2.txt --dry-run
pre-commit run --all-files 2>&1 | tee /tmp/check.log; ./scripts/git-add-failed.sh /tmp/check.log
pre-commit run --all-files 2>&1 | ./scripts/git-add-failed.sh -   # stdin via -
./scripts/git-add-failed.sh --all              # also stage untracked files
./scripts/git-add-failed.sh -h                 # help (header comment)
```

## Default log resolution

When no `LOG` is given, first existing file wins:

1. `<repo>/container/latest_commit_log2.txt`
2. `<repo>/latest_commit_log2.txt`
3. `<repo>/container/latest_commit_log.txt`
4. `<repo>/latest_commit_log.txt`

Otherwise pass the path explicitly (resolved against cwd, then repo top),
or `-` for stdin.

## Log formats understood (mixed in one file is fine)

```text
FILE: /app/src/aws/RelatedArticles.php        <- lint-static.sh / phpcs report
FAIL: ao/api/php_auth/index.php               <- lint-php.sh report
FAIL: ru/qa/.php/_footers/season/Aug.php
```

Extraction: `awk '$1 == "FILE:" || $1 == "FAIL:" { print $2 }'`
(trailing `,;:` trimmed), deduplicated with `sort -u`.

## Path mapping

| Log path | Staged as |
|---|---|
| `/app/src/<rel>` | `container/src/<rel>` (docker mount) |
| `src/<rel>` | `container/src/<rel>` (lint-static run from `container/`) |
| `/var/www/html/<rel>` | `container/src/<rel>` (php-apache docroot) |
| `/code/<rel>` | `container/src/<rel>` (one-off `php:VER-cli` mount) |
| `container/...` | as-is |
| `<rel>.php` (bare) | `container/src/<rel>` when that file exists, else repo-relative as-is |
| absolute host path under repo | repo-relative form |
| absolute path outside repo | skipped, reported |

`./` prefix stripped. Non-existent targets skipped and reported as
`skip (not found): <log> -> <mapped>`.

## Tracked vs untracked

- Default: only files already tracked by git are staged
  (`git ls-files --error-unmatch` check). Untracked hits are listed as
  `untracked (re-run with --all to include)` and NOT staged.
- `--all`: stage untracked too (e.g. a new `PHPCS_FIX_GUIDE.md` from the log).

## Safety

- Read-only until the final step: `--dry-run` prints the would-be
  `git add` list and stages nothing.
- Staging is NUL-separated (`tr '\n' '\0' | xargs -0 git add --`),
  so `bash/2 copy.php` and similar names survive intact.
- Never commits. After staging, review:
  ```bash
  git status --short
  git diff --cached --stat
  git reset -- <file>   # unstage one file
  ```

## Exit codes

- `0` — at least one file staged (or `--dry-run` listed some).
- `1` — nothing to stage (no `FILE:`/`FAIL:` entries, or none resolved
  to existing files).
- `2` — usage error (unknown flag, two `LOG` args, log not found).

## Typical loop

```bash
git commit -m "..." 2>&1 | tee /tmp/check.log   # blocked by phpcs
./scripts/git-add-failed.sh /tmp/check.log --dry-run
./scripts/git-add-failed.sh /tmp/check.log
./scripts/lint-static.sh --tool phpcs --fix --staged   # fix what you staged
git diff --cached
git commit -m "..."   # retry
```

Note: this stages the *failed* files so the next commit re-runs hooks on
them — it does not fix them. Fix first (`lint-static.sh --tool phpcs --fix`),
then stage, then commit.
