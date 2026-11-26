---
name: delegate-to-container
description: "The user says \"delegate to container ...\" (or \"give it to the container\"): you are OpenCode on the host as supervisor, an OpenCode worker in Docker does the work through container/tools/ai/delegate.py"
---
## Use when
The message starts with "delegate to container" or "give it to the container",
or the user runs `/delegate-to-container <task>`. You are OpenCode on the host
and the supervisor: you write the brief, start the worker, review its result
on the host and send fixes. You do not make the change yourself, not even a
small part of it, and you do not hand it to a local subagent: only the Docker
worker gets the isolated worktree and the fixed model.
## Do not use for
- Claude Code on the host: `assign-to-opencode` is the same loop with Claude
  Code as the supervisor.
- OpenCode inside the container: the worker follows
  `container/tools/ai/docker/CONTAINER.md`; there is no Docker or git there.
- A task without the phrase: do it yourself as usual.
## Done means on main
The goal is the work merged into `main`, not a finished branch. Say "done" or
"merged" only when `main` contains the commit (step 7 prints "on main" on a
fresh run). Until then say "not on main yet" and print the commands that get
it there. For a later "is it merged?" load `site-fix-status`.
## Workflow
1. Name the task with a slug (lower case and dashes). The worker gets the new
   worktree `../<project>-agent-<slug>` on the branch `agent/<slug>` from `main`;
   `--worktree PATH` gives it an existing one. Tell the user in one line: slug,
   worktree, model. The worktree starts from `main`: uncommitted host changes
   are not in it.
2. Write the brief under `/tmp/opencode/` (template below). The worker starts
   cold: name the files, the skills to load, what done means and the checks it
   can run in the container.
3. Start it detached, then wait. A round takes 10-30 minutes and the shell
   tool stops a command after 120000 ms unless the call sets a longer timeout,
   so a plain `start` would leave the round `interrupted`:
   `python3 container/tools/ai/delegate.py start <slug> --detach --brief /tmp/opencode/brief-<slug>.md`
   It returns once the round runs in the background. Write the options in
   full (`--det` is refused). Call `wait` only after exit 0:
   `python3 container/tools/ai/delegate.py wait <slug>` blocks up to 9 minutes
   and prints the report when the round ends (exit 0 done, 1 failed, 4 still
   running: call it again and do nothing else on the task). Give the shell
   tool a timeout of 600000 ms for `wait`.
   Add a language flag when the task runs code in that language (`--ruby`,
   `--erlang`, `--rust`, `--java`, `--typescript` and others: the full list is
   in `delegate.py start --help`; PHP and Python need none), `--rw <path>` when
   the task edits outside `container/src` (tools, tests and docs are read-only
   otherwise), `-m <provider/model>` only when the user named a model. Exit 3
   means the model left the catalog: run
   `python3 container/tools/ai/check_opencode_models.py --check` and ask the
   user before you pick a paid model (the free-limit switch of step 6 is the
   one exception). A paid model needs `--opencode-key` or
   `--openrouter-key` with a file outside the repo.
   The worktree is outside the project: `opencode.jsonc` lets you read it
   unasked, and an edit there asks, because the worker makes the change.
4. Review when it ends. The report shows the status, errors, the worker's last
   answer and the changed paths. Then, on the host:
   - `git -C <wt> status --short` and `git -C <wt> diff`; read new files.
   - The `site-verify` checks against the worktree: `php -l` on the touched
     PHP and the matching tests. Run tests with the venv of the main checkout
     and no cache, so the worktree gets no ignored files:
     `cd <wt>/container && <main>/container/.venv/bin/python -m pytest -p no:cacheprovider <tests> -q`
   - The AGENTS.md rules: version header bumped in every changed file, site
     values read from the profile, no secrets.
   - Redirects: the redirect test asks the local site, which serves the main
     checkout, so check rules offline in a worktree with
     `python3 container/tools/htaccess_redirects.py match container/src/.htaccess /old/url.php ...`
     (exit 1 means "matched": read the output).
   - Python written by the worker: run `pre-commit run --files <paths>` on the
     worktree before you hand over commit commands, then remove `.mypy_cache`
     and `.ruff_cache` from it.
   - Images: they are git-ignored, so `git status` and the diff never show
     the banners or pictures a worker made. List them with
     `python3 container/tools/ai/copy_worktree_images.py <wt> --dry-run`
     and check that each image the pages reference exists (step 7 copies
     them).
   - It does what the user asked, and nothing else.
5. Problems: write a fix file under `/tmp/opencode/`, one bullet per issue with
   `file:line`, what is wrong and what to do, and send it into the same
   session, detached, then wait as in step 3:
   `python3 container/tools/ai/delegate.py fix <slug> --detach --brief /tmp/opencode/fix-1-<slug>.md`
   The worker keeps its context, so do not repeat the task. At most 3 fix
   rounds; after that report what is still wrong and ask the user.
