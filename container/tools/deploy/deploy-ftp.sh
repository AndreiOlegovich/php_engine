#!/usr/bin/env bash
# version 0.4.3
# Deploy the site to virtual hosting over FTP/FTPS.
#
# Uploads ./src (or --src DIR) to the hosting, mirroring the tree.
# Credentials NEVER live in this repo: they are read from a secret file
# OUTSIDE the repo (default below), overridable with --creds.
#
# Usage:
#   ./deploy-ftp.sh [--creds PATH] [--src DIR] [--remote-dir DIR]
#                   [--tls|--no-tls] [--delete] [--force] [--dry-run] [-v|--verbose]
#                   [-h|--help] [--files F ...] [--dirs DIR ...]
#                   [--committed] [--staged] [--last-commit]
#                   [--commits N] [--include-tmp] [--include-all] [--include-arj-dirs]
#                   [--no-images] [--delete-removed N]
#
# File selection (default: whole tree; flags combine as a union):
#   --files F ...   upload only these files/dirs (space and/or comma separated,
#                   repeatable; paths relative to --src, or src/... prefixed).
#                   Consumes following args until the next flag.
#                   Explicit --files entries bypass the junk excludes.
#   --dirs DIR ...  upload contents of selected directories (paths relative to
#                   --src); normal junk excludes still apply. Use this for
#                   directory selection when excluded files must stay excluded.
#   --committed     upload only git-tracked files under --src (git ls-files).
#   --staged        upload only staged changes under --src (git diff --cached).
#   --last-commit   upload only files changed in the last commit (HEAD) under
#                   --src (git diff-tree; merge commits diff against each parent).
#   --commits N     upload only files changed over the last N commits under
#                   --src (net git diff HEAD~N..HEAD; N >= 1).
#   --dirs/--committed/--staged/--last-commit/--commits still skip deploy junk (unless
#                   --include-all, or the matching --include-tmp /
#                   --include-arj-dirs flag).
#                   --delete cannot be combined with any selection.
#
# Deleting what git removed (Delete on the backoffice Deploy tab):
#   --delete-removed N  delete on the server the files removed from --src over
#                   the last N commits (net git diff HEAD~N..HEAD; a renamed
#                   file counts as removed at its old path), then the folders
#                   that are gone locally, once empty. Nothing is uploaded and
#                   nothing else is deleted; a path that exists locally again
#                   is kept. With --dry-run it lists them without connecting.
#                   Combines only with --creds, --src, --remote-dir,
#                   --tls/--no-tls, --dry-run and -v.
#
# Examples:
#   ./deploy-ftp.sh --files qa/index.php section/article.php --dry-run
#   ./deploy-ftp.sh --files "qa/index.php,section/article.php"
#   ./deploy-ftp.sh --staged --dry-run
#   ./deploy-ftp.sh --staged
#   ./deploy-ftp.sh --committed --dry-run
#   ./deploy-ftp.sh --last-commit --dry-run
#   ./deploy-ftp.sh --commits 3 --dry-run
#   ./deploy-ftp.sh --delete-removed 1 --dry-run
#
# Options:
#   --creds PATH      secret file to source (default: ~/.config/php_engine/hosting-access.env)
#   --src DIR         local dir to upload (default: src)
#   --remote-dir DIR  override FTP_REMOTE_DIR from the creds file
#   --tls / --no-tls  force explicit FTPS on/off (default: FTP_USE_TLS from creds file)
#   --delete          delete remote files not present locally (default: off, safe)
#   --force           re-upload every file, skip the same-size check
#                     (default: off — same-size files are skipped as unchanged,
#                     except files known to have changed: named with --files
#                     or selected by --staged/--last-commit/--commits, which
#                     are always uploaded, so a same-size edit is not lost)
#   --dry-run         print plan (host, dirs, file count) without connecting
#   -v, --verbose     print each file being uploaded with its per-file status
#                     (default: summary lines only)
#   --include-tmp     upload everything under tmp/ _tmp/ .tmp/ dirs too
#                     (default: off — those dirs are junk-excluded)
#   --include-all     bypass ALL junk excludes and upload every file
#                     (accepts --include--all as an alias)
#   Draft pages: a .php/.html file with the marker $DRAFT_MARKER in its first
#                     4 KB is junk-excluded in every mode; publish one by
#                     naming it with --files. The whole src/_prototypes/
#                     folder (design drafts) is excluded the same way.
#   --include-arj-dirs upload dirs ending in -arj / _arj / .arj too
#                     (default: off — those dirs are junk-excluded)
#   --no-images     skip image files (png/jpg/jpeg/gif/bmp/webp/svg/ico/
#                     tif/tiff/avif; accepts --no-image as an alias).
#                     Applies even with --include-all; explicit --files
#                     entries still upload named images.
#   --lint            run lint-php.sh on the same selection first, abort on errors
#   --php VER         PHP version for --lint (default 8.2, e.g. 8.2)
#   -h, --help        this help
#
# Secret file format (shell env file, chmod 600, e.g. ~/.config/php_engine/hosting-access.env):
#   FTP_HOST="ftp.example.com"
#   FTP_USER="hosting_user"
#   FTP_PASSWORD="secret-here"
#   FTP_PORT="21"            # optional, default 21
#   FTP_REMOTE_DIR="/"       # optional, default /
#   FTP_USE_TLS="false"      # optional, "true" for explicit FTPS
#   FTP_TIMEOUT="30"         # optional, seconds
#
# See hosting-access.env.template for a copy-paste template.
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../..  # tools live in tools/deploy/; project root (compose file, src/) is the runtime cwd

