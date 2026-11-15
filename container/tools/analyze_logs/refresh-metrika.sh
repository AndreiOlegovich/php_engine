#!/usr/bin/env bash
# version 0.1.0
# Refresh the prod-log pipeline: fetch -> bots -> metrika DB.
#
# This is what cron runs every 30 minutes:
#
#   */30 * * * * /path/to/checkout/container/tools/analyze_logs/refresh-metrika.sh >> ~/.local/share/php_engine/logs/refresh-metrika.cron.log 2>&1
#
# Logs live outside the repository in
# ${SITE_LOG_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/php_engine/logs};
# metrika.db and users.db stay beside the scripts; botlist.csv lives in
# the log root too, because it holds client IP addresses.
#
# Steps (each standalone-runnable, failures stop the chain except where
# noted):
#   1. fetch-logs.sh   prod error + access logs, skipped quietly offline
#   2. fetch-devices.sh  owner device profiles from dev, then
#      fetch-user-profiles.sh  the prod profile records staged on dev,
#      which carry the IPs the prod access logs show (both best effort:
#      warn and continue when offline or nothing recorded yet)
#   3. build_users_db.py unique devices with exclude_from_metrika flags
#   4. check_bots.py   rebuild botlist.csv from all access logs
#   5. build_metrika_db.py  rebuild metrika.db (bots, owner devices from
#      users.db and static excluded)
#
# One run at a time: a run that finds another one holding
# $SITE_LOG_DIR/refresh-metrika.lock exits 0 without doing anything, so
# a slow cron run and a backoffice run never write the same files at once.
# Steps 3-5 build each file under a temporary name and rename it into
# place, so readers never see a half-written botlist.csv or database.
#
# Usage:
#   ./refresh-metrika.sh [--skip-fetch] [--skip-devices] [--skip-users]
#                        [--skip-bots] [--skip-db] [--from-local]
#                        [--dry-run-fetch] [-v]
#
#   --from-local  shorthand for --skip-fetch --skip-devices: rebuild
#                 users/bots/metrika from logs already downloaded.
set -euo pipefail

_START_DIR="$(pwd -P)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SKIP_FETCH=0
SKIP_DEVICES=0
SKIP_USERS=0
SKIP_BOTS=0
SKIP_DB=0
FETCH_ARGS=()

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-fetch) SKIP_FETCH=1; shift ;;
    --skip-devices) SKIP_DEVICES=1; shift ;;
    --skip-users) SKIP_USERS=1; shift ;;
    --skip-bots) SKIP_BOTS=1; shift ;;
    --skip-db) SKIP_DB=1; shift ;;
    --from-local) SKIP_FETCH=1; SKIP_DEVICES=1; shift ;;
    --dry-run-fetch) FETCH_ARGS+=(--dry-run); shift ;;
    -v|--verbose) FETCH_ARGS+=(-v); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
  esac
done

# shellcheck disable=SC1091  # shared resolver is checked separately by lint-sh.sh
. "$SCRIPT_DIR/log-root.sh"
mkdir -p "$SITE_LOG_DIR"
LOCK="$SITE_LOG_DIR/refresh-metrika.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] refresh-metrika: another run holds $LOCK, skipping"
  exit 0
fi

echo "[$(date '+%F %T')] refresh-metrika: start"
if [ "$SKIP_FETCH" -eq 0 ]; then
  "$SCRIPT_DIR/fetch-logs.sh" "${FETCH_ARGS[@]}"
else
  echo "fetch: skipped by flag"
fi
if [ "$SKIP_DEVICES" -eq 0 ]; then
  "$SCRIPT_DIR/fetch-devices.sh" "${FETCH_ARGS[@]}" \
    || echo "devices: fetch failed, continuing without fresh profiles"
  "$SCRIPT_DIR/fetch-user-profiles.sh" "${FETCH_ARGS[@]}" \
    || echo "user profiles: fetch failed, continuing without fresh prod profiles"
else
  echo "devices: skipped by flag"
fi
if [ "$SKIP_USERS" -eq 0 ]; then
  python3 "$SCRIPT_DIR/build_users_db.py"
else
  echo "users: skipped by flag"
fi
if [ "$SKIP_BOTS" -eq 0 ]; then
  python3 "$SCRIPT_DIR/check_bots.py"
else
  echo "bots: skipped by flag"
fi
if [ "$SKIP_DB" -eq 0 ]; then
  python3 "$SCRIPT_DIR/build_metrika_db.py" --top 5
else
  echo "db: skipped by flag"
fi
echo "[$(date '+%F %T')] refresh-metrika: done"
