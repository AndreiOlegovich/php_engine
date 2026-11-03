# `wrap-long-lines.sh` manual

Finds PHP lines longer than a limit (default: 120 chars) and wraps them
without breaking code. Lives in `container/tools/` next to the runner;
all commands run from `container/`.

## Quick start

```bash
./tools/fix/wrap-long-lines.sh --search src/
./tools/fix/wrap-long-lines.sh --fix --limit 120 src/index.php
./tools/fix/wrap-long-lines.sh --fix --validate src/jmeter/
./tools/fix/wrap-long-lines.sh --help
```

## Modes

| Flag | What it does | Exit code |
|---|---|---|
| (none) / `--search` | Lists `file:line:length` + preview. Read-only, never touches files. | 1 if any found, else 0 |
| `--fix` | Wraps overlong lines (see Safety), then `php -l` checks every touched file. Files failing the check are restored byte-for-byte. | 1 if anything failed or was skipped, else 0 |
| `--validate` | Runs phpcs (PSR-12 project ruleset) on targets and reports totals. Alone: exit 1 on any finding. With `--fix`: compares findings before/after and reports `introduced`/`fixed`; exit 1 if anything was introduced. | as described |
| `--limit N` | Max line length (default `120`). | — |
| `--php-bin CMD` | PHP binary for the post-fix gate. Default: `docker exec php-apache php`, else host `php`. Without any PHP, `--fix` refuses to run. | — |

Paths are files and/or directories (default `src/`); directories are
scanned for `*.php` without descending into symlinked dirs.

## Safety: why `--fix` does not introduce bugs

1. **PHP-only.** Only lines fully inside `<?php ... ?>` blocks are
   touched. HTML/template text is reported as `ignored` and never
   penalized.
2. **String/comment/heredoc aware.** A purpose-built scanner (not regex)
   tracks quotes, escapes, comments and nesting depth. Lines with
   unterminated constructs are skipped.
3. **Safe split points only**, in order: top-level commas (one argument
   per line, PSR-12 shape with brackets on their own lines),
   `.` concatenation (operator at the start of the continuation line),
   `=` / `=>` assignments. Continuation indent is parent + 4 spaces.
4. **Unwrappable lines are left alone**: long string literals, URLs and
   anything without a safe split point stay as-is and are reported as
   `skipped`.
5. **Byte-safe**: CRLF/LF endings, trailing newlines and encodings are
   preserved exactly.
6. **`php -l` gate**: every modified file must pass; failures (including
   files already broken *before* the fix) are restored and reported as
   `FAILED`/`SKIPPED`.

## Output vocabulary

- `FIXED <file> lines=N skipped=M ignored=K` — wrapped N lines.
- `UNCHANGED <file>` — nothing over the limit (or nothing wrappable).
- `SKIPPED <file> (pre-existing php -l failure)` — broken before; untouched.
- `FAILED <file> (php -l gate, restored)` — fix broke syntax; reverted.
- `validate: introduced=X fixed=Y` — phpcs delta (`--fix --validate`).

## Examples

Report every long line under a tree (CI-friendly: exit 1 on findings):

```bash
./tools/fix/wrap-long-lines.sh --search src/ | tee long-lines.txt
```

Fix one file, proving zero phpcs regressions:

```bash
./tools/fix/wrap-long-lines.sh --fix --validate src/index.php
```

Fix with a custom limit and explicit PHP binary:

```bash
./tools/fix/wrap-long-lines.sh --fix --limit 100 --php-bin "docker exec php-apache php" src/
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ERROR: no PHP available ... Refusing to modify files` | start the stack (`docker compose up -d php-apache`) or pass `--php-bin php` with a host interpreter |
| `ERROR: --validate needs phpcs.phar ...` | run once `./tools/linters/lint-static.sh --tool phpcs --files <a file>` to download the PHAR into the tool cache |
| Everything `SKIPPED (pre-existing php -l failure)` | the target files don't parse — fix syntax first, this tool won't touch them |
| `validate: introduced=N` | inspect the `INTRODUCED` lines; fix by hand or revert that file with `git checkout -- <file>` |
| Script edits `tools/fix/.wrap-long-lines.py` itself flagged by ruff | helper follows the repo ruff config (`uv run ruff check/format`) — keep it clean |

## Files

```text
tools/fix/wrap-long-lines.sh   the script (bash + embedded Python 3, no extra packages)
tools/fix/.wrap-long-lines.py  scanner/wrapper helper (not run directly)
tools/fix/WRAP_LONG_LINES.md   this manual
```
