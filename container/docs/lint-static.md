# lint-static.sh — Manual

Static analysis and code style for PHP, run from `container/`:

```bash
./scripts/lint-static.sh --files ru/qa/.php/BasePage.php
```

Companion to `lint-php.sh` (which only checks syntax via `php -l`):
`lint-static.sh` finds real bugs — undefined variables/functions/properties,
wrong types, dead code — plus style violations.

## Tools (installed as one-off Docker images, nothing on the host)

| Tool | What it finds | Version installed |
|---|---|---|
| `phpstan` (default) | bugs: unknown symbols, bad types, dead code | 2.2.14 |
| `psalm` | bugs, second opinion (missing types, unused code) | 6.18.0 |
| `phpcs` | style (`--standard`, default `PSR12`) | 4.0.4 |

How it runs: tools ship as versioned PHARs, cached in
`~/.cache/aredel-tools/` (outside the repo, never deployed), executed under
the `php:8.2-cli` image. First run downloads missing pieces automatically;
later runs work offline. Psalm gets a minimal config generated per run —
no `psalm.xml` is added to the repo.

Shared `phpcs` config lives in `container/phpcs.xml` (used by default as
`--standard`): PSR-12 plus `<exclude-pattern>` entries. Excluded files are
echoed before every `phpcs`/`phpcbf` run. `BasePage.php`/`ArticlePage.php`
are excluded there (their `Page.php` byte-parity is load-bearing); opt back
in per run with `--fix-page` (uses a stripped temp copy of the ruleset).

## Usage

```bash
# Single tool (default), one file
./scripts/lint-static.sh --files ru/qa/.php/BasePage.php
```

Progress dots print by default on long runs; `-v` adds per-file detail,
and each tool reports elapsed time (`(phpcs took 12s)`).

# Several tools at once (comma or repeatable)
./scripts/lint-static.sh --tool phpstan,psalm,phpcs --files ru/qa/.php/

# Only your staged changes / tracked files
./scripts/lint-static.sh --tool psalm --staged
./scripts/lint-static.sh --committed

# Whole tree (default when no selection is given)
./scripts/lint-static.sh --all

# PHPStan strictness 0-9 (default 1). Start low on legacy code.
./scripts/lint-static.sh --tool phpstan --level 2 --files ru/qa/.php/

# Other style standard
./scripts/lint-static.sh --tool phpcs --standard PSR2 --files ...
```

Selection flags (`--files`, positional paths, `--staged`, `--committed`,
`--all`, `--src`) behave like `lint-php.sh`. Read-only runs never modify
code; only `--fix` writes files (and it needs an explicit scope, see below).

## Reports

```bash
./scripts/lint-static.sh --tool phpstan --files ru/qa/.php/ --report reports/phpstan-qa
```

- Saves the full terminal output to a `.txt` report (`.txt` is appended
  when missing; an existing file is overwritten).
- Terminal output is unchanged; the report additionally starts with the
  report path and a UTC timestamp.
- Exit code still reflects the tools' result (see below), not the report.

## Exit codes

- `0` — every selected tool clean (also prints `OK: ... clean.`).
- `1` — at least one tool reported findings (`DONE: findings above.`).
- `2` — usage/setup error (bad flag, missing dir, failed download).

## Auto-fixing style (`phpcs` → `phpcbf`)

`phpcs` only reports. To actually fix, run the same selection with `--fix`:

```bash
./scripts/lint-static.sh --tool phpcs --fix --files ru/qa/.php/
```

- Runs `phpcbf` (fetched automatically, same cache) instead of `phpcs`.
- Fixes the `[x]` violations **in place**: trailing whitespace, spacing
  around `.`/`=`/`==`, `TRUE`→`true`, EOF newline, `?>` removal.
- `[ ]` violations are never auto-fixed (e.g. `snake_case` method names —
  renaming would break every caller).
- `--fix` refuses to run without `--tool phpcs`.
- `--fix` needs an explicit scope: paths/`--files`, `--staged`,
  `--committed`, non-default `--src`, or `--all`. A bare
  `./scripts/lint-static.sh --tool phpcs --fix` (whole-tree default) is
  refused — pass `--all` to fix everything:
  `./scripts/lint-static.sh --tool phpcs --fix --all`.
- `BasePage.php`/`ArticlePage.php` are **excluded from every `--fix` run**
  (their `Page.php` byte-parity is load-bearing) — the skip is echoed each
  time. Opt in explicitly per run:
  `./scripts/lint-static.sh --tool phpcs --fix --fix-page --files ...`
- Always review afterwards with `git diff` before deploying.

Do **not** run `--fix` on `BasePage.php`/`ArticlePage.php`: their method
bodies are byte-identical to `Page.php` on purpose, and reformatting would
destroy that equivalence. Same caution for any file whose exact bytes
matter — fix new code going forward, leave legacy formatting alone unless
you are ready to review (and deploy) large diffs.

## Reading output on this codebase

Expect findings on legacy files — that is normal, not a reason to fix
everything at once:

- `phpstan` level 1 flags dynamic properties (`Access to an undefined
  property X::$y` — classes assign `$this->...` without declaring them;
  PHP 8.2 deprecates this) and cross-file unknowns when files are
  analyzed standalone (pass a whole dir so symbols resolve).
- `psalm` additionally wants return types everywhere (`MissingReturnType`).
- `phpcs` `PSR12` disagrees with the historical style (closing `?>` tags,
  snake_case methods, long lines) — treat as advisory unless you adopt
  the standard.

Practical loop: narrow scope (`--files` / `--staged`), fix new code first,
raise `--level` only when level N is quiet.

## Updating / configuring

- Update a tool: delete its `.phar` from `~/.cache/aredel-tools/`; the next
  run re-downloads latest. Check versions any time:
  `docker run --rm -v ~/.cache/aredel-tools:/tools:ro php:8.2-cli php /tools/phpstan.phar --version`
- Different PHP runtime: `ADEL_STATIC_PHP_IMAGE=php:8.3-cli ./scripts/lint-static.sh ...`
- Different PHAR cache dir: `ADEL_STATIC_TOOL_CACHE=/path ./scripts/lint-static.sh ...`