DEFAULT_CREDS="${HOME}/.config/php_engine/hosting-access.env"
CREDS="$DEFAULT_CREDS"
SRC_DIR="src"
REMOTE_DIR_OVERRIDE=""
TLS_OVERRIDE=""
DELETE=0
FORCE=0
DRY_RUN=0
VERBOSE=0
LINT=0
PHP_VER="8.2"
PHP_SET=0
FILES_LIST=()
DIRS_LIST=()
COMMITTED=0
STAGED=0
LAST_COMMIT=0
COMMITS=0
COMMITS_N=""
INCLUDE_TMP=0
INCLUDE_ALL=0
INCLUDE_ARJ_DIRS=0
NO_IMAGES=0
DELETE_REMOVED_N=""

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --creds) [ $# -ge 2 ] || { echo "ERROR: --creds needs a value." >&2; exit 2; }
      CREDS="$2"; shift 2 ;;
    --creds=*) CREDS="${1#--creds=}"; shift ;;
    --src) [ $# -ge 2 ] || { echo "ERROR: --src needs a value." >&2; exit 2; }
      SRC_DIR="$2"; shift 2 ;;
    --src=*) SRC_DIR="${1#--src=}"; shift ;;
    --remote-dir) [ $# -ge 2 ] || { echo "ERROR: --remote-dir needs a value." >&2; exit 2; }
      REMOTE_DIR_OVERRIDE="$2"; shift 2 ;;
    --remote-dir=*) REMOTE_DIR_OVERRIDE="${1#--remote-dir=}"; shift ;;
    --tls) TLS_OVERRIDE="true"; shift ;;
    --no-tls) TLS_OVERRIDE="false"; shift ;;
    --delete) DELETE=1; shift ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    --lint) LINT=1; shift ;;
    --php) [ $# -ge 2 ] || { echo "ERROR: --php needs a value (e.g. 8.2, 8.2)." >&2; exit 2; }
      PHP_VER="$2"; PHP_SET=1; shift 2 ;;
    --php=*) PHP_VER="${1#--php=}"; PHP_SET=1; shift ;;
    --files)
      shift
      got=0
      while [ $# -gt 0 ]; do
        case "$1" in
          -*) break ;;
          *) FILES_LIST+=("$1"); got=1; shift ;;
        esac
      done
      [ "$got" -eq 1 ] || { echo "ERROR: --files needs at least one file." >&2; exit 2; }
      ;;
    --files=*)
      v="${1#--files=}"
      [ -n "$v" ] || { echo "ERROR: --files needs at least one file." >&2; exit 2; }
      IFS=',' read -ra _parts <<< "$v"
      for _p in "${_parts[@]}"; do
        _p="$(trim "$_p")"; [ -n "$_p" ] && FILES_LIST+=("$_p")
      done
      [ "${#FILES_LIST[@]}" -gt 0 ] || { echo "ERROR: --files needs at least one file." >&2; exit 2; }
      shift ;;
    --dirs)
      shift
      got=0
      while [ $# -gt 0 ]; do
        case "$1" in
          -*) break ;;
          *) DIRS_LIST+=("$1"); got=1; shift ;;
        esac
      done
      [ "$got" -eq 1 ] || { echo "ERROR: --dirs needs at least one directory." >&2; exit 2; }
      ;;
    --dirs=*)
      v="${1#--dirs=}"
      [ -n "$v" ] || { echo "ERROR: --dirs needs at least one directory." >&2; exit 2; }
      IFS=',' read -ra _parts <<< "$v"
      for _p in "${_parts[@]}"; do
        _p="$(trim "$_p")"; [ -n "$_p" ] && DIRS_LIST+=("$_p")
      done
      [ "${#DIRS_LIST[@]}" -gt 0 ] || { echo "ERROR: --dirs needs at least one directory." >&2; exit 2; }
      shift ;;
    --committed|--commited) COMMITTED=1; shift ;;
    --staged) STAGED=1; shift ;;
    --last-commit) LAST_COMMIT=1; shift ;;
    --commits) [ $# -ge 2 ] || { echo "ERROR: --commits needs a count (e.g. --commits 3)." >&2; exit 2; }
      case "$2" in ''|*[!0-9]*|0) echo "ERROR: --commits needs a positive integer, got '$2'." >&2; exit 2 ;; esac
      COMMITS=1; COMMITS_N="$2"; shift 2 ;;
    --commits=*) v="${1#--commits=}"
      case "$v" in ''|*[!0-9]*|0) echo "ERROR: --commits needs a positive integer, got '$v'." >&2; exit 2 ;; esac
      COMMITS=1; COMMITS_N="$v"; shift ;;
    --include-tmp) INCLUDE_TMP=1; shift ;;
    --include-all|--include--all) INCLUDE_ALL=1; shift ;;
    --include-arj-dirs) INCLUDE_ARJ_DIRS=1; shift ;;
    --no-images|--no-image) NO_IMAGES=1; shift ;;
    --delete-removed) [ $# -ge 2 ] || { echo "ERROR: --delete-removed needs a commit count (e.g. --delete-removed 1)." >&2; exit 2; }
      case "$2" in ''|*[!0-9]*|0) echo "ERROR: --delete-removed needs a positive integer, got '$2'." >&2; exit 2 ;; esac
      DELETE_REMOVED_N="$2"; shift 2 ;;
    --delete-removed=*) v="${1#--delete-removed=}"
      case "$v" in ''|*[!0-9]*|0) echo "ERROR: --delete-removed needs a positive integer, got '$v'." >&2; exit 2 ;; esac
      DELETE_REMOVED_N="$v"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
  esac
done

if [ -n "$DELETE_REMOVED_N" ]; then
  if [ "${#FILES_LIST[@]}" -gt 0 ] || [ "${#DIRS_LIST[@]}" -gt 0 ] || [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ] \
    || [ "$LAST_COMMIT" -eq 1 ] || [ "$COMMITS" -eq 1 ] || [ "$DELETE" -eq 1 ] || [ "$FORCE" -eq 1 ] || [ "$LINT" -eq 1 ] \
    || [ "$INCLUDE_TMP" -eq 1 ] || [ "$INCLUDE_ALL" -eq 1 ] || [ "$INCLUDE_ARJ_DIRS" -eq 1 ] || [ "$NO_IMAGES" -eq 1 ]; then
    echo "ERROR: --delete-removed runs alone: it uploads nothing, so it takes no upload or selection flags." >&2
    exit 2
  fi
fi

# Optional shared settings (site.env): values act as defaults only; CLI
# flags and the secret creds file (sourced below) always win.
# See site.env.example.
if [ -n "${SITE_ENV:-}" ]; then
  [ -f "$SITE_ENV" ] || { echo "ERROR: SITE_ENV file '$SITE_ENV' not found." >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
  [ -z "${FTP_PASSWORD:-}" ] || echo "WARNING: FTP_PASSWORD is set in SITE_ENV ($SITE_ENV); keep passwords in the outside creds file." >&2
fi

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }
[ -f "$CREDS" ] || {
  echo "ERROR: creds file '$CREDS' not found." >&2
  echo "Create it (chmod 600) from the template:" >&2
  echo "  mkdir -p \"$(dirname "$CREDS")\"" >&2
  echo "  cp tools/deploy/hosting-access.env.template \"$CREDS\"" >&2
  echo "  chmod 600 \"$CREDS\" && \$EDITOR \"$CREDS\"" >&2
  echo "Or pass another location: $0 --creds /path/to/creds.env" >&2
  exit 1
}

