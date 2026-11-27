# php_engine

php_engine is a starter kit for PHP sites. You do not build a site inside
php_engine: you run its `new-project.sh`, which makes a new folder for the
site with its own copy of everything below, and you work in that folder.

Each site gets:

- a local web server in Docker: PHP 8.5 and Apache, serving `container/src`;
- checks that run on every `git commit`: PHP syntax, PHP style (PSR-12),
  line endings, broken URLs, shell and Python;
- fixers for most of what the checks find;
- tests for pages, links, images, stylesheets, the sitemap and redirects;
- deploy, log analysis and translation tools;
- instructions and permissions for AI coding agents (Claude Code and
  OpenCode), starting from `AGENTS.md`.

Each site gets its own name, Docker images, containers and ports, so several
sites made from php_engine can run at the same time.

The steps below take an existing site, usually an old one with errors,
lint findings and stray files, from a folder of files to a first clean
commit.

## 1. Install the tools

You need Linux with bash, and:

| Tool | Check | Install |
|---|---|---|
| git, rsync | `git --version`, `rsync --version` | your package manager |
| Docker Engine with Compose v2 | `docker compose version`, and `docker info` works without `sudo` | Docker's install guide, then add your user to the `docker` group |
| Python 3.14 as `python3.14` and `python3` | `python3 --version` | `uv python install 3.14 --default` (needs `~/.local/bin` before `/usr/bin` in `PATH`) |
| uv | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| pre-commit | `pre-commit --version` | `uv tool install --python 3.14 pre-commit` |

Node.js is optional: only the JavaScript and HTML checks use it.

## 2. Get php_engine

```bash
mkdir -p ~/github && cd ~/github
git clone https://github.com/<owner>/php_engine.git
```

`<owner>` is the GitHub account that hosts php_engine; the green **Code**
button on its page shows the full address. Leave this folder as it is: every
site lives in a folder of its own. `git -C ~/github/php_engine pull` later
gets the newest php_engine for the next site; a site made earlier keeps the
files it was made with.

## 3. Create the project

```bash
~/github/php_engine/container/tools/new-project.sh
```

It asks four questions:

```text
Project name: My Shop
Folder [/home/you/github/my-shop]:
The content test looks for markers: pieces of the page HTML source, not CSS.
What every complete page renders last, e.g. id="footer-end" [</html>]:
Named markers for --tag, e.g. menu=<nav class="menu" [none]:
```

- **Project name**: any name with a Latin letter or a digit, without quotes,
  `$`, `\` or a backtick. Its lower-case form, here `my-shop`, names the
  project's Docker images and containers, its credentials and log folders.
  A name that another project already uses is refused.
- **Folder**: Enter takes the offered folder, next to php_engine. It must
  not exist yet.
- **Markers**: pieces of the HTML your pages render (open a page and view
  its source), not CSS rules. The first is what every complete page
  renders last, so the content test can tell a page that stopped halfway;
  Enter keeps `</html>`. The second gives names to the parts the content
  test can look for with `--tag`, such as the top menu:
  `menu=<nav class="menu"`, with `|` between alternatives and `;` between
  names; Enter leaves none. Neither may hold a single quote. They go into
  `container/site-profile.env` as `SITE_BOTTOM_MARKER` and
  `SITE_TAG_MARKERS`, where you can change them later.

Without the questions (the markers keep their defaults unless you add
`--bottom-marker` and `--tag-markers`):

```bash
~/github/php_engine/container/tools/new-project.sh ~/github/my-shop --name "My Shop"
```

In under a minute the script:

1. copies the files git tracks in php_engine; never `.git`, the venv,
   caches, logs or filled credential files;
2. empties `container/src` down to an `index.php` that prints the site name,
   and keeps `container/src/.php`, the engine's own modules;
3. writes `container/site-profile.env` (the site's settings) and
   `container/.env` (the project name and free ports); git ignores both;
4. runs `git init -b main` and `pre-commit install`; nothing is staged;
5. creates `container/.venv` with the test tools.

It ends like this:

```text
  project:      my-shop
  site:         http://127.0.0.1:8081/
  allure:       ports 5060-5066
  images:       my-shop:<php>, my-shop-agent, my-shop-agent-<language>
  credentials:  ~/.config/my-shop/hosting-access.env
Initialising git ...
pre-commit installed at .git/hooks/pre-commit
...
Next, in /home/you/github/my-shop:
  1. edit container/site-profile.env (hosts, language folders, page classes);
  2. put the pages of the site into container/src;
  3. container/tools/new-project.sh --check
  4. git add -- <paths> && git commit -m "<subject>"
