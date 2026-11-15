# pycode-highlight

Highlights unhighlighted Python code inside `.php` article files and
verifies the result byte-for-byte. Stdlib only (`tokenize`, `keyword`,
`html`) — runs as a plain Python script or as a Docker container.

## What it does

1. Finds `<p ...>...</p>` blocks — primarily `<p class="pycode p2">`
   (also `<p class="somecode">`, and with `--aggressive` any `<p>`
   holding misplaced Python code, except `cmd*`/`cout*` output blocks).
2. Skips blocks already highlighted with `<span class="cXXXXXX">`.
   Everything else declared as Python (old `<font>`, `<span style>`,
   `<span class="violet">` palettes, or plain text) is re-highlighted
   into the canonical palette (`c5FD7FF`/`cFCE94F`/`c34E2E2`/`cAD7FA8`,
   see `css_pipeline/source/style.css`).
3. Verifies each block: `strip_tags(unescape(new)) == original source`
   exactly — no added/removed newlines or characters. `compile()` is a
   warning only (many snippets are intentional fragments / demos of
   errors); `--strict-compile` turns it into a skip.
4. Blocks that fail `tokenize` (genuinely broken article code, e.g.
   unbalanced parens) are left untouched and reported as `ERROR`.

Non-Python `somecode` blocks (C++, shell, prose) are detected via a
strict `compile()` + keyword heuristic and left alone.

## Use

```sh
# check only (exit 1 = fixes needed, exit 2 = broken blocks found)
python3 highlight_pycode.py [--aggressive] [--strict-compile] [--quiet] PATH [PATH ...]

# write fixes in place
python3 highlight_pycode.py --write PATH [PATH ...]

# misplaced code in generic tags: also add the pycode class
python3 highlight_pycode.py --write --aggressive --fix-tag PATH [PATH ...]
```

`PATH` is a `.php` file or a directory (recursive). Scratch copies
(`* copy.php`, `sess_*`) are skipped like the other repo linters do.

```sh
# whole tree
python3 highlight_pycode.py --check container/src
```

## Docker

```sh
cd container/tools/pycode-highlight
docker build -t pycode-highlight -f Dockerfile .
docker run --rm -v "$PWD/../..:/work:ro" pycode-highlight --check /work/src
docker run --rm -v "$PWD/../..:/work" pycode-highlight --write /work/src/dev/python
```

## Tests

```sh
python3 -m pytest tests/ -q        # or: python3 tests/test_highlight_pycode.py
```
