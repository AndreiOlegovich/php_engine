#!/usr/bin/env bash
# version 0.0.1
# Delete files listed in delete_on_hosting.txt, backing each one up first.
# Thin companion to copy-with-shadow-backup.sh (which never deletes).
#
# Usage (run ON the server, where these paths exist):
#   ./delete-from-hosting.sh [--dry-run] [list_file] [docroot] [backup_dir]
#
#   list_file   one absolute path per line; "#" lines and blanks skipped
#               (default: ./delete_on_hosting.txt next to this script).
#   docroot     tree the files are deleted from
#               (default: $PROD_DOCROOT or /home/user/example.com/public_html).
#   backup_dir  where removed files are saved, mirroring the docroot
#               skeleton (default: <docroot-parent>/backup_deleted_html).
#
# Behavior per listed path:
#   - outside docroot      -> ERROR, skipped (never touch other trees).
#   - missing in docroot   -> report, skip (already gone).
#   - present              -> copy to backup dir (same rel path), verify
#                             with cmp, then delete the original.
#   --dry-run prints what would happen; touches nothing, writes nothing.
#
# Paths default from site.env (SITE_ENV) when set; built-ins below
# otherwise. See site.env.example.
set -euo pipefail

if [ -n "${SITE_ENV:-}" ]; then
  [ -f "$SITE_ENV" ] || { echo "ERROR: SITE_ENV file '$SITE_ENV' not found." >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
      exit 0 ;;
    -*) echo "ERROR: unknown option: $arg (see --help)." >&2; exit 1 ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done

LIST="${POSITIONAL[0]:-$SCRIPT_DIR/delete_on_hosting.txt}"
DEST="${POSITIONAL[1]:-${PROD_DOCROOT:-/home/user/example.com/public_html}}"
BACKUP="${POSITIONAL[2]:-$(dirname "$DEST")/backup_deleted_html}"

[ -f "$LIST" ] || { echo "ERROR: list file '$LIST' not found." >&2; exit 1; }
[ -d "$DEST" ] || { echo "ERROR: docroot '$DEST' is not a directory." >&2; exit 1; }
DEST="$(cd "$DEST" && pwd)"

DELETED=0
BACKED_UP=0
SKIPPED=0

while IFS= read -r line || [ -n "$line" ]; do
  # trim leading/trailing whitespace (paths never contain it)
  entry="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$entry" in
    ''|\#*) continue ;;
  esac
  # resolve list entry to a docroot-relative path
  case "$entry" in
    "$DEST"/*) rel="${entry#"$DEST"/}" ;;
    /*)
      echo "[refuse] $entry (outside docroot $DEST)" >&2
      SKIPPED=$((SKIPPED + 1))
      continue ;;
    *) rel="$entry" ;;
  esac
  target="$DEST/$rel"
  if [ ! -e "$target" ] && [ ! -L "$target" ]; then
    echo "[gone] $rel (not on hosting)"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  bfile="$BACKUP/$rel"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[would-delete] $rel (backup -> $bfile)"
    continue
  fi
  mkdir -p "$(dirname "$bfile")"
  if [ -L "$target" ]; then
    cp -P "$target" "$bfile"
  else
    cp -p "$target" "$bfile"
  fi
  if ! cmp -s "$target" "$bfile" 2>/dev/null && [ ! -L "$target" ]; then
    echo "[ERROR] backup mismatch for $rel, NOT deleting." >&2
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  rm -f "$target"
  BACKED_UP=$((BACKED_UP + 1))
  DELETED=$((DELETED + 1))
  echo "[deleted] $rel (backup -> $bfile)"
done < "$LIST"

echo "---"
echo "Docroot:   $DEST"
echo "Backup:    $BACKUP"
echo "Deleted: $DELETED, backed up: $BACKED_UP, skipped: $SKIPPED"
[ "$DRY_RUN" -eq 1 ] && echo "(dry run: nothing deleted, nothing written)"
echo "Done."
