#!/usr/bin/env bash
# Download hosting logs over FTP/FTPS into site_logs/.
#
# Same credentials as deploy-ftp.sh (default: ~/.config/php_engine/ftp-creds.env).
# Remote layout this was built for (shared hosting where the FTP user
# lands in the dev site root):
#   prod_logs/  -> <dest>/prod/   (production logs)
#   dev_logs/   -> <dest>/dev/    (usually empty)
#   ROOT_PREFIX files at FTP root -> <dest>/dev/ (dev logs, default prefix below)
# Config via SITE_ENV (site.env): PROD_LOG_DIR, DEV_LOG_DIR, ROOT_PREFIX act as
# defaults; --prod-dir / --dev-dir / --root-prefix flags override them.
#
# Usage:
#   ./download-logs.sh [--creds PATH] [--dest DIR] [--only prod|dev|root|all]
#                      [--prod-dir NAME] [--dev-dir NAME] [--root-prefix PFX]
#                      [--force] [--dry-run] [-v|--verbose] [--tls|--no-tls]
#                      [-h|--help]
#   SITE_ENV=path/to/site.env ./download-logs.sh ...  (optional shared defaults)
#
#   --only WHAT   which remote sources to fetch (default: all).
#                 prod = $PROD_LOG_DIR/, dev = $DEV_LOG_DIR/,
#                 root = ${ROOT_PREFIX}*.log at FTP root -> dest/dev/.
#   --dest DIR    local dir receiving prod/ and dev/ (default: <repo>/site_logs)
#   --prod-dir NAME  remote prod log dir (default: $PROD_LOG_DIR or prod_logs)
#   --dev-dir NAME   remote dev log dir (default: $DEV_LOG_DIR or dev_logs)
#   --root-prefix PFX  filename prefix for root dev logs
#                      (default: $ROOT_PREFIX or dev.example.com.)
#   --force       re-download every file (default: skip same-size files)
#   --dry-run     list what would be downloaded without connecting
#   -v            per-file status lines (default: progress + summary)
set -euo pipefail

_START_DIR="$(pwd -P)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../..  # container/ is the runtime cwd; repo root is one level up

DEFAULT_CREDS="${HOME}/.config/php_engine/ftp-creds.env"
CREDS="$DEFAULT_CREDS"
DEST=""
ONLY="all"
PROD_DIR_OVERRIDE=""
DEV_DIR_OVERRIDE=""
ROOT_PREFIX_OVERRIDE=""
FORCE=0
DRY_RUN=0
VERBOSE=0
TLS_OVERRIDE=""

usage() { local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac; sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --creds) [ $# -ge 2 ] || { echo "ERROR: --creds needs a value." >&2; exit 2; }
      CREDS="$2"; shift 2 ;;
    --creds=*) CREDS="${1#--creds=}"; shift ;;
    --dest) [ $# -ge 2 ] || { echo "ERROR: --dest needs a value." >&2; exit 2; }
      DEST="$2"; shift 2 ;;
    --dest=*) DEST="${1#--dest=}"; shift ;;
    --only) [ $# -ge 2 ] || { echo "ERROR: --only needs a value." >&2; exit 2; }
      ONLY="$2"; shift 2 ;;
    --only=*) ONLY="${1#--only=}"; shift ;;
    --prod-dir) [ $# -ge 2 ] || { echo "ERROR: --prod-dir needs a value." >&2; exit 2; }
      PROD_DIR_OVERRIDE="$2"; shift 2 ;;
    --prod-dir=*) PROD_DIR_OVERRIDE="${1#--prod-dir=}"; shift ;;
    --dev-dir) [ $# -ge 2 ] || { echo "ERROR: --dev-dir needs a value." >&2; exit 2; }
      DEV_DIR_OVERRIDE="$2"; shift 2 ;;
    --dev-dir=*) DEV_DIR_OVERRIDE="${1#--dev-dir=}"; shift ;;
    --root-prefix) [ $# -ge 2 ] || { echo "ERROR: --root-prefix needs a value." >&2; exit 2; }
      ROOT_PREFIX_OVERRIDE="$2"; shift 2 ;;
    --root-prefix=*) ROOT_PREFIX_OVERRIDE="${1#--root-prefix=}"; shift ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    --tls) TLS_OVERRIDE="true"; shift ;;
    --no-tls) TLS_OVERRIDE="false"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
  esac
done

case "$ONLY" in
  all|prod|dev|root) ;;
  *) echo "ERROR: --only must be prod, dev, root or all." >&2; exit 2 ;;
esac

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

