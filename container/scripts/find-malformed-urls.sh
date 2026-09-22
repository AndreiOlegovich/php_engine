#!/usr/bin/env bash
# Find malformed URLs in the site tree (concatenated schemes/hosts etc.).
#
# Catches mistakes like:
#   https://www.testsetup.ruhttps://aredel.com/...   (scheme glued twice)
#   https://ru/qa/aredel.com/ru/i/                   (dotless host)
#
# Usage:
#   ./find-malformed-urls.sh [--src DIR] [--all] [--staged] [--committed]
#                            [--files F ...] [paths...]
#
# Selection (default: whole tree; flags/paths combine as a union):
#   --all             everything under --src (*.php, *.js, *.html)
#   --staged          staged changes (git diff --cached)
#   --committed       git-tracked files (git ls-files; --commited also accepted)
#   --files F ...     explicit files/dirs (space/comma separated, repeatable;
#                     paths relative to --src, or src/... prefixed; dirs expand)
#   paths...          positional files/dirs, same handling as --files
#
# Output: src/relative/path:line: text... [check]. Exit 0 = clean, 1 = hits.
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

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }
echo "hello" | grep -P "h.llo" >/dev/null 2>&1 || { echo "ERROR: grep -P (PCRE) not supported here." >&2; exit 2; }

SRC_ABS="$(cd "$SRC_DIR" && pwd -P)"
# REL_LIST holds src-relative paths (portable, space-safe one-per-line).
REL_LIST="$(mktemp)"
trap 'rm -f "$REL_LIST"' EXIT

to_rel() {
  # print src-relative path for a token, or empty + warning on stderr
  local t="${1#./}"
  case "$t" in
    "$SRC_DIR"/*) t="${t#$SRC_DIR/}" ;;
    container/src/*) t="${t#container/src/}" ;;
  esac
  case "$t" in
    /*)
      case "$t" in
        "$SRC_ABS"/*) t="${t#$SRC_ABS/}" ;;
        *) echo "WARNING: skipping '$1' (outside $SRC_DIR)." >&2; return 1 ;;
      esac
      ;;
  esac
  if [ -e "$SRC_ABS/$t" ]; then printf '%s\n' "$t"; else
    echo "WARNING: skipping '$1' (not found under $SRC_DIR)." >&2; return 1
  fi
}

if [ "${#FILES_LIST[@]}" -gt 0 ] || [ "${#POSITIONAL[@]}" -gt 0 ]; then
  for tok in "${FILES_LIST[@]}" "${POSITIONAL[@]}"; do
    [ -n "$tok" ] || continue
    rel="$(to_rel "$tok")" || continue
    if [ -d "$SRC_ABS/$rel" ] && [ ! -L "$SRC_ABS/$rel" ]; then
      find "$SRC_ABS/$rel" -type f \( -name '*.php' -o -name '*.js' -o -name '*.html' \) \
        | while IFS= read -r f; do printf '%s\n' "${f#$SRC_ABS/}"; done >> "$REL_LIST"
    else
      case "$rel" in *.php|*.js|*.html) printf '%s\n' "$rel" >> "$REL_LIST" ;;
        *) echo "WARNING: skipping '$tok' (not a .php/.js/.html file)." >&2 ;;
      esac
    fi
  done
fi

if [ "$STAGED" -eq 1 ] || [ "$COMMITTED" -eq 1 ]; then
  TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--staged/--committed need git)." >&2; exit 1; }
  PREFIX="${SRC_ABS#$TOP/}"
  [ "$PREFIX" != "$SRC_ABS" ] || { echo "ERROR: $SRC_DIR is outside the git repo." >&2; exit 1; }
  if [ "$STAGED" -eq 1 ]; then
    git -C "$SCRIPT_DIR" diff --cached --name-only -z -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep -E '\.(php|js|html)$' >> "$REL_LIST" || true
  fi
  if [ "$COMMITTED" -eq 1 ]; then
    git -C "$SCRIPT_DIR" ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep -E '\.(php|js|html)$' >> "$REL_LIST" || true
  fi
fi

# --- checks (single-quoted PCRE, no escaping traps) ---
P_DOUBLE='https?://[^"'\''<> ]*?https?://'
P_HOST='https?://[A-Za-z0-9_-]+/'

HITS=0
# Doc placeholders, not real URLs (http://url/..., http://foo/bar, ...).
PLACEHOLDERS="url foo bar hostname host example your-host your_host yourhost jenkins_url"
report() {
  # $1 = label; grep -n output lines arrive on stdin
  local label="$1" line h
  while IFS= read -r line; do
    case "$line" in
      *"://localhost/"*|"://localhost:"*) continue ;;  # legit dotless host
    esac
    for h in $PLACEHOLDERS; do
      # shellcheck disable=SC2053
      case "$line" in *"://$h/"*) continue 2 ;; esac
    done
    line="${line#$SRC_ABS/}"  # show src-relative paths
    printf '%s [%s]\n' "$line" "$label"
    HITS=$((HITS + 1))
  done
  return 0
}

if [ -s "$REL_LIST" ]; then
  sort -u "$REL_LIST" -o "$REL_LIST"; sed -i '/^$/d' "$REL_LIST"
  N="$(wc -l < "$REL_LIST" | tr -d ' ')"
  [ "$N" -gt 0 ] || { echo "nothing to scan."; exit 0; }
  echo "scanning $N selected file(s) under $SRC_DIR/ ..."
  mapfile -t ABS < <(while IFS= read -r rel; do printf '%s\n' "$SRC_ABS/$rel"; done < "$REL_LIST")
  # NOTE: report must NOT be the last stage of a pipeline (subshell would eat
  # the HITS counter) — feed it via process substitution instead.
  report doubled-scheme < <(grep -P -In -e "$P_DOUBLE" -- "${ABS[@]}" 2>/dev/null || true)
  report dotless-host < <(grep -P -In -e "$P_HOST" -- "${ABS[@]}" 2>/dev/null || true)
else
  echo "scanning whole $SRC_DIR/ (*.php, *.js, *.html) ..."
  report doubled-scheme < <(grep -rP -In -e "$P_DOUBLE" \
      --include='*.php' --include='*.js' --include='*.html' \
      --exclude-dir=.git --exclude-dir=reports --exclude-dir=__pycache__ \
      --exclude-dir=.pytest_cache --exclude-dir=.venv --exclude-dir=venv \
      --exclude-dir=node_modules "$SRC_ABS" 2>/dev/null || true)
  report dotless-host < <(grep -rP -In -e "$P_HOST" \
      --include='*.php' --include='*.js' --include='*.html' \
      --exclude-dir=.git --exclude-dir=reports --exclude-dir=__pycache__ \
      --exclude-dir=.pytest_cache --exclude-dir=.venv --exclude-dir=venv \
      --exclude-dir=node_modules "$SRC_ABS" 2>/dev/null || true)
fi

echo "----"
if [ "$HITS" -eq 0 ]; then
  echo "OK: no malformed URLs."
  exit 0
else
  echo "RESULT: $HITS malformed URL hit(s)."
  exit 1
fi
