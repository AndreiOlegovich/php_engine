#!/usr/bin/env bash
# Lint PHP files with the project's PHP 8.2 interpreter (php-apache container).
#
# Usage:
#   ./lint-php.sh [--src DIR] [--php VER] [--all] [--staged] [--committed]
#                 [--files F ...] [paths...]
#
#   --php VER         PHP version to lint with (default: 8.2).
#                     8.2 runs inside the php-apache container (project setup);
#                     other versions (e.g. 8.0) run via one-off php:VER-cli
#                     images (pulled from Docker Hub on first use).
#
# Selection (default: all *.php under --src; flags/paths combine as a union):
#   --all             all *.php under --src (minus deploy junk)
#   --staged          staged changes (git diff --cached)
#   --committed       git-tracked files (git ls-files; --commited also accepted)
#   --files F ...     explicit files/dirs (space/comma separated, repeatable;
#                     paths relative to --src, or src/... prefixed; dirs expand)
#   paths...          positional files/dirs, same handling as --files
#
# Examples:
#   ./lint-php.sh --staged
#   ./lint-php.sh qa/index.php ru/qa/
#   ./lint-php.sh --files "qa/index.php,ru/qa/article.php"
#   ./lint-php.sh --all
#
# Exit: 0 = clean, 1 = syntax errors (or usage/setup failure: 1/2).
# Needs the php-apache container (started automatically if stopped).
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # scripts live in scripts/; project root (compose file, src/) is the runtime cwd

SRC_DIR="src"
PHP_VER="8.2"
ALL=0; STAGED=0; COMMITTED=0
FILES_LIST=()
POSITIONAL=()

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --src) [ $# -ge 2 ] || { echo "ERROR: --src needs a value." >&2; exit 2; }
      SRC_DIR="$2"; shift 2 ;;
    --src=*) SRC_DIR="${1#--src=}"; shift ;;
    --php) [ $# -ge 2 ] || { echo "ERROR: --php needs a value (e.g. 8.2, 8.0)." >&2; exit 2; }
      PHP_VER="$2"; shift 2 ;;
    --php=*) PHP_VER="${1#--php=}"; shift ;;
    --all) ALL=1; shift ;;
    --staged) STAGED=1; shift ;;
    --committed|--commited) COMMITTED=1; shift ;;
    --files)
      shift; got=0
      while [ $# -gt 0 ]; do
        case "$1" in
          -*) break ;;
          *) FILES_LIST+=("$1"); got=1; shift ;;
        esac
      done
      [ "$got" -eq 1 ] || { echo "ERROR: --files needs at least one file." >&2; exit 2; }
      ;;
    --files=*)
      v="${1#--files=}"
      [ -n "$v" ] || { echo "ERROR: --files needs at least one file." >&2; exit 2; }
      IFS=',' read -ra _parts <<< "$v"
      for _p in "${_parts[@]}"; do
        _p="$(trim "$_p")"; [ -n "$_p" ] && FILES_LIST+=("$_p")
      done
      [ "${#FILES_LIST[@]}" -gt 0 ] || { echo "ERROR: --files needs at least one file." >&2; exit 2; }
      shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }
[ -f docker-compose.yml ] || { echo "ERROR: docker-compose.yml not found. Run from container/." >&2; exit 1; }
[[ "$PHP_VER" =~ ^[0-9]+\.[0-9]+$ ]] || { echo "ERROR: bad --php value '$PHP_VER' (want e.g. 8.2, 8.0)." >&2; exit 2; }

SRC_ABS="$(cd "$SRC_DIR" && pwd -P)"
REL_LIST="$(mktemp)"
trap 'rm -f "$REL_LIST"' EXIT

# Junk excluded from --all/git selections (mirrors deploy-ftp.sh).
# Explicit paths bypass these (you asked for those files).
EXCLUDE_DIRS="reports allure-results allure-results-host __pycache__ .pytest_cache .git .venv venv node_modules"

