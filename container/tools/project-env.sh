#!/usr/bin/env bash
# version 0.1.0
# Source this file. It names this project, so that several copies of the
# engine can run side by side without sharing a Docker image, container,
# volume, port, credentials file or log folder.
#
# It sets and exports:
#   SITE_PROJECT      my-site: images my-site:8.2 and my-site-agent, container
#                     my-site-apache, volume my-site-agent-data, ~/.config/my-site
#   SITE_HTTP_PORT    host port of the local site (default 8080)
#   SITE_ALLURE_PORT  first host port of the Allure viewers (default 5050)
#   SITE_LOCAL_BASE   http://127.0.0.1:$SITE_HTTP_PORT
#
# Each value comes from the environment, then from container/.env of the main
# checkout, which new-project.sh writes. The name falls back to the folder of
# the main checkout, so a linked worktree always belongs to its project.

# A name Docker accepts: lower case, digits, - and _, starting and ending with
# a letter or a digit. Prints nothing when no such character is left.
project_slug() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' \
    | LC_ALL=C sed -E 's/[^a-z0-9_-]+/-/g; s/^[^a-z0-9]+//; s/[^a-z0-9]+$//'
}

_project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_project_git="$(git -C "$_project_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
case "$_project_git" in
  */.git) _project_dir="${_project_git%/.git}" ;;
esac

_project_value() {
  [ -r "$_project_dir/container/.env" ] || return 0
  sed -n "s/^$1=[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*\$/\1/p" "$_project_dir/container/.env" | tail -n 1
}

SITE_PROJECT="${SITE_PROJECT:-$(_project_value SITE_PROJECT)}"
SITE_PROJECT="${SITE_PROJECT:-${_project_dir##*/}}"
SITE_PROJECT="$(project_slug "$SITE_PROJECT")"
SITE_PROJECT="${SITE_PROJECT:-php_engine}"
SITE_HTTP_PORT="${SITE_HTTP_PORT:-$(_project_value SITE_HTTP_PORT)}"
SITE_HTTP_PORT="${SITE_HTTP_PORT:-8080}"
SITE_ALLURE_PORT="${SITE_ALLURE_PORT:-$(_project_value SITE_ALLURE_PORT)}"
SITE_ALLURE_PORT="${SITE_ALLURE_PORT:-5050}"
SITE_LOCAL_BASE="http://127.0.0.1:$SITE_HTTP_PORT"
export SITE_PROJECT SITE_HTTP_PORT SITE_ALLURE_PORT SITE_LOCAL_BASE

unset -f _project_value
unset _project_dir _project_git
