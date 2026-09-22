# Container operations manual (`php-apache`)

All commands run from this directory (`container/` — the one with
`docker-compose.yml`).

## What it is

| Item | Value |
|---|---|
| Service | `php-apache` |
| Container name | `php-apache` |
| Image | `php_engine:8.0` — built from `containerfiles/Dockerfile.php` (`php:8.0-apache` + mod_rewrite + `php-aredel.ini`) |
| Host port | `8080` → container port `80` |
| Code | `./src` bind-mounted to `/var/www/html` (live: host edits = instant, no rebuild) |
| PHP config | `./containerfiles/php-aredel.ini` mounted to `/usr/local/etc/php/conf.d/aredel.ini` (live) |
| Framework links | `src/.php -> ao/.php`, `src/.css -> ao/.css` (subpages `require $DOCROOT/.php/...`) |
| Restart policy | `unless-stopped` |

## Build

```bash
docker compose build          # build only
docker compose up -d --build  # build + (re)start
```

Builds are fast on purpose: `.dockerignore` excludes `src/` (2.8 GB / ~28k
files). The code is **not** copied into the image — it arrives via the bind
mount at runtime.

## Run / start / stop / restart

```bash
docker compose up -d          # start (creates container if missing)
docker compose ps             # status; NAME should be php-apache
docker compose stop           # stop (keeps container + data)
docker compose start          # start again after stop
docker compose restart        # restart
docker compose down           # stop + REMOVE container (code is safe: it lives in ./src)
docker compose down --volumes # ⚠ also deletes volumes (rarely needed here; code still safe)
```

Open in browser: `http://127.0.0.1:8080/` (`src/index.php`),
`http://127.0.0.1:8080/qa/`, `http://127.0.0.1:8080/phpinfo.php`.

## Enter the container

```bash
docker compose exec php-apache bash        # shell as root
docker compose exec --user www-data php-apache bash   # shell as Apache user
docker compose exec php-apache php -v      # one-off command
docker exec php-apache apachectl -S        # same via plain docker
```

Useful inside:

```bash
id www-data                                # should match host UID/GID (1000:1000, see below)
php -v
cat /usr/local/etc/php/conf.d/aredel.ini  # effective PHP overrides
ls -l /var/www/html/index.php             # shows HOST ownership/permissions
```

## Logs & debug

```bash
docker compose logs --tail=30              # apache access + error output
docker compose logs -f                     # follow
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/       # expect 200
curl -s http://127.0.0.1:8080/ | head -c 300                          # expect <!DOCTYPE html>
curl -s http://127.0.0.1:8080/qa/ | head -c 300                       # subpage incl. framework
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/.css/ao.css  # expect 200
ss -tlnp | grep 8080                       # is the port actually listened on?
```

What the output means:

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied` / `Failed opening required '...index.php'` | host file not readable by container user | fixed permanently via UID remap (see below); fallback `./fix-permissions.sh` |
| `Failed opening required '/var/www/html/.php/...'` on subpages | missing `src/.php` symlink | `ln -s ao/.php src/.php` (script does it) |
| `/.css/ao.css → 404`, unstyled page | missing `src/.css` symlink | `ln -s ao/.css src/.css` (script does it) |
| `Deprecated: ...` on top of pages | old framework on new PHP | `containerfiles/php-aredel.ini`, then `docker compose up -d` |
| `Conflict. The container name ... already in use` | orphan container from old run | `docker rm -f <old-name> && docker compose up -d` |
| `Bind for 0.0.0.0:8080 failed: port is already allocated` | something else on 8080 | `ss -tlnp \| grep 8080`, stop it or change host port |
| Blank page, `curl` hangs | container not running | `docker compose ps`, `docker compose up -d` |

Deeper checks:

```bash
stat -c '%a %U:%G %n' src/index.php        # want 644 (or 700 + UID remap, both work)
namei -l src/qa/index.php                 # every path component needs o+x (or owner match)
docker compose exec php-apache id www-data
docker compose config                     # render effective compose file
```

## Files

```text
docker-compose.yml              service, ports, mounts (incl. php-aredel.ini)
containerfiles/Dockerfile.php   image recipe (PHP 8.0, mod_rewrite, UID remap, php-aredel.ini)
containerfiles/php-aredel.ini   error_reporting without E_DEPRECATED
.dockerignore                   keeps src/ out of the build context (speed)
fix-permissions.sh              fallback permission fix + health check (--check = diagnose only)
FIX_PERMISSIONS.md              permission problem, full history
CONTAINER.md                    this file
pytest_web/                     link checker (Dockerfile, requirements, test_sitemap.py)
src/                            the website (bind-mounted, live)
src/sitemap_new.xml             regenerated sitemap (see generate-sitemap.sh)
```

## Link checker (pytest_web) + Allure report (allure-web)

`pytest_web` reads a sitemap and GETs every URL against `php-apache`:

```bash
docker compose run --rm pytest_web                        # sitemap.xml (default)
SITEMAP_FILE=/data/sitemap_new.xml FAIL_ON_NON_200=false \
  docker compose run --rm -e SITEMAP_FILE -e FAIL_ON_NON_200 pytest_web
```

`allure-web` (Allure Docker Service) watches the shared results volume and
serves the report:

- Swagger/API: `http://127.0.0.1:5050/`
- Latest HTML report: `http://127.0.0.1:5050/allure-docker-service/projects/default/reports/latest/index.html`
- Summary JSON: `.../reports/latest/widgets/summary.json`

Each run attaches to Allure: a `200 / 404 / other / error` summary table +
JSON, a full `all-urls` CSV, and one entry per non-200 URL.

Notes:

- The test sends `X-Forwarded-Proto: https` (emulates the production TLS
  proxy; `.htaccess` under `ao/`, `ru/qa/` forces https otherwise) and follows
  redirects pinned to the local host. A landing on `/404.php` counts as 404.
- `401` on `http_basic.php` pages is correct (they require auth), not a bug.
- `sitemap.xml` (2021, 504 URLs) scores ~172×200 / 332×404: it references a
  retired tree (`/docker/`, `/code/…`, `/PC/…`) missing from this checkout.
  `sitemap_new.xml` (generated from real files, 3439 URLs) scores ~3418×200.
- `FAIL_ON_NON_200=false` = report-only mode (exit 0).

## Permanent permission fix (no more manual chmod)

Host files are owned by UID/GID `1000` with mode `700` (owner-only). The bind
mount makes the container see **host** permissions, so no `RUN chmod` in the
Dockerfile could ever help — the mount shadows the image.

Instead the Dockerfile remaps Apache's user to the host owner:

```dockerfile
ARG HOST_UID=1000
ARG HOST_GID=1000
RUN groupmod -g ${HOST_GID} www-data && usermod -u ${HOST_UID} www-data
```

Now `www-data` **is** UID 1000 inside the container = the file owner → `700`
files and dirs are readable/traversable with zero host changes. New files
copied from the server (mode `700`) work immediately. Override per host via:

```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d --build
```

`fix-permissions.sh` / `FIX_PERMISSIONS.md` remain as fallback and history.
