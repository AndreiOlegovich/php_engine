#!/usr/bin/env bash
# version 0.3.3
# Unified runner for the pytest_web sitemap link checker.
#
# Usage:
#   ./run_sitemap_tests.sh [--local|--web] [--url URL] [--dir PREFIX]
#                  [--paths-file FILE]
#                  [--sitemap FILE] [--docker|--host] [--test T]
#                  [--report-only|--fail] [--verbose] [--tag TAG]
#                  [--dry-run] [-h|--help]
#                  [-- PYTEST_ARGS...]
#
# Tests (--test: single name, comma list, or "all"; default: all;
# each test keeps its own Allure trend):
#   link             test_sitemap.py (HTTP 200 per URL)
#                      trend: port SITE_ALLURE_PORT (allure-link)
#   content          test_content_loaded.py (200 + id="footer-end" per URL)
#                      trend: port SITE_ALLURE_PORT + 1 (allure-content)
#   css              test_css_coverage.py (asides have CSS rules)
#                      trend: port SITE_ALLURE_PORT + 2 (allure-css)
#   html-id          test_page_html_id.py (200 + Page/BasePage/ArticlePage marker per URL)
#                      trend: port SITE_ALLURE_PORT + 3 (allure-html-id)
#   regression       test_error_regression.py (error-log pages render without PHP fatal)
#                      trend: port SITE_ALLURE_PORT + 5 (allure-regression)
#   footer           test_footer_wrapped.py (contacts footer table in div.wrapper)
#                      trend: port SITE_ALLURE_PORT + 4 (allure-footer)
#   wrapped          test_page_wrapped_once.py (no wrapper inside another wrapper)
#                      trend: port SITE_ALLURE_PORT + 6 (allure-wrapped)
#   internal-links   test_internal_links.py (every <a href="/..."> on a page answers 200;
#                      a broken link is listed with every page it is on)
#                      no Allure viewer yet (results stay in the run container)
#   all             run link, then content (sequential)
#   link,content     same as all, explicit form (any comma combo works)
#
# With more than one test selected the runner prints a short summary
# for all executed tests at the end of the console output (per-test
# PASS/FAIL plus pytest's own counts and duration).
#
# Modes (default: --local):
#   --local            check the local container site.
#                      docker backend: BASE_URL=http://php-apache
#                      host backend:   BASE_URL=http://127.0.0.1:<SITE_HTTP_PORT>
#   --web              check a live site.
#                      --url default: https://example.com
#   --url URL          only meaningful with --web.
#   --dir PREFIX       only check sitemap URLs whose path equals PREFIX or
#                      starts with PREFIX + "/". E.g. --dir section matches
#                      /section, /section/, /section/page.php but not /sectionx.
#                      Leading/trailing slashes are optional.
#   --paths-file FILE  only check URLs whose path is listed in FILE, one
#                      URL path per line, exact match (e.g. /about/ or
#                      /section/page.php; lines not starting with / are
#                      ignored). The backoffice Tests tab writes it for its
#                      Directories / Latest commits page scope. Combines with
#                      --dir (both must match); a test left with no listed
#                      URLs is skipped, not failed.
#   --exclude LIST     comma-separated substrings; skip sitemap URLs whose
#                      path contains any of them. For include-fragments that
#                      are listed in the sitemap but are not pages, e.g.
#                      --exclude _menu.php,_top_path.php,left_block,right_block
#                      (content, css, engine, wrapped and internal-links
#                      tests; the link checker ignores it).
#   --sitemap FILE     sitemap basename under src/ (default: sitemap.xml).
#                      E.g. --sitemap sitemap.xml
#   --docker (default) run via `docker compose run --rm pytest_web`
#                      (Allure results go to the shared volume).
#   --host             run pytest directly on the host
#                      (needs: pip install -r tests/requirements.txt).
#                      Allure results go to reports/allure-results-host/.
#   --report-only      FAIL_ON_NON_200=false (exit 0, report only).
#   --fail (default)   FAIL_ON_NON_200=true (non-200 URLs fail).
#   --verbose          print each URL (pytest -v). Default prints only
#                      `tested N/TOTAL urls` every 100 urls.
#   --tag TAG          check a different body marker instead of footer-end:
#                      `address` = top menu (<nav class="crumbbar"> of the
#                      engines or <nav class="address"> of the includes),
#                      `crumbbar` = engine top menu only, anything else is
#                      matched literally. Content test only.
#   --dry-run          print the resolved command without executing it.
#
# Examples:
#   ./run_sitemap_tests.sh --local
#   ./run_sitemap_tests.sh --local --test content --dir section --host
#   ./run_sitemap_tests.sh --local --dir section --host
#   ./run_sitemap_tests.sh --local --test link,content --paths-file changed.txt
#   ./run_sitemap_tests.sh --web
#   ./run_sitemap_tests.sh --web --url https://www.example.com --dir section
#   ./run_sitemap_tests.sh --web --report-only
set -euo pipefail

