#!/usr/bin/env bash
# version 0.1.1
# Start OpenCode in the repo root after checking every pinned model id.
#
# Runs check_opencode_models.py first: a free id that left the OpenCode
# catalog is switched to the next live id from free-models.txt; when no
# listed id is live, a paid id is gone, or the models endpoint is down, it
# asks before starting. Starting from the repo root makes OpenCode load
# opencode.jsonc, AGENTS.md and the agent/skill folders that exist.
#
# Usage (any working directory):
#   container/tools/ai/opencode.sh [opencode args...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

command -v opencode >/dev/null 2>&1 || {
  echo "ERROR: opencode is not on PATH (install: https://opencode.ai/docs/)." >&2
  exit 127
}
python3 "$SCRIPT_DIR/check_opencode_models.py"
cd "$REPO_ROOT"
exec opencode "$@"
