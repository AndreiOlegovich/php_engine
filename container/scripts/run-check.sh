#!/usr/bin/env bash
# Unified runner for the pytest_web sitemap link checker.
#
# Usage:
#   ./run-check.sh [--local|--web] [--url URL] [--dir PREFIX]
#                  [--sitemap FILE] [--docker|--host]
#                  [--report-only|--fail] [--dry-run] [-h|--help]
#                  [-- PYTEST_ARGS...]
#
# Modes (default: --local):
#   --local            check the local container site.
#                      docker backend: BASE_URL=http://php-apache
#                      host backend:   BASE_URL=http://127.0.0.1:8080
#   --web              check a live site.
#                      --url default: https://aredel.com
#   --url URL          only meaningful with --web.
#   --dir PREFIX       only check sitemap URLs whose path equals PREFIX or
#                      starts with PREFIX + "/". E.g. --dir ru/qa matches
#                      /ru/qa, /ru/qa/, /ru/qa/page.php but not /ru/qax.
#                      Leading/trailing slashes are optional.
#   --sitemap FILE     sitemap basename under src/ (default: sitemap.xml).
#                      E.g. --sitemap sitemap_new.xml
#   --docker (default) run via `docker compose run --rm pytest_web`
#                      (Allure results go to the shared volume).
#   --host             run pytest directly on the host
#                      (needs: pip install -r pytest_web/requirements.txt).
#                      Allure results go to reports/allure-results-host/.
#   --report-only      FAIL_ON_NON_200=false (exit 0, report only).
#   --fail (default)   FAIL_ON_NON_200=true (non-200 URLs fail).
#   --dry-run          print the resolved command without executing it.
#
# Examples:
#   ./run-check.sh --local
#   ./run-check.sh --local --dir ru/qa --host
#   ./run-check.sh --web
#   ./run-check.sh --web --url https://www.aredel.com --dir ru/qa
#   ./run-check.sh --web --sitemap sitemap_new.xml --report-only
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # scripts live in scripts/; project root (compose file, src/) is the runtime cwd

MODE="local"
URL="https://aredel.com"
URL_GIVEN=0
DIR_RAW=""
SITEMAP_ARG="sitemap.xml"
BACKEND="docker"
FAIL_MODE="true"
DRY_RUN=0
PYTEST_ARGS=()

usage() {
  local self="$0"; case "$self" in /*) ;; *) self="$_START_DIR/$self";; esac
  sed -n '2,/^set -euo/p' "$self" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
}

normalize_dir() {
  local d="$1"
  # trim whitespace
  d="$(printf '%s' "$d" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [ -z "$d" ] && { printf ''; return; }
  # strip leading/trailing slashes, then re-add single leading slash
  d="$(printf '%s' "$d" | sed -e 's#^/*##' -e 's#/*$##')"
  [ -z "$d" ] && { printf '/'; return; }
  printf '/%s' "$d"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --local) MODE="local"; shift ;;
    --web) MODE="web"; shift ;;
    --url) [ $# -ge 2 ] || { echo "ERROR: --url needs a value." >&2; exit 2; }
      URL="$2"; URL_GIVEN=1; shift 2 ;;
    --url=*) URL="${1#--url=}"; URL_GIVEN=1; shift ;;
    --dir) [ $# -ge 2 ] || { echo "ERROR: --dir needs a value." >&2; exit 2; }
      DIR_RAW="$2"; shift 2 ;;
    --dir=*) DIR_RAW="${1#--dir=}"; shift ;;
    --sitemap) [ $# -ge 2 ] || { echo "ERROR: --sitemap needs a value." >&2; exit 2; }
      SITEMAP_ARG="$2"; shift 2 ;;
    --sitemap=*) SITEMAP_ARG="${1#--sitemap=}"; shift ;;
    --docker) BACKEND="docker"; shift ;;
    --host) BACKEND="host"; shift ;;
    --report-only) FAIL_MODE="false"; shift ;;
    --fail) FAIL_MODE="true"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do PYTEST_ARGS+=("$1"); shift; done; break ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) echo "ERROR: unexpected argument: $1 (see --help)." >&2; exit 2 ;;
  esac
done

if [ "$MODE" = "local" ] && [ "$URL_GIVEN" -eq 1 ]; then
  echo "ERROR: --url only applies to --web (local uses the container site)." >&2
  exit 2
fi

# Normalize URL (no trailing slash) and prefix.
URL="$(printf '%s' "$URL" | sed -e 's#/*$##')"
[ -n "$URL" ] || { echo "ERROR: empty --url." >&2; exit 2; }
PATH_PREFIX="$(normalize_dir "$DIR_RAW")"

# Resolve sitemap: accept basename or path, verify it exists under src/.
SITEMAP_BASE="$(basename "$SITEMAP_ARG")"
if [ ! -f "src/$SITEMAP_BASE" ]; then
  echo "ERROR: sitemap 'src/$SITEMAP_BASE' not found." >&2
  ls src/*.xml 2>/dev/null || true
  exit 1
fi

if [ "$MODE" = "local" ]; then
  if [ "$BACKEND" = "docker" ]; then
    BASE_URL="http://php-apache"
  else
    BASE_URL="http://127.0.0.1:8080"
  fi
  SITEMAP_LABEL="local ($BASE_URL <- src/$SITEMAP_BASE)"
else
  BASE_URL="$URL"
  SITEMAP_LABEL="web ($BASE_URL <- src/$SITEMAP_BASE)"
fi

echo "mode:        $MODE [$SITEMAP_LABEL]"
echo "backend:     $BACKEND"
echo "base_url:    $BASE_URL"
echo "sitemap:     src/$SITEMAP_BASE"
echo "path_prefix: ${PATH_PREFIX:- (all)}"
echo "fail_mode:   FAIL_ON_NON_200=$FAIL_MODE"

if [ "$BACKEND" = "docker" ]; then
  SITEMAP_FILE="/data/$SITEMAP_BASE"
  # shellcheck disable=SC2155
  CMD=(docker compose run --rm
    -e "BASE_URL=$BASE_URL"
    -e "SITEMAP_FILE=$SITEMAP_FILE"
    -e "PATH_PREFIX=$PATH_PREFIX"
    -e "FAIL_ON_NON_200=$FAIL_MODE"
    pytest_web)
  if [ "${#PYTEST_ARGS[@]}" -gt 0 ]; then
    # Override the service command to append extra pytest args.
    CMD+=(pytest -q --alluredir=/allure-results --clean-alluredir "${PYTEST_ARGS[@]}")
  fi
else
  SITEMAP_FILE="src/$SITEMAP_BASE"
  ALLURE_DIR="reports/allure-results-host"
  mkdir -p "$ALLURE_DIR"
  CMD=(env
    "BASE_URL=$BASE_URL"
    "SITEMAP_FILE=$SITEMAP_FILE"
    "PATH_PREFIX=$PATH_PREFIX"
    "FAIL_ON_NON_200=$FAIL_MODE"
    python3 -m pytest -q "pytest_web" "--alluredir=$ALLURE_DIR" "--clean-alluredir")
  if [ "${#PYTEST_ARGS[@]}" -gt 0 ]; then
    CMD+=("${PYTEST_ARGS[@]}")
  fi
  if ! python3 -c "import pytest, requests" 2>/dev/null; then
    echo "WARNING: host python is missing pytest/requests." >&2
    echo "         Run: pip install -r pytest_web/requirements.txt" >&2
  fi
fi

echo "+ ${CMD[*]}"
if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

exec "${CMD[@]}"
