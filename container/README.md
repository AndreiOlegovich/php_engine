# php_engine container

Local Docker setup for a PHP site (PHP 8.5 and Apache), plus a pytest link
checker with Allure reporting. To start a new site, read the README at the
repository root first.

All commands run from this directory (`container/`). The names and ports come
from `container/.env`, which `tools/new-project.sh` writes: `<project>` below
is its `SITE_PROJECT`, `<port>` its `SITE_HTTP_PORT` and `<allure-port>` its
`SITE_ALLURE_PORT`. Without that file they are `php_engine`, 8080 and 5050.

## Services

| Service | Container | What | URL |
|---|---|---|---|
| `php-apache` | `<project>-apache` | the site (PHP 8.5, Apache) | `http://127.0.0.1:<port>/` |
| `pytest_web` | `<project>-pytest-web` | one-shot link checker (pytest) | — |
| `allure-web` | `<project>-allure-web` | Allure report viewer | `http://127.0.0.1:<allure-port>/` |

Compose commands take the service name (`php-apache`); plain `docker`
commands take the container name (`<project>-apache`).

## Quickstart

```bash
docker compose up -d --build php-apache   # build and start the site
docker compose ps                         # status
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<port>/   # 200
```

Open `http://127.0.0.1:<port>/` in a browser. Code lives in `./src` and is
bind-mounted to `/var/www/html`: edits are live, no rebuild needed.

Start only `php-apache`: a plain `docker compose up -d` also runs the one-shot
link checker, which needs a sitemap (see "Link checker" below).

A page with a PHP error can still answer `200`: `display_errors` is on, so
PHP prints the error into the page. The error log is the place to look:

```bash
docker compose logs php-apache 2>&1 | grep -E 'PHP (Parse|Fatal) error'
```

## Everyday commands

```bash
docker compose up -d php-apache   # start
docker compose stop               # stop (keeps containers)
docker compose start              # start again
docker compose restart php-apache # restart, e.g. after editing php-engine.ini
docker compose down               # stop and remove containers (code in ./src is safe)
docker compose logs --tail=30 php-apache   # Apache access and error log
docker compose logs -f php-apache          # follow the log
docker compose exec php-apache bash                 # shell in the web container (root)
docker compose exec --user www-data php-apache bash # shell as the Apache user
```

Builds stay fast whatever the size of the site: `.dockerignore` sends only
`containerfiles/` and `pytest_web/` to Docker. To build for another host user:
`HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d --build php-apache`.

## PHP version

```bash
docker compose up -d --build php-apache              # PHP 8.5 (image <project>:8.5)
PHP_VER=7.4 docker compose up -d --build php-apache  # another version (image <project>:7.4)
docker compose exec php-apache php -v                # which one runs
```

The version is a build arg (`containerfiles/Dockerfile.php`: `ARG PHP_VER`),
so the images live side by side; switching recreates only the `php-apache`
container, and the code in `./src` is untouched. A version you want to keep
for a while goes into `container/.env` as `PHP_VER=7.4`; without it the next
`docker compose up` goes back to 8.5. An older version is for comparing how
an imported site behaved before; the checks and the linters target 8.5.

`containerfiles/php-engine.ini` hides `E_DEPRECATED` notices, from the page
and from the log alike. To list them, set `error_reporting = E_ALL` there,
run `docker compose restart php-apache` and read the log.

## PHP lint

`lint-php.sh` runs `php -l` with PHP 8.5 over all or selected files. It uses
this project's `php-apache` container when it already runs PHP 8.5, and a
one-off `php:8.5-cli` image otherwise:

```bash
tools/linters/lint-php.sh --staged                  # staged changes only
tools/linters/lint-php.sh example/index.php example/ # files and folders under src/
tools/linters/lint-php.sh --files "a.php,b.php"
tools/linters/lint-php.sh --all                     # every *.php under src/
tools/linters/lint-php.sh --php 7.4 old/page.php    # lint with another PHP
```

