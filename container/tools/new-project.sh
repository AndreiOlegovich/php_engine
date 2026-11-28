#!/usr/bin/env bash
# version 0.7.0
# Start a new site from this engine checkout, under its own project name.
#
# Usage (from anywhere):
#   container/tools/new-project.sh [TARGET] [--name NAME]
#                 [--bottom-marker HTML] [--tag-markers SPEC]
#   container/tools/new-project.sh --configure [TARGET] [--name NAME]
#   container/tools/new-project.sh --check [TARGET]
#   container/tools/new-project.sh --next [TARGET]
#   container/tools/new-project.sh --next-id [TARGET]
#   container/tools/new-project.sh --help
#
#   TARGET        a new folder; it must not exist yet. Without it the script
#                 asks, and offers a folder next to this checkout
#   --name NAME   project name. Without it the script asks in a terminal and
#                 otherwise takes the last part of TARGET
#   --bottom-marker HTML
#                 what every complete page of the site renders last, a piece
#                 of its HTML source such as id="footer-end" (not CSS); the
#                 content test fails a page that stops before it. Default
#                 </html>. Usually left out: after the site is copied in,
#                 container/tools/discover_site.py --url suggests it
#   --tag-markers SPEC
#                 named pieces of the page HTML for --tag of the content
#                 test: name=HTML, "|" between alternatives, ";" between
#                 names, e.g. 'menu=<nav class="menu"'. None by default;
#                 discover_site.py suggests them too
#   --configure   give an existing checkout (TARGET, default: this one) its
#                 project name and free ports; nothing else changes
#   --check       run the whole gate over TARGET (default: the current
#                 checkout) and print what failed and where it is explained;
#                 exit 1 when anything failed
#   --next        look at TARGET (default: the current checkout) and print
#                 the one step it is at, with its command: start the site,
#                 copy the old site in, fill the profile, the gate and the
#                 first commit, the sitemap, the page tests.
#                 container/tools/onboard.sh runs that step
#   --next-id     the same step as one word for scripts: docker, start, copy,
#                 discover, gate, sitemap, tests
#
# The project name keeps projects apart. Its lower-case form is SITE_PROJECT
# in container/.env and names the Compose project, the containers, the site
# and agent images, the OpenCode volume, the agent worktrees, the hosting
# credentials folder and the log folders. The script refuses a name another
# project already uses and picks free ports for the site and Allure.
#
# What it copies: the tracked files of the checkout this script lives in.
# Never .git, .venv, caches, site-profile.env, creds or *.env: an ignored log,
# a visitor export or a filled creds file of this checkout never reaches the
# new one.
# What it empties: container/src, down to an index.php that prints the site
# name. The engine's own folder container/src/.php stays.
# What it writes: container/.env, container/site-profile.env from the example
# with SITE_NAME and the markers, the project name in the agent permission
# files, and git init -b main. Nothing is staged: add the files you mean to
# commit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$SCRIPT_DIR/../.." && pwd)"
ALLURE_VIEWERS=7

# shellcheck disable=SC1091  # shared resolver is checked separately
. "$SCRIPT_DIR/project-env.sh"
# Only project_slug stays: a value of this checkout must not reach the tools
# that this script runs in another one.
unset SITE_PROJECT SITE_HTTP_PORT SITE_ALLURE_PORT SITE_LOCAL_BASE

usage() {
  sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
}

NAME=""
BOTTOM_MARKER=""
TAG_MARKERS=""
CHECK=0
CONFIGURE=0
NEXT_STEP=0
NEXT_ID=0
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name) [ $# -ge 2 ] || { echo "ERROR: --name needs a value." >&2; exit 2; }; NAME="$2"; shift 2 ;;
    --name=*) NAME="${1#--name=}"; shift ;;
    --bottom-marker) [ $# -ge 2 ] || { echo "ERROR: --bottom-marker needs a value." >&2; exit 2; }; BOTTOM_MARKER="$2"; shift 2 ;;
    --bottom-marker=*) BOTTOM_MARKER="${1#--bottom-marker=}"; shift ;;
    --tag-markers) [ $# -ge 2 ] || { echo "ERROR: --tag-markers needs a value." >&2; exit 2; }; TAG_MARKERS="$2"; shift 2 ;;
    --tag-markers=*) TAG_MARKERS="${1#--tag-markers=}"; shift ;;
    --check) CHECK=1; shift ;;
    --next) NEXT_STEP=1; shift ;;
    --next-id) NEXT_STEP=1; NEXT_ID=1; shift ;;
    --configure) CONFIGURE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) [ -z "$TARGET" ] || { echo "ERROR: one TARGET only." >&2; exit 2; }; TARGET="$1"; shift ;;
  esac
