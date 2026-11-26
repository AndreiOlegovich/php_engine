#!/usr/bin/env python3
"""Highlight unhighlighted Python code inside .php files.

Finds <p ...>...</p> blocks (primarily <p class="pycode p2">, also
<p class="somecode ..."> and --aggressive any <p>), detects blocks that
are NOT yet highlighted with <span class="cXXXXXX">, highlights them with
a stdlib-tokenize highlighter matching the site palette:

    import/from            -> <span class="c5FD7FF">
    other keywords         -> <span class="cFCE94F">  (incl. True/False/None)
    builtins + def names   -> <span class="c34E2E2"><b>
    strings / numbers      -> <span class="cAD7FA8">
    comments               -> <span class="c34E2E2">  (no bold)
    imported / class names -> plain

Escapes: " -> &quot;, < -> &lt;, > -> &gt;, ' -> &#x27; (& -> &amp; first).

Verification per block (no extra newlines / no corruption):
    strip_tags(unescape(new_inner)) == strip_tags(unescape(old_inner))
exactly. Optional compile() sanity check (dedented) warns only,
unless --strict-compile.

Usage:
    python3 highlight_pycode.py [--write] [--aggressive] [--fix-tag]
                                [--strict-compile] [--quiet] PATH [PATH ...]

    PATH: .php file or directory (recursive for dirs).
    Default is --check (no writes, exit 1 if fixes needed).
    --write writes fixed files in place.

Docker:
    docker build -t pycode-highlight -f Dockerfile .
    docker run --rm -v $PWD:/work pycode-highlight --check /work/src
"""

from __future__ import annotations

import argparse
import builtins
import html
import io
import keyword
import re
import sys
import textwrap
import tokenize
from pathlib import Path
from typing import Any

VERSION = "0.1.0"

# --- palette ---------------------------------------------------------------
C_IMPORT = "c5FD7FF"  # import / from
C_KW = "cFCE94F"  # other keywords incl. True/False/None
C_FUNC = "c34E2E2"  # builtins + def names (bold) / comments (no bold)
C_STR = "cAD7FA8"  # strings + numbers

NEW_STYLE_RE = re.compile(r'<span\s+class="c[0-9A-Fa-f]{6}"')
P_BLOCK_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

BUILTINS = {n for n in dir(builtins) if not n.startswith("_")}
KEYWORDS = set(keyword.kwlist)  # includes True/False/None on py3

# heuristic: strong python signals for blocks in non-pycode tags
_STMTS = "import|from|def|class|for|while|if|elif|else|try|except|finally|with|return|raise|pass|print\\(|len\\(|logging\\.|sys\\.|self\\."
PY_INDICATOR_RE = re.compile(r"(^\s*(" + _STMTS + r"))", re.MULTILINE)
_PY_KWS = "import|from|def|class|for|while|if|elif|else|return|None|True|False|in|not|and|or|pass|raise|with|as|lambda|yield|assert|del|global|nonlocal|try|except"
PY_KW_RE = re.compile(r"\b(" + _PY_KWS + r")\b")
# shell / output markers -> definitely NOT python code
SHELL_RE = re.compile(
    r"^\s*(\$|#\s*(apt|yum|pip|docker|cat|ls|cd|mkdir)|pip\s+install|python\s+\S+\.py\s*$)",
    re.MULTILINE,
)


