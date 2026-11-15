#!/usr/bin/env bash
# version 0.2.0
# Find directories with changes inside (companion to the copy scripts).
#
# Scans SRC and compares every directory against a snapshot file.
# A directory counts as changed when anything inside it changed:
# its own mtime (entries added/removed/renamed), the subtree size,
# or the newest file mtime beneath it (catches same-size edits).
# Unchanged subtrees are ignored (never descended for reporting).
#
# Usage (run ON the server, where these paths exist):
#   ./find-dirs-with-changes.sh [--max-depth N] [--state-file FILE]
#       [--save] [--snapshot-out FILE] [src_dir]
#
#   --max-depth N   report changed dirs at exactly this depth
#                   (default: 1; e.g. 1 -> ru ubuntu dev,
#                   2 -> section ru/code ubuntu/network section).
#                   Dirs below the depth still feed detection upward;
#                   only the reported level is capped.
#   --state-file    snapshot file (default: .find_dirs.state next to
#                   this script; SMART_STATE_FILE env wins over default).
#   --save          persist the new snapshot (default: scan only, the
#                   file is left untouched so a later copy step can
#                   decide when the scan becomes the baseline).
#   --snapshot-out FILE  write the snapshot of THIS scan to FILE, leaving
#                   the state file untouched. The copy scripts promote it
#                   to the state file after a successful copy, so files
#                   that change on dev during the copy are not recorded as
#                   seen without having been copied.
#   src_dir         tree to scan (default: $DEV_DOCROOT or
#                   /home/user/dev/public_html).
#
# Output: one relative path per line on stdout (selection rule below);
# diagnostics go to stderr. A listed dir means "copy this whole
# subtree". Selection rule: every changed dir at depth 1..MAXDEPTH is
# reported; overlap is harmless (the engine skips identical files), while
# suppressing an ancestor for a changed descendant would silently drop the
# ancestor's own direct-file changes. "." is reported only for root-local
# changes (see @@ROOTFILES below): pure aggregate bubbling from below is
# already covered by the reported descendants.
#
# Paths default from site.env (SITE_ENV) when set; built-ins below
# otherwise. See site.env.example.
set -euo pipefail
export LC_ALL=C  # deterministic sort order and stat number formats

if [ -n "${SITE_ENV:-}" ]; then
  [ -f "$SITE_ENV" ] || { echo "ERROR: SITE_ENV file '$SITE_ENV' not found." >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAXDEPTH=1
STATE_FILE="${SMART_STATE_FILE:-$SCRIPT_DIR/.find_dirs.state}"
SAVE=0
SNAPSHOT_OUT=""
POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --max-depth=*) MAXDEPTH="${1#--max-depth=}"; shift ;;
    --max-depth) [ $# -ge 2 ] || { echo "ERROR: --max-depth needs a value." >&2; exit 1; }; MAXDEPTH="$2"; shift 2 ;;
    --state-file=*) STATE_FILE="${1#--state-file=}"; shift ;;
    --state-file) [ $# -ge 2 ] || { echo "ERROR: --state-file needs a value." >&2; exit 1; }; STATE_FILE="$2"; shift 2 ;;
    --save) SAVE=1; shift ;;
    --snapshot-out=*) SNAPSHOT_OUT="${1#--snapshot-out=}"; shift ;;
    --snapshot-out) [ $# -ge 2 ] || { echo "ERROR: --snapshot-out needs a value." >&2; exit 1; }; SNAPSHOT_OUT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
      exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 1 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
case "$MAXDEPTH" in
  ''|*[!0-9]*) echo "ERROR: --max-depth must be a non-negative integer." >&2; exit 1 ;;
esac
[ "${#POSITIONAL[@]}" -le 1 ] || { echo "ERROR: at most one src_dir." >&2; exit 1; }

SRC="${POSITIONAL[0]:-${DEV_DOCROOT:-/home/user/dev/public_html}}"
[ -d "$SRC" ] || { echo "ERROR: source '$SRC' is not a directory." >&2; exit 1; }
SRC="$(cd "$SRC" && pwd)"

depth_of() {
  # depth of a snapshot rel path ("." is 0)
  local rel="$1"
  [ "$rel" = "." ] && { echo 0; return; }
  awk -F/ '{print NF}' <<< "$rel"
}

# --- current snapshot: rel -> "dmtime|size|filemax" ---
declare -A CUR_DMTIME=() CUR_SIZE=() CUR_MAX=()
while IFS= read -r -d '' d; do
  rel="${d#"$SRC"/}"
  [ "$rel" = "$d" ] && rel="."
  CUR_DMTIME["$rel"]=$(stat -c %.9Y "$d")
  CUR_SIZE["$rel"]=0
  CUR_MAX["$rel"]=0
