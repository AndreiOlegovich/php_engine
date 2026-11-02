#!/usr/bin/env bash
# version 0.0.2
# Validate src/sitemap.xml (or the file given as $1):
#   - parses as XML,
#   - contains exactly one XML declaration and one <urlset> document,
#   - has no duplicate <loc> entries.
#
# Guards against the Oct 2026 incident, when a second full sitemap document
# got concatenated onto the worktree file by a concurrent writer. The
# pre-commit check-xml hook blocks committing such damage; this script
# catches it earlier, between commits.
#
# Suggested schedule (install with crontab -e):
#   */15 * * * * cd /path/to/checkout && container/tools/check-sitemap.sh >> /tmp/opencode/sitemap-check.log 2>&1
#
# Usage:
#   ./check-sitemap.sh [path/to/sitemap.xml]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="${1:-$SCRIPT_DIR/../src/sitemap.xml}"

[ -f "$FILE" ] || { echo "FAIL: file not found: $FILE" >&2; exit 1; }

python3 - "$FILE" <<'EOF'
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

decls = text.count("<?xml")
if decls != 1:
    sys.exit(f"FAIL: {path} has {decls} XML declarations (want exactly 1)")

try:
    ElementTree.fromstring(text)
except ElementTree.ParseError as error:
    sys.exit(f"FAIL: {path} does not parse as XML: {error}")

locs = re.findall(r"<loc>([^<]+)</loc>", text)
dupes = sorted(loc for loc, count in Counter(locs).items() if count > 1)
if dupes:
    sys.exit(f"FAIL: {len(dupes)} duplicate <loc> entries, e.g. {dupes[:5]}")

print(f"OK: {path} valid XML, single document, {len(locs)} urls, no duplicates")
EOF
