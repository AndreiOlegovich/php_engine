#!/usr/bin/env bash
# version 0.0.2
# Check (and optionally fix) the <?php opening header in .php files.
#
# Correct state: files starting with <?php begin with exactly
#   <?php<EOL><EOL>
# i.e. line 1 is exactly "<?php" (no trailing spaces/tabs), line 2 is
# exactly empty (no spaces), line 3+ is the code. Both LF and CRLF are
# accepted as EOL (but the two must match); the fix preserves the file's
# dominant EOL (CRLF if the file contains CRLF, else LF).
#
# Pure host-side check: no Docker, no installs. Read-only unless --fix
# is given.
#
# Usage:
#   ./check-php-header.sh [--src DIR] [--search] [--fix]
#                         [--all] [--staged] [--committed]
#                         [--files F ...] [paths...]
#
# Modes (default: --search; fixing needs an explicit flag):
#   --search             list .php files whose header differs from the
#                        correct state. Read-only. Exit 1 when such files
#                        exist.
#   --fix                rewrite wrong headers IN PLACE to <?php + one
#                        empty line — review with git diff afterwards.
#                        Prints what was fixed. When both --search and
#                        --fix are given, --fix wins.
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
# Classification per .php file (only files whose first bytes are [BOM]<?php
# are checked; the rest are skipped, never listed or fixed):
#   OK                 <?php<EOL><EOL> with no spaces and exactly one blank line
#   NO_NEWLINE         file is just "<?php" (+ spaces) with no newline at all
#   CODE_ON_LINE1      code on the same line: "<?php echo ..." / "<?php;..."
#   TRAILING_SPACE_LINE1  spaces/tabs between <?php and the first newline
#   MISSING_BLANK      no empty line after <?php ("<?php\ncode")
#   BLANK_HAS_SPACES   the empty line contains spaces/tabs
#   EXTRA_BLANKS       more than one empty line ("<?php\n\n\ncode")
#   MIXED_EOL          the two header EOLs differ ("<?php\r\n\ncode")
#   SKIP_NOT_PHP       does not start with <?php (HTML fragment, include
#                      snippet, ...) — skipped
#   SKIP_BINARY        contains NUL — skipped
#
# Examples:
#   ./check-php-header.sh --search
#   ./check-php-header.sh --search --staged
#   ./check-php-header.sh --fix --files ru/qa/article.php
#   ./check-php-header.sh --fix --all
#
# Exit: --search: 0 = all checked headers clean, 1 = wrong headers found
# (or usage failure: 2). --fix: 0 = all now clean, 1 = fix failed,
# 2 = usage failure.
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

