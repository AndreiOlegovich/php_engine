#!/usr/bin/env bash
# version 0.1.0
# Walk a new project through its whole onboarding in one run (README.md,
# sections 4 to 10), and ask when it needs you. The recommended way after
# new-project.sh; onboard.sh does one step per run, and the README gives every
# command by hand.
#
# Usage (from anywhere; it works on the project it lives in):
#   container/tools/auto_onboard.sh
#
# Each step prints its exact command, then runs it. It asks for:
#   - the folder of the old site (the one that holds its index.php);
#   - the profile lines discover_site.py suggests: write them, open the
#     profile in $EDITOR, or stop;
#   - the site's public address for the sitemap, while the profile still has
#     the example one;
#   - the first commit: it shows git status and the exact commit of README
#     section 9, and runs it only on yes.
# When Docker does not run or the gate fails, it says what to do and waits.
# It stops when a step does not move on, or when you answer q; run it again
# to go on where it stopped.
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

# ask PROMPT: the answer in ANSWER; the end of the input stops the walk.
ask() {
  printf '\n%s' "$1"
  if ! read -r ANSWER; then
    echo
    echo "No answer: stopping here. Run container/tools/auto_onboard.sh again to go on."
    exit 0
  fi
}

stop_here() {
  echo
  echo "$1"
  echo "Run container/tools/auto_onboard.sh again to go on."
  exit "${2:-0}"
}

describe() {
  case "$1" in
    docker) echo "Docker" ;;
    start) echo "start the site (README section 4)" ;;
    copy) echo "copy the old site in (README section 5)" ;;
    discover) echo "let the pages suggest the profile (README section 6)" ;;
    gate) echo "the gate and the first commit (README sections 7 to 9)" ;;
    sitemap) echo "the sitemap (README section 10)" ;;
    tests) echo "the page tests (README section 10)" ;;
    *) echo "$1" ;;
  esac
}

ANSWER=""
last=""
repeats=0
while true; do
  step="$("$NP" --next-id)"
  if [ "$step" = "$last" ]; then repeats=$((repeats + 1)); else repeats=0; fi
  last="$step"
  # A step that ran and is still the step did not work: stop instead of looping.
  case "$step" in
    start|sitemap) [ "$repeats" -eq 0 ] || stop_here "The step '$step' did not finish: see the output above." 1 ;;
  esac
  echo
  echo "=== $(describe "$step")"
  case "$step" in
    docker)
      echo "Docker is not running. Start it, e.g.: sudo systemctl start docker"
      ask "Press Enter when it runs, or q to stop: "
      [ "$ANSWER" != q ] || stop_here "Stopped."
      ;;
    start)
      step_start || stop_here "The site did not start: see the output above." 1
      ;;
    copy)
      ask "Folder of the old site (the one that holds its index.php), or q to stop: "
      [ "$ANSWER" != q ] || stop_here "Stopped."
      old="$(old_site_dir "$ANSWER")" || continue
      step_copy "$old" || stop_here "The copy failed: see the output above." 1
      ;;
    discover)
      step_discover || stop_here "The discovery failed: see the output above." 1
      mapfile -t lines < <(discovered_lines)
      echo
      if [ "${#lines[@]}" -gt 0 ]; then
        echo "Suggested for container/site-profile.env (each is a piece of the page HTML or a value the files show):"
        printf '  %s\n' "${lines[@]}"
        ask "[y] write these lines   [e] edit the profile yourself   [q] stop: "
      else
        echo "Nothing certain enough to suggest."
        ask "[e] edit the profile yourself   [q] stop: "
      fi
      case "$ANSWER" in
        y|Y)
          [ "${#lines[@]}" -gt 0 ] || continue
          for line in "${lines[@]}"; do set_profile_line "$line"; done
          echo "written: ${#lines[@]} lines in container/site-profile.env"
          ;;
        e|E)
          show_run "${EDITOR:-nano} container/site-profile.env"
          ;;
        *) stop_here "Stopped; the report is in $DISCOVER_REPORT." ;;
      esac
      [ "$("$NP" --next-id)" != discover ] || stop_here "The profile is still as new-project.sh wrote it." 1
      ;;
    gate)
      if ! step_gate; then
        echo
        echo "Fix the failures in the order of README section 8; each one names its row"
        echo "in container/docs/site-checks.md."
        ask "Press Enter to run the gate again, or q to stop: "
        [ "$ANSWER" != q ] || stop_here "Stopped."
        continue
      fi
      echo
      echo "The gate passed. Next the first commit (README section 9). What git would take in:"
      show_run "git status --short | head -n 30"
      echo "($(git status --short | wc -l) entries; no secret and no junk should be among them)"
      ask "[y] run the first commit   [n] I commit myself (then run auto_onboard.sh again): "
      case "$ANSWER" in
        y|Y) step_commit || stop_here "The commit failed: see the hook output above (README section 9)." 1 ;;
        *) stop_here "Commit with the paths you mean, as README section 9 shows." ;;
      esac
      ;;
    sitemap)
      url="$(profile_value SITE_URL)"
      if [ -z "$url" ] || [ "$url" = "https://example.com" ]; then
        ask "The site's public address, e.g. https://www.my-site.example (q to stop): "
        [ "$ANSWER" != q ] || stop_here "Stopped."
        case "$ANSWER" in
          http://*|https://*) url="${ANSWER%/}" ;;
          *) echo "ERROR: give the address with http:// or https://." >&2; last=""; continue ;;
        esac
        set_profile_line "SITE_URL=\"$url\""
        echo "written: SITE_URL=\"$url\" in container/site-profile.env"
      fi
      step_sitemap "$url" || stop_here "The sitemap failed: see the output above." 1
      ;;
    tests)
      step_tests || true
      echo
      echo "The onboarding is done. README sections 11 (design files) and 12 (deploy) follow."
      exit 0
      ;;
    *)
      echo "ERROR: unknown step '$step' from new-project.sh --next-id." >&2
      exit 2
      ;;
  esac
done