6. A round that failed before doing the work (an `error:` line, a usage limit,
   "Background service failed to start", `interrupted`): read the report and,
   if needed, `python3 container/tools/opencode/agent_sessions.py show <session>`,
   fix the cause, then send one `fix` that says to continue. The free model
   hit its limit (`error: provider.free-limit` with an `opencode/<id>-free`
   model): switch to OpenCode Go at once, without asking. List the live Go
   ids in the container (`--standalone`, or `opencode models` fails with
   "plugins did not finish activating"):
   `container/tools/ai/opencode-docker.sh --opencode-key ~/.config/opencode-agent/opencode.key <wt> shell -c 'opencode models --standalone | grep "^opencode-go/"'`
   Take the same model as `opencode-go/<id>` without `-free`
   (`opencode/deepseek-v4.1-flash-free` becomes
   `opencode-go/deepseek-v4.1-flash`). Not listed: the most similar Go model,
   first the same family, then the same size class (a flash or mini model for
   a flash one), never a larger tier such as `-pro` or `-max`. Then send the
   continue round with the model and the key:
   `python3 container/tools/ai/delegate.py fix <slug> --detach -m opencode-go/<id> --opencode-key ~/.config/opencode-agent/opencode.key --message "<continue where you stopped>"`
   and wait as in step 3. `-m` holds for the later rounds too. Tell the user
   in one line which model you switched to and why. When the free limit was
   hit earlier that day, start a long task on the Go model at once.
   A detached round that ends `interrupted` with no error: the traceback is
   at the end of `container/reports/delegate/<slug>/detach.log`. `ECONNRESET` or
   `getaddrinfo EAI_AGAIN` is a network drop: send one `fix --message` that
   says so, names what exists, and says to check it and continue.
7. Done: tell the user what changed, the checks and their results, the rounds,
   and what the worker left for the host. First line of the report, checked
   from the host: `git -C <wt> status --short` and
   `git -C <main> log --oneline main..agent/<slug>`; anything listed is "not on
   main yet", with the branch and the worktree. Print the commands for the
   user to run (explicit paths, no `git add -A`, no hook bypass, no
   attribution line):
   ```
   git -C <wt> add <paths>
   git -C <wt> commit -m "<subject>"
   git -C <main> status --short        # must be clean: commit or stash first
   git -C <main> merge --ff-only agent/<slug>
   git -C <main> merge-base --is-ancestor agent/<slug> main && echo "on main"
   ```
   If the merge refuses because `main` moved, print `git -C <wt> rebase main`
   before the merge line (it needs the work committed first). Do not
   regenerate the sitemap in the worktree: every page would get a new date.
   Images the dry run of step 4 listed: the merge carries the pages that use
   them but not the images, and the deploy uploads the images of the main
   checkout, so the site would answer 404. Print after the merge line:
   ```
   python3 container/tools/ai/copy_worktree_images.py <wt> --dry-run
   python3 container/tools/ai/copy_worktree_images.py <wt>
   ```
   Exit 1 means an image of another size already exists in the main
   checkout: ask the user, then `--overwrite` if the worker's one is wanted.
   The next commit in the main checkout gets `container/docs/image-files.txt`
   rewritten by the image-list hook; that file goes into the commit.
   Clean up (`git worktree remove <wt>`, `git branch -d agent/<slug>`) only
   after the last line prints "on main" and the image copy, if any, ended
   with 0 differ: removing the worktree deletes the images not copied yet.
   Never commit, merge or remove the worktree yourself: the user runs the
   commands.

`delegate.py status` lists every task; `status <slug>` prints the last report
again. State and logs: `container/reports/delegate/<slug>/`.
## Long jobs
A worker session runs out of context. For a job of many pages put a bounded
batch in one round; when a session has done a lot, start a NEW session on the
same worktree (`delegate.py start <new-slug> --detach --worktree <path>` with
the same flags). Its brief stands alone: the state of the work, an accepted
page to copy the format from, the rules in full. Tell the worker to finish
each page completely, checks included, before the next one.
## Brief template
```
# Task: <one line>
<what and why in 2-5 lines; the user's words where they matter>

## Files
- edit or create: <paths>
- read first: <paths>

## Load first
<skills from the AGENTS.md routing table>

## Done when
- <checkable outcomes>
- the version header is bumped in every changed file

## Checks you can run here
- php -l <files>; other checks that need neither git nor the local site

## Out of scope
- commits (there is no git), the sitemap, anything outside the files above

End with: the files you changed, the checks you ran and their result, and
what is left to run on the host.
```
## Deep reference
`python3 container/tools/ai/delegate.py --help`; the header of
`container/tools/ai/opencode-docker.sh`; `container/tools/ai/docker/CONTAINER.md`;
`assign-to-opencode` (the Claude Code counterpart).
