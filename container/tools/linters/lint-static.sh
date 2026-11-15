#!/usr/bin/env bash
# version 0.2.1
# Static analysis / code style for PHP via one-off Docker images.
#
# No host installs, no composer, no vendor/: tools run as versioned PHARs
# (cached in ~/.cache/php_engine-tools) under the php:8.2-cli image.
# Tools: phpstan (bugs), psalm (bugs), phpcs (style).
#
# Usage:
#   ./lint-static.sh [--src DIR] [--tool phpstan|psalm|phpcs]... [--level N]
#                    [--standard NAME] [--all] [--staged] [--committed]
#                    [--files F ...] [paths...] [--report FILE]
#                    [--worktree W] [-v|--verbose] [-h|--help]
#   Fixing works only through phpcs, so --fix always comes with --tool phpcs:
#   ./lint-static.sh --tool phpcs --fix [--fix-page] [--no-failure-report]
#                    [--worktree W]
#                    <--files F ... | paths... | --staged | --committed | --all>
#   Fix the files the last commit check reported:
#   ./lint-static.sh --fix-failed[=LIST] [--worktree W]
#
# Failed list of the commit check:
#   Every phpcs check with --staged (the pre-commit phpcs hook) saves the
#   paths of the files that failed to
#     container/log/phpcs-latest-commit-failed-list.log
#   one src/... path per line, paths only. A --staged check that finds
#   nothing removes the list. Another place: PHPCS_FAILED_LIST=FILE.
#   --fix-failed    fix only the files named in that list: the same run as
#                   --tool phpcs --fix --files <the paths in the list>.
#                   No question is asked and no other selection may be
#                   given. Afterwards the list holds the files that still
#                   fail, or is removed when none do. Fixed files are not
#                   staged: review with git diff, then git add.
#                   No list or an empty one: nothing to fix, exit code 0.
#   --fix-failed=LIST  read (and afterwards update) another list file.
#
# Another worktree:
#   --worktree W    lint (and with --fix, fix) the files of another git
#                   worktree instead of this checkout. W is a path (absolute
#                   or relative to where you start the script), the folder
#                   name of a worktree or its branch, as `git worktree list`
#                   shows them. Every selection (--files, paths, --staged,
#                   --committed, --all, --src) then reads that worktree, and
#                   its own phpcs.xml and phpstan.neon are used.
#
# Selection (default: all *.php under --src; flags combine as a union):
#   --files F ...   explicit files/dirs (space and/or comma separated,
#                   repeatable; paths relative to --src, or src/... prefixed).
#                   Consumes following args until the next flag.
#   paths...        positional files/dirs, same handling as --files.
#   --committed     git-tracked files under --src (git ls-files).
#   --staged        staged changes under --src (git diff --cached).
#   --all           all *.php under --src (minus deploy junk).
#
# Tool options:
#   --tool T        phpstan (default), psalm, phpcs. Repeatable / comma list.
#                   --fix needs phpcs here; the default phpstan cannot fix.
#   --level N       phpstan level 0-9 (default: 1; start low on legacy code).
#   --standard S    phpcs standard (default: ./phpcs.xml shared ruleset:
#                   PSR-12 plus project excludes).
#   --report FILE   also save the full output to FILE as a .txt report
#                   (".txt" is appended when missing; existing file is
#                   overwritten). Terminal output is unchanged.
#   --fix           REQUIRES --tool phpcs: write `--tool phpcs --fix`.
#                   Without it the script says that --tool is needed, prints
#                   a command for every tool, and then offers the fixer that
#                   fits the selected files, asking Y or n (Enter = Y):
#                     .php  phpcbf, the fixer of phpcs (same run as
#                           --tool phpcs --fix)
#                     .py   ruff (ruff check --fix, then ruff format, with
#                           the project's pyproject.toml)
#                   With both kinds selected (files of both types, or a
#                   folder that holds .php and .py files) two questions
#                   are asked, one per fixer, and each answer counts on
#                   its own. .sh and .js have no fixer here. Without a
#                   terminal nothing is asked and the script stops with
#                   exit code 2.
#                   Auto-fixes with phpcbf instead of just reporting with
#                   phpcs. Fixes the [x] violations IN PLACE — review with
#                   git diff afterwards.
#                   Order: safe families first (operator spacing, lowercase
#                   constants, trailing whitespace — they never loop), then
#                   the full pass, then per-family fallback (see below).
#                   [ ] violations (e.g. camelCaps names) are never auto-fixed.
#                   Needs an explicit scope: paths/--files, --staged,
#                   --committed, non-default --src, or --all. Bare --fix
#                   (whole-tree default) is refused — pass --all to fix all.
#                   Files excluded in ./phpcs.xml stay excluded; the skip is
#                   echoed on every run (pass --fix-page to opt back in).
#                   Conflict fallback: when a full phpcbf pass leaves files
#                   failing (two sniff families undoing each other makes
#                   phpcbf revert the whole file), the still-failing files
#                   are retried sniff-family by sniff-family, keeping a
#                   family's changes per file only when that file's error
#                   count drops — one looping family can no longer discard
#                   every other fix.
#   --fix-page      include BasePage.php/ArticlePage.php in --fix.
#                   Only valid together with --fix.
#                   After a --fix run, files that still fail phpcs are saved
#                   to $FAILED_TO_FIX_LOG/failed_to_fix_<timestamp>.log
#                   (one src-relative path per line, timestamp like
#                   20260925110712, UTC). FAILED_TO_FIX_LOG defaults to "log"
#                   (relative to the invocation dir, created as needed);
#                   override with an absolute or relative path, e.g.
#                   FAILED_TO_FIX_LOG=reports ./lint-static.sh --tool phpcs
#                   --fix --all. Pass --no-failure-report to skip creating
#                   that file.
#   --no-failure-report
#                   do not write the failed_to_fix_<timestamp>.log file
#                   after a --fix run. Only valid together with --fix.
#   -v, --verbose   print every file the script sees before the tools start
#                   ("files seen (N):", one src/... path per line; a folder
#                   is listed as the .php files in it; files the ruleset
#                   excludes are still listed), then verbose tool output
#                   (per-file progress). With a ruff run offered by --fix
#                   the .py files are listed the same way. Default shows
#                   a progress indicator plus per-tool timing.
#
# Exit: 0 = all selected tools clean, 1 = findings (or setup failure: 1/2).
#
# Examples (run from container/; paths are relative to src/):
#   Check, default tool phpstan:
#     ./tools/linters/lint-static.sh
#     ./tools/linters/lint-static.sh --all
#     ./tools/linters/lint-static.sh section/index.php
#     ./tools/linters/lint-static.sh --files section/.php/BasePage.php
#     ./tools/linters/lint-static.sh --files section section/php
#     ./tools/linters/lint-static.sh --files=section/index.php,git/index.php
#     ./tools/linters/lint-static.sh --staged
#     ./tools/linters/lint-static.sh --committed
#     ./tools/linters/lint-static.sh --staged --files section
#   Pick the tool:
#     ./tools/linters/lint-static.sh --tool phpstan --level 2 --files section
#     ./tools/linters/lint-static.sh --tool psalm --staged
#     ./tools/linters/lint-static.sh --tool phpcs --files section
#     ./tools/linters/lint-static.sh --tool phpcs --standard PSR12 --staged
#     ./tools/linters/lint-static.sh --tool phpstan,phpcs --level 2 --all
#     ./tools/linters/lint-static.sh --tool phpstan --tool psalm --staged
#   Fix style in place (phpcbf):
#     ./tools/linters/lint-static.sh --tool phpcs --fix --files section/packages
#     ./tools/linters/lint-static.sh --tool phpcs --fix section/index.php
#     ./tools/linters/lint-static.sh --tool phpcs --fix --staged
#     ./tools/linters/lint-static.sh --tool phpcs --fix --all
#     ./tools/linters/lint-static.sh --tool phpcs --fix --fix-page --files section/.php
#     ./tools/linters/lint-static.sh --tool phpcs --fix --no-failure-report --staged
#     FAILED_TO_FIX_LOG=reports ./tools/linters/lint-static.sh --tool phpcs --fix --all
#   Fix what the last commit check reported (list in log/):
#     git commit ...        # phpcs hook fails and saves the failed list
#     ./tools/linters/lint-static.sh --fix-failed
#     ./tools/linters/lint-static.sh -v --fix-failed
#     ./tools/linters/lint-static.sh --fix-failed=log/my-list.log
#     ./tools/linters/lint-static.sh --worktree agent/example --fix-failed
#     PHPCS_FAILED_LIST=/tmp/failed.log ./tools/linters/lint-static.sh --tool phpcs --staged
#   --fix without --tool phpcs: the script explains and asks Y or n:
#     ./tools/linters/lint-static.sh --fix section/index.php
#     ./tools/linters/lint-static.sh --fix --staged
#     ./tools/linters/lint-static.sh --fix tools/example.py
#     ./tools/linters/lint-static.sh --fix section/index.php tools/example.py
#     ./tools/linters/lint-static.sh --fix section/python
#   Another worktree (path, folder name or branch):
#     ./tools/linters/lint-static.sh --worktree ~/github/site-agent-example --staged
#     ./tools/linters/lint-static.sh --worktree site-agent-example --tool phpcs --files section
#     ./tools/linters/lint-static.sh --worktree agent/example --tool phpcs --fix --staged
#     ./tools/linters/lint-static.sh --worktree ../site-agent-example --tool phpcs --fix section/functions.php
#     ./tools/linters/lint-static.sh --worktree agent/example --fix tools/example.py
#   Report and output:
#     ./tools/linters/lint-static.sh --tool phpcs --all --report phpcs-all
#     ./tools/linters/lint-static.sh --tool psalm --files section --report /tmp/psalm.txt
#     ./tools/linters/lint-static.sh -v --tool phpstan --files section
#     ./tools/linters/lint-static.sh -v --tool phpcs --staged
#     ./tools/linters/lint-static.sh --verbose --worktree agent/example --tool phpcs --all
#     ./tools/linters/lint-static.sh --src src/section --tool phpstan
#     PHP_ENGINE_STATIC_PHP_IMAGE=php:8.4-cli ./tools/linters/lint-static.sh --staged
#     ./tools/linters/lint-static.sh --help
set -euo pipefail

