# version 0.0.1
"""Scanner + wrapper behind wrap-long-lines.sh (do not run directly).

Env:
  WRAP_MODE   search | fix
  WRAP_LIMIT  max line length
  WRAP_PHP_CMD (fix mode) php binary, space-separated, for the syntax gate
Usage: python3 .wrap-long-lines.py <file>
"""

# pylint: disable=invalid-name,too-many-statements
import os
import re
import subprocess
import sys

LIMIT = int(os.environ.get("WRAP_LIMIT", "120"))
MODE = os.environ.get("WRAP_MODE", "search")


def scan(line):
    """PHP-aware scan of one line (bytes).

    Returns a list of (offset, kind, depth) split candidates, or None when
    the line is unsafe to touch (unterminated string/comment/heredoc).
    Kinds: 'comma' (split after), 'concat' (split before '.'), 'assign'
    (split after '=' or '=>').
    """
    cands = []
    squote = dquote = False
    line_c = block_c = False
    depth = 0
    heredoc = None
    i, n = 0, len(line)
    while i < n:
        ch = line[i : i + 1]
        nxt = line[i + 1 : i + 2]
        if heredoc is not None:
            if line.startswith(heredoc, i):
                i += len(heredoc)
                heredoc = None
                continue
            i += 1
            continue
        if line_c or block_c:
            if block_c and ch == b"*" and nxt == b"/":
                block_c = False
                i += 2
                continue
            i += 1
            continue
        if squote:
            if ch == b"\\":
                i += 2
                continue
            if ch == b"'":
                squote = False
            i += 1
            continue
        if dquote:
            if ch == b"\\":
                i += 2
                continue
            if ch == b'"':
                dquote = False
            i += 1
            continue
        if ch == b"<" and line[i : i + 3] == b"<<<":
            j = i + 3
            while j < n and line[j : j + 1] in b" \t":
                j += 1
            k = j
            while k < n and (line[k : k + 1].isalnum() or line[k : k + 1] in b"_"):
                k += 1
            if k > j:
                heredoc = line[j:k]
                i = k
                continue
            i += 1
            continue
        if ch == b"'":
            squote = True
            i += 1
            continue
        if ch == b'"':
            dquote = True
            i += 1
            continue
        if ch == b"/" and nxt == b"/":
            break  # rest of line is a comment
        if ch == b"#":
            break  # rest of line is a comment
        if ch == b"/" and nxt == b"*":
            block_c = True
            i += 2
            continue
        if ch in b"([{":
            depth += 1
        elif ch in b")]}":
            depth = max(0, depth - 1)
        else:
            prev = line[i - 1 : i]
            if ch == b",":
                cands.append((i, "comma", depth))
            elif depth == 0 and ch == b".":
                # not .. spread/concat pair, not a decimal number
                if nxt != b"." and prev != b"." and not (prev.isdigit() or nxt.isdigit()):
                    cands.append((i, "concat", depth))
            elif depth == 0 and ch == b"=" and nxt == b">":
                cands.append((i, "arrow", depth))
            elif depth == 0 and ch == b"=" and nxt != b"=" and prev not in (b"=", b"!", b"<", b">"):
                cands.append((i, "assign", depth))
        i += 1
    if heredoc is not None or squote or dquote or block_c:
        return None
    return cands


def disp_len(s):
    return len(s)


