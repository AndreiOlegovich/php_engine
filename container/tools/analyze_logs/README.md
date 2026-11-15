# Prod log pipeline (analyze_logs)

Prod Apache logs (example.com) flow: FTP → external log dir → bot list →
error report + visits database → Backoffice Metrika tab. All scripts run
from this directory; all paths below are relative to it unless noted.

## Log location

Downloaded logs and device profiles live outside the repository:

```
${SITE_LOG_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/php_engine/logs}/
  prod/access/  prod/error/  dev/  devices/  user_profiles/  botlist.csv
```

Override with `SITE_LOG_DIR` (absolute path); every script and the
backoffice use the same resolver. `metrika.db`, `users.db` and
`crawlers.json` stay beside the scripts. `botlist.csv` lives in the log root
(since 2026-10-23): it holds client IP addresses, user agents and paths
from the prod logs, so it follows the logs out of the repository and under
their deny rules.

The old in-repo `site_logs/` tree is no longer read. It was moved once with:

```bash
mkdir -p ~/.local/share/php_engine
mv container/tools/analyze_logs/site_logs ~/.local/share/php_engine/logs
```

## Chain

```
cron (every 30 min) → refresh-metrika.sh   (one run at a time, see below)
  → fetch-logs.sh          prod error + access logs, skipped quietly offline
  → fetch-devices.sh       owner device profiles from dev (best effort)
  → fetch-user-profiles.sh prod profile records staged on dev (best effort)
  → build_users_db.py      rebuild users.db (devices + exclude flags)
  → check_bots.py          rebuild botlist.csv (1867 bot IPs)
  → build_metrika_db.py    rebuild metrika.db (bots, owner devices + static excluded)
  → Backoffice → Metrika tab reads metrika.db via GET /api/metrika/summary
```

`refresh-metrika.sh` holds `$SITE_LOG_DIR/refresh-metrika.lock` for the
whole chain; a run that finds it taken exits 0 without doing anything.
`users.db`, `botlist.csv` and `metrika.db` are each built under a temporary
name beside the target and renamed over it, so the backoffice and the
other scripts read the old or the new file, never a half-written one.

Cron line (not installed by default — install it when ready):

```
*/30 * * * * /path/to/checkout/container/tools/analyze_logs/refresh-metrika.sh >> ~/.local/share/php_engine/logs/refresh-metrika.cron.log 2>&1
```

## Scripts

- `fetch-logs.sh [--creds PATH] [--dest DIR] [--dry-run] [-v]` —
  flock-guarded wrapper around `download-logs.sh --only prod --latest 2`.
  TCP-checks the FTP host first (creds stay in
  `~/.config/php_engine/ftp-creds.env`, never logged); exit 0 when offline.
- `refresh-metrika.sh [--skip-fetch] [--skip-devices] [--skip-users]
  [--skip-bots] [--skip-db] [--from-local] [--dry-run-fetch] [-v]` — the
  cron entrypoint; failures stop the chain. `--from-local` means
  `--skip-fetch --skip-devices`, i.e. rebuild from already-downloaded logs
  without contacting FTP.
- `check_bots.py [--log-dir DIR] [--output FILE] [--min-probes N]` —
  aggregates all prod access logs per IP and lists high-confidence bots:
  known-bot UA on ≥50% of hits (≥10 total), or ≥10 404s at ≥50% 404-rate,
  or ≥10 probe-path hits, or ≥50 404s, or any single day with ≥150 hits,
  ≥100 distinct paths, ≥100 hits in one hour, or ≥6 distinct user agents
  (≥50 hits). The day-level signals catch polite full-site crawlers that
  never 404. One request with a bot signature also lists the IP: a card
  image name glued onto a page path (`/section/it/gulp_card`) or a Referer
  holding two URLs (`https://google.com, https://…`); the scraper behind
  them uses about one request per IP, below every volume rule. Rebuild is
  total, so `first_seen` survives; output columns
  match the historic `botlist.csv` exactly.
- `analyze_errors.py` — existing error analyzer, plus `--botlist FILE`
  (defaults to `botlist.csv` in the log root): hits from listed client IPs are
  reported as `bot-filtered`, not real errors. Default log dir is
  `${SITE_LOG_DIR}/prod/error` (an old default pointed at a nonexistent
  layout and silently read stale files).
