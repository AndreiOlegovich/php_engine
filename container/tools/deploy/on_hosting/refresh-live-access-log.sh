#!/usr/bin/env bash
# version 0.0.1
# Refresh the staged live access log: delete the copy, then copy it fresh
# from the prod site tree. Deleting first (instead of overwriting) drops
# any stale ownership or permissions on the staged file, so the fresh copy
# inherits the current user.
#
# Usage (run ON the server, where these paths exist):
#   ./refresh-live-access-log.sh
#
# Cron (a few minutes after the dated-log copy at 1:01):
#   5 1 * * * /home/user/dev/tools/refresh-live-access-log.sh
#
# Env overrides: SRC_FILE (default below), DST_FILE (default below).
set -euo pipefail

SRC_FILE="${SRC_FILE:-/home/user/example.com/example.com.access.log}"
DST_FILE="${DST_FILE:-/home/user/dev/prod_logs/access/example.com.access.log}"

[ -f "$SRC_FILE" ] || { echo "ERROR: source '$SRC_FILE' not found." >&2; exit 1; }
mkdir -p "$(dirname "$DST_FILE")"
if [ -f "$DST_FILE" ]; then
  rm "$DST_FILE"
  echo "deleted: $DST_FILE"
else
  echo "absent, nothing to delete: $DST_FILE"
fi
cp "$SRC_FILE" "$DST_FILE"
echo "refreshed: $DST_FILE ($(wc -c < "$DST_FILE") bytes)"