```

Your port can differ: it is `SITE_HTTP_PORT` in `container/.env`. This README
writes 8081.

From here on, work in the new folder. Every command below runs from it:

```bash
cd ~/github/my-shop
```

## 4. Start the empty site

```bash
(cd container && docker compose up -d --build php-apache)
curl -s http://127.0.0.1:8081/
```

The first build downloads the PHP 8.5 image (about 500 MB). The page shows
the site name. Open `http://127.0.0.1:8081/` in a browser.

## 5. Copy the old site into container/src

Copy, do not move: the untouched original is what you compare against.

```bash
OLD=/path/to/old-site     # the folder that holds the old site's index.php
rsync -a --exclude .git/ "$OLD"/ container/src/
```

The trailing slashes copy the content of the folder, not the folder itself.
The site's `index.php` replaces the stub. Reload the browser: a change under
`container/src` shows at once, without a restart. A site that exists only on
its hosting account is downloaded first (an FTP client, `rsync` over SSH or
the hosting panel's backup) and copied from there.

Then look for four things:

- **A `.php/` folder in the old site.** php_engine keeps
  `container/src/.php/_modules/Metrica.php` there (a visitor counter). The
  two folders merge, and a file with the same path is replaced by the old
  site's.
- **Passwords and keys.** `.gitignore` already leaves out `.env`, `*.key`,
  `*.pem`, `.htpasswd` and `*-creds.env`, but a database password inside a
  PHP file is not caught. Find such files:

  ```bash
  grep -rlIiE 'passw|secret|api_?key|token' container/src
  ```

  Keep each secret out of git before the first commit: put it in a file of
  its own, add that file's path to `.gitignore`, and commit a copy without
  the values (for example `config.example.php`) instead.
- **Junk.** Database dumps (`*.sql`), archives (`*.zip`, `*.tar.gz`) and old
  copies do not belong in the docroot: move them out of `container/src`.
  Logs (`*.log`), `*.bak`, `*.orig` and `name copy.php` files are already
  ignored.
- **Images.** `.gitignore` leaves images out of git (`*.jpg`, `*.png`,
  `*.gif`, `*.svg`, `*.webp`, `*.ico` and more, in any letter case), for
  sites too big to keep them in a repository. The local site still shows
  them, because the files are in `container/src`. What git tracks instead is
  `container/docs/image-files.txt`: one line per image with its sha1, size
  and path, rewritten by the `image-list` hook on every commit. So git shows
  which images the site has and when one changed, and a backup or the
  hosting copy can be checked against the list. To keep the images
  themselves in git, delete those lines from `.gitignore` before the first
  commit. An image made in a worktree (by an agent, for example) is ignored
  there too, so merging the branch brings its pages but not the image:
  `python3 container/tools/ai/copy_worktree_images.py <worktree>` copies the
  new images into the main checkout (`--dry-run` lists them first).

`git status --short --ignored container/src` shows what git will leave out.

## 6. Describe the site

`container/site-profile.env` holds the values the tools and tests need to
know about this site; a key you leave out falls back to a default. Change at
least:

| Key | What it is |
|---|---|
| `SITE_URL` | the public address, e.g. `https://www.my-shop.example` |
| `SITE_DEV_URL` | a staging address, if there is one |
| `SITE_OWN_HOSTS` | the host names that count as this site's own links |
| `SITE_LANG_DIRS` | language folders under `container/src` (`ru`, `de`, ...), empty for one language |

What the other keys decide is in `container/docs/new-project.md`; all keys,
with their defaults, are in `container/site-profile.env.example`.

## 7. Find what is broken

### The pages

Open the main pages in a browser, then read the error log:

```bash
(cd container && docker compose logs --no-log-prefix php-apache 2>&1) | grep -oE 'PHP (Parse|Fatal) error: .*' | cut -c1-160 | sort | uniq -c | sort -rn | head -20
```

A broken page usually still answers `200`, because PHP prints the error into
the page: the log is the reliable signal. The command lists each error once,
with the number of times it happened. Common errors of an old site on
PHP 8.5:

| Error | Cause | Fix |
|---|---|---|
| `Call to undefined function mysql_connect()` | the `mysql_*` functions were removed in PHP 7 | `mysqli` or PDO |
| `syntax error, unexpected token "{"` | a string offset in braces, `$text{0}` | `$text[0]` |
| `Call to undefined function each()` | removed in PHP 8 | `foreach` |
| `Call to undefined function create_function()` | removed in PHP 8 | an anonymous function |
| `Call to undefined function ereg()` or `split()` | removed in PHP 7 | `preg_match()`, `explode()` or `preg_split()` |
| `Unparenthesized a ? b : c ? d : e is not supported` | nested ternaries need parentheses since PHP 8 | add the parentheses |

Deprecation notices are hidden, from the page and from the log alike, so
that an old site stays readable. To list them, set `error_reporting = E_ALL`
in `container/containerfiles/php-engine.ini` and run
`(cd container && docker compose restart php-apache)`.

To see how a page behaved on the PHP the site used before, run that version
for a while and switch back afterwards:

```bash
(cd container && PHP_VER=7.4 docker compose up -d --build php-apache)   # the old PHP
(cd container && docker compose up -d php-apache)                       # back to 8.5
```

### The code

```bash
container/tools/new-project.sh --check
```

The first run takes 10 to 15 minutes: it also runs the self-tests of the lint
tools, whose files are new in this repository. It does not stop at the first
failure and ends with a list, for example:

```text
What failed:
  - line endings (container/src)
  - PHP whitespace-only lines (container/src)
  - PHP opening header (container/src)
  - malformed URLs (container/src)
  - PHP syntax (php -l)
  - PHP style (phpcs)
  - pre-commit, every file
```

The last line repeats the same checks the way `git commit` runs them.

## 8. Fix, in this order

Stage the imported files once first, so that git can show what each fixer
changes (staging does not commit anything):

```bash
git add -- container/src
```

After each step, `git diff --stat container/src` and `git diff container/src`
show what changed, and `git add -- container/src` accepts it.

1. **Line endings** (Windows `CRLF` to `LF`):
   `container/tools/linters/check-line-endings.sh --fix --all --src src`
2. **Lines that hold only spaces**:
   `container/tools/linters/check-php-blank-lines.sh --fix --all --src src`
3. **The PHP opening line** (`<?php`, then one empty line):
   `container/tools/linters/check-php-header.sh --fix --all --src src`
4. **PHP style**: `container/tools/linters/lint-static.sh --tool phpcs --fix --all`
   fixes most PSR-12 findings and lists the files it could not fix.
5. **PHP syntax**: `container/tools/linters/lint-php.sh --all` lists each
   `FAIL: <file>` with PHP's message. Fix these by hand, with the table in
   step 7.
6. **Broken URLs** (`https://a.comhttps://a.com/...`, hosts without a dot):
   `container/tools/linters/find-malformed-urls.sh --all --src src`, fixed by
   hand.

Then run `container/tools/new-project.sh --check` again until it prints
`All checks passed.` What each check means and how to fix it is in
`container/docs/site-checks.md`.

When phpcs leaves more findings in old code than you can fix now, park a
folder you will clean up later. Add an exclude pattern, with the reason, to
`container/phpcs.xml`:

```xml
    <!-- Old code, cleaned up later: php -l and the other checks still run on it. -->
    <exclude-pattern>*/src/old-shop/*</exclude-pattern>
```

Remove the line once the folder is clean. Never skip the checks with
`git commit --no-verify`.

## 9. First commit

```bash
git status --short     # no secret and no junk in the list
git add -- .gitignore .pre-commit-config.yaml .python-version AGENTS.md CLAUDE.md opencode.jsonc .claude .opencode container
git commit -m "Start My Shop"
```

The hooks run again and the first commit takes 10 to 15 minutes. When a hook
fails, its name in the output is a row of `container/docs/site-checks.md`:
fix the file, `git add` it again and repeat the commit. The first commit of a
site with images always stops once: `image list` writes the site's images
into `container/docs/image-files.txt`. Run the same `git add` line and commit
again.

To publish the repository, create an empty one on GitHub (no README, no
licence), then:

```bash
git remote add origin git@github.com:<you>/my-shop.git
git push -u origin main
```

## 10. Test the pages

The page tests read the sitemap. Generate it with the site's public address:

```bash
(cd container && BASE_URL=https://www.my-shop.example tools/generate-sitemap.sh)
```

It writes `container/src/sitemap.xml` and the site search index under
`container/src/search/`. Read the sitemap: every PHP file that answers at a
URL is listed, configuration files and includes too. Add those to
`container/tools/sitemap-excludes.conf` (a `filename` or `prune` line each)
and generate again.

With the site running:

```bash
container/tests/run_test.sh -y sitemap internal_links img_src content_loaded
```

| Test | What it reports |
|---|---|
| `sitemap` | a sitemap URL that does not answer 200 (a page with a PHP error can still pass this one) |
| `internal_links` | links between pages that lead nowhere |
| `img_src` | `<img>` tags whose file is missing (reads the files, no server needed) |
| `content_loaded` | pages that stop before their end, usually because of a PHP error |

`content_loaded` looks for `SITE_BOTTOM_MARKER` of the profile, `</html>`
by default. When the site's common footer renders a last element of its own,
name it there, e.g. `SITE_BOTTOM_MARKER='id="footer-end"'` for
`<div id="footer-end"></div>`: then a page that ends in another template,
such as an error page with its own `</html>`, fails too. `BOTTOM_MARKER=...`
in front of the command overrides it for one run. To check another part of
every page, name it in `SITE_TAG_MARKERS`, e.g.
`SITE_TAG_MARKERS='menu=<nav class="menu"'`, and run
`container/tests/run_test.sh -y content_loaded -- --tag menu`; the engine
knows no names of its own, and a `--tag` value that is no name there is
searched as it is.

Each run saves its output in `container/tests/run_test_log/`; `summary.txt`
says PASS or FAIL per test. The tests `page_html_id` and `page_stylesheets`
expect the engine's page classes (`SITE_PAGE_CLASSES`, `SITE_PAGE_MARKERS`
and `SITE_TREE_SHEETS` in the profile): a site without them fails or skips
them. Pages that have no shell by design (an API endpoint, an app) go into
`SITE_NO_MARKER_PATHS` as parts of their paths, e.g. `"/api/ /widgets/"`;
`page_html_id` lists every URL it skips that way as a skipped test.
`redirects` needs `container/tests/redirect_cases.py`, copied from
`redirect_cases.example.py`. The browser report of the link checker (Allure)
is described in `container/README.md`.

