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
