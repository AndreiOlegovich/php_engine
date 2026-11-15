# fortrancode-highlight

Colours Fortran code inside `<p class="fortrancode ...">` blocks of `.php`
article files, on the server side: the colour spans are written into the page
once, no script runs in the browser. Standard library only, so it runs on the
host without gfortran.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `fortrancode`. Other
   blocks (`cmd1`, `cout1`, `pycode`, `somecode`, inline `tt` spans) are left
   alone, so a Makefile in a `somecode` block is never coloured as Fortran.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`lightblue`/`yellow`/
   `violet`/`green`/`grey` palette is not skipped: those spans are dropped and
   the block is re-coloured into the canonical classes, exactly as
   `tools/pycode-highlight` re-highlights the old Python colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | Fortran | Class |
   |---|---|
   | keywords, statements and attributes (`program`, `implicit none`, `do`, `end`, `integer`, `print`, `use`) | `c5fd7ff` |
   | dot words (`.true.`, `.and.`, `.eq.`) | `c5fd7ff` |
   | strings (`'...'`, `"..."`) | `cad7fa8` |
   | comments (`! ...`, `!$omp ...`, `!$acc ...`) | `c999999` |
   | numbers (`42`, `1.0e-6`, `1.0d0`, `7_int32`, `b'1010'`) | `c34e2e2` |
   | intrinsic functions, only where they are called (`sum(a)`, `trim(s)`) | `cff5f00` |
   | names of variables, procedures and program units, operators, `::` | plain |

   Lower case only: `container/src/.css/site.css` define
   `.c5fd7ff` and the others in lower case, and class names are case
   sensitive, so the mixed case `c5FD7FF` of the older Python notes matches
   nothing in the sheets.

   The Ruby tool also uses `cfce94f` (symbols), `c4e9a06` (instance variables)
   and `cff0086` (the name after `def`); Fortran has no symbols and no
   instance variables, so those three classes have no counterpart here.
4. Each `<code>` element of a block is one line of Fortran (that is how the
   blocks get their line numbers). The lines are lexed together and coloured
   one by one, so a colour span never crosses a line.
5. Verifies every block: the text without tags, unescaped, equals the
   original text without tags, unescaped, byte for byte. Only `<code>` tags
   survive inside a block; a `<span>` is allowed and dropped (that is how the
   old palette goes away), while any other tag, a link above all, makes the
   block an error, because dropping it would silently lose markup. A file is
   written to a temporary file next to it and renamed into place, so CRLF
   line endings stay.

## The lexer

A small lexer of its own, not Pygments: the palette and the Fortran traps
below are specific to these articles, and Pygments is not in the standard
library.

- `!` starts a comment everywhere except inside a string, so `"a ! b"` stays a
  string. OpenMP and OpenACC directives (`!$omp parallel`, `!$acc kernels`)
  are comments to a compiler without the flag, so they get the comment colour.
- `'...'` and `"..."` are strings. A doubled quote inside the same kind of
  string is one quote (`'it''s'`), not the end of the string.
- A number may carry an exponent (`1.0e-6`, `1.0d0`, `1.0q0`) and a kind
  suffix (`7_int32`, `3.14_dp`), and BOZ literals (`b'1010'`) are numbers too.
- `.true.`, `.false.`, `.and.`, `.or.`, `.not.` and the old comparison forms
  `.eq.`, `.ne.`, `.lt.`, `.le.`, `.gt.`, `.ge.` are keywords; a lone dot is
  punctuation.
- Fortran is case insensitive, so `PROGRAM`, `Program` and `program` are the
  same keyword.
- An intrinsic name is coloured only where it is called: `sum(a)` is an
  intrinsic, while a variable called `count` or `sum` keeps the plain colour
  of every other name. `len` and `real` are keywords (a type and an attribute),
  so they are coloured as keywords wherever they appear.

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string, or a tag other than
`code` and `span` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/fortrancode-highlight/highlight_fortrancode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/fortrancode-highlight/highlight_fortrancode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every Fortran page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/fortrancode-highlight/tests -q
```

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
