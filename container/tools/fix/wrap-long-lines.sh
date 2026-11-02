#!/usr/bin/env bash
# version 0.0.2
# Find and safely wrap PHP lines longer than a limit (default: 120 chars).
#
# Usage:
#   ./wrap-long-lines.sh [--search] [--fix] [--validate] [--limit N]
#                        [--php-bin CMD] [-h|--help] [paths...]
#
# Modes (default: --search):
#   --search     list lines longer than LIMIT as file:line:length (+ preview).
#                Read-only, never modifies files. Exit 1 if any found, else 0.
#   --fix        wrap overlong lines at safe split points (see SAFETY below),
#                then verify every touched file with `php -l`. A file that
#                fails the syntax check is restored byte-for-byte and
#                reported as failed. Exit 1 if anything failed or was
#                skipped, else 0.
#   --validate   run phpcs (PSR-12 project ruleset) on the target files and
#                report findings. Combined as `--fix --validate`, it snapshots
#                findings before the fix and reports introduced-vs-fixed
#                counts afterwards (exit 1 if anything was introduced).
#   --limit N    max line length (default: 120).
#   --php-bin C  php binary used for the post-fix syntax check
#                (default: auto — `docker exec php-apache`, else host `php`).
#                If no PHP is available, --fix refuses to run (safety first).
#   paths...     files and/or directories (default: src/). Directories are
#                scanned for *.php without descending into symlinked dirs.
#
# SAFETY (how --fix avoids introducing bugs):
#   - Byte-safe: CRLF/LF endings, trailing newlines and encodings preserved.
#   - Only lines fully inside <?php ... ?> blocks are touched; HTML and
#     template text is left alone (reported as ignored, never penalized).
#   - A line is only split OUTSIDE single/double-quoted strings, heredocs
#     and comments (PHP-aware scanner, not regex).
#   - Split points, in order: top-level commas (one argument per line,
#     PSR-12 style), `.` concatenation operators (operator stays at the
#     start of the continuation line), `=` / `=>` assignments.
#   - Lines with no safe split point (long string literals, URLs) are left
#     untouched and reported as skipped.
#   - Every modified file must pass `php -l`; otherwise it is restored
#     from the in-memory original and reported as failed.
#
# Examples:
#   ./wrap-long-lines.sh --search src/
#   ./wrap-long-lines.sh --fix --limit 120 src/index.php
#   ./wrap-long-lines.sh --fix --validate src/
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../..  # tools live in tools/fix/; project root (compose file, src/) is the runtime cwd

MODE_SEARCH=0; MODE_FIX=0; MODE_VALIDATE=0
LIMIT=120
PHP_BIN=""
SHOW_HELP=0
POSITIONAL=()

usage() {
  local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac
  sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --search) MODE_SEARCH=1; shift ;;
    --fix) MODE_FIX=1; shift ;;
    --validate) MODE_VALIDATE=1; shift ;;
    --limit) [ $# -ge 2 ] || { echo "ERROR: --limit needs a value." >&2; exit 2; }
      LIMIT="$2"; shift 2 ;;
    --limit=*) LIMIT="${1#--limit=}"; shift ;;
    --php-bin) [ $# -ge 2 ] || { echo "ERROR: --php-bin needs a value." >&2; exit 2; }
      PHP_BIN="$2"; shift 2 ;;
    --php-bin=*) PHP_BIN="${1#--php-bin=}"; shift ;;
    -h|--help) SHOW_HELP=1; shift ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

if [ "$SHOW_HELP" -eq 1 ]; then usage; exit 0; fi
if [ "$MODE_SEARCH" -eq 0 ] && [ "$MODE_FIX" -eq 0 ] && [ "$MODE_VALIDATE" -eq 0 ]; then
  MODE_SEARCH=1  # default mode
fi
case "$LIMIT" in ''|*[!0-9]*) echo "ERROR: --limit must be a positive integer." >&2; exit 2 ;; esac
[ "$LIMIT" -gt 0 ] || { echo "ERROR: --limit must be a positive integer." >&2; exit 2; }

# Resolve target files (default: src/).
TARGETS=()
if [ "${#POSITIONAL[@]}" -eq 0 ]; then POSITIONAL=("src"); fi
for p in "${POSITIONAL[@]}"; do
  if [ -f "$p" ]; then
    TARGETS+=("$p")
  elif [ -d "$p" ]; then
    while IFS= read -r f; do TARGETS+=("$f"); done \
      < <(find -P "$p" -type f -name "*.php" | LC_ALL=C sort)
  else
    echo "ERROR: path not found: $p" >&2; exit 2
  fi
