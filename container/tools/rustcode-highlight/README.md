# rustcode-highlight

Colours Rust code inside `<p class="rustcode ...">` blocks of `.php` article
files, on the server side: the colour spans are written into the page once,
no script runs in the browser. Standard library only, so it runs on the host
without Rust.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `rustcode`. Other
   blocks (`cmd1`, `cout1`, `somecode`, inline `tt` spans) are left alone.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`lightblue`/`yellow`
   palette is not skipped: those spans are dropped and the block is
   re-coloured into the canonical classes, exactly as
   `tools/pycode-highlight` re-highlights the old Python colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | Rust | Class |
   |---|---|
   | keywords (`fn`, `let`, `if`, `match`, `impl`, `mut`, `self`) | `c5fd7ff` |
   | strings, char literals, raw and byte strings (`"hi"`, `'x'`, `r#"..."#`) | `cad7fa8` |
   | comments (`// ...`, `/* ... */`, doc comments) | `c999999` |
   | attributes and lifetimes (`#[test]`, `#[derive(Debug)]`, `'a`, `'static`) | `cfce94f` |
   | numbers (`42`, `1_000`, `3.5`, `0x1f`, `42u8`) | `c34e2e2` |
   | type names and constants (`String`, `Vec`, `i32`, `Some`, `LIMIT`) | `cff5f00` |
   | macro names (`println!`, `vec!`, `write!`, `assert_eq!`) | `c4e9a06` |
   | the name after `fn` (also `fn fmt`) | `cff0086` |
   | local variables, methods, operators, arguments | plain |

   Lower case only: `container/src/.css/site.css` define
   `.c5fd7ff` and the others in lower case, and class names are case
   sensitive, so the mixed case `c5FD7FF` of the older Python notes matches
   nothing in the sheets.
4. Each `<code>` element of a block is one line of Rust (that is how the
   blocks get their line numbers). The lines are lexed together, because a
   raw string and a block comment span lines, and coloured one by one, so a
   colour span never crosses a line.
5. Verifies every block: the text without tags, unescaped, equals the
   original text without tags, unescaped, byte for byte. Only `<code>` tags
   survive inside a block; a `<span>` is allowed and dropped (that is how the
   old palette goes away), while any other tag, a link above all, makes the
   block an error, because dropping it would silently lose markup. A file is
   written to a temporary file next to it and renamed into place, so CRLF
   line endings stay.

## The lexer

A small lexer of its own, not Pygments: the palette and the Rust traps below
are specific to these articles, and Pygments is not in the standard library.

- `//` starts a comment everywhere except inside a string, a char or an
  attribute, so `"https://..."` stays a string. `/* ... */` nests, and doc
  comments (`///`, `//!`) are comments too.
- `"..."` strings read backslash escapes and may span lines; a raw string
  `r"..."` / `r#"..."#` and a byte string `b"..."` / `br#"..."#` are one
  string each.
- A char literal is `'x'`, `'\n'` or `'\u{1F600}'`. A `'` that does not close
  within one character is a lifetime or a loop label (`'a`, `'static`,
  `'outer:`), so `&'a str` does not paint the rest of the line as a string.
- Attributes are one token: `#[test]`, `#[derive(Debug, Clone)]` and
  `#![allow(dead_code)]`, nested brackets included.
- Numbers keep their base and suffix: `42u8`, `1_000`, `0x1f`, `0b101`,
  `3.5f64`. The dots of `0..=9` are not a decimal point.
- A name before `!` is a macro (`println!`, `vec!`), the name after `fn` is a
  function, a name starting with an upper-case letter is a type or a
  constant, and the built-in lower-case types (`u32`, `str`, `bool`, `f64`)
  are types too.

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string, char, raw string or
block comment, an unclosed `#[` attribute, or a tag other than `code` and
`span` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/rustcode-highlight/highlight_rustcode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/rustcode-highlight/highlight_rustcode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every Rust page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/rustcode-highlight/tests -q
```

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
