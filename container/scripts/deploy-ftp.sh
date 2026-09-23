#!/usr/bin/env bash
# Deploy the site to virtual hosting over FTP/FTPS.
#
# Uploads ./src (or --src DIR) to the hosting, mirroring the tree.
# Credentials NEVER live in this repo: they are read from a secret file
# OUTSIDE the repo (default below), overridable with --creds.
#
# Usage:
#   ./deploy-ftp.sh [--creds PATH] [--src DIR] [--remote-dir DIR]
#                   [--tls|--no-tls] [--delete] [--force] [--dry-run] [-v|--verbose]
#                   [-h|--help] [--files F ...] [--committed] [--staged] [--last-commit]
#                   [--include-tmp] [--include-all] [--include-arj-dirs]
#
# File selection (default: whole tree; flags combine as a union):
#   --files F ...   upload only these files/dirs (space and/or comma separated,
#                   repeatable; paths relative to --src, or src/... prefixed).
#                   Consumes following args until the next flag.
#                   Explicit --files entries bypass the junk excludes.
#   --committed     upload only git-tracked files under --src (git ls-files).
#   --staged        upload only staged changes under --src (git diff --cached).
#   --last-commit   upload only files changed in the last commit (HEAD) under
#                   --src (git diff-tree; merge commits diff against each parent).
#   --committed/--staged/--last-commit still skip deploy junk (unless
#                   --include-all, or the matching --include-tmp /
#                   --include-arj-dirs flag).
#                   --delete cannot be combined with any selection.
#
# Examples:
#   ./deploy-ftp.sh --files qa/index.php ru/qa/article.php --dry-run
#   ./deploy-ftp.sh --files "qa/index.php,ru/qa/article.php"
#   ./deploy-ftp.sh --staged --dry-run
#   ./deploy-ftp.sh --staged
#   ./deploy-ftp.sh --committed --dry-run
#   ./deploy-ftp.sh --last-commit --dry-run
#
# Options:
#   --creds PATH      secret file to source (default: ~/.config/aredel/ftp-creds.env)
#   --src DIR         local dir to upload (default: src)
#   --remote-dir DIR  override FTP_REMOTE_DIR from the creds file
#   --tls / --no-tls  force explicit FTPS on/off (default: FTP_USE_TLS from creds file)
#   --delete          delete remote files not present locally (default: off, safe)
#   --force           re-upload every file, skip the same-size check
#                     (default: off — same-size files are skipped as unchanged)
#   --dry-run         print plan (host, dirs, file count) without connecting
#   -v, --verbose     print each file being uploaded with its per-file status
#                     (default: summary lines only)
#   --include-tmp     upload everything under tmp/ _tmp/ .tmp/ dirs too
#                     (default: off — those dirs are junk-excluded)
#   --include-all     bypass ALL junk excludes and upload every file
#                     (accepts --include--all as an alias)
#   --include-arj-dirs upload dirs ending in -arj / _arj / .arj too
#                     (default: off — those dirs are junk-excluded)
#   --lint            run lint-php.sh on the same selection first, abort on errors
#   --php VER         PHP version for --lint (default 8.2, e.g. 8.0)
#   -h, --help        this help
#
# Secret file format (shell env file, chmod 600, e.g. ~/.config/aredel/ftp-creds.env):
#   FTP_HOST="ftp.example.com"
#   FTP_USER="hosting_user"
#   FTP_PASSWORD="secret-here"
#   FTP_PORT="21"            # optional, default 21
#   FTP_REMOTE_DIR="/"       # optional, default /
#   FTP_USE_TLS="false"      # optional, "true" for explicit FTPS
#   FTP_TIMEOUT="30"         # optional, seconds
#
# See deploy-ftp.creds.example for a copy-paste template.
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # scripts live in scripts/; project root (compose file, src/) is the runtime cwd

DEFAULT_CREDS="${HOME}/.config/aredel/ftp-creds.env"
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
COMMITTED=0
STAGED=0
LAST_COMMIT=0
INCLUDE_TMP=0
INCLUDE_ALL=0
INCLUDE_ARJ_DIRS=0

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
    --php) [ $# -ge 2 ] || { echo "ERROR: --php needs a value (e.g. 8.2, 8.0)." >&2; exit 2; }
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
    --committed|--commited) COMMITTED=1; shift ;;
    --staged) STAGED=1; shift ;;
    --last-commit) LAST_COMMIT=1; shift ;;
    --include-tmp) INCLUDE_TMP=1; shift ;;
    --include-all|--include--all) INCLUDE_ALL=1; shift ;;
    --include-arj-dirs) INCLUDE_ARJ_DIRS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
  esac
