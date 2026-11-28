#!/usr/bin/env python3
# version 0.1.1
"""Find snake_case method calls that miss the camelCase engine APIs.

phpcs only checks method *declarations*; a page calling
``$page->get_ads_block()`` on a camelCase engine passes lint and fatals
at runtime (see section/errors/wrong-method-dialect.php). This script
closes that gap.

How it works: every ``function camelName()`` declared in the engine
trees (every folder named .php under src) yields its
snake_case shadow (``createHead`` -> ``create_head``). Any
``->shadow(`` or ``::shadow(`` call site is an ERROR. Any other
``->[a-z][a-z0-9]*_[a-z0-9_]*\\(`` call is a WARNING for review
(legacy snake_case helpers like ``get_urls()`` are global functions,
not methods, and are not flagged).

Usage:
  ./find-snake-calls.py [--src DIR] [--staged] [--all]
  --src DIR   document root (default: container/src auto-detected)
  --staged    only git-staged files
  --all       same as default (all *.php under src)

Exit: 0 = no ERROR findings, 1 = at least one (warnings never fail).
"""

# pylint: disable=invalid-name
from __future__ import annotations

import os
import re
import subprocess
import sys

ENGINE_DIRS = ()  # empty: discovered per tree by engine_dirs()


def engine_dirs(src):
    """Every folder named .php under src: the framework of that tree."""
    found = []
    for root, dirs, _files in os.walk(src):
        for name in dirs:
            if name == ".php":
                rel = os.path.relpath(os.path.join(root, name), src)
                found.append(rel.replace(os.sep, "/"))
    return sorted(found)


# Vendored code is not ours — never flag inside it.
VENDOR_DIRS = ("SxGeo",)
METHOD_DEF = re.compile(r"function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
CALL = re.compile(r"(->|::)\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def to_snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def engine_methods(src):
    """camelCase methods declared in engine trees -> {snake: camel}.

    A snake_case name the engine declares too (an old engine with both
    is_mobile() and isMobile()) is a method of its own, not a shadow.
    """
    out = {}
    declared = set()
    for tree in ENGINE_DIRS or engine_dirs(src):
        d = os.path.join(src, *tree.split("/"))
        for root, _, files in os.walk(d):
            if any(v in root.split(os.sep) for v in VENDOR_DIRS):
                continue
            for fn in files:
                if not fn.endswith(".php"):
                    continue
                text = read_text(os.path.join(root, fn))
                if text is None:
                    continue
                for m in METHOD_DEF.finditer(text):
                    name = m.group(1)
                    declared.add(name)
                    if name != name.lower() and "_" not in name:
                        out.setdefault(to_snake(name), name)
    return {snake: camel for snake, camel in out.items() if snake not in declared}


def list_files(src, staged):
    if staged:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        return [f for f in out if f.endswith(".php") and os.path.isfile(f)]
    found = []
    for root, _, files in os.walk(src):
        for fn in files:
            if fn.endswith(".php"):
                found.append(os.path.join(root, fn))
    return found


def main(argv):
    src = None
    staged = False
    args = list(argv)
    if any(a in ("-h", "--help") for a in args):
        print(__doc__.strip())
        return 0
    while args:
        a = args.pop(0)
        if a == "--src" and args:
            src = args.pop(0)
        elif a == "--staged":
            staged = True
        elif a == "--all":
            pass
    if src is None:
        here = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.abspath(os.path.join(here, "..", "..", "src"))
        src = cand if os.path.isdir(cand) else os.getcwd()
    shadows = engine_methods(src)
    errors = warnings = 0
    for f in sorted(list_files(src, staged)):
        if any(v in f.split(os.sep) for v in VENDOR_DIRS):
            continue
        content = read_text(f)
        if content is None:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if not s or s.startswith(("#", "//", "*", "<!--")) or "<!--" in line:
                continue
            if "<code>" in line:
                # displayed code example, not executed code
                continue
            for m in CALL.finditer(line):
                op, name = m.groups()
                if op == "::" and "\\" in line[: m.start()]:
                    # fully-qualified displayed name (e.g. log output
                    # Acme\\Site\\Page::get_ads_block()), not a call
                    continue
                if "_" not in name or name == name.upper():
                    continue
                if name in shadows:
                    print(f"{f}:{i}: ERROR: ->{name}() — engine API is {shadows[name]}()")
                    errors += 1
                elif re.match(r"[a-z][a-z0-9]*(_[a-z0-9]+)+$", name):
                    print(f"{f}:{i}: warning: snake_case method call ->{name}() (review)")
                    warnings += 1
    print(f"snake-case: {errors} error(s), {warnings} warning(s), {len(shadows)} shadows")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
