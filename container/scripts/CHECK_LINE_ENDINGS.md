# Line-endings check manual (`check-line-endings.sh`)

Checks CRLF vs LF line endings under the site tree, and optionally
normalises them. All commands run from this directory (`scripts/` — or from
`container/` with a `scripts/` prefix; paths resolve the same).

Pure host-side check: no Docker, no installs. Read-only unless `--fix`
(or bare `--correct`) is given.

## What it checks

Each file is classified by counting `\r` vs `\n` bytes:

| Label | Meaning |
|---|---|
| `LF` | all newlines are `\n` (unix) |
| `CRLF` | all newlines are `\r\n` (dos/windows) |
| `MIXED` | anything else containing a `\r`: mixed CRLF+LF, or lone-`\r` old-Mac endings |
| `NONE` | empty file, or a single line without any newline — accepted for either target |
| `BINARY` | contains NUL — skipped, never listed or fixed |

Default target is **LF**. A file is "wrong" when its label differs from the
target (`LF` target: `CRLF`/`MIXED` are wrong; `CRLF` target: `LF`/`MIXED`
are wrong). `NONE` is always accepted.

Skipped (never listed, never converted): NUL-containing binaries, binary
extensions (`png jpg jpeg gif ico pdf zip gz bz2 xz tar woff woff2 ttf eot
otf mp3 mp4 webm sqlite db db-journal`), editor backups (`* copy*`, `*.bak`,
`*.orig`, `*~`, `*.swp`, `sess_*.php`), symlinks, and junk dirs (`reports`,
`allure-results*`, `__pycache__`, `.pytest_cache`, `.git`, `.venv`, `venv`,
`node_modules`, `.mypy_cache`, `.ruff_cache`).

## Usage

```bash
./check-line-endings.sh --help              # full help
./check-line-endings.sh --search            # whole src/, want LF (default)
./check-line-endings.sh --search --to crlf  # whole src/, want CRLF
./check-line-endings.sh --search --staged   # staged changes only
./check-line-endings.sh --fix --files ru/qa/article.php
./check-line-endings.sh --fix ru/qa/        # positional path, same thing
./check-line-endings.sh --correct --all     # legacy alias for --fix, want LF
./check-line-endings.sh --correct=crlf --files qa/index.php
```

Target flags (default: LF):

```bash
--to lf|crlf        --format lf|crlf        # want LF or CRLF (read-only selector)
--correct           # fix in place to current target (legacy alias for --fix)
--correct=lf|crlf   # also switch target, then fix (or: --correct crlf)
```

Accepted values (case-insensitive): `lf`, `unix` for LF; `crlf`, `dos`,
`windows` for CRLF.

Modes (default: `--search`; fixing needs an explicit flag):

| Flag | Effect |
|---|---|
| `--search` | list files whose endings differ from the target. Read-only. Exit 1 when such files exist. |
| `--fix` | convert wrong files to the target IN PLACE — review with `git diff` afterwards. Prints what was fixed. When both `--search` and `--fix` are given, `--fix` wins. |

Selection flags combine as a union (same convention as `lint-php.sh` /
`find-malformed-urls.sh`): `--files`, positional paths, `--staged`,
`--committed`, `--all` (default when nothing else is given). Names with
spaces work (`--files "ao/chessboard copy/MinimalChess.js"`). Paths may be
`--src`-relative, `src/`-prefixed, or absolute under `--src`; dirs expand to
all files below them.

Exit code: `0` = clean (search) / all now clean (fix), `1` = wrong endings
found (search) or fix failed, `2` = usage/setup error — so it plugs straight
into gates and hooks.

## Reading the report

Search mode prints one line per wrong file:

```text
checking 6 file(s) under src/ (want LF) ...
ru/qa/article.php: CRLF
ru/qa/help.php: MIXED
----
RESULT: 2 of 5 file(s) with wrong endings (want LF; skipped: 1 binary, 0 junk).
```

| Field | Meaning |
|---|---|
| `ru/qa/article.php` | path **relative to `src/`** |
| `CRLF` / `MIXED` | detected label (table above) |

Fix mode prints one line per converted file:

```text
checking 6 file(s) under src/ (want LF) ...
fixed: ru/qa/article.php (CRLF -> LF)
fixed: ru/qa/help.php (MIXED -> LF)
----
OK: fixed 2 file(s) to LF (5 checked; skipped: 1 binary, 0 junk).
```

When clean:

```text
----
OK: 5 checked, all LF (skipped: 1 binary, 0 junk).
```

## Fixing workflow (suggested)

1. Run scoped first: `./check-line-endings.sh --search --staged` (or
   `--files …`) before every commit — small output, obvious fixes.
2. Fix explicitly: `./check-line-endings.sh --fix --files …`, then review
   with `git diff` — a line-ending conversion touches every line of the
   file, so keep the scope tight and commit it separately from content
   changes.
3. Re-run the same scope until `OK: … all LF.` (exit 0).
4. Full-tree `--fix --all` is a one-time normalisation; agree on it with the
   team first (it rewrites history blame for every touched file).

## Files

```text
check-line-endings.sh   the script (bash + tr/wc, perl for CRLF direction)
```

## Notes & limits

- LF → CRLF conversion needs `perl`; errors out clearly where unavailable.
  CRLF → LF uses `perl` when present, `sed 's/\r$//'` fallback otherwise
  (lone-`\r` old-Mac endings survive the fallback — install perl for full
  `MIXED` cleanup).
- Reported paths are `src/`-relative.
- Like the other scripts, it `cd`s to the project root on start, so you can
  invoke it from anywhere: `container/scripts/check-line-endings.sh --search`.
