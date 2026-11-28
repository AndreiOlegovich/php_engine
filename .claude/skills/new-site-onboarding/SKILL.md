---
name: new-site-onboarding
description: "The repo was just copied for a new site: fill the profile, run new-project.sh --check, and fix the first failures in order until the gate is green"
---
## Use when
The repository is a fresh copy for a new site (`container/tools/new-project.sh`
made it), or the first commit of a foreign site fails the hooks.
## Do not use for
An existing site's page work (`site-map`) or a tool change (`site-tooling`).
## Workflow
1. The old site is in `container/src` and the local site runs
   (`cd container && docker compose up -d --build php-apache`). If not, do
   that first: the profile comes from the pages, not from guesses.
2. Let the pages suggest the profile:
   `python3 container/tools/discover_site.py --url` (the files, then up to
   300 rendered pages of `SITE_LOCAL_URL`). Show the user its suggested lines
   with the counts behind each, and the pages that answered no HTML page.
   Markers are pieces of the page HTML, not CSS rules: the bottom marker
   (`SITE_BOTTOM_MARKER`), the `--tag` names (`SITE_TAG_MARKERS`, `name=HTML`,
   `|` between alternatives, `;` between names, in single quotes) and the
   page-class markers (`SITE_PAGE_MARKERS`). The user decides which lines go
   in; never invent a marker the tool did not find, and never take one from
   another site. A suggestion below 90% of the pages, or none at all: ask the
   user, and check an answer on a page before writing it:
   `curl -s http://127.0.0.1:<port>/ | grep -cF '<marker>'` prints 1 or more.
3. Fill `container/site-profile.env` from `container/site-profile.env.example`
   with the accepted lines and the rest: the site and dev URLs, the own
   hosts, the engine folders, the feed folder. A key you leave out falls back
   to a generic default. Do not read or print the filled file's secrets; it
   holds none, but it is ignored by git and stays that way.
4. Run the gate over the whole tree: `container/tools/new-project.sh --check`.
   It does not stop at the first failure; it ends with the list of what failed.
5. Fix in this order, rerunning the same command after each group:
   1. line endings: `container/tools/linters/check-line-endings.sh --fix --all --src src`,
      review the diff (`container/docs/site-checks.md` explains a mixed file);
   2. PHP syntax: `php -l <file>` for each failure;
   3. PHP style: `container/tools/linters/lint-static.sh --tool phpcs --fix --files <path>`
      (needs Docker), then `php -l` again;
   4. PHP whitespace and header: `check-php-blank-lines.sh --fix` and
      `check-php-header.sh --fix` in `container/tools/linters/`, same flags;
   5. shell: `container/tools/linters/lint-sh.sh <file>`;
   6. Python: ruff, mypy and pylint with `container/pyproject.toml`;
   7. the sitemap and the redirects: `container/tools/generate-sitemap.sh`,
      `container/tools/check-sitemap.sh`, `container/tools/check-no-redirected-pages.sh`.
6. Each hook name in the output is one row of `container/docs/site-checks.md`;
   read that row before you change the file.
7. A failure that is the engine's, not the site's, is a bug: report it with the
   file and the command, and do not edit the engine's own test or fixture.
## Done means
`new-project.sh --check` prints "All checks passed" (or lists only the steps
skipped for lack of Docker) and the first commit of the site passes the hooks.
