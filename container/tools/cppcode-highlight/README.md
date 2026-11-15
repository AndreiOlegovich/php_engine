# cppcode-highlight

Colours C++ code inside `<p class="cppcode ...">` blocks of `.php` article
files, on the server side: the colour spans are written into the page once,
no script runs in the browser. Standard library only, so it runs on the host
without a C++ toolchain.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `cppcode`. Other
   blocks (`cmd1`, `cout1`, `somecode`, inline `tt` spans) are left alone.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`lightblue`/`yellow`/
   `violet` palette is not skipped: those spans are dropped and the block is
   re-coloured into the canonical classes, exactly as
   `tools/rubycode-highlight` re-highlights the old Ruby colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | C++ | Class |
   |---|---|
   | keywords and built-in types (`int`, `if`, `return`, `class`, `const`, `auto`) | `c5fd7ff` |
   | string and character literals (`"..."`, `'c'`, `R"(...)"`) | `cad7fa8` |
   | comments (`// ...`, `/* ... */`) | `c999999` |
   | preprocessor directives (`#include`, `#pragma`, `#define`) | `cfce94f` |
   | numbers (`42`, `3.5`, `0x1f`, `1'000`, `0b101`) | `c34e2e2` |
   | type and constant names (`Point`, `SIZE`, `Color`) | `cff5f00` |
   | `std` and the names after `::` (`std::cout`, `vector`) | `c4e9a06` |
   | the name after `class`, `struct`, `enum`, `union`, `namespace` | `cff0086` |
   | local names, function names, operators, arguments | plain |

   Lower case only: `container/src/.css/site.css` define
   `.c5fd7ff` and the others in lower case, and class names are case
   sensitive, so the mixed case `c5FD7FF` of the older Python notes matches
   nothing in the sheets.
4. Each `<code>` element of a block is one line of C++ (that is how the
   blocks get their line numbers). The lines are lexed together, because a
   `#` directive and a `/* */` comment span lines, and coloured one by one,
   so a colour span never crosses a line. A `<code>` tag is written back
   without attributes, so a hand-made class on a line
   (`<code class="green">`) is dropped and only the lexer colours remain.
5. Verifies every block: the text without tags, unescaped, equals the
   original text without tags, unescaped, byte for byte. Only `code` tags
   survive inside a block; `span`, `b`, `strong`, `i` and `em` are allowed
   and dropped (that is how the old palette and the hand-made braces go
   away), while any other tag, a link above all, makes the block an error,
   because dropping it would silently lose markup. A file is written to a
   temporary file next to it and renamed into place, so CRLF line endings
   stay.

## The lexer

A small lexer of its own, not Pygments: the palette and the C++ traps below
are specific to these articles, and Pygments is not in the standard library.

- `//` starts a comment everywhere except inside a string, and `/* ... */`
  is a comment that may span lines; `"http://x"` stays a string.
- `"..."` and `'...'` read their backslash escapes, so `'\''` and `'\n'` are
  one character literal each. A literal left open is an error.
- `R"(...)"` and `R"tag(...)tag"` are raw strings: the whole literal is one
  string.
- `#` at the start of a line (only spaces and tabs before it) starts a
  directive: `#` and the directive word are the directive colour, and the
  `<header>` of `#include` is a string. `#pragma once` and `#define SIZE 5`
  keep their parts.
- Numbers are decimal, float, hex and binary, with `'` digit separators and
  the `u`, `l`, `f`, `z` suffixes: `1'000`, `1.5e-3`, `0x1f`, `0b101`.
- `class`, `struct`, `enum`, `union` and `namespace` name the thing they
  define: the next identifier is the defined name. `enum class Color` works,
  `using namespace std` does not colour `std` as a namespace name.
- `std` and every name after `::` are library names: `std::vector<int>` and
  `Bicycle::getManuf`.
- An identifier that starts with a capital, or is all capitals, is a type or
  a constant: `Point`, `SIZE`, `SDL_Window`.
- A tag is a known name (`code`, `span`, `b`, `strong`, `i`, `em`) followed
  by the end of the tag or by whitespace and attributes without an angle
  bracket. C++ is full of `<` (`i < n`, `p->x`, `vector<int>`), so an
  unknown name is not a tag and stays text.

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string, character literal,
raw string or `/* */` comment, or a tag other than `code`, `span`, `b`,
`strong`, `i` and `em` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/cppcode-highlight/highlight_cppcode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/cppcode-highlight/highlight_cppcode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every C++ page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/cppcode-highlight/tests -q
```

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
