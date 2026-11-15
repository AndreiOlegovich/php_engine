#!/usr/bin/env bash
# version 0.2.0
# Deploy container/src to the the hosting provider docroot over SSH with rsync.
#
# Uploads the CONTENTS of container/src into the docroot (~/example.com/),
# never a src/ folder inside it. What goes up: the files git tracks, plus
# image files (png, jpg, jpeg, gif, bmp, webp, svg, ico, tif, tiff, avif, any
# case), which are git-ignored but needed by the pages. Untracked or ignored
# files of any other kind (notes, backups, session files) never go up.
# Skips what deploy-ftp.sh skips: logs, databases, owner device data,
# backups, junk dirs and draft pages (${SITE_DRAFT_MARKER:-draft} in the first 4 KB).
# Files deleted locally stay on the host; nothing is ever deleted there.
# Modes on the host: folders 755, files 644.
#
# Usage:
#   ./deploy-ssh.sh [-n|--dry-run] [-c|--checksum] [-v|--verbose]
#                   [--host TARGET] [--key PATH] [--remote-dir DIR]
#                   [--config PATH] [-h|--help]
#
#   -n, --dry-run    show what would change, change nothing
#   -c, --checksum   compare files by content, not by size and time
#                    (slower; use it once when the host copy has other mtimes)
#   -v, --verbose    one line per changed file (rsync --itemize-changes)
#   --host TARGET    user@server or a ~/.ssh/config alias
#                    (default: HOSTING_SSH)
#   --key PATH       SSH private key (default: HOSTING_KEY, else
#                    ~/.ssh/hosting-upload when it exists)
#   --remote-dir DIR docroot relative to the remote home
#                    (default: HOSTING_DOCROOT, else example.com)
#   --config PATH    settings file (default: ~/.config/php_engine/hosting.env)
#
# Settings file, the same one fetch_hosting_logs.py reads; start from
# deploy-ssh.env.example next to this script:
#   HOSTING_SSH=<user>@<server>.example.com
#   HOSTING_KEY=~/.ssh/hosting-upload
#   HOSTING_DOCROOT=example.com
# Environment variables of the same names win over the file; flags win
# over both.
#
# Exit 0 on success, 1 on SSH or rsync errors, 2 on bad options or settings.
set -euo pipefail

_START_DIR="$(pwd -P)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/../../src" && pwd)"

CONFIG="${HOME}/.config/php_engine/hosting.env"
HOST_ARG=""
KEY_ARG=""
REMOTE_ARG=""
DRY_RUN=0
CHECKSUM=0
VERBOSE=0

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

need_value() { [ "$2" -ge 2 ] || { echo "ERROR: $1 needs a value." >&2; exit 2; }; }

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY_RUN=1; shift ;;
    -c|--checksum) CHECKSUM=1; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    --host) need_value "$1" $#; HOST_ARG="$2"; shift 2 ;;
    --host=*) HOST_ARG="${1#--host=}"; shift ;;
    --key) need_value "$1" $#; KEY_ARG="$2"; shift 2 ;;
    --key=*) KEY_ARG="${1#--key=}"; shift ;;
    --remote-dir) need_value "$1" $#; REMOTE_ARG="$2"; shift 2 ;;
    --remote-dir=*) REMOTE_ARG="${1#--remote-dir=}"; shift ;;
    --config) need_value "$1" $#; CONFIG="$2"; shift 2 ;;
    --config=*) CONFIG="${1#--config=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
  esac
done

# Last KEY=value line of the settings file, quotes stripped; empty if none.
setting() {
  local line value
  [ -f "$CONFIG" ] || return 0
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$CONFIG" | tail -n 1)" || return 0
  value="${line#*=}"
  value="${value%"${value##*[![:space:]]}"}"
  value="${value#\"}"; value="${value%\"}"; value="${value#\'}"; value="${value%\'}"
  printf '%s' "$value"
}

expand_home() {
  local tilde='~'
  case "$1" in
    "$tilde") printf '%s' "$HOME" ;;
    "$tilde"/*) printf '%s/%s' "$HOME" "${1:2}" ;;
    *) printf '%s' "$1" ;;
  esac
}

TARGET="${HOST_ARG:-${HOSTING_SSH:-$(setting HOSTING_SSH)}}"
KEY="${KEY_ARG:-${HOSTING_KEY:-$(setting HOSTING_KEY)}}"
REMOTE_DIR="${REMOTE_ARG:-${HOSTING_DOCROOT:-$(setting HOSTING_DOCROOT)}}"
REMOTE_DIR="${REMOTE_DIR:-example.com}"

[ -n "$TARGET" ] || { echo "ERROR: no SSH target. Pass --host user@server, or set HOSTING_SSH in $CONFIG." >&2; exit 2; }
if [ -n "$KEY" ]; then
  KEY="$(expand_home "$KEY")"
  [ -f "$KEY" ] || { echo "ERROR: SSH key $KEY not found." >&2; exit 2; }
elif [ -f "$HOME/.ssh/hosting-upload" ]; then
  KEY="$HOME/.ssh/hosting-upload"
fi

# The docroot is a plain relative folder: no "/", "..", or a src/ below it.
REMOTE_DIR="${REMOTE_DIR%/}"
case "/$REMOTE_DIR/" in
  //|*/../*|*/./*|*/src/) echo "ERROR: refusing remote dir '$REMOTE_DIR'." >&2; exit 2 ;;
