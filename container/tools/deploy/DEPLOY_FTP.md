# FTP deploy manual (`deploy-ftp.sh`)

Uploads the site (`src/`) to virtual hosting over FTP/FTPS. All commands run
from this directory (`tools/deploy/` — the script operates on the
project root two levels up, so `src/`, `docker-compose.yml` etc. resolve).

## What it does

| Item | Value |
|---|---|
| Script | `deploy-ftp.sh` (bash + embedded Python `ftplib`, no extra packages) |
| Source | `./src` (override: `--src DIR`) |
| Target | `FTP_REMOTE_DIR` from the creds file (override: `--remote-dir DIR`) |
| Transfer | binary, passive mode; files with identical size are skipped (override: `--force`), except files known to have changed — named with `--files` or picked by `--staged`/`--last-commit`/`--commits` — which are always uploaded |
| Symlinks | resolved — `src/.php` arrives as a real dir (hosting has no symlinks); `src/.css` is already a real dir |
| Images | uploaded (hosting needs them, unlike git where they are ignored); skip with `--no-images` |
| Skipped junk | `*.log`, `*.db`/`*.sqlite*`, `sess_*`, editor backups, `reports/`, caches |

## 1. One-time setup (credentials)

Credentials NEVER live in this repo. Default location (outside the repo):

```
~/.config/<project>/hosting-access.env
```

```bash
mkdir -p ~/.config/<project>
cp hosting-access.env.template ~/.config/<project>/hosting-access.env
chmod 600 ~/.config/<project>/hosting-access.env
$EDITOR ~/.config/<project>/hosting-access.env
```

Fill in:

```bash
FTP_HOST="ftp.example.com"
FTP_USER="hosting_user"
FTP_PASSWORD="secret-here"
FTP_PORT="21"            # optional, default 21
FTP_REMOTE_DIR="/public_html"  # optional, default /
FTP_USE_TLS="false"      # "true" for explicit FTPS
FTP_TIMEOUT="30"         # optional, seconds
```

Optional shared defaults: export `SITE_ENV` pointing at a `site.env`
file (see `site.env.example`; site values live in `site.env`).
Sourced before the secret creds file, so CLI flags and the creds file
always win; only non-secret values belong there (`FTP_PASSWORD` set in
`SITE_ENV` prints a warning):

```bash
SITE_ENV=container/tools/deploy/site.env ./deploy-ftp.sh --dry-run
```

A creds file under `container/` (or anywhere inside the repo) is refused —
keep it outside so it can never be committed. Any path can be used via
`--creds PATH`.

## 2. Usage

```bash
./deploy-ftp.sh --help                              # full help
./deploy-ftp.sh --dry-run                           # plan only, no connection
./deploy-ftp.sh                                     # upload with default creds
./deploy-ftp.sh -v                                  # per-file progress + status
./deploy-ftp.sh --creds /path/to/creds.env          # custom creds location
./deploy-ftp.sh --remote-dir /public_html/test      # staging dir on hosting
./deploy-ftp.sh --tls | --no-tls                    # force FTPS on/off
./deploy-ftp.sh --delete                            # also delete remote-only files (off by default)
./deploy-ftp.sh --delete-removed 1 --dry-run        # list what the last commit removed; drop --dry-run to delete it
./deploy-ftp.sh --force --files qa/index.php       # re-upload even if same size on server
```

### Partial uploads (selection)

Default is the whole tree. `--files`, `--dirs`, `--committed`, `--staged`,
`--last-commit`, `--commits N` combine as a union; `--dry-run` previews
the selection. `--delete` is refused with any selection (it would wipe
everything not selected).

```bash
./deploy-ftp.sh --files qa/index.php section/article.php --dry-run
./deploy-ftp.sh --files "qa/index.php,section/article.php"   # comma form
./deploy-ftp.sh --files qa/ --dry-run      # a whole subdir under src/
./deploy-ftp.sh --dirs qa section --dry-run  # selected dirs, normal junk filters retained
./deploy-ftp.sh --files src/qa/index.php   # src/-prefixed also accepted
./deploy-ftp.sh --staged --dry-run         # staged changes only (git diff --cached)
./deploy-ftp.sh --staged                   # upload staged changes
./deploy-ftp.sh --committed --dry-run      # git-tracked files only (git ls-files)
./deploy-ftp.sh --last-commit --dry-run    # files changed in the last commit (git diff-tree HEAD)
./deploy-ftp.sh --last-commit              # upload files from the last commit
./deploy-ftp.sh --commits 3 --dry-run      # net change over the last 3 commits (git diff HEAD~3..HEAD)
./deploy-ftp.sh --commits 9                # upload files from the last 9 commits
```

Notes:

- Explicit `--files` entries bypass the junk excludes; `--committed` /
  `--staged` / `--last-commit` / `--commits` still skip junk (`*.log`,
  `*.db`, `sess_*`, …).
- `--dirs` selects whole directory trees without bypassing junk exclusions;
  symlinked directories/files and copy-like folders are not followed by
  this safe selection mode. This is the mode used by the backoffice directory
  picker.
