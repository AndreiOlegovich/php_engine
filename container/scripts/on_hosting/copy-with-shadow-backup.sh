#!/usr/bin/env bash
# Copy source tree into destination with optional forced overwrite + shadow backup.
# Usage:
#   ./copy_with_shadow_backup.sh [-f|--force] [--duplicate-check full|size] <source_dir> <dest_dir>
#   ./copy_with_shadow_backup.sh <source_dir> <dest_dir> [-f|--force]
#
# Without -f: copy only new files/dirs, never overwrite existing files.
# With -f:    overwrite differing files in DEST, but first save the old
#             DEST version into backup_public_html (skeleton created by
#             shadow_backup_create if missing).
# Change detection (two layers, byte-exact): compares file sizes first
# (cheap stat short-circuit), then full content with cmp -s, so even
# same-size edits are caught. Symlinks are compared by target content.
#   --duplicate-check full  size + content (default; catches same-size edits)
#   --duplicate-check size  size only (faster on huge trees; same-size
#                           edits are MISSED and stay stale in DEST)
set -euo pipefail

FORCE=0
DUPLICATE_CHECK="full"
POSITIONAL=()

usage() {
  echo "Usage: $0 [-f|--force] [--duplicate-check full|size] <source_dir> <dest_dir>" >&2
  echo "  <source_dir>  dir to copy FROM (its CONTENTS go into dest)" >&2
  echo "  <dest_dir>    dir to copy TO (usually public_html or subdir)" >&2
  echo "  -f/--force    overwrite existing files (backup old versions first)" >&2
  echo "  --duplicate-check MODE  full (default) or size-only, see above" >&2
  echo "Example: $0 ./new_site /var/www/mysite/public_html" >&2
  echo "         $0 -f ./new_site /var/www/mysite/public_html" >&2
  echo "         $0 -f --duplicate-check size ./new_site /var/www/mysite/public_html" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    -f|--force) FORCE=1; shift ;;
    --duplicate-check)
      [ $# -ge 2 ] || { echo "ERROR: --duplicate-check needs a value (full|size)." >&2; usage; exit 1; }
      DUPLICATE_CHECK="$2"; shift 2 ;;
    --duplicate-check=*) DUPLICATE_CHECK="${1#--duplicate-check=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

case "$DUPLICATE_CHECK" in
  full|size) ;;
  *) echo "ERROR: --duplicate-check must be full or size (got '$DUPLICATE_CHECK')." >&2; usage; exit 1 ;;
esac

if [ "${#POSITIONAL[@]}" -ne 2 ]; then
  usage
  exit 1
fi

SRC="${POSITIONAL[0]}"
DEST="${POSITIONAL[1]}"

if [ ! -d "$SRC" ]; then
  echo "Error: source '$SRC' is not a directory." >&2
  exit 1
fi

SRC="$(cd "$SRC" && pwd)"
# DEST may not exist yet -> resolve parent + basename
if [ -d "$DEST" ]; then
  DEST="$(cd "$DEST" && pwd)"
else
  PARENT="$(dirname "$DEST")"
  BASE_D="$(basename "$DEST")"
  if [ ! -d "$PARENT" ]; then
    echo "Error: destination parent '$PARENT' does not exist." >&2
    exit 1
  fi
  PARENT="$(cd "$PARENT" && pwd)"
  DEST="$PARENT/$BASE_D"
fi

if [ "$SRC" = "$DEST" ]; then
  echo "Error: source and destination are the same." >&2
  exit 1
fi

# Prevent recursive copy when DEST is inside SRC
case "$DEST/" in
  "$SRC/"*) echo "Error: destination is inside source (would recurse)." >&2; exit 1 ;;
esac

# ---------------------------------------------------------------------------
# Locate public_html root + backup root for a given destination.
# Sets globals: PUBLIC_HTML (may be empty) and BACKUP_ROOT.
# ---------------------------------------------------------------------------
discover_backup_paths() {
  local dest="$1"
  PUBLIC_HTML=""
  BACKUP_ROOT=""

  # 1. Walk up from dest looking for ancestor named public_html
  local cur="$dest"
  while [ "$cur" != "/" ] && [ -n "$cur" ]; do
    if [ "$(basename "$cur")" = "public_html" ]; then
      PUBLIC_HTML="$cur"
      BACKUP_ROOT="$(dirname "$cur")/backup_public_html"
      return 0
    fi
    cur="$(dirname "$cur")"
  done

  # 2. dest/public_html exists (dest is a site root)
  if [ -d "$dest/public_html" ]; then
    PUBLIC_HTML="$dest/public_html"
    BACKUP_ROOT="$dest/backup_public_html"
    return 0
  fi

  # 3. Some parent of dest contains public_html (dest is sibling of public_html)
  cur="$dest"
  while [ "$cur" != "/" ] && [ -n "$cur" ]; do
    if [ -d "$cur/public_html" ]; then
      PUBLIC_HTML="$cur/public_html"
      BACKUP_ROOT="$cur/backup_public_html"
      return 0
    fi
    # if parent itself is public_html's parent and dest is e.g. mysite/other
    cur="$(dirname "$cur")"
    # avoid infinite loop at /
    if [ "$cur" = "/" ]; then break; fi
  done

  # 4. Fallback: sibling backup dir next to dest
  BACKUP_ROOT="$(dirname "$dest")/backup_public_html"
  PUBLIC_HTML=""
}

