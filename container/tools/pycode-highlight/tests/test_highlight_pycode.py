"""Tests for highlight_pycode (stdlib only, no pytest needed to run)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from highlight_pycode import (  # noqa: E402  # pylint: disable=wrong-import-position
    highlight_source,
    is_python_like,
    process_file,
    strip_and_decode,
)

CANONICAL = """import sys
from urllib.request import urlopen


def fetch_words(url):
    # comment here
    story = urlopen(url)
    for line in story:
        print(line)
    return len(url)


if __name__ == "__main__":
    print(1)
"""


def test_canonical_roundtrip():
    out = highlight_source(CANONICAL)
    assert strip_and_decode(out) == CANONICAL
    assert '<span class="c5FD7FF">import</span> sys' in out
    assert '<span class="c5FD7FF">from</span> urllib.request' in out
    assert '<span class="cFCE94F">def</span> <span class="c34E2E2"><b>fetch_words</b></span>' in out
    assert '<span class="c34E2E2"><b>print</b></span>' in out
    assert '<span class="cAD7FA8">&quot;__main__&quot;</span>' in out
    # no extra newlines: same line count
    assert out.count("\n") == CANONICAL.count("\n")


def test_escaping():
    out = highlight_source("s = \"a<b>&'q'\"\n")
    assert strip_and_decode(out) == "s = \"a<b>&'q'\"\n"
    assert "&lt;" in out and "&gt;" in out and "&amp;" in out and "&#x27;" in out


def test_dunder_not_builtin():
    out = highlight_source('if __name__ == "x":\n    pass\n')
    assert "__name__</b>" not in out  # stays plain, like the canonical pages


def test_strict_detector():
    assert is_python_like("a = sys.argv[0]\n", strict=True)
    assert is_python_like("for i in range(3):\n    print(i)\n", strict=True)
    assert not is_python_like("pip install selenium\n", strict=True)
    assert not is_python_like("int main() {\n return 0;\n}\n", strict=True)
    assert not is_python_like("hello prose about python sys\n", strict=True)


def test_process_file_write_and_verify():
    php = """<?php
echo'
<p class="pycode p2">
import sys
x = sys.argv[0]
</p>
<p class="cout1">
CRITICAL:root:hi
</p>
';
"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.php"
        p.write_text(php, encoding="utf-8")
        st = process_file(p, aggressive=False, fix_tag=False, strict_compile=False, write=True)
        assert st["fixed"] == 1
        assert not st["errors"]
        new = p.read_text(encoding="utf-8")
        assert '<span class="c5FD7FF">import</span> sys' in new
        assert "CRITICAL:root:hi" in new  # output untouched
        # idempotent: second run fixes nothing
        st2 = process_file(p, aggressive=False, fix_tag=False, strict_compile=False, write=True)
        assert st2["fixed"] == 0 and st2["ok"] == 1


if __name__ == "__main__":
    for name, fn in sorted([(k, v) for k, v in globals().items() if k.startswith("test_")]):
        fn()
        print(f"OK {name}")