- `--committed` uploads every **git-tracked** file under `src/`
  (`git ls-files`): a full mirror, skipping server-identical sizes —
  "nothing uncommitted" is normal, not a problem.
- `--staged` uploads only staged changes (`git diff --cached`);
  `--last-commit` only files changed in `HEAD` — use these for
  "just my change" instead of the whole tracked tree.
- `--committed` + `--files` combine as a union (handy for the tracked
  tree plus a few untracked files, which git selections never include).
- `--delete` cannot combine with any selection (it would wipe everything
  not selected); it only works on full-tree runs.
- `--delete-removed N` deletes on the server only the files removed from
  `src/` over the last N commits (net `git diff HEAD~N..HEAD`; a renamed
  file at its old path), then the folders that are gone locally, once
  empty. It uploads nothing and takes no selection flags; a path that
  exists locally again is kept. `--dry-run` lists the files without
  connecting. The backoffice Deploy tab runs it as **Preview Delete** and
  **Delete On Dev**. Prod leftovers are removed on the hosting instead:
  `on_hosting/delete_prod_missing_on_dev.sh`.
- `--include-tmp` also uploads `tmp/`/`_tmp/`/`.tmp/` dirs (default: skipped).
- `--include-arj-dirs` also uploads `-arj`/`_arj`/`.arj` dirs (default: skipped).
- `--include-all` bypasses ALL junk excludes (accepts `--include--all` alias).
- `--no-images` skips image files (`png/jpg/jpeg/gif/bmp/webp/svg/ico/tif/tiff/avif`;
  alias `--no-image`). Applies even with `--include-all`; explicit `--files`
  entries still upload named images.
- `--committed` lists the git index, so newly `git add`-ed (staged) files
  count as committed too. Fully untracked files are never selected by
  `--committed`/`--staged`/`--last-commit`.
- Missing files are skipped with a `WARNING`, not a failure.

Typical first deploy:

```bash
./deploy-ftp.sh --dry-run                # expect ~28k files
./deploy-ftp.sh --remote-dir /test --dry-run
./deploy-ftp.sh --remote-dir /test       # staging upload, check in browser
./deploy-ftp.sh                          # production upload
```

Pre-upload lint (recommended): add `--lint` to any command — the same file
selection is checked with `lint-php.sh` (`php -l` via the `php-apache`
container) and the upload aborts on syntax errors:

```bash
./deploy-ftp.sh --files qa/index.php --lint --dry-run   # lint-only run
./deploy-ftp.sh --staged --lint
./deploy-ftp.sh --staged --lint --php 8.0                # lint with another PHP
```

A file deleted between scan and upload counts as failed and the run
continues (it no longer aborts the whole deploy).

See `./lint-php.sh --help` for the standalone linter. Note the 3 known
pre-existing failures in `*/php_auth/*` digest lessons — selections covering
them will (correctly) block the upload until fixed.

Repeat runs are incremental (same-size files skipped as `unchanged`).
Files named with `--files` or picked by `--staged`, `--last-commit` or
`--commits` are known to have changed, so they skip the size check and are
always uploaded: an edit that keeps the byte count (a typo fix, a changed
digit) is not lost. The run prints `size check skipped for N file(s)`.
For full-tree, `--committed` and `--dirs` runs, same-size but changed
content (or any doubt) → rerun with `--force` to re-upload everything; with
`-v` forced uploads show `status: uploaded (forced)`.

Output: default prints `Uploading N path(s)` with each requested `dir:` /
`file:` (or `Uploading full tree ...`), a progress bar, then a summary
(`uploaded / skipped / failed`). With `-v` / `--verbose` every file is
listed (`uploading: <path>` + `status: uploaded|skipped|FAILED`), and
`--dry-run -v` lists all `would upload` entries instead of the first 10.

The progress bar shows files done, percent, elapsed time and an ETA:

```text
progress: [##################------------] 60% 182/300 files, 1m 4s elapsed, ETA 42s (uploaded=182 skipped_unchanged=0 failed=0)
```

On a terminal it stays on the last line and is redrawn in place, also
under the `-v` file lines. Off a terminal (the backoffice job log, `nohup`)
it is a plain line about every 1 %, so long uploads never look stuck;
`-v` leaves it out there, since every file already has its own lines.

## 3. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `creds file ... not found` | file not created yet | step 1 above, or pass `--creds` |
| `creds file must live OUTSIDE the repo` | path is inside `container/` | move to `~/.config/<project>/`, use `--creds` |
| `FTP_HOST is empty ...` | field missing/typo in creds file | compare with `hosting-access.env.template` |
| `mode 644 (recommend: chmod 600)` | creds readable by others | `chmod 600 <creds file>` (warning only) |
| login/auth failure | wrong user/password/host | verify in hosting panel; check `FTP_PORT`, try `--tls` / `--no-tls` per hoster docs |
| `MLSD failed ... --delete skipped` | hoster lacks MLSD | upload still done; `--delete` partially skipped |
| timeouts on 2.8 GB first upload | slow FTP | rerun (incremental, resumes by skipping done files), or upload unattended with `deploy-batches.sh` (§4) |

