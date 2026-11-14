# haskellcode-highlight

Colours Haskell code inside `<p class="haskellcode ...">` blocks of `.php`
article files, on the server side: the colour spans are written into the page
once, no script runs in the browser. Standard library only, so it runs on the
host without GHC.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `haskellcode`. Other
   blocks (`cmd1`, `cout1`, `pycode`, `somecode`, inline `tt` spans) are left
   alone.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`yellow`/`violet`/
   `green` palette is not skipped: those spans are dropped and the block is
   re-coloured into the canonical classes, exactly as
   `tools/pycode-highlight` re-highlights the old Python colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | Haskell | Class |
   |---|---|
   | keywords (`module`, `where`, `data`, `class`, `instance`, `let`, `do`, `case`, `of`, `if`, `then`, `else`) and `{-# ... #-}` pragmas | `c5fd7ff` |
   | strings and character literals (`"hi"`, `'a'`, `'\n'`) | `cad7fa8` |
   | comments (`-- ...`, `{- ... -}`) | `c999999` |
   | numbers (`42`, `1_000`, `3.5`, `0x1f`) | `c34e2e2` |
   | type and constructor names (`Int`, `Maybe`, `Just`, `True`) | `cff5f00` |
   | the name of a top-level definition (`add ::`, `add a b =`) | `cff0086` |
   | local variables, function calls, operators, arguments | plain |

   Lower case only: the served sheets define `.c5fd7ff` and the others in
   lower case, and class names are case sensitive, so the mixed case
   `c5FD7FF` of some older notes matches nothing in the sheets.
4. Each `<code>` element of a block is one line of Haskell (that is how the
   blocks get their line numbers). The lines are lexed together, because a
   block comment spans lines, and coloured one by one, so a colour span
   never crosses a line.
5. Verifies every block: the text without tags, unescaped, equals the
   original text without tags, unescaped, byte for byte. Only `<code>` tags
   survive inside a block; a `<span>` is allowed and dropped (that is how the
   old palette goes away), while any other tag, a link above all, makes the
   block an error, because dropping it would silently lose markup. A file is
   written to a temporary file next to it and renamed into place, so CRLF
   line endings stay.

## The lexer

A small lexer of its own, not Pygments: the palette and the Haskell traps
below are specific to these articles, and Pygments is not in the standard
library.

- `--` starts a line comment, except when the dashes are part of an operator:
  `-->` and `--|` stay operators.
- `{- ... -}` is a block comment and nests, so a `{-` inside one opens a
  level. `{-# ... #-}` (a pragma such as `LANGUAGE BangPatterns`) is a
  directive and gets the keyword colour.
- `"..."` reads backslash escapes and the backslash-newline gap, so `--`
  and `{-` inside a string stay a string.
- A character literal is `'c'`, `'\n'` or `'\''`; a prime that follows a
  name (`foldl'`, `x'`) belongs to the name and does not open a literal.
- Numbers cover decimal, hexadecimal, octal and binary forms, underscore
  separators, fractions and exponents.
- An upper-case identifier is a type or constructor name. The first name of
  a line at column 0 is a definition name when the line is a signature
  (`add ::`) or an equation (`add a b =`), and `(<.>) ::` colours the
  operator the same way.

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string, character literal
or `{-` comment, or a tag other than `code` and `span` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/haskellcode-highlight/highlight_haskellcode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/haskellcode-highlight/highlight_haskellcode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every Haskell page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/haskellcode-highlight/tests -q
```

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
