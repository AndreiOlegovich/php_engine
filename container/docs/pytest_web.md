# pytest_web — link checker manual

`pytest_web` GETs every `<loc>` URL from a sitemap against the local
`php-apache` container: one pytest case per URL (200 = pass, anything
else = fail), plus a trailing `test_summary` case with overall counts
and a full CSV.

Test source: `pytest_web/test_sitemap.py`.
Compose wiring: `docker-compose.yml` services `pytest_web` (one-shot)
and `allure-web` (report viewer).

## Prerequisites

```bash
docker compose up -d            # php-apache (:8080) + allure-web (:5050)
docker compose ps               # both should be Up
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/  # expect 200
```

Sitemaps live in `./src` (mounted into the checker as `/data:ro`):

| Host path | In-container path | Content |
|---|---|---|
| `src/sitemap.xml` | `/data/sitemap.xml` | regenerated from real files, 3439 URLs (**default**) |
| `src/sitemap_old.xml` | `/data/sitemap_old.xml` | original from 2021, 504 URLs |

## Triggering a run

All commands run from `container/` (the directory with
`docker-compose.yml`).

```bash
# Default: check src/sitemap.xml, non-200 URLs fail the run
docker compose run --rm pytest_web

# Check the old 2021 sitemap instead
SITEMAP_FILE=/data/sitemap_old.xml \
  docker compose run --rm -e SITEMAP_FILE pytest_web

# Report-only mode: always exits 0, non-200s just show red in Allure
SITEMAP_FILE=/data/sitemap.xml FAIL_ON_NON_200=false \
  docker compose run --rm -e SITEMAP_FILE -e FAIL_ON_NON_200 pytest_web
```

### Env knobs

| Var | Default | Meaning |
|---|---|---|
| `SITEMAP_FILE` | `/data/sitemap.xml` | sitemap to check; any `*.xml` under `./src` works as `/data/<name>` |
| `FAIL_ON_NON_200` | `"true"` | `"true"` = non-200 URL cases fail; `"false"` = report-only, exit 0 |
| `BASE_URL` | `http://php-apache` | checked host; leave as-is inside compose |

To check a freshly regenerated sitemap, rebuild it first (see
`generate-sitemap.sh`), then point `SITEMAP_FILE` at the output:

```bash
./generate-sitemap.sh                    # default: src/sitemap_new.xml
SITEMAP_FILE=/data/sitemap_new.xml \
  docker compose run --rm -e SITEMAP_FILE pytest_web
```

### How it works under the hood

- Each sitemap host (e.g. `https://www.aredel.com/qa/`) is rewritten to
  `BASE_URL`, keeping path/query — so production URLs are checked
  against the local container.
- The checker sends `X-Forwarded-Proto: https` (emulates the production
  TLS proxy; `.htaccess` under `ao/`, `ru/qa/` would otherwise force a
  redirect to https) and follows redirects pinned to the local host.
- Landing on `/404.php` counts as 404 (`.htaccess` `ErrorDocument 404`
  serves it with a 200 status, so the path is the signal).
- `401` on `http_basic.php` demo pages is correct (they require auth),
  not a bug.

## Reading the report

### Option A — Allure (needs the stack running)

Latest HTML report (rebuilds ~5–10 s after each run, hard-refresh to
see it):

```text
http://127.0.0.1:5050/allure-docker-service/projects/default/reports/latest/index.html
```

Swagger/API root: `http://127.0.0.1:5050/`

Where to click:

1. **`Suites`** (left sidebar) → `test_sitemap` → **one row per URL**
   (`0000 /1.php`, `0231 /qa/`, …), green = 200, red = anything else.
   Click a row for its `GET …` step and the `response` attachment
   (final URL, status, redirect count).
2. **Status filter** — the red **Failed** pill shows only the broken URLs.
3. **`Graphs`** — pie chart of passed vs failed (= 200 vs non-200 URLs).
4. **`test_summary`** (last case in Suites) → **Attachments**:
   `status-summary.txt` (200/404/other/error counts),
   `status-summary` JSON, `all-urls` CSV (every URL + status,
   downloadable), `sitemap-source` (which sitemap → which base URL).

### Option B — static HTML (no server needed)

After a `pytest_web` run:

```bash
./build-static-report.sh
# -> reports/sitemap-check-report.html (double-click to open)
# -> reports/all-urls.csv (spreadsheet)
```

The HTML page shows total / 200 / 404 / other / error cards, filter
buttons (`All`, `200`, `404`, `other`, `error`), a free-text URL filter,
and one table row per URL (`#`, sitemap URL, checked URL, status,
redirects).

## Expected results

- `sitemap.xml` (3439 URLs): nearly all 200; the few non-200s are real
  behaviors (`401` on `http_basic.php` auth pages, `500`s in `ao/blog/`
  etc., a broken demo `login.php` chain).
- `sitemap_old.xml` (504 URLs, 2021): ~172×200 / 332×404 — it lists a
  retired tree (`/docker/`, `/code/…`, `/PC/…`) missing from this
  checkout, so most 404s are expected.

Bucket meanings in `test_summary` / static report: `200` = OK,
`404` = missing (incl. `/404.php` landings), `other` = non-200/404 HTTP
status (e.g. 401, 500), `error` = request never completed
(timeout/connection error).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ERROR: no results yet, run pytest_web first.` from `build-static-report.sh` | no Allure results in the `allure-results` volume yet — run `docker compose run --rm pytest_web` first |
| `no <loc> entries found in ...` | wrong `SITEMAP_FILE` path or not valid sitemap XML; path must exist under `./src` as `/data/<name>` |
| Allure page shows a stale run | wait ~10 s and hard-refresh (`Ctrl+Shift+R`); `allure-web` polls every 5 s (`CHECK_RESULTS_EVERY_SECONDS`) |
| All URLs `error` / connection failures | `php-apache` not running — `docker compose up -d`, then `curl http://127.0.0.1:8080/` |
| Mass redirects / unexpected non-200s | `X-Forwarded-Proto` handling or `.htaccess` https rules; check `docker compose logs --tail=30` on `php-apache` |
