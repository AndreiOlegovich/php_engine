#!/usr/bin/env bash
# Lint shell scripts with shellcheck via a one-off Docker image.
#
# No host installs: koalaman/shellcheck:stable is pulled once, reused after.
# Read-only: the repo is mounted :ro, so this script never modifies code.
#
# Usage:
#   ./lint-sh.sh [--all] [--staged] [--files F ...] [paths...]
#
# Selection (default: --all; flags/paths combine as a union):
#   --all           every maintained script (container/scripts/*.sh and
#                   top-level container_php83/*.sh).
#   --staged        staged *.sh files (explicit user action: legacy files
#                   staged here are checked too, not skipped).
#   --files F ...   explicit files/dirs (space and/or comma separated,
#                   repeatable; dirs expand to *.sh). Explicit paths are
#                   always checked, even legacy ones under src/ (you asked
#                   for those files).
#   paths...        positional files/dirs, same handling as --files.
#
# Paths may be repo-root-relative (pre-commit runs from the repo root) or
# cwd-relative (manual use); both are resolved via git.
#
# Exit: 0 = clean, 1 = findings (or usage/setup failure: 1/2).
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$TOP" ] || { echo "ERROR: not inside a git repo." >&2; exit 1; }

SHELLCHECK_IMAGE="${ADEL_SHELLCHECK_IMAGE:-koalaman/shellcheck:stable}"

ALL=0; STAGED=0
FILES_LIST=()
POSITIONAL=()

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --all) ALL=1; shift ;;
    --staged) STAGED=1; shift ;;
    --files)
      shift
      got=0
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

REL_LIST="$(mktemp)"
trap 'rm -f "$REL_LIST"' EXIT

# Resolve one token to repo-relative .sh path(s); dirs expand to *.sh.
resolve() { # $1 = token
  local tok="$1" t="$1" abs rel
  t="${t#./}"
  if [ -e "$_START_DIR/$t" ]; then
    abs="$(cd "$_START_DIR" && realpath -m "$t")"
  elif [ -e "$TOP/$t" ]; then
    abs="$(realpath -m "$TOP/$t")"
  else
    echo "WARNING: skipping '$tok' (not found)." >&2; return 0
  fi
  case "$abs" in
    "$TOP"/*) ;;
    *) echo "WARNING: skipping '$tok' (outside the repo)." >&2; return 0 ;;
  esac
  rel="${abs#"$TOP"/}"
  if [ -d "$abs" ] && [ ! -L "$abs" ]; then
    find "$abs" -type f -name "*.sh" | while IFS= read -r f; do
      printf '%s\n' "${f#"$TOP"/}"
    done >> "$REL_LIST"
  elif [ -f "$abs" ] && [ "$abs" = "${abs%.sh}" ]; then
    echo "WARNING: skipping '$tok' (not a .sh file)." >&2
  elif [ -f "$abs" ]; then
    printf '%s\n' "$rel" >> "$REL_LIST"
  else
    echo "WARNING: skipping '$tok' (not a regular file)." >&2
  fi
}

# --- 1) explicit files/dirs: --files + positionals (always checked) ---
if [ "${#FILES_LIST[@]}" -gt 0 ] || [ "${#POSITIONAL[@]}" -gt 0 ]; then
  for tok in "${FILES_LIST[@]}" "${POSITIONAL[@]}"; do
    [ -n "$tok" ] || continue
    resolve "$tok"
  done
fi

# --- 2) staged *.sh (added/copied/modified/renamed; deletions excluded) ---
if [ "$STAGED" -eq 1 ]; then
  git -C "$TOP" diff --cached --name-only -z --diff-filter=ACMR -- '*.sh' 2>/dev/null \
    | tr '\0' '\n' | grep '\.sh$' >> "$REL_LIST" || true
fi

# --- 3) default/all: maintained scripts only (legacy src/ bulk excluded) ---
if [ "$ALL" -eq 1 ] || { [ "${#FILES_LIST[@]}" -eq 0 ] && [ "${#POSITIONAL[@]}" -eq 0 ] && [ "$STAGED" -eq 0 ]; }; then
  for f in "$TOP/container/scripts/"*.sh "$TOP/container_php83/"*.sh; do
    [ -f "$f" ] || continue
    printf '%s\n' "${f#"$TOP"/}" >> "$REL_LIST"
  done
fi

sort -u "$REL_LIST" -o "$REL_LIST"
sed -i '/^$/d' "$REL_LIST"
mapfile -t PATHS < "$REL_LIST"
[ "${#PATHS[@]}" -gt 0 ] || { echo "nothing to lint."; exit 0; }
echo "linting ${#PATHS[@]} shell script(s) with shellcheck ..."

docker image inspect "$SHELLCHECK_IMAGE" >/dev/null 2>&1 \
  || docker pull "$SHELLCHECK_IMAGE" >&2
docker run --rm -v "$TOP:/mnt:ro" -w /mnt "$SHELLCHECK_IMAGE" -- "${PATHS[@]}"