def php_mode_lines(raw):
    """Map each line to True when fully inside a <?php ... ?> segment.

    Tracks open tags (<?php, <?=, <? + letter except <?xml), strings,
    comments and heredocs across the whole file. Lines straddling a
    mode boundary count as outside (never touch mixed lines).
    """
    lines = raw.split(b"\n")
    flags = [False] * len(lines)
    in_php = False
    squote = dquote = False
    line_c = block_c = False
    heredoc = None
    line_no = 0
    line_is_php = True  # all segments of current line inside php?
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i : i + 1]
        nxt = raw[i + 1 : i + 2]
        if ch == b"\n":
            flags[line_no] = line_is_php and in_php
            line_no += 1
            line_is_php = True
            line_c = False
            i += 1
            continue
        if not in_php:
            low3 = raw[i : i + 5].lower()
            if low3.startswith(b"<?php") or low3.startswith(b"<?="):
                in_php = True
                i += 5 if low3.startswith(b"<?php") else 3
                continue
            if ch == b"<" and nxt == b"?" and raw[i + 2 : i + 3].isalpha() and raw[i + 2 : i + 5].lower() != b"xml":
                in_php = True
                i += 2
                continue
            line_is_php = False
            i += 1
            continue
        # inside php: mirror scan() state handling (minus candidate logic)
        if heredoc is not None:
            if raw.startswith(heredoc, i):
                i += len(heredoc)
                heredoc = None
                continue
            i += 1
            continue
        if line_c or block_c:
            if block_c and ch == b"*" and nxt == b"/":
                block_c = False
                i += 2
                continue
            i += 1
            continue
        if squote:
            if ch == b"\\":
                i += 2
                continue
            if ch == b"'":
                squote = False
            i += 1
            continue
        if dquote:
            if ch == b"\\":
                i += 2
                continue
            if ch == b'"':
                dquote = False
            i += 1
            continue
        if ch == b"<" and raw[i : i + 3] == b"<<<":
            j = i + 3
            while j < n and raw[j : j + 1] in b" \t":
                j += 1
            k = j
            while k < n and (raw[k : k + 1].isalnum() or raw[k : k + 1] in b"_"):
                k += 1
            if k > j:
                heredoc = raw[j:k]
                i = k
                continue
            i += 1
            continue
        if ch == b"'":
            squote = True
            i += 1
            continue
        if ch == b'"':
            dquote = True
            i += 1
            continue
        if ch == b"/" and nxt == b"/":
            line_c = True
            i += 1
            continue
        if ch == b"#":
            line_c = True
            i += 1
            continue
        if ch == b"/" and nxt == b"*":
            block_c = True
            i += 2
            continue
        if ch == b"?" and nxt == b">":
            in_php = False
            line_is_php = False
            i += 2
            continue
        i += 1
    if line_no < len(flags):
        flags[line_no] = line_is_php and in_php
    return flags


def wrap_line(text, limit, base_indent):
    """Split one overlong line; returns list of lines or None if unsafe."""
    raw = text.encode("utf-8", "replace")
    cands = scan(raw)
    if cands is None:
        return None
    cont = base_indent + "    "
    # split at commas on the shallowest nesting level (one argument per
    # line); deeper commas stay intact inside their own piece
    comma_depths = sorted({d for _, k, d in cands if k == "comma"})
    if comma_depths:
        min_depth = comma_depths[0]
        commas = [o for o, k, d in cands if k == "comma" and d == min_depth]
        parts, prev = [], 0
        for o in commas:
            parts.append(raw[prev : o + 1])
            prev = o + 1
        parts.append(raw[prev:])
        lines = [parts[0].decode("utf-8", "replace").rstrip()]
        for p in parts[1:]:
            lines.append(cont + p.decode("utf-8", "replace").strip())
        lines = normalize_brackets(lines, base_indent, cont)
        if lines is not None and all(disp_len(ln) <= limit for ln in lines):
            return lines
        if lines is None:
            return None
        # recurse into pieces that are still too long
        out = [lines[0]]
        for ln in lines[1:]:
            if disp_len(ln) <= limit:
                out.append(ln)
                continue
            sub = wrap_segment(ln.encode("utf-8", "replace"), cont, limit)
            if sub is None:
                return None
            out.extend(sub)
        out = normalize_brackets(out, base_indent, cont)
        return out if out is not None and all(disp_len(ln) <= limit for ln in out) else None
    seg = wrap_segment(raw, base_indent, limit)
    return seg


def normalize_brackets(lines, base_indent, cont):
    """PSR-12 shape for a single bracket pair split across lines: the
    opening bracket ends the head line, closing brackets get their own
    line at base indent. Returns the reshaped list, or the input
    unchanged when the shape does not match."""
    if not lines:
        return lines

    head = lines[0]
    # head must end with content after the last opener, e.g. f($a,
    m = re.search(r"([\[\({])[^]\[\(\)\{\}]*$", head)
    if not m:
        return lines
    opener = m.group(1)
    closer = {"(": ")", "[": "]", "{": "}"}[opener]
    # tail must end with the matching closer (+ optional ; and more closers)
    tail = lines[-1]
    tm = re.match(r"^(.*?)([)\]}]+;?)$", tail.strip())
    if not tm or closer not in tm.group(2):
        return lines
    arg_part, close_part = tm.group(1), tm.group(2)
    new_lines = [head[: len(head) - len(m.group(0)) + 1].rstrip()]
    new_lines.append(cont + m.group(0)[1:].strip())
    for ln in lines[1:-1]:
        new_lines.append(ln)
    if arg_part.strip():
        # strip one trailing comma level already handled; keep as is
        new_lines.append(cont + arg_part.strip())
    new_lines.append(base_indent + close_part)
    # sanity: same non-whitespace content, no line got longer than before
    before = "".join(ln.strip() for ln in lines)
    after = "".join(ln.strip() for ln in new_lines)
    if before != after:
        return None
    return new_lines