Exit `0` = clean, `1` = syntax errors listed as `FAIL: <path>`. `php -l`
finds only syntax errors; a removed function such as `mysql_connect()` fails
when the page runs, so read the error log as well.

Deploy hook: `tools/deploy/deploy-ftp.sh --lint [--php VER] ...` lints the
same selection first and aborts the upload on failure (works with `--dry-run`
for a lint-only run).

## JS lint

`lint-js.sh` runs `node --check` (host node, no dependencies) with the same
selection flags as the PHP linter (`--staged`, `--committed`, `--files`,
positional paths, `--all` = default):

```bash
tools/linters/lint-js.sh --staged
tools/linters/lint-js.sh js/app.js
tools/linters/lint-js.sh --all
```

Files with ESM syntax (`import`/`export`) are checked via a temp `.mjs` copy,
since `node --check` parses `.js` as CommonJS. Exit `0` clean / `1` with
`FAIL:` lines.

## Malformed-URL scan

`find-malformed-urls.sh` greps the tree for concatenated or dotless URLs
(`https://…https://…`, `https://ru/…`, …) with the same selection flags as
the linters (`--staged`, `--committed`, `--files`, paths, `--all`):

```bash
tools/linters/find-malformed-urls.sh --staged
tools/linters/find-malformed-urls.sh --all
```

Exit `0` clean / `1` with `path:line: … [check]` lines. Doc placeholders
(`http://url/`, `http://foo/`, …) and `localhost` are skipped; the rest
needs human review before fixing.

## Python lint (uv + ruff + pylint + mypy + pre-commit)

The maintained Python is in `pytest_web/`, `tools/` and `tests/`. The site's
own Python under `src/` is often legacy (Python 2) and every Python linter and
hook skips it. Managed with `uv` (install:
`curl -LsSf https://astral.sh/uv/install.sh | sh`):

```bash
uv sync                        # create .venv (deps from pyproject.toml)
uv run --frozen ruff check pytest_web tools tests
uv run --frozen ruff format --check pytest_web tools tests
uv run --frozen mypy pytest_web
uv run --frozen pylint pytest_web
```

Config lives in `pyproject.toml` (ruff select `E,F,W,I,N,UP,B,C4,SIM`,
line-length 100; lenient mypy baseline; pylint with pytest-fixture
`redefined-outer-name` disabled).

Pre-commit (installed via `uv tool install --python 3.14 pre-commit`; the
config sits at the repository root, the parent of `container/`, so run it
from there):

```bash
cd ..
pre-commit run --files container/pytest_web/test_sitemap.py
```

Brand-new untracked files are invisible to a bare `--all-files`; pass
`--files` until the first commit.

## Link checker + Allure

`pytest_web` GETs every URL of a sitemap against the web container:

```bash
docker compose up -d allure-web                            # the report viewer
docker compose run --rm pytest_web                         # checks src/sitemap.xml
SITEMAP_FILE=/data/sitemap_new.xml FAIL_ON_NON_200=false \
  docker compose run --rm -e SITEMAP_FILE -e FAIL_ON_NON_200 pytest_web
```

Env knobs: `BASE_URL` (default `http://php-apache`), `SITEMAP_FILE`
(default `/data/sitemap.xml`, which is `src/sitemap.xml` on the host),
`FAIL_ON_NON_200` (`true` = non-200 cases fail, `false` = report-only).
A site without a sitemap gets one from the generator below.

## Reading the Allure report

`allure-web` rebuilds the report about 10 s after each checker run
(hard-refresh to see it):

```text
http://127.0.0.1:<allure-port>/allure-docker-service/projects/default/reports/latest/index.html
```

Where to click:

1. **`Suites`** (left sidebar) → `test_sitemap` → **one row per URL**,
   green = 200, red = anything else. Click a row for its `GET …` step and
   the `response` attachment (final URL, status, redirect count).