done < <(find "$SRC" -mindepth 0 -type d -print0)

while IFS= read -r -d '' f; do
  rel="${f#"$SRC"/}"
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  mt=$(stat -c %.9Y "$f" 2>/dev/null || echo 0)
  d="$rel"
  while true; do
    d=$(dirname "$d")
    [ -n "${CUR_SIZE[$d]:-}" ] || break
    CUR_SIZE["$d"]=$(( ${CUR_SIZE[$d]} + sz ))
    [[ "$mt" > "${CUR_MAX[$d]}" ]] && CUR_MAX["$d"]=$mt
    [ "$d" = "." ] && break
  done
done < <(find "$SRC" -mindepth 1 \( -type f -o -type l \) -print0)

# --- root-local aggregate: direct files of SRC only (count|size|max) ---
# Decides whether "." itself must be reported: aggregate bubbling from
# below is covered by reported descendants, but root-level file changes
# (or root entry add/remove, via root dmtime) have no other carrier.
ROOT_COUNT=0
ROOT_SIZE=0
ROOT_MAX=0
while IFS= read -r -d '' f; do
  ROOT_COUNT=$((ROOT_COUNT + 1))
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  mt=$(stat -c %.9Y "$f" 2>/dev/null || echo 0)
  ROOT_SIZE=$((ROOT_SIZE + sz))
  # String compare is intentional: %Y timestamps are fixed-width
  # "seconds.nanoseconds", so lexicographic order is chronological
  # while -gt would choke on the dot.
  # shellcheck disable=SC2071
  [[ "$mt" > "$ROOT_MAX" ]] && ROOT_MAX="$mt"
done < <(find "$SRC" -mindepth 1 -maxdepth 1 -type f -print0)
ROOT_CUR="${CUR_DMTIME[.]}|$ROOT_COUNT|$ROOT_SIZE|$ROOT_MAX"

# --- previous snapshot ---
declare -A OLD=()
ROOT_OLD=""
if [ -f "$STATE_FILE" ]; then
  while IFS= read -r line; do
    case "$line" in
      "@@ROOTFILES|"*) ROOT_OLD="${line#"@@ROOTFILES|"}" ;;
      *)
        IFS='|' read -r rel dm sz mx <<< "$line"
        [ -n "$rel" ] && OLD["$rel"]="$dm|$sz|$mx"
        ;;
    esac
  done < "$STATE_FILE"
  echo "snapshot: $STATE_FILE (${#OLD[@]} dirs)." >&2
else
  echo "no snapshot yet ($STATE_FILE): everything counts as new." >&2
fi

# --- changed dirs ---
CHANGED=()
for rel in "${!CUR_DMTIME[@]}"; do
  cur="${CUR_DMTIME[$rel]}|${CUR_SIZE[$rel]}|${CUR_MAX[$rel]}"
  [ "${OLD[$rel]:-}" != "$cur" ] && CHANGED+=("$rel")
done
for rel in "${!OLD[@]}"; do
  if [ -z "${CUR_DMTIME[$rel]:-}" ]; then
    echo "[gone] $rel (in snapshot, missing from source; not reported)." >&2
  fi
done

# --- select: every changed dir at depth 1..MAXDEPTH, plus "." only for
# --- root-local changes. Overlap is harmless (the engine skips identical
# --- files); suppressing an ancestor for a changed descendant is what used
# --- to silently drop the ancestor's own direct-file changes.
mapfile -t SORTED < <(printf '%s\n' "${CHANGED[@]}" | sort)
for rel in "${SORTED[@]}"; do
  [ -n "$rel" ] || continue
  [ "$rel" = "." ] && continue
  d=$(depth_of "$rel")
  [ "$d" -gt "$MAXDEPTH" ] && continue
  echo "$rel"
done
if [ "$ROOT_CUR" != "$ROOT_OLD" ]; then
  echo "."
fi

write_snapshot() {
  {
    for rel in "${!CUR_DMTIME[@]}"; do
      printf '%s|%s|%s|%s\n' "$rel" "${CUR_DMTIME[$rel]}" "${CUR_SIZE[$rel]}" "${CUR_MAX[$rel]}"
    done | sort
    printf '@@ROOTFILES|%s\n' "$ROOT_CUR"
  } > "$1"
}

if [ -n "$SNAPSHOT_OUT" ]; then
  write_snapshot "$SNAPSHOT_OUT"
  echo "snapshot of this scan written: $SNAPSHOT_OUT." >&2
fi
if [ "$SAVE" -eq 1 ]; then
  write_snapshot "$STATE_FILE"
  echo "snapshot saved: $STATE_FILE." >&2
fi
