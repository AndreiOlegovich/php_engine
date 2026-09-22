# FTP deploy manual (`deploy-ftp.sh`)

Uploads the site (`src/`) to virtual hosting over FTP/FTPS. All commands run
from this directory (`scripts/` — scripts live here; they operate on the
project root one level up, so `src/`, `docker-compose.yml` etc. resolve).

`container_php83/` is not involved.

## What it does

| Item | Value |
|---|---|
| Script | `deploy-ftp.sh` (bash + embedded Python `ftplib`, no extra packages) |
| Source | `./src` (override: `--src DIR`) |
| Target | `FTP_REMOTE_DIR` from the creds file (override: `--remote-dir DIR`) |
| Transfer | binary, passive mode; files with identical size are skipped |
| Symlinks | resolved — `src/.php`, `src/.css` arrive as real dirs (hosting has no symlinks) |
| Images | uploaded (hosting needs them, unlike git where they are ignored) |
| Skipped junk | `*.log`, `*.db`/`*.sqlite*`, `sess_*`, editor backups, `reports/`, caches |

## 1. One-time setup (credentials)

Credentials NEVER live in this repo. Default location (outside the repo):

```
~/.config/aredel/ftp-creds.env
```

```bash
mkdir -p ~/.config/aredel
cp deploy-ftp.creds.example ~/.config/aredel/ftp-creds.env
chmod 600 ~/.config/aredel/ftp-creds.env
$EDITOR ~/.config/aredel/ftp-creds.env
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

A creds file under `container/` (or anywhere inside the repo) is refused —
keep it outside so it can never be committed. Any path can be used via
`--creds PATH`.

## 2. Usage

```bash
./deploy-ftp.sh --help                              # full help
./deploy-ftp.sh --dry-run                           # plan only, no connection
./deploy-ftp.sh                                     # upload with default creds
./deploy-ftp.sh --creds /path/to/creds.env          # custom creds location
./deploy-ftp.sh --remote-dir /public_html/test      # staging dir on hosting
./deploy-ftp.sh --tls | --no-tls                    # force FTPS on/off
./deploy-ftp.sh --delete                            # also delete remote-only files (off by default)
```

### Partial uploads (selection)

Default is the whole tree. `--files`, `--committed`, `--staged` combine as a
union; `--dry-run` previews the selection. `--delete` is refused with any
selection (it would wipe everything not selected).

```bash
./deploy-ftp.sh --files qa/index.php ru/qa/article.php --dry-run
./deploy-ftp.sh --files "qa/index.php,ru/qa/article.php"   # comma form
./deploy-ftp.sh --files qa/ --dry-run      # a whole subdir under src/
./deploy-ftp.sh --files src/qa/index.php   # src/-prefixed also accepted
./deploy-ftp.sh --staged --dry-run         # staged changes only (git diff --cached)
./deploy-ftp.sh --staged                   # upload staged changes
./deploy-ftp.sh --committed --dry-run      # git-tracked files only (git ls-files)
```

Notes:

- Explicit `--files` entries bypass the junk excludes; `--committed` /
  `--staged` still skip junk (`*.log`, `*.db`, `sess_*`, …).
- `--committed` lists the git index, so newly `git add`-ed (staged) files
  count as committed too. Fully untracked files are never selected by
  `--committed`/`--staged`.
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
```

See `README.md` ("PHP lint") for the standalone linter. Note the 3 known
pre-existing failures in `*/php_auth/*` digest lessons — selections covering
them will (correctly) block the upload until fixed.

Repeat runs are incremental (same-size files skipped).

## 3. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `creds file ... not found` | file not created yet | step 1 above, or pass `--creds` |
| `creds file must live OUTSIDE the repo` | path is inside `container/` | move to `~/.config/aredel/`, use `--creds` |
| `FTP_HOST is empty ...` | field missing/typo in creds file | compare with `deploy-ftp.creds.example` |
| `mode 644 (recommend: chmod 600)` | creds readable by others | `chmod 600 <creds file>` (warning only) |
| login/auth failure | wrong user/password/host | verify in hosting panel; check `FTP_PORT`, try `--tls` / `--no-tls` per hoster docs |
| `MLSD failed ... --delete skipped` | hoster lacks MLSD | upload still done; `--delete` partially skipped |
| timeouts on 2.8 GB first upload | slow FTP | rerun (incremental, resumes by skipping done files) |

## 4. Files

```text
deploy-ftp.sh            the script
deploy-ftp.creds.example template (tracked; real creds never in repo)
```

## 5. Security notes

- Real creds file: `chmod 600`, outside the repo, never committed
  (`.gitignore` covers `*-creds.env`, `ftp-creds.env` as a backstop).
- The password is passed to Python via environment, never via argv, and never
  printed (logs show host/user only).
- `--delete` is destructive — remote files with no local counterpart are
  removed. Default is off; use it only when the remote tree should exactly
  mirror `src/`.
