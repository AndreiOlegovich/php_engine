# Start a new site from this repo

A new site is a copy of the engine plus its own pages. The copy carries the
tools, the tests, the agent roles, the skills and the commit gate;
`container/src` starts with one stub page. The README at the repository root
walks through the whole start, from the clone to the first commit; this page
is the reference for the script, the project name and the profile.

## Copy

From a checkout of this repo:

```bash
container/tools/new-project.sh ~/github/my_site --name "My Site"
```

The script copies the **tracked** files of the checkout it lives in, so an
ignored log, a visitor export or a filled creds file never travels. It then:

1. empties `container/src` to an `index.php` that prints the site name; the
   engine's own folder `container/src/.php` stays, and the dummy folder
   `container/src/example`, the path the docs use in their examples, goes;
2. writes `container/site-profile.env` from the example, with `SITE_NAME`;
3. runs `git init -b main` and `pre-commit install`; nothing is staged;
4. creates `container/.venv` with `uv` when `uv` is installed.

`container/site-profile.env` is ignored by git and holds the site values:
hosts, language folders, engine folders, page classes and markers, the feed
folder. A key you leave out falls back to a generic default, so the tree runs
before the profile is complete.

## Project name

The script asks for a project name (or takes `--name`). Its lower-case form is
`SITE_PROJECT` in `container/.env`, and everything two projects could collide
on uses it, so several projects run side by side:

| What | Name |
|---|---|
| Compose project | `<project>` |
| Containers | `<project>-apache`, `<project>-pytest-web`, `<project>-allure-web` |
| Site image | `<project>:<php version>` |
| OpenCode images | `<project>-agent`, `<project>-agent-script`, ... |
| OpenCode sessions | volume `<project>-agent-data` |
| Agent worktrees | `../<project>-agent-<task>` |
| Hosting credentials | `~/.config/<project>/hosting-access.env` |
| Logs and translation runs | `~/.local/share/<project>/` |
| Deploy logs | `~/.cache/<project>/deploy-batches/` |
| Ports | `SITE_HTTP_PORT` and `SITE_ALLURE_PORT`, the first free ones |

The agent permission files get the same name, so their rules keep covering
this project's credentials and logs. A name that another project, image,
volume or credentials folder already uses is refused.

The ports 8080 and 5050 in the docs are the defaults of a checkout without
`container/.env`; your own are in that file. OpenCode logins live in the
volume, so each project logs in once or passes `--opencode-key`.

An existing checkout gets a name and ports with:

```bash
container/tools/new-project.sh --configure --name my_site
```

## Fill the profile

With the site copied in and running, `python3 container/tools/discover_site.py --url`
suggests the markers, the page classes and the language folders from what the
pages render; copy the lines that are right.

| Key | What it decides |
|---|---|
| `SITE_URL`, `SITE_DEV_URL`, `SITE_LOCAL_URL` | the hosts the site tests use |
| `SITE_OWN_HOSTS` | the host names that count as internal links |
| `SITE_LANG_DIRS` | the language folders (empty for a one-language site) |
| `SITE_ENGINE_DIRS` | the folders holding the framework classes |
| `SITE_PAGE_CLASSES`, `SITE_PAGE_MARKERS` | what builds a page, and its marker in the HTML |
| `SITE_NO_MARKER_PATHS` | parts of the URL paths of pages without that marker by design (API endpoints, apps); `test_page_html_id.py` reports each one it skips |
| `SITE_BOTTOM_MARKER` | what every complete page renders last, `</html>` by default; `test_content_loaded.py` fails a page that stops before it |
| `SITE_TAG_MARKERS` | named pieces of the page HTML, such as the top menu, that `test_content_loaded.py --tag <name>` looks for; none by default |
| `SITE_HOSTING` | the hosting the site is uploaded to (`beget`, `dreamhost`, `apache-proxy`, `apache-tls`, `litespeed`, `nginx`, `unknown`); `htaccess_hosting.py`, the gate and a commit hook check the `.htaccess` files against it |
| `SITE_FEED_DIR` | the folder of the article feed |
| `SITE_PHP_VERSION` | the PHP version of the site |

The full list, with the defaults, is `container/site-profile.env.example`.

`container/tools/new-project.sh --next` prints the step a project is at: start
the site, copy the old site in, fill the profile, the gate and the first
commit, the sitemap, the page tests. `container/tools/onboard.sh` runs that
step and prints the one after it; `container/tools/auto_onboard.sh` runs them
all and asks when it needs the user. Both are wrappers around
`container/tools/onboard.py`, where the steps are defined.

## First gate

```bash
container/tools/new-project.sh --check
```

It runs every site checker, shellcheck, `php -l` and phpcs (when Docker runs)
and `pre-commit run --files` over every tracked and not-yet-added file. It does
not stop at the first failure and ends with the list of what failed. On a new
site the first run takes 10 to 15 minutes, because the lint self-tests run too. Each row
of `container/docs/site-checks.md` says what a failure means and how to fix it.
The self-test hooks of the lint scripts need Docker; without it they fail for
that reason alone.

## First commit

```bash
git add -- .gitignore .pre-commit-config.yaml .python-version AGENTS.md CLAUDE.md opencode.jsonc .claude .opencode container
git commit -m "Start <site name>"
```

The hooks from `.pre-commit-config.yaml` run on the index. A failure is a
message, not a stop: read the hook name, fix the file, `git add` it again and
repeat. The skill `new-site-onboarding` gives the order: line endings, PHP
syntax, phpcs, shell, Python, sitemap and redirects.

## Start over

`container/tools/remove-project.sh <folder>` prints what a project owns
(containers, images, volumes, network, log and cache folders, the folder)
and `--yes` removes it, so `new-project.sh` can use the name again. The
credentials folder stays unless `--with-credentials`.

## What the copy does not bring

- the pages, images, `.htaccess` and sitemap of any other site;
- hosting credentials: the deploy scripts read
  `~/.config/<project>/hosting-access.env`; start from
  `container/tools/deploy/hosting-access.env.template` and keep the filled
  file outside the repository.