def wrap_segment(raw, base_indent, limit):
    cands = scan(raw)
    if cands is None:
        return None
    text = raw.decode("utf-8", "replace")
    if disp_len(text) <= limit:
        return [text]
    cont = base_indent + "    "
    ops = [(o, k) for o, k, d in cands if k in ("concat", "arrow", "assign") and d == 0]
    if not ops:
        return None
    lines = []
    prev = 0
    pieces = []  # (text, dot_prefix): a concat dot belongs to the piece AFTER it
    need_dot = False
    for o, k in ops:
        if k == "concat":
            pieces.append((raw[prev:o], need_dot))
            need_dot = True
            prev = o + 1
        elif k == "arrow":
            pieces.append((raw[prev : o + 2], need_dot))
            need_dot = False
            prev = o + 2
        else:  # assign
            pieces.append((raw[prev : o + 1], need_dot))
            need_dot = False
            prev = o + 1
    pieces.append((raw[prev:], need_dot))
    lines.append(pieces[0][0].decode("utf-8", "replace").rstrip())
    for chunk, dot in pieces[1:]:
        t = chunk.decode("utf-8", "replace").strip()
        lines.append(cont + (". " if dot else "") + t)
    if all(disp_len(ln) <= limit for ln in lines):
        return lines
    return None


def main():
    path = sys.argv[1]
    with open(path, "rb") as fh:
        raw = fh.read()
    lines = raw.split(b"\n")

    if MODE == "search":
        for i, ln in enumerate(lines, 1):
            text = ln.decode("utf-8", "replace").rstrip("\r\n")
            if len(text) > LIMIT:
                print(f"{path}:{i}:{len(text)}: {text[:100]}")
        return

    # fix mode
    assert MODE == "fix"
    php_cmd = os.environ["WRAP_PHP_CMD"].split(" ")

    def php_ok(p):
        try:
            r = subprocess.run(php_cmd + ["-l", p], capture_output=True, text=True, timeout=60, check=False)
        except Exception:  # pylint: disable=broad-exception-caught
            return False
        return r.returncode == 0 and "No syntax errors" in (r.stdout + r.stderr)

    if not php_ok(path):
        print(f"SKIPPED {path} (pre-existing php -l failure)")
        return

    out = []
    changed = skipped = ignored = 0
    in_php = php_mode_lines(raw)
    file_eol = b"\r" if b"\r\n" in raw else b""
    for idx, ln in enumerate(lines):
        text = ln.decode("utf-8", "replace").rstrip("\r\n")
        if len(text) <= LIMIT:
            out.append(ln)
            continue
        if idx >= len(in_php) or not in_php[idx]:
            # outside <?php ?> (HTML/template): never touch, don't penalize
            out.append(ln)
            ignored += 1
            continue
        stripped = text.strip()
        if stripped.startswith("<<<"):
            out.append(ln)
            skipped += 1
            continue
        base_indent = text[: len(text) - len(text.lstrip())]
        res = wrap_line(text, LIMIT, base_indent)
        if res is None:
            out.append(ln)
            skipped += 1
            continue
        for nl in res:
            out.append((nl + ("\r" if file_eol else "")).encode("utf-8", "replace"))
        changed += 1

    if changed == 0:
        print(f"UNCHANGED {path} skipped={skipped} ignored={ignored}")
        return
    new_raw = b"\n".join(out)
    with open(path, "wb") as fh:
        fh.write(new_raw)
    if not php_ok(path):
        with open(path, "wb") as fh:
            fh.write(raw)
        print(f"FAILED {path} (php -l gate, restored)")
        return
    print(f"FIXED {path} lines={changed} skipped={skipped} ignored={ignored}")


main()