# Refuse a creds file from inside the repo (it would risk a commit).
APP_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
case "$(cd "$(dirname "$CREDS")" 2>/dev/null && pwd -P)" in
  "$APP_ROOT"*|"${APP_ROOT}/"*)
    echo "ERROR: creds file must live OUTSIDE the repo, not under $APP_ROOT" >&2
    echo "Move it e.g. to $DEFAULT_CREDS and pass --creds if needed." >&2
    exit 1
    ;;
esac

# CREDS path comes from --creds/env by design, so shellcheck cannot follow it.
set -a
# shellcheck disable=SC1090
. "$CREDS"
set +a

FTP_HOST="${FTP_HOST:-}"
FTP_USER="${FTP_USER:-}"
FTP_PASSWORD="${FTP_PASSWORD:-}"
FTP_PORT="${FTP_PORT:-21}"
FTP_REMOTE_DIR="${REMOTE_DIR_OVERRIDE:-${FTP_REMOTE_DIR:-/}}"
FTP_USE_TLS="${TLS_OVERRIDE:-${FTP_USE_TLS:-false}}"
FTP_TIMEOUT="${FTP_TIMEOUT:-30}"

[ -n "$FTP_HOST" ] || { echo "ERROR: FTP_HOST is empty in $CREDS" >&2; exit 1; }
[ -n "$FTP_USER" ] || { echo "ERROR: FTP_USER is empty in $CREDS" >&2; exit 1; }
[ -n "$FTP_PASSWORD" ] || { echo "ERROR: FTP_PASSWORD is empty in $CREDS" >&2; exit 1; }

perms="$(stat -c '%a' "$CREDS" 2>/dev/null || echo '?')"
case "$perms" in
  600|400) ;;
  *) echo "WARNING: $CREDS has mode $perms (recommend: chmod 600)." >&2 ;;
esac

echo "host:        $FTP_HOST:$FTP_PORT (tls=$FTP_USE_TLS)"
echo "user:        $FTP_USER"
echo "src:         $SRC_DIR/ -> remote: $FTP_REMOTE_DIR/"
echo "delete:      $([ "$DELETE" -eq 1 ] && echo yes || echo no)"
echo "force:       $([ "$FORCE" -eq 1 ] && echo yes || echo no)"
echo "include-tmp: $([ "$INCLUDE_TMP" -eq 1 ] && echo yes || echo no)"
echo "include-all: $([ "$INCLUDE_ALL" -eq 1 ] && echo yes || echo no)"
echo "include-arj-dirs: $([ "$INCLUDE_ARJ_DIRS" -eq 1 ] && echo yes || echo no)"
echo "no-images: $([ "$NO_IMAGES" -eq 1 ] && echo yes || echo no)"

# --- --delete-removed: git gives the list, the Python part below deletes ---
REMOVED_LIST=""
if [ -n "$DELETE_REMOVED_N" ]; then
  SRC_ABS="$(cd "$SRC_DIR" && pwd -P)"
  TOP="$(git -C "$SRC_ABS" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$TOP" ] || { echo "ERROR: $SRC_DIR is not inside a git repo (--delete-removed needs git)." >&2; exit 1; }
  TOP="$(cd "$TOP" && pwd -P)"
  git -C "$TOP" rev-parse --verify -q "HEAD~${DELETE_REMOVED_N}" >/dev/null \
    || { echo "ERROR: only $(git -C "$TOP" rev-list --count HEAD) commit(s) in history, --delete-removed $DELETE_REMOVED_N is too big." >&2; exit 1; }
  if [ "$SRC_ABS" = "$TOP" ]; then PREFIX=""; else PREFIX="${SRC_ABS#"$TOP"/}/"; fi
  REMOVED_LIST="$(mktemp)"
  trap 'rm -f "$REMOVED_LIST"' EXIT
  # Net change; --no-renames splits a rename into removed + added, so the
  # old path is deleted on the server too.
  git -C "$TOP" diff --name-only -z --no-renames --diff-filter=D "HEAD~${DELETE_REMOVED_N}" HEAD -- "$SRC_ABS" \
    | tr '\0' '\n' | sed "s#^${PREFIX}##" > "$REMOVED_LIST"
  echo "delete-removed: files removed from $SRC_DIR/ over the last $DELETE_REMOVED_N commit(s) (net diff HEAD~$DELETE_REMOVED_N..HEAD)"
fi
export PHP_ENGINE_DELETE_REMOVED="$REMOVED_LIST"

