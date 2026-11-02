#!/usr/bin/env bash
# version 0.3.1
# Upload the site to Dev unattended: forced, in small batches, with retries.
#
# Wrapper over deploy-ftp.sh for uploads that die halfway through a big
# tree. Lists the files once (deploy-ftp.sh --dry-run -v, so the usual
# junk excludes apply), then uploads them with --force in batches of
# --batch-size files: one deploy-ftp.sh run, one FTP session, per batch.
# A failed batch is rerun after a growing pause, up to --retries times; a
# batch that still fails goes to failed-files.txt and the run moves on,
# so one bad stretch of network does not end the night.
#
# Network outages: after a failed attempt the FTP host:port is probed (DNS
# lookup and TCP connect, no login). If it does not answer, the attempt is
# not counted as a retry: the wrapper waits for the server, probing every
# 30 s, then reruns the batch. A batch waits at most --outage-wait seconds
# in all; past that the run stops and prints a resume command.
#
# Progress: on a terminal a bar (batches done, percent, elapsed, ETA) stays
# on the last line under the batch lines; off a terminal (nohup, the
# backoffice job log) and in run.log it is a "progress:" line each time the
# percentage changes.
#
# Target: the creds file deploy-ftp.sh reads (default
# ~/.config/php_engine/ftp-creds.env, the Dev target). The host: and src: lines printed first show where files go.
#
# Usage:
#   ./deploy-batches.sh [--batch-size N] [--retries N] [--pause SEC]
#                       [--attempt-timeout SEC] [--outage-wait SEC]
#                       [--list FILE] [--start N]
#                       [--log-dir DIR] [--dry-run] [-h|--help]
#                       [deploy-ftp.sh flags...]
#
# Options:
#   --batch-size N         files per deploy-ftp.sh run (default 10)
#   --retries N            reruns of a failed batch (default 5, so up to 6
#                          attempts); rerun k waits k * --pause seconds
#   --pause SEC            base pause before a rerun (default 30)
#   --attempt-timeout SEC  stop an attempt that runs longer and count it as
#                          failed (default 1800)
#   --outage-wait SEC      how long one batch may wait for an FTP server that
#                          does not answer before the run stops (default
#                          3600); 0 counts such attempts like any failure
#   --list FILE            upload the src-relative paths in FILE, one per
#                          line, instead of listing the tree (e.g. the
#                          failed-files.txt of an earlier run)
#   --start N              begin at batch N; to resume an interrupted run,
#                          pair it with --list <that run>/files.txt (the
#                          interrupt message prints the exact command)
#   --log-dir DIR          new folder for files.txt, run.log and
#                          failed-files.txt (default
#                          ~/.cache/php_engine/deploy-batches/<date-time>)
#   --dry-run              list the files and count the batches, upload
#                          nothing
#   -h, --help             this help
#
# deploy-ftp.sh flags: --creds, --src, --remote-dir and --tls/--no-tls
# reach every batch; any other flag (--dirs, --files, --committed,
# --no-images, --include-*, ...) only shapes the file list and is refused
# with --list. --force is always on; --delete and --delete-removed are
# refused. run.log keeps the full per-file output of every attempt.
#
# Unattended, so a closed terminal does not stop it:
#   nohup ./deploy-batches.sh > ~/deploy-batches.out 2>&1 &
#   tail -f ~/deploy-batches.out
#
# Examples:
#   ./deploy-batches.sh --dry-run
#   ./deploy-batches.sh
#   ./deploy-batches.sh --dirs ru/qa --no-images --batch-size 20
#   ./deploy-batches.sh --list ~/.cache/php_engine/deploy-batches/20261024-230000/failed-files.txt
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd, for relative --list/--log-dir

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../..  # tools live in tools/deploy/; project root (compose file, src/) is the runtime cwd

BATCH_SIZE=10
RETRIES=5
PAUSE=30
ATTEMPT_TIMEOUT=1800
OUTAGE_WAIT=3600
OUTAGE_PROBE=30  # seconds between probes while the server does not answer
LIST_FILE=""
START=1
LOG_DIR=""
DRY_RUN=0
CONN_ARGS=()  # reach the listing and every batch
TUNING_ARGS=()  # --batch-size/--retries/--pause/--attempt-timeout/--outage-wait as given, repeated in the rerun hints
SEL_ARGS=()   # shape the file list only

