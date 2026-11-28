#!/usr/bin/env bash
# version 0.0.12
# Regenerate a sitemap from the .php files under src/, in the same format
# as src/sitemap.xml:
#
#   <url>
#     <loc>https://www.example.com/qa/</loc>
#     <lastmod>2021-05-23T17:03:30+00:00</lastmod>
#     <changefreq>monthly</changefreq>
#     <priority>0.90</priority>
#   </url>
#
# Usage:
#   ./generate-sitemap.sh [src_dir] [output.xml]
#   ./generate-sitemap.sh --help
#   BASE_URL=https://www.example.com ./generate-sitemap.sh
#
# Default output is src/sitemap.xml (the single sitemap). The previous
# output is backed up to <repo>/backup/<name>.bak-<UTC timestamp> first
# (backup/ is git-ignored, so a backup never lands in the docroot, where a
# deploy would publish it). The new one is
# built in <output>.tmp.<pid> and renamed over <output> only once it is
# complete and valid XML: readers (tests, backoffice, local site, deploy)
# never see a half-written file, and a failed run keeps the old sitemap.
# Single-writer rule: never hand-merge sitemap dumps —
# a concatenated second document breaks XML validity. All sitemap edits go
# through this script; re-validate afterwards with
# container/tools/check-sitemap.sh.
# Exclusions are not in this script: every prune, directory name and file
# name rule is read from sitemap-excludes.conf next to it (prune / dirname /
# filename lines). Files holding the draft marker (SITE_DRAFT_MARKER, default
# "draft") are skipped wherever they live; deploy-ftp.sh skips the same files.
#
# Mapping (relative to src_dir):
#   index.php             -> BASE_URL/                    priority 1.00
#   <dir>/index.php       -> BASE_URL/<dir>/              priority 0.90
#   <dir>/page.php        -> BASE_URL/<dir>/page.php      priority 0.80
#
# Skipped by default (see the rules in sitemap-excludes.conf): dot- and
# underscore-prefixed folders and files (framework, includes), archive and
# duplicate folders, demo folders, page fragments and helper classes.
#
# lastmod comes from the file mtime (UTC, +00:00 like the original).
# changefreq is always 'monthly', like the original.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # tools live in tools/; project root (src/) is the runtime cwd

usage() {
  sed -n '2,/^set -euo/p' "$SCRIPT_DIR/generate-sitemap.sh" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

SRC_DIR="${1:-src}"
OUT="${2:-$SRC_DIR/sitemap.xml}"
BASE_URL="${BASE_URL:-https://www.example.com}"
BASE_URL="${BASE_URL%/}"  # no trailing slash

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }

if [ -f "$OUT" ]; then
  BACKUP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/backup"
  mkdir -p "$BACKUP_DIR"
  BACKUP="$BACKUP_DIR/$(basename "$OUT").bak-$(date -u +%Y%m%d%H%M%S)"
  cp "$OUT" "$BACKUP"
  echo "backup: $BACKUP" >&2
fi

TMP_OUT="$OUT.tmp.$$"
trap 'rm -f -- "$TMP_OUT"' EXIT

xml_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
      -e "s/'/\&apos;/g" -e 's/"/\&quot;/g'
}

url_encode_spaces() { sed 's/ /%20/g'; }

{
echo '<?xml version="1.0" encoding="UTF-8"?>'
echo '<urlset'
echo '      xmlns="https://www.sitemaps.org/schemas/sitemap/0.9"'
echo '      xmlns:xsi="https://www.w3.org/2001/XMLSchema-instance"'
echo '      xsi:schemaLocation="https://www.sitemaps.org/schemas/sitemap/0.9'
echo '            https://www.sitemaps.org/schemas/sitemap/0.9/sitemap.xsd">'

count=0

# Exclusions live in sitemap-excludes.conf, one rule per line:
#   prune PATH      a path relative to src, pruned with its subtree
#   dirname GLOB    a directory name that is never descended
#   filename GLOB   a file name that is never listed
# A site fills in its own rules there; the script stays generic.
PRUNE=(); SKIP_DIRS=(); SKIP_FILES=()
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  kind="${line%% *}"; value="${line#* }"
  case "$kind" in
    prune) PRUNE+=("$SRC_DIR/$value") ;;
    dirname) SKIP_DIRS+=("$value") ;;
    filename) SKIP_FILES+=("$value") ;;
    *) echo "WARNING: unknown rule in sitemap-excludes.conf: $line" >&2 ;;
  esac
