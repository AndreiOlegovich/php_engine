# version 0.1.0
"""Tests for tools/ai/check_opencode_models.py and tools/ai/audit_agent_setup.py (offline)."""

import sys
from pathlib import Path

import pytest

TOOLS_AI = Path(__file__).resolve().parent.parent.parent / "tools" / "ai"

sys.path.insert(0, str(TOOLS_AI))

import audit_agent_setup as audit  # pylint: disable=wrong-import-position,import-error  # noqa: E402
import check_opencode_models as models  # pylint: disable=wrong-import-position,import-error  # noqa: E402

FREE_LIST = ["deepseek-v4.1-flash-free", "glm-5.3-flash-free"]


def make_repo(root: Path, free_id: str = "old-free") -> Path:
    (root / ".opencode" / "agents").mkdir(parents=True)
    (root / "opencode.jsonc").write_text(f'{{\n  // default\n  "model": "opencode/{free_id}",\n  "permissions": []\n}}\n', encoding="utf-8")
    (root / ".opencode" / "agents" / "triage.md").write_text(f"---\ndescription: Triage\nmodel: opencode/{free_id}\n---\nBody\n", encoding="utf-8")
    (root / ".opencode" / "agents" / "builder.md").write_text("---\ndescription: Builder\nmodel: opencode/gpt-6-luna#high\n---\nBody\n", encoding="utf-8")
    return root


def no_ask(prompt: str) -> str:
    raise AssertionError(f"unexpected question: {prompt}")


def test_pinned_ids_strip_provider_and_variant(tmp_path):
    pins = models.pinned_ids(make_repo(tmp_path))
    assert sorted(pins) == ["gpt-6-luna", "old-free"]
    assert [path.name for path in pins["old-free"]] == ["opencode.jsonc", "triage.md"]


def test_all_live_changes_nothing(tmp_path):
    root = make_repo(tmp_path, "glm-5.3-flash-free")
    before = (root / "opencode.jsonc").read_text(encoding="utf-8")
    code = models.run(root, lambda: {"glm-5.3-flash-free", "gpt-6-luna", "deepseek-v4.1-flash-free"}, no_ask, FREE_LIST)
    assert code == 0
    assert (root / "opencode.jsonc").read_text(encoding="utf-8") == before


def test_missing_free_id_switches_to_first_live_in_list(tmp_path):
    root = make_repo(tmp_path)
    code = models.run(root, lambda: {"glm-5.3-flash-free", "gpt-6-luna"}, no_ask, FREE_LIST)
    assert code == 0
    config = (root / "opencode.jsonc").read_text(encoding="utf-8")
    assert '"model": "opencode/glm-5.3-flash-free"' in config
    assert "// default" in config
    assert "model: opencode/glm-5.3-flash-free\n" in (root / ".opencode" / "agents" / "triage.md").read_text(encoding="utf-8")
    assert not list(root.rglob(".*.tmp"))


def test_check_only_reports_without_writing(tmp_path):
    root = make_repo(tmp_path)
    code = models.run(root, lambda: {"glm-5.3-flash-free", "gpt-6-luna"}, None, FREE_LIST, check_only=True)
    assert code == 2
    assert "opencode/old-free" in (root / "opencode.jsonc").read_text(encoding="utf-8")


def test_no_listed_free_id_asks_and_uses_the_choice(tmp_path):
    root = make_repo(tmp_path)
    answers = iter(["1"])
    code = models.run(root, lambda: {"other-free", "gpt-6-luna"}, lambda _prompt: next(answers), FREE_LIST)
    assert code == 0
    assert "opencode/other-free" in (root / "opencode.jsonc").read_text(encoding="utf-8")


def test_no_listed_free_id_without_terminal_fails(tmp_path):
    root = make_repo(tmp_path)
    assert models.run(root, lambda: {"other-free", "gpt-6-luna"}, None, FREE_LIST) == 2


def test_missing_paid_id_is_never_switched(tmp_path):
    root = make_repo(tmp_path, "glm-5.3-flash-free")
    assert models.run(root, lambda: {"glm-5.3-flash-free"}, None, FREE_LIST) == 1
    assert "opencode/gpt-6-luna#high" in (root / ".opencode" / "agents" / "builder.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(("answer", "code"), [("y", 0), ("", 3)])
def test_unreachable_endpoint_asks(tmp_path, answer, code):
    def offline() -> set[str]:
        raise OSError("offline")

    assert models.run(make_repo(tmp_path), offline, lambda _prompt: answer, FREE_LIST) == code


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("value", "bad"),
    [('"Site: quoted"', False), ("plain words", False), ("Site: unquoted", True), ("*", True), ("a #b", True)],
)
def test_unsafe_value(value, bad):
    assert (audit.unsafe_value(value) is not None) is bad


def test_frontmatter_and_names(tmp_path):
    write(tmp_path, ".claude/skills/good/SKILL.md", '---\nname: good\ndescription: "A: b"\n---\n')
    write(tmp_path, ".claude/skills/bad/SKILL.md", "---\nname: Bad Label\ndescription: Site: x\n---\n")
    write(tmp_path, ".opencode/agents/a.md", "---\npermissions:\n  - action: edit\n    resource: *\n---\n")
    problems = list(audit.check_frontmatter(tmp_path))
    assert problems == [
        ".claude/skills/bad/SKILL.md:3: value contains ': '; quote it",
        ".opencode/agents/a.md:4: value starts with '*'; quote it",
    ]
    assert [problem.split(":")[0] for problem in audit.check_skills(tmp_path)] == [".claude/skills/bad/SKILL.md"]


def test_references_resolve_relative_paths_from_the_skill(tmp_path):
    write(tmp_path, "notes.md", "x")
    write(tmp_path, ".claude/skills/a/SKILL.md", "---\nname: a\n---\nSee ../../../notes.md and notes.md.\n")
    write(tmp_path, ".claude/skills/b/SKILL.md", "---\nname: b\n---\nSee ../../notes.md\n")
    assert list(audit.check_references(tmp_path)) == [".claude/skills/b/SKILL.md: ../../notes.md does not exist"]


def test_links_and_anchors(tmp_path):
    write(tmp_path, "container/src/ai/a/index.php", '<div id="top"></div><a href="/ai/b/#x">b</a><a href="#top">t</a>')
    write(tmp_path, "container/src/ai/b/index.php", '<a href="/ai/a/#missing">a</a><a href="/linux/">l</a>')
    write(tmp_path, "container/src/linux/index.php", "")
    assert list(audit.check_links(tmp_path)) == [
        '/ai/a/: href="/ai/b/#x" has no such anchor',
        '/ai/b/: href="/ai/a/#missing" has no such anchor',
    ]
