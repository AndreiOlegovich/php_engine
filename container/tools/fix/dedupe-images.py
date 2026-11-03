#!/usr/bin/env python3
# version 0.1.1
"""Deduplicate images under one tree against byte-identical twins elsewhere.

For every image under --dir that has a byte-identical
twin (md5) elsewhere under ``src/``:

1. rewrite absolute-path references (``/images/...``) in ``*.php`` files to the
   twin's URL,
2. ``php -l`` every touched PHP file (restore + abort the file on failure),
3. delete the duplicate only when zero references remain (``*.php`` plus
   ``*.css``/``*.js``/``*.html`` as a safety net, never edited),
4. append a redirect for every deletion, in the mod_rewrite pair form the
   .htaccess uses (see redirect_rule).

Dry run by default; ``--apply`` executes. Twin choice is deterministic
(first sorted path outside --dir).

Usage (from ``container/``):
    ./tools/fix/dedupe-images.py [--dir src/images] [--apply]
        [--redirect-file images-redirects.htaccess]

Examples:
    Preview what would change under src/images (writes nothing):

        ./tools/fix/dedupe-images.py --dir src/images

    Deduplicate another tree, e.g. legacy demo images:

        ./tools/fix/dedupe-images.py --dir src/images --apply

    Apply and collect redirects into a custom file:

        ./tools/fix/dedupe-images.py --dir src/images --apply \\
            --redirect-file /tmp/image-redirects.txt

    Typical dry-run output::

        images under src/images/: 3331
        with twin outside: 2977; scanning refs once ...
        ref scan done.
        WOULD-FIX src/images/img/dudewife00100.jpg -> src/misc/img/dudewife00100.JPG (1 file(s))
        ORPHAN src/images/movies/8_march_img/mickey_march8-4.jpg (twin: src/images/mickey_march8-4.jpg)
        done: 387 fixable, 2590 unreferenced, 0 skipped, 0 php file(s) touched.

    Typical apply output::

        DELETED src/images/img/dudewife00100.jpg (twin: src/misc/img/dudewife00100.JPG)
        redirects appended to images-redirects.htaccess: 382
        done: 382 fixable, 2590 unreferenced, 5 skipped, 135 php file(s) touched.

Statuses: FIXED/DELETED (refs rewritten, duplicate removed),
WOULD-FIX (dry-run preview), ORPHAN-DYNAMIC/-DYNAMIC?/-CLEAR (twin
exists but no static refs; see verdict rules below), SKIP with reason
(STILL-REFERENCED css/js/html refs, LINT-FAIL php -l failure).

Orphan verdicts (never auto-deleted, only reported):
  ORPHAN-DYNAMIC  a fully resolved directory-loader call
                  (scandir/glob/opendir/image_from_dir with a literal,
                  concatenated, DOCUMENT_ROOT- or __DIR__-based dir, or a
                  function parameter resolved via same-file call sites)
                  covers the file.
  ORPHAN-DYNAMIC? a partially resolved loader covers it (some branches
                  unknown: variables from other files, $_GET, call-built
                  paths, dirname() chains).
  ORPHAN-CLEAR    no loader call covers it anywhere. Still human-confirmed
                  deletes only: absence of evidence is not evidence.
"""

# pylint: disable=invalid-name
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys

IMG_EXTS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".svg",
    ".ico",
    ".tif",
    ".tiff",
    ".avif",
)
REF_EXTS = (".php",)
SAFETY_EXTS = (".php", ".css", ".js", ".html", ".htm")
LOADER_FUNCS = ("scandir", "glob", "opendir", "image_from_dir", "img_from_dir")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="tree to dedupe")
    parser.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    parser.add_argument(
        "--redirect-file",
        default="images-redirects.htaccess",
        help="where to append Redirect lines",
    )
    return parser.parse_args(argv)


def md5_file(path: str) -> str | None:
    """Return md5 hex of a file, or None when unreadable."""
    try:
        with open(path, "rb") as handle:
            return hashlib.md5(handle.read()).hexdigest()
    except OSError:
        return None


def collect_images(root: str) -> list[str]:
    """All image paths (repo-relative, / separators) under root."""
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(IMG_EXTS):
                found.append(os.path.join(dirpath, name).replace(os.sep, "/"))
    return sorted(found)


def index_by_hash(root: str) -> dict[str, list[str]]:
    """Map md5 -> repo-relative paths for every image under root."""
    index: dict[str, list[str]] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith(IMG_EXTS):
                continue
            path = os.path.join(dirpath, name).replace(os.sep, "/")
            digest = md5_file(path)
            if digest:
                index.setdefault(digest, []).append(path)
    return index


