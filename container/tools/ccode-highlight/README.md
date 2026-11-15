# ccode-highlight

Colours C code inside `<p class="ccode ...">` blocks of `.php` article
files, on the server side: the colour spans are written into the page once,
no script runs in the browser. Standard library only, so it runs on the host
without a C compiler.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `ccode`. Other
   blocks (`cmd1`, `cout1`, `somecode`, inline `tt` spans) are left alone.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`lightblue`/`yellow`/
   `violet` palette is not skipped: those spans are dropped and the block is
   re-coloured into the canonical classes, exactly as
   `tools/rubycode-highlight` re-highlights the old Ruby colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | C | Class |
   |---|---|
   | keywords and preprocessor directives (`int`, `for`, `struct`, `#include`, `#define`) | `c5fd7ff` |
   | strings, character constants, header names (`"text"`, `'\n'`, `<stdio.h>`) | `cad7fa8` |
   | comments (`// ...`, `/* ... */`) | `c999999` |
   | numbers (`42`, `0x1f`, `3.5f`, `1e-3`) | `c34e2e2` |
   | macros and upper-case constants (`MAX`, `NULL`, `EOF`, the name after `#define`) | `cff5f00` |
   | the name before `(` (a function or a call) | `cff0086` |
   | the member after `.` or `->` | `c4e9a06` |

   Lower case only: `container/src/.css/site.css` define
   `.c5fd7ff` and the others in lower case, and class names are case
   sensitive, so the mixed case `c5FD7FF` of the older notes matches
   nothing in the sheets.
4. Each `<code>` element of a block is one line of C (that is how the
   blocks get their line numbers). The lines are lexed together, because a
   `/* ... */` comment spans lines, and coloured one by one, so a colour
   span never crosses a line.
5. Verifies every block: the text without tags, unescaped, equals the
   original text without tags, unescaped, byte for byte. Only `<code>` tags
   survive inside a block; a `<span>` is allowed and dropped (that is how
   the old palette goes away), while any other tag, a link above all, makes
   the block an error, because dropping it would silently lose markup. A
   file is written to a temporary file next to it and renamed into place, so
   CRLF line endings stay.

## The lexer

A small lexer of its own, not Pygments: the palette and the C traps below
are specific to these articles, and Pygments is not in the standard library.

- `//` starts a comment to the end of the line and `/*` one to the next
  `*/`, also across lines; a comment marker inside a string is not a
  comment, and a `#` inside a string is not a directive.
- `"..."` and `'...'` read their escapes; the `L`, `u`, `U` and `u8`
  prefixes belong to the literal. An unterminated literal is an error.
- A `#` in the first column starts a preprocessor line: the `#` and the
  directive name are a keyword, a `<...>` after `#include` is a string, and
  the name after `#define` is a constant.
- Numbers accept the C suffixes and forms: `42`, `0x1f`, `0b101`, `0755`,
  `3.5f`, `1e-3`.
- An upper-case name is a constant (`MAX`, `NULL`, `EOF`); any other name
  before `(` is a function, and a name after `.` or `->` is a member.
- `struct`, `union` and `enum` are keywords and the tag after them is a
  constant (`struct Website`).

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string, character constant
or `/* comment`, or a tag other than `code` and `span` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/ccode-highlight/highlight_ccode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/ccode-highlight/highlight_ccode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every C page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/ccode-highlight/tests -q
```

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
