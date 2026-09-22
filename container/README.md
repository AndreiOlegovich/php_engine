# aredel container

Local Docker setup for the aredel.com PHP site (PHP 8.0 + Apache), plus a
pytest link checker with Allure reporting.

All commands run from this directory (`container/`).

## Services

| Service | Container | What | URL |
|---|---|---|---|
| `php-apache` | `php-apache` | the site (PHP 8.0, Apache) | `http://127.0.0.1:8080/` |
| `pytest_web` | `pytest-web` | one-shot link checker (pytest) | — |
| `allure-web` | `allure-web` | Allure report viewer | `http://127.0.0.1:5050/` |

## Quickstart

```bash
docker compose up -d --build   # build + start everything
docker compose ps              # check status
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/   # 200
```

Open `http://127.0.0.1:8080/` in a browser. Code lives in `./src` and is
bind-mounted to `/var/www/html` — edits are live, no rebuild needed.

## Everyday commands

```bash
docker compose up -d            # start
docker compose stop             # stop (keeps containers)
docker compose start            # start again
docker compose restart          # restart
docker compose down             # stop + remove containers (code in ./src is safe)
docker compose logs --tail=30   # apache access/error log
docker compose logs -f          # follow logs
docker compose exec php-apache bash                 # shell in web container (root)
docker compose exec --user www-data php-apache bash # shell as the Apache user
```

Builds stay fast on purpose: `.dockerignore` excludes `src/` (2.8 GB).
To build for another host user: `HOST_UID=$(id -u) HOST_GID=$(id -g)
docker compose up -d --build`.

## PHP lint

`lint-php.sh` runs `php -l` (project's PHP 8.0, via the `php-apache`
container — started automatically if stopped) over all or selected files:

```bash
./lint-php.sh --staged                  # staged changes only
./lint-php.sh qa/index.php ru/qa/       # explicit files/dirs
./lint-php.sh --files "a.php,b.php"
./lint-php.sh --all                     # all 5k+ *.php under src/
./lint-php.sh --php 8.2 qa/index.php    # lint with PHP 8.2 (default: 8.0)
```

Exit `0` = clean, `1` = syntax errors listed as `FAIL: <path>`.
Deploy hook: `deploy-ftp.sh --lint [--php VER] ...` lints the same selection
first and aborts the upload on failure (works with `--dry-run` for a
lint-only run). `--php` defaults to `8.0` (project container); other versions
run via one-off `php:VER-cli` images, pulled once from Docker Hub.

Known failures (pre-existing, pages 500 locally too): `ao/api/php_auth/index.php`,
`ao/services/php_auth/digest.php`, `ru/api/php_auth/index.php` (stray text at
line ~73). Fix separately; until then a full `--all` lint stays red.

## JS lint

`lint-js.sh` runs `node --check` (host node, no dependencies) with the same
selection flags as the PHP linter (`--staged`, `--committed`, `--files`,
positional paths, `--all` = default):

```bash
./lint-js.sh --staged
./lint-js.sh ao/chessboard/game.js
./lint-js.sh --all     # ~116 *.js under src/
```

Files with ESM syntax (`import`/`export`, e.g. `ao/chessboard/*.js`) are
checked via a temp `.mjs` copy, since `node --check` parses `.js` as
CommonJS. Exit `0` clean / `1` with `FAIL:` lines.

Known failures (pre-existing): 2 truncated archive copies
(`ao/chessboard/arj/MinimalChess.js`, `ao/chessboard copy/MinimalChess.js`)
and 3 `disqus_en.js` files containing a literal `</script>` (they only work
inlined in HTML, not as standalone JS).

## Python lint (uv + ruff + pylint + mypy + pre-commit)

`pytest_web/` is the maintained Python (`src/*.py` are legacy Python 2
helpers, out of scope for every linter). Managed with `uv`
(`~/.local/bin/uv`; install: `curl -LsSf https://astral.sh/uv/install.sh | sh`):

```bash
uv sync                        # create .venv (deps from pyproject.toml)
uv run --frozen ruff check pytest_web
uv run --frozen ruff format --check pytest_web
uv run --frozen mypy pytest_web
uv run --frozen pylint pytest_web
```