done
[ $((CHECK + CONFIGURE + NEXT_STEP)) -le 1 ] || { echo "ERROR: --check, --configure and --next exclude each other." >&2; exit 2; }

# The name of a checkout, read the way its own tools read it.
current_project() {
  (
    # shellcheck disable=SC1091  # shared resolver is checked separately
    . "$1/container/tools/project-env.sh"
    printf '%s' "$SITE_PROJECT"
  )
}

main_checkout() {
  local common
  common="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  case "$common" in
    */.git) printf '%s' "${common%/.git}" ;;
    *) printf '%s' "$1" ;;
  esac
}

env_value() {
  [ -f "$1" ] || return 0
  sed -n "s/^$2=[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*\$/\1/p" "$1" | tail -n 1
}

# Sets NAME: the option, else the answer in a terminal, else the suggestion.
ask_name() {
  local suggested="$1" answer=""
  if [ -z "$NAME" ]; then
    if [ -t 0 ]; then
      read -r -p "Project name${suggested:+ [$suggested]}: " answer || true
    fi
    NAME="${answer:-$suggested}"
  fi
  [ -n "$NAME" ] || { echo "ERROR: give a project name with --name, or a TARGET." >&2; exit 2; }
  # The name goes into a PHP single-quoted string and a sourced env file, so
  # it may not hold text that needs quoting.
  case "$NAME" in
    *$'\n'*|*"'"*|*'"'*|*'$'*|*'`'*|*\\*)
      echo "ERROR: the name may not hold a newline, a quote, a backslash, \$ or \`." >&2
      exit 2
      ;;
  esac
}

slug_of_name() {
  local slug
  slug="$(project_slug "$NAME")"
  [ -n "$slug" ] || {
    echo "ERROR: '$NAME' has no Latin letter or digit; Docker names need one." >&2
    exit 2
  }
  printf '%s' "$slug"
}

# Prints who uses the name and returns 0; returns 1 when it is free.
# With "own" as the third argument the checkout already has this name, so its
# own images and folders do not count.
name_in_use() {
  local slug="$1" root="$2" own="${3:-}" other
  for other in "$(dirname "$root")"/*/container/.env; do
    [ -f "$other" ] && [ "$other" != "$root/container/.env" ] || continue
    if [ "$(env_value "$other" SITE_PROJECT)" = "$slug" ]; then
      echo "the project in ${other%/container/.env}"
      return 0
    fi
  done
  [ -z "$own" ] || return 1
  if [ -e "$HOME/.config/$slug" ]; then echo "the folder ~/.config/$slug"; return 0; fi
  if [ -e "${XDG_DATA_HOME:-$HOME/.local/share}/$slug" ]; then echo "the data folder of $slug"; return 0; fi
  docker info >/dev/null 2>&1 || return 1
  if docker compose ls -a -q 2>/dev/null | grep -qx -- "$slug"; then echo "the Compose project $slug"; return 0; fi
  if [ -n "$(docker image ls -q --filter "reference=$slug-agent*" 2>/dev/null)" ] \
    || [ -n "$(docker image ls -q --filter "reference=$slug:*" 2>/dev/null)" ]; then
    echo "a Docker image named $slug"
    return 0
  fi
  if docker volume inspect "$slug-agent-data" >/dev/null 2>&1; then echo "the volume $slug-agent-data"; return 0; fi
  return 1
}

refuse_used_name() {
  local reason
  if reason="$(name_in_use "$@")"; then
    echo "ERROR: the project name '$1' is already used by $reason. Pick another one." >&2
    exit 2
  fi
}

port_free() {
  python3 -c '
import socket, sys
with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", int(sys.argv[1])))
    except OSError:
        sys.exit(1)
' "$1"
}

# The ports the other projects next to this one have claimed, running or not.
claimed_ports() {
  local root="$1" other value i
  for other in "$(dirname "$root")"/*/container/.env; do
    [ -f "$other" ] && [ "$other" != "$root/container/.env" ] || continue
    value="$(env_value "$other" SITE_HTTP_PORT)"
    [ -z "$value" ] || echo "$value"
    value="$(env_value "$other" SITE_ALLURE_PORT)"
    if [ -n "$value" ]; then
      for ((i = value; i < value + ALLURE_VIEWERS; i++)); do echo "$i"; done
    fi
  done
}

