#!/usr/bin/env bash
# version 0.0.2
# Check (and optionally fix) whitespace-only lines in .php files.
#
# Correct state: no line consists of only spaces/tabs — such lines are
# empty (zero characters before the EOL). The fix never deletes lines,
# it only strips the spaces so the line becomes truly empty. EOLs
# (LF and CRLF) are preserved per line.
#
# Pure host-side check: no Docker, no installs. Read-only unless --fix
# is given.
#
# Usage:
#   ./check-php-blank-lines.sh [--src DIR] [--search] [--fix]
#                              [--all] [--staged] [--committed]
#                              [--files F ...] [paths...]
#
# Modes (default: --search; fixing needs an explicit flag):
#   --search             list whitespace-only lines as src-relative
#                        path:line. Read-only. Exit 1 when such lines
#                        exist.
#   --fix                strip whitespace-only lines to empty IN PLACE —
#                        review with git diff afterwards. Prints what was
#                        fixed. When both --search and --fix are given,
#                        --fix wins.
#
# Selection (default: all *.php under --src; flags/paths combine as a union):
#   --all             all *.php under --src (minus deploy junk).
#   --staged          staged changes (git diff --cached, *.php only).
#   --committed       git-tracked files (git ls-files, *.php only;
#                     --commited also accepted).
#   --files F ...     explicit files/dirs (space/comma separated, repeatable;
#                     paths relative to --src, or src/... prefixed; dirs expand
#                     to *.php below them).
#   paths...          positional files/dirs, same handling as --files.
#
# Skipped (never listed, never fixed): NUL-containing binaries, symlinks,
# empty files, deploy junk dirs, editor backups (* copy*, *.bak, *.orig,
# *~, *.swp, sess_*.php).
#
# Examples:
#   ./check-php-blank-lines.sh --search
#   ./check-php-blank-lines.sh --search --staged
#   ./check-php-blank-lines.sh --fix --files ru/qa/article.php
#   ./check-php-blank-lines.sh --fix --all
#
# Exit: --search: 0 = clean, 1 = whitespace-only lines found (or usage
# failure: 2). --fix: 0 = all now clean, 1 = fix failed, 2 = usage failure.
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../..  # tools live in tools/linters/; project root (compose file, src/) is the runtime cwd

SRC_DIR="src"
SEARCH=0
FIX=0
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
    --search) SEARCH=1; shift ;;
    --fix) FIX=1; shift ;;
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

if [ "$FIX" -eq 0 ] && [ "$SEARCH" -eq 0 ]; then SEARCH=1; fi  # default: report only

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }
command -v perl >/dev/null 2>&1 || { echo "ERROR: perl is required but was not found." >&2; exit 2; }

SRC_ABS="$(cd "$SRC_DIR" && pwd -P)"
REL_LIST="$(mktemp)"
trap 'rm -f "$REL_LIST"' EXIT

# Junk excluded from every selection (mirrors lint-php.sh).
EXCLUDE_DIRS="reports allure-results allure-results-host __pycache__ .pytest_cache .git .venv venv node_modules .mypy_cache .ruff_cache"

# Print line numbers (one per line) of whitespace-only lines in $1 (abs path).
list_ws_lines() { # $1 = abs path
  perl -ne 'print "$.\n" if /^[ \t]+(\r\n|\n|\r)/ or /^[ \t]+\z/' "$1" 2>/dev/null
}

# True (exit 0) when $1 (abs path) has at least one whitespace-only line.
has_ws_lines() { # $1 = abs path
  perl -ne 'if (/^[ \t]+(\r\n|\n|\r)/ or /^[ \t]+\z/) { $found=1; last; } END { exit($found ? 0 : 1); }' "$1" 2>/dev/null
}

# Strip whitespace-only lines to empty, preserving EOLs and line count.
fix_ws_lines() { # $1 = abs path
  perl -0777 -pi -e 's/^[ \t]+(\r\n|\n|\r)/$1/gm; s/^[ \t]+\z//m' "$1"
}

