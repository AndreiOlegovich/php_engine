#!/usr/bin/env bash
# version 0.1.0
# Run the onboarding step a new project is at, then print the one after it.
#
# Usage (from anywhere; it works on the project it lives in):
#   container/tools/onboard.sh                    # run the current step
#   container/tools/onboard.sh /path/to/old-site  # the copy step: copy that site in
#
# The steps, as new-project.sh --next names them (README sections 4 to 10):
#   start     start the site:     docker compose up -d --build php-apache
#   copy      copy the old site:  rsync -a --exclude .git/ OLD/ container/src/
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

profile_value() {
  sed -n "s/^$1=[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*\$/\1/p" container/site-profile.env 2>/dev/null | tail -n 1
}

step="$("$NP" --next-id)"
echo "=== step: $step"
case "$step" in
  docker)
    "$NP" --next
    exit 1
    ;;
  start)
    (cd container && docker compose up -d --build php-apache)
    ;;
  copy)
    old="${1:-}"
    if [ -z "$old" ]; then
      echo "Give the folder that holds the old site's index.php:"
      echo "  container/tools/onboard.sh /path/to/old-site/"
      exit 2
    fi
    [ -d "$old" ] || { echo "ERROR: $old is not a folder." >&2; exit 2; }
    old="$(cd "$old" && pwd)"
    [ "$old" != "$ROOT/container/src" ] || { echo "ERROR: that is this project's own container/src." >&2; exit 2; }
    if [ ! -e "$old/index.php" ] && [ ! -e "$old/index.html" ]; then
      echo "ERROR: $old holds no index.php or index.html. Give the folder of the site itself (often public_html/ inside a hosting copy)." >&2
      exit 2
    fi
    echo "copying $old/ into container/src/ (the original stays as it is) ..."
    rsync -a --exclude .git/ "$old/" container/src/
    ;;
  discover)
    python3 container/tools/discover_site.py --url
    echo
    echo "Copy the suggested lines you agree with into container/site-profile.env"
    echo "(check each one in a page's source first), then run: container/tools/onboard.sh"
    exit 0
    ;;
  gate)
    if ! "$NP" --check; then
      echo
      echo "Fix in the order of README section 8, then run: container/tools/onboard.sh"
      exit 1
    fi
    echo
    echo "Now the first commit, with the paths you mean (README section 9):"
    echo "  git add -- <paths> && git commit -m \"<subject>\""
    echo "then run: container/tools/onboard.sh"
    exit 0
    ;;
  sitemap)
    url="$(profile_value SITE_URL)"
    (cd container && BASE_URL="$url" tools/generate-sitemap.sh)
    ;;
  tests)
    container/tests/run_test.sh -y sitemap internal_links img_src content_loaded
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