# A hook in a linked worktree gets GIT_DIR without GIT_WORK_TREE, and then
# git -C <dir> takes <dir> for the top of the work tree. Let git find the
# worktree from the folder instead.
unset GIT_DIR GIT_WORK_TREE

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../..  # tools live in tools/linters/; project root (compose file, src/) is the runtime cwd
OWN_ROOT="$(pwd -P)"   # container/ of the checkout this script lives in
APP_ROOT="$OWN_ROOT"   # container/ that is linted; --worktree moves it

WORKTREE=""
FIX_FAILED=0
FAILED_LIST_FILE="${PHPCS_FAILED_LIST:-}"
TOOLS_GIVEN=0
RUFF_RC=0
SRC_DIR="src"
SRC_SET=0
TOOLS_LIST=()
LEVEL="1"
STANDARD="/app/phpcs.xml"
STANDARD_SET=0
REPORT=""
FIX=0
FIX_PAGE=0
NO_FAILURE_REPORT=0
VERBOSE=0
ALL=0; STAGED=0; COMMITTED=0
FILES_LIST=()
POSITIONAL=()

PHP_IMAGE="${PHP_ENGINE_STATIC_PHP_IMAGE:-php:8.2-cli}"
TOOL_CACHE="${PHP_ENGINE_STATIC_TOOL_CACHE:-$HOME/.cache/php_engine-tools}"
FAILED_TO_FIX_LOG="${FAILED_TO_FIX_LOG:-log}"
PHPSTAN_URL="https://github.com/phpstan/phpstan/releases/latest/download/phpstan.phar"
PSALM_URL="https://github.com/vimeo/psalm/releases/latest/download/psalm.phar"
PHPCS_URL="https://github.com/PHPCSStandards/PHP_CodeSniffer/releases/latest/download/phpcs.phar"
PHPCBF_URL="https://github.com/PHPCSStandards/PHP_CodeSniffer/releases/latest/download/phpcbf.phar"

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

add_tools() {
  local v="$1" _p
  IFS=',' read -ra _parts <<< "$v"
  for _p in "${_parts[@]}"; do
    _p="$(trim "$_p")"
    case "$_p" in
      phpstan|psalm|phpcs) TOOLS_LIST+=("$_p") ;;
      "") ;;
      *) echo "ERROR: unknown --tool '$_p' (want phpstan|psalm|phpcs)." >&2; exit 2 ;;
    esac
  done
}

