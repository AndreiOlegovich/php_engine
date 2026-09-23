# Fixing file permissions so Apache in Docker can serve `index.php`

## Symptoms

Browser at `http://127.0.0.1:8080/` shows blank page / error instead of homepage.
`curl` shows:

```html
<br />
<b>Warning</b>:  Unknown: Failed to open stream: Permission denied in <b>Unknown</b> on line <b>0</b><br />
<br />
<b>Fatal error</b>:  Failed opening required '/var/www/html/index.php' ...
```

Meanwhile `http://127.0.0.1:8080/phpinfo.php` works. That proves Apache itself
is running — only some files are unreadable.

Other possible symptom for static files / directories:

```text
Forbidden — You don't have permission to access this resource.
```

Apache error log (`docker compose logs`) may show `Permission denied` or
`(13)Permission denied: [client ...] AH00035: access to / denied`.

## Root cause

1. `docker-compose.yml` bind-mounts host code into the container:

   ```yaml
   volumes:
     - ./src:/var/www/html
   ```

   This **overrides** everything `COPY` + `RUN chmod` did in
   `containerfiles/Dockerfile.php`. At runtime the container sees the
   **host permissions** verbatim.

2. Inside the container Apache/PHP runs as `www-data` (`uid=33, gid=33`):

   ```bash
   docker exec php-apache id www-data
   # uid=33(www-data) gid=33(www-data)
   ```

3. On the host most of `src/` is mode `700` / `drwx------`, owned by
   `andrei:andrei`:

   ```bash
   ls -l src/index.php
   # -rwx------ 1 andrei andrei ... src/index.php

   stat -c '%a %U:%G %n' src/index.php
   # 700 andrei:andrei src/index.php

   find src -type f ! -perm -o+r | wc -l   # ~28000 files
   find src -type d ! -perm -o+rx | wc -l  # ~1500 dirs
   ```

   `700` = `rwx------` = only owner can read/traverse. `www-data` (uid 33)
   is "other", so it gets `---` → `Permission denied`.

   `src/phpinfo.php` happened to be `644`, which is why it worked.

4. Directories need the execute (`x`) bit to be traversable. A file can be
   `644` but still unreachable if any parent dir is `700`.

## Required permissions

Standard for Apache + PHP with a bind mount:

| Type      | Mode | Meaning                              | Command |
|-----------|------|--------------------------------------|---------|
| Directory | 755  | `rwxr-xr-x` — everyone can traverse  | `find src -type d -exec chmod 755 {} +` |
| File      | 644  | `rw-r--r--` — everyone can read      | `find src -type f -exec chmod 644 {} +` |

Do **not** `chown` host files to `www-data` — you would lose ownership as
`andrei` and break local editing/git. `chmod o+rX` is enough.

Do **not** use `777` — it works but is insecure and unnecessary.

Shell/Python helper scripts that you run locally can keep `755` if you want
to execute them (`./script.sh`). PHP served by Apache only needs `644`.

## Manual fix (step by step)

From the `container/` directory (the one containing `docker-compose.yml`):

```bash
# 1. Confirm the problem
stat -c '%a %U:%G %n' src/index.php
# bad:  700 andrei:andrei src/index.php
# good: 644 andrei:andrei src/index.php

docker exec php-apache ls -l /var/www/html/index.php
curl -s http://127.0.0.1:8080/ | head

# 2. Fix directories (traverse + list)
find src -type d -exec chmod 755 {} +

# 3. Fix files (read for Apache, keep ownership as you)
find src -type f -exec chmod 644 {} +

# 4. Optional: keep local helper scripts executable
# find src -type f -name '*.sh' -exec chmod 755 {} +

# 5. Restart / verify (no rebuild needed — bind mount is live)
docker compose up -d
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
curl -s http://127.0.0.1:8080/ | head
docker compose logs --tail=20
```

No `docker compose build` is needed because the volume is live. Rebuilding
alone does **not** fix it — the `RUN chmod` in the Dockerfile is shadowed by
the mount.

## Automated fix

```bash
chmod +x fix-permissions.sh
./fix-permissions.sh
# or: ./fix-permissions.sh --check   # only diagnose, change nothing
```

The script does steps 1–5 above, plus the framework-symlink check from
"Subpages still fail" below, counts broken files before/after, and
curl-checks `/`, `/qa/` and `/phpinfo.php`.

