# Replace shared files atomically

Date: 2026-10-22.

## Problem

A generator that writes a shared file with `{ ... } > "$OUT"` empties the
file first and refills it incrementally. While the run lasts, every reader
sees a truncated file: tests that parse it fail, the local site serves a
broken response, a watcher reports a false failure, and a deploy that
copies it uploads the partial file. If validation fails afterwards, the
invalid file is already in place, so a bad run replaced a good file.

## Copying before reading does not fix it

Copying the file before reading it is just another read: a copy taken
during a run is as broken as the original. A reader cannot tell a
finished file from one that is still being written. Fix the writer.

## Fix

Build the new file next to the real one and rename it into place only
once it is complete and valid:

```bash
TMP_OUT="$OUT.tmp.$$"
trap 'rm -f -- "$TMP_OUT"' EXIT

{
  ...                       # generate
} > "$TMP_OUT"

if python3 -c 'import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' "$TMP_OUT" 2>/dev/null; then
  echo "XML valid." >&2
else
  echo "ERROR: generated file is not valid XML; $OUT left unchanged." >&2
  exit 1
fi
mv -- "$TMP_OUT" "$OUT"     # same filesystem: atomic replace
trap - EXIT
```

`rename(2)` on one filesystem is atomic: a reader sees either the old
file or the new one, never a mix. Validate before the rename, so a failed
run leaves the previous file untouched.

## Rules

- Never write the shared path directly.
- Write `<path>.tmp.<pid>` beside it (same directory, same filesystem).
- Validate the temporary file before replacing the target.
- Replace with `mv`, never with `cp` (a copy is not atomic).
- Remove the temporary file on every exit path (`trap ... EXIT`).