esac
[ -f "$SRC/index.php" ] && [ -f "$SRC/.htaccess" ] || { echo "ERROR: $SRC does not look like the site root." >&2; exit 2; }

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)
[ -n "$KEY" ] && SSH+=(-i "$KEY" -o IdentitiesOnly=yes)
SSH_CMD="$(printf '%q ' "${SSH[@]}")"

# Same skips as deploy-ftp.sh (BASE_EXCLUDE_DIRS, TMP_EXCLUDE_DIRS, the -arj
# dirs, BASE_EXCLUDE_FILES, EXCLUDE_PATH_PREFIXES); keep the two in step.
# The files are named one by one (--files-from), and rsync then checks each
# path on its own: an excluded folder does not hide the files below it, so
# every folder rule comes twice, the folder and "folder/**".
EXCLUDE_DIRS=(
  /services/user_profile/data
  reports allure-results allure-results-host __pycache__ .pytest_cache .git .venv
  node_modules _prototypes tmp _tmp .tmp '*-arj' '*_arj' '*.arj'
)
EXCLUDES=()
for d in "${EXCLUDE_DIRS[@]}"; do EXCLUDES+=("--exclude=$d/" "--exclude=$d/**"); done
EXCLUDES+=(
  '--exclude=*.log' '--exclude=*.db' '--exclude=*.sqlite' '--exclude=*.sqlite3' '--exclude=*.db-journal'
  '--exclude=sess_*' '--exclude=* copy*' '--exclude=*.bak' '--exclude=*.orig' '--exclude=*~'
  '--exclude=*.swp' '--exclude=*.swo' '--exclude=.DS_Store' '--exclude=Thumbs.db'
)

git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "ERROR: $SRC is not in a git work tree; the upload list comes from git." >&2; exit 2; }

DRAFTS="$(mktemp)"
LIST="$(mktemp)"
trap 'rm -f "$DRAFTS" "$LIST"' EXIT

# Draft pages: the marker in the first 4 KB, as deploy-ftp.sh checks it.
(cd "$SRC" && grep -rl --include='*.php' --include='*.html' "${SITE_DRAFT_MARKER:-draft}" . 2>/dev/null || true) |
  while IFS= read -r f; do
    head4k="$(head -c 4096 "$SRC/$f")"
    [[ "$head4k" == *${SITE_DRAFT_MARKER:-draft}* ]] && printf '%s\n' "${f#./}"
  done > "$DRAFTS" || true

# Upload list, NUL-separated, paths relative to src: tracked files plus
# images, minus drafts and minus tracked files deleted from disk.
{
  git -C "$SRC" ls-files -z
  (cd "$SRC" && find . -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \
    -o -iname '*.gif' -o -iname '*.bmp' -o -iname '*.webp' -o -iname '*.svg' -o -iname '*.ico' \
    -o -iname '*.tif' -o -iname '*.tiff' -o -iname '*.avif' \) -printf '%P\0')
} | sort -zu | { grep -z -v -x -F -f "$DRAFTS" || true; } |
  while IFS= read -r -d '' f; do
    if [ -f "$SRC/$f" ] || [ -L "$SRC/$f" ]; then printf '%s\0' "$f"; fi
  done > "$LIST"
TRACKED="$(git -C "$SRC" ls-files | wc -l)"
echo "Upload list: $(tr -cd '\0' < "$LIST" | wc -c) files ($TRACKED tracked, plus untracked images, minus $(wc -l < "$DRAFTS") draft pages); junk excludes apply on top"

# A typo in the remote dir must not start a second site: it has to exist
# and hold index.php already.
if ! "${SSH[@]}" "$TARGET" "test -f $(printf '%q' "$REMOTE_DIR")/index.php"; then
  echo "ERROR: $REMOTE_DIR/index.php not found on the host (or SSH failed); not uploading." >&2
  exit 1
fi

# --files-from turns off the recursion of -a: only the listed files go up.
RSYNC=(rsync -a -h --stats '--chmod=D755,F644' -e "$SSH_CMD" "${EXCLUDES[@]}" --from0 "--files-from=$LIST")
[ "$DRY_RUN" -eq 1 ] && RSYNC+=(--dry-run)
[ "$CHECKSUM" -eq 1 ] && RSYNC+=(--checksum)
[ "$VERBOSE" -eq 1 ] && RSYNC+=(--itemize-changes)

echo "Uploading $SRC/ to $REMOTE_DIR/ on the host$([ "$DRY_RUN" -eq 1 ] && echo ' (dry run)')"
# Trailing slashes on both sides: copy the contents of src, not src itself.
"${RSYNC[@]}" "$SRC/" "$TARGET:$REMOTE_DIR/" || { echo "ERROR: rsync failed." >&2; exit 1; }
