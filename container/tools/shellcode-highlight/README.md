# shellcode-highlight

Colours shell commands inside `.php` article files with the site
palette, on the server side: the colour spans are written into the page
once, no script runs in the browser.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `cmd1` (one
   command per block) or `shellscript` (a script). Output blocks (`cout1`)
   and the light inline `span.cmd-line` are left alone: the palette is
   made for the dark background of `cmd1` and `shellscript`.
2. Skips blocks already highlighted with `<span class="cXXXXXX">`.
3. Colours the rest with the palette of the Python and JSON blocks
   (`tools/pycode-highlight`, `tools/jsoncode-highlight`) plus the grey of
   `source/_colors.css`, in lower case: the built sheets define these
   classes only in lower case, and class names are case-sensitive.

   | Shell | Class |
   |---|---|
   | command names (`docker`, `git`, `jq`) and keywords (`for`, `do`, `if`) | `c5fd7ff` |
   | options (`-s`, `--auto`, `--format` of `--format=%s`) | `cfce94f` |
   | quoted strings, here-documents | `cad7fa8` |
   | variables (`$c`, `${HOME}`, the name of `NAME=value`, a loop name) | `c34e2e2` |
   | comments | `c999999` |
   | arguments, subcommands, `\|`, `;`, `&&`, `>` | plain |

4. Keeps every tag already in a block (links) and every entity as
   written. A colour span is split where such a tag falls, so tags
   always nest.
5. Verifies each block: removing the added colour spans gives back the
   original inner HTML byte for byte. A file is written to a temporary
   file next to it and renamed into place; CRLF line endings stay.

## The lexer

A small lexer of its own, not Pygments: the Pygments bash lexer marks
neither the command name nor the options, and colours `exec` in
`docker exec` as a builtin. This one knows where a command starts:

- at the start of a line, and after `|`, `||`, `&&`, `;`, `&`, `(`,
  `$(`, `` ` `` and `<(`;
- after the keywords that take a command: `if`, `then`, `else`, `elif`,
  `do`, `while`, `until`, `!`, `{`, `time`;
- after the prefix commands `sudo`, `env`, `exec`, `xargs`, `nohup`,
  `command`, `builtin`, `nice` and `doas`, past their options and
  `NAME=value` assignments.

It also reads `case` patterns, `for` and `select` loops, `name() { ... }`
functions, `NAME=(...)` arrays, `$(( ))`, `${...}`, `$'...'`,
redirections such as `2>&1` and here-documents. A word of letters in
angle brackets, such as `<task>` or `<id>`, is a placeholder of the
article, not a redirection.

## Use

Standard library only. From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/shellcode-highlight/highlight_shellcode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/shellcode-highlight/highlight_shellcode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do.
Run it on the pages you are working on: a directory run over all of
`container/src` rewrites more than 1,600 pages, and the post-commit feed
hook then records every one of them as modified.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/shellcode-highlight/tests -q
```