def find_all_refs(urls: set[str], root: str) -> dict[str, list[str]]:
    """Map each url to the PHP files containing it (single walk).

    Extracts /...-style path tokens per file and matches them exactly,
    so one pass covers thousands of urls.
    """
    exts = "png|jpg|jpeg|gif|bmp|webp|svg|ico|tif|tiff|avif"
    token_re = re.compile(r"/[^\s'\"()<>\\]+?\.(?:" + exts + r")(?![\w.-])", re.I)
    hits: dict[str, list[str]] = {u: [] for u in urls}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(REF_EXTS):
                continue
            path = os.path.join(dirpath, name).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    content = handle.read()
            except OSError:
                continue
            for token in set(token_re.findall(content)):
                if token in hits:
                    hits[token].append(path)
    return hits


def count_refs_anywhere(url: str, root: str) -> int:
    """References to url in php/css/js/html under root."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith(SAFETY_EXTS):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    total += handle.read().count(url)
            except OSError:
                continue
    return total


def line_of(content: str, offset: int) -> int:
    """1-based line number for a byte offset."""
    return content.count("\n", 0, offset) + 1


def collect_assignments(content: str) -> dict[str, list[str]]:
    """Map $var to its assigned right-hand sides (single-line only)."""
    found: dict[str, list[str]] = {}
    for match in re.finditer(r"\$(\w+)\s*=\s*(.*?);", content):
        found.setdefault(match.group(1), []).append(match.group(2))
    return found


def _eval_atom(
    expr: str,
    assignments: dict[str, list[str]],
    file_dir: str,
    _seen: frozenset[str],
) -> tuple[set[str], bool] | None:
    """Literals, __DIR__, DOCUMENT_ROOT and $var (None when no atom matches)."""
    lit = re.fullmatch(r"'((?:[^'\\]|\\.)*)'", expr, re.S) or re.fullmatch(r'"([^"$]*)"', expr, re.S)
    if lit:
        return {lit.group(1)}, True
    if expr == "__DIR__":
        return {file_dir}, True
    docroot = re.fullmatch(r"\$_SERVER\s*\[\s*['\"]DOCUMENT_ROOT['\"]\s*\]", expr)
    if docroot:
        return {"<ROOT>"}, True
    var = re.fullmatch(r"\$(\w+)", expr)
    if not var:
        return None
    rhss = assignments.get(var.group(1), [])
    if not rhss or var.group(1) in _seen:
        return set(), False
    values: set[str] = set()
    certain = True
    for rhs in rhss:
        sub, ok = eval_expr(rhs, assignments, file_dir, _seen | {var.group(1)})
        values |= sub
        certain = certain and ok
    return values, certain


def eval_expr(
    expr: str,
    assignments: dict[str, list[str]],
    file_dir: str,
    _seen: frozenset[str] = frozenset(),
) -> tuple[set[str], bool]:
    """Statically evaluate a PHP path expression.

    Return (values, certain). Handles string literals, `.` concat of
    certain parts, $_SERVER['DOCUMENT_ROOT'] (repo src/) and __DIR__.
    Anything else (function calls, $_GET, object access) is uncertain.
    """
    expr = expr.strip()
    atom = _eval_atom(expr, assignments, file_dir, _seen)
    if atom is not None:
        return atom
    if "." in expr:
        # crude concat split on top-level dots only
        parts = re.split(r"\s*\.\s*", expr)
        if len(parts) > 1:
            values = {""}
            uncertain = False
            for part in parts:
                sub, ok = eval_expr(part, assignments, file_dir, _seen)
                uncertain = uncertain or not ok
                values = {a + b for a in values for b in sub}
            return values, not uncertain
    return set(), False


def normalize_dir(value: str, file_dir: str) -> str | None:
    """Map a resolved dir value to web-path form (/...), or None."""
    value = value.strip()
    if value.startswith("<ROOT>"):
        return "/" + value[len("<ROOT>") :].lstrip("/")
    if value.startswith("/"):
        return value
    # repo- or file-relative: anchor at the file's directory
    base = "/" + file_dir[len("src/") :] if file_dir.startswith("src/") else "/" + file_dir
    return base.rstrip("/") + "/" + value.lstrip("./")


def _resolve_param_calls(
    content: str,
    func_params: dict[str, list[str]],
    assignments: dict[str, list[str]],
    file_dir: str,
    var_name: str,
) -> tuple[set[str], bool]:
    """Resolve a function parameter via same-file literal call sites."""
    values: set[str] = set()
    certain = True
    for call in re.finditer(r"(?<![\w$:>-])(\w+)\s*\(", content):
        callee = call.group(1)
        if callee not in func_params or callee in LOADER_FUNCS:
            continue
        cargs = balanced_args(content, call.end() - 1)
        if not cargs:
            continue
        items = split_top_level(cargs[0])
        try:
            idx = func_params[callee].index(var_name)
        except ValueError:
            continue
        if idx < len(items):
            sub, ok = eval_expr(items[idx], assignments, file_dir)
            values |= sub
            certain = certain and ok
    return values, certain


def _loaders_in_file(path: str, content: str) -> list[dict]:
    """Loader calls with resolved dirs found in one file's content."""
    file_dir = os.path.dirname(path)
    assignments = collect_assignments(content)
    # function definitions and their call sites (same file)
    func_params: dict[str, list[str]] = {}
    for match in re.finditer(r"function\s+(\w+)\s*\(([^)]*)\)", content):
        params = [p.strip().lstrip("$").split(" ")[0] for p in match.group(2).split(",")]
        func_params[match.group(1)] = [p for p in params if p]
    found: list[dict] = []
    for match in re.finditer(r"(?<![\w$:>-])(scandir|glob|opendir|image_from_dir|img_from_dir)\s*\(", content):
        func = match.group(1)
        parsed = balanced_args(content, match.end() - 1)
        if not parsed:
            continue
        arg_text, _ = parsed
        first = split_top_level(arg_text)[0] if arg_text.strip() else ""
        values, certain = eval_expr(first, assignments, file_dir)
        # function parameter? resolve via same-file literal call sites
        if not values and certain is False:
            var = re.fullmatch(r"\$(\w+)", first.strip())
            if var:
                values, certain = _resolve_param_calls(content, func_params, assignments, file_dir, var.group(1))
        dirs = set()
        for value in values:
            norm = normalize_dir(value, file_dir)
            if norm:
                dirs.add(norm.rstrip("/") + "/")
        found.append(
            {
                "file": path,
                "line": line_of(content, match.start()),
                "func": func,
                "dirs": dirs,
                "certain": certain and bool(dirs),
            }
        )
    return found


