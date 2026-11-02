"""Static audit of the AI assistant setup: AGENTS.md, skills, agents, /ai/ guides.

Each check prints "ok" or one "FAIL" line per problem:
  frontmatter  skill, agent and command files open with --- and every
               key: value line parses (a value with ": " or a leading * must
               be quoted, or YAML rejects the whole file)
  skills       every skill has a description and name: <id> (the directory
               name for SKILL.md, the file stem for a flat skill)
  references   files named in AGENTS.md and the skills exist
  links        internal links and #anchors between /ai/ pages resolve
  models       every pinned opencode/<id> is in the live catalog (--offline skips)

Usage (any working directory):
  python3 container/tools/ai/audit_agent_setup.py [--offline]
Exit: 0 every check passed, 1 at least one FAIL.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from check_opencode_models import MODELS_URL, fetch_live, pinned_ids

REPO_ROOT = Path(__file__).resolve().parents[3]
_KEY_VALUE = re.compile(r"^\s*(?:-\s+)?([A-Za-z][\w-]*):\s+(.*)$")
_PATH_TOKEN = re.compile(r"(?<![\w/.-])((?:\.\./)+[\w./-]+|(?:\.claude|\.opencode|container|backoffice-container|references)/[\w./-]+|[\w-]+\.md)")
# Skill locations OpenCode or Claude Code read; the last two are the pre-2026-10-22 layout.
SKILL_GLOBS = (".claude/skills/*/SKILL.md", ".opencode/skills/*/SKILL.md", ".opencode/skills/*.md", "container/src/ai/.opencode/skills/*.md")
_HREF = re.compile(r'href="(/[^"#?]*)?(?:\?[^"#]*)?(#[^"]*)?"')
_ID = re.compile(r'id="([^"]+)"')


def skill_files(root: Path) -> list[Path]:
    return [path for pattern in SKILL_GLOBS for path in sorted(root.glob(pattern))]


def skill_id(path: Path) -> str:
    """The id the path gives: directory name for SKILL.md, file stem otherwise."""
    return path.parent.name if path.name == "SKILL.md" else path.stem


def setup_files(root: Path) -> list[Path]:
    """Skill, agent and command files the assistants parse."""
    patterns = (".claude/agents/*.md", ".opencode/agents/*.md", ".opencode/commands/*.md")
    return skill_files(root) + [path for pattern in patterns for path in sorted(root.glob(pattern))]


def frontmatter(path: Path) -> list[str] | None:
    """Lines between the opening and closing ---, or None when missing."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return None
    return lines[1 : lines.index("---", 1)]


def unsafe_value(value: str) -> str | None:
    """Why an unquoted YAML scalar breaks parsing, or None when it is fine."""
    value = value.strip()
    if not value or value[0] in "\"'[{|>":
        return None
    if value[0] in "*&!%@`":
        return f"value starts with {value[0]!r}; quote it"
    if ": " in value or value.endswith(":"):
        return "value contains ': '; quote it"
    if " #" in value:
        return "value contains ' #' (starts a comment); quote it"
    return None


def check_frontmatter(root: Path) -> Iterator[str]:
    for path in setup_files(root):
        lines = frontmatter(path)
        rel = path.relative_to(root)
        if lines is None:
            yield f"{rel}: no frontmatter between --- lines"
            continue
        for number, line in enumerate(lines, 2):
            match = _KEY_VALUE.match(line)
            reason = match and unsafe_value(match.group(2))
            if reason:
                yield f"{rel}:{number}: {reason}"


def frontmatter_fields(path: Path) -> dict[str, str]:
    fields = {}
    for line in frontmatter(path) or []:
        match = _KEY_VALUE.match(line)
        if match and not line.startswith((" ", "-")):
            fields[match.group(1)] = match.group(2).strip().strip("\"'")
    return fields


def check_skills(root: Path) -> Iterator[str]:
    for path in skill_files(root):
        fields = frontmatter_fields(path)
        rel = path.relative_to(root)
        if fields.get("name") != skill_id(path):
            yield f"{rel}: name {fields.get('name')!r} is not the id {skill_id(path)!r} (Agent Skills names are the kebab-case id)"
        if not fields.get("description"):
            yield f"{rel}: no description, so no assistant will ever load it"


def check_references(root: Path) -> Iterator[str]:
    docs = [root / "AGENTS.md", *skill_files(root)]
    for doc in (path for path in docs if path.is_file()):
        for token in sorted(set(_PATH_TOKEN.findall(doc.read_text(encoding="utf-8")))):
            token = token.rstrip(".,:;)")
            bases = (doc.parent,) if token.startswith("../") else (doc.parent, root, root / "container")
            if token != "SKILL.md" and not any((base / token).exists() for base in bases):
                yield f"{doc.relative_to(root)}: {token} does not exist"


def ai_pages(root: Path) -> dict[str, str]:
    """URL -> source for every /ai/ page."""
    src = root / "container" / "src"
    return {f"/{path.parent.relative_to(src)}/": path.read_text(encoding="utf-8") for path in (src / "ai").rglob("index.php")}


def check_links(root: Path) -> Iterator[str]:
    src = root / "container" / "src"
    pages = ai_pages(root)
    anchors = {url: set(_ID.findall(text)) for url, text in pages.items()}
    for url, text in sorted(pages.items()):
        for match in _HREF.finditer(text):
            target, fragment = match.group(1) or url, match.group(2)
            if target in pages:
                if fragment and fragment[1:] not in anchors[target]:
                    yield f"{url}: {match.group(0)} has no such anchor"
            elif not (src / target.strip("/")).exists():
                yield f"{url}: {match.group(0)} has no such page"


def check_models(root: Path) -> Iterator[str]:
    pins = pinned_ids(root)
    try:
        live = fetch_live()
    except (OSError, ValueError, KeyError) as error:
        yield f"cannot read {MODELS_URL}: {error}"
        return
    for model, files in sorted(pins.items()):
        if model not in live:
            yield f"opencode/{model} is not in the live catalog ({', '.join(str(p.relative_to(root)) for p in files)})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--offline", action="store_true", help="skip the live model-id check")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: this checkout)")
    args = parser.parse_args(argv)
    checks = {"frontmatter": check_frontmatter, "skills": check_skills, "references": check_references, "links": check_links}
    if not args.offline:
        checks["models"] = check_models
    failed = False
    for name, check in checks.items():
        problems = list(check(args.root))
        failed = failed or bool(problems)
        print(f"ok:   {name}" if not problems else "\n".join(f"FAIL: {name}: {problem}" for problem in problems))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
