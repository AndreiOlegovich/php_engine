---
name: site-verify
description: "Finish or commit work: php -l, tests, pre-commit failure audit, explicit-path commit"
---
## Use when
The task ends with "verify", "finish", "commit" or "hand over".
## Do not use for
Content planning or a first scaffold.
## Commit approval
Finish verification first, then ask whether the user commits or wants you to.
Ask every time and wait for the answer. Never run `git commit`, `--amend` or
another history change without it. If the user commits, give the exact
explicit-path `git add` and `git commit -m` commands and leave the worktree
uncommitted. Never `git add -A`.
## Do not hide untracked work
Never edit `.git/info/exclude`, `.gitignore` or another ignore file to make
`git status` clean. Decide for each untracked file: commit or delete. Ignore
rules are for generated or shared artifacts.
## Command order in a hand-over
List the commands in the order they run, as one block, with each check
directly before the command it guards. Reread the block top to bottom: step N
must not need anything from step N+1.
## Workflow
1. `php -l` on every changed PHP file; render a changed page through the CLI
   with `DOCUMENT_ROOT` set to `container/src`, expect exit 0.
2. `container/tools/run-tests-venv.sh -- <the matching tests> -q`; never
   `pip install` into the managed venv.
3. `pre-commit run --files <explicit paths>`. On a failure: name the hook,
   audit its finding, fix it narrowly, rerun. What each site hook means:
   `container/docs/site-checks.md`.
4. Regenerate the sitemap only if pages were added or removed, and show the
   `<loc>` count before and after.
5. Python stays on 3.12: `python3 container/tools/linters/check_python_consistency.py --require-precommit`
   and `tests/test_python_version_consistency.py` pass before you finish.
6. Commit: `git add -- <explicit paths>`, then
   `git commit -m "<subject>" -m "<body>"`. No AI attribution anywhere.
## Never bypass the suite
A broken hook is a tooling bug, not permission to skip every gate. Never use
`git commit --no-verify`, `git -c core.hooksPath=/dev/null` or an equivalent.
Repair the hook environment and rerun. `SKIP=<hook-id>` disables one hook, and
only for a hit you audited and call intended, with every other hook passing.
## check-diff-artifacts hits are real damage
`container/tools/check-diff-artifacts.sh --staged` blocks byte-level damage:
line endings lost or flipped together with other edits, U+FFFD, mojibake, a
file saved in another encoding, a BOM added. A pure line-ending conversion
passes with a note, as do whitespace-only diffs. On a hit, restore the file
from HEAD and redo the edit, or split the work: one commit converts, another
edits. Rules: `container/tools/CHECK_DIFF_ARTIFACTS.md`.
## Done means
Every check above passed, the commit holds only the named paths, and the
message ends with its last content line.
