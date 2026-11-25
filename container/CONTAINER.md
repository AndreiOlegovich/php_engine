# Container operations manual (`php-apache`)

All commands run from this directory (`container/`, the one with
`docker-compose.yml`). The names and ports come from `container/.env`:
`<project>` below is its `SITE_PROJECT`, `<port>` its `SITE_HTTP_PORT`.
Without that file they are `php_engine` and 8080.

## What it is

| Item | Value |
|---|---|
| Service | `php-apache` (the name Compose commands take) |
| Container name | `<project>-apache` (the name plain `docker` commands take) |
| Image | `<project>:8.5`, built from `containerfiles/Dockerfile.php` (`php:${PHP_VER}-apache` + mod_rewrite + UID remap + `php-engine.ini`); another version with `PHP_VER=7.4 docker compose up -d --build php-apache` |
| Host port | `<port>` → container port `80` |
| Code | `./src` bind-mounted to `/var/www/html` (live: host edits = instant, no rebuild) |
| PHP config | `./containerfiles/php-engine.ini` mounted to `/usr/local/etc/php/conf.d/php-engine.ini` (read when Apache starts) |
| Restart policy | `unless-stopped` |

## Build

```bash
docker compose build php-apache          # build only
docker compose up -d --build php-apache  # build + (re)start
```

The code is **not** copied into the image: it arrives through the bind mount
at runtime, and `.dockerignore` sends only `containerfiles/` and `pytest_web/`
to Docker, so the size of `src/` never slows a build.

## Run / start / stop / restart

```bash
docker compose up -d php-apache  # start (creates the container if missing)
docker compose ps                # status; NAME should be <project>-apache
docker compose stop              # stop (keeps container + data)
docker compose start             # start again after stop
docker compose restart php-apache  # restart, e.g. after editing php-engine.ini
docker compose down              # stop + REMOVE containers (code is safe: it lives in ./src)
docker compose down --volumes    # also deletes the Allure results volume (code still safe)
```

Open in a browser: `http://127.0.0.1:<port>/` (`src/index.php`).

## Enter the container

```bash
docker compose exec php-apache bash                   # shell as root
docker compose exec --user www-data php-apache bash   # shell as the Apache user
docker compose exec php-apache php -v                 # one-off command
docker exec <project>-apache apachectl -S             # the same via plain docker
```

Useful inside:

```bash
id www-data                                   # should match the host UID/GID (see below)
php -v
cat /usr/local/etc/php/conf.d/php-engine.ini  # effective PHP overrides
ls -l /var/www/html/index.php                 # shows HOST ownership/permissions
```

## Logs & debug

```bash
docker compose logs --tail=30 php-apache   # Apache access + error output
docker compose logs -f php-apache          # follow
docker compose logs php-apache 2>&1 | grep -E 'PHP (Parse|Fatal) error'   # broken pages
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<port>/   # expect 200
curl -s http://127.0.0.1:<port>/ | head -c 300                      # the start of the page
ss -tlnp | grep <port>                     # is the port actually listened on?
```

`display_errors` is on, so a page with a PHP error usually still answers
`200` and shows the error in the page: a `200` is not proof that a page works.

What the output means:

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied` / `Failed opening required '...index.php'` | host file not readable by the container user | fixed via the UID remap (see below); fallback `tools/fix/fix-permissions.sh` |
| `Parse error` or `Fatal error` on a page that answers 200 | code that PHP 8.5 no longer accepts | `tools/linters/lint-php.sh --all` for syntax; the error log for removed functions |
| no `Deprecated:` notices anywhere | `containerfiles/php-engine.ini` leaves them out | set `error_reporting = E_ALL` there, `docker compose restart php-apache` |
| `Conflict. The container name "/<project>-apache" is already in use` | a container left from an earlier run | `docker rm -f <project>-apache && docker compose up -d php-apache` |
| `Bind for 0.0.0.0:<port> failed: port is already allocated` | another project or program uses the port | `ss -tlnp \| grep <port>`; or set a free `SITE_HTTP_PORT` in `container/.env` (and `SITE_LOCAL_URL` in `container/site-profile.env`) |
| Blank page, `curl` hangs | container not running | `docker compose ps`, `docker compose up -d php-apache` |

Deeper checks:

```bash
stat -c '%a %U:%G %n' src/index.php        # want 644 (or 700 + UID remap, both work)
namei -l src/index.php                     # every path component needs o+x (or owner match)
docker compose exec php-apache id www-data
docker compose config                      # render the effective compose file
```

## Files

```text
docker-compose.yml               services, ports, mounts (incl. php-engine.ini)
.env                             this project's name and ports (new-project.sh; ignored by git)
.dockerignore                    keeps everything but containerfiles/ and pytest_web/ out of builds
containerfiles/Dockerfile.php    image recipe (PHP_VER, default 8.5; mod_rewrite, UID remap, php-engine.ini)
containerfiles/php-engine.ini    error_reporting without E_DEPRECATED
tools/fix/fix-permissions.sh     fallback permission fix + health check (--check = diagnose only)
tools/fix/FIX_PERMISSIONS.md     the permission problem, in full
CONTAINER.md                     this file
pytest_web/                      link checker (Dockerfile, requirements, test_sitemap.py)
src/                             the website (bind-mounted, live)
src/sitemap.xml                  the sitemap (tools/generate-sitemap.sh writes it)
```

## Link checker (pytest_web) + Allure report (allure-web)

`pytest_web` reads a sitemap and GETs every URL against `php-apache`:

```bash
docker compose up -d allure-web                           # the report viewer
docker compose run --rm pytest_web                        # src/sitemap.xml (default)
SITEMAP_FILE=/data/sitemap_new.xml FAIL_ON_NON_200=false \
  docker compose run --rm -e SITEMAP_FILE -e FAIL_ON_NON_200 pytest_web
```

`allure-web` (Allure Docker Service) watches the shared results volume and
serves the report on `SITE_ALLURE_PORT` from `container/.env`:

- Swagger/API: `http://127.0.0.1:<allure-port>/`
- Latest HTML report: `http://127.0.0.1:<allure-port>/allure-docker-service/projects/default/reports/latest/index.html`
- Summary JSON: `.../reports/latest/widgets/summary.json`

Each run attaches to Allure: a `200 / 404 / other / error` summary table +
JSON, a full `all-urls` CSV, and one entry per non-200 URL.

Notes:

- The checker sends `X-Forwarded-Proto: https` (as a hosting TLS proxy does;
  a site's `.htaccess` may force https otherwise) and follows redirects
  pinned to the local host. A landing on `/404.php` counts as 404.
- A `401` on a page behind HTTP authentication is correct, not a bug.
- `FAIL_ON_NON_200=false` = report-only mode (exit 0).

## Permanent permission fix (no more manual chmod)

Files copied from a server often keep mode `700` (owner only). The bind
mount makes the container see **host** permissions, so no `RUN chmod` in the
Dockerfile could ever help: the mount shadows the image.

Instead the Dockerfile remaps Apache's user to the host owner:

```dockerfile
ARG HOST_UID=1000
ARG HOST_GID=1000
RUN groupmod -g ${HOST_GID} www-data && usermod -u ${HOST_UID} www-data
```

Now `www-data` **is** the file owner inside the container, so `700` files and
folders are readable with zero host changes. A host user other than
1000:1000 builds with:

```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d --build php-apache
```

`tools/fix/fix-permissions.sh` and `tools/fix/FIX_PERMISSIONS.md` remain as
fallback and history.
