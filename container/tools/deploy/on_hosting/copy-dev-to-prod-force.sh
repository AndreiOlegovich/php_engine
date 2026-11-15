#!/usr/bin/env bash
# version 0.0.1
# Copy the dev site tree into the prod site tree with shadow backup.
# Force variant: ALWAYS overwrites differing files (backs up old DEST
# versions first). Thin wrapper: fixed source/destination, remaining
# flags forwarded.
#
# Usage (run ON the server, where these paths exist):
#   ./copy-dev-to-prod-force.sh [--duplicate-check full|size]
#
#   Always behaves as if -f/--force was given: existing files that differ
#   are overwritten, old DEST versions saved into backup_public_html.
#   --duplicate-check full|size: forwarded to copy-with-shadow-backup.sh
#             (full = size + content, default; size = size only, faster but
#             same-size edits are missed).
#
# Both paths include public_html on purpose: the base script copies the
# CONTENTS of source into dest, and it locates the backup dir by walking
# up from dest to the public_html ancestor. Paths default from site.env
# (SITE_ENV) when set; built-ins below otherwise. See site.env.example.
set -euo pipefail

if [ -n "${SITE_ENV:-}" ]; then
  [ -f "$SITE_ENV" ] || { echo "ERROR: SITE_ENV file '$SITE_ENV' not found." >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
fi

# Always force; drop any user-passed -f/--force (already implied).
ARGS=()
for arg in "$@"; do
  case "$arg" in
    -f|--force) ;;
    *) ARGS+=("$arg") ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/copy-with-shadow-backup.sh" -f "${ARGS[@]}" \
  "${DEV_DOCROOT:-/home/user/dev/public_html}" \
  "${PROD_DOCROOT:-/home/user/example.com/public_html}"
