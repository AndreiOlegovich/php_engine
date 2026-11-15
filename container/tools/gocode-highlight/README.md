# gocode-highlight

Colours Go code inside `<p class="gocode ...">` blocks of `.php` article
files, on the server side: the colour spans are written into the page once,
no script runs in the browser. Standard library only, so it runs on the host
without a Go toolchain.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `gocode`. Other
   blocks (`cmd1`, `cout1`, `somecode`, inline `span.cmd-line`) are left
   alone.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`yellow`/`lightblue`/
   `green` palette is not skipped: those spans are dropped and the block is
   re-coloured into the canonical classes, exactly as
   `tools/rubycode-highlight` re-highlights the old Ruby colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | Go | Class |
   |---|---|
   | keywords (`package`, `func`, `var`, `if`, `range`, `return`, `nil`, `true`) | `c5fd7ff` |
   | strings, raw strings and rune literals (`"..."`, `` `...` ``, `'a'`) | `cad7fa8` |
   | comments (`// ...`, `/* ... */`) | `c999999` |
   | numbers (`42`, `3.5`, `0x1f`, `0b101`, `1_000`, `2i`) | `c34e2e2` |
   | predeclared types (`int`, `string`, `error`, `any`) | `c4e9a06` |
   | exported names (`Println`, `Server`, `T`) and other capitals | `cff5f00` |
   | the name after `func`, methods included (`func (s *Server) Start`) | `cff0086` |
   | local names, calls, operators, arguments | plain |

   Lower case only: `container/src/.css/site.css` define
   `.c5fd7ff` and the others in lower case, and class names are case
   sensitive, so the mixed case `c5FD7FF` of the older Python notes matches
   nothing in the sheets.
4. Each `<code>` element of a block is one line of Go (that is how the
   blocks get their line numbers). The lines are lexed together, because a
   raw string and a `/* */` comment span lines, and coloured one by one, so
   a colour span never crosses a line.
5. Verifies every block: the text without tags, unescaped, equals the
   original text without tags, unescaped, byte for byte. Only `<code>` tags
   survive inside a block; a `<span>` is allowed and dropped (that is how
   the old palette goes away), while any other tag, a link above all, makes
   the block an error, because dropping it would silently lose markup. A
   file is written to a temporary file next to it and renamed into place, so
   CRLF line endings stay.

## The lexer

A small Go lexer of its own, not Pygments: the palette and the Go traps
below are specific to these articles, and Pygments is not in the standard
library.

- `//` starts a comment to the end of the line, and `/*` a comment up to
  `*/`, but only outside a string: `"https://example.com"` stays a string.
- `"..."` reads backslash escapes; it cannot span a line, so an unterminated
  one is an error. `` `...` `` is a raw string: no escapes, and it may span
  lines.
- `'...'` is a rune literal, with the same backslash escapes, so `'\''` and
  `'\n'` colour as one string each.
- Numbers cover decimal and float forms, `0x`/`0b`/`0o` bases, `_` digit
  separators, exponents, hex floats, and the imaginary `i` suffix
  (`2i`, `1.5i`).
- The name after `func` is a function name, also for methods: after
  `func (` the lexer walks the receiver parentheses to its closing `)` and
  colours the next identifier. An anonymous `func(` has no name, and the
  next identifier after its `)` is not touched.
- Predeclared type names (`int`, `string`, `error`, `any`, `byte`, `rune`,
  ...) get their own class; any other identifier starting with a capital
  letter is an exported name and gets the constant class.
- `true`, `false`, `nil` and `iota` are predeclared identifiers, but they
  colour as keywords, as Ruby's `true`/`false`/`nil` do in the Ruby tool.

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string, raw string, rune
or block comment, or a tag other than `code` and `span` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/gocode-highlight/highlight_gocode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/gocode-highlight/highlight_gocode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every Go page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/gocode-highlight/tests -q
```

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