# --- file selection: union of --files / --dirs / git selection flags ---
# Explicit --files go to ONLY_EXP (bypass junk excludes); git-based go to
# ONLY_GIT and --dirs go to their own selection lists (junk excludes still
# apply). Empty = whole tree.
ONLY_EXP=""
ONLY_GIT=""
ONLY_DIRS=""
ONLY_CHG=""  # files known to have changed: always uploaded
if [ "${#FILES_LIST[@]}" -gt 0 ] || [ "${#DIRS_LIST[@]}" -gt 0 ] || [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ] || [ "$LAST_COMMIT" -eq 1 ] || [ "$COMMITS" -eq 1 ]; then
  if [ "$DELETE" -eq 1 ]; then
    echo "ERROR: --delete cannot be combined with file selection (--files/--dirs/--committed/--staged/--last-commit/--commits)." >&2
    exit 2
  fi
  ONLY_EXP="$(mktemp)"; ONLY_GIT="$(mktemp)"; ONLY_DIRS="$(mktemp)"; ONLY_CHG="$(mktemp)"
  trap 'rm -f "$ONLY_EXP" "$ONLY_GIT" "$ONLY_DIRS" "$ONLY_CHG"' EXIT
  SRC_ABS="$(cd "$SRC_DIR" && pwd -P)"

  # 1) explicit --files entries (array: names with spaces survive intact)
  if [ "${#FILES_LIST[@]}" -gt 0 ]; then
    for tok in "${FILES_LIST[@]}"; do
      t="${tok#./}"
      case "$t" in
        "$SRC_DIR"/*) t="${t#"$SRC_DIR"/}" ;;
        container/src/*) t="${t#container/src/}" ;;
      esac
      case "$t" in
        /*)
          case "$t" in
            "$SRC_ABS"/*) t="${t#"$SRC_ABS"/}" ;;
            *) echo "WARNING: skipping '$tok' (outside $SRC_DIR)." >&2; continue ;;
          esac
          ;;
      esac
      ap="$SRC_ABS/$t"
      if [ -d "$ap" ] && [ ! -L "$ap" ]; then
        find "$ap" -type f | while IFS= read -r f; do
          printf '%s\n' "${f#"$SRC_ABS"/}"
        done >> "$ONLY_EXP"
      elif [ -f "$ap" ]; then
        printf '%s\n' "$t" | tee -a "$ONLY_CHG" >> "$ONLY_EXP"
      else
        echo "WARNING: skipping '$tok' (not found under $SRC_DIR)." >&2
      fi
    done
  fi

  # Safe directory selections are kept as directory prefixes; Python applies
  # the normal junk filters while scanning rather than expanding with find.
  if [ "${#DIRS_LIST[@]}" -gt 0 ]; then
    for tok in "${DIRS_LIST[@]}"; do
      [[ "$tok" =~ [[:cntrl:]] ]] && { echo "ERROR: --dirs path contains a control character." >&2; exit 2; }
      t="${tok#./}"
      case "$t" in
        "$SRC_DIR"/*) t="${t#"$SRC_DIR"/}" ;;
        container/src/*) t="${t#container/src/}" ;;
      esac
      case "$t" in
        ""|/*|..|../*|*/../*|*/..) echo "ERROR: unsafe --dirs path '$tok'." >&2; exit 2 ;;
      esac
      ap="$SRC_ABS/$t"
      [ -d "$ap" ] && [ ! -L "$ap" ] || { echo "ERROR: --dirs path '$tok' is not a real directory under $SRC_DIR." >&2; exit 2; }
      real="$(cd "$ap" && pwd -P)"
      case "$real/" in
        "$SRC_ABS/"*) ;;
        *) echo "ERROR: --dirs path '$tok' resolves outside $SRC_DIR." >&2; exit 2 ;;
      esac
      [ "$real" != "$SRC_ABS" ] || { echo "ERROR: selecting the whole source root with --dirs is not allowed." >&2; exit 2; }
      rel="${real#"$SRC_ABS"/}"
      [ -n "$rel" ] || { echo "ERROR: selecting the whole source root with --dirs is not allowed." >&2; exit 2; }
      printf '%s\n' "$rel" >> "$ONLY_DIRS"
    done
    sort -u "$ONLY_DIRS" -o "$ONLY_DIRS"
    sed -i '/^$/d' "$ONLY_DIRS"
  fi

  # 2) git-based selections (repo paths stripped to src-relative)
  if [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ] || [ "$LAST_COMMIT" -eq 1 ] || [ "$COMMITS" -eq 1 ]; then
    TOP="$(git -C "$SCRIPT_DIR/../.." rev-parse --show-toplevel 2>/dev/null || true)"
    [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--committed/--staged/--last-commit/--commits need git)." >&2; exit 1; }
    PREFIX="${SRC_ABS#"$TOP"/}"
    [ "$PREFIX" != "$SRC_ABS" ] || { echo "ERROR: $SRC_DIR is outside the git repo." >&2; exit 1; }
    if [ "$COMMITTED" -eq 1 ]; then
      git -C "$SCRIPT_DIR/../.." ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" >> "$ONLY_GIT" || true
    fi
    if [ "$STAGED" -eq 1 ]; then
      # NOTE: git diff has no --full-name; --name-only is already toplevel-relative.
      git -C "$SCRIPT_DIR/../.." diff --cached --name-only -z --diff-filter=ACMR -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" | tee -a "$ONLY_CHG" >> "$ONLY_GIT" || true
    fi
    if [ "$LAST_COMMIT" -eq 1 ]; then
      # Files changed in HEAD (last commit), added/copied/modified/renamed.
      # -m keeps merge commits working (diff against each parent, union).
      git -C "$SCRIPT_DIR/../.." diff-tree --no-commit-id --name-only -z -r -m --diff-filter=ACMR HEAD -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" | tee -a "$ONLY_CHG" >> "$ONLY_GIT" || true
    fi
    if [ "$COMMITS" -eq 1 ]; then
      # Net change over the last N commits (HEAD~N..HEAD). Files touched
      # and reverted inside the window correctly drop out.
      git -C "$SCRIPT_DIR/../.." rev-parse --verify -q "HEAD~${COMMITS_N}" >/dev/null \
        || { echo "ERROR: only $(git -C "$SCRIPT_DIR/../.." rev-list --count HEAD) commit(s) in history, --commits $COMMITS_N is too big." >&2; exit 1; }
      git -C "$SCRIPT_DIR/../.." diff --name-only -z --diff-filter=ACMR "HEAD~${COMMITS_N}" HEAD -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" | tee -a "$ONLY_CHG" >> "$ONLY_GIT" || true
    fi
  fi

  sort -u "$ONLY_EXP" -o "$ONLY_EXP"; sed -i '/^$/d' "$ONLY_EXP"
  sort -u "$ONLY_GIT" -o "$ONLY_GIT"; sed -i '/^$/d' "$ONLY_GIT"
  sort -u "$ONLY_CHG" -o "$ONLY_CHG"; sed -i '/^$/d' "$ONLY_CHG"
  SEL_COUNT="$(( $(wc -l < "$ONLY_EXP" | tr -d ' ') + $(wc -l < "$ONLY_GIT" | tr -d ' ') ))"
  [ "$SEL_COUNT" -gt 0 ] || [ "${#DIRS_LIST[@]}" -gt 0 ] || { echo "ERROR: selection is empty (no matching files)." >&2; exit 1; }
  echo "selection:   $SEL_COUNT explicit/git file(s), ${#DIRS_LIST[@]} safe dir(s)"
  if [ "$COMMITS" -eq 1 ]; then
    echo "commits:     last $COMMITS_N commit(s) (net diff HEAD~$COMMITS_N..HEAD)"
  fi
fi

