# version 0.1.0
"""Static gate against the HTML/PHP reflow damage (incident of 2026-09-28).

Commit 2af4d055 ("Bring src/ to zero PHPCS violations and camelCase") ran
an HTML reflower over container/src/ and mangled ~230 files:

  * <span style=/class=/src=/href= attributes were split across lines
    ("<span style=$" + indented value on the next line);
  * Python tokens were broken at dots and spaces ("@pytest" + a new
    line ". fixture(scope=", "random.choice(symbols)" became
    "random" + a new line ". choice(symbols)");
  * ls -l output was split after the file-mode trailing dot
    ("lrwxrwxrwx" + a new line ". 1 root root ...").

Rendered pages therefore show wrong, non-executable code examples.
The repo was repaired in bulk; this module pins the repaired state so
the reflower damage cannot be committed again. Three cheap text checks
run over every *.php under container/src/:

ATTR_BREAK   no HTML attribute dangling at end of line.
DOT_LINES    no content line starting with ". " + token character
             (lowercase letter, digit or "("). Real shell commands
             (". script.sh", ". test_env/bin/activate") and the
             linux/man/top.php man-page bullets are allow-listed.
GT_IN_TAG    no "&gt;" inside an HTML tag: the escaped bracket made
             browsers swallow the next attribute ("=" vanished from
             route_name assignments on the fixture page).

Runs under pytest (whole-tree scan, ~1s), as a standalone CLI with the
same checks, and from the pre-commit hook check-reflow.sh:

    pytest tests/test_html_code_corruption.py          # from container/
    python3 tests/test_html_code_corruption.py         # same, no pytest
"""

import os
import re
import sys
from pathlib import Path

# REFLOW_SRC_DIR: scan a different tree (detector self-tests / incident
# replays); default is the maintained source tree.
SRC_DIR = Path(os.environ.get("REFLOW_SRC_DIR", str(Path(__file__).resolve().parents[1] / "src")))

# --- check 1: attribute left dangling at end of line -------------------------
# <span style=$ / <div class=$ / <img src=$ / <a href=$ (value on next line).
# Per-line (applied only to lines ending with "="); no multiline flag.
ATTR_LINE_RE = re.compile(r"^.*<[a-zA-Z][^<>]*[ \t](?:style|class|src|href|alt|data-[a-z-]+)=[ \t]*$")

# --- check 2: content line starting with ". " + token char -------------------
# The reflower broke code at dots: "random.choice" -> "random" newline
# ". choice(symbols)". Legit PHP string concat starts with ". '" / ". $"
# and is excluded; sentence-like prose starts with a capital/Cyrillic
# letter and is not matched.
DOT_LINE_RE = re.compile(r"(?m)^[ \t]+\. (?=[a-z0-9(])")
DOT_CONCAT_RE = re.compile(r"^[ \t]+\. *(['\"]|\$)")

# Legit ". " lines (shell source/exec commands pasted verbatim).
DOT_ALLOWLIST_RE = re.compile(r"^[ \t]+\. (?:test_env/bin/activate|script\.sh|bash_script\.sh)[ \t]*$")

# --- check 3: escaped bracket inside an HTML tag -----------------------------
# <span style="color: #D0D0D0"&gt;&#61; </span> — the "&gt;" closes the tag
# for the browser, so "&#61;" (the "=" of an assignment) disappears.
GT_IN_TAG_RE = re.compile(r"<[a-zA-Z][a-zA-Z0-9]*\b[^<>\n]*&gt;")

# --- check 4: stray PHP string terminator inside HTML -------------------------
# <p class="p2">';</p> — a "';" closing the echo string inside markup.
# Seen 3 times on ao/api/post/post_result.php (Oct 2026, writer unknown);
# the page then fatals: unexpected token "<", expecting end of file.
# The ">" guard keeps code samples like "...\'...\';</code>" unflagged.
STRTERM_IN_TAG_RE = re.compile(r">';</[a-zA-Z]")

# Deployment junk (mirrors lint-php.sh): scratch copies never get committed.
SKIP_NAMES = re.compile(r"(?:^| )copy[^/]*\.php$|/sess_[^/]*\.php$")

# Man-page tables/bullets in linux/man/top.php legitimately start with ". "
# (e.g. ". malloc()            |" or ". provide a scheduling boost ...").
MAN_PAGE_SUFFIXES = ("linux/man/top.php",)