# ---------------------------------------------------------------------------
# Dedicated function: create shadow backup dir with same skeleton (dirs only).
# Uses $PUBLIC_HTML as skeleton source if available, else plain mkdir.
# Must be called before first backup write; safe to call multiple times.
# ---------------------------------------------------------------------------
shadow_backup_create() {
  if [ -n "${BACKUP_ROOT:-}" ] && [ -d "$BACKUP_ROOT" ] && [ -n "${PUBLIC_HTML:-}" ] && [ -d "${PUBLIC_HTML:-}" ]; then
    # Backup exists; top-up skeleton for any new dirs added since last time.
    find "$PUBLIC_HTML" -mindepth 1 -type d -print0 | while IFS= read -r -d '' d; do
      rel="${d#"$PUBLIC_HTML"}"
      mkdir -p "$BACKUP_ROOT$rel"
    done
    return 0
  fi

  mkdir -p "$BACKUP_ROOT"
  echo "Created shadow backup dir: $BACKUP_ROOT"

  if [ -n "${PUBLIC_HTML:-}" ] && [ -d "$PUBLIC_HTML" ]; then
    echo "Replicating skeleton from: $PUBLIC_HTML"
    find "$PUBLIC_HTML" -mindepth 1 -type d -print0 | while IFS= read -r -d '' d; do
      rel="${d#"$PUBLIC_HTML"}"
      mkdir -p "$BACKUP_ROOT$rel"
    done
  else
    echo "No public_html found; using plain backup dir (no skeleton source)." >&2
  fi
}

# ---------------------------------------------------------------------------
# Change detection: fast size check first, cmp -s arbitrates ties.
# files_differ SRC DST -> exit 0 (differ) + sets DIFF_WHY, or exit 1
# (identical). Each script keeps its own copy: these files run standalone
# on hosting, so no shared library is sourced.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Main copy
# ---------------------------------------------------------------------------
discover_backup_paths "$DEST"
BACKUP_READY=0
ensure_backup_ready() {
  if [ "$BACKUP_READY" -eq 0 ]; then
    shadow_backup_create
    BACKUP_READY=1
  fi
}

# rel path of a dest file inside backup: relative to PUBLIC_HTML if dest is
# under it, else relative to DEST itself.
backup_path_for() {
  local dest_file="$1"
  local rel
  if [ -n "${PUBLIC_HTML:-}" ]; then
    case "$dest_file/" in
      "$PUBLIC_HTML/"*)
        rel="${dest_file#"$PUBLIC_HTML"}"
        printf '%s%s' "$BACKUP_ROOT" "$rel"
        return 0
        ;;
    esac
  fi
  rel="${dest_file#"$DEST"}"
  printf '%s%s' "$BACKUP_ROOT" "$rel"
}

mkdir -p "$DEST"

COPIED=0
SKIPPED=0
OVERWRITTEN=0
BACKED_UP=0

# 1. Replicate directory skeleton (empty dirs included)
find "$SRC" -mindepth 1 -type d -print0 | while IFS= read -r -d '' d; do
  rel="${d#"$SRC"}"
  mkdir -p "$DEST$rel"
done

# 2. Copy files
while IFS= read -r -d '' sfile; do
  rel="${sfile#"$SRC"}"
  dfile="$DEST$rel"

  if [ -L "$sfile" ]; then
    # Symlink: replicate link itself
    mkdir -p "$(dirname "$dfile")"
    if [ ! -e "$dfile" ]; then
      cp -P "$sfile" "$dfile"
      COPIED=$((COPIED+1))
      echo "[new-link] $rel"
    else
      if [ "$FORCE" -eq 1 ]; then
        # backup old link/file before replace
        if files_differ "$sfile" "$dfile"; then
          ensure_backup_ready
          bfile="$(backup_path_for "$dfile")"
          mkdir -p "$(dirname "$bfile")"
          cp -P "$dfile" "$bfile" 2>/dev/null || cp -a "$dfile" "$bfile"
          BACKED_UP=$((BACKED_UP+1))
          echo "[backup] $rel -> $bfile"
        fi
        cp -Pf "$sfile" "$dfile"
        OVERWRITTEN=$((OVERWRITTEN+1))
        echo "[overwrite-link] $rel"
      else
        SKIPPED=$((SKIPPED+1))
        echo "[skip] $rel (exists)"
      fi
    fi
    continue
  fi

  if [ ! -e "$dfile" ]; then
    mkdir -p "$(dirname "$dfile")"
    cp -p "$sfile" "$dfile"
    COPIED=$((COPIED+1))
    echo "[copy] $rel"
  else
    if [ "$FORCE" -eq 1 ]; then
      if files_differ "$sfile" "$dfile"; then
        ensure_backup_ready
        bfile="$(backup_path_for "$dfile")"
        mkdir -p "$(dirname "$bfile")"
        cp -p "$dfile" "$bfile"
        BACKED_UP=$((BACKED_UP+1))
        echo "[backup] $rel -> $bfile"
        cp -p -f "$sfile" "$dfile"
        OVERWRITTEN=$((OVERWRITTEN+1))
        echo "[overwrite] $rel ($DIFF_WHY)"
      else
        SKIPPED=$((SKIPPED+1))
        echo "[skip] $rel (identical)"
      fi
    else
      SKIPPED=$((SKIPPED+1))
      echo "[skip] $rel (exists, use -f to replace)"
    fi
  fi
done < <(find "$SRC" -mindepth 1 -type f -print0)

# Counters below are accurate: the file loop reads via process
# substitution in the current shell (no subshell loss).
echo "---"
echo "Source:      $SRC"
echo "Destination: $DEST"
echo "Force (-f):  $FORCE"
echo "Duplicate check: $DUPLICATE_CHECK"
echo "Copied: $COPIED, Overwritten: $OVERWRITTEN, Backed up: $BACKED_UP, Skipped: $SKIPPED"
if [ "$BACKUP_READY" -eq 1 ]; then
  echo "Backup dir:  $BACKUP_ROOT"
else
  echo "Backup dir:  (not needed - no overwrites)"
fi
echo "Done."
