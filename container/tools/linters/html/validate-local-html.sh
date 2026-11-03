#!/usr/bin/env bash
# version 0.1.0
# Validate locally served pages' HTML structure.
# Fetches BASE_URL + path, then runs html-validate (pinned v7: works on
# the stock Ubuntu 22.04 node 16; latest v8 needs node 22+).
# Project rules live next to this script (.htmlvalidate.json):
# recommended set minus house-style noise (no-inline-style,
# attr-delimiter, no-trailing-whitespace).
#
# Usage (from anywhere):
#   validate-local-html.sh /linux/debian/man/            # one URL path
#   validate-local-html.sh --files src/linux/debian/     # dir or file(s)
#   validate-local-html.sh --files a.php,b.php /other/   # union
#   BASE_URL=http://127.0.0.1:8080 validate-local-html.sh /dev/php/oop/
set -u

usage() { local self="$0"; sed -n '2,/^set -u/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -u'; }

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
FILES_LIST=()
URLS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --files)
      [ $# -ge 2 ] || { echo "ERROR: --files needs a value." >&2; exit 2; }
      FILES_LIST+=("$2"); shift 2 ;;
    --files=*) FILES_LIST+=("${1#--files=}"); shift ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) URLS+=("$1"); shift ;;
  esac
done

# --files entries (comma-separated, repeatable): files and dirs, repo-
# relative (src/...) or absolute; dirs expand to routable .php pages
# (index.php -> dir URL; RelatedArticles/*_inc/arj/copy skipped).
expand_files() {
  local tok part f
  for tok in "$@"; do
    IFS=',' read -ra PARTS <<< "$tok"
    for part in "${PARTS[@]}"; do
    [ -n "$part" ] || continue
    case "$part" in
      /*) f="$part" ;;
      *) f="$PWD/$part" ;;
    esac
    if [ -d "$f" ]; then
      find "$f" -type f -name "*.php" \
        ! -name "RelatedArticles.php" ! -name "*_inc.php" ! -name "*arj*" ! -name "* copy*" \
        | sort | while IFS= read -r pf; do
        rel_to_url "$pf"
      done
    elif [ -f "$f" ]; then
      rel_to_url "$f"
    else
      echo "WARNING: skipping '$part' (not found)." >&2
    fi
    done
  done
}

rel_to_url() {
  # $1 = abs .php path -> print URL path if routable under src/
  local ap="$1" u
  case "$ap" in
    */src/*) u="/${ap#*/src/}" ;;
    *) echo "WARNING: skipping '$1' (outside src/)." >&2; return 0 ;;
  esac
  case "$u" in */index.php) u="${u%index.php}";; esac
  printf '%s\n' "$u"
}

if [ "${#FILES_LIST[@]}" -gt 0 ]; then
  while IFS= read -r u; do
    [ -n "$u" ] && URLS+=("$u")
  done < <(expand_files "${FILES_LIST[@]}")
fi
if [ "${#URLS[@]}" -eq 0 ]; then
  URLS=("/")
fi

FAILED=0
for PATH_ARG in "${URLS[@]}"; do
  TMP="$(mktemp --suffix=.html)"
  echo "== $PATH_ARG"
  if ! curl -sSL --max-time 60 "${BASE_URL}${PATH_ARG}" -o "$TMP"; then
    echo "ERROR: fetch failed: ${BASE_URL}${PATH_ARG}" >&2
    rm -f "$TMP"
    FAILED=1
    continue
  fi
  if npx --yes html-validate@7 --config "$ROOT/.htmlvalidate.json" "$TMP"; then
    echo "OK: $PATH_ARG"
  else
    FAILED=1
  fi
  rm -f "$TMP"
done
trap - EXIT
exit "$FAILED"
