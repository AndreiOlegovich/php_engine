#!/usr/bin/env bash
# version 0.0.1
# Proof test for the finder coverage rule (run on hosting, needs only bash).
#
# Setup under test: a directory AND one of its subdirectories both change
# between scans (e.g. .css/site.css plus .css/react/mathdict.css).
# Correct behavior: the reported list must still cover the parent's own
# direct-file change (via the parent entry), otherwise an engine copying
# only reported subtrees never copies that file — yet the next --save
# marks it done. Files silently missed.
#
# Safe by design: scratch trees under mktemp, scratch state file, the real
# find-dirs-with-changes.sh next to this script. No repo writes.
# Exit 0: covered (rule holds). Exit 1: BUG reproduced. Exit 2: inconclusive.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FINDER="$SCRIPT_DIR/find-dirs-with-changes.sh"
[ -x "$FINDER" ] || { echo "ERROR: $FINDER not found." >&2; exit 2; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/src/.css/react"
printf 'v1 root' > "$WORK/src/.css/site.css"
printf 'v1 nested' > "$WORK/src/.css/react/mathdict.css"
"$FINDER" --max-depth 4 --state-file "$WORK/state" --save "$WORK/src" >/dev/null

printf 'v2 root CHANGED' > "$WORK/src/.css/site.css"
printf 'v2 nested CHANGED' > "$WORK/src/.css/react/mathdict.css"

finder_out=""
if ! finder_out="$("$FINDER" --max-depth 4 --state-file "$WORK/state" "$WORK/src")"; then
  echo "INCONCLUSIVE: finder errored during the test." >&2
  exit 2
fi
mapfile -t REPORTED <<< "$finder_out"
listed=""
for r in ${REPORTED[@]+"${REPORTED[@]}"}; do
  [ -n "$r" ] && listed="$listed$r"$'\n'
done

echo "Reported dirs:"
printf '%s' "$listed" | sed 's/^/  /'
if printf '%s' "$listed" | grep -qx '\.css'; then
  echo "PASS: .css is reported, so its direct-file change gets copied."
else
  echo "FAIL: .css is NOT reported although .css/site.css changed."
  echo "An engine copying only the reported subtrees would copy .css/react"
  echo "but never .css/site.css — and the next --save would mark it done."
  exit 1
fi