usage() { sed -n '2,/^set -euo/p' "$SCRIPT_DIR/deploy-batches.sh" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

abs_path() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$_START_DIR" "$1" ;; esac; }

check_count() {  # option value minimum
  case "$2" in
    ''|*[!0-9]*|0[0-9]*) echo "ERROR: $1 needs a whole number, got '$2'." >&2; exit 2 ;;
  esac
  [ "$2" -ge "$3" ] || { echo "ERROR: $1 must be at least $3, got '$2'." >&2; exit 2; }
}

while [ $# -gt 0 ]; do
  case "$1" in
    --batch-size|--retries|--pause|--attempt-timeout|--outage-wait|--list|--start|--log-dir)
      [ $# -ge 2 ] || { echo "ERROR: $1 needs a value." >&2; exit 2; }
      case "$1" in
        --batch-size) check_count "$1" "$2" 1; BATCH_SIZE="$2"; TUNING_ARGS+=("$1" "$2") ;;
        --retries) check_count "$1" "$2" 0; RETRIES="$2"; TUNING_ARGS+=("$1" "$2") ;;
        --pause) check_count "$1" "$2" 0; PAUSE="$2"; TUNING_ARGS+=("$1" "$2") ;;
        --attempt-timeout) check_count "$1" "$2" 1; ATTEMPT_TIMEOUT="$2"; TUNING_ARGS+=("$1" "$2") ;;
        --outage-wait) check_count "$1" "$2" 0; OUTAGE_WAIT="$2"; TUNING_ARGS+=("$1" "$2") ;;
        --list) LIST_FILE="$(abs_path "$2")" ;;
        --start) check_count "$1" "$2" 1; START="$2" ;;
        --log-dir) LOG_DIR="$(abs_path "$2")" ;;
      esac
      shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) shift ;;  # always on
    --delete|--delete-removed|--delete-removed=*)
      echo "ERROR: ${1%%=*} is refused: this wrapper only uploads." >&2; exit 2 ;;
    --creds|--src|--remote-dir)
      [ $# -ge 2 ] || { echo "ERROR: $1 needs a value." >&2; exit 2; }
      CONN_ARGS+=("$1" "$2"); shift 2 ;;
    --creds=*|--src=*|--remote-dir=*|--tls|--no-tls) CONN_ARGS+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) SEL_ARGS+=("$1"); shift ;;
  esac
done

if [ -n "$LIST_FILE" ]; then
  [ "${#SEL_ARGS[@]}" -eq 0 ] || { echo "ERROR: --list is the selection, drop: ${SEL_ARGS[*]}" >&2; exit 2; }
  [ -s "$LIST_FILE" ] || { echo "ERROR: --list file '$LIST_FILE' is missing or empty." >&2; exit 1; }
fi
command -v timeout >/dev/null || { echo "ERROR: timeout (GNU coreutils) not found." >&2; exit 1; }

[ -n "$LOG_DIR" ] || LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/php_engine/deploy-batches/$(date +%Y%m%d-%H%M%S)"
[ ! -e "$LOG_DIR/run.log" ] || { echo "ERROR: $LOG_DIR already holds a run; pick a new --log-dir." >&2; exit 2; }
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run.log"
FILES="$LOG_DIR/files.txt"
FAILED="$LOG_DIR/failed-files.txt"
: > "$LOG"
: > "$FAILED"

TTY=0
[ ! -t 1 ] || TTY=1
BAR=""  # on a terminal: the progress bar, redrawn on the last line under each new line

say() {
  local line
  line="$(date '+%F %T') $*"
  printf '%s\n' "$line" >> "$LOG"
  if [ -n "$BAR" ]; then printf '\r\033[K%s\n%s' "$line" "$BAR"; else printf '%s\n' "$line"; fi
}

duration() {
  if [ "$1" -lt 60 ]; then printf '%ds' "$1"
  elif [ "$1" -lt 3600 ]; then printf '%dm %ds' $(($1 / 60)) $(($1 % 60))
  else printf '%dh %dm' $(($1 / 3600)) $(($1 % 3600 / 60)); fi
}

# Prints a command line that reruns this wrapper with the same tuning and
# connection flags (--start counts batches, so --batch-size must match).
say_command() { local s; s="$(printf '%q ' "$0" "$@" "${TUNING_ARGS[@]}" "${CONN_ARGS[@]}")"; say "  ${s% }"; }

