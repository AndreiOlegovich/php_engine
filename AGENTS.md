# Project instructions

php_engine is a PHP site engine. `container/src` is the docroot and a URL maps
1:1 to a file: `/about/` is `container/src/about/index.php`, `/about.php` is
`container/src/about.php`. Tools live in `container/tools`, tests in
`container/tests`, the site's own values in `container/site-profile.env`
(copy `container/site-profile.env.example`; do not commit the filled file).

The engine is copied for a new site: the pages, the hosts and the language
folders belong to the site, and a tool or test reads them from the profile
(`SITE_*` keys) instead of naming one site's value.

## Route the task

| Task | Load these skills first |
|---|---|
| Edit or create anything under `container/src` | `site-map` |
| Redirects and rules in an `.htaccess` | `site-htaccess` |
| `container/tools`, `container/tests`, a script or a Dockerfile | `site-tooling` |
| Finish, verify or commit | `site-verify` |
| "Is it done / merged / live?" | `site-fix-status` |
| Claude Code only: the message starts with "assign to opencode" (or "delegate to opencode") | `assign-to-opencode` |
| OpenCode only: the message starts with "delegate to container" (or "give it to the container") | `delegate-to-container` |

## Always

- No attribution to any AI anywhere: no `Co-Authored-By` lines and no
  "Generated with Claude Code" lines in commit messages, PR bodies, plans,
  briefs or docs, and no Anthropic or Claude credit in them. A commit message
  and a PR body end with their last content line.
- Never commit or push without asking: finish verification, then ask whether
  to commit or to hand over explicit-path `git add` / `git commit` commands.
  Never `git add -A`, never `--no-verify` or another hook bypass, never
  `git push --force`, and never edit an ignore file to hide untracked work.
- Read a file before you edit it. An unfamiliar file or an uncommitted change
  is someone's work: look at it before you overwrite or delete it.
- Never commit secrets: `*.env`, `*creds*`, `*.key`, `*.pem` and `.htpasswd`
  stay out of the repository. A template is tracked, the filled file is not.
- After each edit run the narrowest check: `php -l` for one PHP file,
  `pre-commit run --files <paths>` for the files you changed, and the matching
  test (`container/tools/run-tests-venv.sh -- tests/<file> -q`).
- Bump the `# version X.Y.Z` header of every changed versioned file; the
  `check-version` hook fails a change without it.
- Python stays on 3.12. Declare dependencies in the owning `pyproject.toml`
  and lock with `uv`; never `pip install` into a uv-managed `.venv`.
- Line endings under `container/src` are LF (`site-line-endings` hook). Do not
  mix a line-ending conversion with other edits in one commit.
- A script that rewrites a file something else reads writes a temp file in the
  same directory and renames it (`container/docs/atomic-file-writes.md`).
- Regenerate the sitemap (`cd container && ./tools/generate-sitemap.sh`) only
  after adding or removing pages.
- Search file contents with `git grep -n`: it reads tracked files only.
  `grep -r` with lines also reads ignored data, so ask first.
- What each commit hook on `container/src` means and how to fix it:
  `container/docs/site-checks.md`.

## Agents

- OpenCode: start it with `container/tools/ai/opencode.sh`, which checks that
  every pinned model id still exists first. Roles in `.opencode/agents/`:
  `triage` (read-only, free model), `builder` (edits), `reviewer` (read-only
  review). Permissions are in `opencode.jsonc`; never start OpenCode with
  `--auto` on the host, it approves every ask.
- Claude Code: the same three roles in `.claude/agents/`; permissions in
  `.claude/settings.json`.
- Supervisor loop: `container/tools/ai/delegate.py` gives a task to OpenCode
  in Docker, in its own worktree `../site-agent-<slug>`, and sends the fix
  rounds into the same session. The worker follows
  `container/tools/ai/docker/CONTAINER.md`.