## 11. Design files: CSS, JavaScript, images

An old site often links stylesheets, scripts, fonts or images that are no
longer there, or keeps several copies of the same stylesheet.

- Every file a browser asked for and did not get:

  ```bash
  (cd container && docker compose logs --no-log-prefix php-apache 2>&1) | grep '" 404 ' | awk '{print $7}' | sort | uniq -c | sort -rn | head -20
  ```

  The browser's developer tools (F12, **Network**, filter by status) show
  the same for one page.
- Missing image files: the `img_src` test above.
- JavaScript syntax: `container/tools/linters/lint-js.sh --all` (needs Node.js).
- HTML structure of a page: `container/tools/linters/html/validate-local-html.sh /`
  (needs `curl` and `npx`, and the site running).
- Links to the old address of the site:
  `grep -rnI 'old-domain.example' container/src`.
- Duplicate images: `container/tools/fix/dedupe-images.py --dir container/src/images`.
- A site with one stylesheet per language folder: `SITE_TREE_SHEETS` in the
  profile, checked by the `page_stylesheets` test.

## 12. Deploy

The deploy scripts read the hosting credentials from
`~/.config/my-shop/hosting-access.env`. Start from
`container/tools/deploy/hosting-access.env.template`, keep the filled file
outside the repository, and read `container/tools/deploy/DEPLOY_FTP.md`.
Start with `--dry-run`, which prints the plan without connecting.

