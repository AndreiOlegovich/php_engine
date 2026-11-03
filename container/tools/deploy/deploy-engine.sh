#!/usr/bin/env bash
# version 0.3.1
# Deploy the PHP engine framework + stylesheets to hosting (limited scope, forced).
#
# Thin wrapper over deploy-ftp.sh: uploads ONLY the selected engine dirs
# and the stylesheets (.css), with --force (re-upload every file even if
# the remote copy has the same size). Use when prod serves stale code:
# the default same-size skip is exactly what leaves old framework files
# on the server.
#
# Scope (edit the WITH_* variables below to change the default):
#   --engine src/.php          (WITH_ENGINE; --core is an alias)
#   .css is always included while WITH_CSS=1 (--no-css to disable).
#   Default (no flags): the engine tree + css.
#
# Forced mode is the default (every file is re-uploaded even if the
# remote copy has the same size). Pass --no-force for a normal run
# where same-size files are skipped as unchanged.
#
# Notes:
#   - src/.css used to be a symlink to ao/.css and is now an independent
#     real directory; plain `find` descends into it normally. The link
#     resolution below is kept for any other symlinked scope dir.
#   - This still does NOT clear PHP OPcache on the hosting. If pages keep
#     rendering old markup after the upload, restart php-fpm/apache on the
#     server (or call opcache_reset()); prod ignores changed files while
#     opcache.validate_timestamps is Off.
#   - --delete is refused (deploy-ftp.sh forbids it with any selection).
#
# Usage:
#   ./deploy-engine.sh [--engine|--core] [--no-engine] [--no-css]
#                      [--no-force] [--dry-run] [-v|--verbose] [--creds PATH]
#                      [--remote-dir DIR] [--tls|--no-tls] [--lint]
#                      [other deploy-ftp.sh flags...]
#
# Examples:
#   ./deploy-engine.sh --dry-run
#   ./deploy-engine.sh --engine -v
#   ./deploy-engine.sh --no-css --dry-run
#   ./deploy-engine.sh --no-force --dry-run   # same-size files skipped
set -euo pipefail

# --- default scope (1 = include, 0 = skip) ---
# The --engine/--core and --no-engine flags override these: when at
# least one is given, only the selected parts are uploaded.
WITH_ENGINE=1
WITH_CSS=1
# Forced re-upload is the default; --no-force turns it off.
FORCE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../..  # tools live in tools/deploy/; project root (compose file, src/) is the runtime cwd

usage() { sed -n '2,/^set -euo/p' "$SCRIPT_DIR/deploy-engine.sh" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

ENGINE_DIRS=(.php)
PASSTHROUGH=()

while [ $# -gt 0 ]; do
  case "$1" in
    --engine|--core) WITH_ENGINE=1; shift ;;
    --no-engine) WITH_ENGINE=0; shift ;;
    --no-css) WITH_CSS=0; shift ;;
    --no-force) FORCE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) PASSTHROUGH+=("$1"); shift ;;
  esac
done
FILES_ARGS=()
if [ "$WITH_ENGINE" -eq 1 ]; then
  FILES_ARGS+=("${ENGINE_DIRS[0]}")
fi
if [ "$WITH_CSS" -eq 1 ]; then FILES_ARGS+=(.css); fi

# Resolve symlinked scope dirs (e.g. src/.php): plain find(1)
# lists zero files for a symlinked --files dir.
RESOLVED=()
for d in "${FILES_ARGS[@]}"; do
  if [ ! -e "src/$d" ]; then
    echo "WARNING: skipping '$d' (not found under src/)." >&2
    continue
  fi
  if [ -L "src/$d" ]; then
    target="$(readlink "src/$d")"
    case "$target" in
      /*) echo "WARNING: skipping '$d' (symlink points outside src: $target)." >&2; continue ;;
    esac
    target="${target#./}"
    RESOLVED+=("$target")
  else
    RESOLVED+=("$d")
  fi
done
[ "${#RESOLVED[@]}" -gt 0 ] || { echo "ERROR: scope is empty (engine and css disabled)." >&2; exit 2; }

# shellcheck disable=SC2145
if [ "$FORCE" -eq 1 ]; then
  echo "scope: ${RESOLVED[@]} (forced re-upload)" >&2
  exec ./tools/deploy/deploy-ftp.sh --force --files "${RESOLVED[@]}" "${PASSTHROUGH[@]}"
else
  echo "scope: ${RESOLVED[@]} (same-size files skipped)" >&2
  exec ./tools/deploy/deploy-ftp.sh --files "${RESOLVED[@]}" "${PASSTHROUGH[@]}"
fi