# Classify one file; prints OK | SKIP_* | WRONG:<reasons>.
header_status() { # $1 = abs path
  perl -e '
    use strict; use warnings;
    my $f = $ARGV[0];
    open my $fh, "<:raw", $f or die "cannot open $f: $!";
    local $/; my $c = <$fh>; close $fh;
    $c = "" unless defined $c;
    if ($c =~ /\0/) { print "SKIP_BINARY\n"; exit 0; }
    $c =~ s/^\xEF\xBB\xBF//;
    if ($c !~ /^\Q<?php\E/) { print "SKIP_NOT_PHP\n"; exit 0; }
    if ($c =~ /^\Q<?php\E(\r\n|\n)\1(?!\r|\n)/) { print "OK\n"; exit 0; }
    my $after = substr($c, 5);
    if ($after eq "" || $after =~ /\A[ \t]+\z/) { print "WRONG:NO_NEWLINE\n"; exit 0; }
    if ($after =~ /\A[^ \t\r\n]/) { print "WRONG:CODE_ON_LINE1\n"; exit 0; }
    my @reasons;
    if ($after =~ /\A[ \t]+(?:\r\n|\n|\r)/) { push @reasons, "TRAILING_SPACE_LINE1"; }
    my $tmp = $after;
    $tmp =~ s/\A[ \t]*//;
    my $eol1 = ($tmp =~ s/\A(\r\n|\n|\r)//) ? $1 : "";
    if ($eol1 eq "") { print "WRONG:CODE_ON_LINE1\n"; exit 0; }
    if ($tmp eq "") {
      push @reasons, "MISSING_BLANK";
    } elsif ($tmp =~ /\A[ \t]*(?:\r\n|\n|\r)/) {
      if ($tmp =~ /\A[ \t]+(?:\r\n|\n|\r)/) { push @reasons, "BLANK_HAS_SPACES"; }
      my $t2 = $tmp; $t2 =~ s/\A[ \t]*//;
      my $eol2 = ($t2 =~ s/\A(\r\n|\n|\r)//) ? $1 : "";
      if ($eol2 ne "" && $eol1 ne $eol2) { push @reasons, "MIXED_EOL"; }
      if ($t2 =~ /\A[ \t]*(?:\r\n|\n|\r)/) { push @reasons, "EXTRA_BLANKS"; }
    } else {
      push @reasons, "MISSING_BLANK";
    }
    @reasons = ("MISSING_BLANK") unless @reasons;
    print "WRONG:" . join(",", @reasons) . "\n";
  ' "$1"
}

# Rewrite one wrong header in place; preserves BOM and dominant EOL.
fix_header() { # $1 = abs path
  perl -e '
    use strict; use warnings;
    my $f = $ARGV[0];
    open my $fh, "<:raw", $f or die "cannot open $f: $!";
    local $/; my $c = <$fh>; close $fh;
    $c = "" unless defined $c;
    my $bom = "";
    if ($c =~ s/^(\xEF\xBB\xBF)//) { $bom = $1; }
    my $eol = ($c =~ /\r\n/) ? "\r\n" : "\n";
    $c =~ s/^\Q<?php\E//;
    my $rest;
    if ($c =~ s/\A[ \t]*(\r\n|\n|\r)//) {
      $c =~ s/\A(?:[ \t]*(?:\r\n|\n|\r))*//;
      $rest = $c;
    } elsif ($c =~ s/\A[ \t]+//) {
      $rest = $c;
    } else {
      $rest = $c;
      $rest =~ s/\A[ \t]+//;
    }
    my $new = $bom . "<?php" . $eol . $eol . $rest;
    open my $out, ">:raw", $f or die "cannot write $f: $!";
    print $out $new; close $out;
  ' "$1"
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
echo "checking ${#RELS[@]} PHP file(s) under $SRC_DIR/ (want '<?php' + one empty line) ..."

CHECKED=0; WRONG=0; FIXED=0; FAILED=0; SKIP_PHP=0; SKIP_BIN=0
for rel in "${RELS[@]}"; do
  abs="$SRC_ABS/$rel"
  if [ -L "$abs" ]; then continue; fi  # never rewrite a symlink
  if [ ! -f "$abs" ]; then echo "WARNING: skipping '$rel' (not a regular file)." >&2; continue; fi
  if [ ! -r "$abs" ]; then echo "WARNING: skipping '$rel' (not readable)." >&2; continue; fi
  if [ ! -s "$abs" ]; then continue; fi  # empty: no header
  status="$(header_status "$abs")"
  case "$status" in
    OK) CHECKED=$((CHECKED + 1)) ;;
    SKIP_NOT_PHP) SKIP_PHP=$((SKIP_PHP + 1)) ;;
    SKIP_BINARY) SKIP_BIN=$((SKIP_BIN + 1)) ;;
    WRONG:*)
      reason="${status#WRONG:}"
      CHECKED=$((CHECKED + 1)); WRONG=$((WRONG + 1))
      if [ "$FIX" -eq 1 ]; then
        if fix_header "$abs"; then
          newstatus="$(header_status "$abs")"
          if [ "$newstatus" = "OK" ]; then
            FIXED=$((FIXED + 1))
            printf 'fixed: %s (%s)\n' "$rel" "$reason"
          else
            FAILED=$((FAILED + 1))
            printf 'FAILED: %s (was %s, now %s)\n' "$rel" "$reason" "$newstatus" >&2
          fi
        else
          FAILED=$((FAILED + 1))
          printf 'FAILED: %s (%s, fix error)\n' "$rel" "$reason" >&2
        fi
      else
        printf '%s: %s\n' "$rel" "$reason"
      fi
      ;;
    *) echo "WARNING: skipping '$rel' (unexpected status '$status')." >&2 ;;
  esac
done

echo "----"
if [ "$FIX" -eq 1 ]; then
  if [ "$FAILED" -gt 0 ]; then
    echo "RESULT: fixed $FIXED, FAILED $FAILED ($CHECKED checked; skipped: $SKIP_PHP non-<?php, $SKIP_BIN binary)." >&2
    exit 1
  elif [ "$WRONG" -eq 0 ]; then
    echo "OK: $CHECKED checked, all headers clean (skipped: $SKIP_PHP non-<?php, $SKIP_BIN binary)."
    exit 0
  else
    echo "OK: fixed $FIXED file(s) ($CHECKED checked; skipped: $SKIP_PHP non-<?php, $SKIP_BIN binary)."
    exit 0
  fi
else
  if [ "$WRONG" -eq 0 ]; then
    echo "OK: $CHECKED checked, all headers clean (skipped: $SKIP_PHP non-<?php, $SKIP_BIN binary)."
    exit 0
  else
    echo "RESULT: $WRONG of $CHECKED file(s) with wrong header (skipped: $SKIP_PHP non-<?php, $SKIP_BIN binary)."
    exit 1
  fi
fi