## 13. With an AI agent

Claude Code and OpenCode read `AGENTS.md` in the site folder. To have an
agent take steps 7 and 8, tell it: "The repo was just copied for a new site:
run new-project.sh --check and fix the failures." Its `new-site-onboarding`
skill follows the same order.

## Everyday commands

| What | Command |
|---|---|
| start the site | `(cd container && docker compose up -d php-apache)` |
| stop everything | `(cd container && docker compose stop)` |
| PHP errors | `(cd container && docker compose logs --no-log-prefix php-apache 2>&1) \| grep -E 'PHP (Parse\|Fatal) error'` |
| check one PHP file | `container/tools/linters/lint-php.sh --files src/about/index.php` |
| fix the style of one file | `container/tools/linters/lint-static.sh --tool phpcs --fix --files src/about/index.php` |
| the checks of one commit, before committing | `pre-commit run --files <paths>` |
| everything | `container/tools/new-project.sh --check` |

## More to read

- `container/README.md`: the containers, the linters, the link checker and
  the full tool index.
- `container/CONTAINER.md`: build, run, enter and debug the web container.
- `container/docs/new-project.md`: the project name, ports and profile keys.
- `container/docs/site-checks.md`: each commit check on `container/src`.
- `container/docs/lint-static.md`: phpcs, phpstan and psalm.
- `AGENTS.md`: the rules for AI agents working on the site.
