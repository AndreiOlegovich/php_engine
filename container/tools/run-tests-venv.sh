#!/usr/bin/env bash
# version 0.1.4
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
# Host defaults (override via env): BASE_URL=http://127.0.0.1:<SITE_HTTP_PORT>
# (the local PHP server of this project), PYTHON_VERSION=3.12.
#
# Exit: pytest's exit code (0 = pass).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

SETUP_ONLY=0
if [ "${1:-}" = "--setup-only" ]; then SETUP_ONLY=1; shift; fi
if [ "${1:-}" = "--" ]; then shift; fi

command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not found (see https://docs.astral.sh/uv/)." >&2; exit 2; }

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

# The lockfile holds the test deps and the dev tools (ruff, mypy, pylint) that
# the lint self-tests call.
echo "installing locked deps ..."
UV_PROJECT_ENVIRONMENT="$VENV" uv sync --project "$ROOT" --locked --group dev --python "$PYTHON_VERSION"

if [ "$SETUP_ONLY" -eq 1 ]; then
  echo "venv ready: $VENV (python $("$VENV/bin/python" --version 2>&1))"
  exit 0
fi

# shellcheck disable=SC1091  # shared resolver is checked separately
export BASE_URL="${BASE_URL:-$(. "$SCRIPT_DIR/project-env.sh"; printf '%s' "$SITE_LOCAL_BASE")}"
export SITEMAP_FILE="${SITEMAP_FILE:-src/sitemap.xml}"
echo "BASE_URL=$BASE_URL SITEMAP_FILE=$SITEMAP_FILE"

if [ "$#" -eq 0 ]; then
  set -- tests/ -q
fi
exec "$VENV/bin/python" -m pytest "$@"