done < "$SCRIPT_DIR/sitemap-excludes.conf"

FIND_ARGS=(-mindepth 1)
for p in "${PRUNE[@]}"; do FIND_ARGS+=(-path "$p" -prune -o); done
if [ "${#SKIP_DIRS[@]}" -gt 0 ]; then
  FIND_ARGS+=(-type d \()
  first=1
  for n in "${SKIP_DIRS[@]}"; do
    if [ "$first" -eq 1 ]; then first=0; else FIND_ARGS+=(-o); fi
    FIND_ARGS+=(-name "$n")
  done
  FIND_ARGS+=(\) -prune -o)
fi
FIND_ARGS+=(-type f -name "*.php")
for n in "${SKIP_FILES[@]}"; do FIND_ARGS+=(! -name "$n"); done
DRAFT_MARKER="${SITE_DRAFT_MARKER:-draft}"
while IFS= read -r file; do
  rel="${file#"$SRC_DIR"/}"
  base="$(basename "$rel")"

  if [[ "$rel" == "index.php" ]]; then
    loc="$BASE_URL/"
    prio="1.00"
  elif [[ "$base" == "index.php" ]]; then
    loc="$BASE_URL/${rel%/index.php}/"
    prio="0.90"
  else
    loc="$BASE_URL/$rel"
    prio="0.80"
  fi
  loc="$(printf '%s' "$loc" | url_encode_spaces | xml_escape)"
  lastmod="$(date -u -r "$file" +"%Y-%m-%dT%H:%M:%S+00:00")"

  echo "<url>"
  echo "  <loc>$loc</loc>"
  echo "  <lastmod>$lastmod</lastmod>"
  echo "  <changefreq>monthly</changefreq>"
  echo "  <priority>$prio</priority>"
  echo "</url>"
  count=$((count + 1))
done < <(LC_ALL=C find -P "$SRC_DIR" "${FIND_ARGS[@]}" -print0 \
  | xargs -0 grep -L -Z -F -e "$DRAFT_MARKER" -- | tr '\0' '\n' | LC_ALL=C sort)

echo "</urlset>"
total=$(find -P "$SRC_DIR" -type f -name "*.php" | wc -l)
echo "Wrote $count URLs to $OUT ($((total - count)) skipped) " >&2
} > "$TMP_OUT"

# Validate XML
if python3 -c 'import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' "$TMP_OUT" 2>/dev/null; then
  echo "XML valid." >&2
else
  echo "ERROR: generated sitemap is not valid XML; $OUT left unchanged." >&2
  exit 1
fi
mv -f -- "$TMP_OUT" "$OUT"

# The search export reads $SRC_DIR/sitemap.xml. Rebuild it only when that is
# the file just written: with a custom output path site_index.py would read the
# old sitemap and overwrite the export with stale data. A failed export fails
# the run; a skipped one leaves the previous export alone.
if [ "$OUT" = "$SRC_DIR/sitemap.xml" ] && [ -f "$SCRIPT_DIR/search/site_index.py" ]; then
  if ! python3 "$SCRIPT_DIR/search/site_index.py" --src "$SRC_DIR" --search "$SRC_DIR/search"; then
    echo "ERROR: the search export could not be rebuilt; $OUT is in place but search data is stale." >&2
    exit 1
  fi
else
  echo "search export skipped: $OUT is not $SRC_DIR/sitemap.xml, so site_index.py would read the old sitemap." >&2
fi
