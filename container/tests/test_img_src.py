# version 0.2.1
"""Static gate against <img> tags that cannot show an image.

A page must not ship an <img> that a browser cannot resolve:

  NO_SRC      the tag has no src attribute at all;
  EMPTY_SRC   src="" or a blank value;
  MISSING_FILE a local src that points at a file that does not exist.

"Local" means an absolute path ("/aa_img/x.png"), a path relative to the
folder of the file that holds the tag ("img/x.png"), or a URL on our own
host ("https://example.com/...", "https://www.example.com/..."). Query
string, fragment and URL escaping ("%20") are stripped before the lookup.

Every *.php and *.html under container/src/ is scanned; a tag may span
several lines and is matched in any letter case, but the escaped "&lt;img"
of a code example is not a tag and is skipped.

Images are not in the repository (they are git-ignored), so the file
check needs an image root that holds them, for example the main
checkout's container/src:

    IMG_ROOT=/path/to/a/tree/with/images
    pytest tests/test_img_src.py          # from container/
    python3 tests/test_img_src.py         # same, no pytest, exit 1 on a hit

IMG_SRC_DIR points the scan at another tree; IMG_ROOT defaults to it, so on
the host (where the images are next to the pages) no setup is needed.

A checkout without the images (a worktree, the OpenCode container) needs no
image root: container/docs/image-files.txt lists every image file with its
sha1 and size, and a local src whose path relative to container/src is in
that list counts as present. IMG_LIST points the check at another list; an
empty IMG_LIST= turns the list off, and a missing list file is no error
(disk only, as before).

External URLs, data: URIs and src values built by PHP are not problems:
they are counted for the summary only.
"""

import os
import re
import sys
from functools import cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

# IMG_SRC_DIR: scan a different tree; default is the maintained source tree.
SRC_DIR = Path(os.environ.get("IMG_SRC_DIR", str(Path(__file__).resolve().parents[1] / "src")))

# IMG_ROOT: where the image files live. On the host the images sit inside
# the source tree itself, so the default needs no setup.
IMG_ROOT = Path(os.environ.get("IMG_ROOT", str(SRC_DIR)))

# IMG_LIST: the committed list of the image files under container/src (sha1,
# size, path). An empty IMG_LIST= turns it off; a missing file is no error.
DEFAULT_IMG_LIST = Path(__file__).resolve().parents[1] / "docs" / "image-files.txt"
_IMG_LIST_ENV = os.environ.get("IMG_LIST")
IMG_LIST = DEFAULT_IMG_LIST if _IMG_LIST_ENV is None else (Path(_IMG_LIST_ENV) if _IMG_LIST_ENV.strip() else None)