# pick_port START COUNT [CLAIMED...]: the first run of COUNT free ports.
pick_port() {
  local port="$1" count="$2" i ok
  shift 2
  while [ "$port" -lt 65000 ]; do
    ok=1
    for ((i = port; i < port + count; i++)); do
      if printf '%s\n' "$@" | grep -qx -- "$i" || ! port_free "$i"; then
        ok=0
        port=$((i + 1))
        break
      fi
    done
    if [ "$ok" -eq 1 ]; then
      echo "$port"
      return 0
    fi
  done
  echo "ERROR: no free port from $1." >&2
  return 1
}

# Writes container/.env and the local URL of the profile. Ports the checkout
# already has stay. New ones start above the defaults 8080 and 5050-5056, which
# a checkout without container/.env uses.
write_identity() {
  local root="$1" slug="$2" env_file="$1/container/.env" profile="$1/container/site-profile.env"
  local http allure claimed=()
  mapfile -t claimed < <(claimed_ports "$root")
  http="$(env_value "$env_file" SITE_HTTP_PORT)"
  allure="$(env_value "$env_file" SITE_ALLURE_PORT)"
  [ -n "$http" ] || http="$(pick_port 8081 1 "${claimed[@]}")"
  [ -n "$allure" ] || allure="$(pick_port 5060 "$ALLURE_VIEWERS" "${claimed[@]}")"
  {
    if [ -f "$env_file" ]; then
      grep -vE '^SITE_(PROJECT|HTTP_PORT|ALLURE_PORT)=' "$env_file" || true
    else
      echo "# This project's name and ports (new-project.sh). Ignored by git."
    fi
    printf 'SITE_PROJECT="%s"\n' "$slug"
    printf 'SITE_HTTP_PORT="%s"\n' "$http"
    printf 'SITE_ALLURE_PORT="%s"\n' "$allure"
  } > "$env_file.tmp"
  mv "$env_file.tmp" "$env_file"
  if [ -f "$profile" ]; then
    sed "s#^SITE_LOCAL_URL=.*#SITE_LOCAL_URL=\"http://127.0.0.1:$http\"#" "$profile" > "$profile.tmp"
    chmod --reference="$profile" "$profile.tmp"
    mv "$profile.tmp" "$profile"
  fi
  printf '  project:      %s\n  site:         http://127.0.0.1:%s/\n  allure:       ports %s-%s\n' \
    "$slug" "$http" "$allure" "$((allure + ALLURE_VIEWERS - 1))"
  printf '  images:       %s:<php>, %s-agent, %s-agent-<language>\n' "$slug" "$slug" "$slug"
  printf '  credentials:  ~/.config/%s/hosting-access.env\n' "$slug"
}

# Sets KEY='VALUE' in the profile, in place of the example's line. Single
# quotes, because a marker is HTML and holds double quotes.
set_profile_value() {
  local profile="$1" key="$2" value="$3"
  PROFILE_KEY="$key" PROFILE_LINE="$key='$value'" awk '
    index($0, ENVIRON["PROFILE_KEY"] "=") == 1 { if (!done) print ENVIRON["PROFILE_LINE"]; done = 1; next }
    { print }
    END { if (!done) print ENVIRON["PROFILE_LINE"] }
  ' "$profile" > "$profile.tmp"
  chmod --reference="$profile" "$profile.tmp"
  mv "$profile.tmp" "$profile"
}

# The markers the content test looks for are the site's own HTML, which
# nobody knows before the site is copied in: only the options set them here,
# and discover_site.py suggests them later from the rendered pages.
check_markers() {
  case "$BOTTOM_MARKER$TAG_MARKERS" in
    *"'"*) echo "ERROR: a marker may not hold a single quote." >&2; exit 2 ;;
  esac
}

write_markers() {
  [ -z "$BOTTOM_MARKER" ] || set_profile_value "$1" SITE_BOTTOM_MARKER "$BOTTOM_MARKER"
  [ -z "$TAG_MARKERS" ] || set_profile_value "$1" SITE_TAG_MARKERS "$TAG_MARKERS"
}