# CLI flags win over SITE_ENV (PROD_LOG_DIR/DEV_LOG_DIR/ROOT_PREFIX), built-ins last.
PROD_DIR="${PROD_DIR_OVERRIDE:-${PROD_LOG_DIR:-prod_logs}}"
DEV_DIR="${DEV_DIR_OVERRIDE:-${DEV_LOG_DIR:-dev_logs}}"
ROOT_PREFIX="${ROOT_PREFIX_OVERRIDE:-${ROOT_PREFIX:-dev.example.com.}}"

APP_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
[ -z "$DEST" ] && DEST="$REPO_ROOT/site_logs"
mkdir -p "$DEST"

[ -f "$CREDS" ] || { echo "ERROR: creds file '$CREDS' not found." >&2; exit 1; }
case "$(cd "$(dirname "$CREDS")" 2>/dev/null && pwd -P)" in
  "$APP_ROOT"*|"${APP_ROOT}/"*)
    echo "ERROR: creds file must live OUTSIDE the repo, not under $APP_ROOT" >&2
    exit 1 ;;
esac

set -a
# shellcheck disable=SC1090
. "$CREDS"
set +a

FTP_HOST="${FTP_HOST:-}"
FTP_USER="${FTP_USER:-}"
FTP_PASSWORD="${FTP_PASSWORD:-}"
FTP_PORT="${FTP_PORT:-21}"
FTP_USE_TLS="${TLS_OVERRIDE:-${FTP_USE_TLS:-false}}"
FTP_TIMEOUT="${FTP_TIMEOUT:-30}"

[ -n "$FTP_HOST" ] || { echo "ERROR: FTP_HOST is empty in $CREDS" >&2; exit 1; }
[ -n "$FTP_USER" ] || { echo "ERROR: FTP_USER is empty in $CREDS" >&2; exit 1; }
[ -n "$FTP_PASSWORD" ] || { echo "ERROR: FTP_PASSWORD is empty in $CREDS" >&2; exit 1; }

echo "host: $FTP_HOST:$FTP_PORT (tls=$FTP_USE_TLS)"
echo "dest: $DEST/ (prod/ dev/)"
echo "only: $ONLY / force: $([ "$FORCE" -eq 1 ] && echo yes || echo no)"

export ADEL_FTP_HOST="$FTP_HOST" ADEL_FTP_USER="$FTP_USER" ADEL_FTP_PASS="$FTP_PASSWORD"
export ADEL_FTP_PORT="$FTP_PORT" ADEL_FTP_TLS="$FTP_USE_TLS"
export ADEL_FTP_TIMEOUT="$FTP_TIMEOUT" ADEL_DEST="$DEST" ADEL_ONLY="$ONLY"
export ADEL_PROD_DIR="$PROD_DIR" ADEL_DEV_DIR="$DEV_DIR" ADEL_ROOT_PREFIX="$ROOT_PREFIX"
export ADEL_DRYRUN="$DRY_RUN" ADEL_VERBOSE="$VERBOSE" ADEL_FORCE="$FORCE"

python3 - <<'EOF'
import os
import sys
from ftplib import FTP, FTP_TLS, error_perm

host = os.environ["ADEL_FTP_HOST"]
user = os.environ["ADEL_FTP_USER"]
pw = os.environ["ADEL_FTP_PASS"]
port = int(os.environ.get("ADEL_FTP_PORT", "21") or 21)
use_tls = os.environ.get("ADEL_FTP_TLS", "false").lower() in ("1", "true", "yes")
timeout = int(os.environ.get("ADEL_FTP_TIMEOUT", "30") or 30)
dest = os.environ["ADEL_DEST"]
only = os.environ.get("ADEL_ONLY", "all")
dryrun = os.environ.get("ADEL_DRYRUN") == "1"
verbose = os.environ.get("ADEL_VERBOSE") == "1"
force = os.environ.get("ADEL_FORCE") == "1"

# (remote dir or root glob, local subdir under dest)
SOURCES = {
    "prod": [(os.environ.get("ADEL_PROD_DIR", "prod_logs"), "prod", None)],
    "dev": [(os.environ.get("ADEL_DEV_DIR", "dev_logs"), "dev", None)],
    "root": [(None, "dev", os.environ.get("ADEL_ROOT_PREFIX", "dev.example.com."))],
}
if only == "all":
    wanted = SOURCES["prod"] + SOURCES["dev"] + SOURCES["root"]
else:
    wanted = SOURCES[only]