_START_DIR="$(pwd -P)"  # invocation cwd (scripts cd elsewhere at startup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ..  # runner lives in tests/; project root (compose file, src/) is the runtime cwd
# shellcheck disable=SC1091  # shared resolver is checked separately
. tools/project-env.sh

MODE="local"
URL="https://example.com"
URL_GIVEN=0
DIR_RAW=""
PATHS_FILE_RAW=""
EXCLUDE_RAW=""
TEST="all"
SITEMAP_ARG="sitemap.xml"
BACKEND="docker"
FAIL_MODE="true"
VERBOSE=0
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
    --paths-file) [ $# -ge 2 ] || { echo "ERROR: --paths-file needs a value." >&2; exit 2; }
      PATHS_FILE_RAW="$2"; shift 2 ;;
    --paths-file=*) PATHS_FILE_RAW="${1#--paths-file=}"; shift ;;
    --exclude) [ $# -ge 2 ] || { echo "ERROR: --exclude needs a value." >&2; exit 2; }
      EXCLUDE_RAW="$2"; shift 2 ;;
    --exclude=*) EXCLUDE_RAW="${1#--exclude=}"; shift ;;
    --sitemap) [ $# -ge 2 ] || { echo "ERROR: --sitemap needs a value." >&2; exit 2; }
      SITEMAP_ARG="$2"; shift 2 ;;
    --sitemap=*) SITEMAP_ARG="${1#--sitemap=}"; shift ;;
    --docker) BACKEND="docker"; shift ;;
    --host) BACKEND="host"; shift ;;
    --test) [ $# -ge 2 ] || { echo "ERROR: --test needs a value." >&2; exit 2; }
      TEST="$2"; shift 2 ;;
    --test=*) TEST="${1#--test=}"; shift ;;
    --tag) [ $# -ge 2 ] || { echo "ERROR: --tag needs a value." >&2; exit 2; }
      PYTEST_ARGS+=("--tag" "$2"); shift 2 ;;
    --tag=*) PYTEST_ARGS+=("--tag" "${1#--tag=}"); shift ;;
    --report-only) FAIL_MODE="false"; shift ;;
    --fail) FAIL_MODE="true"; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
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

