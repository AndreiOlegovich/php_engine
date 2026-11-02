# In the container

You run in a Docker container with --auto: every command runs without a
prompt. The container holds the tracked files of an agent worktree and
nothing else, so these replace some rules of AGENTS.md here:

- There is no git and no .git. Search with `grep -rn` or `rg` instead of
  `git grep`: there are no ignored files here for them to read.
- container/src is the docroot and is writable, and so is any path the run was started with
  `--rw`. AGENTS.md, the skills, container/docs, container/tools and
  container/tests are read-only otherwise.
- Files that read as empty and refuse writes (`*.env`, `*creds*`) are
  masked: say what should change in them, the edit is made on the host.
- There are no credentials here. Test the deploy scripts with `--dry-run`
  and a dummy creds file in /tmp, never against a real host.
- The local site, the tests that need it (`run-tests-venv.sh`) and commits
  are on the host. Do not try to reach them; list what is left to run on
  the host at the end of your answer.
- Scratch files go to /tmp.
