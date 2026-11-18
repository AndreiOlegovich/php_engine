#!/usr/bin/env bash
# version 0.3.2
# Run the container tests (container/tests) with the project venv.
#
# Usage (from any folder):
#   container/tests/run_test.sh [-y] [--setup-only] [TEST ...] [-- PYTEST_ARGS...]
#
#   TEST          a test name ("redirects" -> tests/test_redirects.py), a file
#                 or folder relative to container/ (tests/test_x.py, tests/),
#                 several allowed. No TEST: all of tests/.
#   -y            answer yes to the question about using the venv (no prompt)
#   --setup-only  create or update container/.venv only, then exit
#   -- ARGS       everything after -- goes to pytest unchanged (-k, -x, --tag ...)
#                 without other args pytest gets -q
#
# The venv: this script cannot activate a venv in your shell. If the venv is
# not active it asks (Y/n) to use container/.venv for this run, and prints the
# command that activates it in your shell.
#
# Environment: BASE_URL (default http://127.0.0.1:8080), SITEMAP_FILE (default
# src/sitemap.xml), PYTHON_VERSION (default 3.12).
# Exit: pytest's exit code, or 1/2 when the script stops before pytest.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$ROOT/.venv"
REQS="$ROOT/tests/requirements.txt"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

show_help() {
  cat <<'HELP'
run_test.sh: run the container tests in container/tests with the project venv.

USAGE
  container/tests/run_test.sh [-y] [--setup-only] [TEST ...] [-- PYTEST_ARGS...]
  container/tests/run_test.sh --help

  Run it from any folder. TEST is a name (redirects -> tests/test_redirects.py),
  or a file/folder relative to container/ (tests/test_redirects.py, tests/).
  Give several TESTs to run them together. With no TEST, or with --all, every
  test file runs, one pytest per file. Everything after -- goes to pytest
  unchanged. Without other pytest args, -q is added.

LOGS
  Every run prints to the console and also saves each test's output in a log
  folder: tests/run_test_log/<name>.log, plus summary.txt (PASS or FAIL per
  test file). A new run deletes the old files in that folder first.
  -dn NAME (alias --dir-nested) saves into run_test_log/NAME instead, so each
  set of results gets its own folder. run_test_log/ is in .gitignore.
  -d NAME uses another folder (absolute, or relative to tests/); it must be
  empty or one this script made (it has the marker file .run_test_logs), so
  other folders are refused. -d and -dn can't be combined.
  The exit code is 1 if any test file failed.

OPTIONS
  --all         run every test file (same as no TEST)
  -d NAME       log folder: an absolute path or one relative to tests/
  -dn NAME, --dir-nested NAME
                log folder run_test_log/NAME (gitignored)
  -y            say yes to the venv question (use it in scripts)
  --setup-only  create or update container/.venv (uv), then exit
  -h, --help    this text

VENV
  The venv is container/.venv (Python 3.12). If it is missing or the wrong
  version, the script asks to create it. If it is not active in your shell,
  the script prints "source /.../container/.venv/bin/activate" and asks whether
  it can use the venv for this run. It cannot change your shell: the activate
  command has to be run by you.

ENVIRONMENT (defaults are the same as the old tools/run-tests-venv.sh)
  BASE_URL       the site under test       default http://127.0.0.1:8080
                 (local php-apache; use https://dev.example.com for dev,
                 https://example.com only for read-only checks)
  SITEMAP_FILE   sitemap for the sitemap tests  default src/sitemap.xml
                 (relative to container/)
  PYTHON_VERSION  default 3.12
  Test-specific variables are listed under each test below (see the file's
  docstring for the full list).

WHAT NEEDS WHAT
  NEEDS A RUNNING SITE (BASE_URL must answer; start the local site first)
    sitemap, content_loaded, page_html_id, page_wrapped_once, footer_wrapped,
    css_coverage, internal_links, error_regression, redirects, url_selection
  NEEDS THE NETWORK, NOT THE LOCAL SITE
    css_parity (fetches /.css/site.css from dev and prod)
  NEEDS GIT (reads the repo history)
    html_code_corruption, log_paths, post_to_feed
  STATIC (reads files only, no site, no network)
    css_sources, download_logs, img_src, page_stylesheets,
    python_version_consistency, ru_related_articles
  NEEDS PHP (the php command; no site, no network)
    search_quality

TESTS (name -> file: what it checks; example)

  sitemap            test_sitemap.py: every sitemap <loc> answers HTTP 200,
                     one case per URL. Needs the site and SITEMAP_FILE.
                     Env: PATH_PREFIX (only pages under this path, e.g. /section).
                     PATH_PREFIX=/section ./run_test.sh sitemap

  content_loaded     test_content_loaded.py: every sitemap page renders the
                     bottom marker <div id="footer-end"></div>.
                     Env: PATH_PREFIX, EXCLUDE_SUBSTR, FAIL_ON_NON_200, FAIL_ON_MISSING_BOTTOM.
                     ./run_test.sh content_loaded

  page_html_id       test_page_html_id.py: every sitemap page renders one engine
                     marker (Page / BasePage / ArticlePage).
                     Env: PATH_PREFIX.
                     ./run_test.sh page_html_id

  page_wrapped_once  test_page_wrapped_once.py: no width-constraining wrapper
                     sits inside another one (sitemap pages).
                     ./run_test.sh page_wrapped_once

  footer_wrapped     test_footer_wrapped.py: pages that render the contacts footer
                     wrap it in a width-constraining div.
                     Env: PATH_PREFIX, EXCLUDE_SUBSTR, FAIL_ON_UNWRAPPED_FOOTER.
                     ./run_test.sh footer_wrapped

  css_coverage       test_css_coverage.py: every <aside> on a sitemap page has
                     matching CSS rules (catches unstyled asides).
                     Env: PATH_PREFIX, EXCLUDE_SUBSTR, FAIL_ON_UNSTYLED.
                     ./run_test.sh css_coverage

  css_parity         test_css_parity.py: /.css/site.css is identical on local,
                     dev and prod (catches deploy drift). Network needed.
                     Env: CSS_PARITY_DEV, CSS_PARITY_PROD, CSS_PARITY_PATH, CSS_PARITY_TIMEOUT.
                     ./run_test.sh css_parity

  css_sources        test_css_sources.py: every CSS source file is listed in
                     css_pipeline/targets.conf (static).
                     ./run_test.sh css_sources

  download_logs      test_download_logs.py: size policy of
                     tools/analyze_logs/download-logs.sh (static).
                     ./run_test.sh download_logs

  error_regression   test_error_regression.py: pages that fataled on prod or dev
                     render without a PHP error. Env: HTACCESS_FILE,
                     FAIL_ON_NON_200, FAIL_ON_PHP_ERROR, PATH_PREFIX.
                     ./run_test.sh error_regression

  html_code_corruption  test_html_code_corruption.py: no HTML/PHP reflow damage
                     in container/src (static, git for the history check).
                     Env: REFLOW_SRC_DIR.
                     ./run_test.sh html_code_corruption

  img_src            test_img_src.py: every <img> in the source resolves to a
                     file (static).
                     ./run_test.sh img_src

  internal_links     test_internal_links.py: every root-relative <a href="/...">
                     on a sitemap page answers 200. A broken link is listed with
                     every page it is on. Needs the site and SITEMAP_FILE.
                     ./run_test.sh internal_links

  log_paths          test_log_paths.py: the external log-root resolver used by the
                     shell and Python tools (git used by the repo check).
                     ./run_test.sh log_paths

  page_stylesheets   test_page_stylesheets.py: each tree's page classes and pages
                     link that tree's site sheet (src/ site.css, src/ru/ ru.css,
                     and so on) (static).
                     ./run_test.sh page_stylesheets

  post_to_feed       test_post_to_feed.py: the feed builder: page changes per
                     commit, the walk over main, the feed-only commit (git).
                     ./run_test.sh post_to_feed

  python_version_consistency  test_python_version_consistency.py: Python 3.12
                     toolchain guards (pyvenv.cfg, venv recreation) (static).
                     ./run_test.sh python_version_consistency

  redirects          test_redirects.py: answers of container/src/.htaccess over
                     HTTP: status and exact Location for each CASES entry, plus
                     sampled rules. The sampled group needs
                     container/tools/htaccess_redirects.py and the repo .htaccess.
                     Env: BASE_URL, REDIRECT_SAMPLE_STEP (default 25; 1 = every
                     rule, 0 = none), HTACCESS_FILE.
                     ./run_test.sh redirects
                     BASE_URL=https://dev.example.com ./run_test.sh redirects
                     REDIRECT_SAMPLE_STEP=0 BASE_URL=https://example.com ./run_test.sh redirects

  ru_related_articles  test_ru_related_articles.py: Russian pages load
                     RelatedArticles from Russian folders only (static).
                     ./run_test.sh ru_related_articles

  search_quality     test_search_quality.py: the site search keeps its ranking
                     on two fixed query sets (tests/search/*.tsv) over the
                     real English index: MRR and top-5 share above a floor.
                     .venv/bin/python tests/test_search_quality.py prints the numbers.
                     ./run_test.sh search_quality

  url_selection      test_url_selection.py: PATHS_FILE narrows the sitemap tests
                     to the listed pages (needs the site and SITEMAP_FILE).
                     ./run_test.sh url_selection

EXAMPLES
  ./run_test.sh --setup-only                  # make or update the venv
  ./run_test.sh redirects -- -q -x           # stop at the first failure
  ./run_test.sh sitemap -- -k qa              # pytest -k filter
  ./run_test.sh css_sources download_logs     # static tests, no site needed
  ./run_test.sh -y                            # all tests, no venv question
  ./run_test.sh --all                         # all tests, logs in tests/run_test_log/
  ./run_test.sh --all -dn nightly             # all tests, logs in tests/run_test_log/nightly/
  ./run_test.sh css_sources -dn check1 -- -x  # one test, logs in tests/run_test_log/check1/

NOTES
  - A run with no TEST, or with --all, runs every test file. Site tests fail when the site is not running.
  - Some tests need a value in the environment (see their line above).
  - The file docstrings in tests/ hold the full variable list.
HELP
}

ASSUME_YES=0
SETUP_ONLY=0
RUN_ALL=0
LOG_NAME=""
NESTED=""
TARGETS=()
PYTEST_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1; shift ;;
    --setup-only) SETUP_ONLY=1; shift ;;
    --all) RUN_ALL=1; shift ;;
    -d) [ $# -ge 2 ] || { echo "-d needs a folder name" >&2; exit 2; }; LOG_NAME="$2"; shift 2 ;;
    -dn|--dir-nested) [ $# -ge 2 ] || { echo "$1 needs a folder name" >&2; exit 2; }; NESTED="$2"; shift 2 ;;
    -h|--help) show_help; exit 0 ;;
    --) shift; PYTEST_ARGS+=("$@"); break ;;
    -*) echo "unknown option: $1 (see -h)" >&2; exit 2 ;;
    *) TARGETS+=("$1"); shift ;;
  esac
done

# Turn a TEST argument into a path relative to container/, appended to RESOLVED.
RESOLVED=()
resolve_into_list() {
  local t="$1"
  if [ -e "$ROOT/tests/test_$t.py" ]; then RESOLVED+=("tests/test_$t.py")
  elif [ -e "$ROOT/$t" ]; then RESOLVED+=("$t")
  else echo "no such test: $t (name, or a path under container/)" >&2; exit 2
  fi
}

ask_yes() {
  # ask_yes "question": 0 = yes. -y answers yes; no terminal answers no.
  [ "$ASSUME_YES" -eq 1 ] && return 0
  if [ ! -t 0 ]; then
    echo "$1 [no terminal to answer; rerun with -y to say yes]" >&2
    return 1
  fi
  local answer
  read -r -p "$1 [Y/n] " answer
  case "${answer:-Y}" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

venv_ready() {
  [ -x "$VENV/bin/python" ] &&
    [ "$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" = "$PYTHON_VERSION" ]
}

venv_active() {
  [ -n "${VIRTUAL_ENV:-}" ] && [ "$VIRTUAL_ENV" = "$VENV" ]
}

create_venv() {
  command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not found (https://docs.astral.sh/uv/)." >&2; exit 2; }
  [ -f "$REQS" ] || { echo "ERROR: requirements not found: $REQS" >&2; exit 2; }
  rm -rf "$VENV"
  echo "creating $VENV for Python $PYTHON_VERSION ..."
  uv venv --python "$PYTHON_VERSION" "$VENV"
  echo "installing test deps from $REQS ..."
  uv pip install --python "$VENV/bin/python" -r "$REQS"
}

if ! venv_ready; then
  echo "The test venv is missing or not Python $PYTHON_VERSION: $VENV"
  if ask_yes "Create it now (uv, installs the test deps)?"; then
    create_venv
  else
    echo "stopped. Create the venv, then run this script again."
    exit 1
  fi
fi

if [ "$SETUP_ONLY" -eq 1 ]; then
  echo "venv ready: $VENV ($("$VENV/bin/python" --version 2>&1))"
  echo "to activate it in your shell: source $VENV/bin/activate"
  exit 0
fi

if ! venv_active; then
  echo "The venv is not activated in this shell."
  echo "To activate it yourself, run this command in your shell:"
  echo "  source $VENV/bin/activate"
  if ! ask_yes "Can this script use $VENV for this run?"; then
    echo "stopped. Run the command above, then run this script again."
    exit 1
  fi
  echo "using $VENV/bin/python for this run."
fi

export BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
export SITEMAP_FILE="${SITEMAP_FILE:-src/sitemap.xml}"
echo "BASE_URL=$BASE_URL SITEMAP_FILE=$SITEMAP_FILE"

cd "$ROOT"
if [ "$RUN_ALL" -eq 1 ] || [ ${#TARGETS[@]} -eq 0 ]; then
  for f in tests/test_*.py; do RESOLVED+=("$f"); done
else
  for t in "${TARGETS[@]}"; do resolve_into_list "$t"; done
fi
if [ ${#PYTEST_ARGS[@]} -eq 0 ]; then
  PYTEST_ARGS=("-q")
fi

# Log folder: tests/run_tests_logs, or -d NAME (a path under tests/ or absolute).
# Each run deletes the old results in that folder and saves the new ones.
# Only a folder this script made (it has the marker file) is emptied; any other
# non-empty folder is refused, so -d cannot wipe something by mistake.
MARK=".run_test_logs"
BASE_LOG="$SCRIPT_DIR/run_test_log"
if [ -n "$NESTED" ] && [ -n "$LOG_NAME" ]; then
  echo "ERROR: use either -d or -dn, not both." >&2
  exit 2
fi
if [ -n "$NESTED" ]; then
  case "$NESTED" in
    */*|.|..) echo "ERROR: -dn takes one folder name, not a path: $NESTED" >&2; exit 2 ;;
  esac
  LOG_DIR="$BASE_LOG/$NESTED"
elif [ -z "$LOG_NAME" ]; then
  LOG_DIR="$BASE_LOG"
else
  case "$LOG_NAME" in
    /*) LOG_DIR="$LOG_NAME" ;;
    *) LOG_DIR="$SCRIPT_DIR/$LOG_NAME" ;;
  esac
fi
# The folder this run writes into must be empty, or one made by this script.
if [ -e "$LOG_DIR" ]; then
  if [ ! -d "$LOG_DIR" ] || { [ ! -f "$LOG_DIR/$MARK" ] && [ -n "$(ls -A "$LOG_DIR")" ]; }; then
    echo "ERROR: $LOG_DIR exists and was not made by this script; pick another name." >&2
    exit 2
  fi
  # Only the files of the last run are removed; nested folders stay.
  find "$LOG_DIR" -maxdepth 1 -type f -delete
fi
mkdir -p "$LOG_DIR"
touch "$LOG_DIR/$MARK"
mkdir -p "$BASE_LOG"
touch "$BASE_LOG/$MARK"
SUMMARY="$LOG_DIR/summary.txt"
: > "$SUMMARY"

echo "logs: $LOG_DIR"
FAILED=0
for t in "${RESOLVED[@]}"; do
  name="$(basename "$t" .py)"
  name="${name#test_}"
  log="$LOG_DIR/$name.log"
  echo "=== $t (log: $log)"
  set +e
  "$VENV/bin/python" -m pytest "$t" "${PYTEST_ARGS[@]}" 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e
  if [ "$rc" -eq 0 ]; then
    echo "PASS $t" >> "$SUMMARY"
  else
    echo "FAIL(exit $rc) $t" >> "$SUMMARY"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "summary ($SUMMARY):"
cat "$SUMMARY"
if [ "$FAILED" -gt 0 ]; then
  echo "$FAILED of ${#RESOLVED[@]} test file(s) failed."
  exit 1
fi
echo "all ${#RESOLVED[@]} test file(s) passed."