2. **Status filter**: the red **Failed** pill shows only the broken URLs.
3. **`Graphs`**: pie chart of passed vs failed (= 200 vs non-200 URLs).
4. **`test_summary`** (last case in Suites) → **Attachments**:
   `status-summary.txt` (200/404/other/error counts), `status-summary.json`,
   `all-urls.csv` (every URL and status, downloadable).

How it works under the hood: sitemap hosts are rewritten to the local
container, the checker sends `X-Forwarded-Proto: https` the way a hosting
proxy does (so an `.htaccess` rule that forces https sees https), redirects
are followed pinned to the local host, and landing on `/404.php` counts as
404.

No-server fallback (same data, plain HTML table with filters):

```bash
tools/build-static-report.sh        # after a pytest_web run
# -> reports/sitemap-check-report.html (double-click to open)
# -> reports/all-urls.csv (spreadsheet)
```

## Sitemap generator

```bash
BASE_URL=https://www.example.com tools/generate-sitemap.sh   # writes src/sitemap.xml
tools/generate-sitemap.sh [src_dir] [output.xml]
tools/check-sitemap.sh src/sitemap.xml                        # validate it
```

Scans `*.php` and skips what `tools/sitemap-excludes.conf` lists (dot- and
`_`-folders, includes, backups); `lastmod` comes from the file time. The
previous file is kept as `backup/<name>.bak-<timestamp>` at the repository
root (git-ignored, outside the docroot).

## Docs

- `CONTAINER.md`: full operations manual (build, run, stop, enter, debug,
  troubleshooting table, permanent permission fix).
- `tools/fix/FIX_PERMISSIONS.md`: the `700`-permission problem: symptoms,
  manual fix, stale-container conflict.
- `tools/fix/fix-permissions.sh`: permission diagnose/fix and health check
  (`--check` = diagnose only).
- `docs/site-checks.md`: what each commit hook on `src/` blocks and how to
  fix it.

## Tool index

Everything runs from `container/`. Scripts are host-side unless noted.

| Area | Command | Needs |
|---|---|---|
| PHP syntax | `tools/linters/lint-php.sh --files src/x.php` | Docker |
| PHP style / static analysis | `tools/linters/lint-static.sh --tool phpcs --files src/x.php` | Docker |
| Shell | `tools/linters/lint-sh.sh --all` | Docker |
| Line endings | `tools/linters/check-line-endings.sh --search` | bash |
| PHP blank lines / header | `tools/linters/check-php-blank-lines.sh --search`, `tools/linters/check-php-header.sh --search` | bash |
| PHP long lines / braces | `tools/fix/wrap-long-lines.sh --search`, `tools/fix/fix-closing-brace.sh` | php |
| Duplicate images | `tools/fix/dedupe-images.py --dir src/images` | python, php |
| Version markers | `tools/linters/check-version.sh` (staged) | git |
| Diff artifacts | `tools/check-diff-artifacts.sh --staged` | git |
| Reflow damage | `tools/linters/check-reflow.sh` | python |
| Snake-case calls | `tools/linters/find-snake-calls.py --src src` | python |
| HTML structure | `tools/linters/html/validate-local-html.sh /` | curl, npx |
| Sitemap / redirects | `tools/check-sitemap.sh`, `tools/htaccess_redirects.py list` | python |
| Labels | `tools/zpl/render_zpl.py --page src/labels/zpl_01.php` | network |
| Translation | `tools/translate/ru_en_translate.py extract PATH --workdir DIR` | optional Argos/Ollama |
| Logs | `tools/analyze_logs/analyze_errors.py --log-dir DIR`, `check_bots.py`, `list_404.py`, `find_broken_hrefs.py` | python |
| Deploy | `tools/deploy/deploy-engine.sh --dry-run`, `tools/deploy/deploy-batches.sh` | creds file |
| Tests | `tools/run-tests-venv.sh -- tests -q` | uv, Python 3.14 |
| Agent tooling | `tools/ai/opencode.sh`, `tools/ai/opencode-docker.sh` | opencode, Docker |