def esc(s: str) -> str:
    """Escape code text for HTML inside pycode blocks."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")


def strip_and_decode(inner: str) -> str:
    """Inner HTML -> raw source: <br> to newline, strip tags, unescape."""
    no_br = BR_RE.sub("\n", inner)
    no_tags = TAG_RE.sub("", no_br)
    return html.unescape(no_tags)


class _LineMap:
    """Absolute offsets for tokenize (row, col) positions."""

    def __init__(self, src: str) -> None:
        self.offsets = [0]
        for ln in src.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(ln))
        self.total = self.offsets[-1]
        self.n_lines = len(self.offsets) - 1

    def pos(self, row: int, col: int) -> int:
        # clamp defensive (tokenize can point past EOF on missing newline)
        if row - 1 < self.n_lines:
            return min(self.offsets[row - 1] + col, self.total)
        return self.total


def _name_html(tstr: str, prev_name: str, in_import: bool) -> tuple[str, bool]:
    """HTML for a NAME token; returns (html, in_import)."""
    if tstr in ("import", "from"):
        return f'<span class="{C_IMPORT}">' + esc(tstr) + "</span>", True
    if prev_name == "def":
        # function name directly after def
        return f'<span class="{C_FUNC}"><b>' + esc(tstr) + "</b></span>", in_import
    if prev_name in ("class", "@") or in_import:
        return esc(tstr), in_import  # class/decorator/imported names stay plain
    if tstr in KEYWORDS:
        return f'<span class="{C_KW}">' + esc(tstr) + "</span>", in_import
    if tstr in BUILTINS:
        return f'<span class="{C_FUNC}"><b>' + esc(tstr) + "</b></span>", in_import
    return esc(tstr), in_import


def highlight_source(src: str) -> str:
    """Highlight raw python source. Gaps between tokens copied verbatim.

    Raises SyntaxError/TokenizeError on untokenizable input.
    """
    lm = _LineMap(src)
    out: list[str] = []
    cur = 0
    prev_name = ""  # previous significant NAME text
    in_import = False

    toks = tokenize.generate_tokens(io.StringIO(src).readline)
    for tok in toks:
        ttype, tstr, start, end, _ = tok
        if ttype in (tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        s_abs = lm.pos(*start)
        e_abs = lm.pos(*end)
        if s_abs > cur:
            out.append(esc(src[cur:s_abs]))  # verbatim gap (ws/newlines)
            cur = s_abs
        if ttype in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
            out.append(esc(tstr))
            cur = e_abs
            if ttype == tokenize.NEWLINE:
                in_import = False
                prev_name = ""
            continue
        if ttype == tokenize.COMMENT:
            out.append(f'<span class="{C_FUNC}">' + esc(tstr) + "</span>")
        elif ttype in (tokenize.STRING, tokenize.NUMBER):
            out.append(f'<span class="{C_STR}">' + esc(tstr) + "</span>")
        elif ttype == tokenize.NAME:
            html_snippet, in_import = _name_html(tstr, prev_name, in_import)
            out.append(html_snippet)
            prev_name = tstr
            cur = e_abs
            continue
        else:  # OP and everything else: plain, escaped
            out.append(esc(tstr))
            if tstr == ";":
                in_import = False
            # any OP breaks `def <name>` / `class <name>` / `@<name>` adjacency
            prev_name = tstr if tstr == "@" else ""
            cur = e_abs
            continue
        cur = e_abs
    if cur < lm.total:
        out.append(esc(src[cur:]))
    return "".join(out)


def _compiles(src: str) -> bool:
    try:
        compile(textwrap.dedent(src), "<snippet>", "exec")
    except SyntaxError, ValueError:
        return False
    return True


def _tokenizable(src: str) -> bool:
    try:
        list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    return True


def _cyrillic_heavy(s: str) -> bool:
    cyr = sum(1 for ch in s if "\u0400" <= ch <= "\u04ff")
    return cyr > 0 and cyr / max(len(s), 1) > 0.3


def is_python_like(src: str, *, strict: bool = False) -> bool:
    """Conservative python detector for blocks in unexpected tags.

    strict=True (non-pycode blocks): only True when the snippet compiles
    as Python. Blocks declared as Python (pycode) use strict=False which
    additionally accepts tokenizable pseudo-code fragments.
    """
    s = src.strip()
    if len(s) < 4 or SHELL_RE.search(s) or _cyrillic_heavy(s):
        return False
    if _compiles(s):
        # compiles, but prose also compiles: require a code indicator.
        return bool(PY_INDICATOR_RE.search(s) or PY_KW_RE.search(s) or "=" in s or "(" in s)
    if strict or not _tokenizable(s):
        return False
    # fragments (pseudo-code like `item in container`, `a = sys.argv[0]`)
    indicators = len(PY_KW_RE.findall(s))
    has_assign = "=" in s or ":" in s or "(" in s or "[" in s or PY_INDICATOR_RE.search(s) is not None
    return indicators >= 1 and has_assign or indicators >= 2


def class_list(attrs: str) -> list[str]:
    m = CLASS_ATTR_RE.search(attrs)
    if not m:
        return []
    return (m.group(2) if m.group(2) is not None else m.group(3)).split()


def _block_action(classes: list[str], inner: str, raw: str, *, aggressive: bool) -> str:
    """ok | ignore | empty | nonpython | target for one <p> block."""
    if NEW_STYLE_RE.search(inner):
        return "ok"
    is_pycode = "pycode" in classes
    joined = "".join(classes)
    targeted = is_pycode or "somecode" in classes or (aggressive and "cmd" not in joined and "cout" not in joined and "cmdout" not in joined)
    if not targeted:
        return "ignore"
    if not raw.strip():
        return "empty"
    if not is_pycode and not is_python_like(raw, strict=True):
        # somecode / generic tag: must compile as python (strict),
        # otherwise it is C++/shell/prose - leave alone quietly.
        return "nonpython"
    return "target"


def _highlight_block(raw: str, where: str) -> tuple[str | None, str | None]:
    """(highlighted, error): error is None on success.

    The round-trip check guarantees no added/removed newlines or chars.
    """
    try:
        highlighted = highlight_source(raw)
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        return None, f"{where} tokenize failed: {e}"
    if strip_and_decode(highlighted) != raw:
        return None, f"{where} round-trip mismatch (NOT written)"
    return highlighted, None


def _with_pycode_class(attrs: str) -> str:
    def _add_class(cm: re.Match) -> str:
        q = '"' if cm.group(2) is not None else "'"
        val = cm.group(2) if cm.group(2) is not None else cm.group(3)
        if "pycode" not in val.split():
            val = (val + " pycode").strip()
        return f"class={q}{val}{q}"

    if CLASS_ATTR_RE.search(attrs):
        return CLASS_ATTR_RE.sub(_add_class, attrs, count=1)
    return attrs + ' class="pycode p2"'


def process_file(path: Path, *, aggressive: bool, fix_tag: bool, strict_compile: bool, write: bool) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stats: dict[str, Any] = {
        "file": str(path),
        "ok": 0,
        "fixed": 0,
        "skipped_not_python": 0,
        "skipped_empty": 0,
        "compile_warn": [],
        "errors": [],
    }

    def repl(m: re.Match) -> str:
        attrs, inner = m.group(1), m.group(2)
        classes = class_list(attrs)
        raw = strip_and_decode(inner)
        action = _block_action(classes, inner, raw, aggressive=aggressive)
        if action == "ok":
            stats["ok"] += 1
            return m.group(0)
        if action == "ignore":
            return m.group(0)
        if action != "target":
            stats["skipped_empty" if action == "empty" else "skipped_not_python"] += 1
            return m.group(0)

        highlighted, error = _highlight_block(raw, f"{path}:{m.start()}")
        if error is not None:
            stats["errors"].append(error)
            return m.group(0)

        # compile sanity (dedented; leading file-indent is not code indent)
        try:
            compile(textwrap.dedent(raw).strip("\n"), "<snippet>", "exec")
        except (SyntaxError, ValueError) as e:
            stats["compile_warn"].append(f"{path}:{m.start()} compile note: {e}")
            if strict_compile:
                return m.group(0)

        stats["fixed"] += 1
        new_attrs = _with_pycode_class(attrs) if fix_tag and "pycode" not in classes else attrs
        return f"<p{new_attrs}>{highlighted}</p>"

    new_text = P_BLOCK_RE.sub(repl, text)
    stats["changed"] = new_text != text
    if write and stats["changed"]:
        # safe: failed blocks are left byte-identical by repl(); every
        # written block passed the exact round-trip check above.
        path.write_text(new_text, encoding="utf-8")
    stats["new_text"] = new_text
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Highlight Python code in .php files.")
    ap.add_argument("paths", nargs="+", help=".php file or directory (recursive)")
    ap.add_argument("--check", action="store_true", help="check only, no writes (this is the default)")
    ap.add_argument("--write", action="store_true", help="write fixes in place")
    ap.add_argument(
        "--aggressive",
        action="store_true",
        help="also scan non-pycode <p> tags for misplaced python code",
    )
    ap.add_argument("--fix-tag", action="store_true", help="with --aggressive, add pycode class to fixed tags")
    ap.add_argument("--strict-compile", action="store_true", help="skip blocks that do not compile()")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    files: list[Path] = []
    for p in args.paths:
        pp = Path(p)
        if pp.is_dir():
            files += sorted(p for p in pp.rglob("*.php") if p.is_file())
        elif pp.is_file():
            files.append(pp)
        else:
            print(f"no such file/dir: {p}", file=sys.stderr)
    # skip scratch copies like the repo's lint tools do
    files = [f for f in files if " copy" not in f.name and not f.name.startswith("sess_")]

    total_ok = total_fixed = 0
    need_fix_files: list[str] = []
    failed = False
    for f in files:
        st = process_file(
            f,
            aggressive=args.aggressive,
            fix_tag=args.fix_tag,
            strict_compile=args.strict_compile,
            write=args.write,
        )
        total_ok += st["ok"]
        total_fixed += st["fixed"]
        if st["errors"]:
            failed = True
            for e in st["errors"]:
                print(f"ERROR {e}", file=sys.stderr)
        for w in st["compile_warn"]:
            if not args.quiet:
                print(f"WARN {w}")
        if st["changed"] and not args.write:
            need_fix_files.append(st["file"])
        if not args.quiet and (st["fixed"] or st["changed"]):
            print(f"{f}: ok={st['ok']} fixed={st['fixed']} skipped_nonpy={st['skipped_not_python']}")

    if not args.quiet:
        mode = "write" if args.write else "check"
        print(f"files={len(files)} highlighted={total_ok} fixed={total_fixed} mode={mode}")
    if need_fix_files and not args.write:
        print(
            f"{len(need_fix_files)} file(s) need highlighting (run with --write). First:",
            file=sys.stderr,
        )
        for fp in need_fix_files[:10]:
            print(f"  {fp}", file=sys.stderr)
    if failed:
        return 2
    if need_fix_files and not args.write:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
