#!/usr/bin/env bash
# Fix host permissions so Apache (www-data, uid 33) in the php-apache
# container can read src/ via the bind mount ./src:/var/www/html.
#
# Usage:
#   ./fix-permissions.sh            # fix + verify
#   ./fix-permissions.sh --check    # diagnose only, change nothing
#
# Must be run from the directory containing docker-compose.yml
# (or pass SRC_DIR as first non-flag argument, default: src).
set -euo pipefail

CHECK_ONLY=0
SRC_DIR="src"
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    -h|--help)
      echo "Usage: $0 [--check] [src_dir]"
      echo "  --check   diagnose only, change nothing"
      exit 0
      ;;
    *) SRC_DIR="$arg" ;;
  esac
done

URL_BASE="${URL_BASE:-http://127.0.0.1:8080}"

fail() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[ -f docker-compose.yml ] || fail "docker-compose.yml not found. Run from container/ directory."
[ -d "$SRC_DIR" ] || fail "source dir '$SRC_DIR' not found."

info "Checking permissions in $SRC_DIR/ ..."
bad_files=$(find "$SRC_DIR" -type f ! -perm -o+r | wc -l)
bad_dirs=$(find "$SRC_DIR" -type d ! -perm -o+rx | wc -l)
echo "Files not world-readable:   $bad_files"
echo "Dirs not traversable (o+rx): $bad_dirs"
stat -c 'index.php: %a %U:%G %n' "$SRC_DIR/index.php" 2>/dev/null || true

# Framework symlinks: pages require $DOCROOT/.php/... and /.css/ao.css,
# but this checkout only has them under ao/. Check their presence too.
missing_links=0
for dot in .php .css; do
  if [ -e "$SRC_DIR/$dot" ]; then
    echo "Symlink/dir $SRC_DIR/$dot: present ($(stat -c '%N' "$SRC_DIR/$dot"))"
  else
    echo "Symlink/dir $SRC_DIR/$dot: MISSING"
    missing_links=1
  fi
done

if [ "$CHECK_ONLY" -eq 1 ]; then
  if [ "$bad_files" -gt 0 ] || [ "$bad_dirs" -gt 0 ] || [ "$missing_links" -ne 0 ]; then
    echo "Result: BROKEN (permissions and/or missing src/.php + src/.css symlinks)."
    echo "Run without --check to fix."
    exit 2
  else
    echo "Result: permissions OK."
    exit 0
  fi
fi

if [ "$bad_files" -eq 0 ] && [ "$bad_dirs" -eq 0 ]; then
  info "Nothing to fix — permissions already OK."
else
  info "Fixing directories to 755 ..."
  find "$SRC_DIR" -type d -exec chmod 755 {} +

  info "Fixing files to 644 ..."
  find "$SRC_DIR" -type f -exec chmod 644 {} +

  # Uncomment if you want helper scripts to stay executable locally:
  # find "$SRC_DIR" -type f -name '*.sh' -exec chmod 755 {} +

  echo "After fix:"
  echo "Files not world-readable:   $(find "$SRC_DIR" -type f ! -perm -o+r | wc -l)"
  echo "Dirs not traversable:       $(find "$SRC_DIR" -type d ! -perm -o+rx | wc -l)"
  stat -c 'index.php: %a %U:%G %n' "$SRC_DIR/index.php"
fi

# 2b. Framework symlinks (subpages need $DOCROOT/.php + /.css; only ao/ has copies).
for dot in .php .css; do
  if [ ! -e "$SRC_DIR/$dot" ]; then
    if [ -e "$SRC_DIR/ao/$dot" ]; then
      info "Creating symlink $SRC_DIR/$dot -> ao/$dot ..."
      ln -s "ao/$dot" "$SRC_DIR/$dot"
    else
      fail "$SRC_DIR/$dot missing and no $SRC_DIR/ao/$dot to link from."
    fi
  fi
done

# Bind mount is live — no rebuild needed. Just make sure container is up.
if docker compose ps --status running 2>/dev/null | grep -q php-apache; then
  info "Container already running."
else
  info "Starting container ..."
  docker compose up -d
  sleep 3
fi

info "Verifying over HTTP ($URL_BASE) ..."
code_root=$(curl -s -o /tmp/fix_perm_root.html -w '%{http_code}' "$URL_BASE/" || echo "000")
code_info=$(curl -s -o /dev/null -w '%{http_code}' "$URL_BASE/phpinfo.php" || echo "000")
code_qa=$(curl -s -o /tmp/fix_perm_qa.html -w '%{http_code}' "$URL_BASE/qa/" || echo "000")
code_css=$(curl -s -o /dev/null -w '%{http_code}' "$URL_BASE/.css/ao.css" || echo "000")
echo "GET /            -> HTTP $code_root"
echo "GET /phpinfo.php -> HTTP $code_info"
echo "GET /qa/         -> HTTP $code_qa"
echo "GET /.css/ao.css -> HTTP $code_css"

if grep -q "Permission denied\|Failed opening required" /tmp/fix_perm_root.html /tmp/fix_perm_qa.html 2>/dev/null; then
  echo "FAIL: page still shows 'Permission denied' / 'Failed opening required'."
  echo "See FIX_PERMISSIONS.md section 'Subpages still fail'."
  head -c 500 /tmp/fix_perm_qa.html; echo
  exit 1
fi

if grep -qi "<!DOCTYPE html" /tmp/fix_perm_root.html 2>/dev/null && [ "$code_root" = "200" ]; then
  echo "OK: index.php is served correctly. Open $URL_BASE/ in your browser."
else
  echo "WARNING: unexpected body for /. First bytes:"
  head -c 500 /tmp/fix_perm_root.html; echo
  exit 1
fi
