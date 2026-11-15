#!/usr/bin/env bash
# version 0.1.0
# Delete prod files that are missing on dev: dev is the source of truth, and
# the copy scripts only add or overwrite, so a file removed from dev stays on
# prod until this script removes it.
#
# Usage (run ON the server, where these paths exist):
#   ./delete-prod-missing-on-dev.sh --dry-run     list the candidates only
#   ./delete-prod-missing-on-dev.sh               delete them (with backup)
#
# Options:
#   --dry-run          list every delete candidate; delete nothing.
#   --max N            refuse a real run with more than N candidates
#                      (default 300), so a half-empty dev cannot empty prod.
#                      Review the dry-run list, then raise it if it is right.
#   --exclude-file F   more paths to keep: one shell glob per line, relative
#                      to the docroot (e.g. promo/*); "#" lines are skipped.
#   [dev_dir] [prod_dir]  default: DEV_DOCROOT / PROD_DOCROOT from site.env
#                      (SITE_ENV), else the dev and example.com docroots.
#
# Every run, dry or not, writes the candidates, one absolute prod path per
# line, to delete_candidates/<YYYYmmdd-HHMMSS>.txt next to this script. A
# real run passes that list to delete-from-hosting.sh (same folder), which
# saves each file into <prod parent>/backup_deleted_html before deleting it,
# and then removes the prod folders that are missing on dev and now empty.
#
# Always kept, prod-only by design: services/user_profile/data/,
# .well-known/, cgi-bin/, *.log, PHP sessions (sess_*), tmp/ folders,
# search-console verification files (google*.html, yandex_*.html),
# .user.ini and .htpasswd.
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
MAX=300
EXCLUDE_FILE=""
POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --max) [ $# -ge 2 ] || { echo "ERROR: --max needs a number." >&2; exit 2; }
      case "$2" in ''|*[!0-9]*) echo "ERROR: --max needs a number, got '$2'." >&2; exit 2 ;; esac
      MAX="$2"; shift 2 ;;
    --exclude-file) [ $# -ge 2 ] || { echo "ERROR: --exclude-file needs a file." >&2; exit 2; }
      EXCLUDE_FILE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
      exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

DEV="${POSITIONAL[0]:-${DEV_DOCROOT:-/home/user/dev/public_html}}"
PROD="${POSITIONAL[1]:-${PROD_DOCROOT:-/home/user/example.com/public_html}}"
[ -d "$DEV" ] || { echo "ERROR: dev docroot '$DEV' is not a directory." >&2; exit 1; }
[ -d "$PROD" ] || { echo "ERROR: prod docroot '$PROD' is not a directory." >&2; exit 1; }
# Logical paths, as delete-from-hosting.sh resolves its docroot.
DEV="$(cd "$DEV" && pwd)"
PROD="$(cd "$PROD" && pwd)"
case "$PROD/" in "$DEV/"*) echo "ERROR: prod '$PROD' is dev '$DEV' or inside it." >&2; exit 1 ;; esac
case "$DEV/" in "$PROD/"*) echo "ERROR: dev '$DEV' is inside prod '$PROD'." >&2; exit 1 ;; esac
# A dev tree without its front page and .htaccess is no source of truth.
if [ ! -f "$DEV/index.php" ] || [ ! -f "$DEV/.htaccess" ]; then
  echo "ERROR: $DEV has no index.php or .htaccess; refusing to compare against it." >&2
  exit 1
fi
DELETER="$SCRIPT_DIR/delete-from-hosting.sh"
if [ "$DRY_RUN" -eq 0 ] && [ ! -x "$DELETER" ]; then
  echo "ERROR: $DELETER not found or not executable; it does the backup and delete." >&2
  exit 1
fi