- `list_prefix_404.py LOG [LOG ...] [--prefix PATH ...] [--php-only | --all]
  [--lines | --paths-only] [--sort count|time|path]` — lists missing pages
  under `/qa /code /db /windows /dvps /linux`. It recognizes the `302` plus
  328/332-byte-body marker that `ErrorDocument 404 /404.php` writes, groups
  hits by URL with count and first/last seen, and skips static assets unless
  `--all` is given. Plain and `.gz` logs are accepted.
- `build_metrika_db.py [--log-dir DIR] [--db FILE] [--botlist FILE]
  [--users-db FILE] [--crawlers FILE] [--top N]` — counts status-200 page views per page and day, excluding
  static assets (css/js/images/fonts/...) by extension, bot UAs,
  botlist IPs, and the owner: hits whose (IP, user agent) pair is a device
  flagged `exclude_from_metrika` in `users.db`. Query strings are stripped. Also writes the `crawlers`
  reference table (one row per `crawlers.json` entry) and dual
  `visitors/hits` + `visitors_nocrawler/hits_nocrawler` columns: the latter
  additionally drop hits whose UA matches the crawlers.json patterns
  (see `crawlers.md`). Served by `GET /api/metrika/summary` with
  `?exclude_crawlers=true`; the Metrika tab has an "Exclude crawlers"
  toggle button for it.
- `download-logs.sh` — fetch-all/prod/dev downloader; skips a local copy
  that is the same size as or larger than the remote file and replaces a
  smaller (incomplete) local copy. `--force` re-downloads everything.
  `--dest` defaults to `$SITE_LOG_DIR` (the external tree above).
  `list_404.py` and `find_broken_hrefs.py` are the unchanged 404 utilities;
  `botlist.csv` had no maintaining script before `check_bots.py`.

## Data files (external log dir, or beside the scripts)

- `$SITE_LOG_DIR/prod/` — downloaded prod logs (`error/`, `access/`).
- `$SITE_LOG_DIR/devices/devices.jsonl` — owner device profiles pulled
  from dev by `fetch-devices.sh` (see below). This is the canonical list the
  future Metrika self-exclusion reads.
- `$SITE_LOG_DIR/user_profiles/` — staged profile logs pulled from dev.
- `$SITE_LOG_DIR/refresh-metrika.cron.log` — created by cron.
- `metrika.db`, `users.db` — live beside the scripts (git-ignored via `*.db`).
- `$SITE_LOG_DIR/botlist.csv` — regenerated by `check_bots.py` on every
  run; not tracked in git.

## Owner device profiles (/services/user_profile/)

- Page `container/src/services/user_profile/index.php` records one JSON line per
  visit: IP, forwarded-for, user agent, language, headers (Cookie and
  Authorization redacted), timestamp, and a `device_id` fingerprint.
- Password gate, fail closed: only visits with the right `?key=` land in
  `data/devices.jsonl` (owner list); keyless visits land in
  `data/visitors.jsonl` (public log, never the owner list). Key sources in
  order: env `USER_PROFILE_KEY`, file `USER_PROFILE_KEY_FILE`, then
  `$HOME/.config/php_engine/user-profile.key` (chmod 600, outside the repo).
  Make one with `openssl rand -hex 32`.
- Public save form: visitors without the key see a thank-you page with a
  "Save your user profile" password + text-captcha form. A correct password
  (checked against `USER_PROFILE_SAVING_PASSWORD` from hosting env or `site.env`,
  never echoed back) plus a correct captcha one-time answer stores the
  profile in `data/password_profiles.jsonl`, which `build_users_db.py`
  treats as ownership-proved (excluded from Metrika by default, like keyed
  devices). Password sources in order (see `services/user_profile/_password.php`):
  env `USER_PROFILE_SAVING_PASSWORD`, file `USER_PROFILE_SAVING_PASSWORD_FILE`,
  then the `USER_PROFILE_SAVING_PASSWORD=` line in the `SITE_ENV` file, then a
  hardcoded fallback used only when `site.env` is missing (change it to your
  own value — the file is committed). Put the real value in `site.env` on the server;
  only the documented variable name ships in `site.env.example`.