Config lives in `pyproject.toml` (ruff select `E,F,W,I,N,UP,B,C4,SIM`,
line-length 100; lenient mypy baseline; pylint with pytest-fixture
`redefined-outer-name` disabled). Baseline is green.

Pre-commit (installed via `uv tool install pre-commit`; repo root is the
parent dir, so run from there):

```bash
cd ~/github
pre-commit run --config aredel/container/.pre-commit-config.yaml --files \
  aredel/container/pytest_web/test_sitemap.py
```

Hooks: ruff check, ruff format, mypy, pylint (all scoped to `pytest_web/`)
plus whitespace/yaml/toml hygiene on the Python configs. Note: brand-new
untracked files are invisible to bare `--all-files`; pass `--files` (as
above) until the first commit.

## Link checker + Allure

`pytest_web` GETs every URL from a sitemap against the web container:

```bash
docker compose run --rm pytest_web                         # checks src/sitemap.xml
SITEMAP_FILE=/data/sitemap_new.xml FAIL_ON_NON_200=false \
  docker compose run --rm -e SITEMAP_FILE -e FAIL_ON_NON_200 pytest_web
```

Env knobs: `BASE_URL` (default `http://php-apache`), `SITEMAP_FILE`
(`/data/sitemap.xml` or `/data/sitemap_new.xml`), `FAIL_ON_NON_200`
(`true` = non-200 cases fail, `false` = report-only).

Expected results: `sitemap.xml` (2021, 504 URLs) is ~172×200 / 332×404 —
it lists a retired tree (`/docker/`, `/code/…`, `/PC/…`) missing from this
checkout. `sitemap_new.xml` (regenerated from real files, ~3.4k URLs) is
~3418×200; the few non-200s are real behaviors (`401` on `http_basic.php`
auth pages, `500`s in `ao/blog/` etc., a broken demo `login.php` chain).

## Reading the Allure report

`allure-web` rebuilds the report ~10s after each checker run (hard-refresh
to see it):

```text
http://127.0.0.1:5050/allure-docker-service/projects/default/reports/latest/index.html
```

Where to click:

1. **`Suites`** (left sidebar) → `test_sitemap` → **one row per URL**
   (`0000 /1.php`, `0231 /qa/`, …), green = 200, red = anything else.
   Click a row for its `GET …` step and the `response` attachment
   (final URL, status, redirect count).
2. **Status filter** — the red **Failed** pill shows only the broken URLs.
3. **`Graphs`** — pie chart of passed vs failed (= 200 vs non-200 URLs).
4. **`test_summary`** (last case in Suites) → **Attachments**:
   `status-summary.txt` (200/404/other/error counts), `status-summary.json`,
   `all-urls.csv` (every URL + status, downloadable).

How it works under the hood: sitemap hosts are rewritten to the local
container, `X-Forwarded-Proto: https` emulates the production TLS proxy
(`.htaccess` under `ao/`, `ru/qa/` forces https otherwise), redirects are
followed pinned to the local host, and landing on `/404.php` counts as 404.

No-server fallback (same data, plain HTML table with filters):

```bash
./build-static-report.sh        # after a pytest_web run
# -> reports/sitemap-check-report.html (double-click to open)
# -> reports/all-urls.csv (spreadsheet)
```

## Sitemap generator

```bash
./generate-sitemap.sh [src_dir] [output.xml]   # default: src/sitemap_new.xml
BASE_URL=https://www.aredel.com ./generate-sitemap.sh
```

Scans `*.php`, skips framework/includes/backups (dot-/`_`-dirs pruned,
`arj*`, `*img`, `*RelatedArticles*`, `*_inc.php`, `*_arj.php`,
`*_module_*`, `* copy.php`), `lastmod` from file mtime, priorities
`/` → 1.00, `index.php` → 0.90, pages → 0.80. Same entry format as
`src/sitemap.xml`.

## Docs

- `CONTAINER.md` — full operations manual (build/run/stop/enter/debug,
  troubleshooting table, permanent permission fix).
- `FIX_PERMISSIONS.md` — the `700`-permission problem: symptoms, manual fix,
  framework symlinks (`src/.php`, `src/.css`), stale-container conflict.
- `fix-permissions.sh` — permission diagnose/fix + health check
  (`--check` = diagnose only).