## Verify it worked

```bash
stat -c '%a %n' src/index.php
# 644 src/index.php

curl -s http://127.0.0.1:8080/ | head
# should start with <!DOCTYPE html><html lang="en">, NOT "Permission denied"

curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
# 200
```

Also open in browser:

- `http://127.0.0.1:8080/` (`src/index.php`)
- `http://127.0.0.1:8080/phpinfo.php`
- any subpage, e.g. `http://127.0.0.1:8080/qa/`

## If it still fails

1. Parent dirs: `namei -l src/index.php` — every component needs `o+x`.
2. Fresh container: `docker compose up -d && docker compose logs --tail=30`.
3. SELinux/AppArmor (rare on Ubuntu): `dmesg | tail` for `denied` lines.
4. Still `403` on one file only: `stat -c '%a %U:%G' src/path/to/file` and fix it individually.

## Subpages still fail (`/.php/... not found`) — the framework symlinks

Symptom after permissions are `644`/`755`: homepage `/` works, but every
subpage (`/qa/`, `/postgres/`, `/dvps/docker/`, ...) returns:

```html
<b>Fatal error</b>:  Failed opening required '/var/www/html/.php/Page.php' ...
```

and the homepage ends with:

```html
<b>Warning</b>:  include(/var/www/html/.php/php_includes/_footer.php): Failed to open stream ...
```

plus `/.css/ao.css` returns `404` (page unstyled).

Cause: all PHP pages do `require_once($root.'/.php/...')` and link
`href="/.css/ao.css"`, but this checkout has no top-level `src/.php` or
`src/.css` — the only copies live under `src/ao/.php` and `src/ao/.css`
(the `/ao/` subsection ships its own framework copy; the root copy was
never checked out / was hidden as dotfiles).

Fix (live immediately, no rebuild — bind mount):

```bash
ln -s ao/.php src/.php
ln -s ao/.css src/.css
ls -la src/ | grep -E '\.php|\.css'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/.css/ao.css  # 200
curl -s http://127.0.0.1:8080/qa/ | head -c 300                             # <!DOCTYPE..., no Fatal error
```

`fix-permissions.sh` does this automatically.

## Pages render but with `Deprecated` notices on top (PHP 8.3)

Symptom: subpages show blocks like `Deprecated: Creation of dynamic
property SxGeo::$b_idx_len ...` or `Optional parameter $type declared
before required parameter ...`. The old framework was written for PHP 5/7.

Fix: `containerfiles/php-aredel.ini` sets

```ini
error_reporting = E_ALL & ~E_DEPRECATED
display_errors = On
log_errors = On
```

It is live-mounted in `docker-compose.yml`:

```yaml
- ./containerfiles/php-aredel.ini:/usr/local/etc/php/conf.d/aredel.ini:ro
```

(and also `COPY`d in `containerfiles/Dockerfile.php` for fresh builds).
After changing it: `docker compose up -d` (recreate, no `--build` needed).

## Stale `dff1e5470f76_php-apache` container blocks `docker compose up`

Symptom: `docker compose up -d` fails with

```text
Conflict. The container name "/dff1e5470f76_php-apache" is already in use ...
```

That was an orphan container from an old compose run shadowing the proper
`container_name: php-apache`. Fix once:

```bash
docker rm -f dff1e5470f76_php-apache
docker compose up -d
docker compose ps   # NAME should be php-apache
```

## Prevention

- Keep new files at default umask `022` (dirs `755`, files `644`).
- Don't `chmod 700 -R src` or copy from a `~/.ssh`-style location.
- Remember: with a bind mount, host `chmod` is container `chmod`.

## PHP version (pinned to 8.0)

`containerfiles/Dockerfile.php` uses `FROM php:8.0-apache` — the baseline we
make the site work on first. `containerfiles/php-aredel.ini` additionally
hides `E_DEPRECATED` in browser output. To change version later, edit the
`FROM` line and run `docker compose up -d --build`.

Note: the Dockerfile intentionally does **not** `COPY src/` — the code comes
from the `./src:/var/www/html` bind mount. Copying it made the build context
2.8 GB / ~28k files and stalled builds, and the mount shadowed it at runtime
anyway. `.dockerignore` also excludes `src/` to keep builds fast.
