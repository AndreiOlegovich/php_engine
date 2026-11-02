#!/usr/bin/env bash
# version 0.1.0
# Static gate against the HTML/PHP reflower damage (incident 2026-09-28).
#
# Runs the checks of tests/test_html_code_corruption.py over the whole
# container/src/ tree (pure text scan, ~1s): broken HTML attributes
# ("<span style=$" + value on the next line), dot-broken code lines
# ("random" / ". choice(symbols)") and "&gt;" inside HTML tags.
#
# Usage:
#   ./check-reflow.sh            scan the whole src/ tree
#   ./check-reflow.sh --staged   same (the scan is whole-tree regardless;
#                                accepted for pre-commit hook symmetry)
#
# Exit: 0 = clean, 1 = damage found (details on stderr).
# The scan needs only python3 (no pytest, no venv).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF="$SCRIPT_DIR/$(basename "$0")"
cd "$SCRIPT_DIR"
cd ../..  # tools/linters/ -> container/

usage() { sed -n '2,/^set -euo/p' "$SELF" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'; }

for arg in "$@"; do
  case "$arg" in
    --staged) ;;  # whole-tree scan anyway
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $arg (see --help)." >&2; exit 2 ;;
  esac
done

exec python3 tests/test_html_code_corruption.py