# --- finding <img> tags ------------------------------------------------------
# "&lt;img" holds no literal "<", so it never matches; the tag body is read
# by hand so a ">" inside a quoted attribute cannot cut the tag short.
IMG_RE = re.compile(r"<img\b", re.IGNORECASE)
SRC_RE = re.compile(r"""(?<![\w-])src\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)

# Our own hosts: their URLs are local paths in disguise.
OWN_HOSTS = tuple(os.environ.get("SITE_OWN_HOSTS", "example.com www.example.com").split())

# Deployment junk (mirrors lint-php.sh): scratch copies never get committed.
SKIP_NAMES = re.compile(r"(?:^| )copy[^/]*\.php$|/sess_[^/]*\.php$")

# True exceptions only, as (path relative to src, line, reason). Keep short.
# A commented-out line is not rendered, so its tag cannot show a broken image.
ALLOWLIST = {
    (".php/_modules/ads_variables.php", 140, "banner assignment commented out with #"),
}

KINDS = ("NO_SRC", "EMPTY_SRC", "MISSING_FILE")

MAX_REPORTED = 40  # keep failure output readable


def _iter_files():
    """Every *.php and *.html under the src dir except deployment junk."""
    for pattern in ("*.php", "*.html"):
        for path in sorted(SRC_DIR.rglob(pattern)):
            if path.is_file() and not SKIP_NAMES.search(path.as_posix()):
                yield path


def _iter_tags(text):
    """(offset, tag text) for every <img ...> tag, quoted values respected."""
    for match in IMG_RE.finditer(text):
        pos = match.end()
        quote = None
        while pos < len(text):
            char = text[pos]
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == ">":
                break
            pos += 1
        yield match.start(), text[match.start() : pos + 1]


def _attr_src(tag):
    """The src value of a tag, None when the tag has no src attribute."""
    match = SRC_RE.search(tag)
    if not match:
        return None
    return next(group for group in match.groups() if group is not None).strip()


def _is_dynamic(src):
    """True for a value PHP builds at render time ("<?php", $var, {$var})."""
    return "$" in src or "<?" in src


def _local_target(src):
    """(path to look up, None) for a local src, (None, reason) otherwise.

    urlsplit also drops "?query" and "#fragment"; unquote turns "%20" back
    into a space. A URL on our own host is a local path in disguise.
    """
    split = urlsplit(src)
    if split.scheme or src.startswith("//"):
        host = split.hostname or src[2:].split("/")[0]
        if host.lower() not in OWN_HOSTS:
            return None, "external"
    return unquote(split.path), None


def _resolve(path, holder):
    """The file behind `path`, read as being held by `holder` inside src."""
    try:
        rel_holder = holder.relative_to(SRC_DIR)
    except ValueError:
        rel_holder = Path("")
    base = IMG_ROOT if path.startswith("/") else IMG_ROOT / rel_holder.parent
    return Path(os.path.normpath(base / path.lstrip("/")))


@cache
def _listed():
    """The paths the image list names, relative to container/src.

    Empty when IMG_LIST is off or the file is not there. A line is
    "sha1  size  path": split on whitespace at most twice, so a path may
    hold spaces.
    """
    if IMG_LIST is None or not IMG_LIST.is_file():
        return frozenset()
    paths = set()
    for line in IMG_LIST.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=2)
        if len(fields) == 3:
            paths.add(fields[2])
    return frozenset(paths)


def _listed_here(path):
    """True when `path` is a listed image, so its tree need not be checked out."""
    listed = _listed()
    for root in (SRC_DIR, IMG_ROOT):
        try:
            if path.relative_to(root).as_posix() in listed:
                return True
        except ValueError:
            continue
    return False


@cache
def _scan():
    """(problems, skipped) over the whole tree, computed once per run.

    problems: (path, lineno, kind, src) for every broken tag;
    skipped:  {"external": n, "data": n, "dynamic": n,
               "dynamic_files": {file relative to src: n}}
    """
    problems = []
    skipped = {"external": 0, "data": 0, "dynamic": 0, "dynamic_files": {}}
    allowed = {(entry[0], entry[1]) for entry in ALLOWLIST}
    for path in _iter_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for offset, tag in _iter_tags(text):
            lineno = text.count("\n", 0, offset) + 1
            rel = path.relative_to(SRC_DIR).as_posix()
            if (rel, lineno) in allowed:
                continue
            src = _attr_src(tag)
            if src is None:
                problems.append((path, lineno, "NO_SRC", ""))
                continue
            if not src:
                problems.append((path, lineno, "EMPTY_SRC", ""))
                continue
            if src.startswith("data:"):
                skipped["data"] += 1
                continue
            if _is_dynamic(src):
                skipped["dynamic"] += 1
                skipped["dynamic_files"][rel] = skipped["dynamic_files"].get(rel, 0) + 1
                continue
            target, reason = _local_target(src)
            if target is None:
                skipped[reason] += 1
                continue
            resolved = _resolve(target, path)
            if not resolved.is_file() and not _listed_here(resolved):
                problems.append((path, lineno, "MISSING_FILE", src))
    return tuple(problems), skipped


def _problems_of(kinds):
    """Problems of the given kinds."""
    problems, _ = _scan()
    return [hit for hit in problems if hit[2] in kinds]


def _fail(kinds, hint):
    """Assert no problem of `kinds`; print the first MAX_REPORTED otherwise."""
    hits = _problems_of(kinds)
    lines = "\n".join(f"  {p}:{ln} {kind} {src}" for p, ln, kind, src in hits[:MAX_REPORTED])
    assert not hits, f"{'/'.join(kinds)}: {len(hits)} <img> tag(s) without a usable image ({hint}). First sites:\n{lines}"


def test_no_img_without_src():
    """Every <img> has a src attribute."""
    _fail(("NO_SRC",), "add the src of the intended image")


def test_no_img_with_empty_src():
    """No src="" or blank src."""
    _fail(("EMPTY_SRC",), "fill in the src of the intended image")


def test_no_img_with_missing_file():
    """Every local src has a file behind it (IMG_ROOT or the image list)."""
    _fail(("MISSING_FILE",), f"no such file under {IMG_ROOT} and not named by IMG_LIST ({IMG_LIST or 'off'})")


def test_attr_src_reads_missing_empty_and_upper_case_src():
    """The src reader tells a missing src, an empty one and an upper-case tag apart."""
    assert _attr_src('<img alt="x">') is None
    assert _attr_src('<img src="">') == ""
    assert _attr_src("<IMG\n  SRC='/aa_img/gone.png'>") == "/aa_img/gone.png"


def test_listed_image_counts_as_present(tmp_path, monkeypatch):
    """A path the image list names counts as present; an unlisted one does not."""
    listing = tmp_path / "image-files.txt"
    listing.write_text(f"{'0' * 40}  42  img/only-on-the-host.png\n{'0' * 40}  7  img/two words.png\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "IMG_LIST", listing)
    _listed.cache_clear()
    try:
        assert _listed_here(IMG_ROOT / "img" / "only-on-the-host.png")
        assert _listed_here(IMG_ROOT / "img" / "two words.png")
        assert not _listed_here(IMG_ROOT / "img" / "not-listed.png")
    finally:
        _listed.cache_clear()


def _report():
    """Print every problem line by line, then the totals per kind."""
    problems, skipped = _scan()
    for path, lineno, kind, src in problems:
        print(f"{path}:{lineno} {kind} {src}")
    print("---")
    if IMG_LIST is None:
        print("image list: off")
    elif IMG_LIST.is_file():
        print(f"image list: {IMG_LIST} ({len(_listed())} paths)")
    else:
        print(f"image list: {IMG_LIST} (missing, disk only)")
    for kind in KINDS:
        print(f"{kind}: {sum(1 for hit in problems if hit[2] == kind)}")
    print(f"external srcs (not checked): {skipped['external']}")
    print(f"data: URIs (not checked): {skipped['data']}")
    print(f"dynamic srcs (not checked): {skipped['dynamic']}")
    for rel, count in sorted(skipped["dynamic_files"].items(), key=lambda item: (-item[1], item[0]))[:10]:
        print(f"  {count:4d} {rel}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(_report())
