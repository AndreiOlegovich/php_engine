#!/usr/bin/env bash
# Check (and optionally fix) CRLF vs LF line endings under the site tree.
#
# Pure host-side check: no Docker, no installs. Read-only unless --fix
# (or bare --correct) is given.
#
# Usage:
#   ./check-line-endings.sh [--src DIR] [--to lf|crlf] [--format lf|crlf]
#                           [--correct[=lf|crlf]] [--search] [--fix]
#                           [--all] [--staged] [--committed]
#                           [--files F ...] [paths...]
#
# Target (default: LF):
#   --to F, --format F   want LF (unix) or CRLF (dos/windows) line endings.
#                        Accepted values (case-insensitive): lf, unix for LF;
#                        crlf, dos, windows for CRLF.
#   --correct[=F]        legacy alias kept for the first version of this
#                        script: bare --correct fixes files in place to the
#                        current target (same as --fix); --correct=F (or
#                        --correct F) also switches the target and fixes.
#                        The default target is LF, so bare --fix / --correct
#                        normalises everything to LF.
#
# Modes (default: --search; fixing needs an explicit flag):
#   --search             list files whose endings differ from the target.
#                        Read-only. Exit 1 when such files exist.
#   --fix                convert wrong files to the target IN PLACE — review
#                        with git diff afterwards. Prints what was fixed.
#                        When both --search and --fix are given, --fix wins.
#
# Selection (default: whole tree under --src; flags/paths combine as a union):
#   --all             all regular text files under --src (binary files and
#                     deploy junk are skipped, never converted).
#   --staged          staged changes (git diff --cached).
#   --committed       git-tracked files (git ls-files; --commited accepted).
#   --files F ...     explicit files/dirs (space/comma separated, repeatable;
#                     paths relative to --src, or src/... prefixed; dirs expand
#                     to all files below them).
#   paths...          positional files/dirs, same handling as --files.
#
# Classification per file: LF (all \n), CRLF (all \r\n), MIXED (anything else
# containing a \r: mixed CRLF+LF, or lone-\r old-Mac endings), NONE (empty or
# a single line without any newline — accepted for either target), BINARY
# (contains NUL — skipped, never listed or fixed).
#
# Examples:
#   ./check-line-endings.sh --search
#   ./check-line-endings.sh --search --to crlf --staged
#   ./check-line-endings.sh --fix --files ru/qa/article.php
#   ./check-line-endings.sh --correct --all
#
# Exit: --search: 0 = all clean, 1 = files with wrong endings (or usage
# failure: 2). --fix: 0 = all now clean, 1 = fix failed, 2 = usage failure.
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # scripts live in scripts/; project root (compose file, src/) is the runtime cwd

SRC_DIR="src"
TARGET="lf"
SEARCH=0
FIX=0
ALL=0; STAGED=0; COMMITTED=0
FILES_LIST=()
POSITIONAL=()

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

norm_target() { # $1 = raw value; prints lf|crlf, or nothing when unknown
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    lf|unix) printf 'lf' ;;
    crlf|dos|windows) printf 'crlf' ;;
    *) printf '' ;;
  esac
}

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --src) [ $# -ge 2 ] || { echo "ERROR: --src needs a value." >&2; exit 2; }
      SRC_DIR="$2"; shift 2 ;;
    --src=*) SRC_DIR="${1#--src=}"; shift ;;
    --to|--format)
      [ $# -ge 2 ] || { echo "ERROR: $1 needs a value (lf|crlf)." >&2; exit 2; }
      _t="$(norm_target "$2")"
      [ -n "$_t" ] || { echo "ERROR: bad format '$2' (want lf|crlf)." >&2; exit 2; }
      TARGET="$_t"; shift 2 ;;
    --to=*|--format=*)
      _v="${1#*=}"
      _t="$(norm_target "$_v")"
      [ -n "$_t" ] || { echo "ERROR: bad format '$_v' (want lf|crlf)." >&2; exit 2; }
      TARGET="$_t"; shift ;;
    --correct=*)
      _v="${1#--correct=}"
      if [ -z "$_v" ]; then
        FIX=1
      else
        _t="$(norm_target "$_v")"
        [ -n "$_t" ] || { echo "ERROR: bad --correct value '$_v' (want lf|crlf)." >&2; exit 2; }
        TARGET="$_t"; FIX=1
      fi
      shift ;;
    --correct)
      if [ $# -ge 2 ]; then
        _t="$(norm_target "$2")"
        if [ -n "$_t" ]; then TARGET="$_t"; FIX=1; shift 2; else FIX=1; shift; fi
      else
        FIX=1; shift
      fi ;;
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

if [ "$TARGET" = "lf" ]; then WANT="LF"; else WANT="CRLF"; fi

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }

if [ "$FIX" -eq 1 ] && [ "$TARGET" = "crlf" ] && ! command -v perl >/dev/null 2>&1; then
  echo "ERROR: converting to CRLF needs perl, which was not found." >&2; exit 2
fi

SRC_ABS="$(cd "$SRC_DIR" && pwd -P)"
REL_LIST="$(mktemp)"
trap 'rm -f "$REL_LIST"' EXIT

# Junk excluded from every selection (mirrors deploy-ftp.sh plus editor backups).
# Explicit binary extensions are skipped in the scan loop below.
EXCLUDE_DIRS="reports allure-results allure-results-host __pycache__ .pytest_cache .git .venv venv node_modules .mypy_cache .ruff_cache"

kind_of() { # $1 = abs path of a non-empty, non-binary file; prints LF|CRLF|MIXED|NONE
  local _cr _lf
  _cr="$(tr -d -c '\r' < "$1" | wc -c | tr -d ' ')"
  _lf="$(tr -d -c '\n' < "$1" | wc -c | tr -d ' ')"
  if [ "$_cr" -eq 0 ] && [ "$_lf" -eq 0 ]; then printf 'NONE'
  elif [ "$_cr" -eq 0 ]; then printf 'LF'
  elif [ "$_cr" -eq "$_lf" ]; then printf 'CRLF'
  else printf 'MIXED'
  fi
}

is_wrong() { # $1 = kind; returns 0 when it differs from $TARGET
  case "$TARGET:$1" in
    lf:CRLF|lf:MIXED|crlf:LF|crlf:MIXED) return 0 ;;
    *) return 1 ;;
  esac
}

# --- 1) explicit files/dirs: --files + positionals ---
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
      find "$ap" -type f -print | while IFS= read -r f; do
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
      | tr '\0' '\n' | sed "s#^$PREFIX/##" >> "$REL_LIST" || true
  fi
  if [ "$COMMITTED" -eq 1 ]; then
    git -C "$SCRIPT_DIR/.." ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" >> "$REL_LIST" || true
  fi
fi

# --- 3) default/all: every regular file minus junk dirs ---
if [ "$ALL" -eq 1 ] || { [ "${#FILES_LIST[@]}" -eq 0 ] && [ "${#POSITIONAL[@]}" -eq 0 ] && [ "$STAGED" -eq 0 ] && [ "$COMMITTED" -eq 0 ]; }; then
  PRUNE_ARGS=()
  # shellcheck disable=SC2086
  for d in $EXCLUDE_DIRS; do PRUNE_ARGS+=( -name "$d" -o ); done
  if [ "${#PRUNE_ARGS[@]}" -gt 0 ]; then
    unset 'PRUNE_ARGS[${#PRUNE_ARGS[@]}-1]'  # drop trailing -o
    find "$SRC_ABS" -mindepth 1 \( "${PRUNE_ARGS[@]}" \) -prune -o \
      -type f -print \
      | while IFS= read -r f; do printf '%s\n' "${f#"$SRC_ABS"/}"; done >> "$REL_LIST"
  else
    find "$SRC_ABS" -mindepth 1 -type f -print \
      | while IFS= read -r f; do printf '%s\n' "${f#"$SRC_ABS"/}"; done >> "$REL_LIST"
  fi
fi

