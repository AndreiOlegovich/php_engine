#!/usr/bin/env bash
# version 0.1.0
# Start a new site from this engine checkout.
#
# Usage (from anywhere):
#   container/tools/new-project.sh TARGET [--name NAME]
#   container/tools/new-project.sh --check [TARGET]
#   container/tools/new-project.sh --help
#
#   TARGET        a new folder; it must not exist yet
#   --name NAME   project name (default: the last part of TARGET)
#   --check       run the whole gate over TARGET (default: the current
#                 checkout) and print what failed and where it is explained;
#                 exit 1 when anything failed
#
# What it copies: the tracked files of the checkout this script lives in.
# Never .git, .venv, caches, site-profile.env, creds or *.env: an ignored log,
# a visitor export or a filled creds file of this checkout never reaches the
# new one.
# What it empties: container/src, down to an index.php that prints the site
# name. The engine's own folder container/src/.php stays.
# What it writes: container/site-profile.env from the example, with SITE_NAME,
# and git init -b main. Nothing is staged: add the files you mean to commit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
  sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
}

NAME=""
CHECK=0
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name) [ $# -ge 2 ] || { echo "ERROR: --name needs a value." >&2; exit 2; }; NAME="$2"; shift 2 ;;
    --name=*) NAME="${1#--name=}"; shift ;;
    --check) CHECK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) [ -z "$TARGET" ] || { echo "ERROR: one TARGET only." >&2; exit 2; }; TARGET="$1"; shift ;;
  esac
done

bootstrap() {
  [ -n "$TARGET" ] || { echo "ERROR: TARGET is required to create a site." >&2; exit 2; }
  [ ! -e "$TARGET" ] || { echo "ERROR: $TARGET already exists; pick a new folder." >&2; exit 2; }
  git -C "$SOURCE" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { echo "ERROR: $SOURCE is not a git checkout; the tracked file list is needed." >&2; exit 2; }

  local name="${NAME:-$(basename "$TARGET")}"
  # The name goes into a PHP single-quoted string and a sourced env file, so
  # it may not hold text that needs quoting.
  case "$name" in
    *$'\n'*|*"'"*|*'"'*|*'$'*|*'`'*|*\\*)
      echo "ERROR: the name may not hold a newline, a quote, a backslash, \$ or \`." >&2
      exit 2
      ;;
  esac

  echo "Copying the tracked files of $SOURCE to $TARGET ..."
  mkdir -p "$TARGET"
  git -C "$SOURCE" ls-files -z | tar -C "$SOURCE" --null -T - -cf - | tar -C "$TARGET" -xf -

  echo "Emptying container/src down to a stub ..."
  local src="$TARGET/container/src"
  mkdir -p "$src"
  find "$src" -mindepth 1 -maxdepth 1 ! -name '.php' -exec rm -rf -- {} +
  {
    printf '%s\n' '<?php' ''
    printf "%s\n" "\$siteName = '$name';" ''
    printf '%s\n' "echo '<!DOCTYPE html>';" \
      "echo '<html lang=\"en\">';" \
      "echo '<head><meta charset=\"utf-8\"><title>' . htmlspecialchars(\$siteName, ENT_QUOTES) . '</title></head>';" \
      "echo '<body>';" \
      "echo '<h1>' . htmlspecialchars(\$siteName, ENT_QUOTES) . '</h1>';" \
      "echo '<p>Drop the pages of the new site into container/src.</p>';" \
      "echo '</body>';" \
      "echo '</html>';"
  } > "$src/index.php"

  echo "Writing container/site-profile.env ..."
  cp "$TARGET/container/site-profile.env.example" "$TARGET/container/site-profile.env"
  printf 'SITE_NAME="%s"\n' "$name" >> "$TARGET/container/site-profile.env"

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

Next, in $TARGET:
  1. edit container/site-profile.env (hosts, language folders, page classes);
  2. put the pages of the site into container/src;
  3. container/tools/new-project.sh --check
  4. git add -- <paths> && git commit -m "<subject>"
NEXT
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

if [ "$CHECK" -eq 1 ]; then
  check
else
  bootstrap
fi