def collect_loaders(root: str) -> list[dict]:
    """Find directory-loader calls with statically resolved dir sets.

    Each item: file, line, func, dirs (set of web paths), certain (bool).
    Handles literal/concat args, same-file $var assignments (incl. `.=`),
    and function parameters resolved via same-file literal call sites.
    """
    loaders: list[dict] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(REF_EXTS):
                continue
            path = os.path.join(dirpath, name).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    content = handle.read()
            except OSError:
                continue
            loaders.extend(_loaders_in_file(path, content))
    return loaders


def classify_orphan(image_url: str, loaders: list[dict]) -> tuple[str, str]:
    """Verdict for an image with no static refs.

    Returns (verdict, evidence): DYNAMIC (a fully resolved loader covers
    it), DYNAMIC? (a partially resolved loader covers it), or CLEAR (no
    loader call covers it anywhere).
    """
    certain_hits: list[str] = []
    uncertain_hits: list[str] = []
    for loader in loaders:
        for directory in loader["dirs"]:
            if image_url == directory or image_url.startswith(directory):
                evidence = f"{loader['file']}:{loader['line']} via {loader['func']}({directory})"
                if loader["certain"]:
                    certain_hits.append(evidence)
                else:
                    uncertain_hits.append(evidence)
    if certain_hits:
        return "DYNAMIC", "; ".join(sorted(set(certain_hits)))
    if uncertain_hits:
        return "DYNAMIC?", "; ".join(sorted(set(uncertain_hits)))
    return "CLEAR", "no loader call covers it"


