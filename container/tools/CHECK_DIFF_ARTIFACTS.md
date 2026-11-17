# Diff-artifacts check (`check-diff-artifacts.sh`)

Blocks the byte-level damage that scripted rewrites (sed and Python
read/write round trips, editors) leave in a diff, and nothing else. Every
rule compares the new side of a file with its base side, so damage that
was already in HEAD never fails a later commit.

## What blocks (`ARTIFACT`, exit 1)

| # | Rule | Example |
|---|---|---|
| 1 | line endings: CRLF lost in a CRLF file **together with other changes**, an LF file turned CRLF, or a file with one ending style left with mixed endings | Python text-mode write of a CRLF page that also edits it; lines pasted with LF into a CRLF file; a page moved from one folder to another that lost CRLF and changed a line |
| 2 | U+FFFD replacement chars introduced into an existing file | `errors='replace'` round trip of non-UTF-8 bytes |
| 3 | mojibake introduced (10 or more new double-encoded sequences) | UTF-8 read as cp1252/latin-1 (`é` turns into `Ã` + `©`, `—` into `â` `€` `”`) or cp1251 (`Привет` into `Р` `џ` `С` `Ђ` …) and saved again |
| 4 | a valid UTF-8 file no longer valid UTF-8 | a page saved as cp1251 or latin-1 |
| 5 | a UTF-8 BOM added to an existing file, or a new PHP file with a BOM | PHP prints the BOM before any header |

Rule 3 counts byte patterns that real English and Russian text never has
(the Cyrillic set leaves out pairs such as `С°` or `С…` that genuine text
has). Only new sequences count, and 10 are needed: a page that quotes one
mojibake word (2–6 sequences) passes, one damaged line of Russian (20 or
more) fails.

Rule 1 judges the style of the whole file: `crlf` (every line), `lf` (no
CR) or `mixed`. A file that was mixed before only fails when it was mostly
CRLF and becomes all LF. When CRLF is lost, the rule checks whether the new
file is exactly the base with the CR before each line break removed
(`sed 's/\r$//' base | cmp -s - new`). If so, the file was only converted
to LF and passes with a `NOTE`; any other byte that changed, even one space,
keeps the block. The direction is one way: LF to CRLF always blocks.

## What passes

- **A conversion to LF and nothing else**: printed as `NOTE: … converted
  from CRLF to LF, nothing else changed` (or `from mixed endings to LF`),
  also for a file that is moved at the same time. This is how the
  repository moves to LF: convert files in a change that only converts them,
  edit them in another.
- **Whitespace-only diffs** (indentation, trailing spaces, a run of spaces):
  printed as `NOTE: … whitespace-only change`, exit stays 0. The check uses
  `git diff -b`, so a space added or removed inside a line ("Java Script" →
  "JavaScript") is a real change and gets no note.
