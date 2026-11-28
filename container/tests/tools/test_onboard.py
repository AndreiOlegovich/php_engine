# version 0.1.2
"""Tests for tools/onboard.py: the steps, found from the project, and the walk.

The project is a tmp_path tree; Docker and the commands are replaced, so each
step's check, question and command is tested on its own.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import onboard as ob  # noqa: E402  pylint: disable=wrong-import-position,import-error

EXAMPLE = 'SITE_URL="https://example.com"\nSITE_LOCAL_URL="http://127.0.0.1:8080"\nSITE_PAGE_MARKERS="Page_html_id"\nSITE_HOSTING=""\n'


@pytest.fixture(name="project")
def fixture_project(tmp_path: Path) -> ob.Project:
    """A project as new-project.sh leaves it: the stub page, the profile, git without a commit."""
    root = tmp_path / "shop"
    (root / "container" / "src" / ".php").mkdir(parents=True)
    (root / "container" / "src" / "index.php").write_text(f"<?php\n\necho '<p>{ob.STUB_TEXT}.</p>';\n", encoding="utf-8")
    (root / "container" / ".env").write_text('SITE_PROJECT="shop"\n', encoding="utf-8")
    (root / "container" / "site-profile.env.example").write_text(EXAMPLE, encoding="utf-8")
    (root / "container" / "site-profile.env").write_text(EXAMPLE.replace("8080", "8081") + 'SITE_NAME="Shop"\n', encoding="utf-8")
    (root / "README.md").write_text("## 7. Before\n\n## 8. Fix, in this order\n\nthe fixers\n\n## 9. First commit\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    return ob.Project(root)


@pytest.fixture(name="docker")
def fixture_docker(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Docker and the site container, switched by the test."""
    state = {"docker": True, "site": True}
    monkeypatch.setattr(ob, "docker_runs", lambda: state["docker"])
    monkeypatch.setattr(ob, "site_runs", lambda slug: state["site"])
    return state


@pytest.fixture(name="ran")
def fixture_ran(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    """Runner.run records the commands and succeeds."""
    calls: list[list[str]] = []

    def run(_self: object, command: list[str], **_: object) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(ob.Runner, "run", run)
    yield calls


def answers(monkeypatch: pytest.MonkeyPatch, *replies: str) -> None:
    queue = list(replies)

    def fake_input(prompt: str = "") -> str:
        print(prompt, end="")
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_steps_follow_the_project(project: ob.Project, docker: dict[str, bool]) -> None:
    docker["docker"] = False
    assert ob.current(project).step == "docker"
    docker.update(docker=True, site=False)
    assert ob.current(project).step == "start"
    docker["site"] = True
    assert ob.current(project).step == "copy"
    put(project.src / "index.php", "<?php\n\necho 'old home';\n")
    assert ob.current(project).step == "discover"
    project.set_profile_line('SITE_HOSTING="beget"')
    assert ob.current(project).step == "discover"  # the hosting is a step of its own, not a filled profile
    project.set_profile_line('SITE_PAGE_MARKERS="bottomid"')
    project.set_profile_line('SITE_HOSTING=""')
    assert ob.current(project).step == "hosting"
    project.set_profile_line('SITE_HOSTING="beget"')
    state = ob.current(project)
    assert state.step == "gate" and "the hosting is beget" in state.done
    project.git("add", "container/src/index.php")
    project.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "first")
    assert ob.current(project).step == "sitemap"
    put(project.src / "sitemap.xml", "<urlset/>\n")
    assert ob.current(project).step == "tests"


def test_a_profile_line_replaces_its_key_and_keeps_the_mode(project: ob.Project) -> None:
    os.chmod(project.profile_path, 0o600)
    project.set_profile_line('SITE_HOSTING="beget"')
    project.set_profile_line("SITE_TAG_MARKERS='menu=<nav class=\"menu\"'")
    text = project.profile_path.read_text(encoding="utf-8")
    assert text.count("SITE_HOSTING=") == 1 and 'SITE_HOSTING="beget"' in text
    assert text.rstrip().endswith("SITE_TAG_MARKERS='menu=<nav class=\"menu\"'")
    assert stat.S_IMODE(project.profile_path.stat().st_mode) == 0o600


def test_only_scripts_may_stay_executable(project: ob.Project) -> None:
    for name in ("page.php", "img/a.JPG", "tools/run.sh"):
        put(project.src / name, "x\n")
        os.chmod(project.src / name, 0o755)
    assert sorted(p.name for p in ob.executable_files(project)) == ["a.JPG", "page.php"]


def test_the_old_site_folder_is_checked(project: ob.Project, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not a folder"):
        ob.old_site_dir(project, str(tmp_path / "nowhere"))
    with pytest.raises(ValueError, match="own container/src"):
        ob.old_site_dir(project, str(project.src))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="public_html"):
        ob.old_site_dir(project, str(empty))
    put(tmp_path / "old" / "index.html", "<html></html>\n")
    assert ob.old_site_dir(project, str(tmp_path / "old")) == (tmp_path / "old").resolve()