done

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }
[ -f "$CREDS" ] || {
  echo "ERROR: creds file '$CREDS' not found." >&2
  echo "Create it (chmod 600) from the template:" >&2
  echo "  mkdir -p \"$(dirname "$CREDS")\"" >&2
  echo "  cp scripts/deploy-ftp.creds.example \"$CREDS\"" >&2
  echo "  chmod 600 \"$CREDS\" && \$EDITOR \"$CREDS\"" >&2
  echo "Or pass another location: $0 --creds /path/to/creds.env" >&2
  exit 1
}

# Refuse a creds file from inside the repo (it would risk a commit).
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
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

# --- file selection: union of --files / --committed / --staged ---
# Explicit --files go to ONLY_EXP (bypass junk excludes); git-based go to
# ONLY_GIT (junk excludes still apply). Empty = whole tree.
ONLY_EXP=""
ONLY_GIT=""
if [ "${#FILES_LIST[@]}" -gt 0 ] || [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ] || [ "$LAST_COMMIT" -eq 1 ]; then
  if [ "$DELETE" -eq 1 ]; then
    echo "ERROR: --delete cannot be combined with file selection (--files/--committed/--staged/--last-commit)." >&2
    exit 2
  fi
  ONLY_EXP="$(mktemp)"; ONLY_GIT="$(mktemp)"
  trap 'rm -f "$ONLY_EXP" "$ONLY_GIT"' EXIT
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
        printf '%s\n' "$t" >> "$ONLY_EXP"
      else
        echo "WARNING: skipping '$tok' (not found under $SRC_DIR)." >&2
      fi
    done
  fi

  # 2) git-based selections (repo paths stripped to src-relative)
  if [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ] || [ "$LAST_COMMIT" -eq 1 ]; then
    TOP="$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null || true)"
    [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--committed/--staged/--last-commit need git)." >&2; exit 1; }
    PREFIX="${SRC_ABS#"$TOP"/}"
    [ "$PREFIX" != "$SRC_ABS" ] || { echo "ERROR: $SRC_DIR is outside the git repo." >&2; exit 1; }
    if [ "$COMMITTED" -eq 1 ]; then
      git -C "$SCRIPT_DIR/.." ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" >> "$ONLY_GIT" || true
    fi
    if [ "$STAGED" -eq 1 ]; then
      # NOTE: git diff has no --full-name; --name-only is already toplevel-relative.
      git -C "$SCRIPT_DIR/.." diff --cached --name-only -z --diff-filter=ACMR -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" >> "$ONLY_GIT" || true
    fi
    if [ "$LAST_COMMIT" -eq 1 ]; then
      # Files changed in HEAD (last commit), added/copied/modified/renamed.
      # -m keeps merge commits working (diff against each parent, union).
      git -C "$SCRIPT_DIR/.." diff-tree --no-commit-id --name-only -z -r -m --diff-filter=ACMR HEAD -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" >> "$ONLY_GIT" || true
    fi
  fi

  sort -u "$ONLY_EXP" -o "$ONLY_EXP"; sed -i '/^$/d' "$ONLY_EXP"
  sort -u "$ONLY_GIT" -o "$ONLY_GIT"; sed -i '/^$/d' "$ONLY_GIT"
  SEL_COUNT="$(( $(wc -l < "$ONLY_EXP" | tr -d ' ') + $(wc -l < "$ONLY_GIT" | tr -d ' ') ))"
  [ "$SEL_COUNT" -gt 0 ] || { echo "ERROR: selection is empty (no matching files)." >&2; exit 1; }
  echo "selection:   $SEL_COUNT file(s) (--files/--committed/--staged/--last-commit union)"
fi

export ADEL_ONLY_EXPLICIT="$ONLY_EXP" ADEL_ONLY_GIT="$ONLY_GIT"

# Default progress: announce what is about to be uploaded.
if [ "${#FILES_LIST[@]}" -gt 0 ]; then
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
elif [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ] || [ "$LAST_COMMIT" -eq 1 ]; then
  echo "Uploading git selection (--committed/--staged/--last-commit)."
else
  echo "Uploading full tree under $SRC_DIR/ ..."
fi

