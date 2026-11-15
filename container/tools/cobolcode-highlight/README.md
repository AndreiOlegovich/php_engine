# cobolcode-highlight

Colours COBOL code inside `<p class="cobolcode ...">` blocks of `.php` article
files, on the server side: the colour spans are written into the page once,
no script runs in the browser. Standard library only, so it runs on the host
without a COBOL compiler.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `cobolcode`. Other
   blocks (`cmd1`, `cout1`, `shellscript`, `somecode`, inline `tt` spans) are
   left alone.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`lightblue` palette is not
   skipped: those spans are dropped and the block is re-coloured into the
   canonical classes, exactly as `tools/rubycode-highlight` re-highlights the
   old Ruby colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | COBOL | Class |
   |---|---|
   | verbs and reserved words (`MOVE`, `DIVISION`, `SECTION`, `PIC`, `VALUE`, `IF`, `END-IF`) | `c5fd7ff` |
   | string literals and `==pseudo-text==` of `COPY ... REPLACING` | `cad7fa8` |
   | comments (`*> ...`) | `c999999` |
   | numeric literals and level numbers (`01`, `78`, `9(3)`, `0.13`) | `c34e2e2` |
   | intrinsic function names after `FUNCTION` (`LENGTH`, `CURRENT-DATE`, `TRIM`) | `cff5f00` |
   | special registers (`RETURN-CODE`, `SQLCODE`, `COMMAND-LINE`) and `:host-variables` | `c4e9a06` |
   | the name after `PROGRAM-ID`, `CLASS-ID`, `METHOD-ID`, a section header and a paragraph header | `cff0086` |
   | data names, subscripts, operators, `EXEC` arguments | plain |

   Lower case only: `container/src/.css/site.css` define
   `.c5fd7ff` and the others in lower case, and class names are case
   sensitive, so the mixed case `c5FD7FF` of the older Python notes matches
   nothing in the sheets.
4. Each `<code>` element of a block is one line of COBOL (that is how the
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

A small lexer of its own, not Pygments: the palette and the COBOL traps below
are specific to these articles, and Pygments is not in the standard library.

- `*>` starts a comment everywhere except inside a string, so a literal
  `"*>"` stays a string.
- `"..."` and `'...'` are one string each; a doubled quote (`""`, `''`) is an
  escaped quote and does not end the literal.
- `==...==` is the pseudo-text of `COPY ... REPLACING` and is painted as a
  string, so `==EMP-NAME==` stays readable.
- A COBOL name may contain hyphens, so `WS-TOTAL-PAY`, `PROGRAM-ID` and
  `END-IF` are one token each, not a word and a minus sign.
- Level numbers (`01`, `05`, `78`, `88`), picture repetitions (`9(3)`) and
  decimal literals (`0.13`) are numbers.
- After the keyword `FUNCTION` the next name is an intrinsic function and gets
  the constant colour; `FUNCTION LENGTH(WS-NAME)` paints `LENGTH`, not
  `WS-NAME`.
- Special registers (`RETURN-CODE`, `SQLCODE`, `ARGUMENT-VALUE`, ...) get the
  variable colour, and so does a `:host-variable` in an `EXEC SQL` block.
- The name after `PROGRAM-ID`, `CLASS-ID` and `METHOD-ID` is a definition, and
  so is a paragraph header (`COMPUTE-TOTALS.`) or a section header
  (`INIT-SECTION SECTION.`) at the start of a line.
- `EXEC SQL` and `EXEC CICS` blocks are ordinary COBOL to this lexer: the SQL
  and CICS words in the keyword set are coloured, the host variables and data
  names stay plain.

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string or `==pseudo-text==`,
or a tag other than `code` and `span` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/cobolcode-highlight/highlight_cobolcode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/cobolcode-highlight/highlight_cobolcode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every COBOL page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/cobolcode-highlight/tests -q
```

Without pytest, run the tool on copies in `/tmp` and compare the check, write
and re-check exit codes, as the tests do.

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
