#!/usr/bin/env bash
# version 0.2.0
# Run the onboarding step a new project is at, then print the one after it.
# It prints the exact command of the step before it runs it. To go through all
# the steps in one run, with questions where it needs you: auto_onboard.sh.
#
# Usage (from anywhere; it works on the project it lives in):
#   container/tools/onboard.sh                    # run the current step
#   container/tools/onboard.sh /path/to/old-site  # the copy step: copy that site in
#
# The steps, as new-project.sh --next names them (README sections 4 to 10):
#   start     start the site:     docker compose up -d --build php-apache
#   copy      copy the old site:  rsync -a --exclude .git/ --info=progress2 --no-inc-recursive OLD/ container/src/
#             (one progress line: copied, percent, speed, time left, files left)
#   discover  suggest the profile: discover_site.py --url; you copy the lines
#             you agree with into container/site-profile.env
#   gate      new-project.sh --check; the first commit stays yours, because
#             you choose which paths go into it
#   sitemap   generate-sitemap.sh with SITE_URL of the profile
#   tests     the page tests
# Docker itself it does not start: that needs sudo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NP="$ROOT/container/tools/new-project.sh"
cd "$ROOT"

case "${1:-}" in
  -h|--help) sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; exit 0 ;;
esac

# shellcheck disable=SC1091  # the shared steps are checked on their own
. "$SCRIPT_DIR/onboard-lib.sh"

step="$("$NP" --next-id)"
echo "=== step: $step"
case "$step" in
  docker)
    "$NP" --next
    exit 1
    ;;
  start)
    step_start
    ;;
  copy)
    if [ -z "${1:-}" ]; then
      echo "Give the folder that holds the old site's index.php:"
      echo "  container/tools/onboard.sh /path/to/old-site/"
      exit 2
    fi
    old="$(old_site_dir "$1")" || exit 2
    step_copy "$old"
    ;;
  discover)
    step_discover
    echo
    echo "Copy the suggested lines you agree with into container/site-profile.env"
    echo "(check each one in a page's source first), then run: container/tools/onboard.sh"
    exit 0
    ;;
  gate)
    if ! step_gate; then
      echo
      echo "Fix in the order of README section 8, then run: container/tools/onboard.sh"
      exit 1
    fi
    echo
    echo "Now the first commit (README section 9); auto_onboard.sh can run it for you:"
    echo "  git add -- ${COMMIT_PATHS[*]}"
    echo "  git commit -m \"Start $(profile_value SITE_NAME)\""
    echo "then run: container/tools/onboard.sh"
    exit 0
    ;;
  sitemap)
    step_sitemap "$(profile_value SITE_URL)"
    ;;
  tests)
    step_tests
    echo
    echo "The onboarding is done: README sections 11 (design files) and 12 (deploy) follow."
    exit 0
    ;;
  *)
    echo "ERROR: unknown step '$step' from new-project.sh --next-id." >&2
    exit 2
    ;;
esac

echo
"$NP" --next
