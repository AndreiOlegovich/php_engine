#!/usr/bin/env bash
# Copy the dev site tree into the prod site tree with shadow backup.
# Thin wrapper: fixed source/destination, all flags forwarded.
#
# Usage (run ON the server, where these paths exist):
#   ./copy-dev-to-prod.sh [-f|--force] [--duplicate-check full|size]
#
#   No flags: copy only new files/dirs, never overwrite.
#   -f:       overwrite differing files, backing up old DEST versions first.
#   --duplicate-check full|size: forwarded to copy_with_shadow_backup.sh
#             (full = size + content, default; size = size only, faster but
#             same-size edits are missed).
#
# The -f flag can also come from the environment:
#   USE_FORCE="true" ./copy-dev-to-prod.sh
# USE_FORCE defaults to "false" (any value other than 1/true/yes means off).
# An explicit -f/--force CLI flag always wins over USE_FORCE.
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

USE_FORCE="${USE_FORCE:-false}"
FORCE_FLAG=()
case "${USE_FORCE,,}" in
  1|true|yes) FORCE_FLAG=(-f) ;;
esac
for arg in "$@"; do
  case "$arg" in
    -f|--force) FORCE_FLAG=(-f); break ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/copy-with-shadow-backup.sh" "${FORCE_FLAG[@]}" "$@" \
  "${DEV_DOCROOT:-/home/user/dev/public_html}" \
  "${PROD_DOCROOT:-/home/user/example.com/public_html}"