sort -u "$REL_LIST" -o "$REL_LIST"
sed -i '/^$/d' "$REL_LIST"
mapfile -t RELS < "$REL_LIST"
[ "${#RELS[@]}" -gt 0 ] || { echo "nothing to scan."; exit 0; }
echo "checking ${#RELS[@]} file(s) under $SRC_DIR/ (want $WANT) ..."

CHECKED=0; WRONG=0; FIXED=0; FAILED=0; SKIP_BIN=0; SKIP_JUNK=0
for rel in "${RELS[@]}"; do
  abs="$SRC_ABS/$rel"
  case "$rel" in
    *.png|*.jpg|*.jpeg|*.gif|*.ico|*.pdf|*.zip|*.gz|*.bz2|*.xz|*.tar|*.woff|*.woff2|*.ttf|*.eot|*.otf|*.mp3|*.mp4|*.webm|*.sqlite|*.db|*.db-journal)
      SKIP_JUNK=$((SKIP_JUNK + 1)); continue ;;
    *\ copy*|*.bak|*.orig|*~|*.swp|*/sess_*.php|sess_*.php)
      SKIP_JUNK=$((SKIP_JUNK + 1)); continue ;;
  esac
  if [ -L "$abs" ]; then SKIP_JUNK=$((SKIP_JUNK + 1)); continue; fi  # never rewrite a symlink
  if [ ! -f "$abs" ]; then echo "WARNING: skipping '$rel' (not a regular file)." >&2; continue; fi
  if [ ! -r "$abs" ]; then echo "WARNING: skipping '$rel' (not readable)." >&2; continue; fi
  if [ ! -s "$abs" ]; then CHECKED=$((CHECKED + 1)); continue; fi  # empty: no endings
  if [ "$(tr -d -c '\000' < "$abs" | wc -c | tr -d ' ')" -gt 0 ]; then
    SKIP_BIN=$((SKIP_BIN + 1)); continue  # binary: never list or convert
  fi
  kind="$(kind_of "$abs")"
  CHECKED=$((CHECKED + 1))
  if is_wrong "$kind"; then
    WRONG=$((WRONG + 1))
    if [ "$FIX" -eq 1 ]; then
      if [ "$TARGET" = "lf" ]; then
        if command -v perl >/dev/null 2>&1; then
          perl -pi -e 's/\r\n?/\n/g' "$abs"
        else
          sed -i 's/\r$//' "$abs"  # fallback: CRLF -> LF (lone CRs survive)
        fi
      else
        perl -pi -e 's/\r\n|\r|\n/\r\n/g' "$abs"
      fi
      newkind="$(kind_of "$abs")"
      if is_wrong "$newkind"; then
        FAILED=$((FAILED + 1))
        printf 'FAILED: %s (still %s after fix)\n' "$rel" "$newkind" >&2
      else
        FIXED=$((FIXED + 1))
        printf 'fixed: %s (%s -> %s)\n' "$rel" "$kind" "$WANT"
      fi
    else
      printf '%s: %s\n' "$rel" "$kind"
    fi
  fi
done

echo "----"
if [ "$FIX" -eq 1 ]; then
  if [ "$FAILED" -gt 0 ]; then
    echo "RESULT: fixed $FIXED, FAILED $FAILED (want $WANT)." >&2
    exit 1
  elif [ "$WRONG" -eq 0 ]; then
    echo "OK: $CHECKED checked, all already $WANT (skipped: $SKIP_BIN binary, $SKIP_JUNK junk)."
    exit 0
  else
    echo "OK: fixed $FIXED file(s) to $WANT ($CHECKED checked; skipped: $SKIP_BIN binary, $SKIP_JUNK junk)."
    exit 0
  fi
else
  if [ "$WRONG" -eq 0 ]; then
    echo "OK: $CHECKED checked, all $WANT (skipped: $SKIP_BIN binary, $SKIP_JUNK junk)."
    exit 0
  else
    echo "RESULT: $WRONG of $CHECKED file(s) with wrong endings (want $WANT; skipped: $SKIP_BIN binary, $SKIP_JUNK junk)."
    exit 1
  fi
fi
