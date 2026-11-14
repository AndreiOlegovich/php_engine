#!/usr/bin/env bash
# version 0.0.3
# Copy only the engine: every .php/.js/.css tree (at any depth) plus
# root .htaccess, sitemap.xml and robots.txt. Optional forced overwrite
# + shadow backup. Companion copy-engine-force.sh defaults to forced.
# Usage:
#   ./copy-engine.sh [options] [<source_dir> [<dest_dir>]]
#
# With no dirs it copies DEV_DOCROOT -> PROD_DOCROOT (the normal hosting
# run): /home/user/dev/public_html ->
# /home/user/example.com/public_html. Override via site.env (SITE_ENV)
# or, only when there is no other way, explicit dirs.
#
# Without -f: copy only new files, never overwrite existing files.
# With -f:    copy every in-scope file; existing versions are overwritten
#             (differing ones are saved into backup_public_html first,
#             identical ones are re-copied without a backup).
# Scope: all files under directories named .php, .js or .css (at any depth),
# plus root .htaccess, sitemap.xml and robots.txt. Other content pages and
# assets are left alone. Change detection is byte-exact: file sizes first
# (cheap stat short-circuit), then full content with cmp -s, so even
# same-size edits are caught (unless --duplicate-check size is passed).
set -euo pipefail

FORCE=0
DUPLICATE_CHECK="full"
POSITIONAL=()
# Wrapper (copy-engine-force.sh) overrides this so help names the script
# the user actually invoked.
PROG_NAME="${COPY_ENGINE_PROG:-$0}"

# Optional shared settings (site.env); explicit dirs still win below.
if [ -n "${SITE_ENV:-}" ]; then
  [ -f "$SITE_ENV" ] || { echo "ERROR: SITE_ENV file '$SITE_ENV' not found." >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
fi

usage() {
  echo "Usage: $PROG_NAME [-f|--force] [--no-force] [--duplicate-check full|size] [<source_dir> [<dest_dir>]]" >&2
  echo "  <source_dir>  dir to copy FROM; default: ${DEV_DOCROOT:-/home/user/dev/public_html}" >&2
  echo "  <dest_dir>    dir to copy TO;   default: ${PROD_DOCROOT:-/home/user/example.com/public_html}" >&2
  echo "  No dirs: copy the default dev public_html into the default prod public_html." >&2
  echo "  -f/--force    copy every in-scope file, overwriting existing ones" >&2
  echo "                (differing old versions are backed up first)" >&2
  echo "  --no-force    never overwrite (cancels an earlier --force)" >&2
  if [ "${COPY_ENGINE_FORCE_DEFAULT:-0}" = "1" ]; then
    echo "  (this wrapper defaults to force ON; pass --no-force to opt out)" >&2
  fi
  echo "  --duplicate-check MODE  full (default) or size-only, see above" >&2
  echo "Scope: .php/.js/.css trees + root .htaccess, sitemap.xml, robots.txt." >&2
  echo "Other content pages and files are skipped." >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    -f|--force) FORCE=1; shift ;;
    --no-force) FORCE=0; shift ;;
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

if [ "${#POSITIONAL[@]}" -gt 2 ]; then
  echo "ERROR: at most 2 directory arguments allowed (got ${#POSITIONAL[@]})." >&2
  usage
  exit 1
fi

SRC="${POSITIONAL[0]:-${DEV_DOCROOT:-/home/user/dev/public_html}}"
DEST="${POSITIONAL[1]:-${PROD_DOCROOT:-/home/user/example.com/public_html}}"

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

  if [ -n "${PUBLIC_HTML:-}" ] && [ -d "${PUBLIC_HTML:-}" ]; then
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
# Main copy: *.php, *.js and *.css regular files only.
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

COPIED=0
SKIPPED=0
OVERWRITTEN=0
BACKED_UP=0
FAILED_FILES=0

engine_files() {
  find "$SRC" -type f \( \
    -path '*/.php/*' -o -path '*/.js/*' -o -path '*/.css/*' \
  \) -print0
  local f
  for f in .htaccess sitemap.xml robots.txt; do
    [ -f "$SRC/$f" ] && printf '%s\0' "$SRC/$f"
  done
}

while IFS= read -r -d '' sfile; do
  rel="${sfile#"$SRC"}"
  dfile="$DEST$rel"

  if [ ! -e "$dfile" ]; then
    mkdir -p "$(dirname "$dfile")"
    if cp -p "$sfile" "$dfile"; then
      COPIED=$((COPIED+1))
      echo "[copy] $rel"
    else
      echo "[ERROR] copy failed: $rel (continues with the rest)" >&2
      FAILED_FILES=$((FAILED_FILES+1))
    fi
  elif [ "$FORCE" -eq 1 ]; then
    if files_differ "$sfile" "$dfile"; then
      ensure_backup_ready
      bfile="$(backup_path_for "$dfile")"
      mkdir -p "$(dirname "$bfile")"
      if ! cp -p "$dfile" "$bfile"; then
        echo "[ERROR] backup failed: $rel (skips overwrite, continues)" >&2
        FAILED_FILES=$((FAILED_FILES+1))
      elif ! cp -p -f "$sfile" "$dfile"; then
        echo "[ERROR] overwrite failed after backup: $rel (backup kept, continues)" >&2
        FAILED_FILES=$((FAILED_FILES+1))
      else
        BACKED_UP=$((BACKED_UP+1))
        echo "[backup] $rel -> $bfile"
        OVERWRITTEN=$((OVERWRITTEN+1))
        echo "[overwrite] $rel ($DIFF_WHY)"
      fi
    elif cp -p -f "$sfile" "$dfile"; then
      # Force means copy every in-scope file, even when it is identical.
      OVERWRITTEN=$((OVERWRITTEN+1))
      echo "[force-copy] $rel (identical, copied anyway)"
    else
      echo "[ERROR] force copy failed: $rel (continues with the rest)" >&2
      FAILED_FILES=$((FAILED_FILES+1))
    fi
  else
    SKIPPED=$((SKIPPED+1))
    echo "[skip] $rel (exists, use -f to replace)"
  fi
done < <(engine_files)

# Counters below are accurate: the file loop reads via process
# substitution in the current shell (no subshell loss).
echo "---"
echo "Source:      $SRC"
echo "Destination: $DEST"
echo "Force (-f):  $FORCE"
echo "Duplicate check: $DUPLICATE_CHECK"
echo "Filter:      .php/.js/.css trees + root .htaccess, sitemap.xml, robots.txt"
echo "Copied: $COPIED, Overwritten: $OVERWRITTEN, Backed up: $BACKED_UP, Skipped: $SKIPPED"
if [ "$BACKUP_READY" -eq 1 ]; then
  echo "Backup dir:  $BACKUP_ROOT"
else
  echo "Backup dir:  (not needed - no overwrites)"
fi
if [ "$FAILED_FILES" -ne 0 ]; then
  echo "[ERROR] $FAILED_FILES file(s) failed; rerun to retry them." >&2
  exit 1
fi
echo "Done."