# The agents' deny rules name the credentials and log folders: they must follow
# the project, or a new site's secrets would sit outside them.
rename_protected() {
  local root="$1" old="$2" new="$3" file
  [ "$old" != "$new" ] || return 0
  for file in .claude/settings.json opencode.jsonc .opencode/agents/reviewer.md; do
    [ -f "$root/$file" ] || continue
    sed -e "s#\.config/$old#.config/$new#g" \
      -e "s#\.local/share/$old#.local/share/$new#g" \
      -e "s#\*$old/logs#*$new/logs#g" \
      -e "s#/$old-agent-#/$new-agent-#g" "$root/$file" > "$root/$file.tmp"
    chmod --reference="$root/$file" "$root/$file.tmp"
    mv "$root/$file.tmp" "$root/$file"
  done
}

bootstrap() {
  local suggested="" slug engine_project offered
  [ -z "$TARGET" ] || suggested="$(basename "$TARGET")"
  ask_name "$suggested"
  slug="$(slug_of_name)"
  if [ -z "$TARGET" ]; then
    [ -t 0 ] || { echo "ERROR: TARGET is required outside a terminal." >&2; exit 2; }
    offered="$(dirname "$SOURCE")/$slug"
    read -r -p "Folder [$offered]: " TARGET || true
    TARGET="${TARGET:-$offered}"
  fi
  TARGET="$(realpath -m "$TARGET")"
  [ ! -e "$TARGET" ] || { echo "ERROR: $TARGET already exists; pick a new folder." >&2; exit 2; }
  git -C "$SOURCE" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { echo "ERROR: $SOURCE is not a git checkout; the tracked file list is needed." >&2; exit 2; }
  refuse_used_name "$slug" "$TARGET"
  engine_project="$(current_project "$SOURCE")"
  check_markers

  echo "Copying the tracked files of $SOURCE to $TARGET ..."
  mkdir -p "$TARGET"
  git -C "$SOURCE" ls-files -z | tar -C "$SOURCE" --null -T - -cf - | tar -C "$TARGET" -xf -

  echo "Emptying container/src down to a stub ..."
  local src="$TARGET/container/src"
  mkdir -p "$src"
  find "$src" -mindepth 1 -maxdepth 1 ! -name '.php' -exec rm -rf -- {} +
  {
    printf '%s\n' '<?php' ''
    printf "%s\n" "\$siteName = '$NAME';" ''
    printf '%s\n' "echo '<!DOCTYPE html>';" \
      "echo '<html lang=\"en\">';" \
      "echo '<head><meta charset=\"utf-8\"><title>' . htmlspecialchars(\$siteName, ENT_QUOTES) . '</title></head>';" \
      "echo '<body>';" \
      "echo '<h1>' . htmlspecialchars(\$siteName, ENT_QUOTES) . '</h1>';" \
      "echo '<p>Drop the pages of the new site into container/src.</p>';" \
      "echo '</body>';" \
      "echo '</html>';"
  } > "$src/index.php"

  echo "Writing container/site-profile.env and container/.env ..."
  cp "$TARGET/container/site-profile.env.example" "$TARGET/container/site-profile.env"
  printf 'SITE_NAME="%s"\n' "$NAME" >> "$TARGET/container/site-profile.env"
  write_markers "$TARGET/container/site-profile.env"
  write_identity "$TARGET" "$slug"
  rename_protected "$TARGET" "$engine_project" "$slug"

  echo "Initialising git ..."
  git -C "$TARGET" init -q -b main

  if command -v pre-commit >/dev/null 2>&1; then
    ( cd "$TARGET" && pre-commit install )
  else
    echo "SKIP: pre-commit is not installed; the hooks are not installed."
  fi
  if command -v uv >/dev/null 2>&1; then
    ( cd "$TARGET" && container/tools/run-tests-venv.sh --setup-only )
  else
    echo "SKIP: uv is not installed; the venv and the Python tests wait."
  fi

  cat <<NEXT

Next steps (README.md, sections 4 to 10):

  1. cd $(printf '%q' "$TARGET")
     go to the project: every command below runs there
  2. container/tools/auto_onboard.sh
     runs all the steps, printing each command, and asks when it needs you:
     the old site's folder, the profile lines, the public address, the
     first commit

Other ways:
  container/tools/onboard.sh         one step per run, then it shows the next
  README.md, sections 4 to 10        every command, to type by hand
  container/tools/new-project.sh --next   where the project is, any time
NEXT
}

