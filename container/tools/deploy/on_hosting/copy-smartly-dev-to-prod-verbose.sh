#!/usr/bin/env bash
# version 0.2.0
# Verbose smart copy: sync only changed dirs from dev to prod, with shadow
# backup. Same engine as copy-smartly-dev-to-prod.sh,
# but loud by default and logging everything to a nearby log file.
#
# Flow:
#   1. find-dirs-with-changes.sh (same dir) lists changed dirs at
#      --max-depth (first run: everything is new).
#   2. Each listed dir is copied whole with copy-with-shadow-backup.sh
#      -f --duplicate-check full: size short-circuit first, then full
#      content comparison, so real changes are caught even when the
#      file size is the same. Overwritten prod files are backed up
#      first (see that script); new files are copied; identical files
#      are skipped; nothing is ever deleted ([gone]/[stale] only
#      reported).
#   3. On success the scan from step 1 becomes the new snapshot, so a
#      file that changes on dev during the copy is caught next run.
#
# Usage (run ON the server, where these paths exist):
#   ./copy-smartly-dev-to-prod-verbose.sh [--max-depth N]
#       [--state-file FILE] [--log-file FILE] [--dry-run] [-q|--quiet]
#       [-v|--verbose]
#
#   --max-depth N    directory level reported by the finder
#                    (default: 4; 1 -> ru ubuntu dev,
#                    2 -> section ru/code ubuntu/network section).
#   --state-file     snapshot file (default: .smart_copy.state next to
#                    this script; SMART_STATE_FILE env wins over default).
#                    Shared with the quiet variant: both read and write it.
#   --log-file FILE  run log (default: copy_smartly_verbose.log next to
#                    this script; SMART_VERBOSE_LOG env wins over default).
#                    Appended to every run with a timestamp header.
#   --dry-run        list changed dirs only: copy nothing, save nothing
#                    (still logged).
#   -q|--quiet       hide the engine's per-file [skip] lines (one-shot;
#                    SMART_VERBOSE=0 does the same persistently).
#   -v|--verbose     no-op, kept for compatibility (verbose is default).
#
# Output verbosity: VERBOSE=1 shows every engine line; -q/--quiet (or
# SMART_VERBOSE=0) hides per-file [skip] lines. Everything printed also
# lands in the log file via tee.
#
# Both paths include public_html on purpose. Paths default from site.env
# (SITE_ENV) when set; built-ins below otherwise. See site.env.example.
set -euo pipefail
export LC_ALL=C

if [ -n "${SITE_ENV:-}" ]; then
  [ -f "$SITE_ENV" ] || { echo "ERROR: SITE_ENV file '$SITE_ENV' not found." >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAXDEPTH="${SMART_MAXDEPTH:-4}"
STATE_FILE="${SMART_STATE_FILE:-$SCRIPT_DIR/.smart_copy.state}"
LOG_FILE="${SMART_VERBOSE_LOG:-$SCRIPT_DIR/copy_smartly_verbose.log}"
DRY_RUN=0
VERBOSE="${SMART_VERBOSE:-1}"
ARGS=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --max-depth=*) MAXDEPTH="${1#--max-depth=}"; shift ;;
    --max-depth) [ $# -ge 2 ] || { echo "ERROR: --max-depth needs a value." >&2; exit 1; }; MAXDEPTH="$2"; shift 2 ;;
    --state-file=*) STATE_FILE="${1#--state-file=}"; shift ;;
    --state-file) [ $# -ge 2 ] || { echo "ERROR: --state-file needs a value." >&2; exit 1; }; STATE_FILE="$2"; shift 2 ;;
    --log-file=*) LOG_FILE="${1#--log-file=}"; shift ;;
    --log-file) [ $# -ge 2 ] || { echo "ERROR: --log-file needs a value." >&2; exit 1; }; LOG_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -q|--quiet) VERBOSE=0; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
      exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 1 ;;
    *) echo "ERROR: unexpected argument: $1 (see --help)." >&2; exit 1 ;;
  esac