# --- 1) explicit files/dirs: --files + positionals (only *.php) ---
if [ "${#FILES_LIST[@]}" -gt 0 ] || [ "${#POSITIONAL[@]}" -gt 0 ]; then
  for tok in "${FILES_LIST[@]}" "${POSITIONAL[@]}"; do
    [ -n "$tok" ] || continue
    t="${tok#./}"
    case "$t" in
      "$SRC_DIR"/*) t="${t#"$SRC_DIR"/}" ;;
      container/src/*) t="${t#container/src/}" ;;
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
      find "$ap" -type f -name '*.php' -print | while IFS= read -r f; do
        printf '%s\n' "${f#"$SRC_ABS"/}"
      done >> "$REL_LIST"
    elif [ -f "$ap" ]; then
      case "$t" in
        *.php) printf '%s\n' "$t" >> "$REL_LIST" ;;
        *) echo "WARNING: skipping '$tok' (not a .php file)." >&2 ;;
      esac
    else
      echo "WARNING: skipping '$tok' (not found under $SRC_DIR)." >&2
    fi
  done
fi

# --- 2) git selections (*.php only) ---
if [ "$STAGED" -eq 1 ] || [ "$COMMITTED" -eq 1 ]; then
  TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--staged/--committed need git)." >&2; exit 1; }
  PREFIX="${SRC_ABS#"$TOP"/}"
  [ "$PREFIX" != "$SRC_ABS" ] || { echo "ERROR: $SRC_DIR is outside the git repo." >&2; exit 1; }
  if [ "$STAGED" -eq 1 ]; then
    git -C "$SCRIPT_DIR/../.." diff --cached --name-only -z --diff-filter=ACMR -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep '\.php$' >> "$REL_LIST" || true
  fi
  if [ "$COMMITTED" -eq 1 ]; then
    git -C "$SCRIPT_DIR/../.." ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
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
mapfile -t RELS < "$REL_LIST"
[ "${#RELS[@]}" -gt 0 ] || { echo "nothing to scan."; exit 0; }
echo "checking ${#RELS[@]} PHP file(s) under $SRC_DIR/ (whitespace-only lines) ..."

CHECKED=0; WRONG_FILES=0; WRONG_LINES=0; FIXED=0; FIXED_LINES=0; FAILED=0
for rel in "${RELS[@]}"; do
  abs="$SRC_ABS/$rel"
  if [ -L "$abs" ]; then continue; fi  # never rewrite a symlink
  if [ ! -f "$abs" ]; then echo "WARNING: skipping '$rel' (not a regular file)." >&2; continue; fi
  if [ ! -r "$abs" ]; then echo "WARNING: skipping '$rel' (not readable)." >&2; continue; fi
  if [ ! -s "$abs" ]; then continue; fi  # empty: no lines
  if [ "$(tr -d -c '\000' < "$abs" | wc -c | tr -d ' ')" -gt 0 ]; then continue; fi  # binary
  CHECKED=$((CHECKED + 1))
  # Fast prefilter (no perl fork for clean files); -I skips binaries.
  if ! grep -IqP '^[ \t]+(\r?$)' "$abs" 2>/dev/null; then continue; fi
  if ! has_ws_lines "$abs"; then continue; fi  # prefilter false positive guard
  lines="$(list_ws_lines "$abs")"
  [ -n "$lines" ] || continue
  n="$(printf '%s\n' "$lines" | wc -l | tr -d ' ')"
  WRONG_FILES=$((WRONG_FILES + 1)); WRONG_LINES=$((WRONG_LINES + n))
  if [ "$FIX" -eq 1 ]; then
    if fix_ws_lines "$abs"; then
      # Verify: nothing whitespace-only may remain.
      if grep -IqP '^[ \t]+(\r?$)' "$abs" 2>/dev/null; then
        FAILED=$((FAILED + 1))
        printf 'FAILED: %s (%s line(s) remain)\n' "$rel" "$n" >&2
      else
        FIXED=$((FIXED + 1)); FIXED_LINES=$((FIXED_LINES + n))
        printf 'fixed: %s (%s line(s))\n' "$rel" "$n"
      fi
    else
      FAILED=$((FAILED + 1))
      printf 'FAILED: %s (fix error)\n' "$rel" >&2
    fi
  else
    while IFS= read -r ln; do
      [ -n "$ln" ] || continue
      printf '%s:%s\n' "$rel" "$ln"
    done <<< "$lines"
  fi
done

echo "----"
if [ "$FIX" -eq 1 ]; then
  if [ "$FAILED" -gt 0 ]; then
    echo "RESULT: fixed $FIXED file(s) ($FIXED_LINES line(s)), FAILED $FAILED ($CHECKED checked)." >&2
    exit 1
  elif [ "$WRONG_FILES" -eq 0 ]; then
    echo "OK: $CHECKED checked, no whitespace-only lines."
    exit 0
  else
    echo "OK: fixed $FIXED file(s) ($FIXED_LINES line(s)) ($CHECKED checked)."
    exit 0
  fi
else
  if [ "$WRONG_FILES" -eq 0 ]; then
    echo "OK: $CHECKED checked, no whitespace-only lines."
    exit 0
  else
    echo "RESULT: $WRONG_LINES whitespace-only line(s) in $WRONG_FILES of $CHECKED file(s)."
    exit 1
  fi
fi
