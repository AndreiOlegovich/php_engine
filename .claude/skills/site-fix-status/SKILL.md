---
name: site-fix-status
description: "Say whether a fix or page is done, merged into main or live: check the current main head, branches, worktrees and stashes"
---
## Use when
The user asks whether a fix is done, merged, committed, on `main` or live, or
before you repeat a "fixed" claim from an earlier session.
## Do not use for
Writing the fix, committing (`site-verify`), or reading the logs themselves:
the logs hold visitor data, so ask before you open them.
## Rule 1: start from the current main head
Never reuse a `main` snapshot from earlier in the conversation. Run
`git fetch origin --prune && git rev-parse --short origin/main` and name that
head in the answer ("as of origin/main <sha>").
## Rule 2: check each fix against that head
- A commit: `git merge-base --is-ancestor <sha> origin/main && echo IN_MAIN || echo NOT_IN_MAIN`
- A file a fix should have added: `git ls-tree -r --name-only origin/main -- <path>`
- A rule in `.htaccess`: `git show origin/main:container/src/.htaccess | grep -n '<pattern>'`
- Code and content: `git grep -n <pattern> origin/main -- container/src`
## Rule 3: look for unmerged work
A fix can exist without being on `main`. Check, in this order:
1. Branches: `git for-each-ref --format='%(refname:short)' refs/heads`, then
   `git rev-list --count origin/main..<branch>`; 0 means its commits are in main.
2. Worktrees: `git worktree list --porcelain`, then `git -C <path> status --short`
   for each. Untracked files count.
3. Stashes: `git stash list`, then `git stash show --name-only stash@{n}`.
## Rule 4: live is not main
A commit on `main` is live only after a deploy. Say "on main, not checked
live" unless the user gave a log window or a URL to compare against.
## Rule 5: report
One line per item: `fixed on main` with the commit; `on a branch or worktree
only` with the location; `not fixed` with what is missing. Name the head the
answer is based on. Do not claim tests passed unless they ran against that head.