KEEP=(
  'services/user_profile/data/*'
  '.well-known/*'
  'cgi-bin/*'
  '*.log'
  'sess_*' '*/sess_*'
  'tmp/*' '*/tmp/*'
  'google*.html' 'yandex_*.html'
  '.user.ini' '*/.user.ini'
  '.htpasswd' '*/.htpasswd'
)
if [ -n "$EXCLUDE_FILE" ]; then
  [ -f "$EXCLUDE_FILE" ] || { echo "ERROR: exclude file '$EXCLUDE_FILE' not found." >&2; exit 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    case "$line" in ''|\#*) continue ;; esac
    KEEP+=("$line")
  done < "$EXCLUDE_FILE"
fi

# Prints the first keep pattern that matches a docroot-relative path.
keep_pattern() {
  local pattern
  for pattern in "${KEEP[@]}"; do
    # shellcheck disable=SC2053  # the right side is a glob on purpose
    if [[ "$1" == $pattern ]]; then
      printf '%s' "$pattern"
      return 0
    fi
  done
  return 1
}

STAMP="$(date +%Y%m%d-%H%M%S)"
LIST_DIR="$SCRIPT_DIR/delete_candidates"
mkdir -p "$LIST_DIR"
LIST="$LIST_DIR/$STAMP.txt"
TMP_LIST="$(mktemp "$LIST_DIR/.$STAMP.XXXXXX")"
trap 'rm -f -- "$TMP_LIST"' EXIT
{
  echo "# Prod files missing on dev, $STAMP ($([ "$DRY_RUN" -eq 1 ] && echo "dry run" || echo "real run"))"
  echo "# dev:  $DEV"
  echo "# prod: $PROD"
} > "$TMP_LIST"

CANDIDATES=0
UNUSUAL=0
declare -A KEPT_BY=()
while IFS= read -r -d '' path; do
  rel="${path#"$PROD"/}"
  if [ -e "$DEV/$rel" ] || [ -L "$DEV/$rel" ]; then
    continue
  fi
  if pattern="$(keep_pattern "$rel")"; then
    KEPT_BY["$pattern"]=$(( ${KEPT_BY["$pattern"]:-0} + 1 ))
    continue
  fi
  # delete-from-hosting.sh reads one path per line and trims the ends.
  case "$rel" in
    *$'\n'*|*$'\r'*|*[[:space:]])
      UNUSUAL=$((UNUSUAL + 1))
      echo "[skip] unusual file name, delete by hand if needed: ${rel//[$'\n\r']/?}"
      continue ;;
  esac
  CANDIDATES=$((CANDIDATES + 1))
  echo "[candidate] $rel"
  printf '%s\n' "$PROD/$rel" >> "$TMP_LIST"
done < <(find "$PROD" \( -type f -o -type l \) -print0 | sort -z)
mv -f -- "$TMP_LIST" "$LIST"
trap - EXIT

# Prod folders missing on dev, deepest first: removed once empty.
FOLDERS=()
while IFS= read -r -d '' path; do
  rel="${path#"$PROD"/}"
  [ -d "$DEV/$rel" ] && continue
  keep_pattern "$rel/" >/dev/null && continue
  FOLDERS+=("$rel")
done < <(find "$PROD" -mindepth 1 -depth -type d -print0)

for pattern in "${!KEPT_BY[@]}"; do
  echo "[keep] ${KEPT_BY[$pattern]} file(s) matching $pattern"
done
for rel in "${FOLDERS[@]}"; do
  echo "[folder] $rel/ (missing on dev; removed if empty after the deletion)"
done

echo "---"
echo "Dev:        $DEV"
echo "Prod:       $PROD"
echo "Candidates: $CANDIDATES (list: $LIST)"
[ "$UNUSUAL" -eq 0 ] || echo "Skipped:    $UNUSUAL unusual file name(s), see [skip] lines"

if [ "$DRY_RUN" -eq 1 ]; then
  [ "$CANDIDATES" -le "$MAX" ] || echo "Note: $CANDIDATES candidates exceed --max $MAX; a real run will need --max $CANDIDATES."
  echo "(dry run: nothing deleted)"
  exit 0
fi
if [ "$CANDIDATES" -gt "$MAX" ]; then
  echo "ERROR: $CANDIDATES candidates exceed --max $MAX. Review $LIST, then rerun with --max $CANDIDATES." >&2
  exit 1
fi
if [ "$CANDIDATES" -gt 0 ]; then
  "$DELETER" "$LIST" "$PROD"
fi
REMOVED_FOLDERS=0
for rel in "${FOLDERS[@]}"; do
  if rmdir -- "$PROD/$rel" 2>/dev/null; then
    REMOVED_FOLDERS=$((REMOVED_FOLDERS + 1))
    echo "[removed-folder] $rel/"
  fi
done
echo "Empty folders removed: $REMOVED_FOLDERS"
echo "Done."
