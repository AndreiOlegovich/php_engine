#!/usr/bin/env bash
# version 0.1.1
# Shared external log root for the prod-log pipeline.
#
# Default: ${XDG_DATA_HOME:-$HOME/.local/share}/<project>/logs
# Override: SITE_LOG_DIR=/absolute/path
#
# Sourced by download-logs.sh and the fetch-* wrappers; callers keep their
# --dest flag, which always wins.

# shellcheck disable=SC1091  # shared resolver is checked separately
. "$(dirname "${BASH_SOURCE[0]}")/../project-env.sh"

if [ -z "${SITE_LOG_DIR:-}" ]; then
  SITE_LOG_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/$SITE_PROJECT/logs"
fi
# `~` inside an env value is not expanded by the shell; do it explicitly.
# Keep the literal tilde in a variable: shellcheck's SC2088 would fire on a
# quoted "~/" pattern even though this is a deliberate prefix strip.
_SITE_TILDE='~'
case "$SITE_LOG_DIR" in
  "$_SITE_TILDE") SITE_LOG_DIR="$HOME" ;;
  "$_SITE_TILDE"/*) SITE_LOG_DIR="$HOME/${SITE_LOG_DIR:2}" ;;
esac
unset _SITE_TILDE
[ "$SITE_LOG_DIR" = "/" ] || SITE_LOG_DIR="${SITE_LOG_DIR%/}"
export SITE_LOG_DIR