# --- 1) explicit files/dirs: --files + positionals ---
# Arrays (not a flat string) so names with spaces survive intact.
if [ "${#FILES_LIST[@]}" -gt 0 ] || [ "${#POSITIONAL[@]}" -gt 0 ]; then
  for tok in "${FILES_LIST[@]}" "${POSITIONAL[@]}"; do
    [ -n "$tok" ] || continue
    t="${tok#./}"
    case "$t" in
      "$SRC_DIR"/*) t="${t#"$SRC_DIR"/}" ;;
      container/src/*) t="${t#container/src/}" ;;
      container_php83/src/*) t="${t#container_php83/src/}" ;;
    esac
    case "$t" in
      /*)
        case "$t" in
          "$SRC_ABS"/*) t="${t#"$SRC_ABS"/}" ;;
          *) echo "WARNING: skipping '$tok' (outside $SRC_DIR)." >&2; continue ;;
        esac
        ;;
    esac
    ap="$SRC_ABS/$t"
    if [ -d "$ap" ] && [ ! -L "$ap" ]; then
      find "$ap" -type f -name "*.php" | while IFS= read -r f; do
        printf '%s\n' "${f#"$SRC_ABS"/}"
      done >> "$REL_LIST"
    elif [ -f "$ap" ]; then
      printf '%s\n' "$t" >> "$REL_LIST"
    else
      echo "WARNING: skipping '$tok' (not found under $SRC_DIR)." >&2
    fi
  done
fi

# --- 2) git selections ---
if [ "$STAGED" -eq 1 ] || [ "$COMMITTED" -eq 1 ]; then
  TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--staged/--committed need git)." >&2; exit 1; }
  PREFIX="${SRC_ABS#"$TOP"/}"
  [ "$PREFIX" != "$SRC_ABS" ] || { echo "ERROR: $SRC_DIR is outside the git repo." >&2; exit 1; }
  if [ "$STAGED" -eq 1 ]; then
    git -C "$SCRIPT_DIR/.." diff --cached --name-only -z --diff-filter=ACMR -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep '\.php$' >> "$REL_LIST" || true
  fi
  if [ "$COMMITTED" -eq 1 ]; then
    git -C "$SCRIPT_DIR/.." ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep '\.php$' >> "$REL_LIST" || true
  fi
fi

# --- 3) default/all: every *.php minus junk ---
if [ "$ALL" -eq 1 ] || { [ "${#FILES_LIST[@]}" -eq 0 ] && [ "${#POSITIONAL[@]}" -eq 0 ] && [ "$STAGED" -eq 0 ] && [ "$COMMITTED" -eq 0 ]; }; then
  PRUNE_ARGS=()
  # shellcheck disable=SC2086
  for d in $EXCLUDE_DIRS; do PRUNE_ARGS+=( -name "$d" -o ); done
  if [ "${#PRUNE_ARGS[@]}" -gt 0 ]; then
    unset 'PRUNE_ARGS[${#PRUNE_ARGS[@]}-1]'  # drop trailing -o
    find "$SRC_ABS" -mindepth 1 \( "${PRUNE_ARGS[@]}" \) -prune -o \
      -type f -name '*.php' ! -name '* copy*.php' ! -name 'sess_*.php' -print \
      | while IFS= read -r f; do printf '%s\n' "${f#"$SRC_ABS"/}"; done >> "$REL_LIST"
  else
    find "$SRC_ABS" -mindepth 1 \
      -type f -name '*.php' ! -name '* copy*.php' ! -name 'sess_*.php' -print \
      | while IFS= read -r f; do printf '%s\n' "${f#"$SRC_ABS"/}"; done >> "$REL_LIST"
  fi
fi

sort -u "$REL_LIST" -o "$REL_LIST"
sed -i '/^$/d' "$REL_LIST"
TOTAL="$(wc -l < "$REL_LIST" | tr -d ' ')"
# Empty selection = nothing to check = pass (keeps deploy --lint green for
# non-PHP selections; warnings for bad paths were already printed above).
[ "$TOTAL" -gt 0 ] || { echo "nothing to lint."; exit 0; }
echo "linting $TOTAL PHP file(s) with php $PHP_VER ..."

# Inner loop (single-quoted at each call site: container expands $f/$out,
# host must not). Prefix differs per backend: /var/www/html vs /code.
FAIL=0
set +e
if [ "$PHP_VER" = "8.2" ]; then
  # Project interpreter: code is bind-mounted, paths match 1:1.
  if ! docker compose ps --status running 2>/dev/null | grep -q php-apache; then
    echo "starting php-apache ..."
    docker compose up -d php-apache >/dev/null
  fi
  for i in $(seq 1 30); do
    docker compose exec -T php-apache php -v >/dev/null 2>&1 && break
    sleep 1
    [ "$i" -eq 30 ] && { echo "ERROR: php-apache did not become ready." >&2; exit 1; }
  done
  docker compose exec -T php-apache php -v 2>/dev/null | head -n 1
  tr '\n' '\0' < "$REL_LIST" | docker compose exec -T php-apache bash -c '
    fail=0; n=0
    while IFS= read -r -d "" f; do
      n=$((n+1))
      [ $((n % 500)) -eq 0 ] && echo "... $n files" >&2
      out=$(php -l "/var/www/html/$f" 2>&1) || { echo "FAIL: $f"; echo "$out" | sed "s/^/      /"; fail=$((fail+1)); }
    done
    echo "linted: $n, failures: $fail"
    exit $fail
  '
  FAIL=$?
else
  # Other versions: one-off official CLI image (pulled once, reused after).
  IMG="php:${PHP_VER}-cli"
  docker image inspect "$IMG" >/dev/null 2>&1 || {
    echo "pulling $IMG ..."
    docker pull "$IMG" || { echo "ERROR: cannot pull $IMG." >&2; exit 1; }
  }
  docker run --rm -v "$SRC_ABS":/code:ro "$IMG" php -v 2>/dev/null | head -n 1
  tr '\n' '\0' < "$REL_LIST" | docker run --rm -i -v "$SRC_ABS":/code:ro "$IMG" bash -c '
    fail=0; n=0
    while IFS= read -r -d "" f; do
      n=$((n+1))
      [ $((n % 500)) -eq 0 ] && echo "... $n files" >&2
      out=$(php -l "/code/$f" 2>&1) || { echo "FAIL: $f"; echo "$out" | sed "s/^/      /"; fail=$((fail+1)); }
    done
    echo "linted: $n, failures: $fail"
    exit $fail
  '
  FAIL=$?
fi
set -e
[ "$FAIL" -eq 0 ] && { echo "OK: no syntax errors."; exit 0; }
echo "RESULT: $FAIL file(s) with errors."
exit 1
