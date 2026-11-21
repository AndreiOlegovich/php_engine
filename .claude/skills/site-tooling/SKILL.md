---
name: site-tooling
description: "Change a script, a test, a linter or a Dockerfile in container/tools, container/tests or container/containerfiles"
---
## Use when
The task changes a shell script, a Python tool, a test, a linter or an agent
image.
## Do not use for
Pages and redirects (`site-map`, `site-htaccess`).
## Workflow
1. Shell scripts: bump the `# version X.Y.Z` header on every edit
   (`check-version` fails a change without it); `shellcheck` runs in
   pre-commit.
2. Python targets 3.12. Gate with `pre-commit run --files <paths>` (ruff,
   ruff format, mypy, pylint with `container/pyproject.toml`).
3. Dependencies: add to the owning `pyproject.toml`, then
   `uv lock --project <dir>` and `uv sync --project <dir>`. Never `pip install`
   into a `.venv`; the next `uv sync` wipes it.
4. Tests: `cd container && ./tools/run-tests-venv.sh -- tests/<file> -q`.
5. Tools and tests read site values from `container/site-profile.env`
   (`container/tests/site_profile.py`, or the `SITE_*` variable in shell); a
   missing value skips the test with a message. Do not name one site's
   host, folder or class.
6. A script that rewrites a file other code reads writes a temp file in the
   same directory and renames it (`container/docs/atomic-file-writes.md`).
7. A tree-wide rewrite reads and writes bytes, so line endings survive; a
   conversion of line endings is a commit that changes nothing else.
8. A quick look at a folder or a site copy: list the first level, step into a
   lone wrapper (`public_html/`), then compare names only. No `cmp`, `diff`
   or hashes unless asked.
## Done means
The narrow checks above ran on the changed files and the matching test passes.
