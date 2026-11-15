#!/usr/bin/env bash
# version 0.0.3
# Copy only the engine (.php/.js/.css trees + root .htaccess,
# sitemap.xml, robots.txt), forced by default.
# Thin wrapper around copy-engine.sh (same dir): force is ON unless
# --no-force is passed explicitly (later flags win, so an explicit
# --no-force after the injected --force still applies).
#
# Usage (run ON the server, where these paths exist):
#   ./copy-engine-force.sh [--no-force] [--duplicate-check full|size] [<source_dir> [<dest_dir>]]
#
#   With no dirs it copies the default dev public_html into the default
#   prod public_html. Without flags every in-scope file is copied,
#   overwriting existing ones; differing old DEST versions are saved into
#   backup_public_html first. Pass --no-force for the copy-only-new-files
#   behavior.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$SCRIPT_DIR/copy-engine.sh"
[ -f "$ENGINE" ] || { echo "ERROR: copy-engine.sh not found in $SCRIPT_DIR." >&2; exit 1; }

EXTRA=(--force)
for arg in "$@"; do
  case "$arg" in
    --no-force) EXTRA=(); break ;;
  esac
done

exec env \
  COPY_ENGINE_PROG="$0" \
  COPY_ENGINE_FORCE_DEFAULT=1 \
  "$ENGINE" "${EXTRA[@]}" "$@"
