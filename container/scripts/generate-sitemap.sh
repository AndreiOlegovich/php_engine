#!/usr/bin/env bash
# Regenerate a sitemap from the .php files under src/, in the same format
# as src/sitemap.xml:
#
#   <url>
#     <loc>https://www.aredel.com/qa/</loc>
#     <lastmod>2021-05-23T17:03:30+00:00</lastmod>
#     <changefreq>monthly</changefreq>
#     <priority>0.90</priority>
#   </url>
#
# Usage:
#   ./generate-sitemap.sh [src_dir] [output.xml]
#   BASE_URL=https://www.aredel.com ./generate-sitemap.sh
#
# Mapping (relative to src_dir):
#   index.php             -> BASE_URL/                    priority 1.00
#   <dir>/index.php       -> BASE_URL/<dir>/              priority 0.90
#   <dir>/page.php        -> BASE_URL/<dir>/page.php      priority 0.80
#
# Skipped (not public pages; directories are pruned, never descended):
#   - dot-/underscore-prefixed directories: framework (ao/.php, ao/.css),
#     backend (ao/_master/ with csv import, init_table, logins)
#   - arj directories (archive, e.g. edu/nucl/arj, chessboard/arj2..arj7)
#   - *img directories (image assets: img, aa_img, db_img, ...)
#   - symlinked dirs are never descended either (find -P)
#   - dot-/underscore-prefixed files (hidden files, includes like _footer.php)
#   - RelatedArticles*.php (helper classes, not pages)
#   - *_inc.php (includes), *_arj.php (archive/dev variants like index_arj.php)
#   - *_module_*.php (module fragments, e.g. blog h_module_*.php)
#   - '* copy.php' (editor duplicates like 'dict copy.php')
#
# lastmod comes from the file mtime (UTC, +00:00 like the original).
# changefreq is always 'monthly', like the original.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # scripts live in scripts/; project root (src/) is the runtime cwd

SRC_DIR="${1:-src}"
OUT="${2:-$SRC_DIR/sitemap_new.xml}"
BASE_URL="${BASE_URL:-https://www.aredel.com}"
BASE_URL="${BASE_URL%/}"  # no trailing slash

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }

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
done < <(LC_ALL=C find -P "$SRC_DIR" -mindepth 1 \
  -type d \( -name ".*" -o -name "_*" -o -name "arj" -o -name "arj[0-9]*" -o -name "*img" \) -prune -o \
  -type f -name "*.php" \
    ! -name ".*" ! -name "_*" \
    ! -name "*RelatedArticles*.php" \
    ! -name "*_inc.php" ! -name "*_arj.php" \
    ! -name "*_module_*.php" \
    ! -name "* copy.php" -print | LC_ALL=C sort)

echo "</urlset>"
total=$(find -P "$SRC_DIR" -type f -name "*.php" | wc -l)
echo "Wrote $count URLs to $OUT ($((total - count)) skipped) " >&2
} > "$OUT"

# Validate XML
if python3 -c "import xml.dom.minidom,sys; xml.dom.minidom.parse('$OUT')" 2>/dev/null; then
  echo "XML valid." >&2
else
  echo "ERROR: $OUT is not valid XML." >&2
  exit 1
fi