export PHP_ENGINE_ONLY_EXPLICIT="$ONLY_EXP" PHP_ENGINE_ONLY_GIT="$ONLY_GIT"
export PHP_ENGINE_ONLY_DIRS="$ONLY_DIRS" PHP_ENGINE_ONLY_CHANGED="$ONLY_CHG"

# Default progress: announce what is about to be uploaded.
if [ -n "$DELETE_REMOVED_N" ]; then
  echo "Deleting on the server what git removed; nothing is uploaded."
elif [ "${#FILES_LIST[@]}" -gt 0 ]; then
  echo "Uploading ${#FILES_LIST[@]} path(s):"
  for _tok in "${FILES_LIST[@]}"; do
    _t="${_tok#./}"
    case "$_t" in
      "$SRC_DIR"/*) _t="${_t#"$SRC_DIR"/}" ;;
      container/src/*) _t="${_t#container/src/}" ;;
    esac
    if [ -d "$SRC_DIR/$_t" ] && [ ! -L "$SRC_DIR/$_t" ]; then
      echo "  dir:  ${_t%/}/"
    elif [ -f "$SRC_DIR/$_t" ]; then
      echo "  file: $_t"
    else
      echo "  path: $_t"
    fi
  done
elif [ "${#DIRS_LIST[@]}" -gt 0 ]; then
  echo "Uploading selected directories with junk exclusions enabled:"
  for _tok in "${DIRS_LIST[@]}"; do echo "  dir:  ${_tok%/}/"; done
elif [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ] || [ "$LAST_COMMIT" -eq 1 ] || [ "$COMMITS" -eq 1 ]; then
  echo "Uploading git selection (--committed/--staged/--last-commit/--commits)."
else
  echo "Uploading full tree under $SRC_DIR/ ..."
fi

# --- pre-upload lint (same selection; runs even with --dry-run) ---
if [ "$LINT" -eq 1 ]; then
  [ -x tools/linters/lint-php.sh ] || { echo "ERROR: tools/linters/lint-php.sh not found (run from container/)." >&2; exit 1; }
  LINT_ARGS=(--src "$SRC_DIR" --php "$PHP_VER")
  if [ "${#FILES_LIST[@]}" -gt 0 ]; then
    LINT_ARGS+=(--files "${FILES_LIST[@]}")
  fi
  if [ "${#DIRS_LIST[@]}" -gt 0 ]; then
    LINT_ARGS+=(--dirs "${DIRS_LIST[@]}")
  fi
  [ "$COMMITTED" -eq 1 ] && LINT_ARGS+=(--committed)
  [ "$STAGED" -eq 1 ] && LINT_ARGS+=(--staged)
  if [ "$LAST_COMMIT" -eq 1 ] || [ "$COMMITS" -eq 1 ]; then
    # lint-php.sh has no --last-commit/--commits: expand the resolved
    # list to --files.
    while IFS= read -r _lf; do
      [ -n "$_lf" ] && LINT_ARGS+=(--files "$_lf")
    done < "$ONLY_GIT"
  fi
  echo "==> pre-upload lint ..."
  ./tools/linters/lint-php.sh "${LINT_ARGS[@]}" || { echo "ERROR: lint failed, aborting upload." >&2; exit 1; }
elif [ "$PHP_SET" -eq 1 ]; then
  echo "WARNING: --php has no effect without --lint." >&2
fi

export PHP_ENGINE_FTP_HOST="$FTP_HOST" PHP_ENGINE_FTP_USER="$FTP_USER" PHP_ENGINE_FTP_PASS="$FTP_PASSWORD"
export PHP_ENGINE_FTP_PORT="$FTP_PORT" PHP_ENGINE_FTP_REMOTE="$FTP_REMOTE_DIR" PHP_ENGINE_FTP_TLS="$FTP_USE_TLS"
export PHP_ENGINE_FTP_TIMEOUT="$FTP_TIMEOUT" PHP_ENGINE_SRC="$SRC_DIR" PHP_ENGINE_DELETE="$DELETE" PHP_ENGINE_DRYRUN="$DRY_RUN"
export PHP_ENGINE_VERBOSE="$VERBOSE" PHP_ENGINE_COMMITTED="$COMMITTED" PHP_ENGINE_STAGED="$STAGED"
export PHP_ENGINE_LAST_COMMIT="$LAST_COMMIT" PHP_ENGINE_COMMITS="$COMMITS"
export PHP_ENGINE_FORCE="$FORCE"
export PHP_ENGINE_INCLUDE_TMP="$INCLUDE_TMP" PHP_ENGINE_INCLUDE_ALL="$INCLUDE_ALL"
export PHP_ENGINE_INCLUDE_ARJ_DIRS="$INCLUDE_ARJ_DIRS"
export PHP_ENGINE_NO_IMAGES="$NO_IMAGES"

python3 - <<'EOF'
import fnmatch, os, posixpath, shutil, sys, time
from ftplib import FTP, FTP_TLS, error_perm

host = os.environ["PHP_ENGINE_FTP_HOST"]
user = os.environ["PHP_ENGINE_FTP_USER"]
pw = os.environ["PHP_ENGINE_FTP_PASS"]
port = int(os.environ.get("PHP_ENGINE_FTP_PORT", "21") or 21)
remote_base = os.environ.get("PHP_ENGINE_FTP_REMOTE", "/") or "/"
use_tls = os.environ.get("PHP_ENGINE_FTP_TLS", "false").lower() in ("1", "true", "yes")
timeout = int(os.environ.get("PHP_ENGINE_FTP_TIMEOUT", "30") or 30)
src = os.environ.get("PHP_ENGINE_SRC", "src")
delete = os.environ.get("PHP_ENGINE_DELETE") == "1"
dryrun = os.environ.get("PHP_ENGINE_DRYRUN") == "1"
verbose = os.environ.get("PHP_ENGINE_VERBOSE") == "1"
committed_flag = os.environ.get("PHP_ENGINE_COMMITTED") == "1"
staged_flag = os.environ.get("PHP_ENGINE_STAGED") == "1"
last_commit_flag = os.environ.get("PHP_ENGINE_LAST_COMMIT") == "1"
commits_flag = os.environ.get("PHP_ENGINE_COMMITS") == "1"
force = os.environ.get("PHP_ENGINE_FORCE") == "1"
include_tmp = os.environ.get("PHP_ENGINE_INCLUDE_TMP") == "1"
include_all = os.environ.get("PHP_ENGINE_INCLUDE_ALL") == "1"
include_arj_dirs = os.environ.get("PHP_ENGINE_INCLUDE_ARJ_DIRS") == "1"
no_images = os.environ.get("PHP_ENGINE_NO_IMAGES") == "1"
delete_removed = os.environ.get("PHP_ENGINE_DELETE_REMOVED", "")

def connect():
    """Logged-in FTP session in passive mode; exits 1 with hints on failure."""
    ftp = (FTP_TLS if use_tls else FTP)()
    try:
        ftp.connect(host, port, timeout=timeout)
        ftp.login(user, pw)
    except Exception as e:
        print(f"ERROR: cannot connect/login to {host}:{port} as {user}: {e}")
        print("Hints: verify FTP_HOST/PORT/USER/PASSWORD in the creds file;")
        print("  check the hosting panel (server name, password, IP restrictions);")
        print("  try FileZilla from this machine; try another network;")
        print("  if TCP connects but no login prompt arrives, the hoster is")
        print("  stalling your IP — contact hosting support.")
        sys.exit(1)
    if use_tls:
        ftp.prot_p()
    ftp.set_pasv(True)
    return ftp

def delete_removed_files(list_path):
    """--delete-removed: delete only what git removed; returns the exit code."""
    with open(list_path, encoding="utf-8") as fh:
        removed = sorted({line.rstrip("\n") for line in fh if line.strip()})
    kept = [rel for rel in removed if os.path.lexists(os.path.join(src, rel))]
    targets = [rel for rel in removed if rel not in kept]
    # Folders that are gone locally, deepest first; the server refuses to
    # remove a folder that still holds anything.
    folders = set()
    for rel in targets:
        parent = posixpath.dirname(rel)
        while parent and not os.path.isdir(os.path.join(src, parent)):
            folders.add(parent)
            parent = posixpath.dirname(parent)
    folders = sorted(folders, key=lambda d: (-d.count("/"), d))
    print(f"removed in git: {len(removed)}, to delete: {len(targets)}, "
          f"kept (exist locally again): {len(kept)}")
    for rel in kept:
        print(f"  kept: {rel} (exists locally again)")
    if dryrun:
        for rel in targets:
            print(f"  would delete: {rel}")
        for d in folders:
            print(f"  would remove folder if empty: {d}/")
        print("dry-run: no connection, nothing deleted.")
        return 0
    if not targets:
        print("nothing to delete.")
        return 0
    base = remote_base.rstrip("/") or "/"
    ftp = connect()
    ftp.voidcmd("TYPE I")  # some servers refuse SIZE in ASCII mode
    deleted = absent = failed = 0
    for rel in targets:
        rpath = posixpath.join(base, rel)
        try:
            ftp.size(rpath)
        except error_perm:
            absent += 1
            print(f"  absent: {rel} (not on the server)")
            continue
        try:
            ftp.delete(rpath)
        except Exception as e:
            failed += 1
            print(f"FAILED: {rel} ({e})")
        else:
            deleted += 1
            print(f"  deleted: {rel}")
    removed_folders = 0
    for d in folders:
        try:
            ftp.rmd(posixpath.join(base, d))
        except error_perm:
            continue  # not empty, or not there
        removed_folders += 1
        print(f"  removed folder: {d}/")
    ftp.quit()
    print(f"deleted: {deleted}, absent: {absent}, failed: {failed}, "
          f"folders removed: {removed_folders}")
    print("done.")
    return 1 if failed else 0

if delete_removed:
    sys.exit(delete_removed_files(delete_removed))

# Junk that must never go to hosting (mirrors the repo .gitignore logic).
# NOTE: images are intentionally NOT excluded — hosting needs them.
# tmp/_tmp/.tmp dirs are excluded by default (session/demo junk); --include-tmp
# re-includes them, --include-all bypasses every rule below.
# Dirs ending in -arj / _arj / .arj are excluded by default; --include-arj-dirs
# re-includes them, --include-all bypasses every rule below.
BASE_EXCLUDE_DIRS = {"reports", "allure-results", "allure-results-host",
                "__pycache__", ".pytest_cache", ".git",
                # NOTE: no "venv" here on purpose — the only venv/ dirs in
                # src/ are article sections (dev/python/ve/venv and twins);
                # real virtualenvs live in ".venv", still excluded above.
                ".venv", "node_modules",
                # Design drafts, never published (src/_prototypes/README.md).
                "_prototypes"}
TMP_EXCLUDE_DIRS = {"tmp", "_tmp", ".tmp"}
ARJ_DIR_SUFFIXES = ("-arj", "_arj", ".arj")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
              ".ico", ".tif", ".tiff", ".avif")

def is_arj_dir(name):
    """True if a dir name ends in -arj / _arj / .arj."""
    return name.endswith(ARJ_DIR_SUFFIXES)

def is_arj_excluded(name):
    """True if an -arj-suffixed dir is currently excluded."""
    return is_arj_dir(name) and not include_all and not include_arj_dirs
BASE_EXCLUDE_FILES = ["*.log", "*.db", "*.sqlite", "*.sqlite3", "*.db-journal",
                 "sess_*", "* copy*", "*.bak", "*.orig", "*~", "*.swp",
                 "*.swo", ".DS_Store", "Thumbs.db"]
# Full-tree paths that must never deploy even though siblings do
# (owner device fingerprints recorded on hosting, not in git).
EXCLUDE_PATH_PREFIXES = ("services/user_profile/data/",)
if include_all:
    EXCLUDE_DIRS = set()
    EXCLUDE_FILES = []
elif include_tmp:
    EXCLUDE_DIRS = set(BASE_EXCLUDE_DIRS)
    EXCLUDE_FILES = list(BASE_EXCLUDE_FILES)
else:
    EXCLUDE_DIRS = set(BASE_EXCLUDE_DIRS) | set(TMP_EXCLUDE_DIRS)
    EXCLUDE_FILES = list(BASE_EXCLUDE_FILES)

def exclude_reason(rel):
    """Return junk-exclude detail string, or None if the file is allowed."""
    posix = rel.replace(os.sep, "/")
    for prefix in EXCLUDE_PATH_PREFIXES:
        if posix == prefix.rstrip("/") or posix.startswith(prefix):
            return f"protected path '{prefix}' (device profiles, never deploy)"
    parts = rel.split(os.sep)
    for p in parts[:-1]:
        if p in EXCLUDE_DIRS:
            return f"dir {p}/"
        if is_arj_excluded(p):
            return f"dir {p}/ (arj-suffixed)"
    base = parts[-1]
    for pat in EXCLUDE_FILES:
        if fnmatch.fnmatch(base, pat):
            return f"pattern '{pat}' matched '{base}'"
    if is_draft(rel):
        return "draft page ($DRAFT_MARKER marker)"
    return None

DRAFT_MARKER = os.environ.get("SITE_DRAFT_MARKER", "draft").encode()

def is_draft(rel):
    """True if a .php/.html file declares itself a draft in its first 4 KB."""
    if os.path.splitext(rel)[1].lower() not in (".php", ".html", ".htm"):
        return False
    try:
        with open(os.path.join(src, rel), "rb") as fh:
            return DRAFT_MARKER in fh.read(4096)
    except OSError:
        return False

def is_tmp_path(rel):
    """True if the file lives under a tmp/ _tmp/ .tmp/ dir."""
    return any(p in TMP_EXCLUDE_DIRS for p in rel.split(os.sep)[:-1])

def is_image_skipped(rel):
    """True if --no-images filters this file (explicit --files bypass it)."""
    return no_images and os.path.splitext(rel)[1].lower() in IMAGE_EXTS

def not_selected_reason():
    if committed_flag or staged_flag or last_commit_flag or commits_flag:
        return ("skipped (untracked / not-selected: not in "
                "--files/--dirs/--committed/--staged/--last-commit/--commits union; untracked files are "
                "never selected by git flags, use --files)")
    return "skipped (not-selected: not in --files/--dirs selection)"

def load_sel(path):
    s = set()
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    s.add(line)
    return s

explicit = load_sel(os.environ.get("PHP_ENGINE_ONLY_EXPLICIT", ""))
gitsel = load_sel(os.environ.get("PHP_ENGINE_ONLY_GIT", ""))
selected_dirs = load_sel(os.environ.get("PHP_ENGINE_ONLY_DIRS", ""))
# Named with --files or picked by --staged/--last-commit/--commits: known to
# have changed, so the same-size check must not skip a same-size edit.
known_changed = load_sel(os.environ.get("PHP_ENGINE_ONLY_CHANGED", ""))
select_mode = bool(explicit or gitsel or selected_dirs)

def is_in_selected_dir(rel_posix):
    return any(rel_posix == selected or rel_posix.startswith(selected + "/")
               for selected in selected_dirs)

local_files = {}  # rel_posix -> abs path
skipped_not_selected = []  # [(rel_posix, reason)]
skipped_junk = []  # [(rel_posix, reason)]
skipped_junk_dirs = []  # [(rel_posix_dir, reason)]
for root, dirs, files in os.walk(src, followlinks=True):
    # Record pruned junk dirs explicitly instead of silently dropping them,
    # then prune so we don't descend into e.g. node_modules/.venv.
    # Explicit --files entries bypass the prune: keep any dir on the path
    # to an explicitly selected file (e.g. venv/ content pages).
    dirs.sort()
    # Safe --dirs never follows a symlinked directory outside the tree.
    if selected_dirs:
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    pruned = [
        d for d in dirs
        if d in EXCLUDE_DIRS
        or is_arj_excluded(d)
        or (selected_dirs and " copy" in d.lower())
    ]
    kept = []
    for d in pruned:
        drel = os.path.relpath(os.path.join(root, d), src).replace(os.sep, "/") + "/"
        if any(e == drel.rstrip("/") or e.startswith(drel) for e in explicit):
            kept.append(d)
            continue
        detail = "copy-like directory" if " copy" in d.lower() else f"dir {d}/"
        skipped_junk_dirs.append((drel, f"skipped (junk-excluded: {detail})"))
    pruned = [d for d in pruned if d not in kept]
    dirs[:] = [d for d in dirs if d not in pruned]
    for f in sorted(files):
        ap = os.path.join(root, f)
        if not os.path.isfile(ap) or os.path.islink(ap) and not os.path.exists(ap):
            continue
        rel = os.path.relpath(ap, src)
        rel_posix = rel.replace(os.sep, "/")
        if selected_dirs and os.path.islink(ap):
            skipped_junk.append((rel_posix, "skipped (symlink in safe directory selection)"))
            continue
        # --include-tmp uploads everything under tmp/ _tmp/ .tmp/ dirs;
        # --include-all already emptied the exclude lists, this is belt & braces.
        under_tmp = is_tmp_path(rel)
        er = None if (include_all or (include_tmp and under_tmp)) else exclude_reason(rel)
        junk_note = f"; also junk-excluded ({er}, explicit --files bypasses this)" if er else ""
        if (select_mode and rel_posix not in explicit and rel_posix not in gitsel
                and not is_in_selected_dir(rel_posix)):
            skipped_not_selected.append((rel_posix, not_selected_reason() + junk_note))
            continue
        if er and rel_posix not in explicit:
            skipped_junk.append((rel_posix,
                f"skipped (junk-excluded: {er}; explicit --files bypasses this)"))
            continue
        if is_image_skipped(rel) and rel_posix not in explicit:
            skipped_junk.append((rel_posix,
                "skipped (image: --no-images; explicit --files bypasses this)"))
            continue
        local_files[rel_posix] = ap

n_junk = len(skipped_junk) + len(skipped_junk_dirs)
print(f"local files to consider: {len(local_files)}")
print(f"skipped (not-selected/untracked): {len(skipped_not_selected)}")
print(f"skipped (junk-excluded): {n_junk} "
      f"({len(skipped_junk)} files + {len(skipped_junk_dirs)} dirs)")
if select_mode and not local_files:
    print("ERROR: selection is empty after deploy junk exclusions.")
    sys.exit(1)

def print_skip_list(title, items, limit=None, skip_when_selected=False):
    if not items:
        return
    if skip_when_selected and select_mode:
        # With --files/--committed/--staged the files outside the selection
        # are expected skips (tens of thousands) — report only the count
        # above, not a per-file dump. Per-file reasons are still shown for
        # files inside the selection (would upload / unchanged / junk).
        return
    print(title)
    show = sorted(items) if (verbose or limit is None) else sorted(items)[:limit]
    for rel, reason in show:
        print(f"  skipped: {rel}")
        print(f"    reason: {reason}")
    if limit is not None and not verbose and len(items) > limit:
        print(f"  ... and {len(items) - limit} more (rerun with -v to list all)")

always_upload = known_changed & set(local_files)
if always_upload and not force:
    print(f"size check skipped for {len(always_upload)} file(s) known to have changed (--files/--staged/--last-commit/--commits)")

if dryrun:
    print("dry-run: no connection, nothing uploaded.")
    show = sorted(local_files) if verbose else sorted(local_files)[:10]
    for rel in show:
        print(f"  would upload: {rel}")
    if not verbose and len(local_files) > 10:
        print(f"  ... and {len(local_files) - 10} more")
    print_skip_list("skipped files (not-selected/untracked):",
                    skipped_not_selected, limit=10,
                    skip_when_selected=True)
    print_skip_list("skipped files (junk-excluded):",
                    skipped_junk, limit=10)
    print_skip_list("skipped dirs (junk-excluded):",
                    skipped_junk_dirs, limit=10,
                    skip_when_selected=True)
    sys.exit(0)

if verbose and (skipped_not_selected or skipped_junk or skipped_junk_dirs):
    print_skip_list("skipped before upload (not-selected/untracked):",
                    skipped_not_selected, limit=None,
                    skip_when_selected=True)
    print_skip_list("skipped before upload (junk-excluded):",
                    skipped_junk, limit=None)
    print_skip_list("skipped before upload (junk-excluded dirs):",
                    skipped_junk_dirs, limit=None,
                    skip_when_selected=True)

ftp = connect()

def ensure_dir(rdir):
    if not rdir or rdir == "/":
        return
    parts = [p for p in rdir.strip("/").split("/") if p]
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            ftp.mkd(cur)
        except error_perm:
            pass  # exists

def remote_size(rpath):
    try:
        return ftp.size(rpath)
    except Exception:
        return None

ensure_dir(remote_base)
uploaded = skipped_unchanged = failed = 0
if force:
    print("force: on (same-size check disabled, every file will be re-uploaded)")
total = len(local_files)
step = max(1, total // 100)  # default mode off a terminal: ~100 progress lines, no silent gaps
# On a terminal the progress bar stays on the last line and is redrawn in
# place; off it (backoffice job log, nohup) it is a plain line every ~1%,
# and -v leaves it out there, since every file already gets its own lines.
tty = sys.stdout.isatty()
started = time.monotonic()
bar_shown = False
bar_drawn_at = 0.0

def duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"

def progress_text(i):
    filled = i * 30 // total
    elapsed = time.monotonic() - started
    return (f"progress: [{'#' * filled}{'-' * (30 - filled)}] {i * 100 // total}% {i}/{total} files, "
            f"{duration(elapsed)} elapsed, ETA {duration(elapsed / i * (total - i))} "
            f"(uploaded={uploaded} skipped_unchanged={skipped_unchanged} failed={failed})")

def out(line):
    """Print a line in the upload loop, above the terminal progress bar."""
    global bar_shown
    if bar_shown:
        sys.stdout.write("\r\033[K")
        bar_shown = False
    print(line)

def show_progress(i):
    global bar_shown, bar_drawn_at
    if tty:
        now = time.monotonic()
        if bar_shown and now - bar_drawn_at < 0.25 and i != total:
            return
        width = shutil.get_terminal_size().columns - 1  # a wrapped line cannot be redrawn
        sys.stdout.write("\r\033[K" + progress_text(i)[:width])
        sys.stdout.flush()
        bar_shown, bar_drawn_at = True, now
    elif not verbose and (i % step == 0 or i == total):
        print(f"  {progress_text(i)}", flush=True)

for i, rel in enumerate(sorted(local_files), 1):
    rpath = posixpath.join(remote_base.rstrip("/") or "/", rel)
    ensure_dir(posixpath.dirname(rpath))
    try:
        lsize = os.path.getsize(local_files[rel])
    except OSError as e:
        # File vanished between scan and upload (concurrent edit): count it
        # and continue instead of aborting the whole run.
        failed += 1
        if verbose:
            out(f"uploading: {rel}")
            out(f"  status: FAILED (vanished mid-run: {e})")
        else:
            out(f"FAILED: {rel} (vanished mid-run: {e})")
        show_progress(i)
        continue
    rsize = None if force or rel in always_upload else remote_size(rpath)
    if rsize == lsize:
        skipped_unchanged += 1
        if verbose:
            out(f"uploading: {rel}")
            out(f"  status: skipped (unchanged: local {lsize} B == remote {rsize} B)")
    else:
        if verbose:
            out(f"uploading: {rel}")
        try:
            with open(local_files[rel], "rb") as fh:
                ftp.storbinary(f"STOR {rpath}", fh)
        except Exception as e:
            failed += 1
            if verbose:
                out(f"  status: FAILED ({e})")
            else:
                out(f"FAILED: {rel} ({e})")
        else:
            uploaded += 1
            if verbose:
                out(f"  status: uploaded (forced)" if force else f"  status: uploaded")
    show_progress(i)
if bar_shown:
    print()  # keep the finished bar on its own line
print(f"uploaded: {uploaded}, "
      f"skipped (unchanged: same size on server): {skipped_unchanged}, "
      f"skipped (not-selected/untracked): {len(skipped_not_selected)}, "
      f"skipped (junk-excluded): {n_junk}, failed: {failed}")
if failed:
    sys.exit(1)

if delete:
    # Collect remote files via MLSD (most hostings support it).
    remote_files = set()
    def walk(rdir):
        try:
            entries = list(ftp.mlsd(rdir))
        except Exception as e:
            print(f"WARNING: MLSD failed on {rdir} ({e}); --delete skipped below it.")
            return
        for name, facts in entries:
            if name in (".", ".."):
                continue
            rp = posixpath.join(rdir, name)
            if facts.get("type") == "dir":
                walk(rp)
            elif facts.get("type") == "file":
                remote_files.add(rp[len(remote_base.rstrip('/') or '/'):].lstrip("/"))
    walk(remote_base.rstrip("/") or "/")
    extra = sorted(r for r in remote_files if r not in local_files)
    for r in extra:
        ftp.delete(posixpath.join(remote_base.rstrip("/") or "/", r))
    print(f"deleted remote-only files: {len(extra)}")

ftp.quit()
print("done.")
EOF
