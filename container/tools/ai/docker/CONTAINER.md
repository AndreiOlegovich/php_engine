# In the container

You run in a Docker container with --auto: every command runs without a
prompt. The container holds the tracked files of an agent worktree and
nothing else, so these replace some rules of AGENTS.md here:

- There is no git and no .git. Search with `grep -rn` or `rg` instead of
  `git grep`: there are no ignored files here for them to read.
- container/src is writable, and so is any path the run was started with
  `--rw`. AGENTS.md, the skills, container/docs, container/tools and
  container/tests are read-only otherwise.
- Files that read as empty and refuse writes (`*.env`, `*creds*`) are
  masked: say what should change in them, the edit is made on the host.
- There are no credentials here. Test the deploy scripts with `--dry-run`
  and a dummy creds file in /tmp, never against a real host.
- The local site, the tests that need it (`run-tests-venv.sh`) and commits
  are on the host. Do not try to reach them; list what is left to run on
  the host at the end of your answer.
- Do not run generate-sitemap.sh, even after adding pages: it dates every
  page by its file time, and here every file has the time of the checkout.
  The sitemap is rebuilt on the host after the merge.
- Image files are not in the container: `container/docs/image-files.txt`
  lists the ones on the host (path, size, sha1), and `test_img_src.py`
  reads it.
- Scratch files go to /tmp.

## Lesson pages with code or questions

Before you report a lesson page you wrote or changed, run both checks on it:

```bash
python3 container/tools/course_check/code_blocks.py container/src/dev/rust/course/011_ownership.php --lang rust
python3 container/tools/course_check/quiz_answers.py container/src/dev/rust/course/011_ownership.php --lang rust
```

`--lang` is the language of the course. A `FAIL`, `WRONG`, `REFERS` or
`STRUCT` line is yours to fix; `SKIP` and `UNMATCHED` are usually a limit of
the tool, so read them before you change anything. The manual, with the
commands for a whole course and for another language, is
`container/docs/course-checks.md`.

## Native image programs

The native image (`Dockerfile.native.opencode`) has the compilers and these
tools: `cargo clippy`, `cargo fmt`, `maturin`, `patchelf`, `valgrind`,
`strace` and `hyperfine`.