configure() {
  local root current slug
  root="$(main_checkout "$(realpath "${TARGET:-$SOURCE}")")"
  [ -f "$root/container/tools/project-env.sh" ] \
    || { echo "ERROR: $root has no container/tools/project-env.sh." >&2; exit 2; }
  current="$(current_project "$root")"
  ask_name "$current"
  slug="$(slug_of_name)"
  if [ "$slug" = "$current" ]; then
    refuse_used_name "$slug" "$root" own
  else
    refuse_used_name "$slug" "$root"
  fi
  echo "Writing $root/container/.env ..."
  write_identity "$root" "$slug"
  rename_protected "$root" "$current" "$slug"
  if [ "$slug" != "$current" ]; then
    echo "note: anything under the old name $current stays: move ~/.config/$current by hand if it holds this site's credentials."
  fi
}

check() {
  local root="${TARGET:-$SOURCE}"
  [ -d "$root/container/tools" ] || { echo "ERROR: $root is not a project root." >&2; exit 2; }
  cd "$root"
  local failed=()
  local label
  run() {
    label="$1"; shift
    printf '== %s\n' "$label"
    if "$@"; then return 0; fi
    failed+=("$label")
    printf 'FAILED: %s\n\n' "$label" >&2
  }

  if [ -f container/src/sitemap.xml ]; then
    run "sitemap.xml parses, one document, unique loc" container/tools/check-sitemap.sh container/src/sitemap.xml
  else
    echo "SKIP: no container/src/sitemap.xml yet."
  fi
  run "line endings (container/src)" container/tools/linters/check-line-endings.sh --search --all --src src
  run "PHP whitespace-only lines (container/src)" container/tools/linters/check-php-blank-lines.sh --search --all --src src
  run "PHP opening header (container/src)" container/tools/linters/check-php-header.sh --search --all --src src
  run "malformed URLs (container/src)" container/tools/linters/find-malformed-urls.sh --all --src src
  run "snake_case method calls (container/src)" python3 container/tools/linters/find-snake-calls.py --src container/src
  if docker info >/dev/null 2>&1; then
    run "shell scripts (shellcheck)" container/tools/linters/lint-sh.sh --all
    run "PHP syntax (php -l)" container/tools/linters/lint-php.sh --all
    run "PHP style (phpcs)" container/tools/linters/lint-static.sh --tool phpcs --all
  else
    echo "SKIP: Docker is not running; shellcheck, php -l and phpcs wait for the host."
  fi
  if command -v pre-commit >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    # Tracked and not-yet-added files alike, so a fresh site is checked
    # before anything is staged.
    local files=()
    mapfile -d '' -t files < <(git ls-files -z --cached --others --exclude-standard)
    run "pre-commit, every file" pre-commit run --files "${files[@]}"
  else
    echo "SKIP: pre-commit is not installed, or this is not a git checkout."
  fi

  if [ "${#failed[@]}" -eq 0 ]; then
    echo "All checks passed."
    return 0
  fi
  echo
  echo "What failed:"
  printf '  - %s\n' "${failed[@]}"
  cat <<'DOC'

Where each failure is explained:
  line endings, PHP whitespace, PHP header, malformed URLs, sitemap,
  snake_case calls ...... container/docs/site-checks.md
  PHP syntax and style .. container/docs/lint-static.md
  shell scripts ......... container/tools/linters/lint-sh.sh --help
  pre-commit ............ the hook name in the output, then AGENTS.md
DOC
  return 1
}

# The profile as new-project.sh wrote it: the example plus the name and port.
profile_untouched() {
  local root="$1"
  [ -f "$root/container/site-profile.env" ] || return 0
  diff -q <(grep -vE '^(#|$|SITE_NAME=|SITE_LOCAL_URL=)' "$root/container/site-profile.env" | LC_ALL=C sort) \
    <(grep -vE '^(#|$|SITE_LOCAL_URL=)' "$root/container/site-profile.env.example" | LC_ALL=C sort) >/dev/null
}

