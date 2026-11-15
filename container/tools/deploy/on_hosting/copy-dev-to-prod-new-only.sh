#!/usr/bin/env bash
# version 0.0.1
# Copy new and updated files from the dev site tree into the prod site
# tree, with shadow backup. Thin wrapper: fixed source/destination.
#
# Usage (run ON the server, where these paths exist):
#   ./copy-dev-to-prod-new-only.sh
#
#   Always behaves as if -f/--force was given, with full content check:
#   - missing in prod -> copy
#   - differs in prod -> backup old prod version, then overwrite
#   - identical      -> skip
#   Change detection is size first (cheap short-circuit), then full
#   content comparison, so real changes are caught even when the file
#   size is the same. A --duplicate-check flag passed on the command
#   line is ignored: this variant always checks content, never size only.
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

# Always force with full content check; drop any user-passed -f/--force
# or --duplicate-check flags (this variant's semantics are fixed).
ARGS=()
SKIP_NEXT=0
for arg in "$@"; do
  if [ "$SKIP_NEXT" -eq 1 ]; then
    SKIP_NEXT=0
    continue
  fi
  case "$arg" in
    -f|--force) ;;
    --duplicate-check) SKIP_NEXT=1 ;;
    --duplicate-check=*) ;;
    *) ARGS+=("$arg") ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/copy-with-shadow-backup.sh" -f --duplicate-check full "${ARGS[@]}" \
  "${DEV_DOCROOT:-/home/user/dev/public_html}" \
  "${PROD_DOCROOT:-/home/user/example.com/public_html}"
