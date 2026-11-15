# rubycode-highlight

Colours Ruby code inside `<p class="rubycode ...">` blocks of `.php` article
files, on the server side: the colour spans are written into the page once,
no script runs in the browser. Standard library only, so it runs on the host
without Ruby.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `rubycode`. Other
   blocks (`cmd1`, `cout1`, `pycode`, `somecode`, inline `tt` spans) are left
   alone.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`lightblue`/`yellow`/
   `violet` palette is not skipped: those spans are dropped and the block is
   re-coloured into the canonical classes, exactly as
   `tools/pycode-highlight` re-highlights the old Python colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | Ruby | Class |
   |---|---|
   | keywords (`def`, `end`, `if`, `do`, `class`, `nil`, `true`) | `c5fd7ff` |
   | strings, heredocs, `%w[]`/`%i[]`/`%q()`, `/regex/` literals | `cad7fa8` |
   | comments (`# ...`, `=begin`..`=end`) | `c999999` |
   | symbols (`:admin`, `:"a b"`, `:+`) | `cfce94f` |
   | numbers (`42`, `1_000`, `3.5`, `0x1f`) | `c34e2e2` |
   | constants (`ENV`, `LIMIT`, `__FILE__`, `Port:` of a hash) | `cff5f00` |
   | instance and global variables (`@name`, `@@count`, `$stdout`, `$1`) | `c4e9a06` |
   | the method name after `def` (also `def <=>`, `def self.read`) | `cff0086` |
   | local variables, method calls, operators, arguments | plain |

   Lower case only: `container/src/.css/site.css` define
   `.c5fd7ff` and the others in lower case, and class names are case
   sensitive, so the mixed case `c5FD7FF` of the older Python notes matches
   nothing in the sheets.
4. Each `<code>` element of a block is one line of Ruby (that is how the
   blocks get their line numbers). The lines are lexed together, because a
   heredoc and `=begin` span lines, and coloured one by one, so a colour span
   never crosses a line.
5. Verifies every block: the text without tags, unescaped, equals the
   original text without tags, unescaped, byte for byte. Only `<code>` tags
   survive inside a block; a `<span>` is allowed and dropped (that is how the
   old palette goes away), while any other tag, a link above all, makes the
   block an error, because dropping it would silently lose markup. A file is
   written to a temporary file next to it and renamed into place, so CRLF
   line endings stay.

## The lexer

A small lexer of its own, not Pygments: the palette and the Ruby traps below
are specific to these articles, and Pygments is not in the standard library.

- `#` starts a comment everywhere except inside a string or a `%` literal, so
  `"#f00"` stays a string.
- `"..."` reads `#{...}` interpolation, braces and quotes inside it included,
  and paints the whole literal as one string; `'...'` has no interpolation.
- `%w[]`, `%W[]`, `%i[]`, `%I[]`, `%q()`, `%Q{}`, `%x<>`, `%r//` and `%()`
  are one string each; paired delimiters nest, and `%` after a value
  (`a % b`, `x %= 2`) stays the modulo operator.
- Heredocs: `<<EOS`, `<<-EOS`, `<<~EOS` and the quoted `<<'EOS'`/`<<"EOS"`
  forms. The marker and the body up to the closing delimiter are a string; a
  heredoc body starts on the next line, and several heredocs may start on one
  line. `<<` followed by anything else (`results << square`) is the append
  operator.
- A symbol is `:` followed at once by a name, a quote or an operator
  character: `:admin`, `:"a b"`, `:+`, `:[]`. A label (`name: "Ada"`) and the
  colon of `a ? b : c` are not symbols and stay plain.
- `/` starts a regex literal unless an expression value comes before it, so
  `s =~ /[0-9]+/` is a regex and `7 / 2` is division. Brackets inside the
  regex are skipped, and the trailing flags are part of the literal.
- `=begin` and `=end` are block comments, and `__END__` ends the code, but
  only at column 0, as in Ruby.

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string, regex, `%` literal,
heredoc or `=begin`, an `=end` without `=begin`, or a tag other than `code`
and `span` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/rubycode-highlight/highlight_rubycode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/rubycode-highlight/highlight_rubycode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every Ruby page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/rubycode-highlight/tests -q
```

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