done
[ "${#TARGETS[@]}" -gt 0 ] || { echo "No .php files found." >&2; exit 2; }

export WRAP_LIMIT="$LIMIT"
export WRAP_RUN_DIR="$PWD"
HELPER="$SCRIPT_DIR/.wrap-long-lines.py"
if [ ! -f "$HELPER" ]; then
  echo "ERROR: helper missing: $HELPER" >&2
  exit 2
fi
TMPBASE="${TMPDIR:-/tmp}/wrap-long-lines-$$"
export WRAP_TMPDIR="$TMPBASE"
mkdir -p "$WRAP_TMPDIR"
trap 'rm -rf "$WRAP_TMPDIR"' EXIT

# ---- --search: report only ----

# ---- --search: report only ----
if [ "$MODE_SEARCH" -eq 1 ] && [ "$MODE_FIX" -eq 0 ] && [ "$MODE_VALIDATE" -eq 0 ]; then
  FOUND=0
  for f in "${TARGETS[@]}"; do
    while IFS= read -r line; do printf '%s\n' "$line"; FOUND=1; done \
      < <(WRAP_MODE=search python3 "$HELPER" "$f")
  done
  if [ "$FOUND" -eq 1 ]; then exit 1; fi
  echo "OK: no lines over $LIMIT chars in ${#TARGETS[@]} file(s)."
  exit 0
fi

# ---- --fix needs PHP ----
PHPCS_IMG="php:8.2-cli"
TOOL_CACHE="${PHP_ENGINE_STATIC_TOOL_CACHE:-$HOME/.cache/php_engine-tools}"
PHP_CMD=""
if [ "$MODE_FIX" -eq 1 ]; then
  if [ -n "$PHP_BIN" ]; then
    PHP_CMD="$PHP_BIN"
  elif docker exec php-apache php -v >/dev/null 2>&1; then
    # Wrapper: files under ./src lint via the running container (fast);
    # anything else via a one-off php:8.2-cli container (bind-mounted).
    PHP_CMD="$WRAP_TMPDIR/php-lint.sh"
    cat > "$PHP_CMD" <<EOF
#!/usr/bin/env bash
# args: [-l] <file>; maps container/src paths into php-apache, rest via mount.
op="\$1"; f="\$2"
case "\$PWD/\$f" in
  "$PWD/src/"*)
    rel=\${f#src/}; rel=\${rel#./src/}
    exec docker exec php-apache php "\$op" "/var/www/html/\$rel" ;;
  *)
    dir=\$(cd "\$(dirname "\$f")" && pwd); base=\$(basename "\$f")
    exec docker run --rm -v "\$dir:/w:ro" $PHPCS_IMG php "\$op" "/w/\$base" ;;
esac
EOF
    chmod +x "$PHP_CMD"
  elif command -v php >/dev/null 2>&1; then
    PHP_CMD="php"
  else
    echo "ERROR: no PHP available for the post-fix syntax check" >&2
    echo "  (tried: --php-bin, 'docker exec php-apache php', host 'php')." >&2
    echo "  Refusing to modify files without verification." >&2
    exit 2
  fi
  export WRAP_PHP_CMD="$PHP_CMD"
fi

# ---- --validate needs phpcs ----
if { [ "$MODE_VALIDATE" -eq 1 ]; }; then
  if [ ! -f "$TOOL_CACHE/phpcs.phar" ] || ! docker image inspect "$PHPCS_IMG" >/dev/null 2>&1; then
    echo "ERROR: --validate needs phpcs.phar in $TOOL_CACHE and image $PHPCS_IMG." >&2
    echo "  Run once: ./tools/linters/lint-static.sh --tool phpcs --files <a file> (downloads the PHAR)." >&2
    exit 2
  fi
fi