def test_the_suggested_lines_are_read_from_the_report(tmp_path: Path) -> None:
    report = tmp_path / "report.txt"
    report.write_text('== Files\n  SITE_X=not this\n\n== Suggested lines for container/site-profile.env (check each, then copy):\n  SITE_LANG_DIRS="en fi"\n  SITE_TAG_MARKERS=\'top=class="top"\'\n', encoding="utf-8")
    assert ob.suggested_lines(report) == ['SITE_LANG_DIRS="en fi"', "SITE_TAG_MARKERS='top=class=\"top\"'"]
    assert ob.suggested_lines(tmp_path / "missing.txt") == []


def test_next_from_elsewhere_starts_each_command_with_cd(project: ob.Project, docker: dict[str, bool], capsys: pytest.CaptureFixture[str]) -> None:
    docker["site"] = False
    ob.show_next(project, ob.current(project))
    out = capsys.readouterr().out
    assert f"cd {project.root} && (cd container && docker compose up -d --build php-apache)" in out
    assert f"recommended:    cd {project.root} && container/tools/auto_onboard.sh" in out


def test_the_walk_writes_the_suggested_lines_on_yes(project: ob.Project, docker: dict[str, bool], monkeypatch: pytest.MonkeyPatch) -> None:
    put(project.src / "index.php", "<?php\n\necho 'old home';\n")
    monkeypatch.setattr(ob, "step_discover", lambda run: (0, ['SITE_LANG_DIRS="en fi"', 'SITE_PAGE_MARKERS="bottomid"']))
    answers(monkeypatch, "y")
    assert ob.Walk(project).step("discover")
    profile = project.profile()
    assert profile["SITE_LANG_DIRS"] == "en fi" and profile["SITE_PAGE_MARKERS"] == "bottomid"
    assert ob.current(project).step == "hosting"


def test_the_walk_asks_for_the_hosting_by_number(project: ob.Project, ran: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    answers(monkeypatch, "9", "1")
    walk = ob.Walk(project)
    assert walk.step("hosting")  # 9 is no choice: asked again in the next round
    assert not project.profile().get("SITE_HOSTING")
    assert walk.step("hosting")
    assert project.profile()["SITE_HOSTING"] == "beget"
    assert ["python3", "container/tools/htaccess_hosting.py"] in ran


def test_before_the_gate_modes_sitemaps_and_fixers_are_offered(project: ob.Project, ran: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    put(project.src / "page.php", "<?php\n")
    os.chmod(project.src / "page.php", 0o700)
    put(project.src / "sitemap.xml", "<urlset/>\n")
    # print section 8, run the mode fix, move the sitemaps, run the fixers: line endings yes, the rest skipped
    answers(monkeypatch, "y", "y", "y", "y", "y", "s", "s", "s", "s", "s")
    monkeypatch.setattr(ob.Project, "git", lambda self, *args: subprocess.CompletedProcess(args, 1 if args[:2] == ("diff", "--cached") else 0, "", ""))
    ob.Walk(project).before_gate()
    out = capsys.readouterr().out
    assert "## 8. Fix, in this order" in out and "## 9." not in out
    assert ob.MODE_FIX[0] in ran and ob.MODE_FIX[2] in ran
    assert any(command[0] == "mv" and command[-1] == "backup/old-site/" for command in ran)
    assert ob.FIXERS[0].command in ran and ob.FIXERS[1].command not in ran


def test_the_first_commit_is_tried_once_more_after_the_image_list(project: ob.Project, monkeypatch: pytest.MonkeyPatch) -> None:
    codes = iter([0, 1, 0, 0])  # add, commit stopped by the image-list hook, add again, commit
    calls: list[list[str]] = []

    def run(_self: object, command: list[str], **_: object) -> int:
        calls.append(command)
        return next(codes)

    monkeypatch.setattr(ob.Runner, "run", run)
    assert ob.step_commit(ob.Runner(project)) == 0
    assert [c[:2] for c in calls] == [["git", "add"], ["git", "commit"], ["git", "add"], ["git", "commit"]]
    assert calls[1] == ["git", "commit", "-m", "Start Shop"]


def test_the_walk_stops_at_the_end_of_input(project: ob.Project, docker: dict[str, bool], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    answers(monkeypatch)
    assert ob.Walk(project).walk() == 0
    assert "No answer: stopping here." in capsys.readouterr().out


def test_undo_is_asked_twice_and_no_asks_again(project: ob.Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    answers(monkeypatch, "u", "n", "u", "y")
    assert ob.Walk(project).accept_or_undo(5) == "u"
    assert capsys.readouterr().out.count("Undo the changes in 5 files?") == 2


def test_accept_needs_no_second_answer(project: ob.Project, monkeypatch: pytest.MonkeyPatch) -> None:
    answers(monkeypatch, "y")
    assert ob.Walk(project).accept_or_undo(5) == "y"


def test_each_question_drops_what_waited_in_the_terminal(project: ob.Project, monkeypatch: pytest.MonkeyPatch) -> None:
    dropped: list[bool] = []
    monkeypatch.setattr(ob, "drop_typeahead", lambda: dropped.append(True))
    answers(monkeypatch, "y")
    ob.Walk(project).ask("? ")
    assert dropped == [True]


def test_enter_alone_or_a_word_is_no_answer(project: ob.Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    answers(monkeypatch, "", "yes", "u")
    assert ob.Walk(project).choose("? ", "yu") == "u"
    assert capsys.readouterr().out.count("Answer one of") == 2