done
case "$MAXDEPTH" in
  ''|*[!0-9]*) echo "ERROR: --max-depth must be a non-negative integer." >&2; exit 1 ;;
esac
case "$VERBOSE" in
  0|1) ;;
  *) echo "ERROR: SMART_VERBOSE must be 0 or 1." >&2; exit 1 ;;
esac

# Single instance: a second run would interleave copies and snapshots.
if [ "${FLOCKED:-}" != 1 ]; then
  if ! flock -n "$SCRIPT_DIR/.smart_copy.lock" true 2>/dev/null; then
    echo "Another smart copy is already running, exiting." >&2
    exit 1
  fi
  exec env FLOCKED=1 flock -n "$SCRIPT_DIR/.smart_copy.lock" "$0" "${ARGS[@]}"
fi

SRC="${DEV_DOCROOT:-/home/user/dev/public_html}"
DEST="${PROD_DOCROOT:-/home/user/example.com/public_html}"
[ -d "$SRC" ] || { echo "ERROR: source '$SRC' is not a directory." >&2; exit 1; }
SRC="$(cd "$SRC" && pwd)"

FINDER=""
for d in "$SCRIPT_DIR" "$SCRIPT_DIR/.."; do
  if [ -x "$d/find-dirs-with-changes.sh" ]; then
    FINDER="$d/find-dirs-with-changes.sh"
    break
  fi
done
[ -n "$FINDER" ] || { echo "ERROR: find-dirs-with-changes.sh not found in $SCRIPT_DIR or its parent." >&2; exit 1; }
ENGINE=""
for d in "$SCRIPT_DIR" "$SCRIPT_DIR/.."; do
  if [ -x "$d/copy-with-shadow-backup.sh" ]; then
    ENGINE="$d/copy-with-shadow-backup.sh"
    break
  fi
done
[ -n "$ENGINE" ] || { echo "ERROR: copy-with-shadow-backup.sh not found in $SCRIPT_DIR or its parent." >&2; exit 1; }

# Log everything from here on (console keeps working via tee).
touch "$LOG_FILE" || { echo "ERROR: cannot write log file '$LOG_FILE'." >&2; exit 1; }
exec > >(tee -a "$LOG_FILE") 2>&1
START_SECONDS=$SECONDS
echo "===== smart copy (verbose) run at $(date '+%F %T') ($0 ${ARGS[*]}) ====="
echo "Config:"
echo "  SRC=$SRC"
echo "  DEST=$DEST"
echo "  STATE_FILE=$STATE_FILE"
echo "  LOG_FILE=$LOG_FILE"
echo "  MAXDEPTH=$MAXDEPTH"
if [ -n "${SITE_ENV:-}" ]; then
  echo "  SITE_ENV=$SITE_ENV (values above come from it)"
else
  echo "  SITE_ENV=(unset; built-in paths above)"
fi

echo "[$(date '+%F %T')] Phase 1: finding changed dirs (max depth $MAXDEPTH) ..."
# The scan below becomes the snapshot only after a successful copy; a
# rescan at the end would mark files that changed on dev meanwhile as
# seen without copying them.
PENDING="$STATE_FILE.pending.$$"
trap 'rm -f -- "$PENDING"' EXIT
# The finder's exit status must be checked explicitly: mapfile swallows it,
# and an empty list would otherwise be misread as "nothing changed" while
# the snapshot below advances past uncopied work.
finder_out=""
if ! finder_out="$("$FINDER" --max-depth "$MAXDEPTH" --state-file "$STATE_FILE" --snapshot-out "$PENDING" "$SRC")"; then
  echo "[ERROR] change scan failed; copying nothing, snapshot untouched." >&2
  exit 1
