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
1. Fill `container/site-profile.env` from `container/site-profile.env.example`:
   the site and dev URLs, the own hosts, the language folders, the engine
   folders, the page classes and markers, the feed folder. A key you leave out
   falls back to a generic default; start from the defaults and change what the
   site differs in. Do not read or print the filled file's secrets; it holds
   none, but it is ignored by git and stays that way.
2. The markers are the site's own HTML, so ask the user for them; never guess
   them and never take them from another site. A marker is a piece of the
   page source, such as `id="footer-end"` or `<nav class="menu"`, not a CSS
   rule. Ask, unless new-project.sh already wrote the answers:
   - what every complete page renders last: `SITE_BOTTOM_MARKER`, `</html>`
     when the footer renders nothing of its own;
   - which parts the content test should find by name with `--tag`, such as
     the top menu: `SITE_TAG_MARKERS`, `name=HTML` with `|` between
     alternatives and `;` between names, in single quotes; none when the
     user names none;
   - when the site has page classes of its own, the marker each one renders:
     `SITE_PAGE_MARKERS`.
   Check each answer against a page of the local site before you write it:
   `curl -s http://127.0.0.1:<port>/ | grep -cF '<marker>'` (the port of
   `SITE_LOCAL_URL`) prints 1 or more. Show the user a marker that is not
   found; do not change it on your own.
3. Run the gate over the whole tree: `container/tools/new-project.sh --check`.
   It does not stop at the first failure; it ends with the list of what failed.
4. Fix in this order, rerunning the same command after each group:
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
5. Each hook name in the output is one row of `container/docs/site-checks.md`;
   read that row before you change the file.
6. A failure that is the engine's, not the site's, is a bug: report it with the
   file and the command, and do not edit the engine's own test or fixture.
## Done means
`new-project.sh --check` prints "All checks passed" (or lists only the steps
skipped for lack of Docker) and the first commit of the site passes the hooks.