while [ $# -gt 0 ]; do
  case "$1" in
    --src) [ $# -ge 2 ] || { echo "ERROR: --src needs a value." >&2; exit 2; }
      SRC_DIR="$2"; SRC_SET=1; shift 2 ;;
    --src=*) SRC_DIR="${1#--src=}"; SRC_SET=1; shift ;;
    --tool) [ $# -ge 2 ] || { echo "ERROR: --tool needs a value." >&2; exit 2; }
      add_tools "$2"; shift 2 ;;
    --tool=*) add_tools "${1#--tool=}"; shift ;;
    --level) [ $# -ge 2 ] || { echo "ERROR: --level needs a value (0-9)." >&2; exit 2; }
      LEVEL="$2"; shift 2 ;;
    --level=*) LEVEL="${1#--level=}"; shift ;;
    --standard) [ $# -ge 2 ] || { echo "ERROR: --standard needs a value." >&2; exit 2; }
      STANDARD="$2"; STANDARD_SET=1; shift 2 ;;
    --standard=*) STANDARD="${1#--standard=}"; STANDARD_SET=1; shift ;;
    --report) [ $# -ge 2 ] || { echo "ERROR: --report needs a value." >&2; exit 2; }
      REPORT="$2"; shift 2 ;;
    --report=*) REPORT="${1#--report=}"; shift ;;
    --all) ALL=1; shift ;;
    --staged) STAGED=1; shift ;;
    --committed|--commited) COMMITTED=1; shift ;;
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
    --worktree) [ $# -ge 2 ] || { echo "ERROR: --worktree needs a path, folder name or branch." >&2; exit 2; }
      WORKTREE="$2"; shift 2 ;;
    --worktree=*) WORKTREE="${1#--worktree=}"
      [ -n "$WORKTREE" ] || { echo "ERROR: --worktree needs a path, folder name or branch." >&2; exit 2; }
      shift ;;
    --fix) FIX=1; shift ;;
    --fix-failed) FIX_FAILED=1; shift ;;
    --fix-failed=*) FIX_FAILED=1; FAILED_LIST_FILE="${1#--fix-failed=}"
      [ -n "$FAILED_LIST_FILE" ] || { echo "ERROR: --fix-failed= needs a list file." >&2; exit 2; }
      shift ;;
    --fix-page) FIX_PAGE=1; shift ;;
    --no-failure-report) NO_FAILURE_REPORT=1; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

[ "${#TOOLS_LIST[@]}" -eq 0 ] || TOOLS_GIVEN=1
[ "${#TOOLS_LIST[@]}" -gt 0 ] || TOOLS_LIST=(phpstan)
# dedupe tools, keep order
DEDUP=()
for _t in "${TOOLS_LIST[@]}"; do
  skip=0
  for _d in "${DEDUP[@]}"; do [ "$_t" = "$_d" ] && skip=1; done
  [ "$skip" -eq 0 ] && DEDUP+=("$_t")
done
TOOLS_LIST=("${DEDUP[@]}")

# --- --worktree: move the project root to another worktree's container/ ---
# W is a directory, or the folder name / branch of a registered worktree.
resolve_worktree() {
  local w="$1" cand="" line path=""
  case "$w" in
    "~"/*) w="$HOME/${w#"~"/}" ;;
  esac
  case "$w" in
    /*) [ -d "$w" ] && cand="$w" ;;
    *) [ -d "$_START_DIR/$w" ] && cand="$_START_DIR/$w" ;;
  esac
  if [ -z "$cand" ]; then
    while IFS= read -r line; do
      case "$line" in
        "worktree "*)
          path="${line#worktree }"
          [ "${path##*/}" = "$w" ] && cand="$path"
          ;;
        "branch refs/heads/"*)
          [ "${line#branch refs/heads/}" = "$w" ] && cand="$path"
          ;;
      esac
    done < <(git -C "$OWN_ROOT" worktree list --porcelain 2>/dev/null)
  fi
  if [ -z "$cand" ]; then
    echo "ERROR: --worktree '$1' is not a directory, a worktree folder name or a worktree branch." >&2
    echo "Worktrees of this repository:" >&2
    git -C "$OWN_ROOT" worktree list >&2 2>/dev/null || true
    return 1
  fi
  if [ -d "$cand/container/src" ]; then
    (cd "$cand/container" && pwd -P)
  elif [ -d "$cand/src" ]; then
    (cd "$cand" && pwd -P)
  else
    echo "ERROR: --worktree '$1': no container/src under $cand." >&2
    return 1
  fi
}

if [ -n "$WORKTREE" ]; then
  APP_ROOT="$(resolve_worktree "$WORKTREE")" || exit 2
  cd "$APP_ROOT"
  echo "worktree: $APP_ROOT"
fi

# --- failed list of the commit check: paths only, one src/... per line ---
FAILED_LIST_CUSTOM=1
case "$FAILED_LIST_FILE" in
  "") FAILED_LIST_FILE="$APP_ROOT/log/phpcs-latest-commit-failed-list.log"; FAILED_LIST_CUSTOM=0 ;;
  /*) ;;
  *) FAILED_LIST_FILE="$_START_DIR/$FAILED_LIST_FILE" ;;
esac
# The script as a command for hints: the worktree in use goes with it.
HINT_CMD="./tools/linters/lint-static.sh"
[ -z "$WORKTREE" ] || HINT_CMD="$HINT_CMD --worktree $WORKTREE"
FIX_FAILED_FLAG="--fix-failed"
[ "$FAILED_LIST_CUSTOM" -eq 0 ] || FIX_FAILED_FLAG="--fix-failed=$FAILED_LIST_FILE"

# save_failed_list FILE : FILE holds phpcs output; its FILE: lines become
# the list (written to a temp file, then renamed). Nothing failing: the
# list is removed.
save_failed_list() {
  local list tmp
  list="$(grep '^FILE:' "$1" 2>/dev/null \
    | sed 's/^FILE:[[:space:]]*//; s#^/app/##; s/[[:space:]]*$//' | sort -u || true)"
  if [ -n "$list" ]; then
    mkdir -p "$(dirname "$FAILED_LIST_FILE")" || return 0
    tmp="$(mktemp "$FAILED_LIST_FILE.XXXXXX")" || return 0
    printf '%s\n' "$list" > "$tmp"
    mv "$tmp" "$FAILED_LIST_FILE"
    echo "failed list: $FAILED_LIST_FILE ($(printf '%s\n' "$list" | grep -c .) file(s))"
    echo "  fix them with: $HINT_CMD $FIX_FAILED_FLAG"
  elif [ -e "$FAILED_LIST_FILE" ]; then
    rm -f "$FAILED_LIST_FILE"
    echo "failed list removed (nothing failing): $FAILED_LIST_FILE"
  fi
}