fi
mapfile -t DIRS <<< "$finder_out"
# mapfile keeps one empty element for empty input; drop it so counts,
# loops, and the empty-tree branch below all see the real list.
picked=()
for t in "${DIRS[@]}"; do [ -n "$t" ] && picked+=("$t"); done
DIRS=()
for t in ${picked[@]+"${picked[@]}"}; do DIRS+=("$t"); done
unset picked
echo "Changed directories: ${#DIRS[@]}."
for t in "${DIRS[@]}"; do echo "  [changed] $t"; done
# Total dirs scanned, so the log also answers "why wasn't X copied":
# anything not listed above matched the snapshot and was not visited.
# The count itself is best-effort: a failed find must never abort the run.
total_dirs=$(find "$SRC" -mindepth 0 -type d 2>/dev/null | wc -l) || total_dirs="?"
unchanged="unknown"
if [ "$total_dirs" -gt 0 ] 2>/dev/null; then
  unchanged=$((total_dirs - ${#DIRS[@]}))
fi
echo "Scanned $total_dirs directories, ${#DIRS[@]} changed, $unchanged unchanged (not visited)."

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run: nothing copied, snapshot untouched (elapsed $((SECONDS - START_SECONDS))s)."
  exit 0
fi
if [ "${#DIRS[@]}" -eq 0 ]; then
  mv -f -- "$PENDING" "$STATE_FILE"
  echo "Nothing to copy. Snapshot saved (elapsed $((SECONDS - START_SECONDS))s)."
  exit 0
fi

echo "[$(date '+%F %T')] Phase 2: copying ..."
UPDATED=()
FAILED=0
for t in "${DIRS[@]}"; do
  [ -n "$t" ] || continue
  if [ "$t" = "." ]; then
    s="$SRC"; df="$DEST"
  else
    s="$SRC/$t"; df="$DEST/$t"
  fi
  echo "--- [$t] ---"
  echo "  run: copy-with-shadow-backup.sh -f --duplicate-check full"
  echo "    src=$s"
  echo "    dst=$df"
  # stale dest files (report only, never deleted)
  if [ -d "$df" ]; then
    while IFS= read -r -d '' df2; do
      r="${df2#"$df"/}"
      [ -e "$s/$r" ] || [ -L "$s/$r" ] || echo "[stale] $t/$r (in prod, missing from dev; not deleted)"
    done < <(find "$df" -mindepth 1 -type f -print0 2>/dev/null)
  fi
  # Verbose by default: show every engine line unless -q/--quiet
  # (or SMART_VERBOSE=0). The grep group always exits 0 (|| true), so
  # with pipefail the pipeline status still reflects the engine: a
  # failed engine is detected via PIPESTATUS, not masked.
  engine_status=0
  if [ "$VERBOSE" -eq 1 ]; then
    "$ENGINE" -f --duplicate-check full "$s" "$df" || engine_status=$?
  else
    "$ENGINE" -f --duplicate-check full "$s" "$df" 2>&1 | { grep -v '^\[skip\] ' || true; } || engine_status=${PIPESTATUS[0]}
  fi
  if [ "$engine_status" -eq 0 ]; then
    UPDATED+=("$t")
  else
    echo "[ERROR] copy failed for $t, snapshot NOT saved." >&2
    FAILED=1
  fi
done

if [ "$FAILED" -eq 0 ]; then
  mv -f -- "$PENDING" "$STATE_FILE"
  echo "Snapshot saved: $STATE_FILE."
else
  echo "Failures occurred; snapshot left untouched (next run retries everything)." >&2
fi

# --- final summary: which directories were updated ---
echo "=========================================="
echo "Directories updated: ${#UPDATED[@]}"
for t in "${UPDATED[@]}"; do
  if [ "$t" = "." ]; then
    echo "  / (site root)"
  else
    echo "  /$t"
  fi
done
echo "Snapshot: $STATE_FILE"
echo "Log saved: $LOG_FILE"
[ "$FAILED" -eq 0 ] || exit 1
echo "[$(date '+%F %T')] Done (elapsed $((SECONDS - START_SECONDS))s)."