# container/src holds only the stub index.php (and the engine's .php folder).
src_is_stub() {
  local src="$1/container/src" entries
  entries="$(find "$src" -mindepth 1 -maxdepth 1 ! -name .php -printf '%f\n' 2>/dev/null)"
  [ "$entries" = index.php ] && grep -q 'Drop the pages of the new site into container/src' "$src/index.php"
}

next_step() {
  local root slug go="" done=()
  root="$(main_checkout "$(realpath "${TARGET:-$SOURCE}")")"
  [ -f "$root/container/.env" ] || { echo "ERROR: $root has no container/.env: not a project new-project.sh made." >&2; exit 2; }
  slug="$(env_value "$root/container/.env" SITE_PROJECT)"
  [ "$(pwd -P)" = "$root" ] || go="cd $(printf '%q' "$root") && "
  # say ID NUMBER WHAT SECTION RUN LINE...: RUN is how onboard.sh runs it.
  say() {
    local id="$1" number="$2" what="$3" section="$4" run_it="$5"
    shift 5
    if [ "$NEXT_ID" = 1 ]; then echo "$id"; return 0; fi
    printf 'Project %s, %s\n' "$slug" "$root"
    local joined
    joined="$(printf '%s, ' "${done[@]}")"
    [ "${#done[@]}" -eq 0 ] || printf 'Done: %s.\n' "${joined%, }"
    printf '\nNext, step %s of 7: %s (README section %s)\n' "$number" "$what" "$section"
    printf '\n  recommended:    %s\n' "${go}container/tools/auto_onboard.sh"
    printf '                  all the remaining steps; it asks when it needs you\n'
    printf '  this step only: %s\n' "$run_it"
    printf '  by hand:\n'
    printf '    %s\n' "$@"
  }
  local onboard="${go}container/tools/onboard.sh"
  local runs="$onboard"

  if ! docker info >/dev/null 2>&1; then
    say docker 2 "start Docker, then the empty site" 4 "$onboard" "Docker is not running: start it (sudo systemctl start docker), then:" "${go}(cd container && docker compose up -d --build php-apache)"
    return 0
  fi
  if [ -z "$(docker ps -q --filter "name=^${slug}-apache\$" 2>/dev/null)" ]; then
    say start 2 "start the site" 4 "$runs" "${go}(cd container && docker compose up -d --build php-apache)"
    return 0
  fi
  done+=("the site runs at $(env_value "$root/container/site-profile.env" SITE_LOCAL_URL)/")
  if src_is_stub "$root"; then
    say copy 3 "copy the old site in" 5 "$onboard /path/to/old-site/" "${go}rsync -a --exclude .git/ --info=progress2 --no-inc-recursive /path/to/old-site/ container/src/" \
      "(/path/to/old-site/ is the folder that holds its index.php; keep the trailing slashes)"
    return 0
  fi
  done+=("the old site is in container/src")
  if profile_untouched "$root"; then
    say discover 4 "let the pages suggest the profile" 6 "$runs" "${go}python3 container/tools/discover_site.py --url" \
      "then copy the suggested lines you agree with into container/site-profile.env"
    return 0
  fi
  done+=("the profile is filled")
  if ! git -C "$root" rev-parse -q --verify HEAD >/dev/null; then
    say gate "5 and 6" "the gate, then the first commit" "7 to 9" "$onboard" "${go}container/tools/new-project.sh --check" \
      "fix in the order of README section 8 until it prints All checks passed, then:" \
      "git add -- <paths> && git commit -m \"<subject>\""
    return 0
  fi
  done+=("the first commit is made")
  if [ ! -f "$root/container/src/sitemap.xml" ]; then
    say sitemap 7 "generate the sitemap" 10 "$runs" "${go}(cd container && BASE_URL=$(env_value "$root/container/site-profile.env" SITE_URL) tools/generate-sitemap.sh)"
    return 0
  fi
  done+=("the sitemap exists")
  say tests 7 "test the pages" 10 "$runs" "${go}container/tests/run_test.sh -y sitemap internal_links img_src content_loaded" \
    "the onboarding ends here: README sections 11 (design files) and 12 (deploy) follow"
}

if [ "$CHECK" -eq 1 ]; then
  check
elif [ "$NEXT_STEP" -eq 1 ]; then
  next_step
elif [ "$CONFIGURE" -eq 1 ]; then
  configure
else
  bootstrap
fi