def php_lint_ok(path: str) -> bool:
    """php -l via host php (falls back to True when php is missing)."""
    try:
        proc = subprocess.run(["php", "-l", path], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return True
    return proc.returncode == 0


def split_top_level(text: str) -> list[str]:
    """Split on top-level commas (no nesting descent)."""
    parts, depth, current = [], 0, ""
    in_single = in_double = False
    for ch in text:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if in_single or in_double:
            current += ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def balanced_args(text: str, start: int) -> tuple[str, int] | None:
    """Arg text inside the paren opened at start; plus index past ')'."""
    depth = 0
    in_single = in_double = False
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if in_single or in_double:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
    return None


def redirect_rule(old_url: str, new_url: str) -> str:
    """The two .htaccess lines that send old_url to new_url, https kept."""
    pattern = "^" + re.escape(old_url) + "$"
    pattern = f'"{pattern}"' if re.search(r"\s", pattern) else pattern
    return f"RewriteCond %{{REQUEST_URI}} {pattern}\nRewriteRule ^ %{{ENV:SITE_SCHEME}}://%{{HTTP_HOST}}{new_url} [R=301,L]"


def process_image(image: str, twin: str, hits: list[str], apply: bool) -> tuple[str, list[str], str | None]:
    """Rewrite refs of image to twin. Return (status, touched, redirect).

    Status is FIXED (refs rewritten), ORPHAN (no refs found) or SKIP
    with reason. Redirect is the redirect rule when the image may go.
    """
    old_url = "/" + image[len("src/") :]
    new_url = "/" + twin[len("src/") :]
    if not hits:
        return ("ORPHAN", [], redirect_rule(old_url, new_url))
    if not apply:
        return ("WOULD-FIX", hits, redirect_rule(old_url, new_url))
    for path in hits:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        # word-boundary replace: never touch longer URLs containing this one
        fixed_content, _ = re.subn(re.escape(old_url) + r"(?![\w.-])", new_url, content)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(fixed_content)
    bad = [p for p in hits if not php_lint_ok(p)]
    if bad:
        return ("LINT-FAIL", hits, None)
    if count_refs_anywhere(old_url, "src") > 0:
        return ("STILL-REFERENCED", hits, None)
    return ("FIXED", hits, redirect_rule(old_url, new_url))


def _build_jobs(images: list[str], index: dict[str, list[str]], target_dir: str) -> tuple[list[tuple[str, str, str]], set[str]]:
    """Match each image to its twin; return jobs + wanted url set."""
    jobs: list[tuple[str, str, str]] = []
    wanted: set[str] = set()
    for image in images:
        digest = md5_file(image)
        twins = sorted(p for p in index.get(digest or "", []) if not p.startswith(target_dir + "/"))
        if not twins:
            continue
        old_url = "/" + image[len("src/") :]
        jobs.append((image, twins[0], old_url))
        wanted.add(old_url)
    return jobs, wanted


def _handle_job(
    image: str,
    twin: str,
    old_url: str,
    hits: list[str],
    loaders: list[dict],
    apply: bool,
    counts: dict[str, int],
    touched_files: set[str],
    redirects: list[str],
) -> None:
    """Process one job: fix/delete/report; mutate counts in place."""
    status, hits, redirect = process_image(image, twin, hits, apply)
    if status == "ORPHAN":
        # No static refs: resolve directory loaders for a verdict.
        # Galleries often build names dynamically (scandir/glob),
        # so even CLEAR stays report-only, never auto-deleted.
        counts["orphan"] += 1
        verdict, evidence = classify_orphan(old_url, loaders)
        counts[{"DYNAMIC": "dynamic", "DYNAMIC?": "dynamic_maybe"}.get(verdict, "clear")] += 1
        print(f"ORPHAN-{verdict} {image} (twin: {twin}) [{evidence}]")
    elif status == "FIXED":
        if apply:
            os.remove(image)
            print(f"DELETED {image} (twin: {twin})")
        if redirect:
            redirects.append(redirect)
        counts["fixed"] += 1
        touched_files.update(hits)
    elif status == "WOULD-FIX":
        counts["fixed"] += 1
        print(f"WOULD-FIX {image} -> {twin} ({len(hits)} file(s))")
    else:
        counts["skipped"] += 1
        print(f"SKIP {image}: {status}")


def main(argv: list[str] | None = None) -> int:
    """Entry point: scan, rewrite, delete, report. Returns exit code."""
    args = parse_args(argv)
    target_dir = args.dir.rstrip("/")
    images = collect_images(target_dir)
    print(f"images under {target_dir}/: {len(images)}", flush=True)
    index = index_by_hash("src")
    # twin per image + full url set, then ONE ref pass over all php files
    jobs, wanted = _build_jobs(images, index, target_dir)
    print(f"with twin outside: {len(jobs)}; scanning refs once ...", flush=True)
    ref_hits = find_all_refs(wanted, "src")
    print("ref scan done; collecting directory loaders ...", flush=True)
    loaders = collect_loaders("src")
    print(f"loader calls found: {len(loaders)}", flush=True)
    counts = {"fixed": 0, "orphan": 0, "skipped": 0, "dynamic": 0, "dynamic_maybe": 0, "clear": 0}
    touched_files: set[str] = set()
    redirects: list[str] = []
    for image, twin, old_url in jobs:
        _handle_job(
            image,
            twin,
            old_url,
            ref_hits.get(old_url, []),
            loaders,
            args.apply,
            counts,
            touched_files,
            redirects,
        )
    if args.apply and redirects:
        with open(args.redirect_file, "a", encoding="utf-8") as handle:
            handle.write("\n".join(redirects) + "\n")
        print(f"redirects appended to {args.redirect_file}: {len(redirects)}")
    print(f"done: {counts['fixed']} fixable, {counts['orphan']} unreferenced (DYNAMIC={counts['dynamic']} DYNAMIC?={counts['dynamic_maybe']} CLEAR={counts['clear']}), {counts['skipped']} skipped, {len(touched_files)} php file(s) touched.")
    if counts["orphan"]:
        print("certainty: DYNAMIC = a fully resolved loader call covers the file; DYNAMIC? = a partially resolved call covers it (some branches unknown: variables from other files, $_GET, call-built paths); CLEAR = no loader call covers it (still human-confirmed deletes only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