# --- pre-upload lint (same selection; runs even with --dry-run) ---
if [ "$LINT" -eq 1 ]; then
  [ -x scripts/lint-php.sh ] || { echo "ERROR: scripts/lint-php.sh not found (run from container/ or scripts/)." >&2; exit 1; }
  LINT_ARGS=(--src "$SRC_DIR" --php "$PHP_VER")
  if [ "${#FILES_LIST[@]}" -gt 0 ]; then
    LINT_ARGS+=(--files "${FILES_LIST[@]}")
  fi
  [ "$COMMITTED" -eq 1 ] && LINT_ARGS+=(--committed)
  [ "$STAGED" -eq 1 ] && LINT_ARGS+=(--staged)
  if [ "$LAST_COMMIT" -eq 1 ]; then
    # lint-php.sh has no --last-commit: expand the resolved list to --files.
    while IFS= read -r _lf; do
      [ -n "$_lf" ] && LINT_ARGS+=(--files "$_lf")
    done < "$ONLY_GIT"
  fi
  echo "==> pre-upload lint ..."
  ./scripts/lint-php.sh "${LINT_ARGS[@]}" || { echo "ERROR: lint failed, aborting upload." >&2; exit 1; }
elif [ "$PHP_SET" -eq 1 ]; then
  echo "WARNING: --php has no effect without --lint." >&2
fi

export ADEL_FTP_HOST="$FTP_HOST" ADEL_FTP_USER="$FTP_USER" ADEL_FTP_PASS="$FTP_PASSWORD"
export ADEL_FTP_PORT="$FTP_PORT" ADEL_FTP_REMOTE="$FTP_REMOTE_DIR" ADEL_FTP_TLS="$FTP_USE_TLS"
export ADEL_FTP_TIMEOUT="$FTP_TIMEOUT" ADEL_SRC="$SRC_DIR" ADEL_DELETE="$DELETE" ADEL_DRYRUN="$DRY_RUN"
export ADEL_VERBOSE="$VERBOSE" ADEL_COMMITTED="$COMMITTED" ADEL_STAGED="$STAGED"
export ADEL_LAST_COMMIT="$LAST_COMMIT"
export ADEL_FORCE="$FORCE"
export ADEL_INCLUDE_TMP="$INCLUDE_TMP" ADEL_INCLUDE_ALL="$INCLUDE_ALL"
export ADEL_INCLUDE_ARJ_DIRS="$INCLUDE_ARJ_DIRS"

python3 - <<'EOF'
import fnmatch, os, posixpath, sys
from ftplib import FTP, FTP_TLS, error_perm

host = os.environ["ADEL_FTP_HOST"]
user = os.environ["ADEL_FTP_USER"]
pw = os.environ["ADEL_FTP_PASS"]
port = int(os.environ.get("ADEL_FTP_PORT", "21") or 21)
remote_base = os.environ.get("ADEL_FTP_REMOTE", "/") or "/"
use_tls = os.environ.get("ADEL_FTP_TLS", "false").lower() in ("1", "true", "yes")
timeout = int(os.environ.get("ADEL_FTP_TIMEOUT", "30") or 30)
src = os.environ.get("ADEL_SRC", "src")
delete = os.environ.get("ADEL_DELETE") == "1"
dryrun = os.environ.get("ADEL_DRYRUN") == "1"
verbose = os.environ.get("ADEL_VERBOSE") == "1"
committed_flag = os.environ.get("ADEL_COMMITTED") == "1"
staged_flag = os.environ.get("ADEL_STAGED") == "1"
last_commit_flag = os.environ.get("ADEL_LAST_COMMIT") == "1"
force = os.environ.get("ADEL_FORCE") == "1"
include_tmp = os.environ.get("ADEL_INCLUDE_TMP") == "1"
include_all = os.environ.get("ADEL_INCLUDE_ALL") == "1"
include_arj_dirs = os.environ.get("ADEL_INCLUDE_ARJ_DIRS") == "1"

# Junk that must never go to hosting (mirrors aredel/.gitignore logic).
# NOTE: images are intentionally NOT excluded — hosting needs them.
# tmp/_tmp/.tmp dirs are excluded by default (session/demo junk); --include-tmp
# re-includes them, --include-all bypasses every rule below.
# Dirs ending in -arj / _arj / .arj are excluded by default; --include-arj-dirs
# re-includes them, --include-all bypasses every rule below.
BASE_EXCLUDE_DIRS = {"reports", "allure-results", "allure-results-host",
                "__pycache__", ".pytest_cache", ".git",
                ".venv", "venv", "node_modules"}
TMP_EXCLUDE_DIRS = {"tmp", "_tmp", ".tmp"}
ARJ_DIR_SUFFIXES = ("-arj", "_arj", ".arj")

def is_arj_dir(name):
    """True if a dir name ends in -arj / _arj / .arj."""
    return name.endswith(ARJ_DIR_SUFFIXES)

def is_arj_excluded(name):
    """True if an -arj-suffixed dir is currently excluded."""
    return is_arj_dir(name) and not include_all and not include_arj_dirs
