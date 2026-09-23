# PHP fix runbook

How to find PHP issues, auto-fix the safe ones, verify, and deploy.
All commands run from `container/`.

## 0. Scope first

With no file selection the tools default to the **whole tree** (~28k files)
— that takes many minutes and, for `--fix`, touches thousands of legacy
files. Prefer one small dir at a time. Progress dots print by default on
long runs; `-v` adds per-file detail; each tool reports elapsed time
(`(phpcs took 12s)`). A silent run is a working run — verify with
`docker stats` if in doubt.

## 1. Preview (report-only, changes nothing)

```bash
./scripts/lint-static.sh --tool phpcs --files ru/qa/.php/
```

- Add `--report reports/phpcs-qa` to also save the output as `.txt`.
- For bugs (not style), replace `--tool phpcs` with `--tool phpstan`
  (add `--level 2` for stricter) or `--tool psalm`. These two only report;
  only `phpcs` has an auto-fixer.

## 2. Fix (in place)

```bash
./scripts/lint-static.sh --tool phpcs --fix --files ru/qa/.php/
```

- Runs `phpcbf` instead of `phpcs`. Fixes the `[x]` violations **in place**:
  trailing whitespace, spacing around `.`/`=`/`==`, `TRUE`→`true`,
  missing EOF newline, closing `?>` removal.
- `[ ]` violations are never auto-fixed (e.g. `snake_case` method names —
  renaming would break every caller; namespaces).
- `--fix` refuses to run without `--tool phpcs`.
- `BasePage.php`/`ArticlePage.php` are excluded from every `--fix` run
  (echoed each time); add `--fix-page` to include them.
- Fix one small dir at a time so diffs stay reviewable. Never bulk-fix
  the whole tree in one go.

## 3. Review before anything else

```bash
git diff --stat
git diff <file>
```

- Revert a single file: `git checkout -- <file>`
- Full rollback: `git reset --hard backup/before-php-fixes-2026-09-23`
  (backup branch, local only).

## 4. Verify the fix didn't break rendering

```bash
./scripts/lint-php.sh --files <same paths>   # syntax gate, non-zero exit on error
```

Then open the page at `http://localhost:8080/<path>` — the `php-apache`
container serves `src/` live, no rebuild needed. Check
`docker logs php-apache | grep -i fatal` for runtime errors.

## 5. Deploy

```bash
./scripts/deploy-ftp.sh --files <paths> --lint --dry-run   # check plan first
./scripts/deploy-ftp.sh --files <paths> --lint             # real upload
```

## Guardrails

- Keep `--fix` away from `BasePage.php` / `ArticlePage.php`: their method
  bodies are byte-identical to `Page.php` on purpose — reformatting
  destroys that equivalence (checked by `/tmp/opencode/verify_page_split.py`).
- `phpstan`/`psalm` findings need human judgment: cross-file "unknown
  symbol" hits are analysis artifacts, not bugs; `Page.php` constructor
  deprecations are inherited intentionally for caller parity.