# Commented-out PHP may contain pasted HTML with dangling attributes.
COMMENT_RE = re.compile(r"^[ \t]*#")

MAX_REPORTED = 40  # keep failure output readable


def _iter_php_files():
    """All *.php under container/src/ except deployment junk."""
    return sorted(p for p in SRC_DIR.rglob("*.php") if p.is_file() and not SKIP_NAMES.search(p.as_posix()))


def _dot_damage(stripped, line, is_man):
    """True when a ". " line is reflower dot-splitting, not real content."""
    return bool(not is_man and stripped.startswith(". ") and DOT_LINE_RE.match(line) and not DOT_CONCAT_RE.match(line) and not DOT_ALLOWLIST_RE.match(line))


def _find(text, path, kinds):
    """(file, lineno, line) for every damage site covered by `kinds`."""
    hits = []
    want_attr, want_dot, want_gt = "attr" in kinds, "dot" in kinds, "gt" in kinds
    want_strterm = "strterm" in kinds
    is_man = path.as_posix().endswith(MAN_PAGE_SUFFIXES)
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or COMMENT_RE.match(line):
            continue
        stripped = line.strip()
        # cheap endswith / startswith / find probes gate every regex below
        if want_attr and line.rstrip().endswith("=") and ATTR_LINE_RE.match(line):
            hits.append((lineno, line))
        if want_dot and _dot_damage(stripped, line, is_man):
            hits.append((lineno, line))
        if want_gt and "&gt;" in line and GT_IN_TAG_RE.search(line):
            hits.append((lineno, line))
        if want_strterm and "';" in line and STRTERM_IN_TAG_RE.search(line):
            hits.append((lineno, line))
    return hits


def _check(kinds):
    """Damage sites over the whole src/ tree for the given kinds."""
    hits = []
    for path in _iter_php_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [(path, *h) for h in _find(text, path, kinds)]
    return hits


def _fail(name, hits):
    """Assert `hits` empty; print first MAX_REPORTED sites otherwise."""
    assert not hits, f"{name}: {len(hits)} damage site(s) in container/src/ — this is the 2026-09-28 reflower corruption returning. Join the line with the previous one (restore the code example); see the module docstring of tests/test_html_code_corruption.py. First sites:\n" + "\n".join(f"  {p}:{ln}: {line.strip()[:110]}" for p, ln, line in hits[:MAX_REPORTED])


def test_no_broken_html_attributes():
    """No <span style=$ / class=$ / src=$ / href=$ dangling attributes."""
    _fail("broken attribute", _check({"attr"}))


def test_no_broken_dot_lines():
    """No ". choice(...)" / ". fixture(" lines from dot-broken code."""
    _fail("dot-broken line", _check({"dot"}))


def test_no_escaped_bracket_in_tag():
    """No "&gt;" inside an HTML tag (the "=" -eating fixture-page bug)."""
    _fail("escaped bracket in tag", _check({"gt"}))


def test_no_stray_php_terminator_in_markup():
    """No "';" closing an echo string inside HTML markup (post_result bug)."""
    _fail("stray terminator in markup", _check({"strterm"}))


def test_detector_flags_known_damage():
    """The detector must flag the four incident signatures verbatim."""
    corrupted = '<span style=\n    "color: #D0D0D0">route_name</span>\n        <span style=\n            "color: #D0D0D0">random</span>\n            . choice(symbols)</span>\n            . 1 root root     23 Feb 15 17:10\n<span style="color: #D0D0D0"&gt;&#61; </span>\n<span class=\n    "orange">-m</span>\n<h1>Title</h1><p class="p2">\';</p></main>\';\n'
    assert _find(corrupted, Path("sample.php"), {"attr", "dot", "gt", "strterm"})


if __name__ == "__main__":
    all_hits = _check({"attr", "dot", "gt", "strterm"})
    if all_hits:
        print(f"FAIL: {len(all_hits)} reflow damage site(s):", file=sys.stderr)
        for path, lineno, line in all_hits[:MAX_REPORTED]:
            print(f"  {path}:{lineno}: {line.strip()[:110]}", file=sys.stderr)
        sys.exit(1)
    print("OK: no reflow damage in container/src/")