def list_dir(ftp, path):
    """Return [(name, is_dir)] for a remote dir (MLSD, fallback NLST)."""
    try:
        return [
            (name, facts.get("type") == "dir") for name, facts in ftp.mlsd(path)
        ]
    except Exception:
        pass  # no MLSD on this server; fall back to NLST + CWD probing
    out = []
    for name in ftp.nlst(path):
        if "/" in name:
            base, probe = name.rsplit("/", 1)[-1], name
        else:
            base, probe = name, f"{path}/{name}" if path else name
        if base in (".", ".."):
            continue
        if not probe.startswith("/"):
            probe = "/" + probe
        try:
            ftp.cwd(probe)
            out.append((base, True))
        except error_perm:
            out.append((base, False))
        finally:
            try:
                ftp.cwd("/")
            except Exception:
                pass
    return out


def collect(ftp, remote_dir):
    """Recursively list files under remote_dir -> [(remote_path, size|None)]."""
    found = []

    def walk(path):
        try:
            entries = list_dir(ftp, path)
        except Exception as error:
            print(f"WARNING: cannot list {path or '/'} ({error})")
            return
        for name, is_dir in entries:
            if name in (".", ".."):
                continue
            full = f"{path}/{name}" if path else name
            if is_dir:
                walk(full)
            else:
                try:
                    size = ftp.size(full)
                except Exception:
                    size = None
                found.append((full, size))

    walk(remote_dir)
    return found


def collect_root_glob(ftp, prefix):
    """Files at FTP root whose name starts with prefix."""
    try:
        names = ftp.nlst()
    except Exception as error:
        print(f"WARNING: cannot list FTP root ({error})")
        return []
    found = []
    for name in names:
        if "/" in name or not name.startswith(prefix):
            continue
        try:
            size = ftp.size(name)
        except Exception:
            size = None
        found.append((name, size))
    return found


def plan(ftp):
    """Build [(remote, local, size)] for all wanted sources."""
    jobs = []
    for remote_dir, local_sub, prefix in wanted:
        if prefix is not None:
            files = collect_root_glob(ftp, prefix)
            strip = 0
        else:
            files = collect(ftp, remote_dir)
            strip = len(remote_dir) + 1
        for remote, size in files:
            rel = remote[strip:]
            local = os.path.join(dest, local_sub, rel)
            jobs.append((remote, local, size))
    return sorted(jobs)


def run(jobs, ftp):
    downloaded = skipped = failed = 0
    total = len(jobs)
    for i, (remote, local, size) in enumerate(jobs, 1):
        if not force and size is not None and os.path.isfile(local):
            try:
                if os.path.getsize(local) == size:
                    skipped += 1
                    if verbose:
                        print(f"  skipped (unchanged): {remote}")
                    continue
            except OSError:
                pass
        if dryrun:
            if verbose or downloaded < 20:
                print(f"  would download: {remote} -> {local}")
            downloaded += 1
            continue
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        if os.path.isfile(local) and not os.access(local, os.W_OK):
            try:
                os.chmod(local, 0o644)  # refreshed logs are often read-only
            except OSError:
                pass
        try:
            with open(local, "wb") as handle:
                ftp.retrbinary(f"RETR {remote}", handle.write)
        except Exception as error:
            failed += 1
            print(f"FAILED: {remote} ({error})")
        else:
            downloaded += 1
            if verbose:
                print(f"  downloaded: {remote} -> {local}")
        if not verbose and (i % max(1, total // 50) == 0 or i == total):
            print(f"  progress: {i}/{total} "
                  f"(downloaded={downloaded} skipped={skipped} failed={failed})",
                  flush=True)
    return downloaded, skipped, failed


if dryrun:
    print("dry-run: listing remotely, nothing downloaded.")
    Cls = FTP_TLS if use_tls else FTP
    ftp = Cls()
    try:
        ftp.connect(host, port, timeout=timeout)
        ftp.login(user, pw)
    except Exception as error:
        print(f"ERROR: cannot connect/login: {error}")
        sys.exit(1)
    if use_tls:
        ftp.prot_p()
    ftp.set_pasv(True)
    jobs = plan(ftp)
    ftp.quit()
    run(jobs, None)  # dry-run path never touches ftp
    print(f"would download: {len(jobs)} file(s)")
    sys.exit(0)

Cls = FTP_TLS if use_tls else FTP
ftp = Cls()
try:
    ftp.connect(host, port, timeout=timeout)
    ftp.login(user, pw)
except Exception as error:
    print(f"ERROR: cannot connect/login to {host}:{port} as {user}: {error}")
    sys.exit(1)
if use_tls:
    ftp.prot_p()
ftp.set_pasv(True)
jobs = plan(ftp)
print(f"remote files to consider: {len(jobs)}")
downloaded, skipped, failed = run(jobs, ftp)
ftp.quit()
print(f"downloaded: {downloaded}, skipped (unchanged): {skipped}, failed: {failed}")
if failed:
    sys.exit(1)
print("done.")
EOF
