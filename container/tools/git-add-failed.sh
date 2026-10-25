#!/usr/bin/env bash
# Stage files reported as failed by the last commit check (pre-commit / lint).
#
# Parses a failure log for repo files and runs `git add --` on them.
# Nothing is committed — only staged. Review with `git status` / `git diff --cached`.
#
# Log formats understood (mixed in one file is fine):
#   FILE: /app/src/aws/RelatedArticles.php        (lint-static.sh / phpcs report)
#   FAIL: ao/api/php_auth/index.php               (lint-php.sh report)
#   FAIL: ru/qa/.php/_footers/season/Aug.php
#
# Path mapping:
#   /app/src/<rel>  -> container/src/<rel>   (container mount in docker)
#   src/<rel>       -> container/src/<rel>   (lint-static run from container/)
#   <rel>.php       -> container/src/<rel>   (lint-php --files style, relative to src/)
# Already repo-relative paths (container/src/..., PHPCS_FIX_GUIDE.md) pass through.
#
# Usage:
#   ./git-add-failed.sh [LOG] [--dry-run] [--all] [-h|--help]
#
#   LOG         failure log file (default: latest_commit_log2.txt next to this
#               script's parent dir? No — default: container/latest_commit_log2.txt
#               when run from container/, else ./latest_commit_log2.txt).
#               Pass - to read from stdin (e.g. `pre-commit run 2>&1 | ./git-add-failed.sh -`).
#   --dry-run   print `git add` command without running it.
#   --all       also stage untracked files listed in the log (default: only
#               files already tracked by git; untracked are listed as skipped).
#   -h|--help   this help.
#
# Examples:
#   ./git-add-failed.sh
#   ./git-add-failed.sh latest_commit_log2.txt --dry-run
#   pre-commit run --all-files 2>&1 | tee /tmp/check.log; ./git-add-failed.sh /tmp/check.log
#
# Exit: 0 = at least one file staged (or dry-run listed some), 1 = nothing to stage / usage error.
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$TOP" ] || { echo "ERROR: not inside a git repo." >&2; exit 1; }

LOG=""
DRY_RUN=0
INCLUDE_UNTRACKED=0

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --all) INCLUDE_UNTRACKED=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -) LOG="-"; shift ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *)
      [ -z "$LOG" ] || { echo "ERROR: only one LOG argument allowed." >&2; exit 2; }
      LOG="$1"; shift ;;
  esac
done

# Default log: prefer container/latest_commit_log2.txt when it exists.
if [ -z "$LOG" ]; then
  if [ -f "$TOP/container/latest_commit_log2.txt" ]; then
    LOG="$TOP/container/latest_commit_log2.txt"
  elif [ -f "$TOP/latest_commit_log2.txt" ]; then
    LOG="$TOP/latest_commit_log2.txt"
  elif [ -f "$TOP/container/latest_commit_log.txt" ]; then
    LOG="$TOP/container/latest_commit_log.txt"
  elif [ -f "$TOP/latest_commit_log.txt" ]; then
    LOG="$TOP/latest_commit_log.txt"
  else
    echo "ERROR: no log file found (looked for container/latest_commit_log2.txt)." >&2
    echo "HINT: pass a log explicitly: $0 /tmp/check.log" >&2
    exit 1
  fi
fi

if [ "$LOG" = "-" ]; then
  LOG_TMP="$(mktemp)"
  trap 'rm -f "$LOG_TMP"' EXIT
  cat > "$LOG_TMP"
  LOG="$LOG_TMP"
elif [ ! -f "$LOG" ]; then
  # Resolve relative to invocation cwd, then repo top.
  if [ -f "$_START_DIR/$LOG" ]; then
    LOG="$_START_DIR/$LOG"
  elif [ -f "$TOP/$LOG" ]; then
    LOG="$TOP/$LOG"
  else
    echo "ERROR: log file '$LOG' not found." >&2
    exit 1
  fi
fi

echo "log: $LOG"

# --- 1) extract candidate paths from the log ---
CANDIDATES="$(mktemp)"
trap 'rm -f "$CANDIDATES" "${LOG_TMP:-}"' EXIT

# FILE: <path>  |  FAIL: <path>  (path = $2; trailing punctuation trimmed)
awk '
  $1 == "FILE:" { p = $2; sub(/[,:;]+$/, "", p); print p }
  $1 == "FAIL:" { p = $2; sub(/[,:;]+$/, "", p); print p }
