#!/usr/bin/env bash
# Static analysis / code style for PHP via one-off Docker images.
#
# No host installs, no composer, no vendor/: tools run as versioned PHARs
# (cached in ~/.cache/aredel-tools) under the php:8.2-cli image.
# Tools: phpstan (bugs), psalm (bugs), phpcs (style).
#
# Usage:
#   ./lint-static.sh [--src DIR] [--tool phpstan|psalm|phpcs]... [--level N]
#                    [--standard NAME] [--all] [--staged] [--committed]
#                    [--files F ...] [paths...] [--report FILE] [--fix] [-v|--verbose] [-h|--help]
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
#   --level N       phpstan level 0-9 (default: 1; start low on legacy code).
#   --standard S    phpcs standard (default: ./phpcs.xml shared ruleset:
#                   PSR-12 plus project excludes).
#   --report FILE   also save the full output to FILE as a .txt report
#                   (".txt" is appended when missing; existing file is
#                   overwritten). Terminal output is unchanged.
#   --fix           auto-fix with phpcbf instead of just reporting with phpcs.
#                   Only valid together with --tool phpcs. Fixes the [x]
#                   violations IN PLACE — review with git diff afterwards.
#                   [ ] violations (e.g. camelCaps names) are never auto-fixed.
#                   Needs an explicit scope: paths/--files, --staged,
#                   --committed, non-default --src, or --all. Bare --fix
#                   (whole-tree default) is refused — pass --all to fix all.
#                   Files excluded in ./phpcs.xml stay excluded; the skip is
#                   echoed on every run (pass --fix-page to opt back in).
#   --fix-page      include BasePage.php/ArticlePage.php in --fix.
#                   Only valid together with --fix.
#   -v, --verbose   verbose tool output (per-file progress). Default shows
#                   a progress indicator plus per-tool timing.
#
# Examples:
#   ./lint-static.sh --files ru/qa/.php/BasePage.php
#   ./lint-static.sh --tool psalm --staged
#   ./lint-static.sh --tool phpstan,phpcs --level 2 --all
#
# Exit: 0 = all selected tools clean, 1 = findings (or setup failure: 1/2).
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # scripts live in scripts/; project root (compose file, src/) is the runtime cwd

SRC_DIR="src"
SRC_SET=0
TOOLS_LIST=()
LEVEL="1"
STANDARD="/app/phpcs.xml"
STANDARD_SET=0
REPORT=""
FIX=0
FIX_PAGE=0
VERBOSE=0
ALL=0; STAGED=0; COMMITTED=0
FILES_LIST=()
POSITIONAL=()

PHP_IMAGE="${ADEL_STATIC_PHP_IMAGE:-php:8.2-cli}"
TOOL_CACHE="${ADEL_STATIC_TOOL_CACHE:-$HOME/.cache/aredel-tools}"
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
    --fix) FIX=1; shift ;;
    --fix-page) FIX_PAGE=1; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

[ "${#TOOLS_LIST[@]}" -gt 0 ] || TOOLS_LIST=(phpstan)
# dedupe tools, keep order
DEDUP=()
for _t in "${TOOLS_LIST[@]}"; do
  skip=0
  for _d in "${DEDUP[@]}"; do [ "$_t" = "$_d" ] && skip=1; done
  [ "$skip" -eq 0 ] && DEDUP+=("$_t")
done
TOOLS_LIST=("${DEDUP[@]}")

if [ "$FIX" -eq 1 ]; then
  has_phpcs=0
  for _t in "${TOOLS_LIST[@]}"; do [ "$_t" = "phpcs" ] && has_phpcs=1; done
  [ "$has_phpcs" -eq 1 ] || { echo "ERROR: --fix needs --tool phpcs." >&2; exit 2; }
fi
if [ "$FIX_PAGE" -eq 1 ] && [ "$FIX" -eq 0 ]; then
  echo "ERROR: --fix-page needs --fix." >&2; exit 2
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
RULESET_FILE="$SCRIPT_DIR/../phpcs.xml"
RULESET_TMP=""
PSALM_CFG=""
trap 'rm -f "$REL_LIST"; for _d in $PSALM_CFG $RULESET_TMP; do [ -n "$_d" ] && rm -rf "$_d"; done' EXIT

# <exclude-pattern> values from the shared ruleset (for the per-run echo).
ruleset_excludes() {
  grep -o '<exclude-pattern>[^<]*</exclude-pattern>' "$RULESET_FILE" 2>/dev/null \
    | sed 's#.*<exclude-pattern>##; s#</exclude-pattern>.*##'
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
      container_php83/src/*) t="${t#container_php83/src/}" ;;
    esac
    case "$t" in
      /*)
        case "$t" in
          "$SRC_ABS"/*) t="${t#"$SRC_ABS"/}" ;;
          *) echo "WARNING: skipping '$tok' (outside $SRC_DIR; hint: drop the leading slash, e.g. 'ru/qa/pytest')." >&2; continue ;;
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
  TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--committed/--staged need git)." >&2; exit 1; }
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
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
echo "analyzing ${#PATHS[@]} path(s) under $SRC_DIR/ ..."
OVERALL=0
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
        docker run --rm -v "$APP_ROOT:/app" -w /app -v "$TOOL_CACHE:/tools:ro" \
          "${EXTRA_MOUNT[@]}" \
          "$PHP_IMAGE" php /tools/phpcbf.phar --standard="$EFF_STANDARD" \
          "${_extra[@]}" -- "${PATHS[@]}"
      else
        docker run --rm -v "$APP_ROOT:/app" -w /app -v "$TOOL_CACHE:/tools:ro" \
          "$PHP_IMAGE" php /tools/phpcs.phar --standard="$STANDARD" \
          "${_extra[@]}" -- "${PATHS[@]}"
      fi
      ;;
  esac
  rc=$?
  echo "  ($_t took $((SECONDS - _t0))s)"
  [ "$rc" -eq 0 ] || OVERALL=1
done
set -e
[ "$OVERALL" -eq 0 ] && echo "OK: all selected tools clean." || echo "DONE: findings above."
exit "$OVERALL"
