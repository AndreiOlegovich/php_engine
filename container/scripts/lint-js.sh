#!/usr/bin/env bash
# Lint JavaScript files with node --check (host node, no dependencies).
#
# Usage:
#   ./lint-js.sh [--src DIR] [--all] [--staged] [--committed]
#                [--files F ...] [paths...]
#
# Selection (default: all *.js under --src; flags/paths combine as a union):
#   --all             all *.js regular files under --src (minus deploy junk)
#   --staged          staged changes (git diff --cached)
#   --committed       git-tracked files (git ls-files; --commited also accepted)
#   --files F ...     explicit files/dirs (space/comma separated, repeatable;
#                     paths relative to --src, or src/... prefixed; dirs expand)
#   paths...          positional files/dirs, same handling as --files
#
# Examples:
#   ./lint-js.sh --staged
#   ./lint-js.sh ao/chessboard/game.js
#   ./lint-js.sh --files "a.js,b.js"
#   ./lint-js.sh --all
#
# Notes:
#   Files using ESM syntax (import/export) are checked via a temp .mjs copy,
#   since node --check parses .js as CommonJS.
#
# Exit: 0 = clean, 1 = syntax errors (usage/setup failure: 2).
# Needs: node on PATH.
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # scripts live in scripts/; project root (compose file, src/) is the runtime cwd

SRC_DIR="src"
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

command -v node >/dev/null 2>&1 || { echo "ERROR: node not found on PATH." >&2; exit 2; }
[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }

SRC_ABS="$(cd "$SRC_DIR" && pwd -P)"
REL_LIST="$(mktemp)"
trap 'rm -f "$REL_LIST"' EXIT

EXCLUDE_DIRS="reports allure-results allure-results-host __pycache__ .pytest_cache .git .venv venv node_modules"

# --- 1) explicit files/dirs: --files + positionals (bypass junk excludes) ---
# Arrays (not a flat string) so names with spaces survive intact.
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
      find "$ap" -type f -name "*.js" | while IFS= read -r f; do
        printf '%s\n' "${f#"$SRC_ABS"/}"
      done >> "$REL_LIST"
    elif [ -f "$ap" ]; then
      printf '%s\n' "$t" >> "$REL_LIST"
    else
      echo "WARNING: skipping '$tok' (not found under $SRC_DIR)." >&2
    fi
  done
fi

# --- 2) git selections (junk excludes still apply) ---
if [ "$STAGED" -eq 1 ] || [ "$COMMITTED" -eq 1 ]; then
  TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--staged/--committed need git)." >&2; exit 1; }
  PREFIX="${SRC_ABS#"$TOP"/}"
  [ "$PREFIX" != "$SRC_ABS" ] || { echo "ERROR: $SRC_DIR is outside the git repo." >&2; exit 1; }
  if [ "$STAGED" -eq 1 ]; then
    git -C "$SCRIPT_DIR" diff --cached --name-only -z -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep '\.js$' >> "$REL_LIST" || true
  fi
  if [ "$COMMITTED" -eq 1 ]; then
    git -C "$SCRIPT_DIR" ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep '\.js$' >> "$REL_LIST" || true
  fi
fi

# --- 3) default/all: every *.js regular file minus junk ---
if [ "$ALL" -eq 1 ] || { [ "${#FILES_LIST[@]}" -eq 0 ] && [ "${#POSITIONAL[@]}" -eq 0 ] && [ "$STAGED" -eq 0 ] && [ "$COMMITTED" -eq 0 ]; }; then
  PRUNE_ARGS=()
  # shellcheck disable=SC2086
  for d in $EXCLUDE_DIRS; do PRUNE_ARGS+=( -name "$d" -o ); done
  if [ "${#PRUNE_ARGS[@]}" -gt 0 ]; then
    unset 'PRUNE_ARGS[${#PRUNE_ARGS[@]}-1]'  # drop trailing -o
    find "$SRC_ABS" -mindepth 1 \( "${PRUNE_ARGS[@]}" \) -prune -o \
      -type f -name '*.js' ! -name '* copy*.js' -print \
      | while IFS= read -r f; do printf '%s\n' "${f#"$SRC_ABS"/}"; done >> "$REL_LIST"
  else
    find "$SRC_ABS" -mindepth 1 \
      -type f -name '*.js' ! -name '* copy*.js' -print \
      | while IFS= read -r f; do printf '%s\n' "${f#"$SRC_ABS"/}"; done >> "$REL_LIST"
  fi
fi

sort -u "$REL_LIST" -o "$REL_LIST"
sed -i '/^$/d' "$REL_LIST"
TOTAL="$(wc -l < "$REL_LIST" | tr -d ' ')"
# Empty selection = nothing to check = pass (keeps deploy --lint green for
# non-JS selections; warnings for bad paths were already printed above).
[ "$TOTAL" -gt 0 ] || { echo "nothing to lint."; exit 0; }
echo "linting $TOTAL JS file(s) with $(node --version) (node --check) ..."

TMPDIR_JS="$(mktemp -d)"
trap 'rm -f "$REL_LIST"; rm -rf "$TMPDIR_JS"' EXIT

fail=0; n=0
while IFS= read -r f; do
  n=$((n+1))
  [ $((n % 50)) -eq 0 ] && echo "... $n files" >&2
  ap="$SRC_ABS/$f"
  [ -f "$ap" ] || { echo "WARNING: disappeared: $f" >&2; continue; }
  target="$ap"; disp="$f"
  if grep -qE '^[[:space:]]*(import[ ({]|export[ ({])' "$ap" 2>/dev/null; then
    # ESM: node --check parses .js as CommonJS, so check via a .mjs copy.
    # Filenames are unique per basename risk: hash the relative path.
    h="$(printf '%s' "$f" | cksum | cut -d' ' -f1)"
    target="$TMPDIR_JS/mod-$h.mjs"; disp="$f (esm)"
    cp "$ap" "$target"
  fi
  out=$(node --check "$target" 2>&1) || {
    echo "FAIL: $disp"; echo "$out" | head -n 5 | sed "s/^/      /"; fail=$((fail+1))
  }
done < "$REL_LIST"

echo "linted: $n, failures: $fail"
if [ "$fail" -eq 0 ]; then echo "OK: no syntax errors."; exit 0; fi
echo "RESULT: $fail file(s) with errors."
exit 1
