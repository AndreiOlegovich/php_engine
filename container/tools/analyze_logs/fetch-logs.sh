#!/usr/bin/env bash
# version 0.0.5
# Fetch prod logs every 30 minutes (cron) — skips quietly without internet.
#
# Single instance via flock; downloads only prod (error + access) with
# --latest 2 so each run refreshes the two live log files. Downstream
# chain (devices -> users -> bots -> metrika DB) runs in refresh-metrika.sh, which
# is what cron should call:
#
#   */30 * * * * /path/to/checkout/container/tools/analyze_logs/refresh-metrika.sh >> ~/.local/share/<project>/logs/refresh-metrika.cron.log 2>&1
#
# Run directly for one fetch:
#   ./fetch-logs.sh [--creds PATH] [--dest DIR] [--dry-run] [-v]
#
# Logs go to ${SITE_LOG_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/<project>/logs}
# unless --dest is given.
#
# Exit 0 when downloaded OR skipped offline; 1 on real errors.
set -euo pipefail

_START_DIR="$(pwd -P)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091  # shared resolver is checked separately by lint-sh.sh
. "$SCRIPT_DIR/log-root.sh"

CREDS="${HOME}/.config/${SITE_PROJECT}/hosting-access.env"
DEST=""
DRY_RUN=0
VERBOSE=0

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --creds) [ $# -ge 2 ] || { echo "ERROR: --creds needs a value." >&2; exit 2; }
      CREDS="$2"; shift 2 ;;
    --creds=*) CREDS="${1#--creds=}"; shift ;;
    --dest) [ $# -ge 2 ] || { echo "ERROR: --dest needs a value." >&2; exit 2; }
      DEST="$2"; shift 2 ;;
    --dest=*) DEST="${1#--dest=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
  esac
done

# Single instance: a slow FTP run must not overlap the next cron tick.
# (After option parsing so --help stays side-effect free.)
[ "${FLOCKED:-}" = 1 ] || exec env FLOCKED=1 flock -n "$SCRIPT_DIR/.fetch-logs.lock" "$0" "$@"

[ -f "$CREDS" ] || { echo "ERROR: creds file '$CREDS' not found." >&2; exit 1; }

# shellcheck disable=SC1090
. "$CREDS" >/dev/null 2>&1 || { echo "ERROR: cannot read creds file." >&2; exit 1; }
FTP_HOST="${FTP_HOST:-}"
FTP_PORT="${FTP_PORT:-21}"
[ -n "$FTP_HOST" ] || { echo "ERROR: FTP_HOST is empty in creds file." >&2; exit 1; }

echo "[$(date '+%F %T')] fetch-logs: checking $FTP_HOST:$FTP_PORT"
if ! timeout 15 bash -c "</dev/tcp/$FTP_HOST/$FTP_PORT" 2>/dev/null; then
  echo "[$(date '+%F %T')] fetch-logs: no connection, skipping (offline?)"
  exit 0
fi

ARGS=(--only prod --latest 2 --creds "$CREDS")
[ -n "$DEST" ] && ARGS+=(--dest "$DEST")
[ "$DRY_RUN" -eq 1 ] && ARGS+=(--dry-run)
[ "$VERBOSE" -eq 1 ] && ARGS+=(-v)

exec "$SCRIPT_DIR/download-logs.sh" "${ARGS[@]}"
