# `fix-closing-brace` manual

Fixes `PSR12.Classes.ClosingBrace.StatementAfter`
("Closing brace must not be followed by any comment or statement on the
same line"). That sniff is not auto-fixable (`[ ]`), so
`tools/fix/fix-closing-brace.php` does the job with PHP's own tokenizer
instead of regex. `tools/fix/fix-closing-brace.sh` is a thin wrapper that
finds a PHP interpreter and delegates.

In practice this is almost always a stray `;` after a function body:

```php
function web_related_urls()
{
    return array(
    "/zh/hosting/", "选择哪个托管",
      );
};   // <- the ";" goes away
```

## Run from the host

Any PHP 8.0+ on the host, from any directory — paths fall back to the
project root when they do not exist under the current one:

```bash
php container/tools/fix/fix-closing-brace.php container/src/.php/
./container/tools/fix/fix-closing-brace.sh --force container/src/
./container/tools/fix/fix-closing-brace.sh --help
```

From `container/` itself the shorter form works too:

```bash
php tools/fix/fix-closing-brace.php src/.php/
./tools/fix/fix-closing-brace.sh --force src/
```

## Run from inside the container

Only `./src` is mounted into `php-apache` (at `/var/www/html`);
`tools/` is not, so copy the fixer in first. From the host:

```bash
docker compose up -d php-apache
docker cp container/tools/fix/fix-closing-brace.php php-apache:/tmp/
docker exec -it php-apache bash
```

Inside the container (PHP 8.2 CLI is preinstalled, nothing to add):

```bash
php -v
php /tmp/fix-closing-brace.php /var/www/html/ru/code/python/collections/set/
php /tmp/fix-closing-brace.php --force /var/www/html/
```

Edits land on the host immediately (shared volume), so verify from the
host afterwards and clean up the copy when done:

```bash
exit
./tools/linters/lint-static.sh --tool phpcs --files src/.php/Page.php
docker exec php-apache rm /tmp/fix-closing-brace.php
```

## Modes

| Call | What it does | Exit code |
|---|---|---|
| `fix-closing-brace.php [paths...]` | Fixes clear cases (see below), reports the rest as skipped. Read-only for everything doubtful. | 1 if anything skipped, else 0 |
| `... --force [paths...]` | Additionally splits `} //comment` and `} statement` tails onto their own line, and moves a *required* `;` (closure, anonymous class) to its own line. Never drops a `;` the code needs. | as above |
| `fix-closing-brace.sh [--force] [--php-bin CMD] [paths...]` | Same, via host `php` (or `--php-bin`). | the fixer's code |

Paths are files and/or directories (default `src/`); directories are
scanned for `*.php` without descending into symlinked dirs.

## What counts as a clear case

The script pairs every `}` with its `{` and classifies the owner:

| Owner of `{...}` | The `;` is... | Default | `--force` |
|---|---|---|---|
| Named function / class / interface / trait / enum | stray, droppable | drop it | drop it |
| Control block (`if`, `for`, `while`, `try`, ...), `declare`, bracketed `namespace` | stray, droppable | drop it | drop it |
| Closure (`$f = function () {...};`), anonymous class (`$o = new class {...};`) | **mandatory** | leave, report `required semicolon` | move `;` to its own line |
| `} //comment`, `} statement();` tails | ambiguous placement | leave, report why | split onto own line |
| `} else {`, `} catch`, `} finally {`, `} while (...)` | another sniff's business | never touched | never touched |

## Safety

1. **Tokenizer, not regex.** `token_get_all` sees real strings, heredocs,
   comments and interpolation, so a `}` inside any of those is never
   mistaken for a block brace. Attributes (`new #[A] class`) and
   `function name(...)` signatures are resolved before deciding.
2. **Byte-safe**: CRLF/LF endings and trailing whitespace are preserved;
   only the offending `;` (or newline) changes.
3. **`php -l` gate twice**: files already broken are skipped untouched;
   every modified file must pass afterwards (the check runs on
   `PHP_BINARY`, the same interpreter doing the fix) or it is restored
   byte-for-byte and reported as `FAILED`.
4. **Verify like any fixer**: `php -l`, re-run `phpcs` on the targets,
   review with `git diff` before deploying.

## Output vocabulary

- `FIXED <file> lines=N skipped=M` — plus one `skipped <file>:<line>
  (<reason>)` line per doubt.
- `UNCHANGED <file>` — nothing to do (or nothing fixable in this mode).
- `SKIPPED <file> (pre-existing php -l failure)` — broken before; untouched.
- `FAILED <file> (php -l gate, restored)` — fix broke syntax; reverted.

## Files

```text
tools/fix/fix-closing-brace.php  the fixer (PHP 8.0+, no extensions needed)
tools/fix/fix-closing-brace.sh   host wrapper (finds php, forwards args+exit code)
tools/fix/FIX_CLOSING_BRACE.md   this manual
```