BASE_EXCLUDE_FILES = ["*.log", "*.db", "*.sqlite", "*.sqlite3", "*.db-journal",
                 "sess_*", "* copy*", "*.bak", "*.orig", "*~", "*.swp",
                 "*.swo", ".DS_Store", "Thumbs.db"]
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
    return None

def is_tmp_path(rel):
    """True if the file lives under a tmp/ _tmp/ .tmp/ dir."""
    return any(p in TMP_EXCLUDE_DIRS for p in rel.split(os.sep)[:-1])

def not_selected_reason():
    if committed_flag or staged_flag or last_commit_flag:
        return ("skipped (untracked / not-selected: not in "
                "--files/--committed/--staged/--last-commit union; untracked files are "
                "never selected by --committed/--staged/--last-commit, use --files)")
    return "skipped (not-selected: not in --files list)"

def load_sel(path):
    s = set()
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    s.add(line)
    return s

explicit = load_sel(os.environ.get("ADEL_ONLY_EXPLICIT", ""))
gitsel = load_sel(os.environ.get("ADEL_ONLY_GIT", ""))
select_mode = bool(explicit or gitsel)

local_files = {}  # rel_posix -> abs path
skipped_not_selected = []  # [(rel_posix, reason)]
skipped_junk = []  # [(rel_posix, reason)]
skipped_junk_dirs = []  # [(rel_posix_dir, reason)]
for root, dirs, files in os.walk(src, followlinks=True):
    # Record pruned junk dirs explicitly instead of silently dropping them,
    # then prune so we don't descend into e.g. node_modules/.venv.
    dirs.sort()
    pruned = [d for d in dirs if d in EXCLUDE_DIRS or is_arj_excluded(d)]
    for d in pruned:
        drel = os.path.relpath(os.path.join(root, d), src).replace(os.sep, "/") + "/"
        skipped_junk_dirs.append((drel, f"skipped (junk-excluded: dir {d}/)"))
    dirs[:] = [d for d in dirs if d not in pruned]
    for f in sorted(files):
        ap = os.path.join(root, f)
        if not os.path.isfile(ap) or os.path.islink(ap) and not os.path.exists(ap):
            continue
        rel = os.path.relpath(ap, src)
        rel_posix = rel.replace(os.sep, "/")
        # --include-tmp uploads everything under tmp/ _tmp/ .tmp/ dirs;
        # --include-all already emptied the exclude lists, this is belt & braces.
        under_tmp = is_tmp_path(rel)
        er = None if (include_all or (include_tmp and under_tmp)) else exclude_reason(rel)
        junk_note = f"; also junk-excluded ({er}, explicit --files bypasses this)" if er else ""
        if select_mode and rel_posix not in explicit and rel_posix not in gitsel:
            skipped_not_selected.append((rel_posix, not_selected_reason() + junk_note))
            continue
        if er and rel_posix not in explicit:
            skipped_junk.append((rel_posix,
                f"skipped (junk-excluded: {er}; explicit --files bypasses this)"))
            continue
        local_files[rel_posix] = ap

n_junk = len(skipped_junk) + len(skipped_junk_dirs)
print(f"local files to consider: {len(local_files)}")
print(f"skipped (not-selected/untracked): {len(skipped_not_selected)}")
print(f"skipped (junk-excluded): {n_junk} "
      f"({len(skipped_junk)} files + {len(skipped_junk_dirs)} dirs)")

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

Cls = FTP_TLS if use_tls else FTP
ftp = Cls()
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
step = max(1, total // 100)  # default mode: ~100 progress updates, no silent gaps
for i, rel in enumerate(sorted(local_files), 1):
    rpath = posixpath.join(remote_base.rstrip("/") or "/", rel)
    ensure_dir(posixpath.dirname(rpath))
    lsize = os.path.getsize(local_files[rel])
    rsize = None if force else remote_size(rpath)
    if rsize == lsize:
        skipped_unchanged += 1
        if verbose:
            print(f"uploading: {rel}")
            print(f"  status: skipped (unchanged: local {lsize} B == remote {rsize} B)")
    else:
        if verbose:
            print(f"uploading: {rel}")
        try:
            with open(local_files[rel], "rb") as fh:
                ftp.storbinary(f"STOR {rpath}", fh)
        except Exception as e:
            failed += 1
            if verbose:
                print(f"  status: FAILED ({e})")
            else:
                print(f"FAILED: {rel} ({e})")
        else:
            uploaded += 1
            if verbose:
                print(f"  status: uploaded (forced)" if force else f"  status: uploaded")
    if not verbose and (i % step == 0 or i == total):
        print(f"  progress: {i}/{total} (uploaded={uploaded} skipped_unchanged={skipped_unchanged} failed={failed})", flush=True)
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
