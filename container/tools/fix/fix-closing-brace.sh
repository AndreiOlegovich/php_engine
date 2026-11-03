#!/usr/bin/env bash
# version 0.0.1
# Wrapper around fix-closing-brace.php (same dir).
#
# Why a wrapper: the fix itself needs PHP's tokenizer (token_get_all) to
# tell `function foo() {};` (safe) apart from `$f = function () {};`
# (fatal if touched). Pure bash/sed cannot do that safely, so this script
# only finds a PHP interpreter and delegates. See fix-closing-brace.php
# for the fixer, its --force mode and its safety guarantees.
#
# Usage:
#   ./fix-closing-brace.sh [--force] [--php-bin CMD] [paths...]
#
#   --force      fix comment/code tails too, not just clear cases.
#   --php-bin C  php binary (default: host `php`, needs 7.4+).
#   paths...     files and/or directories (default: src/).
#
# Exit: the fixer's exit code (0 = nothing skipped, 1 = skips, 2 = usage).
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../..  # tools live in tools/fix/; project root (src/) is the runtime cwd

PHP_BIN=""
SHOW_HELP=0
POSITIONAL=()

while [ $# -gt 0 ]; do
  case "$1" in
    --force) POSITIONAL+=("--force"); shift ;;
    --php-bin) [ $# -ge 2 ] || { echo "ERROR: --php-bin needs a value." >&2; exit 2; }
      PHP_BIN="$2"; shift 2 ;;
    --php-bin=*) PHP_BIN="${1#--php-bin=}"; shift ;;
    -h|--help) SHOW_HELP=1; shift ;;
    -*) echo "ERROR: unknown option: $1 (see fix-closing-brace.php --help)." >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

if [ -z "$PHP_BIN" ]; then
  if command -v php >/dev/null 2>&1; then
    PHP_BIN="php"
  else
    echo "ERROR: no PHP interpreter found (tried host 'php')." >&2
    echo "HINT: install PHP 7.4+ or pass --php-bin <cmd>." >&2
    exit 2
  fi
fi

if [ "$SHOW_HELP" -eq 1 ]; then
  exec "$PHP_BIN" "$SCRIPT_DIR/fix-closing-brace.php" --help
fi

# Run from the invocation dir so relative paths behave like any other tool;
# default `src` still resolves because we fall back to the project root.
if [ "${#POSITIONAL[@]}" -eq 0 ]; then
  "$PHP_BIN" "$SCRIPT_DIR/fix-closing-brace.php"
else
  cd "$_START_DIR"
  "$PHP_BIN" "$SCRIPT_DIR/fix-closing-brace.php" "${POSITIONAL[@]}"
fi