# snapshot phpcs findings (JSON) for "${TARGETS[@]}" into $1.
# TARGETS may live anywhere: resolve to absolute paths and serve them to
# the container from a read-only host-root mount.
snapshot_phpcs() {
  local out="$1" outdir
  outdir="$(dirname "$out")"
  : > "$outdir/targets.txt"
  local f abs
  for f in "${TARGETS[@]}"; do
    case "$f" in
      /*) abs="$f" ;;
      *) abs="$WRAP_RUN_DIR/$f" ;;
    esac
    printf '/host%s\n' "$abs" >> "$outdir/targets.txt"
  done
  docker run --rm -v "$TOOL_CACHE:/tools:ro" \
    -v /:/host:ro -v "$outdir:/out" \
    -v "$WRAP_RUN_DIR/phpcs.xml:/app/phpcs.xml:ro" \
    "$PHPCS_IMG" sh -c \
    'php -d memory_limit=1G /tools/phpcs.phar -q --standard=/app/phpcs.xml --report=json $(cat /out/targets.txt) > /out/snap.json; true'
  if [ ! -s "$outdir/snap.json" ]; then
    echo "ERROR: phpcs produced no report (see docker output above)." >&2
    return 1
  fi
  # rewrite /host prefixes back to repo-relative paths for readable output
  python3 - "$outdir/snap.json" "$out" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
files = {}
for path, data in d.get("files", {}).items():
    if path.startswith("/host"):
        beg = path[len("/host"):]
        try:
            rel = beg.split("container/", 1)[1]
            path = f"container/{rel}"
        except IndexError:
            path = beg
    files[path] = data
d["files"] = files
json.dump(d, open(sys.argv[2], "w"))
EOF
  rm -f "$outdir/targets.txt" "$outdir/snap.json"
}

if [ "$MODE_FIX" -eq 1 ] && [ "$MODE_VALIDATE" -eq 1 ]; then
  snapshot_phpcs "$WRAP_RUN_DIR/.wrap-before.json"
fi

# ---- --fix ----
if [ "$MODE_FIX" -eq 1 ]; then
  FAILED=0; FIXED=0; SKIPPED=0
  for f in "${TARGETS[@]}"; do
    result="$(WRAP_MODE=fix python3 "$HELPER" "$f")"
    echo "$result"
    case "$result" in
      FIXED*) FIXED=$((FIXED+1)) ;;
      UNCHANGED*) ;;
      SKIPPED*|FAILED*) SKIPPED=$((SKIPPED+1)); FAILED=$((FAILED+1)) ;;
      *) echo "ERROR: unexpected helper output for $f: $result" >&2; SKIPPED=$((SKIPPED+1)); FAILED=$((FAILED+1)) ;;
    esac
  done
  echo "fix: $FIXED file(s) wrapped, $SKIPPED file(s) need attention."
  if [ "$MODE_VALIDATE" -eq 0 ]; then
    [ "$FAILED" -eq 0 ]
    exit $?
  fi
fi

# ---- --validate ----
if [ "$MODE_VALIDATE" -eq 1 ]; then
  if [ "$MODE_FIX" -eq 0 ]; then
    # standalone: just report current findings
    snapshot_phpcs "$WRAP_RUN_DIR/.wrap-after.json"
    python3 - "$WRAP_RUN_DIR/.wrap-after.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
totals = d.get("totals", {})
print(f"phpcs: {totals.get('errors',0)} errors, {totals.get('warnings',0)} warnings "
      f"in {len(d.get('files',{}))} file(s).")
sys.exit(0 if (totals.get("errors",0)+totals.get("warnings",0)) == 0 else 1)
EOF
    rc=$?
    rm -f "$WRAP_RUN_DIR/.wrap-targets-$$.txt" "$WRAP_RUN_DIR/.wrap-after.json"
    exit "$rc"
  fi
  # combined --fix --validate: delta before/after
  snapshot_phpcs "$WRAP_RUN_DIR/.wrap-after.json"
  python3 - "$WRAP_RUN_DIR/.wrap-before.json" "$WRAP_RUN_DIR/.wrap-after.json" <<'EOF'
import json, re, sys
def sig(m):
    return (m.get("source"), re.sub(r"\d+", "#", m.get("message", "")))
def load(p):
    d = json.load(open(p))
    out = {}
    for path, data in d.get("files", {}).items():
        out[path] = {sig(m) for m in data.get("messages", [])}
    return out
before, after = load(sys.argv[1]), load(sys.argv[2])
introduced = fixed = 0
for path, msgs in after.items():
    new = msgs - before.get(path, set())
    gone = before.get(path, set()) - msgs
    introduced += len(new)
    fixed += len(gone)
    for s in sorted(new):
        print(f"INTRODUCED {path}: {s[0]}: {s[1][:80]}")
print(f"validate: introduced={introduced} fixed={fixed}")
sys.exit(0 if introduced == 0 else 1)
EOF
  rc=$?
  rm -f "$WRAP_RUN_DIR/.wrap-targets-$$.txt" "$WRAP_RUN_DIR/.wrap-before.json" "$WRAP_RUN_DIR/.wrap-after.json"
  if [ "$FAILED" -ne 0 ]; then
    echo "fix phase had $FAILED file(s) needing attention." >&2
    exit 1
  fi
  exit "$rc"
fi