- **1:1 rewrites of many lines**: translations, a path renamed on every
  line, a regenerated `sitemap.xml`. Not reported. The old rule 4 ("added
  == deleted on files > 100 lines") stood for "possible EOL flip or
  encoding damage"; rules 1–4 now catch those directly.
- Notes, not blocking: a BOM removed, stray CRs removed from a mostly-LF
  file, and a new file with mixed endings, U+FFFD (a page may show it on
  purpose) or a BOM (vendored files ship with one; a new PHP file still
  fails). A new file has no base to be damaged against.
- The rolling feed files `container/src/feed/feed.json` and `arch.json`
  skip rule 1 and the whitespace note (the feed writer serializes them
  whole); the encoding rules still apply.
- Generated CSS concatenations under `container/css_pipeline/build/` take
  their line endings from their source partials (some CRLF, some LF), which
  are checked themselves: a rule 1 hit there is a `NOTE`.
- Skipped: untracked, deleted, binary (git's NUL test), symlink and
  submodule entries.

Renamed and copied files are compared with their source, so a move that
loses CRLF and changes the file fails, and a move that only converts it to
LF passes with a note (the old script skipped renames). A move
whose content changed too much for git's rename detection shows up as a
new file and only gets the new-file checks.

## Usage

All commands run from anywhere (the script `cd`s to the repo root):

```bash
container/tools/check-diff-artifacts.sh                 # unstaged: index -> worktree
container/tools/check-diff-artifacts.sh --staged        # HEAD -> index (what pre-commit gates)
container/tools/check-diff-artifacts.sh --all           # HEAD -> worktree, staged and unstaged
container/tools/check-diff-artifacts.sh --commit <rev>  # an existing commit vs its first parent
```

Exit code: `0` = clean (notes allowed), `1` = artifacts found, `2` = usage
error. Wired into `.pre-commit-config.yaml` (`check-diff-artifacts
(staged)`, `always_run`, blocks the commit). Needs bash, git, GNU grep
(`-P` for the mojibake rule) and iconv (rule 4); without one of the last
two that rule is skipped with a message on stderr.

## Reading the report

```text
ARTIFACT: container/src/section/sum.js (from container/src/old/sum.js) [staged]: CRLF line endings lost (base CRLF on all 3 lines, now LF) together with other changes; a conversion alone passes
NOTE: container/src/section/array_sort.php [staged]: converted from CRLF to LF, nothing else changed (212 lines)
NOTE: container/src/web/css/x.php [staged]: whitespace-only change (12+/12-: indentation, trailing spaces), not blocking
check-diff-artifacts: 1 artifact(s). Fix them, or audit and skip this hook only: container/tools/CHECK_DIFF_ARTIFACTS.md
```

A CRLF loss with edits has two fixes: give the edited file its CRLF back
with `container/tools/linters/check-line-endings.sh --fix --to crlf --files …`
and commit the edit, or commit the conversion first (convert the base
version to LF, `sed -i 's/\r$//'`, commit) and the edit after it. Mixed
endings: `--to crlf` or `--to lf` to match the base. Re-stage and re-run
until `check-diff-artifacts: clean`. Encoding damage: restore the file from the
base (`git restore --source=HEAD -- <path>`) and redo the edit in binary
or UTF-8 mode.

## When a hit is intended

A conversion to LF needs no skip. What is left is rare: a file converted to
CRLF on purpose (a Windows batch file), a repair of damage that is already
pushed, or a long mojibake sample quoted in an article. Audit, then skip
this hook alone, never the whole suite:

```bash
SKIP=check-diff-artifacts git commit -m "..." -m "Committed with SKIP=check-diff-artifacts: <rule> is intended because ..."
```

## Calibration

Replayed with `--commit` over the 1,174 commits of `main` up to
`619c84f74` (2026-11-17).

- Old rules (0.0.4): 16 commits flagged. 2 were real CRLF losses
  (`2af4d0554`, 332 files; `a402d2992`); 14 were false positives
  (translations, `sitemap.xml` lastmod refreshes, path fixes, an intended
  whitespace edit), each needing an audited `SKIP`.
- New rules: none of the 14 is flagged. Both real losses still are:
  `2af4d0554` with 275 files that lost CRLF while the formatter edited
  them (its 53 files that only changed line endings and 4 mixed dumps
  normalised to LF are notes), and `a402d2992`. One loss the old script
  missed because it skipped renames is caught: `45e2c4d58` moved six Mocha
  files, changed them and turned them from CRLF to LF. `8d889c516` and
  `b68ebc079` are flagged for generated CSS that went mixed at its old
  paths (`src/.css/output/`, `src/.css/source/core/`); the same case under
  today's `css_pipeline/build/` is a note.
- The initial import (`029c88b25`, 9,155 new files) and the libwebp copy
  (`c59c3ebe6`) give notes only: pages that show U+FFFD on purpose and
  vendored READMEs with a BOM.

## Relation to `linters/check-line-endings.sh`

`CHECK_LINE_ENDINGS.md` documents the absolute-state check (file endings
vs LF/CRLF target, with `--fix`). This tool is the diff-oriented
complement: it only looks at what changed against the base, so
pre-existing CRLF files elsewhere in the tree never raise noise here.

## Files

```text
check-diff-artifacts.sh                           the script
CHECK_DIFF_ARTIFACTS.md                           this doc
../tests/linters/test_check_diff_artifacts_sh.py  self-test in scratch repos
```