# Optional shared settings (site.env): PROD_URL applies only when --url
# was not given. See deploy/site.env.example.
if [ -n "${SITE_ENV:-}" ]; then
  [ -f "$SITE_ENV" ] || { echo "ERROR: SITE_ENV file '$SITE_ENV' not found." >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
  if [ "$URL_GIVEN" -eq 0 ] && [ -n "${PROD_URL:-}" ]; then
    URL="$PROD_URL"
  fi
fi

# Normalize URL (no trailing slash) and prefix.
URL="$(printf '%s' "$URL" | sed -e 's#/*$##')"
[ -n "$URL" ] || { echo "ERROR: empty --url." >&2; exit 2; }
PATH_PREFIX="$(normalize_dir "$DIR_RAW")"

# Optional page list: absolute path (relative to the invocation dir), so the
# docker backend can bind-mount it into pytest_web.
PATHS_FILE_ABS=""
PATHS_COUNT=0
if [ -n "$PATHS_FILE_RAW" ]; then
  case "$PATHS_FILE_RAW" in
    /*) PATHS_FILE_ABS="$PATHS_FILE_RAW" ;;
    *) PATHS_FILE_ABS="$_START_DIR/$PATHS_FILE_RAW" ;;
  esac
  [ -f "$PATHS_FILE_ABS" ] || { echo "ERROR: --paths-file '$PATHS_FILE_RAW' not found." >&2; exit 1; }
  PATHS_COUNT="$(grep -c '^/' "$PATHS_FILE_ABS" || true)"
  [ "$PATHS_COUNT" -gt 0 ] || { echo "ERROR: --paths-file '$PATHS_FILE_RAW' lists no URL paths." >&2; exit 1; }
fi

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
    BASE_URL="$SITE_LOCAL_BASE"
  fi
  SITEMAP_LABEL="local ($BASE_URL <- src/$SITEMAP_BASE)"
else
  BASE_URL="$URL"
  SITEMAP_LABEL="web ($BASE_URL <- src/$SITEMAP_BASE)"
fi

case "$TEST" in
  all) MODULES="link content" ;;
  *)
    MODULES=""
    IFS=',' read -ra TEST_SEL <<< "$TEST"
    for t in "${TEST_SEL[@]}"; do
      case "$t" in
        link) MODULES="$MODULES link" ;;
        content) MODULES="$MODULES content" ;;
        css) MODULES="$MODULES css" ;;
        html-id) MODULES="$MODULES engine" ;;
        regression) MODULES="$MODULES regression" ;;
        footer) MODULES="$MODULES footer" ;;
        wrapped) MODULES="$MODULES wrapped" ;;
        internal-links) MODULES="$MODULES internal-links" ;;
        *) echo "ERROR: --test must be link|content|css|html-id|regression|footer|wrapped|internal-links|all (got '$t')." >&2; exit 2 ;;
      esac
    done
    ;;
esac
# Dedupe while keeping the first-mentioned order.
read -ra _mods <<< "$MODULES"
MODULES="$(printf '%s\n' "${_mods[@]}" | awk '!seen[$0]++' | paste -sd' ' -)"
[ -n "$MODULES" ] || { echo "ERROR: empty --test selection." >&2; exit 2; }
NUM_MODULES="$(printf '%s' "$MODULES" | wc -w)"

# Per-test identity: pytest module, results dir, viewer port.
# Separate volumes/viewers keep each test's Allure trend unmixed.
module_file() {
  case "$1" in
    link) printf 'test_sitemap.py' ;;
    content) printf 'test_content_loaded.py' ;;
    css) printf 'test_css_coverage.py' ;;
    engine) printf 'test_page_html_id.py' ;;
    regression) printf 'test_error_regression.py' ;;
    footer) printf 'test_footer_wrapped.py' ;;
    wrapped) printf 'test_page_wrapped_once.py' ;;
    internal-links) printf 'test_internal_links.py' ;;
  esac
}
module_port() {
  case "$1" in
    link) printf '%s' "$SITE_ALLURE_PORT" ;;
    content) printf '%s' "$((SITE_ALLURE_PORT + 1))" ;;
    css) printf '%s' "$((SITE_ALLURE_PORT + 2))" ;;
    engine) printf '%s' "$((SITE_ALLURE_PORT + 3))" ;;
    regression) printf '%s' "$((SITE_ALLURE_PORT + 5))" ;;
    footer) printf '%s' "$((SITE_ALLURE_PORT + 4))" ;;
    wrapped) printf '%s' "$((SITE_ALLURE_PORT + 6))" ;;
    internal-links) printf '' ;;  # no Allure viewer in docker-compose.yml yet
  esac
}

echo "mode:        $MODE [$SITEMAP_LABEL]"
echo "backend:     $BACKEND"
echo "test:        $TEST"
echo "base_url:    $BASE_URL"
echo "sitemap:     src/$SITEMAP_BASE"
echo "path_prefix: ${PATH_PREFIX:- (all)}"
if [ -n "$PATHS_FILE_ABS" ]; then
  echo "paths_file:  $PATHS_FILE_ABS ($PATHS_COUNT paths)"
else
  echo "paths_file:   (none)"
fi
echo "exclude:     ${EXCLUDE_RAW:- (none)}"
echo "fail_mode:   FAIL_ON_NON_200=$FAIL_MODE"

# Build CMD for one module ($1); sets CMD array and ALLURE_TARGET label.
# -q prints dots plus `tested N/TOTAL` every 100 urls (tests/conftest.py);
# -v (via --verbose) prints each URL instead.
build_cmd() {
  local mod="$1" testfile vflag
  testfile="$(module_file "$mod")"
  vflag="-q"
  [ "$VERBOSE" -eq 1 ] && vflag="-v"
  if [ "$BACKEND" = "docker" ]; then
    SITEMAP_FILE="/data/$SITEMAP_BASE"
    ALLURE_TARGET="/allure-results-$mod"
    local scope_args=()
    if [ -n "$PATHS_FILE_ABS" ]; then
      scope_args=(-v "$PATHS_FILE_ABS:/selection/paths.txt:ro" -e "PATHS_FILE=/selection/paths.txt")
    fi
    # shellcheck disable=SC2155
    CMD=(docker compose run --rm
      "${scope_args[@]}"
      -e "BASE_URL=$BASE_URL"
      -e "SITEMAP_FILE=$SITEMAP_FILE"
      -e "PATH_PREFIX=$PATH_PREFIX"
      -e "EXCLUDE_SUBSTR=$EXCLUDE_RAW"
      -e "FAIL_ON_NON_200=$FAIL_MODE"
      -e "PYTHONUNBUFFERED=1"
      pytest_web
      pytest "$vflag" "--alluredir=$ALLURE_TARGET" --clean-alluredir "$testfile"
      "${PYTEST_ARGS[@]}")
  else
    SITEMAP_FILE="src/$SITEMAP_BASE"
    ALLURE_DIR="reports/allure-results-host-$mod"
    mkdir -p "$ALLURE_DIR"
    ALLURE_TARGET="$ALLURE_DIR"
    CMD=(env
      "BASE_URL=$BASE_URL"
      "SITEMAP_FILE=$SITEMAP_FILE"
      "PATH_PREFIX=$PATH_PREFIX"
      "EXCLUDE_SUBSTR=$EXCLUDE_RAW"
      "FAIL_ON_NON_200=$FAIL_MODE"
      "PATHS_FILE=$PATHS_FILE_ABS"
      "PYTHONUNBUFFERED=1"
      python3 -m pytest "$vflag" "tests/$testfile" "--alluredir=$ALLURE_DIR" "--clean-alluredir"
      "${PYTEST_ARGS[@]}")
    if ! python3 -c "import pytest, requests" 2>/dev/null; then
      echo "WARNING: host python is missing pytest/requests." >&2
      echo "         Run: pip install -r tests/requirements.txt" >&2
    fi
  fi
}

print_link() {
  # $1 = module. Allure viewers rebuild ~10 s after the run; hard-refresh.
  local mod="$1" port
  port="$(module_port "$mod")"
  if [ "$BACKEND" = "docker" ] && [ -z "$port" ]; then
    echo "Allure results [$mod]: no viewer in docker-compose.yml, results stay in the run container"
  elif [ "$BACKEND" = "docker" ]; then
    echo "Allure trend [$mod] (allure rebuilds it ~10 s after the run):"
    echo "  http://127.0.0.1:$port/allure-docker-service/projects/default/reports/latest/index.html"
  else
    echo "Allure results [$mod]: $ALLURE_TARGET/"
    echo "  view with: allure serve $ALLURE_TARGET"
  fi
}

RC=0
# Cheap total for the progress line (exact count comes from pytest later).
URL_TOTAL="$(grep -c "<loc>" "src/$SITEMAP_BASE" 2>/dev/null || echo "?")"
[ -n "$PATHS_FILE_ABS" ] && URL_TOTAL="$PATHS_COUNT"

# Multi-test runs capture each pytest output to build the end-of-run
# summary. Single-test runs stream untouched (current behavior).
if [ "$NUM_MODULES" -gt 1 ] && [ "$DRY_RUN" -eq 0 ]; then
  CAPTURE=1
  SUM_TMP="$(mktemp -d)"
  trap 'rm -rf "$SUM_TMP"' EXIT
else
  CAPTURE=0
fi
SUM_NAME=(); SUM_RC=(); SUM_LINE=()

pytest_summary() {
  # $1 = captured output file -> pytest's own counts line, or a fallback.
  local line
  line="$(tr -d '\r' < "$1" | grep -E '[0-9]+ (passed|failed)' | tail -1 || true)"
  if [ -n "$line" ]; then
    printf '%s' "$line" | sed -e 's/[=]//g' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
  else
    printf '%s' "(no pytest summary line)"
  fi
}

for mod in $MODULES; do
  build_cmd "$mod"
  # Capture needs the pytest side unbuffered-free of pty artifacts:
  # docker compose allocates a TTY by default, which mangles the
  # streamed output - disable it when the output goes through a pipe.
  if [ "$CAPTURE" -eq 1 ] && [ "$BACKEND" = "docker" ]; then
    CMD=(docker compose run --rm -T "${CMD[@]:4}")
  fi
  echo "+ ${CMD[*]}"
  if [ "$DRY_RUN" -eq 1 ]; then
    continue
  fi
  echo "starting $mod check: ~$URL_TOTAL urls at $(date -u +%H:%M:%SZ) ..."
  # `set +e` so a failing pytest still falls through to the next test;
  # the worst exit code is re-raised at the end.
  set +e
  if [ "$CAPTURE" -eq 1 ]; then
    "${CMD[@]}" | tee "$SUM_TMP/$mod.log"
    mod_rc=${PIPESTATUS[0]}
    set -e
    SUM_NAME+=("$mod")
    SUM_RC+=("$mod_rc")
    SUM_LINE+=("$(pytest_summary "$SUM_TMP/$mod.log")")
  else
    "${CMD[@]}"
    mod_rc=$?
    set -e
  fi
  [ "$mod_rc" -ne 0 ] && RC="$mod_rc"
  echo
  print_link "$mod"
done

# Short summary for all executed tests (multi-test runs only).
if [ "$CAPTURE" -eq 1 ] && [ "${#SUM_NAME[@]}" -gt 0 ]; then
  echo
  echo "summary ($MODE, $BASE_URL)"
  overall="all tests passed"
  i=0
  while [ "$i" -lt "${#SUM_NAME[@]}" ]; do
    if [ "${SUM_RC[$i]}" -eq 0 ]; then
      status="PASS"
    else
      status="FAIL"
      overall="FAILURES present"
    fi
    printf '%-10s %-4s %s\n' "${SUM_NAME[$i]}" "$status" "${SUM_LINE[$i]}"
    i=$((i + 1))
  done
  echo "result: $overall (exit $RC)"
fi
exit "$RC"