if [ "$FIX_FAILED" -eq 1 ]; then
  if [ "$ALL" -eq 1 ] || [ "$STAGED" -eq 1 ] || [ "$COMMITTED" -eq 1 ] \
      || [ "${#FILES_LIST[@]}" -gt 0 ] || [ "${#POSITIONAL[@]}" -gt 0 ]; then
    echo "ERROR: --fix-failed takes its files from the failed list only; drop the other selection." >&2
    exit 2
  fi
  if [ ! -s "$FAILED_LIST_FILE" ]; then
    echo "no failed list at $FAILED_LIST_FILE: nothing to fix."
    echo "A phpcs check with --staged (the commit hook) writes it when files fail."
    echo "The hook of a checkout whose lint-static.sh is older than 0.2.0 never writes it."
    echo "  write the list now:        $HINT_CMD --tool phpcs --staged"
    echo "  or check and fix in one:   $HINT_CMD --tool phpcs --fix --staged"
    exit 0
  fi
  while IFS= read -r _line || [ -n "$_line" ]; do
    _line="$(trim "$_line")"
    case "$_line" in ""|"#"*) continue ;; esac
    FILES_LIST+=("$_line")
  done < "$FAILED_LIST_FILE"
  [ "${#FILES_LIST[@]}" -gt 0 ] || { echo "failed list $FAILED_LIST_FILE names no files: nothing to fix."; exit 0; }
  echo "failed list: $FAILED_LIST_FILE (${#FILES_LIST[@]} file(s) to fix)"
  FIX=1
  _has=0
  for _t in "${TOOLS_LIST[@]}"; do [ "$_t" = "phpcs" ] && _has=1; done
  if [ "$TOOLS_GIVEN" -eq 0 ]; then TOOLS_LIST=(phpcs); elif [ "$_has" -eq 0 ]; then TOOLS_LIST+=(phpcs); fi
fi

# --- --fix without a tool that can fix: explain, then offer the fixer ---
SELF_CMD="./tools/linters/lint-static.sh"

fix_tool_help() {
  local wt=""
  [ -z "$WORKTREE" ] || wt=" --worktree $WORKTREE"
  cat >&2 <<EOF
--fix needs the --tool flag: only phpcs can fix (through phpcbf), and the
tools selected now (${TOOLS_LIST[*]}) only report.

Tools of this script, with a command for each:
  phpstan  bugs, report only
             $SELF_CMD$wt --tool phpstan --level 2 --files section
  psalm    bugs, report only
             $SELF_CMD$wt --tool psalm --staged
  phpcs    style, report
             $SELF_CMD$wt --tool phpcs --files section
  phpcs    style, fix in place with phpcbf
             $SELF_CMD$wt --tool phpcs --fix --files section
             $SELF_CMD$wt --tool phpcs --fix --staged
             $SELF_CMD$wt --tool phpcs --fix --all
Other file types:
  .py      ruff fixes
             ruff check --fix --config pyproject.toml tools/example.py
             ruff format --config pyproject.toml tools/example.py
  .sh      shellcheck, report only
             ./tools/linters/lint-sh.sh --files tools/generate-sitemap.sh
  .js      node --check, report only
             ./tools/linters/lint-js.sh --files section/game.js

EOF
}

