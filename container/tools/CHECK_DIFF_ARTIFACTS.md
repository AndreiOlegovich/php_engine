# Diff-artifacts check (`check-diff-artifacts.sh`)

Catches unexpected diff artifacts vs HEAD before they get committed —
the kind scripted rewrites (sed/python bulk edits) silently introduce:

| # | Check | Example |
|---|---|---|
| 1 | whitespace/EOL-only diff (`git diff` non-empty, `git diff -w` empty) | CRLF→LF flip rewriting every line, as happened to `html/symbols/sedYvetIK` |
| 2 | CRLF line endings lost (HEAD blob has CR, worktree file does not) | same flip, flagged directly |
| 3 | U+FFFD replacement chars introduced (read-as-utf8/write damage) | non-UTF-8 bytes mangled by `errors='replace'` round-trips |
| 4 | full-file rewrites (added == deleted on files > 100 lines) | suspicious churn worth a second look |

Pure host-side check: bash + git + grep, no Docker, no installs.
Untracked files are skipped (nothing to compare against).

## Usage

All commands run from anywhere (the script `cd`s to the repo root):

```bash
container/tools/check-diff-artifacts.sh            # unstaged worktree files
container/tools/check-diff-artifacts.sh --staged   # index (what pre-commit gates)
container/tools/check-diff-artifacts.sh --all      # both
```

Exit code: `0` = clean, `1` = artifacts found (prints one `ARTIFACT: …`
line per hit). Wired into `.pre-commit-config.yaml`
(`check-diff-artifacts (staged)`, `always_run`, blocks the commit).

## Reading the report

```text
ARTIFACT: container/src/dev/js/mocha_testpage2.html [worktree]: whitespace/EOL-only diff (37+/ 37-, invisible under -w)
ARTIFACT: container/src/dev/js/mocha_testpage2.html [worktree]: CRLF line endings lost (HEAD blob has CR)
```

Fix the flagged file (e.g. restore CRLF with
`container/tools/linters/check-line-endings.sh --fix --to crlf --files …`),
then re-run until `check-diff-artifacts: clean`.

## Relation to `linters/check-line-endings.sh`

`CHECK_LINE_ENDINGS.md` documents the absolute-state check (file endings
vs LF/CRLF target, with `--fix`). This tool is the diff-oriented
complement: it only looks at what changed vs HEAD, so pre-existing
CRLF files elsewhere in the tree never raise noise here.

## Files

```text
check-diff-artifacts.sh   the script
CHECK_DIFF_ARTIFACTS.md   this doc
```
