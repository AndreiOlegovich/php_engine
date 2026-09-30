#!/usr/bin/env bash
# Copy a selected file set from the dev site tree to the prod site tree
# with shadow backup (same convention as copy_with_shadow_backup.sh).
#
# Run ON the server, where these paths exist:
#   ./copy_selected_dev_to_prod.sh [--duplicate-check full|size] [dev_dir] [prod_dir]
#
# Defaults:
#   dev_dir   /home/user/dev/public_html
#   prod_dir  /home/user/example.com/public_html
#   backup    <prod_parent>/backup_public_html (sibling of public_html)
#
# Behavior per file (rel path):
#   - missing in dev   -> report, skip
#   - missing in prod  -> copy
#   - identical        -> skip
#   - differs          -> backup old prod file, then overwrite with dev file
#
# Change detection (two layers, byte-exact): file sizes first (cheap
# stat short-circuit), then full content with cmp -s, so even same-size
# edits are caught.
#   --duplicate-check full  size + content (default; catches same-size edits)
#   --duplicate-check size  size only (faster on huge trees; same-size
#                           edits are MISSED and stay stale in prod)
set -euo pipefail

DUPLICATE_CHECK="full"
POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --duplicate-check)
      [ $# -ge 2 ] || { echo "ERROR: --duplicate-check needs a value (full|size)." >&2; exit 1; }
      DUPLICATE_CHECK="$2"; shift 2 ;;
    --duplicate-check=*) DUPLICATE_CHECK="${1#--duplicate-check=}"; shift ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
      exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 1 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
case "$DUPLICATE_CHECK" in
  full|size) ;;
  *) echo "ERROR: --duplicate-check must be full or size (got '$DUPLICATE_CHECK')." >&2; exit 1 ;;
esac

# Optional shared settings (site.env); positional dirs win over it.
# See site.env.example.
if [ -n "${SITE_ENV:-}" ] && [ "${#POSITIONAL[@]}" -eq 0 ]; then
  [ -f "$SITE_ENV" ] || { echo "Error: SITE_ENV file '$SITE_ENV' not found." >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
fi

DEV="${POSITIONAL[0]:-${DEV_DOCROOT:-/home/user/dev/public_html}}"
PROD="${POSITIONAL[1]:-${PROD_DOCROOT:-/home/user/example.com/public_html}}"
BACKUP="$(dirname "$PROD")/backup_public_html"

[ -d "$DEV" ] || { echo "Error: dev dir '$DEV' not found." >&2; exit 1; }
mkdir -p "$PROD" "$BACKUP"

COPIED=0; SKIPPED=0; BACKED_UP=0; MISSING=0

# Same files_differ() as in copy_with_shadow_backup.sh (duplicated on
# purpose: these files run standalone on hosting, no shared library).
DIFF_WHY=""
files_differ() {
  local src="$1" dst="$2" ssize dsize
  ssize=$(stat -c%s "$src" 2>/dev/null) || { DIFF_WHY="cannot stat source"; return 0; }
  dsize=$(stat -c%s "$dst" 2>/dev/null) || { DIFF_WHY="cannot stat dest"; return 0; }
  if [ "$ssize" != "$dsize" ]; then
    DIFF_WHY="size differs (src ${ssize} B, dst ${dsize} B)"
    return 0
  fi
  if [ "$DUPLICATE_CHECK" = "size" ]; then
    return 1  # size-only mode: equal size counts as identical
  fi
  if cmp -s "$src" "$dst" 2>/dev/null; then
    return 1
  fi
  DIFF_WHY="content differs (same size ${ssize} B)"
  return 0
}

while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in \#*) continue ;; esac
  if [ ! -f "$DEV/$f" ]; then
    echo "[missing-in-dev] $f"
    MISSING=$((MISSING+1))
    continue
  fi
  if [ -f "$PROD/$f" ] && ! files_differ "$DEV/$f" "$PROD/$f"; then
    echo "[skip] $f (identical)"
    SKIPPED=$((SKIPPED+1))
    continue
  fi
  if [ -f "$PROD/$f" ]; then
    mkdir -p "$BACKUP/$(dirname "$f")"
    cp -p "$PROD/$f" "$BACKUP/$f"
    BACKED_UP=$((BACKED_UP+1))
    echo "[backup] $f -> $BACKUP/$f ($DIFF_WHY)"
  fi
  mkdir -p "$PROD/$(dirname "$f")"
  cp -p "$DEV/$f" "$PROD/$f"
  COPIED=$((COPIED+1))
  echo "[copied] $f"
done <<'EOF'
index.php
about/index.php
blog/first-post.php
# ^ example paths — replace with your own file list

EOF

echo "---"
echo "Copied: $COPIED, Skipped (identical): $SKIPPED, Backed up: $BACKED_UP, Missing in dev: $MISSING"
echo "Backup dir: $BACKUP"
echo "Done."