# --- file list: the dry run doubles as a check of the flags and the target ---
if [ -n "$LIST_FILE" ]; then
  sed -e 's/\r$//' -e '/^[[:space:]]*$/d' "$LIST_FILE" > "$FILES"
  LISTING=(--files "./$(head -n 1 "$FILES")")  # one file is enough to show the target
  say "list: $LIST_FILE"
else
  LISTING=("${SEL_ARGS[@]}")
  say "listing files (deploy-ftp.sh --dry-run) ..."
fi
rc=0
./tools/deploy/deploy-ftp.sh --dry-run -v "${CONN_ARGS[@]}" "${LISTING[@]}" > "$LOG_DIR/listing.log" 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then
  tail -n 20 "$LOG_DIR/listing.log" >&2
  echo "ERROR: deploy-ftp.sh --dry-run failed (exit $rc), see $LOG_DIR/listing.log" >&2
  exit "$rc"
fi
grep -E '^(host|src):' "$LOG_DIR/listing.log" | while IFS= read -r line; do say "$line"; done || true
# host:port for the outage probe, from deploy-ftp.sh's "host:  name:port (tls=...)" line
hostport="$(sed -n 's/^host:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$LOG_DIR/listing.log" | head -n 1)"
FTP_HOST="${hostport%:*}"
FTP_PORT="${hostport##*:}"
if [ "$OUTAGE_WAIT" -gt 0 ] && { [ -z "$FTP_HOST" ] || [ "$FTP_HOST" = "$hostport" ]; }; then
  say "WARNING: no host:port in listing.log; failed attempts count as retries even in an outage"
  OUTAGE_WAIT=0
fi
[ -n "$LIST_FILE" ] || sed -n 's/^  would upload: //p' "$LOG_DIR/listing.log" > "$FILES"

