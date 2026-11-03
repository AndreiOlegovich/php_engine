#!/usr/bin/env bash
# version 0.1.1
# Set up .venv with uv and run the pytest suite with the venv python.
#
# No system-python pollution: everything lives in <root>/.venv.
# Re-runnable: existing venv/deps are reused when up to date; a venv built
# for another Python minor is recreated (a stale pyvenv.cfg must never be
# reused while bin/python points at a newer interpreter).
#
# Usage (from container/):
#   ./tools/run-tests-venv.sh [--setup-only] [-- PYTEST_ARGS...]
#
#   (no args)      ensure venv + deps, then run the full suite.
#   --setup-only   only ensure venv + deps, do not run pytest.
#   -- ARGS...     forwarded to pytest (e.g. -- tests/ -q -k remarque).
#
# Host defaults (override via env): BASE_URL=http://127.0.0.1:8080
# (the local PHP server on :8080), PYTHON_VERSION=3.12.
#
# Exit: pytest's exit code (0 = pass).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
REQS="$ROOT/tests/requirements.txt"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

SETUP_ONLY=0
if [ "${1:-}" = "--setup-only" ]; then SETUP_ONLY=1; shift; fi
if [ "${1:-}" = "--" ]; then shift; fi

command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not found (see https://docs.astral.sh/uv/)." >&2; exit 2; }
[ -f "$REQS" ] || { echo "ERROR: requirements not found: $REQS" >&2; exit 2; }

venv_python_version="$(
  "$VENV/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true
)"
if [ ! -x "$VENV/bin/python" ] || [ "$venv_python_version" != "$PYTHON_VERSION" ]; then
  if [ -e "$VENV" ]; then
    echo "recreating stale $VENV (found ${venv_python_version:-no python}; want $PYTHON_VERSION) ..."
  else
    echo "creating $VENV for Python $PYTHON_VERSION ..."
  fi
  rm -rf "$VENV"
  uv venv --python "$PYTHON_VERSION" "$VENV"
fi

echo "installing test deps ..."
uv pip install --python "$VENV/bin/python" -r "$REQS"

if [ "$SETUP_ONLY" -eq 1 ]; then
  echo "venv ready: $VENV (python $("$VENV/bin/python" --version 2>&1))"
  exit 0
fi

export BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
echo "BASE_URL=$BASE_URL"

if [ "$#" -eq 0 ]; then
  set -- tests/ -q
fi
exec "$VENV/bin/python" -m pytest "$@"