- `fetch-devices.sh [--creds PATH] [--dest DIR] [--remote PATH]
  [--dry-run] [-v]` pulls dev's `services/user_profile/data/devices.jsonl` over FTP
  and merges new lines into `$SITE_LOG_DIR/devices/devices.jsonl` (exact-line
  dedupe, idempotent). Runs best-effort inside `refresh-metrika.sh`.
- `fetch-user-profiles.sh` pulls the three staged profile logs
  (`devices`, `visitors`, `password_profiles`) from `user_profiles/` at the FTP
  root (which is `dev.example.com/user_profiles/` on the server)
  into `$SITE_LOG_DIR/user_profiles/` the same way (same creds, `--dry-run`,
  offline-tolerant). `build_users_db.py` reads them as the `dev-profiles-*`
  sources.
- Never deployed, never committed: `container/src/services/user_profile/data/` is
  git-ignored and excluded in `deploy-ftp.sh` (`EXCLUDE_PATH_PREFIXES`),
  so hosting keeps its copy and local stays local.

## metrika.db schema

```sql
visits_daily(page TEXT, day TEXT, visitors INT, hits INT, visitors_nocrawler INT, hits_nocrawler INT, PK(page, day));
site_daily(day TEXT PK, visitors INT, hits INT, visitors_nocrawler INT, hits_nocrawler INT);  -- site-wide uniques
crawlers(pattern TEXT PK, description TEXT, url TEXT, source TEXT);  -- crawlers.json reference, see crawlers.md
meta(key TEXT PK, value TEXT);  -- built_at, min_day, max_day, crawler_patterns, owner_devices
```

`visitors` = distinct client IPs per page and day, `hits` = page views.
Bots, excluded owner devices and static assets never enter the tables. The `_nocrawler`
columns additionally exclude self-identifying crawlers (crawlers.json UA
match); the API serves them when `?exclude_crawlers=true`.

## users.db schema

```sql
users(device_id PK, ip, user_agent, accept_language,
      first_seen, last_seen, visits, sources, exclude_from_metrika);
```

One row per unique device from all three profile sources (local keyed,
local visitors, downloaded dev copy). Rebuilds preserve explicitly set
flags; otherwise keyed devices default to excluded, visitors-only devices
to included. Curate with `build_users_db.py --list | --exclude ID |
--include ID`. `build_metrika_db.py` leaves out the hits of every excluded
device. The profile page derives `device_id` from IP + user agent +
Accept-Language, so a row stands for one browser on one network: open
`/services/user_profile/` once from each new network (home, mobile, office)
to exclude it. The match is on the exact (IP, user agent) pair, not the IP
alone, so other people behind a shared mobile-carrier IP stay counted.

## Backoffice wiring

- `GET /api/metrika/summary?days=N` (N clamped to 1–366) returns the last
  N days present in the DB, top 150 pages with per-day visitors/hits, and
  the resolved external `log_dir`. 409 when the DB is missing;
  `BACKOFFICE_METRIKA_DB` overrides the database path (used by tests).
  Covered by `test_metrika_*` in `backoffice-container/tests/test_api.py`.
- `POST /api/actions/rebuild-metrika-db/run` (allowlisted action,
  `refresh-metrika.sh --from-local`) rebuilds users/bots/metrika from
  already-downloaded logs without FTP; no confirmation phrase.
- `POST /api/actions/download-production-logs/run` downloads new prod logs
  into the external tree; the UI confirmation modal offers a typed phrase
  or the "Quick confirm (no typing)" radio option.
- UI `Metrika` tab (`App.jsx` + `style.css`): a tinted **Logs & rebuild**
  bar with primary **Download logs** and outlined **Rebuild from local
  logs** buttons, kept visually distinct from the date-range toggles.
  The summary reloads automatically after a successful rebuild. The
  `dist/` bundle rebuilds in Docker at `start.sh` time (workstation Node
  is too old to build).

## Verify

```bash
./refresh-metrika.sh --skip-fetch          # bots + db from local logs
python3 analyze_errors.py --top 5          # bot-filtered header on top
# backoffice API tests (from repo root):
backoffice-container/.venv/bin/python -m pytest backoffice-container/tests/ -q
```
