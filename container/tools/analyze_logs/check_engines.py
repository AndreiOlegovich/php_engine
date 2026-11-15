# version 0.0.1
"""Fail when a .php engine file requires code outside its own tree.

Engines: container/src/.php, container/src/extra/.php,
container/src/section/.php. A $root-anchored require/include pointing at
another engine couples the trees and re-arms double-declare fatals
(same FQCN, two inodes). Same-FQCN twins are reported for information
only — legitimate divergence exists (images, right_blocks, footers).

Usage:
  check_engines.py [--src DIR]
Exit 1 on any cross-engine require, else 0.
"""

from __future__ import annotations

import argparse
import os
import re

ENGINES = (".php", "extra/.php", "section/.php")

REQUIRE_RE = re.compile(r"""(?:require|include)(?:_once)?\s*\(\s*\$root\s*\.\s*['"]([^'"]+)['"]""")
NAMESPACE_RE = re.compile(r"^\s*namespace\s+([^;]+);", re.MULTILINE)
CLASS_DEF_RE = re.compile(r"^\s*(?:final\s+|abstract\s+)?class\s+(\w+)", re.MULTILINE)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define and parse CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="", help="local container/src dir")
    return parser.parse_args(argv)


def default_src() -> str:
    """Locate container/src from the script location."""
    here = os.path.dirname(os.path.realpath(__file__))
    repo = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(repo, "container", "src")


def engine_of(target: str) -> str | None:
    """Return which engine tree a $root-anchored target belongs to."""
    for engine in ENGINES:
        if target == "/" + engine or target.startswith("/" + engine + "/"):
            return engine
    return None


def find_cross_links(src_root: str) -> list[tuple[str, str]]:
    """List (file, target) requires escaping their own engine tree."""
    hits = []
    for engine in ENGINES:
        top = os.path.join(src_root, engine)
        for root, _, files in os.walk(top):
            for name in sorted(files):
                if not name.endswith(".php"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
                for match in REQUIRE_RE.finditer(text):
                    target = match.group(1)
                    owner = engine_of(target)
                    if owner is not None and owner != engine:
                        hits.append((os.path.relpath(path, src_root), target))
    return hits


def find_fqcn_twins(src_root: str) -> dict[str, list[str]]:
    """Map FQCNs declared in more than one engine tree to their files."""
    owners: dict[str, list[str]] = {}
    for engine in ENGINES:
        top = os.path.join(src_root, engine)
        for root, _, files in os.walk(top):
            for name in sorted(files):
                if not name.endswith(".php"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
                namespace = NAMESPACE_RE.search(text)
                prefix = namespace.group(1).strip() + "\\" if namespace else ""
                for match in CLASS_DEF_RE.finditer(text):
                    fqcn = prefix + match.group(1)
                    owners.setdefault(fqcn, []).append(os.path.relpath(path, src_root))
    return {k: v for k, v in owners.items() if len({p.split("/")[0] for p in v}) > 1}


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    src_root = args.src or default_src()
    cross_links = find_cross_links(src_root)
    for path, target in cross_links:
        print(f"CROSS-LINK {path} -> {target}")
    twins = find_fqcn_twins(src_root)
    if twins:
        print(f"same-FQCN twins (informational): {len(twins)}")
    if cross_links:
        print(f"FAILED: {len(cross_links)} cross-engine require(s)")
        return 1
    print("OK: no cross-engine requires")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
