#!/usr/bin/env bash
# version 0.1.0
# The onboarding steps, shared by onboard.sh (one step per run) and
# auto_onboard.sh (all steps, asking when it needs the user). Source it with
# ROOT set to the project root; every step prints the exact command, then runs
# it there. These are the same commands as README.md, sections 4 to 10.

# The paths of the first commit, as README section 9 gives them.
COMMIT_PATHS=(.gitignore .pre-commit-config.yaml .python-version AGENTS.md CLAUDE.md opencode.jsonc .claude .opencode container)
DISCOVER_REPORT="container/reports/discover-site.txt"

# show_run COMMAND: print it the way it can be typed, then run it.
show_run() {
  printf '\n$ %s\n' "$1"
  bash -o pipefail -c "$1"
}

profile_value() {
  sed -n "s/^$1=[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*\$/\1/p" "$ROOT/container/site-profile.env" 2>/dev/null | tail -n 1
}

# set_profile_line LINE: KEY=VALUE replaces the line of KEY, or is appended.
set_profile_line() {
  local profile="$ROOT/container/site-profile.env" key="${1%%=*}"
  PROFILE_KEY="$key" PROFILE_LINE="$1" awk '
    index($0, ENVIRON["PROFILE_KEY"] "=") == 1 { if (!done) print ENVIRON["PROFILE_LINE"]; done = 1; next }
    { print }
    END { if (!done) print ENVIRON["PROFILE_LINE"] }
  ' "$profile" > "$profile.tmp"
  chmod --reference="$profile" "$profile.tmp"
  mv "$profile.tmp" "$profile"
}

# old_site_dir PATH: the folder of the old site, checked; empty and 1 when not.
old_site_dir() {
  local old="$1"
  [ -d "$old" ] || { echo "ERROR: $old is not a folder." >&2; return 1; }
  old="$(cd "$old" && pwd)"
  [ "$old" != "$ROOT/container/src" ] || { echo "ERROR: that is this project's own container/src." >&2; return 1; }
  if [ ! -e "$old/index.php" ] && [ ! -e "$old/index.html" ]; then
    echo "ERROR: $old holds no index.php or index.html. Give the folder of the site itself (often public_html/ inside a hosting copy)." >&2
    return 1
  fi
  printf '%s' "$old"
}

step_start() {
  show_run "(cd container && docker compose up -d --build php-apache)"
}

step_copy() {
  echo "Copying $1/ into container/src/; the original stays as it is."
  echo "rsync counts the files first, which takes a while on a big site; then one line"
  echo "shows the progress: copied, percent, speed, time left (xfr#copied, to-chk=left/total)."
  show_run "rsync -a --exclude .git/ --info=progress2 --no-inc-recursive $(printf '%q' "$1/") container/src/"
  echo "copied: $(find "$ROOT/container/src" -type f | wc -l) files in container/src"
}

# step_discover: the report also goes to $DISCOVER_REPORT (git-ignored).
step_discover() {
  mkdir -p "$(dirname "$DISCOVER_REPORT")"
  show_run "python3 container/tools/discover_site.py --url | tee $DISCOVER_REPORT"
}

# The suggested lines of the last discovery report, one per line.
discovered_lines() {
  sed -n '/^== Suggested lines/,$p' "$ROOT/$DISCOVER_REPORT" 2>/dev/null | sed -n 's/^  \(SITE_[A-Z_]*=.*\)$/\1/p'
}

step_gate() {
  show_run "container/tools/new-project.sh --check"
}

# step_commit: the first commit of README section 9. The first commit of a site
# with images stops once (the image-list hook writes the list): stage and
# commit again, as the README says.
step_commit() {
  local paths=() path message
  for path in "${COMMIT_PATHS[@]}"; do [ ! -e "$ROOT/$path" ] || paths+=("$path"); done
  message="Start $(profile_value SITE_NAME)"
  local add="git add -- ${paths[*]}" commit
  commit="git commit -m $(printf '%q' "$message")"
  show_run "$add" || return 1
  if show_run "$commit"; then return 0; fi
  echo
  echo "The commit stopped. When it was the image-list hook writing container/docs/image-files.txt,"
  echo "staging again and committing once more finishes it (README section 9):"
  show_run "$add" || return 1
  show_run "$commit"
}

step_sitemap() {
  show_run "(cd container && BASE_URL=$(printf '%q' "$1") tools/generate-sitemap.sh)"
}

step_tests() {
  show_run "container/tests/run_test.sh -y sitemap internal_links img_src content_loaded"
}
