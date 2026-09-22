#!/usr/bin/env bash
# Deploy the site to virtual hosting over FTP/FTPS.
#
# Uploads ./src (or --src DIR) to the hosting, mirroring the tree.
# Credentials NEVER live in this repo: they are read from a secret file
# OUTSIDE the repo (default below), overridable with --creds.
#
# Usage:
#   ./deploy-ftp.sh [--creds PATH] [--src DIR] [--remote-dir DIR]
#                   [--tls|--no-tls] [--delete] [--dry-run] [-h|--help]
#                   [--files F ...] [--committed] [--staged]
#
# File selection (default: whole tree; flags combine as a union):
#   --files F ...   upload only these files/dirs (space and/or comma separated,
#                   repeatable; paths relative to --src, or src/... prefixed).
#                   Consumes following args until the next flag.
#                   Explicit --files entries bypass the junk excludes.
#   --committed     upload only git-tracked files under --src (git ls-files).
#   --staged        upload only staged changes under --src (git diff --cached).
#                   --committed/--staged still skip deploy junk.
#                   --delete cannot be combined with any selection.
#
# Examples:
#   ./deploy-ftp.sh --files qa/index.php ru/qa/article.php --dry-run
#   ./deploy-ftp.sh --files "qa/index.php,ru/qa/article.php"
#   ./deploy-ftp.sh --staged --dry-run
#   ./deploy-ftp.sh --staged
#   ./deploy-ftp.sh --committed --dry-run
#
# Options:
#   --creds PATH      secret file to source (default: ~/.config/aredel/ftp-creds.env)
#   --src DIR         local dir to upload (default: src)
#   --remote-dir DIR  override FTP_REMOTE_DIR from the creds file
#   --tls / --no-tls  force explicit FTPS on/off (default: FTP_USE_TLS from creds file)
#   --delete          delete remote files not present locally (default: off, safe)
#   --dry-run         print plan (host, dirs, file count) without connecting
#   --lint            run lint-php.sh on the same selection first, abort on errors
#   --php VER         PHP version for --lint (default 8.0, e.g. 8.2)
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DEFAULT_CREDS="${HOME}/.config/aredel/ftp-creds.env"
CREDS="$DEFAULT_CREDS"
SRC_DIR="src"
REMOTE_DIR_OVERRIDE=""
TLS_OVERRIDE=""
DELETE=0
DRY_RUN=0
LINT=0
PHP_VER="8.0"
PHP_SET=0
FILES_LIST=()
COMMITTED=0
STAGED=0

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

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
    --dry-run) DRY_RUN=1; shift ;;
    --lint) LINT=1; shift ;;
    --php) [ $# -ge 2 ] || { echo "ERROR: --php needs a value (e.g. 8.0, 8.2)." >&2; exit 2; }
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
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
  esac
done

[ -d "$SRC_DIR" ] || { echo "ERROR: source dir '$SRC_DIR' not found." >&2; exit 1; }
[ -f "$CREDS" ] || {
  echo "ERROR: creds file '$CREDS' not found." >&2
  echo "Create it (chmod 600) from the template:" >&2
  echo "  mkdir -p \"$(dirname "$CREDS")\"" >&2
  echo "  cp deploy-ftp.creds.example \"$CREDS\"" >&2
  echo "  chmod 600 \"$CREDS\" && \$EDITOR \"$CREDS\"" >&2
  echo "Or pass another location: $0 --creds /path/to/creds.env" >&2
  exit 1
}

# Refuse to use a creds file from inside the repo (it would risk a commit).
case "$(cd "$(dirname "$CREDS")" 2>/dev/null && pwd -P)" in
  "$SCRIPT_DIR"*|"${SCRIPT_DIR}/"*)
    echo "ERROR: creds file must live OUTSIDE the repo, not under $SCRIPT_DIR" >&2
    echo "Move it e.g. to $DEFAULT_CREDS and pass --creds if needed." >&2
    exit 1
    ;;
esac

# shellcheck disable=SC1090
set -a; . "$CREDS"; set +a

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

