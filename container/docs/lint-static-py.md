# lint-static.py — Full Manual

Python port of `lint-static.sh`: static analysis and code style for PHP,
run from `container/`. Same flags, same behavior, same exit codes —
split into one function per action under `tools/linters/lint_static_with_py/`.

```bash
./tools/linters/lint-static.py --files section/.php/BasePage.php
./tools/linters/lint-static.py --tool phpstan,phpcs --level 2 --all
```

Companion to `lint-php.sh` (syntax only, `php -l`): this tool finds real
bugs — undefined variables/functions/properties, wrong types, dead code —
plus style violations. Read-only by default; only `--fix` writes files.

- Shell version: `tools/linters/lint-static.sh` (reference implementation).
- Python version: `tools/linters/lint-static.py` + `tools/linters/lint_static_with_py/` package.
- Shared `phpcs` config: `container/phpcs.xml` (PSR-12 plus excludes).

---

## 1. Contents

- [2. File layout](#2-file-layout)
- [3. Requirements](#3-requirements)
- [4. Quick start](#4-quick-start)
- [5. Selection (which files are checked)](#5-selection-which-files-are-checked)
- [6. Full CLI reference](#6-full-cli-reference)
- [7. Environment variables](#7-environment-variables)
- [8. Tools](#8-tools)
- [9. Reports (`--report`)](#9-reports---report)
- [10. Auto-fixing (`--fix`)](#10-auto-fixing---fix)
- [11. Failure log (`FAILED_TO_FIX_LOG`)](#11-failure-log-failed_to_fix_log)
- [12. Exit codes](#12-exit-codes)
- [13. Module / function reference](#13-module--function-reference)
- [14. Examples](#14-examples)
- [15. Reading output on this codebase](#15-reading-output-on-this-codebase)
- [16. Port notes (Python vs shell)](#16-port-notes-python-vs-shell)
- [17. Troubleshooting](#17-troubleshooting)
- [18. Updating / configuring](#18-updating--configuring)

---

## 2. File layout

```text
container/
  tools/linters/
    lint-static.py          # entry point (chmod +x); parses nothing itself
    lint_static_with_py/
      __init__.py           # package marker
      config.py             # Config dataclass, defaults, validate_config()
      cli.py                # parse_args(), add_tools(), dedupe_tools(), ...
      paths.py              # explicit + git + whole-tree path resolution
      worktree.py           # --worktree: path, folder name or branch -> container/ root
      fix_offer.py          # --fix without --tool phpcs: message, Y/n offer, ruff
      fix_fallback.py       # phpcbf per-family fallback
      ruleset.py            # phpcs.xml <exclude-pattern> helpers
      docker.py             # image pull, PHAR fetch, docker_php_run()
      report.py             # --report tee, FILE: parsing, failure log
      runners.py            # run_phpstan(), run_psalm(), run_phpcs_tool(), ...
      main.py               # resolve_runtime_dirs(), run_all_tools(), main()
  docs/
    lint-static.md          # shell-script manual
    lint-static-py.md       # this file
```

Each module owns one concern; every action is a named function (no
monolith). Import any of them directly for tests or reuse:

```python
import sys
sys.path.insert(0, "tools/linters")
from lint_static_with_py.cli import parse_args
from lint_static_with_py.config import validate_config

cfg = parse_args(["--tool", "phpcs", "--files", "section/"])
validate_config(cfg)
```

---

## 3. Requirements

- Python `>= 3.14` (uses `X | None` syntax, `tomllib`-era stdlib only —
  no third-party deps).
- `docker` on `PATH` (tools run in containers; nothing installs on host).
- Git checkout (only needed for `--staged` / `--committed`).
- Run from `container/` (the script resolves `src/`, `phpcs.xml`,
  `docker-compose` context relative to itself, but `FAILED_TO_FIX_LOG`
  and `--report` resolve relative to your invocation dir).

No `pip install` needed. First run downloads PHARs + image automatically;
later runs work offline.

---

## 4. Quick start

```bash
cd container

# Default tool (phpstan, level 1), one file
./tools/linters/lint-static.py --files section/.php/BasePage.php

# Style check on a directory
./tools/linters/lint-static.py --tool phpcs --files section/.php/

# Bugs + style, stricter phpstan, whole tree
./tools/linters/lint-static.py --tool phpstan,phpcs --level 2 --all

# Only what you staged
./tools/linters/lint-static.py --tool psalm --staged

# Fix style in place, then review
./tools/linters/lint-static.py --tool phpcs --fix --files section/.php/
git diff --stat
```

Progress dots print by default on long runs; `-v` adds per-file detail;
each tool reports elapsed time (`(phpcs took 12s)`).

---

## 5. Selection (which files are checked)

Flags combine as a **union**. Resolution order in `paths.resolve_paths()`:

1. **Explicit** (`--files` + positionals) via `resolve_explicit_paths()`:
   paths relative to `--src`, or `src/...`-prefixed, or `./`-prefixed.
   `container/src/...` prefix is also accepted. Absolute paths must sit
   under the resolved `src/` dir. Missing/outside tokens are skipped with
   a `WARNING` on stderr (not fatal).
2. **Git** (`--staged`, `--committed`) via `resolve_git_paths()`:
   `--staged` = `git diff --cached --name-only -z --diff-filter=ACMR`,
   `--committed` = `git ls-files --full-name -z`. Both filtered to
   `*.php` under `--src`, deduped against explicit paths. Fails with
   exit `2` outside a git repo or when `src/` is outside the repo.
   (`--commited` single-`t` is accepted as an alias, like the shell.)
3. **Whole tree** (default): when none of `--files`/positionals/
   `--staged`/`--committed`/`--all` is given, `PATHS = [src/]`.
   Analyzers get the tree as-is so cross-file symbols resolve (unlike
   `lint-php.sh`, no deploy-junk pruning here).

Empty final selection prints `nothing to check.` and exits `0`.

`--fix` **refuses** the whole-tree default (you must pass an explicit
scope: paths/`--files`, `--staged`, `--committed`, non-default `--src`,
or `--all`). This guard lives in `config.validate_config()`.

---

## 6. Full CLI reference

```text
./lint-static.py [--src DIR] [--tool phpstan|psalm|phpcs]... [--level N]
                 [--standard NAME] [--all] [--staged] [--committed]
                 [--files F ...] [paths...] [--report FILE] [--fix]
                 [--fix-page] [--no-failure-report] [--worktree W]
                 [-v|--verbose] [-h|--help]
```

| Flag | Form | Default | Meaning |
|---|---|---|---|
| `--src` | `--src DIR` / `--src=DIR` | `src` | Source tree. Sets `src_set`, which counts as `--fix` scope. |
| `--tool` | `--tool T` / `--tool=T`, repeatable, comma list | `phpstan` | One of `phpstan`, `psalm`, `phpcs`. Order kept, deduped. Unknown → exit `2`. |
| `--level` | `--level N` / `--level=N` | `1` | PHPStan level `0-9`. Anything else → exit `2`. |
| `--standard` | `--standard S` / `--standard=S` | `/app/phpcs.xml` | `phpcs` standard (container path or builtin like `PSR2`). Custom value sets `standard_set`. |
| `--report` | `--report F` / `--report=F` | — | Also tee full output to `F.txt` (`.txt` appended when missing; existing file overwritten). Dir must exist + be writable. |
| `--all` | flag | off | Whole tree (`PATHS = [src/]`). Also counts as `--fix` scope. |
| `--staged` | flag | off | Staged `*.php` under `--src`. |
| `--committed` | flag (`--commited` alias) | off | Tracked `*.php` under `--src`. |
| `--files` | `--files F ...` (to next flag) / `--files=a,b` / repeatable | — | Explicit files/dirs, space and/or comma separated. Needs ≥1 value. |
| `paths...` | positional | — | Same handling as `--files`. |
| `--fix` | flag | off | Run `phpcbf` in place instead of `phpcs`. Needs `--tool phpcs` + explicit scope. Without `--tool phpcs`: message plus Y/n offer (§10.1). |
| `--worktree` | `--worktree W` / `--worktree=W` | — | Lint and fix another worktree: path, folder name or branch (§10.2). |
| `--fix-failed` | flag / `--fix-failed=LIST` | off | Fix only the files named in the commit check's failed list (§10.3). Implies `--tool phpcs --fix`; no other selection allowed. |
| `--fix-page` | flag | off | Include `BasePage.php`/`ArticlePage.php` in `--fix` (stripped temp ruleset). Needs `--fix`. |
| `--no-failure-report` | flag | off | Skip the `failed_to_fix_*.log` file. Needs `--fix`. |
| `-v`, `--verbose` | flag | off | Print every file the script sees (`files seen (N):`, one path per line; a folder is listed as its `.php` files), then verbose tool output (`phpstan -v`, psalm `--show-info=true`, `phpcs -v`). |
| `-h`, `--help` | flag | — | Print usage, which ends with the example commands; exit `0`. |

Order in `main()`: `--worktree` is resolved first; then `--fix` without
`phpcs` goes to `fix_offer.offer_fix()`, which either adds `phpcs` to the
tools or ends the run.
Validation order (`config.validate_config()`): `--fix` needs `phpcs` →
`--fix-page` needs `--fix` → `--no-failure-report` needs `--fix` →
`--fix` needs scope → `--level` must be one digit. First failure wins,
message on stderr, exit `2`.

---

## 7. Environment variables

| Var | Default | Meaning |
|---|---|---|
| `PHP_ENGINE_STATIC_PHP_IMAGE` | `php:8.5-cli` | Runtime image for all tool containers. |
| `PHP_ENGINE_STATIC_TOOL_CACHE` | `~/.cache/php_engine-tools` | Host dir where `*.phar` files are cached (outside repo, never deployed). |
| `PHPCS_FAILED_LIST` | `container/log/phpcs-latest-commit-failed-list.log` | Failed list of the commit check: written by `--staged` phpcs checks, read by `--fix-failed` (§10.3). |
| `FAILED_TO_FIX_LOG` | `log` | Dir for `failed_to_fix_<timestamp>.log` after `--fix`. Relative → resolved under invocation dir, created with `mkdir -p`. Absolute → used as-is. |

Examples:

```bash
PHP_ENGINE_STATIC_PHP_IMAGE=php:8.3-cli ./tools/linters/lint-static.py --files section/
PHP_ENGINE_STATIC_TOOL_CACHE=/tmp/tools ./tools/linters/lint-static.py --tool phpcs --all
FAILED_TO_FIX_LOG=reports ./tools/linters/lint-static.py --tool phpcs --fix --all
```

Read once at `Config` construction (`config.default_config()`); changing
them mid-run has no effect.

---

## 8. Tools

All tools run as versioned PHARs under the PHP image. Setup sequence in
`main.main()`: print `runtime: ...` → `ensure_docker_image()` (pull when
`docker image inspect` fails) → `ensure_tools()` (fetch missing PHARs) →
`print_tool_versions()` → `run_all_tools()`.

| Tool | PHAR | What it finds |
|---|---|---|
| `phpstan` (default) | `phpstan.phar` | Bugs: unknown symbols, bad types, dead code. `analyse --level=N`. Progress bar on; `-v` adds tool verbose. |
| `psalm` | `psalm.phar` | Bugs, second opinion (missing types, unused code). Gets a generated minimal config (`errorLevel="4"`, `<directory name="/app/<src>">`) mounted at `/psalmcfg:ro`, run with `--no-cache`. |
| `phpcs` | `phpcs.phar` / `phpcbf.phar` | Style. `-p` progress dots by default, `-v` per-file. Default standard is the shared `phpcs.xml` (PSR-12 + excludes); excludes echoed before every run. |

PHAR URLs live in `config.py` (`PHPSTAN_URL`, `PSALM_URL`, `PHPCS_URL`,
`PHPCBF_URL`); all track `releases/latest`. `phpcbf.phar` is fetched only
when `--fix` is active.

---

## 9. Reports (`--report`)

```bash
./tools/linters/lint-static.py --tool phpstan --files section/.php/ --report reports/phpstan-qa
```

- Implemented by `report.setup_report()` + `report.start_report()`.
- `.txt` appended when missing; existing file overwritten; parent dir must
  exist and be writable (else exit `2`).
- Terminal output unchanged: stdout is tee'd (`report.Tee`) to the file.
- File starts with `report: <path>` + UTC `report started: ...` line.
- Exit code still reflects the tools, not the report.

---

## 10. Auto-fixing (`--fix`)

`phpcs` only reports. `--fix` runs `phpcbf` **in place**:

```bash
./tools/linters/lint-static.py --tool phpcs --fix --files section/.php/
```

- Fixes `[x]` violations: trailing whitespace, spacing around `.`/`=`/`==`,
  `TRUE`→`true`, EOF newline, `?>` removal.
- Never auto-fixes `[ ]` violations (e.g. `snake_case` method names —
  renaming would break callers; namespaces).
- Requires `--tool phpcs` and an explicit scope. Without `--tool phpcs`
  the script explains and offers the fixer (§10.1).
- `BasePage.php`/`ArticlePage.php` stay excluded via `phpcs.xml` (their
  `Page.php` byte-parity is load-bearing); the skip is echoed every run.
  `--fix-page` opts back in per run: `runners.prepare_fix_standard()`
  writes a stripped temp copy of the ruleset (minus Base/Article excludes)
  and mounts it at `/ruleset/phpcs.xml`. With a custom `--standard`,
  `--fix-page` only warns (that standard controls excludes).
- After `phpcbf`, the script **re-checks with `phpcs`** — `phpcbf`'s own
  exit code only says whether it touched anything. The re-check exit code
  is the tool result; its `FILE:` lines feed the failure log.
- Always `git diff` before deploying. Fix one small dir at a time.

### 10.1 `--fix` without `--tool phpcs`: the Y/n offer

`--fix` without `--tool phpcs` does not start a fix. The script says that
the `--tool` flag is needed, prints a command for every tool (`phpstan`,
`psalm`, `phpcs` report, `phpcs` fix) and for the other file types, then
offers the fixer that fits the selected files and waits for one key,
`Y` or `n` (Enter counts as `Y`):

| Selected | Question | On `Y` |
|---|---|---|
| `.php` files, a folder with `.php` files, `--staged`, `--committed`, `--all` | Run the fix with phpcbf, the fixer of phpcs? | the same run as `--tool phpcs --fix` |
| `.py` files, a folder with `.py` files | Run the fix with ruff? | `ruff check --fix`, then `ruff format`, with `container/pyproject.toml` |
| `.sh`, `.js`, other files | none | a note: `lint-sh.sh` and `lint-js.sh` only report |

```bash
./tools/linters/lint-static.py --fix section/index.php                        # one question: phpcbf
./tools/linters/lint-static.py --fix tools/example.py                    # one question: ruff
./tools/linters/lint-static.py --fix section/index.php tools/example.py  # two questions
./tools/linters/lint-static.py --fix section/python                               # folder with both: two questions
```

- With both kinds selected two questions are asked, phpcbf first, and each
  answer counts on its own: `n` then `Y` fixes only the Python files.
- A `.php` path is relative to `src/` as everywhere in this script. A `.py`
  path is looked up from the folder you start in, then `src/`, `container/`
  and the repository root; with `--worktree` only inside that worktree.
- ruff comes from `container/.venv/bin/ruff`, else from `PATH`.
- A report-only tool passed with `--fix` (`--tool phpstan --fix`) still runs
  after `Y`; `phpcs` is added to it.
- Without a terminal (a hook, a pipe) nothing is asked and nothing is
  fixed: exit code `2`.
- Exit code after a ruff-only run: `0` when ruff is clean, `1` when
  findings remain. `n` to every question: `2`.

### 10.2 Another worktree (`--worktree`)

Another worktree is linted, and with `--fix` fixed, through `--worktree W`.
`W` is one of:

- a path, absolute or relative to the folder you start in;
- the folder name of a worktree, as `git worktree list` shows it;
- the branch of a worktree.

```bash
./tools/linters/lint-static.py --worktree ~/github/my_site-agent-example --staged
./tools/linters/lint-static.py --worktree my_site-agent-example --tool phpcs --files section
./tools/linters/lint-static.py --worktree agent/example --tool phpcs --fix --staged
./tools/linters/lint-static.py --worktree agent/example --fix tools/example.py
```

- The first output line names the tree in use: `worktree: /path/container`.
- Every selection reads that worktree: `--files` and paths are relative to
  its `src/`, `--staged` reads its index, `--committed` its tracked files.
- Its own `phpcs.xml` and `phpstan.neon` are used, not the ones of the
  checkout the script is started from.
- An unknown `W` prints the worktree list and exits with `2`; so does a
  folder without `container/src`.

Do **not** `--fix` `BasePage.php`/`ArticlePage.php` (or any file whose
exact bytes matter) unless you are ready to review and deploy large diffs.

---

### 10.3 Failed list of the commit check and `--fix-failed`

Every `phpcs` check with `--staged` saves the paths of the files that
failed. The pre-commit hook `phpcs-src` is such a check, so after a blocked
`git commit` the list is there:

```text
container/log/phpcs-latest-commit-failed-list.log
```

```text
src/section/functions.php
src/section/index.php
```

- Paths only, one per line, relative to `container/`, sorted. The file is
  ignored by git (`*.log`).
- Each `--staged` check replaces the list; a `--staged` check that finds
  nothing removes it. Checks without `--staged` leave it alone.
- With `--worktree` the list is the one of that worktree.
- Another place for the list: `PHPCS_FAILED_LIST=FILE` (the check writes
  there, `--fix-failed` reads there).

`--fix-failed` fixes only the files named in the list:

```bash
git commit -m "..."                              # phpcs hook fails, list is saved
./tools/linters/lint-static.py --fix-failed        # phpcbf on the listed files only
git diff                                         # review
git add -- <fixed files>                         # fixed files are not staged
```

- The same run as `--tool phpcs --fix --files <the paths in the list>`: no
  question is asked, `--tool` is not needed.
- No other selection may be given with it (`--staged`, `--all`,
  `--committed`, paths): exit code `2`.
- Afterwards the list holds the files that still fail (violations phpcbf
  cannot fix), or is removed when none do.
- No list, or an empty one: `nothing to fix`, exit code `0`.
- The hook of a worktree runs that worktree's own `lint-static.sh`. A
  branch whose script is older than 0.2.0 never writes the list, so
  `--fix-failed` finds none there; the message prints the two ways out:
  write the list with `./tools/linters/lint-static.py --worktree W --tool phpcs --staged`,
  or check and fix in one run with
  `./tools/linters/lint-static.py --worktree W --tool phpcs --fix --staged`.
- The hints the script prints (`fix them with: ...`) carry `--worktree W`
  and `--fix-failed=LIST` when those are in use, so they work as printed.
- `--fix-failed=LIST` reads, and afterwards updates, another list file.
- A listed file that no longer exists is skipped with a warning.

Not the same as the two older files:

| File | Written by | Holds |
|---|---|---|
| `log/phpcs-latest-commit-failed-list.log` | every `--staged` phpcs check; `--fix-failed` | paths that failed the last commit check; one file, replaced each time |
| `$FAILED_TO_FIX_LOG/failed_to_fix_<timestamp>.log` | every `--fix` run | paths still failing after that fix; a new file per run, in the folder you start in |
| `--report FILE` | any run with the flag | the full output, not a list |

`tools/git-add-failed.sh` stages the files named in a log you saved
yourself; it does not write a list and does not fix.

## 11. Failure log (`FAILED_TO_FIX_LOG`)

After every `--fix` run (unless `--no-failure-report`):

1. `report.parse_failed_files()` extracts `^FILE:` lines from the
   re-check output, strips the `/app/` prefix, sorts + dedupes.
2. Empty → prints `no failure report: nothing still failing.`
3. Non-empty → `report.write_failure_report()` writes
   `failed_to_fix_<UTC timestamp>.log` (e.g. `20260925110712`, one
   `src/...` path per line) into `report.failure_dir()` and prints
   `failure report: <path> (<N> file(s) still failing)`.

`failure_dir()`: absolute `FAILED_TO_FIX_LOG` → as-is; relative (default
`log`) → `<invocation-dir>/log`, created as needed. Override per run:

```bash
FAILED_TO_FIX_LOG=reports ./tools/linters/lint-static.py --tool phpcs --fix --all
./tools/linters/lint-static.py --tool phpcs --fix --no-failure-report --files section/
```

Logs match `*.log` in root `.gitignore` (never committed) and `log/` is in
`container/.dockerignore` (never sent to Docker).

---

## 12. Exit codes

- `0` — every selected tool clean (`OK: all selected tools clean.`).
  Also `0` for `nothing to check.` and `--help`.
- `1` — at least one tool reported findings (`DONE: findings above.`)
  or a runtime/setup failure (missing `src/`, failed download, docker
  error).
- `2` — usage error (bad flag, bad `--level`, unknown `--worktree`,
  `--fix` without `phpcs` when nothing was fixed, `--fix` without
  scope, `--fix-page` / `--no-failure-report` without `--fix`, bad
  `--report` dir).

---

## 13. Module / function reference

### `config.py` — shared configuration

| Function / class | Signature | Does |
|---|---|---|
| `Config` | `@dataclass` (see §6–7 for fields) | All runtime options + env defaults + resolved dirs (`start_dir`, `script_dir`, `app_root`, `own_root`, `src_abs`, `ruleset_file`). `app_root` is the linted `container/` (moved by `--worktree`), `own_root` the one the script lives in; `tools_given` tells whether `--tool` was passed, `ruff_rc` keeps the result of an offered ruff run. |
| `default_config` | `() -> Config` | Return a `Config` with env-based defaults. |
| `validate_config` | `(cfg: Config) -> None` | Raise `ValueError` on bad flag combos / `--level` (see §6). |

Constants: `VALID_TOOLS`, `PHPSTAN_URL`, `PSALM_URL`, `PHPCS_URL`,
`PHPCBF_URL`.

### `cli.py` — argument parsing

| Function | Signature | Does |
|---|---|---|
| `split_comma_list` | `(value: str) -> list[str]` | Split comma list, trim, drop empties. |
| `add_tools` | `(cfg: Config, value: str) -> None` | Validate + append tools from a comma list. |
| `dedupe_tools` | `(cfg: Config) -> None` | Dedupe `cfg.tools`, keep order. |
| `parse_args` | `(argv: list[str]) -> Config` | Full flag parser (see §6). `ValueError` on usage errors; `SystemExit(0)` on `--help`. Empty `--tool` → `["phpstan"]`. |
| `print_usage` | `() -> None` | Write `USAGE` to stdout: flags, then the example commands at the end. |

### `paths.py` — file selection

| Function | Signature | Does |
|---|---|---|
| `normalize_token` | `(cfg: Config, token: str) -> str \| None` | Strip `./`, `src/`, `container/src/` prefixes; resolve absolute under `src_abs`. `None` = outside tree. |
| `resolve_explicit_paths` | `(cfg: Config) -> list[str]` | `--files` + positionals → `SRC_DIR/rel` list; `WARNING` on stderr for skipped tokens. |
| `git_repo_top` | `(script_dir: str) -> str` | `git rev-parse --show-toplevel` or `''`. |
| `git_name_list` | `(args: list[str], cwd: str) -> list[str]` | Run git listing (`-z` aware); `[]` on failure. |
| `resolve_git_paths` | `(cfg: Config, paths: list[str]) -> list[str]` | Append staged/committed `*.php` (deduped). `ValueError` outside repo. |
| `resolve_paths` | `(cfg: Config) -> list[str]` | Explicit + git + whole-tree default (see §5). |

`list_seen_files(base, paths, ext) -> list[str]` expands the paths to the
files behind them (a folder becomes its `*.ext` files) and
`print_seen_files(base, paths, ext)` prints them for `-v`.

### `ruleset.py` — `phpcs.xml` helpers

| Function | Signature | Does |
|---|---|---|
| `ruleset_excludes` | `(ruleset_file: str) -> list[str]` | All `<exclude-pattern>` values (`[]` on `OSError`). |
| `print_ruleset_excludes` | `(ruleset_file: str) -> None` | Echo excludes before each `phpcs`/`phpcbf` run. |
| `stripped_ruleset` | `(ruleset_file: str, dest_file: str) -> None` | Copy ruleset minus `BasePage`/`ArticlePage` excludes (for `--fix-page`). |

### `docker.py` — container plumbing

| Function | Signature | Does |
|---|---|---|
| `run_command` | `(args, *, capture=False, check=False) -> CompletedProcess[str]` | Thin `subprocess.run` wrapper. |
| `ensure_docker_image` | `(php_image: str) -> None` | `docker image inspect`, else `docker pull`. |
| `fetch_phar` | `(name: str, url: str, tool_cache: str) -> str` | Download PHAR when missing; `RuntimeError` on failure/empty. |
| `ensure_tools` | `(cfg: Config) -> None` | Fetch PHARs for selected tools (`phpcbf` only with `--fix`). |
| `print_tool_versions` | `(cfg: Config) -> None` | Print each tool `--version` (psalm first line). |
| `docker_php_run` | `(cfg, phar, tool_args, extra_mounts=None) -> int` | Run PHAR with `/app` + tool-cache mounts; stream output; return exit code. |

### `report.py` — reports + failure log

| Function / class | Signature | Does |
|---|---|---|
| `normalize_report_path` | `(report: str) -> str` | Append `.txt` when missing. |
| `setup_report` | `(cfg: Config) -> str` | Validate `--report` target; `ValueError` on bad dir; remove stale file. |
| `Tee` | `Tee(report_path)` + `write/flush/close` | Dual-write stdout + file (mirrors `exec > >(tee)`). |
| `start_report` | `(report_path: str) -> Tee \| None` | Install `Tee` as `sys.stdout`; print header lines. |
| `parse_failed_files` | `(recheck_output: str) -> list[str]` | `FILE:` → sorted unique `src/...` paths. |
| `failure_dir` | `(cfg: Config) -> str` | Resolve `FAILED_TO_FIX_LOG` to absolute dir. |
| `write_failure_report` | `(cfg, failed: list[str]) -> str \| None` | Write timestamped log; `None` when empty. |

`report.py` also holds the failed list of the commit check:
`failed_list_path(cfg)`, `save_failed_list(cfg, phpcs_output)` (temp file,
then rename; removes the list when nothing failed) and
`load_failed_list(path)`. `main.apply_fix_failed(cfg)` turns `--fix-failed`
into a phpcs fix run over the listed files.

### `runners.py` — tool execution

| Function | Signature | Does |
|---|---|---|
| `run_phpstan` | `(cfg, paths) -> int` | `phpstan analyse [--verbose] --level=N -- <paths>`. |
| `run_psalm` | `(cfg, paths) -> int` | Temp-dir `psalm.xml` + `psalm --no-cache --config=...`. |
| `run_phpcbf` | `(cfg, paths, standard, extra_mounts=None) -> int` | `phpcbf --standard=... -p [-v]`. |
| `run_phpcs_check` | `(cfg, paths, standard, *, capture=False, extra_mounts=None) -> tuple[int, str]` | `phpcs`; stream, or capture + echo (like `\| tee`). |
| `prepare_fix_standard` | `(cfg) -> tuple[str, list[str], str]` | Effective standard + mounts + temp dir for `--fix`/`--fix-page`. |
| `run_phpcs_tool` | `(cfg, paths) -> int` | Report-only `phpcs`, or `phpcbf` → re-check → failure log. |
| `run_tool` | `(cfg, tool, paths) -> int` | Dispatch + `==> tool ...` + `(tool took Ns)` timing. |

### `worktree.py` — `--worktree`

| Function | Signature | Does |
|---|---|---|
| `list_worktrees` | `(own_root: str) -> list[tuple[str, str]]` | `(path, branch)` of every worktree (`git worktree list --porcelain`). |
| `find_worktree_dir` | `(name, start_dir, own_root) -> str` | Directory a `--worktree` value means: a path first, else a folder name or branch; `''` when unknown. |
| `resolve_worktree` | `(name, start_dir, own_root) -> str` | The `container/` root of that worktree; `ValueError` with the worktree list when unknown or without `container/src`. |

### `fix_offer.py` — `--fix` without `--tool phpcs`

| Function / class | Signature | Does |
|---|---|---|
| `FixTargets` | `@dataclass` | Explicit targets by type: `php_seen`, `keep` (tokens left for phpcs), `py_targets`, `n_explicit`. |
| `fix_tool_help` | `(cfg) -> str` | The message: `--tool` is needed, one command per tool and per other file type. |
| `ask_yn` | `(question: str) -> bool` | One key on the terminal, `Y`/`n`, Enter = `Y`; `False` without a terminal. |
| `find_target` | `(cfg, token) -> str` | Existing path of a token (start folder, `src/`, `container/`, repository root). |
| `dir_has` | `(path, ext) -> bool` | Folder holds a file with this extension (`.venv`, `node_modules`, `.git` pruned). |
| `classify_targets` | `(cfg) -> FixTargets` | Sort `--files` + positionals into PHP and Python; a folder with both counts for both. |
| `find_ruff` / `run_ruff` | `(cfg) -> str` / `(cfg, targets) -> int` | Pick ruff (`.venv/bin`, then `PATH`); run `ruff check --fix` and `ruff format`. |
| `offer_fix` | `(cfg) -> int \| None` | Print the message, ask per fixer; `None` = go on with phpcs, else the exit code. |

### `main.py` — orchestration

| Function | Signature | Does |
|---|---|---|
| `resolve_runtime_dirs` | `(cfg, script_dir, start_dir) -> None` | Fill `own_root` (= `container/`, i.e. `tools/linters/../..`), `app_root` (the same, or the `--worktree` root), `src_abs`, `ruleset_file`. |
| `check_source_dir` | `(cfg) -> None` | `FileNotFoundError` when `src/` missing. |
| `run_all_tools` | `(cfg, paths) -> int` | Run each tool in order; `0` clean / `1` findings. |
| `main` | `(argv: list[str] \| None = None) -> int` | Parse → resolve (`--worktree`) → fix offer → validate → report setup → docker setup → run. Maps `ValueError→2`, `FileNotFoundError→1`, `RuntimeError→1`. |

---

## 14. Examples

```bash
# 1. Default phpstan on one file
./tools/linters/lint-static.py --files section/.php/BasePage.php

# 2. Positional path (same as --files)
./tools/linters/lint-static.py section/.php/BasePage.php

# 3. Comma + repeatable tools
./tools/linters/lint-static.py --tool phpstan,psalm,phpcs --files section/.php/
./tools/linters/lint-static.py --tool phpstan --tool phpcs --files section/.php/

# 4. Staged / tracked files
./tools/linters/lint-static.py --tool psalm --staged
./tools/linters/lint-static.py --committed

# 5. Whole tree (default when no selection)
./tools/linters/lint-static.py --all

# 6. Stricter phpstan
./tools/linters/lint-static.py --tool phpstan --level 2 --files section/.php/

# 7. Custom standard
./tools/linters/lint-static.py --tool phpcs --standard PSR2 --files section/

# 8. Save output to report
./tools/linters/lint-static.py --tool phpstan --files section/.php/ --report reports/phpstan-qa

# 9. Fix style in place + review
./tools/linters/lint-static.py --tool phpcs --fix --files section/.php/
git diff --stat

# 10. Fix everything (explicit --all required)
./tools/linters/lint-static.py --tool phpcs --fix --all

# 11. Include page files in a fix run
./tools/linters/lint-static.py --tool phpcs --fix --fix-page --files section/.php/

# 12. Custom failure-log dir / skip it
FAILED_TO_FIX_LOG=reports ./tools/linters/lint-static.py --tool phpcs --fix --all
./tools/linters/lint-static.py --tool phpcs --fix --no-failure-report --files section/

# 13. --fix without --tool phpcs: explanation, then Y/n per fixer
./tools/linters/lint-static.py --fix section/index.php
./tools/linters/lint-static.py --fix section/index.php tools/example.py

# 14. Another worktree: path, folder name or branch
./tools/linters/lint-static.py --worktree agent/example --tool phpcs --staged
./tools/linters/lint-static.py --worktree ../my_site-agent-example --tool phpcs --fix section/functions.php

# 15. Verbose + custom runtime / cache
./tools/linters/lint-static.py -v --tool phpcs --files section/
PHP_ENGINE_STATIC_PHP_IMAGE=php:8.3-cli ./tools/linters/lint-static.py --files section/
PHP_ENGINE_STATIC_TOOL_CACHE=/tmp/tools ./tools/linters/lint-static.py --tool phpcs --all

# 16. Use as a library (tests, scripting)
python3 -c "
import sys; sys.path.insert(0, 'tools/linters')
from lint_static_with_py.cli import parse_args
from lint_static_with_py.report import parse_failed_files
cfg = parse_args(['--tool', 'phpcs', '--files', 'section/'])
print(cfg.tools, cfg.files)
print(parse_failed_files('FILE: /app/src/a.php\n'))
"
```

---

## 15. Reading output on this codebase

Expect findings on legacy files — normal, not a reason to fix everything:

- `phpstan` level 1 flags dynamic properties (`Access to an undefined
  property X::$y` — `$this->...` assigned without declaration; PHP 8.2
  deprecates this) and cross-file unknowns when files are analyzed
  standalone (pass a whole dir so symbols resolve).
- `psalm` additionally wants return types (`MissingReturnType`).
- `phpcs` PSR-12 disagrees with historical style (closing `?>`, snake_case
  methods, long lines) — advisory unless the standard is adopted.

Practical loop: narrow scope (`--files` / `--staged`), fix new code first,
raise `--level` only when the current level is quiet.

---

## 16. Port notes (Python vs shell)

- Same flags, same selection union, same messages, same exit codes
  (`0`/`1`/`2`). `--commited` alias, `--files` space/comma/repeatable,
  `--tool` comma/repeatable + dedupe, `--report` `.txt` rule, `--fix`
  scope guard, `FILE:` → `failed_to_fix_*.log` flow are all preserved.
- Differences are mechanical: `argparse` replaced by a hand parser in
  `cli.parse_args()` (so `--files` greedy-to-next-flag and `=` forms match
  the shell exactly); `exec > >(tee)` replaced by `report.Tee` on
  `sys.stdout`; `mktemp` dirs replaced by `tempfile`; `curl` replaced by
  `urllib.request`; `grep/sed` on `FILE:` lines replaced by regex in
  `parse_failed_files()`.
- `main.main(argv)` accepts an explicit argv list (default `sys.argv[1:]`)
  and returns the exit code instead of exiting — importable and testable.
- The shell script `cd`s to `container/`; the Python port keeps the
  invocation dir as `cfg.start_dir` and computes `app_root` from the
  package location, so relative `--report` / `FAILED_TO_FIX_LOG` resolve
  identically.

---

## 17. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `unknown --tool '...'` (exit 2) | Only `phpstan\|psalm\|phpcs`. Check commas/spelling. |
| `--fix refuses the whole-tree default` (exit 2) | Add scope: `--files ...`, `--staged`, `--committed`, `--src DIR`, or `--all`. |
| `--fix needs the --tool flag` … `nothing was fixed` (exit 2) | `--fix` only works with `--tool phpcs`: add it, or answer `Y` to the offer (§10.1). In a hook or pipe there is no terminal, so nothing is asked. |
| `--worktree '...' is not a directory, a worktree folder name or a worktree branch` (exit 2) | Take the path, folder name or branch from the printed worktree list. |
| `ruff not found` | `uv sync --project container` (ruff lives in `container/.venv`). |
| `--fix-page / --no-failure-report needs --fix` | Those flags are `--fix`-only. |
| `bad --level '...'` | Single digit `0-9`. |
| `not inside a git repo` | `--staged`/`--committed` need git. |
| `source dir '...' not found` | Wrong `--src` or wrong cwd; run from `container/`. |
| `report dir '...' not found / not writable` | Create the dir first; check permissions. |
| `download of ... failed` | Network/PHAR URL issue; delete partial file in tool cache and retry. |
| `docker: command not found` / image pull fails | Docker not running / no network. Start Docker, retry. |
| Empty run prints `nothing to check.` | Selection matched nothing (bad path tokens — see `WARNING`s on stderr). |
| `failure report skipped` | You passed `--no-failure-report` — expected. |
| `no failure report: nothing still failing.` | `--fix` cleaned everything — expected. |

Validate the port itself without Docker:

```bash
python3 -m py_compile tools/linters/lint-static.py tools/linters/lint_static_with_py/*.py
python3 tools/linters/lint-static.py --help
python3 tools/linters/lint-static.py --tool bogus --files src   # exit 2
python3 tools/linters/lint-static.py --tool phpcs --fix          # exit 2 (no scope)
```

---

## 18. Updating / configuring

- Update a tool: delete its `.phar` from `~/.cache/php_engine-tools/` (or
  `$PHP_ENGINE_STATIC_TOOL_CACHE`); next run re-downloads latest. Versions:
  `docker run --rm -v ~/.cache/php_engine-tools:/tools:ro php:8.5-cli php /tools/phpstan.phar --version`
- Different PHP runtime: `PHP_ENGINE_STATIC_PHP_IMAGE=php:8.3-cli ./tools/linters/lint-static.py ...`
- Different PHAR cache: `PHP_ENGINE_STATIC_TOOL_CACHE=/path ./tools/linters/lint-static.py ...`
- Different failure-log dir: `FAILED_TO_FIX_LOG=reports ./tools/linters/lint-static.py --tool phpcs --fix --all`
- Lint the port itself: `uvx ruff check tools/linters/lint_static_with_py/ tools/linters/lint-static.py`
- Format the port: `uvx ruff format tools/linters/lint_static_with_py/ tools/linters/lint-static.py`