# ask_yn QUESTION : one key, Y or n (Enter = Y). No terminal: no question.
ask_yn() {
  local a=""
  if [ ! -t 0 ]; then
    echo "No terminal to ask on: nothing was fixed." >&2
    return 1
  fi
  printf '%s [Y/n] ' "$1" >&2
  IFS= read -r -n 1 a || a="n"
  [ -z "$a" ] || echo >&2
  case "$a" in
    ""|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

# find_target TOKEN : print the existing path a token means. A .php token is
# src-relative like everywhere in this script; other files are also looked
# up from the start folder, the project root and the repository root.
find_target() {
  local t="${1#./}" c
  local cands=()
  case "$t" in
    /*) cands=("$t") ;;
    *)
      [ -n "$WORKTREE" ] || cands+=("$_START_DIR/$t")
      cands+=("$APP_ROOT/$SRC_DIR/$t" "$APP_ROOT/$t" "$APP_ROOT/../$t")
      ;;
  esac
  for c in "${cands[@]}"; do
    if [ -e "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

dir_has() { # $1 = dir, $2 = extension
  [ -n "$(find "$1" \( -name .venv -o -name node_modules -o -name .git \) -prune \
    -o -type f -name "*.$2" -print -quit 2>/dev/null)" ]
}

# print_seen EXT paths... : -v listing of the files behind the paths;
# a folder is listed as its *.EXT files.
print_seen() {
  local ext="$1" _p seen
  shift
  seen="$(for _p in "$@"; do
    if [ -d "$_p" ]; then
      find "$_p" \( -name .venv -o -name node_modules -o -name .git \) -prune \
        -o -type f -name "*.$ext" -print
    else
      printf '%s\n' "$_p"
    fi
  done | sort -u)"
  echo "files seen ($(printf '%s' "$seen" | grep -c . || true)):"
  [ -z "$seen" ] || printf '%s\n' "$seen" | sed 's/^/  /'
}

run_ruff() {
  local ruff="" rc=0
  local cfg=()
  if [ -x "$APP_ROOT/.venv/bin/ruff" ]; then ruff="$APP_ROOT/.venv/bin/ruff"
  elif [ -x "$OWN_ROOT/.venv/bin/ruff" ]; then ruff="$OWN_ROOT/.venv/bin/ruff"
  elif command -v ruff >/dev/null 2>&1; then ruff="ruff"
  else
    echo "ERROR: ruff not found (looked in .venv/bin and PATH); run: uv sync --project $OWN_ROOT" >&2
    return 2
  fi
  [ ! -f "$APP_ROOT/pyproject.toml" ] || cfg=(--config "$APP_ROOT/pyproject.toml")
  [ "$VERBOSE" -eq 0 ] || print_seen py "$@"
  echo "fixing IN PLACE with ruff (review with git diff afterwards) ..."
  echo "==> ruff check --fix ..."
  "$ruff" check --fix "${cfg[@]}" -- "$@" || rc=1
  echo "==> ruff format ..."
  "$ruff" format "${cfg[@]}" -- "$@" || rc=1
  return "$rc"
}

if [ "$FIX" -eq 1 ]; then
  has_phpcs=0
  for _t in "${TOOLS_LIST[@]}"; do [ "$_t" = "phpcs" ] && has_phpcs=1; done
  if [ "$has_phpcs" -eq 0 ]; then
    fix_tool_help
    # Sort the explicit targets by file type; .py targets leave the PHP run.
    PHP_SEEN=0
    PY_TARGETS=()
    KEEP=()
    N_EXPLICIT=0
    for tok in "${FILES_LIST[@]}" "${POSITIONAL[@]}"; do
      [ -n "$tok" ] || continue
      N_EXPLICIT=$((N_EXPLICIT + 1))
      if ! _found="$(find_target "$tok")"; then
        echo "WARNING: '$tok' not found; skipped." >&2
        continue
      fi
      if [ -d "$_found" ]; then
        _kinds=0
        if dir_has "$_found" php; then PHP_SEEN=1; KEEP+=("$tok"); _kinds=1; fi
        if dir_has "$_found" py; then PY_TARGETS+=("$_found"); _kinds=1; fi
        [ "$_kinds" -eq 1 ] || echo "NOTE: '$tok' holds no .php or .py files; nothing to fix there." >&2
        continue
      fi
      case "$_found" in
        *.php) PHP_SEEN=1; KEEP+=("$tok") ;;
        *.py) PY_TARGETS+=("$_found") ;;
        *.sh) echo "NOTE: '$tok': shell scripts have no fixer here; shellcheck only reports (./tools/linters/lint-sh.sh --files ...)." >&2 ;;
        *.js) echo "NOTE: '$tok': JavaScript has no fixer here; ./tools/linters/lint-js.sh only reports." >&2 ;;
        *) echo "NOTE: '$tok': no fixer known for this file type." >&2 ;;
      esac
    done
    # --staged, --committed and --all select .php files only.
    if [ "$STAGED" -eq 1 ] || [ "$COMMITTED" -eq 1 ] || [ "$ALL" -eq 1 ] \
        || { [ "$SRC_SET" -eq 1 ] && [ "$N_EXPLICIT" -eq 0 ]; }; then
      PHP_SEEN=1
    fi
    FILES_LIST=("${KEEP[@]}")
    POSITIONAL=()
    RAN_RUFF=0
    if [ "$PHP_SEEN" -eq 1 ]; then
      if ask_yn "PHP files are selected. Run the fix with phpcbf, the fixer of phpcs (same as --tool phpcs --fix)?"; then
        if [ "$TOOLS_GIVEN" -eq 1 ]; then TOOLS_LIST+=(phpcs); else TOOLS_LIST=(phpcs); fi
        has_phpcs=1
      fi
    fi
    if [ "${#PY_TARGETS[@]}" -gt 0 ]; then
      if ask_yn "Python files are selected (${#PY_TARGETS[@]} path(s)). Run the fix with ruff (ruff check --fix, then ruff format)?"; then
        RAN_RUFF=1
        run_ruff "${PY_TARGETS[@]}" || RUFF_RC=1
      fi
    fi
    if [ "$has_phpcs" -eq 0 ]; then
      if [ "$RAN_RUFF" -eq 1 ]; then
        [ "$RUFF_RC" -eq 0 ] && echo "OK: ruff clean." || echo "DONE: ruff findings above."
        exit "$RUFF_RC"
      fi
      if [ "$PHP_SEEN" -eq 0 ] && [ "${#PY_TARGETS[@]}" -eq 0 ]; then
        echo "ERROR: --fix needs --tool phpcs and a scope, e.g. --tool phpcs --fix --files section/packages" >&2
      else
        echo "ERROR: nothing was fixed: --fix needs --tool phpcs." >&2
      fi
      exit 2
    fi
  fi
fi
if [ "$FIX_PAGE" -eq 1 ] && [ "$FIX" -eq 0 ]; then
  echo "ERROR: --fix-page needs --fix." >&2; exit 2
fi
if [ "$NO_FAILURE_REPORT" -eq 1 ] && [ "$FIX" -eq 0 ]; then
  echo "ERROR: --no-failure-report needs --fix." >&2; exit 2
fi
if [ "$FIX" -eq 1 ] && [ "$ALL" -eq 0 ] && [ "${#FILES_LIST[@]}" -eq 0 ] \
    && [ "${#POSITIONAL[@]}" -eq 0 ] && [ "$STAGED" -eq 0 ] \
    && [ "$COMMITTED" -eq 0 ] && [ "$SRC_SET" -eq 0 ]; then
  echo "ERROR: --fix refuses the whole-tree default (no scope given)." >&2
  echo "HINT: pass explicit paths (--files ... / paths...), --staged," >&2
  echo "      --committed, --src DIR, or --all to fix everything." >&2
  exit 2
fi

[[ "$LEVEL" =~ ^[0-9]$ ]] || { echo "ERROR: bad --level '$LEVEL' (want 0-9)." >&2; exit 2; }
[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }

SRC_ABS="$(cd "$SRC_DIR" && pwd -P)"
REL_LIST="$(mktemp)"
RULESET_FILE="$APP_ROOT/phpcs.xml"
RULESET_TMP=""
PSALM_CFG=""
trap 'rm -f "$REL_LIST"; for _d in $PSALM_CFG $RULESET_TMP; do [ -n "$_d" ] && rm -rf "$_d"; done' EXIT

# <exclude-pattern> values from the shared ruleset (for the per-run echo).
ruleset_excludes() {
  grep -o '<exclude-pattern>[^<]*</exclude-pattern>' "$RULESET_FILE" 2>/dev/null \
    | sed 's#.*<exclude-pattern>##; s#</exclude-pattern>.*##'
}

# --- phpcbf --fix conflict fallback -------------------------------------
# Safe families first: these never loop (pure spacing/casing/whitespace),
# so they run before the full pass to shrink the violation set early.
# One looping sniff family makes phpcbf discard the WHOLE file ("FAILED TO
# FIX"), wiping out every other fix. The fallback below retries the still-
# failing files family by family (safe whitespace first, structural last)
# and keeps a family's changes per file only when that file's error count
# drops (monotonic guard).
# Helpers share globals set by the caller: APP_ROOT, TOOL_CACHE, PHP_IMAGE,
# EFF_STANDARD (ruleset inside the container), EXTRA_MOUNT (array) and
# _extra (array, progress flags). Paths are APP-relative (src/...), which
# doubles as the in-container path (-w /app).
FIX_SNIFF_GROUPS=(
  "PSR12.Files.FileHeader,PSR12.Operators.OperatorSpacing,Squiz.WhiteSpace.SuperfluousWhitespace,Generic.Files.LineEndings,PSR2.Files.ClosingTag,PSR2.Files.EndFileNewLine,Generic.Functions.FunctionCallArgumentSpacing,Generic.PHP.LowerCaseConstant,PSR12.ControlStructures.ControlStructureSpacing,Squiz.WhiteSpace.ControlStructureSpacing,Squiz.Functions.FunctionDeclarationArgumentSpacing,Generic.Formatting.DisallowMultipleStatements,PSR2.ControlStructures.ElseIfDeclaration"
  "PSR2.Methods.FunctionCallSignature"
  "Squiz.ControlStructures.ControlSignature"
  "Generic.WhiteSpace.ScopeIndent"
  "Squiz.WhiteSpace.ScopeClosingBrace,PSR2.Methods.FunctionClosingBrace"
  "Squiz.Functions.MultiLineFunctionDeclaration"
)

# Safe families: loop-free subset applied before the full pass.
SAFE_SNIFFS="PSR12.Operators.OperatorSpacing,Generic.PHP.LowerCaseConstant,Squiz.WhiteSpace.SuperfluousWhitespace"

# phpcbf_paths [--sniffs S] -- paths... : run phpcbf, ignore its exit code
# (it exits nonzero whenever it touches anything).
phpcbf_paths() {
  local sniffs=()
  # NOTE: --sniffs needs the equals form; space-separated is rejected
  # with: ERROR: option "--sniffs" not known
  if [ "${1:-}" = "--sniffs" ]; then sniffs=("--sniffs=$2"); shift 2; fi
  [ "${1:-}" = "--" ] && shift
  docker run --rm -v "$APP_ROOT:/app" -w /app -v "$TOOL_CACHE:/tools:ro" \
    "${EXTRA_MOUNT[@]}" \
    "$PHP_IMAGE" php /tools/phpcbf.phar --standard="$EFF_STANDARD" \
    "${sniffs[@]}" "${_extra[@]}" -- "$@" || true
}

# phpcs_csv paths... : print phpcs --report=csv to stdout.
phpcs_csv() {
  docker run --rm -v "$APP_ROOT:/app" -w /app -v "$TOOL_CACHE:/tools:ro" \
    "${EXTRA_MOUNT[@]}" \
    "$PHP_IMAGE" php /tools/phpcs.phar --standard="$EFF_STANDARD" \
    --report=csv -- "$@" 2>/dev/null || true
}

# csv_file_count csv_text container_path : print error+warning rows for file.
csv_file_count() {
  printf '%s\n' "$1" | grep -c "^\"$2\"," || true
}

# csv_failing_files csv_text : print failing container paths (/app/...), sorted.
csv_failing_files() {
  printf '%s\n' "$1" | awk -F',' 'NR>1 {print $1}' | tr -d '"' | sort -u
}

# fix_fallback_groups paths... : per-family passes with monotonic guard.
# Modifies files in place; always returns 0.
fix_fallback_groups() {
  local csv failing group f rel backup before after
  csv="$(phpcs_csv "$@")"
  failing="$(csv_failing_files "$csv")"
  [ -z "$failing" ] && return 0
  backup="$(mktemp -d)"
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    rel="${f#/app/}"
    mkdir -p "$backup/$(dirname "$rel")"
    cp "$APP_ROOT/$rel" "$backup/$rel"
  done <<<"$failing"
  for group in "${FIX_SNIFF_GROUPS[@]}"; do
    [ -z "$failing" ] && break
    echo "fallback: --sniffs=${group%%,*}... ($(printf '%s\n' "$failing" | grep -c . | tr -d ' ') file(s) still failing)"
    declare -A before=()
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      before["$f"]="$(csv_file_count "$csv" "$f")"
    done <<<"$failing"
    mapfile -t _targets <<<"$failing"
    phpcbf_paths --sniffs "$group" -- "${_targets[@]}"
    csv="$(phpcs_csv "$@")"
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      after="$(csv_file_count "$csv" "$f")"
      rel="${f#/app/}"
      if [ "$after" -lt "${before[$f]:-0}" ] || [ "$after" -eq 0 ]; then
        cp "$APP_ROOT/$rel" "$backup/$rel"  # improved/clean: keep, refresh backup
      else
        cp "$backup/$rel" "$APP_ROOT/$rel"  # no progress: restore pre-group state
      fi
    done <<<"$failing"
    failing="$(csv_failing_files "$csv")"
  done
  rm -rf "$backup"
}

# Whole-tree selections pass the tree as-is (analyzers resolve cross-file
# symbols); no deploy-junk pruning here, unlike lint-php.sh.
# Explicit paths bypass nothing — you asked for those files.

# --- 1) explicit files/dirs: --files + positionals (kept as given, src-relative) ---
declare -a PATHS=()
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
          *) echo "WARNING: skipping '$tok' (outside $SRC_DIR; hint: drop the leading slash, e.g. 'section/pytest')." >&2; continue ;;
        esac
        ;;
    esac
    ap="$SRC_ABS/$t"
    if [ -e "$ap" ]; then
      PATHS+=("$SRC_DIR/$t")
      printf '%s\n' "$t" >> "$REL_LIST"
    else
      echo "WARNING: skipping '$tok' (not found under $SRC_DIR)." >&2
    fi
  done
fi

# --- 2) git selections ---
if [ "$STAGED" -eq 1 ] || [ "$COMMITTED" -eq 1 ]; then
  TOP="$(git -C "$APP_ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--committed/--staged need git)." >&2; exit 1; }
  PREFIX="${SRC_ABS#"$TOP"/}"
  [ "$PREFIX" != "$SRC_ABS" ] || { echo "ERROR: $SRC_DIR is outside the git repo." >&2; exit 1; }
  if [ "$STAGED" -eq 1 ]; then
    git -C "$APP_ROOT" diff --cached --name-only -z --diff-filter=ACMR -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep '\.php$' >> "$REL_LIST" || true
  fi
  if [ "$COMMITTED" -eq 1 ]; then
    git -C "$APP_ROOT" ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
      | tr '\0' '\n' | sed "s#^$PREFIX/##" | grep '\.php$' >> "$REL_LIST" || true
  fi
  while IFS= read -r _r; do
    [ -n "$_r" ] || continue
    skip=0
    for _p in "${PATHS[@]}"; do [ "$_p" = "$SRC_DIR/$_r" ] && skip=1; done
    [ "$skip" -eq 0 ] && PATHS+=("$SRC_DIR/$_r")
  done < "$REL_LIST"
fi

# --- 3) default/all: pass the whole tree (analyzers resolve cross-file symbols) ---
if [ "$ALL" -eq 1 ] || { [ "${#FILES_LIST[@]}" -eq 0 ] && [ "${#POSITIONAL[@]}" -eq 0 ] && [ "$STAGED" -eq 0 ] && [ "$COMMITTED" -eq 0 ]; }; then
  PATHS=("$SRC_DIR")
fi

[ "${#PATHS[@]}" -gt 0 ] || { echo "nothing to check."; exit 0; }

# --- report: mirror all output below into a .txt file (terminal unchanged) ---
if [ -n "$REPORT" ]; then
  case "$REPORT" in
    *.txt) ;;
    *) REPORT="$REPORT.txt" ;;
  esac
  RDIR="$(dirname "$REPORT")"
  [ -d "$RDIR" ] || { echo "ERROR: report dir '$RDIR' not found." >&2; exit 2; }
  [ -w "$RDIR" ] || { echo "ERROR: report dir '$RDIR' not writable." >&2; exit 2; }
  rm -f "$REPORT"
  exec > >(tee "$REPORT") 2>&1
  echo "report: $REPORT"
  date -u +"report started: %Y-%m-%dT%H:%M:%SZ"
fi

[ "$VERBOSE" -eq 0 ] || print_seen php "${PATHS[@]}"

for _t in "${TOOLS_LIST[@]}"; do
  if [ "$_t" = "phpcs" ] && [ "$STANDARD_SET" -eq 0 ] && [ ! -f "$RULESET_FILE" ]; then
    echo "ERROR: $RULESET_FILE not found; pass --standard NAME (for example --standard PSR12)." >&2
    exit 2
  fi
done

# --- tool setup: pull runtime image, fetch PHARs into host cache ---
echo "runtime: $PHP_IMAGE"
docker image inspect "$PHP_IMAGE" >/dev/null 2>&1 \
  || docker pull "$PHP_IMAGE" >&2
mkdir -p "$TOOL_CACHE"
fetch_phar() { # $1=name $2=url
  if [ -f "$TOOL_CACHE/$1" ]; then return 0; fi
  echo "fetching $1 ..."
  curl -sSL -o "$TOOL_CACHE/$1" "$2"
  [ -s "$TOOL_CACHE/$1" ] || { echo "ERROR: download of $1 failed." >&2; exit 1; }
}
for _t in "${TOOLS_LIST[@]}"; do
  case "$_t" in
    phpstan) fetch_phar phpstan.phar "$PHPSTAN_URL" ;;
    psalm) fetch_phar psalm.phar "$PSALM_URL" ;;
    phpcs)
      fetch_phar phpcs.phar "$PHPCS_URL"
      [ "$FIX" -eq 1 ] && fetch_phar phpcbf.phar "$PHPCBF_URL"
      ;;
  esac
done
for _t in "${TOOLS_LIST[@]}"; do
  case "$_t" in
    phpstan) docker run --rm -v "$TOOL_CACHE:/tools:ro" "$PHP_IMAGE" php /tools/phpstan.phar --version ;;
    psalm) docker run --rm -v "$TOOL_CACHE:/tools:ro" "$PHP_IMAGE" php /tools/psalm.phar --version 2>&1 | head -n 1 ;;
    phpcs)
      if [ "$FIX" -eq 1 ]; then
        docker run --rm -v "$TOOL_CACHE:/tools:ro" "$PHP_IMAGE" php /tools/phpcbf.phar --version
      else
        docker run --rm -v "$TOOL_CACHE:/tools:ro" "$PHP_IMAGE" php /tools/phpcs.phar --version
      fi
      ;;
  esac
done

# --- run ---
echo "analyzing ${#PATHS[@]} path(s) under $SRC_DIR/ ..."
OVERALL="$RUFF_RC"
set +e
for _t in "${TOOLS_LIST[@]}"; do
  echo "==> $_t ..."
  _t0=$SECONDS
  case "$_t" in
    phpstan)
      # Progress bar on by default; -v adds tool verbose output.
      _extra=()
      [ "$VERBOSE" -eq 1 ] && _extra=(-v)
      docker run --rm -v "$APP_ROOT:/app" -w /app -v "$TOOL_CACHE:/tools:ro" \
        "$PHP_IMAGE" php /tools/phpstan.phar analyse \
        "${_extra[@]}" --level="$LEVEL" -- "${PATHS[@]}"
      ;;
    psalm)
      # Psalm needs a config file: generate a minimal one per run (outside
      # the repo) pointing at the analyzed tree inside the container.
      PSALM_CFG="$(mktemp -d)"
      _extra=()
      [ "$VERBOSE" -eq 1 ] && _extra=(--show-info=true)
      printf '%s\n' '<?xml version="1.0"?>' \
        '<psalm errorLevel="4" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' \
        '  xmlns="https://getpsalm.org/schema/config">' \
        '  <projectFiles>' \
        "    <directory name=\"/app/$SRC_DIR\" />" \
        '  </projectFiles>' \
        '</psalm>' > "$PSALM_CFG/psalm.xml"
      docker run --rm -v "$APP_ROOT:/app" -w /app -v "$TOOL_CACHE:/tools:ro" \
        -v "$PSALM_CFG:/psalmcfg:ro" \
        "$PHP_IMAGE" php /tools/psalm.phar --no-cache --config=/psalmcfg/psalm.xml \
        "${_extra[@]}" -- "${PATHS[@]}"
      ;;
    phpcs)
      PHPCS_NOTHING=0
      _phpcs_rc=0
      # Echo ruleset excludes before every phpcs/phpcbf run.
      if [ "$STANDARD" = "/app/phpcs.xml" ] && [ -f "$RULESET_FILE" ]; then
        echo "excludes (phpcs.xml):"
        ruleset_excludes | while IFS= read -r _x; do echo "  - $_x"; done
      fi
      # -p: progress dots by default; -v: per-file listing.
      _extra=(-p)
      [ "$VERBOSE" -eq 1 ] && _extra=(-p -v)
      if [ "$FIX" -eq 1 ]; then
        echo "fixing IN PLACE with phpcbf (review with git diff afterwards) ..."
        EFF_STANDARD="$STANDARD"
        EXTRA_MOUNT=()
        if [ "$FIX_PAGE" -eq 1 ]; then
          if [ "$STANDARD_SET" -eq 0 ]; then
            # Opt back in: temp copy of the ruleset minus the page excludes.
            RULESET_TMP="$(mktemp -d)"
            grep -v '<exclude-pattern>.*\(BasePage\|ArticlePage\)\.php' \
              "$RULESET_FILE" > "$RULESET_TMP/phpcs.xml"
            EFF_STANDARD="/ruleset/phpcs.xml"
            EXTRA_MOUNT=(-v "$RULESET_TMP:/ruleset:ro")
            echo "note: BasePage.php/ArticlePage.php INCLUDED via --fix-page."
          else
            echo "WARNING: custom --standard with --fix-page: that standard controls excludes." >&2
          fi
        fi
        echo "safe families first (never loop), then the full pass ..."
        phpcbf_paths --sniffs "$SAFE_SNIFFS" -- "${PATHS[@]}"
        phpcbf_paths -- "${PATHS[@]}"
        # Conflict fallback: if the full pass left failures (phpcbf prints
        # FAILED TO FIX and reverts the file when two families loop),
        # retry the still-failing files family by family.
        fix_fallback_groups "${PATHS[@]}"
        # Re-check with phpcs: phpcbf's own exit code only tells whether it
        # touched anything, not whether violations remain. The re-check's
        # exit code becomes this tool's result, and its FILE: lines feed
        # the $FAILED_TO_FIX_LOG/failed_to_fix_<timestamp>.log report
        # (unless opted out).
        echo "re-checking with phpcs (files still failing go to \$FAILED_TO_FIX_LOG/failed_to_fix_<timestamp>.log) ..."
        PHPCS_RECHECK_TMP="$(mktemp)"
        docker run --rm -v "$APP_ROOT:/app" -w /app -v "$TOOL_CACHE:/tools:ro" \
          "${EXTRA_MOUNT[@]}" \
          "$PHP_IMAGE" php /tools/phpcs.phar --standard="$EFF_STANDARD" \
          "${_extra[@]}" -- "${PATHS[@]}" 2>&1 | tee "$PHPCS_RECHECK_TMP"
        rc=${PIPESTATUS[0]}
        if [ "$NO_FAILURE_REPORT" -eq 1 ]; then
          echo "failure report skipped (--no-failure-report)."
        else
          FAILED_LIST="$(grep '^FILE:' "$PHPCS_RECHECK_TMP" 2>/dev/null \
            | sed 's/^FILE:[[:space:]]*//' | sed 's#^/app/##' | sort -u || true)"
          if [ -n "$FAILED_LIST" ]; then
            TS="$(date -u +%Y%m%d%H%M%S)"
            case "$FAILED_TO_FIX_LOG" in
              /*) FAIL_DIR="$FAILED_TO_FIX_LOG" ;;
              *) FAIL_DIR="$_START_DIR/$FAILED_TO_FIX_LOG" ;;
            esac
            mkdir -p "$FAIL_DIR" || { echo "ERROR: cannot create failure-report dir '$FAIL_DIR'." >&2; exit 1; }
            FAIL_LOG="$FAIL_DIR/failed_to_fix_${TS}.log"
            printf '%s\n' "$FAILED_LIST" > "$FAIL_LOG"
            N_FAILED="$(printf '%s\n' "$FAILED_LIST" | wc -l | tr -d ' ')"
            echo "failure report: $FAIL_LOG ($N_FAILED file(s) still failing)"
          else
            echo "no failure report: nothing still failing."
          fi
        fi
        if [ "$FIX_FAILED" -eq 1 ]; then
          save_failed_list "$PHPCS_RECHECK_TMP"
          echo "fixed files are not staged: review with git diff, then git add them."
        fi
        rm -f "$PHPCS_RECHECK_TMP"
      else
        # phpcs exits 1 with "No files were checked" when the ruleset
        # excludes every selected path (BasePage.php is excluded for byte
        # parity). That is a clean vacuous run, not a finding; keep real
        # phpcs failures fatal.
        PHPCS_OUT="$(mktemp)"
        docker run --rm -v "$APP_ROOT:/app" -w /app -v "$TOOL_CACHE:/tools:ro" \
          "$PHP_IMAGE" php /tools/phpcs.phar --standard="$STANDARD" \
          "${_extra[@]}" -- "${PATHS[@]}" 2>&1 | tee "$PHPCS_OUT"
        _phpcs_rc=${PIPESTATUS[0]}
        if [ "$_phpcs_rc" -ne 0 ] \
            && grep -q 'No files were checked' "$PHPCS_OUT" \
            && grep -q 'excluded or did not match' "$PHPCS_OUT"; then
          echo "note: all selected files are excluded by the ruleset; nothing to check."
          PHPCS_NOTHING=1
        fi
        # The commit check (--staged) keeps the paths of the failing files.
        [ "$STAGED" -eq 0 ] || save_failed_list "$PHPCS_OUT"
        rm -f "$PHPCS_OUT"
      fi
      ;;
  esac
  _case_rc=$?
  # The phpcs --fix branch sets rc itself from the phpcs re-check (phpcbf's
  # own exit code is meaningless) — keep it, ignore the rm/echo status.
  if [ "$_t" != "phpcs" ] || [ "$FIX" -eq 0 ]; then
    rc="$_case_rc"
  fi
  if [ "$_t" = "phpcs" ] && [ "$FIX" -eq 0 ]; then
    # The non-fix pipeline above ends with tee/rm, so its own status is not
    # phpcs's. An all-excluded selection is clean (see the branch comment).
    rc="$_phpcs_rc"
    [ "$PHPCS_NOTHING" -eq 1 ] && rc=0
  fi
  echo "  ($_t took $((SECONDS - _t0))s)"
  [ "$rc" -eq 0 ] || OVERALL=1
done
set -e
[ "$OVERALL" -eq 0 ] && echo "OK: all selected tools clean." || echo "DONE: findings above."
exit "$OVERALL"
