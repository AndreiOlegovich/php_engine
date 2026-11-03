# tools/fix — command cheat sheet

All commands run from `container/`. Fixers modify files in place:
review with `git diff` before deploying.

## Find lines over 120 chars (read-only)

```bash
./tools/fix/wrap-long-lines.sh --search src/.php/
./tools/fix/wrap-long-lines.sh --search src/ | tee long-lines.txt
```

## Wrap long lines safely (`php -l` gate, skips string literals)

```bash
./tools/fix/wrap-long-lines.sh --fix src/index.php
./tools/fix/wrap-long-lines.sh --fix --limit 100 src/
./tools/fix/wrap-long-lines.sh --fix --validate src/
```

Unwrappable lines (long string literals, URLs) report as `skipped` —
split them by hand with `.` concatenation, then re-run with `--validate`.

## Fix stray `};` after function braces (clear cases only)

```bash
php tools/fix/fix-closing-brace.php src/.php/
./tools/fix/fix-closing-brace.sh --force src/
```

Default drops `;` only where provably safe (named functions/classes,
control blocks). Closures and anonymous classes **need** the `;`:
skipped by default, moved to their own line under `--force`.
`./tools/fix/fix-closing-brace.sh --php-bin <cmd>` picks the interpreter.

## Full style fix with conflict fallback

```bash
./tools/linters/lint-static.sh --tool phpcs --files src/.php/Page.php
./tools/linters/lint-static.sh --tool phpcs --fix --files src/.php/Page.php
```

`--fix` order: safe families first, then the full pass, then
sniff-family by sniff-family for still-failing files (a family's
changes are kept per file only when its error count drops).
Single family on purpose:

```bash
vendor/bin/phpcbf --standard=phpcs.xml --sniffs=PSR2.Methods.FunctionCallSignature -- src/.php/Page.php
```

`[ ]` violations (camelCase renames, `};` closings) are never auto-fixed.

## Verify after any fix

```bash
docker run --rm -v "$PWD:/app" -w /app php:8.2-cli php -l /app/src/.php/Page.php
docker run --rm -v "$PWD:/app" -w /app -v "$HOME/.cache/php_engine-tools:/tools:ro" php:8.2-cli php /tools/phpcs.phar --standard=/app/phpcs.xml -- src/.php/Page.php
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
```

Manuals: `tools/fix/WRAP_LONG_LINES.md`,
`tools/fix/FIX_CLOSING_BRACE.md`.
