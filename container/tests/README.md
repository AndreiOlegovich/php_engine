# container/tests — ported test suite

Pytest suite for the tools in `container/tools/`. Run it from `container/`
through the uv-managed venv:

```bash
./tools/run-tests-venv.sh                 # whole suite
./tools/run-tests-venv.sh -- tests/linters -q
./tools/run-tests-venv.sh -- tests/tools -q
```

`run-tests-venv.sh` creates `container/.venv` with `uv`, installs
`tests/requirements.txt` and recreates the venv when it was built for
another Python minor. Only `uv` and Python 3.12 are needed on the host.

## Static checks (no server needed)

| File | Asserts |
|---|---|
| `test_html_code_corruption.py` | no reflow damage under `src/`: broken HTML attributes, dot-broken code lines, `&gt;` inside tags |
| `test_python_version_consistency.py` | pyproject, lockfile, Docker image, pre-commit and venvs all agree on Python 3.12 |
| `linters/test_check_version_sh.py` | the version-marker gate fails an unbumped staged script or page and passes a bumped one |
| `linters/test_lint_php_sh.py` | `php -l` reports every broken fixture and skips non-PHP input (needs Docker) |
| `linters/test_lint_static_sh.py` | the phpcs gate reports every broken fixture and stays wired in pre-commit (needs Docker) |
| `tools/test_htaccess_redirects.py` | either `.htaccess` redirect form becomes a path regex; CLI exit codes |
| `tools/test_analyze_errors.py`, `tools/test_check_bots.py`, `test_log_paths.py`, `test_download_logs.py` | log analyzers take a file/dir argument and keep the documented rules |
| `tools/test_ru_en_translate.py` | the translation pipeline preserves markup and placeholders |

## Files

```text
tests/conftest.py            progress output and shared pytest options
tests/linters/               self-tests of the linters and gates
tests/tools/                 tests of the ported command-line tools
tests/test_*.py              whole-tree and toolchain gates
tests/requirements.txt       pytest, requests, pyyaml
tools/run-tests-venv.sh      uv venv + pytest runner
```

Docker-backed linters are skipped when `docker` is not on `PATH`.

## HTTP tests (need the local site and a sitemap)

| File | Asserts |
|---|---|
| `test_sitemap.py` | every sitemap `<loc>` answers 200 (one case per URL) |
| `test_content_loaded.py` | every page renders the bottom marker (`SITE_BOTTOM_MARKER`) |
| `test_page_html_id.py` | every page renders one shell marker (`SITE_PAGE_MARKERS`) |
| `test_page_stylesheets.py` | each tree links its own sheet (`SITE_TREE_SHEETS`) |
| `test_page_wrapped_once.py` | no width wrapper inside another (`SITE_WRAPPER_CLASSES`) |
| `test_footer_wrapped.py` | the footer table sits inside a wrapper |
| `test_internal_links.py` | every root-relative link answers 200 |

Site values come from `container/site-profile.env` (see
`site-profile.env.example`); a test whose key is unset skips.
`tests/url_selection.py` narrows a run to `PATHS_FILE`;
`tests/run_sitemap_tests.sh --test link|all` picks the group.

## Redirect, error and image gates (need the local site)

| File | Asserts |
|---|---|
| `test_redirects.py` | one request per case in `redirect_cases.py`: status and exact `Location` (copy `redirect_cases.example.py`) |
| `test_error_regression.py` | pages in `error_regression_cases.py` render without the PHP fatal signature |
| `test_img_src.py` | every `<img>` has a usable `src`; own-host URLs resolve locally (`SITE_OWN_HOSTS`) |
