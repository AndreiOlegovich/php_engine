# jsoncode-highlight

Colours JSON code inside `.php` article files with the site palette,
on the server side: the colour spans are written into the page once, no
script runs in the browser. Uses the Pygments JSON lexer, which also
reads `//` and `/* */` comments, so `opencode.jsonc` and similar files
work too.

## What it does

1. Finds `<p ...>...</p>` blocks whose class list holds `jsoncode`,
   including `jsoncode jsoncode-with-hidden-lines` blocks.
2. Skips blocks already highlighted with `<span class="cXXXXXX">`.
3. Colours the rest with the palette of the Python blocks
   (`tools/pycode-highlight`), written in lower case: the built sheets
   define these classes only in lower case (`source/_colors.css`), and
   class names are case-sensitive.

   | JSON | Class |
   |---|---|
   | keys (`"action"`) | `c5fd7ff` |
   | string and number values | `cad7fa8` |
   | `true`, `false`, `null` | `cfce94f` |
   | comments | `c34e2e2` |
   | `{ } [ ] , :` | plain |

4. Keeps every tag already in a block (the `span.extra-lines` runs of a
   hidden-lines block, links) and every entity as written. A colour span
   is split where such a tag falls, so tags always nest.
5. Verifies each block: removing the added colour spans gives back the
   original inner HTML byte for byte. A block the lexer cannot read is
   left alone and reported as `ERROR`.

## Use

Pygments is in the `dev` group of `container/pyproject.toml`, so run it
with the container venv (`uv sync --project container` once):

```sh
# check only (exit 1 = blocks need highlighting, exit 2 = a block could not be read)
container/.venv/bin/python container/tools/jsoncode-highlight/highlight_jsoncode.py PATH [PATH ...]

# write the colours in place
container/.venv/bin/python container/tools/jsoncode-highlight/highlight_jsoncode.py --write PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do.
Write the block plain first, with its hidden-lines runs if any, then run
the tool: it colours inside the runs.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/jsoncode-highlight/tests -q
```