mapfile -t LIST < "$FILES"
[ "${#LIST[@]}" -gt 0 ] || { say "nothing to upload."; exit 0; }
BATCHES=$(( (${#LIST[@]} + BATCH_SIZE - 1) / BATCH_SIZE ))
[ "$START" -le "$BATCHES" ] || { echo "ERROR: --start $START is past the last batch ($BATCHES)." >&2; exit 2; }
say "files: ${#LIST[@]} in $BATCHES batches of $BATCH_SIZE, from batch $START; retries per batch: $RETRIES"
say "log: $LOG_DIR"

if [ "$DRY_RUN" -eq 1 ]; then
  say "dry-run: nothing uploaded; batch $START would be:"
  printf '  %s\n' "${LIST[@]:$(( (START - 1) * BATCH_SIZE )):BATCH_SIZE}"
  exit 0
fi

# --- upload: one forced deploy-ftp.sh run per batch, rerun on failure ---
child=""
batch="$START"
print_resume() {
  say_command --list "$FILES" --start "$batch"
  [ ! -s "$FAILED" ] || say "batches that failed before it: $FAILED"
}
on_signal() {
  # timeout forwards TERM to its whole process group (deploy-ftp.sh + python3).
  [ -z "$child" ] || kill -TERM "$child" 2>/dev/null || true
  say "interrupted in batch $batch/$BATCHES; resume with:"
  print_resume
  exit 130
}
trap on_signal INT TERM HUP

# DNS lookup + TCP connect to the FTP port, no login: tells a dropped network
# or a server that is gone from a batch that failed on its own.
server_answers() {
  timeout 30 python3 -c 'import socket, sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=15).close()' \
    "$FTP_HOST" "$FTP_PORT" 2>/dev/null &
  child=$!
  local rc=0
  wait "$child" || rc=$?
  child=""
  return "$rc"
}

COLS=80
[ "$TTY" -eq 0 ] || COLS="$(tput cols 2>/dev/null || echo 80)"
LOOP_START=$SECONDS
logged_pct=-1
trap '[ -z "$BAR" ] || printf "\n"' EXIT  # leave the bar on its own line

# After each batch: the bar on a terminal is redrawn; run.log (and the output
# off a terminal) gets a progress line each time the percentage changes.
show_progress() {
  local pct=$((batch * 100 / BATCHES)) filled=$((batch * 30 / BATCHES)) elapsed=$((SECONDS - LOOP_START)) eta bar rest text
  eta=$((elapsed * (BATCHES - batch) / (batch - START + 1)))
  printf -v bar '%*s' "$filled" ''
  printf -v rest '%*s' $((30 - filled)) ''
  text="progress: [${bar// /#}${rest// /-}] $pct% $batch/$BATCHES batches, $(duration "$elapsed") elapsed, ETA $(duration "$eta"), $failed_batches failed"
  if [ "$pct" -ne "$logged_pct" ]; then
    logged_pct=$pct
    if [ "$TTY" -eq 1 ]; then printf '%s %s\n' "$(date '+%F %T')" "$text" >> "$LOG"; else say "$text"; fi
  fi
  if [ "$TTY" -eq 1 ]; then
    BAR="${text:0:$((COLS - 1))}"  # a wrapped line cannot be redrawn
    printf '\r\033[K%s' "$BAR"
  fi
}

# Probes every OUTAGE_PROBE seconds for up to $1 seconds; 0 once the server answers.
wait_for_server() {
  local start=$SECONDS step
  while [ $((SECONDS - start)) -lt "$1" ]; do
    step=$(( $1 - (SECONDS - start) ))
    [ "$step" -le "$OUTAGE_PROBE" ] || step=$OUTAGE_PROBE
    sleep "$step" &  # background + wait: a kill of this script alone ends the wait at once
    child=$!
    wait "$child" || true
    child=""
    server_answers && return 0
  done
  return 1
}

ok=0
failed_batches=0
for (( batch = START; batch <= BATCHES; batch++ )); do
  part=("${LIST[@]:$(( (batch - 1) * BATCH_SIZE )):BATCH_SIZE}")
  args=()
  for f in "${part[@]}"; do args+=("./$f"); done  # ./ keeps a name starting with - from reading as a flag
  attempt=1
  outage_left=$OUTAGE_WAIT  # seconds this batch may still wait for the server
  while :; do
    printf '\n=== %s batch %d/%d attempt %d/%d ===\n' \
      "$(date '+%F %T')" "$batch" "$BATCHES" "$attempt" "$((RETRIES + 1))" >> "$LOG"
    # Background + wait, so a signal reaches the trap without waiting for the batch.
    timeout "$ATTEMPT_TIMEOUT" ./tools/deploy/deploy-ftp.sh --force -v "${CONN_ARGS[@]}" --files "${args[@]}" >> "$LOG" 2>&1 &
    child=$!
    rc=0
    wait "$child" || rc=$?
    child=""
    if [ "$rc" -eq 0 ]; then
      ok=$((ok + 1))
      say "batch $batch/$BATCHES ok: ${#part[@]} files from ${part[0]}$([ "$attempt" -eq 1 ] || echo " (attempt $attempt)")"
      break
    fi
    [ "$rc" -ne 2 ] || { say "batch $batch/$BATCHES: deploy-ftp.sh rejected its arguments (exit 2), see $LOG"; exit 2; }
    why="exit $rc"
    [ "$rc" -ne 124 ] || why="no end after ${ATTEMPT_TIMEOUT}s"
    if [ "$outage_left" -gt 0 ] && ! server_answers; then
      say "batch $batch/$BATCHES attempt $attempt failed ($why) and $FTP_HOST:$FTP_PORT does not answer; waiting for it up to ${outage_left}s (not counted as a retry)"
      waited=$SECONDS
      if ! wait_for_server "$outage_left"; then
        say "$FTP_HOST:$FTP_PORT still does not answer after $((SECONDS - waited))s; stopped in batch $batch/$BATCHES, resume with:"
        print_resume
        exit 1
      fi
      outage_left=$(( outage_left - (SECONDS - waited) ))
      say "$FTP_HOST:$FTP_PORT answers again after $((SECONDS - waited))s; rerunning batch $batch/$BATCHES"
      continue
    fi
    if [ "$attempt" -gt "$RETRIES" ]; then
      failed_batches=$((failed_batches + 1))
      printf '%s\n' "${part[@]}" >> "$FAILED"
      say "batch $batch/$BATCHES FAILED after $attempt attempts ($why): ${#part[@]} files from ${part[0]}"
      break
    fi
    say "batch $batch/$BATCHES attempt $attempt failed ($why), retry in $((attempt * PAUSE))s"
    sleep "$((attempt * PAUSE))"
    attempt=$((attempt + 1))
  done
  show_progress
done
trap - INT TERM HUP
[ -z "$BAR" ] || { printf '\n'; BAR=""; }

say "done: $ok of $((BATCHES - START + 1)) batches uploaded, $failed_batches failed; log: $LOG_DIR"
if [ "$failed_batches" -gt 0 ]; then
  say "failed files: $FAILED; upload them again with:"
  say_command --list "$FAILED"
  exit 1
fi