# --- file selection: union of --files / --committed / --staged ---
# Explicit --files go to ONLY_EXP (bypass junk excludes); git-based go to
# ONLY_GIT (junk excludes still apply). Empty = whole tree.
ONLY_EXP=""
ONLY_GIT=""
if [ "${#FILES_LIST[@]}" -gt 0 ] || [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ]; then
  if [ "$DELETE" -eq 1 ]; then
    echo "ERROR: --delete cannot be combined with file selection (--files/--committed/--staged)." >&2
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
        "$SRC_DIR"/*) t="${t#$SRC_DIR/}" ;;
        container/src/*) t="${t#container/src/}" ;;
      esac
      case "$t" in
        /*)
          case "$t" in
            "$SRC_ABS"/*) t="${t#$SRC_ABS/}" ;;
            *) echo "WARNING: skipping '$tok' (outside $SRC_DIR)." >&2; continue ;;
          esac
          ;;
      esac
      ap="$SRC_ABS/$t"
      if [ -d "$ap" ] && [ ! -L "$ap" ]; then
        find "$ap" -type f | while IFS= read -r f; do
          printf '%s\n' "${f#$SRC_ABS/}"
        done >> "$ONLY_EXP"
      elif [ -f "$ap" ]; then
        printf '%s\n' "$t" >> "$ONLY_EXP"
      else
        echo "WARNING: skipping '$tok' (not found under $SRC_DIR)." >&2
      fi
    done
  fi

  # 2) git-based selections (repo paths stripped to src-relative)
  if [ "$COMMITTED" -eq 1 ] || [ "$STAGED" -eq 1 ]; then
    TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
    [ -n "$TOP" ] || { echo "ERROR: not inside a git repo (--committed/--staged need git)." >&2; exit 1; }
    PREFIX="${SRC_ABS#$TOP/}"
    [ "$PREFIX" != "$SRC_ABS" ] || { echo "ERROR: $SRC_DIR is outside the git repo." >&2; exit 1; }
    if [ "$COMMITTED" -eq 1 ]; then
      git -C "$SCRIPT_DIR" ls-files --full-name -z -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" >> "$ONLY_GIT" || true
    fi
    if [ "$STAGED" -eq 1 ]; then
      # NOTE: git diff has no --full-name; --name-only is already toplevel-relative.
      git -C "$SCRIPT_DIR" diff --cached --name-only -z -- "$SRC_DIR" 2>/dev/null \
        | tr '\0' '\n' | sed "s#^$PREFIX/##" >> "$ONLY_GIT" || true
    fi
  fi

  sort -u "$ONLY_EXP" -o "$ONLY_EXP"; sed -i '/^$/d' "$ONLY_EXP"
  sort -u "$ONLY_GIT" -o "$ONLY_GIT"; sed -i '/^$/d' "$ONLY_GIT"
  SEL_COUNT="$(( $(wc -l < "$ONLY_EXP" | tr -d ' ') + $(wc -l < "$ONLY_GIT" | tr -d ' ') ))"
  [ "$SEL_COUNT" -gt 0 ] || { echo "ERROR: selection is empty (no matching files)." >&2; exit 1; }
  echo "selection:   $SEL_COUNT file(s) (--files/--committed/--staged union)"
fi

export ADEL_ONLY_EXPLICIT="$ONLY_EXP" ADEL_ONLY_GIT="$ONLY_GIT"

# --- pre-upload lint (same selection; runs even with --dry-run) ---
if [ "$LINT" -eq 1 ]; then
  [ -x ./lint-php.sh ] || { echo "ERROR: lint-php.sh not found next to $0." >&2; exit 1; }
  LINT_ARGS=(--src "$SRC_DIR" --php "$PHP_VER")
  if [ "${#FILES_LIST[@]}" -gt 0 ]; then
    LINT_ARGS+=(--files "${FILES_LIST[@]}")
  fi
  [ "$COMMITTED" -eq 1 ] && LINT_ARGS+=(--committed)
  [ "$STAGED" -eq 1 ] && LINT_ARGS+=(--staged)
  echo "==> pre-upload lint ..."
  ./lint-php.sh "${LINT_ARGS[@]}" || { echo "ERROR: lint failed, aborting upload." >&2; exit 1; }
elif [ "$PHP_SET" -eq 1 ]; then
  echo "WARNING: --php has no effect without --lint." >&2
fi

export ADEL_FTP_HOST="$FTP_HOST" ADEL_FTP_USER="$FTP_USER" ADEL_FTP_PASS="$FTP_PASSWORD"
export ADEL_FTP_PORT="$FTP_PORT" ADEL_FTP_REMOTE="$FTP_REMOTE_DIR" ADEL_FTP_TLS="$FTP_USE_TLS"
export ADEL_FTP_TIMEOUT="$FTP_TIMEOUT" ADEL_SRC="$SRC_DIR" ADEL_DELETE="$DELETE" ADEL_DRYRUN="$DRY_RUN"

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

# Junk that must never go to hosting (mirrors aredel/.gitignore logic).
# NOTE: images are intentionally NOT excluded — hosting needs them.
EXCLUDE_DIRS = {"reports", "allure-results", "allure-results-host",
                "__pycache__", ".pytest_cache", ".git",
                ".venv", "venv", "node_modules"}
EXCLUDE_FILES = ["*.log", "*.db", "*.sqlite", "*.sqlite3", "*.db-journal",
                 "sess_*", "* copy*", "*.bak", "*.orig", "*~", "*.swp",
                 "*.swo", ".DS_Store", "Thumbs.db"]

def excluded(rel):
    parts = rel.split(os.sep)
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return True
    base = parts[-1]
    return any(fnmatch.fnmatch(base, pat) for pat in EXCLUDE_FILES)

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
for root, dirs, files in os.walk(src, followlinks=True):
    # prune excluded dirs in place (also skip dot/underscore framework-backend dirs? No:
    # hosting needs the full site, unlike the sitemap generator — only prune junk).
    dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
    for f in sorted(files):
        ap = os.path.join(root, f)
        if not os.path.isfile(ap) or os.path.islink(ap) and not os.path.exists(ap):
            continue
        rel = os.path.relpath(ap, src)
        rel_posix = rel.replace(os.sep, "/")
        if select_mode and rel_posix not in explicit and rel_posix not in gitsel:
            continue
        if rel_posix not in explicit and excluded(rel):
            continue
        local_files[rel_posix] = ap

print(f"local files to consider: {len(local_files)}")
if dryrun:
    print("dry-run: no connection, nothing uploaded.")
    for rel in sorted(local_files)[:10]:
        print(f"  would upload: {rel}")
    if len(local_files) > 10:
        print(f"  ... and {len(local_files) - 10} more")
    sys.exit(0)

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
uploaded = skipped = 0
for rel in sorted(local_files):
    rpath = posixpath.join(remote_base.rstrip("/") or "/", rel)
    ensure_dir(posixpath.dirname(rpath))
    lsize = os.path.getsize(local_files[rel])
    rsize = remote_size(rpath)
    if rsize == lsize:
        skipped += 1
        continue
    with open(local_files[rel], "rb") as fh:
        ftp.storbinary(f"STOR {rpath}", fh)
    uploaded += 1
print(f"uploaded: {uploaded}, unchanged (skipped): {skipped}")

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
