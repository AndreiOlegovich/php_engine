# asmcode-highlight

Colours x86 assembly code inside `<p class="asmcode ...">` blocks of `.php`
article files, on the server side: the colour spans are written into the page
once, no script runs in the browser. Standard library only, so it runs on the
host without NASM.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `asmcode`. Other
   blocks (`cmd1`, `cout1`, `somecode`, inline `tt` spans) are left alone.
2. Skips blocks that already carry the palette (`<span class="cXXXXXX">`).
   A block that still has the older `blue`/`orange`/`lightblue`/`yellow`/
   `green`/`violet` palette is not skipped: those spans are dropped and the
   block is re-coloured into the canonical classes, exactly as
   `tools/rubycode-highlight` re-highlights the old Ruby colours.
3. Colours the rest with the palette of the served sheets, written in lower
   case as the `cmd1` spans do:

   | Assembly | Class |
   |---|---|
   | instructions (`mov`, `lea`, `syscall`, `vaddps`) | `c5fd7ff` |
   | directives (`section`, `global`, `db`, `equ`, `%macro`) | `c5fd7ff` |
   | strings and character constants (`"hi"`, `'A'`) | `cad7fa8` |
   | comments (`; ...`) | `c999999` |
   | label definitions (`_start:`, `print_loop:`, `.loop:`) | `cff0086` |
   | constants (`SYS_EXIT`, `PT_X`, `NODE_SIZE`) | `cff5f00` |
   | numbers (`42`, `0xFF`, `0b1010`, `1.0`) | `c34e2e2` |
   | registers (`rax`, `eax`, `ax`, `al`, `r15b`, `xmm0`, `ymm0`) | `c4e9a06` |
   | macro parameters (`%1`, `%%loop`) | `c4e9a06` |
   | memory symbols, size keywords (`byte`, `qword`), operators | plain |

   Lower case only: `container/src/.css/site.css` define
   `.c5fd7ff` and the others in lower case, and class names are case
   sensitive, so the mixed case `c5FD7FF` of the older Python notes matches
   nothing in the sheets.
4. Each `<code>` element of a block is one line of assembly (that is how the
   blocks get their line numbers), so the lines are lexed one by one and a
   colour span never crosses a line. The round-trip check below guarantees the
   line structure survives.
5. Verifies every block: the text without tags, unescaped, equals the
   original text without tags, unescaped, byte for byte. Only `<code>` tags
   survive inside a block; a `<span>` is allowed and dropped (that is how the
   old palette goes away), while any other tag, a link above all, makes the
   block an error, because dropping it would silently lose markup. A file is
   written to a temporary file next to it and renamed into place, so CRLF
   line endings stay.

## The lexer

A small line lexer of its own, not Pygments (not in the standard library):
the palette and the assembly traps below are specific to these articles.

- An instruction is a keyword only at the start of a statement, after an
  optional label. That keeps command blocks readable: `apk add --no-cache
  nasm` and `docker run` do not colour `add` or `run` as instructions.
- NASM is case-insensitive: `MOV` is an instruction and `RAX` a register.
  An upper-case word that is not an instruction or a directive is an EQU
  constant.
- `;` starts a comment everywhere except inside a string, so
  `msg db "a;b", 0` keeps the semicolon in the string.
- `"..."`, `'...'` and backtick strings are one string each; character
  constants such as `'A'` are strings too.
- `name:`, `.local:` and `_start:` at the start of a statement are label
  definitions. `loop:` is a label while `loop` alone is the instruction.
- `%1` and `%%loop` are macro parameters; `%macro`, `%define`, `%include`,
  `%ifdef` and the other preprocessor words are keywords.
- `section`, `global`, `extern`, `bits`, `org`, `times`, `align`, `db`/`dw`/
  `dd`/`dq`, `resb`/`resq`, `struc` and `endstruc` are directives.
- Numbers: decimal, `0x` hex, `0b` binary, `0o` octal, `1.0` floats and the
  NASM suffix forms; `$` and `$$` stay plain.
- A string that never closes before the end of the block is an error. A
  quoted shell argument may span lines inside a command block, and each
  line keeps its share of the string colour.

A block the lexer cannot read cleanly is reported as `ERROR` with its line
and left byte for byte as it was: an unterminated string, or a tag other
than `code` and `span` inside the block.

## Use

From the repo root:

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be coloured)
python3 container/tools/asmcode-highlight/highlight_asmcode.py PATH [PATH ...]

# write the colours in place
python3 container/tools/asmcode-highlight/highlight_asmcode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do. Run it
on the pages you are working on: a directory run over all of
`container/src` rewrites every assembly page at once.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/asmcode-highlight/tests -q
```

Without pytest, the test functions also run under a plain interpreter that
passes a fresh temporary directory for `tmp_path`.

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