' "$LOG" | sort -u > "$CANDIDATES"

[ -s "$CANDIDATES" ] || { echo "nothing to stage (no FILE:/FAIL: entries in log)."; exit 1; }

# --- 2) map container paths to repo-relative paths ---
STAGE_LIST="$(mktemp)"
SKIPPED_LIST="$(mktemp)"
trap 'rm -f "$CANDIDATES" "$STAGE_LIST" "$SKIPPED_LIST" "${LOG_TMP:-}"' EXIT

while IFS= read -r cand; do
  [ -n "$cand" ] || continue
  rel=""
  case "$cand" in
    /app/src/*) rel="container/src/${cand#/app/src/}" ;;
    container/src/*) rel="$cand" ;;
    container/*) rel="$cand" ;;
    src/*) rel="container/${cand}" ;;
    /var/www/html/*) rel="container/src/${cand#/var/www/html/}" ;;
    /code/*) rel="container/src/${cand#/code/}" ;;
    /*)
      # Absolute host path inside the repo? Accept, else skip.
      case "$cand" in
        "$TOP"/*) rel="${cand#"$TOP"/}" ;;
        *) echo "  skip (outside repo): $cand" >> "$SKIPPED_LIST"; continue ;;
      esac
      ;;
    *)
      # Bare relative path: prefer container/src/<cand> when that file exists
      # (lint-php prints paths relative to src/), else take as repo-relative.
      if [ -e "$TOP/container/src/$cand" ]; then
        rel="container/src/$cand"
      else
        rel="$cand"
      fi
      ;;
  esac
  # Strip a stray leading ./ if present.
  rel="${rel#./}"
  if [ -e "$TOP/$rel" ]; then
    printf '%s\n' "$rel" >> "$STAGE_LIST"
  else
    echo "  skip (not found): $cand -> $rel" >> "$SKIPPED_LIST"
  fi
done < "$CANDIDATES"

sort -u "$STAGE_LIST" -o "$STAGE_LIST"
sed -i '/^$/d' "$STAGE_LIST"

# --- 3) by default, stage only tracked files; report untracked separately ---
UNTRACKED_LIST="$(mktemp)"
trap 'rm -f "$CANDIDATES" "$STAGE_LIST" "$SKIPPED_LIST" "$UNTRACKED_LIST" "${LOG_TMP:-}"' EXIT

if [ "$INCLUDE_UNTRACKED" -eq 0 ] && [ -s "$STAGE_LIST" ]; then
  FILTERED="$(mktemp)"
  trap 'rm -f "$CANDIDATES" "$STAGE_LIST" "$SKIPPED_LIST" "$UNTRACKED_LIST" "$FILTERED" "${LOG_TMP:-}"' EXIT
  while IFS= read -r f; do
    if git -C "$TOP" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
      printf '%s\n' "$f" >> "$FILTERED"
    else
      printf '%s\n' "$f" >> "$UNTRACKED_LIST"
    fi
  done < "$STAGE_LIST"
  mv "$FILTERED" "$STAGE_LIST"
fi

if [ ! -s "$STAGE_LIST" ]; then
  echo "nothing to stage (no log paths resolved to existing files)."
  [ -s "$SKIPPED_LIST" ] && { echo "skipped:"; cat "$SKIPPED_LIST"; }
  [ -s "$UNTRACKED_LIST" ] && { echo "untracked (re-run with --all to include):"; sed 's/^/  /' "$UNTRACKED_LIST"; }
  exit 1
fi

COUNT="$(wc -l < "$STAGE_LIST" | tr -d ' ')"
echo "staging $COUNT file(s):"
sed 's/^/  /' "$STAGE_LIST"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: nothing staged. Would run:"
  echo "git -C \"$TOP\" add -- \\"
  sed 's/^/  /' "$STAGE_LIST"
  exit 0
fi

# NUL-separated to survive spaces in names (e.g. "bash/2 copy.php").
tr '\n' '\0' < "$STAGE_LIST" | (cd "$TOP" && xargs -0 git add --)
echo "staged. Review with: git status --short; git diff --cached --stat"

if [ -s "$SKIPPED_LIST" ]; then
  echo "skipped:"; cat "$SKIPPED_LIST"
fi
if [ -s "$UNTRACKED_LIST" ]; then
  echo "untracked (not staged; re-run with --all to include):"
  sed 's/^/  /' "$UNTRACKED_LIST"
fi
