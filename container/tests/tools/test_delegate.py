# version 0.1.2
"""Tests for tools/ai/delegate.py (offline: a temporary repo and a fake launcher)."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

TOOLS_AI = Path(__file__).resolve().parent.parent.parent / "tools" / "ai"

sys.path.insert(0, str(TOOLS_AI))

import delegate  # pylint: disable=wrong-import-position,import-error  # noqa: E402

SESSION = "ses_e59475dbbffeJi8yGvk91IHmcj"
# Real lines of `opencode run --format json` (OpenCode 2.1.4), shortened ids.
TEXT = '{"type":"text","timestamp":1,"sessionID":"%s","part":{"type":"text","text":"%s"}}'
FINISH = '{"type":"step_finish","timestamp":2,"sessionID":"%s","part":{"type":"step-finish","reason":"stop","cost":0.002,"tokens":{"input":81,"output":3,"reasoning":0,"cache":{"read":7040,"write":0}}}}'
ERROR = '{"type":"error","timestamp":3,"sessionID":"%s","error":{"type":"provider.no-route","message":"Model unavailable: opencode/no-such-model-free"}}'


def git(path: Path, *args: str) -> str:
    command = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


def fake_launcher(path: Path, lines: list[str], code: int = 0) -> Path:
    """A launcher that records its arguments and prints the given events."""
    events = path.with_suffix(".events")
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{path}.args"\necho "OpenCode in Docker: one task" >&2\ncat "{events}"\nexit {code}\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


@pytest.fixture
def env(tmp_path: Path) -> delegate.Env:
    repo = tmp_path / "project"
    (repo / "container" / "src").mkdir(parents=True)
    (repo / "container" / "src" / "index.php").write_text("<?php\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    config = tmp_path / "opencode.jsonc"
    config.write_text('{\n  // "model": "commented/out",\n  "model": "opencode/deepseek-v4.1-flash-free",\n}\n', encoding="utf-8")
    launcher = fake_launcher(tmp_path / "launcher.sh", [TEXT % (SESSION, "OK"), FINISH % SESSION])
    return delegate.Env(
        repo=repo,
        launcher=launcher,
        config=config,
        state_root=tmp_path / "state",
        fetch=lambda: {"deepseek-v4.1-flash-free", "glm-5.3-flash-free"},
        free_list=["deepseek-v4.1-flash-free", "glm-5.3-flash-free"],
    )


def launcher_args(env: delegate.Env) -> list[str]:
    return Path(f"{env.launcher}.args").read_text(encoding="utf-8").splitlines()


def test_summarize_reads_session_answer_cost_and_errors() -> None:
    lines = [TEXT % (SESSION, "first"), "not json", TEXT % (SESSION, "last"), FINISH % SESSION, ERROR % SESSION, ""]
    summary = delegate.summarize(lines)
    assert summary.session == SESSION
    assert summary.answer == "last"
    assert summary.finish == "stop"
    assert summary.cost == pytest.approx(0.002)
    assert (summary.tokens_in, summary.tokens_out) == (81, 3)
    assert summary.errors == ["provider.no-route: Model unavailable: opencode/no-such-model-free"]
    assert summary.bad_lines == 1
    assert summary.events == {"text": 2, "step_finish": 1, "error": 1}
    # A last step without its step_finish leaves no stale reason behind.
    unfinished = delegate.summarize([FINISH % SESSION, '{"type":"step_start","sessionID":"s"}', TEXT % (SESSION, "Done.")])
    assert unfinished.finish is None
    assert unfinished.answer == "Done."


def test_config_model_skips_comments(env: delegate.Env) -> None:
    assert delegate.config_model(env.config) == "opencode/deepseek-v4.1-flash-free"
    assert delegate.config_model(env.config.with_name("missing.jsonc")) == "the default of OpenCode"


def test_check_model(env: delegate.Env) -> None:
    assert delegate.check_model("opencode/glm-5.3-flash-free#high", env)[0]
    live, note = delegate.check_model("opencode/gone-free", env)
    assert not live
    assert "opencode/deepseek-v4.1-flash-free" in note
    assert delegate.check_model("openrouter/some/model", env) == (True, "not checked: only opencode/<id> models have a public catalog")

    def down() -> set[str]:
        raise OSError("offline")

    env.fetch = down
    assert delegate.check_model("opencode/gone-free", env)[0]


def test_start_creates_worktree_and_records_the_session(env: delegate.Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert delegate.main(["start", "fix-typo", "--message", "Reply with exactly: OK"], env) == 0
    worktree = env.repo.parent / "site-agent-fix-typo"
    assert git(worktree, "branch", "--show-current") == "agent/fix-typo"
    task = json.loads((env.state_root / "fix-typo" / "state.json").read_text(encoding="utf-8"))
    assert task["session"] == SESSION
    assert task["rounds"][0]["status"] == "done"
    assert "pid" not in task["rounds"][0]
    args = launcher_args(env)
    assert args[:5] == [str(worktree), "run", "--standalone", "--format", "json"]
    assert args[5:7] == ["--title", "fix-typo"]
    assert args[-1] == "Reply with exactly: OK"
    assert "-m" not in args
    out = capsys.readouterr().out
    assert f"session:   {SESSION}" in out
    assert "  OK" in out
    assert (env.state_root / "fix-typo" / "round-1.prompt.md").read_text(encoding="utf-8") == "Reply with exactly: OK\n"


def test_fix_continues_the_session_with_the_same_flags(env: delegate.Env, tmp_path: Path) -> None:
    key = tmp_path / "opencode.key"
    key.write_text("k\n", encoding="utf-8")
    start = ["start", "pages", "--message", "Do it", "--ruby", "--rw", "container/tools", "-m", "opencode/glm-5.3-flash-free", "--opencode-key", str(key)]
    assert delegate.main(start, env) == 0
    brief = tmp_path / "fix.md"
    brief.write_text("- Bump the version.\n", encoding="utf-8")
    assert delegate.main(["fix", "pages", "--brief", str(brief)], env) == 0
    args = launcher_args(env)
    assert args[:6] == ["--rw", "container/tools", "--opencode-key", str(key.resolve()), "--ruby", str(env.repo.parent / "site-agent-pages")]
    assert args[10:14] == ["--session", SESSION, "-m", "opencode/glm-5.3-flash-free"]
    assert args[-2:] == ["Task:", "- Bump the version."]  # one argument, printed one line per line
    task = delegate.load_task(env, "pages")
    assert [r["kind"] for r in task["rounds"]] == ["start", "fix"]


def test_fix_switches_model_and_key_for_later_rounds(env: delegate.Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert delegate.main(["start", "big", "--message", "x"], env) == 0
    key = tmp_path / "opencode.key"
    key.write_text("dummy\n", encoding="utf-8")
    env.fetch = set  # an empty catalog: opencode-go/ ids are not checked against it
    switch = ["fix", "big", "--message", "y", "-m", "opencode-go/deepseek-v4.1-flash", "--opencode-key", str(key)]
    assert delegate.main(switch, env) == 0
    args = launcher_args(env)
    assert args[:2] == ["--opencode-key", str(key.resolve())]
    assert args[-4:-1] == [SESSION, "-m", "opencode-go/deepseek-v4.1-flash"]
    assert delegate.main(["fix", "big", "--message", "z"], env) == 0  # kept for the next round
    assert launcher_args(env)[:2] == ["--opencode-key", str(key.resolve())]
    task = delegate.load_task(env, "big")
    assert [r["model"] for r in task["rounds"]] == ["opencode/deepseek-v4.1-flash-free", "opencode-go/deepseek-v4.1-flash", "opencode-go/deepseek-v4.1-flash"]
    assert "dummy" not in (env.state_root / "big" / "state.json").read_text(encoding="utf-8")
    assert "model:     opencode-go/deepseek-v4.1-flash" in capsys.readouterr().out
    assert delegate.main(["fix", "big", "--message", "w", "-m", "opencode/gone-free"], env) == 3


def test_error_event_fails_the_round(env: delegate.Env, capsys: pytest.CaptureFixture[str]) -> None:
    fake_launcher(env.launcher, [ERROR % SESSION], code=1)
    assert delegate.main(["start", "bad", "--message", "x"], env) == 1
    task = delegate.load_task(env, "bad")
    assert task["rounds"][0]["status"] == "failed"
    assert task["rounds"][0]["exit"] == 1
    assert "error:     provider.no-route" in capsys.readouterr().out


def test_missing_model_stops_before_the_worktree(env: delegate.Env) -> None:
    assert delegate.main(["start", "gone", "--message", "x", "-m", "opencode/gone-free"], env) == 3
    assert not (env.repo.parent / "site-agent-gone").exists()
    assert not (env.state_root / "gone").exists()


def test_existing_worktree_and_refusals(env: delegate.Env) -> None:
    other = env.repo.parent / "site-agent-old"
    git(env.repo, "worktree", "add", "-q", "-b", "agent/old", str(other))
    assert delegate.main(["start", "old", "--message", "x"], env) == 2  # the path exists
    assert delegate.main(["start", "old", "--message", "x", "--worktree", str(other)], env) == 0
    assert delegate.load_task(env, "old")["branch"] == "agent/old"
    assert delegate.main(["start", "old", "--message", "x", "--worktree", str(other)], env) == 2  # task exists
    assert delegate.main(["start", "Bad_Slug", "--message", "x"], env) == 2
    assert delegate.main(["fix", "nothing", "--message", "x"], env) == 2
    assert delegate.main(["start", "empty", "--message", "  "], env) == 2


def test_fix_refuses_a_worktree_with_ignored_files(env: delegate.Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert delegate.main(["start", "fix-typo", "--message", "x"], env) == 0
    container = env.repo.parent / "site-agent-fix-typo" / "container"
    (container / "__pycache__").mkdir()
    (container / "__pycache__" / "a.pyc").write_bytes(b"")
    assert delegate.main(["fix", "fix-typo", "--message", "y"], env) == 0  # caches are allowed
    (container / ".venv").mkdir()
    (container / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert delegate.main(["fix", "fix-typo", "--message", "z"], env) == 2
    assert "container/.venv/" in capsys.readouterr().err
    assert len(delegate.load_task(env, "fix-typo")["rounds"]) == 2


def test_status_marks_a_dead_round_interrupted(env: delegate.Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert delegate.main(["start", "fix-typo", "--message", "x"], env) == 0
    task = delegate.load_task(env, "fix-typo")
    task["rounds"].append({"round": 2, "kind": "fix", "started": "t", "status": "running", "pid": 2**22 + 12345})
    delegate.save_task(env, task)
    assert delegate.main(["status"], env) == 0
    assert "interrupted" in capsys.readouterr().out
    assert delegate.load_task(env, "fix-typo")["rounds"][-1]["status"] == "interrupted"
    assert delegate.main(["fix", "fix-typo", "--message", "again"], env) == 0
    assert delegate.main(["status", "fix-typo"], env) == 0
    assert "round 3 (fix)" in capsys.readouterr().out


class FakeProcess:
    """What detach() reads of a Popen: the pid and poll()."""

    def __init__(self, pid: int, code: int | None) -> None:
        self.pid = pid
        self.code = code

    def poll(self) -> int | None:
        return self.code


def add_running_round(env: delegate.Env, slug: str, pid: int) -> dict:
    task = delegate.load_task(env, slug)
    task["rounds"].append({"round": len(task["rounds"]) + 1, "kind": "fix", "started": "t", "status": "running", "pid": pid})
    delegate.save_task(env, task)
    return task


def test_wait_reports_a_finished_round(env: delegate.Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert delegate.main(["start", "w", "--message", "x"], env) == 0
    capsys.readouterr()
    assert delegate.main(["wait", "w"], env) == 0
    assert "status:    done, exit 0" in capsys.readouterr().out
    assert delegate.main(["wait", "nothing"], env) == 2


def test_wait_times_out_on_a_live_round_and_settles_a_dead_one(env: delegate.Env, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(delegate, "WAIT_POLL", 0.01)
    assert delegate.main(["start", "w", "--message", "x"], env) == 0
    task = add_running_round(env, "w", os.getpid())
    capsys.readouterr()
    assert delegate.main(["wait", "w", "--timeout", "0.05"], env) == delegate.STILL_RUNNING
    assert "round 2 of w is still running" in capsys.readouterr().out
    task["rounds"][-1]["pid"] = 2**22 + 12345  # no such process
    delegate.save_task(env, task)
    assert delegate.main(["wait", "w"], env) == 1
    assert delegate.load_task(env, "w")["rounds"][-1]["status"] == "interrupted"


def test_wait_returns_when_the_round_ends(env: delegate.Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(delegate, "WAIT_POLL", 0.01)
    assert delegate.main(["start", "w", "--message", "x"], env) == 0
    task = add_running_round(env, "w", os.getpid())

    def finish() -> None:
        time.sleep(0.1)
        task["rounds"][-1].pop("pid")
        task["rounds"][-1].update(status="done", ended="t2", exit=0)
        delegate.save_task(env, task)

    thread = threading.Thread(target=finish)
    thread.start()
    assert delegate.main(["wait", "w", "--timeout", "5"], env) == 0
    thread.join()


def test_detach_runs_the_round_through_spawn_and_returns(env: delegate.Env, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[list[str]] = []

    def spawn(argv: list[str], log: Path) -> FakeProcess:
        seen.append(argv)
        assert log == env.state_root / argv[1] / "detach.log"
        assert delegate.main(argv, env) == 0  # what the background process does
        return FakeProcess(4242, None)

    env.spawn = spawn
    assert delegate.main(["start", "bg", "--detach", "--message", "x"], env) == 0
    assert seen == [["start", "bg", "--message", "x"]]
    out = capsys.readouterr().out
    assert "round 1 of bg runs in the background (pid 4242)" in out
    assert "delegate.py wait bg" in out
    assert delegate.main(["fix", "bg", "--message", "y", "--detach"], env) == 0
    assert seen[-1] == ["fix", "bg", "--message", "y"]
    assert "round 2 of bg runs in the background" in capsys.readouterr().out


def test_detach_refuses_early_and_reports_a_failed_start(env: delegate.Env, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[list[str]] = []

    def spawn(argv: list[str], log: Path) -> FakeProcess:
        calls.append(argv)
        log.write_text("model:     opencode/gone-free: not in the OpenCode catalog\n", encoding="utf-8")
        return FakeProcess(1, 3)

    env.spawn = spawn
    assert delegate.main(["start", "Bad_Slug", "--detach", "--message", "x"], env) == 2
    assert delegate.main(["fix", "nothing", "--detach", "--message", "x"], env) == 2
    assert not calls  # refused before anything runs in the background
    assert delegate.main(["start", "early", "--detach", "--message", "x"], env) == 3
    assert "not in the OpenCode catalog" in capsys.readouterr().err
    assert delegate.main(["start", "run", "--message", "x"], env) == 0
    add_running_round(env, "run", os.getpid())
    assert delegate.main(["fix", "run", "--detach", "--message", "y"], env) == 2
    assert len(calls) == 1


def test_spawn_detached_runs_this_script_with_the_log(tmp_path: Path) -> None:
    log = tmp_path / "detach.log"
    process = delegate.spawn_detached(["--help"], log)
    assert process.wait(timeout=30) == 0
    assert "usage: delegate.py" in log.read_text(encoding="utf-8")


def test_detach_never_runs_twice(env: delegate.Env, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    def spawn(argv: list[str], log: Path) -> FakeProcess:
        raise AssertionError(f"spawned {argv} into {log}")

    env.spawn = spawn
    with pytest.raises(SystemExit) as exit_info:
        delegate.main(["start", "short", "--det", "--message", "x"], env)
    assert exit_info.value.code == 2  # argparse refuses the shortened option
    assert "--det" in capsys.readouterr().err
    monkeypatch.setenv(delegate.DETACHED_ENV, "1")  # what spawn_detached gives the background run
    assert delegate.main(["start", "inner", "--detach", "--message", "x"], env) == 0
    assert delegate.load_task(env, "inner")["rounds"][-1]["status"] == "done"
