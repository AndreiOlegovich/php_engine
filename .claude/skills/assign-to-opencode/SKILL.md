---
name: assign-to-opencode
description: "The user says \"assign to opencode ...\" (or \"delegate to opencode\"): you are Claude Code on the host as supervisor, OpenCode in Docker does the work through container/tools/ai/delegate.py"
---
## Use when
The message starts with "assign to opencode", "delegate to opencode" or "give
it to opencode", or the user runs `/assign-to-opencode <task>`. You are the
supervisor: you write the brief, start the worker, review its result on the
host and send fixes. You do not make the change yourself, not even a small
part of it.
## Do not use for
- OpenCode on the host: `delegate-to-container` is the same `delegate.py` and
  container with OpenCode as the supervisor.
- OpenCode in the container: this skill is for Claude Code on the host.
- A task without the phrase: do it yourself as usual.
## Done means on main
The goal is the work merged into `main`, not a finished branch. Say "done" or
"merged" only when `main` contains the commit (step 7 prints "on main" on a
fresh run). Until then say "not on main yet" and print the commands that get
it there. For a later "is it merged?" load `site-fix-status`.
## Workflow
1. Name the task with a slug (lower case and dashes). The worker gets the new
   worktree `../site-agent-<slug>` on the branch `agent/<slug>` from `main`;
   `--worktree PATH` gives it an existing one. Tell the user in one line: slug,
   worktree, model.
1a. When the task needs a tool the image lacks, review the container first:
   read the Dockerfile of the image the task runs in
   (`container/containerfiles/Dockerfile.<flavour>.opencode`) and check what is
   there. Report the findings and suggested changes to the user, and do not
   start the worker or edit the Dockerfile until the user approves.
2. Write the brief under `/tmp/opencode/` (template in `delegate-to-container`).
   The worker starts cold: name the files, the skills to load (routing table of
   AGENTS.md), what done means and the checks it can run in the container.
3. Start it with Bash `run_in_background` and wait for the notification; do not
   poll: `python3 container/tools/ai/delegate.py start <slug> --brief <file>`
   Add a language flag when the task runs code in that language (full list in
   `delegate.py start --help`; PHP and Python need none), `--rw <path>` when the
   task edits outside `container/src` (tools, tests and docs are read-only
   otherwise), `-m <provider/model>` only when the user named a model. Exit 3
   means the model left the catalog: run
   `python3 container/tools/ai/check_opencode_models.py --check` and ask the
   user before you pick a paid model.
4. Review when it ends, on the host: `git -C <wt> status --short`,
   `git -C <wt> diff`, read new files; the `site-verify` checks against the
   worktree (`php -l`, the matching tests with the venv of the main checkout and
   `-p no:cacheprovider`); the AGENTS.md rules (version header, site values from
   the profile, no secrets); redirects checked offline with
   `htaccess_redirects.py match`; `pre-commit run --files <paths>` on Python the
   worker wrote, then remove `.mypy_cache` and `.ruff_cache` from the worktree.
   It does what the user asked, and nothing else.
5. Problems: write a fix file, one bullet per issue with `file:line`, what is
   wrong and what to do, and send it into the same session in the background:
   `python3 container/tools/ai/delegate.py fix <slug> --brief <fix-file>`.
   Do not repeat the task. At most 3 fix rounds; then report and ask the user.
6. A round that failed before doing the work (an `error:` line, a usage limit,
   `interrupted`): read the report, and if needed
   `python3 container/tools/opencode/agent_sessions.py show <session>`, fix the
   cause, then send one `fix` that says to continue. A free-model limit: ask the
   user before you switch to a paid model. A network drop (`ECONNRESET`,
   `getaddrinfo EAI_AGAIN`): send one `fix --message` that says so, names what
   exists, and says to check it and continue. Only `test -r` a key file, never
   read it.
7. Done: as step 7 of `delegate-to-container`: the changed paths, the checks and
   their results, the rounds, what the worker left for the host, then "not on
   main yet" or "on main" checked from the host, and the explicit-path commit
   and `merge --ff-only` commands for the user to run. Never commit, merge or
   remove the worktree yourself.

`delegate.py status` lists every task; `status <slug>` prints the last report
again. State and logs: `container/reports/delegate/<slug>/`.
## Deep reference
`python3 container/tools/ai/delegate.py --help`; `container/tools/ai/docker/CONTAINER.md`;
`delegate-to-container` (the OpenCode counterpart and the brief template).