## 4. Files

```text
deploy-ftp.sh            the script
deploy-engine.sh         engine+css wrapper: uploads ONLY .php, ru/.php,
                         section/.php, ao/.php and .css, --force by default
                         (scope via the flags in deploy-engine.sh --help, or the
                         WITH_* variables at the top of the script;
                         --no-force for a same-size-skipping run)
deploy-batches.sh        unattended wrapper: the whole tree (or a selection),
                         --force, in batches of 10 files, a failed batch
                         rerun up to 5 times, a network outage waited out
hosting-access.env.template template (tracked; real creds never in repo)
```

### Batch upload (whole site, unattended)

A full-tree run often dies halfway: one dropped connection ends the
session and the rest of the tree is never uploaded. `deploy-batches.sh`
lists the files once (`deploy-ftp.sh --dry-run -v`, so the junk excludes
still apply), then runs `deploy-ftp.sh --force --files …` on 10 files at a
time, one FTP session per batch. A failed batch is rerun after 30 s, 60 s,
… 150 s; a batch that fails all 6 attempts goes to `failed-files.txt` and
the run moves on.

A network outage does not use up the retries. After a failed attempt the
wrapper checks that the FTP host answers (DNS lookup and TCP connect to
the port, no login). If it does not, the attempt is not counted: the
wrapper probes every 30 s and reruns the batch once the host answers. A
batch waits for the host at most `--outage-wait` seconds in all (1 hour);
past that the run stops and prints the command that resumes it, since
every later batch would fail too.

```bash
./deploy-batches.sh --dry-run                     # 28,792 files in 2,880 batches
nohup ./deploy-batches.sh > ~/deploy-batches.out 2>&1 &
tail -f ~/deploy-batches.out                      # a line per batch, a progress line per percent
./deploy-batches.sh --dirs section --no-images      # a selection instead of the whole tree
./deploy-batches.sh --list ~/.cache/<project>/deploy-batches/<run>/failed-files.txt
```

| Option | Default | Meaning |
|---|---|---|
| `--batch-size N` | 10 | files per `deploy-ftp.sh` run |
| `--retries N` | 5 | reruns of a failed batch; rerun k waits k × `--pause` |
| `--pause SEC` | 30 | base pause before a rerun |
| `--attempt-timeout SEC` | 1800 | an attempt that runs longer counts as failed |
| `--outage-wait SEC` | 3600 | how long one batch waits for an FTP host that does not answer; 0 counts such attempts as retries |
| `--list FILE` | | upload these src-relative paths instead of listing the tree |
| `--start N` | 1 | begin at batch N (resume an interrupted run) |
| `--log-dir DIR` | `~/.cache/<project>/deploy-batches/<date-time>/` | `files.txt`, `run.log`, `failed-files.txt` |

- The target is the default creds file, as for `deploy-ftp.sh`; the
  `host:` line printed first shows where the files go.
- `--creds`, `--src`, `--remote-dir` and `--tls`/`--no-tls` reach every
  batch; other `deploy-ftp.sh` flags only shape the file list.
  `--delete` and `--delete-removed` are refused.
- Progress: on a terminal a bar (`[####------] 41% 1180/2880 batches,
  3h 50m elapsed, ETA 5h 31m, 2 failed`) stays on the last line under the
  batch lines; off a terminal and in `run.log` it is a `progress:` line
  each time the percentage changes.
- `run.log` holds the per-file status of every attempt. The run ends with
  the command that uploads `failed-files.txt` again; Ctrl-C or a kill
  prints the command that resumes from the current batch.
- Each batch rescans `src/` (about 2.5 s), which adds about 2 hours over
  2,880 batches; `--batch-size 20` halves that.
- The backoffice Deploy tab runs it as **Upload All In Batches** (with
  `--lint`, 12-hour job limit like Upload All; commit and directory
  uploads stop after 1 hour).

### Engine-only deploy (stale prod code)

```bash
./deploy-engine.sh --dry-run            # all four engines + css, forced
./deploy-engine.sh --core --ao          # only .php + ao/.php + css, forced
./deploy-engine.sh --no-force           # same scope, skip same-size files
```

Use it when prod serves stale markup: the normal same-size skip leaves
old framework files on the server. After the upload, clear PHP OPcache
on the hosting (restart php-fpm/apache or `opcache_reset()`) — FTP
cannot do that, and with `opcache.validate_timestamps` Off the server
ignores changed files until then.

## 5. Security notes

- Real creds file: `chmod 600`, outside the repo, never committed
  (`.gitignore` covers `*-creds.env`, `hosting-access.env` as a backstop).
- The password is passed to Python via environment, never via argv, and never
  printed (logs show host/user only).
- `--delete` is destructive — remote files with no local counterpart are
  removed. Default is off; use it only when the remote tree should exactly
  mirror `src/`.
