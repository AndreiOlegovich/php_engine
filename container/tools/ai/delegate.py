#!/usr/bin/env python3
# version 0.5.1
"""Give a task to OpenCode in Docker and follow it: the supervisor side.

Claude Code (skill assign-to-opencode) or OpenCode (skill
delegate-to-container) on the host supervises; OpenCode in a container
started by opencode-docker.sh is the worker. This tool gives the worker a task in its
own worktree, records the OpenCode session, and sends each follow-up fix into
that same session, so the worker keeps its context between rounds.

  start SLUG --brief FILE   worktree ../PROJECT-agent-SLUG on a new branch
                            agent/SLUG (or --worktree PATH), model check,
                            round 1 in a new session titled SLUG
  fix SLUG --brief FILE     the next round in the same session; -m and the
                            key options switch the model or the key for
                            this round and the ones after it
  status [SLUG]             every task, or one: its last round, the worker's
                            last answer, what changed in the worktree
  wait SLUG [--timeout S]   block until the running round ends (default
                            540 s), then print its report

--detach on start and fix runs the round in a new session in the background
and returns once the round is recorded as running: for a supervisor whose
shell tool has no background mode and stops a command after a few minutes
(OpenCode). It then calls wait until the round ends. The round's output goes
to container/reports/delegate/SLUG/detach.log. Options are never shortened
(--det is an error), and the background run gets DELEGATE_DETACHED=1, so it
runs the round instead of detaching again.

Every run uses `opencode run --standalone --format json`: --standalone gives
each container its own server (two containers on the shared session volume
otherwise fail with "Background service failed to start"), and the JSON
events carry the session id and the errors.

State, per task, in this checkout (git-ignored): container/reports/delegate/
SLUG/state.json and, per round, round-N.prompt.md (the task text),
round-N.jsonl (OpenCode's events) and round-N.err (the launcher's stderr).

Usage (any working directory):
  python3 container/tools/ai/delegate.py start fix-typo --brief /tmp/brief.md
  python3 container/tools/ai/delegate.py start ruby-batch --ruby --brief b.md
  python3 container/tools/ai/delegate.py start more-pages --worktree ../PROJECT-agent-more-pages --brief b.md
  python3 container/tools/ai/delegate.py fix fix-typo --message "Bump the version header too."
  python3 container/tools/ai/delegate.py fix big-task -m opencode-go/deepseek-v4.1-flash --opencode-key ~/.config/opencode-agent/opencode.key --brief fix-1.md
  python3 container/tools/ai/delegate.py status
  python3 container/tools/ai/delegate.py status fix-typo
  python3 container/tools/ai/delegate.py start fix-typo --detach --brief /tmp/opencode/brief.md
  python3 container/tools/ai/delegate.py wait fix-typo --timeout 540

Exit: 0 the round ended without an error (or, with --detach, started), 1 the
worker failed or reported an error, 2 bad arguments or state, 3 the model is
not in the OpenCode catalog, 4 wait timed out with the round still running.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1]))

import check_opencode_models as models  # pylint: disable=wrong-import-position,import-error  # noqa: E402
from project_name import project_name  # pylint: disable=wrong-import-position,import-error  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_AI = REPO_ROOT / "container" / "tools" / "ai"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
MODEL_RE = re.compile(r'^\s*"model"\s*:\s*"([^"]+)"', re.MULTILINE)
MAX_MESSAGE = 100_000
ANSWER_LIMIT = 4000
STATUS_LIMIT = 40
# Flags of opencode-docker.sh that pick an image with more languages: what
# the image holds and the flags that pick it.
IMAGE_GROUPS = (
    ("the image with the compiled languages (assembler, C, C++, Fortran, COBOL, Go, Rust, Haskell)", ("native", "assembler", "c", "cpp", "fortran", "cobol", "go", "rust", "haskell")),
    ("the image with the VM languages (Java, Groovy, .NET, Erlang, Elixir, Thrift)", ("vm", "java", "groovy", "dotnet", "elixir", "thrift")),
    ("the image with the interpreted languages (JavaScript, TypeScript, Ruby)", ("script", "javascript", "js", "typescript")),
    ("the image with Ruby alone", ("ruby",)),
    ("the image with Erlang/OTP alone", ("erlang",)),
)
IMAGES = tuple(flag for _, flags in IMAGE_GROUPS for flag in flags)
WAIT_POLL = 5.0  # seconds between two looks at a running round
DETACH_WAIT = 120.0  # seconds --detach waits for the background round to be recorded
STILL_RUNNING = 4
DETACHED_ENV = "DELEGATE_DETACHED"  # set in the background run: never detach twice
# The paths opencode-docker.sh mounts read-only; keep in step with its `ro=`.
LAUNCHER_READ_ONLY = ("AGENTS.md", ".claude/skills", "skill.md", "_internal_ai_skill_for_files.md")


class TaskError(RuntimeError):
    """Bad arguments or state the user can fix; exit 2."""


def spawn_detached(argv: list[str], log: Path) -> subprocess.Popen[bytes]:
    """This script with argv, in a new session, output appended to log."""
    with log.open("ab") as out:
        return subprocess.Popen(  # pylint: disable=consider-using-with  # it must outlive this process
            [sys.executable, str(Path(__file__).resolve()), *argv],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, DETACHED_ENV: "1"},
        )


@dataclass
class Env:
    """Where the tool reads and writes; tests point it at a temporary repo."""

    repo: Path = REPO_ROOT
    launcher: Path = TOOLS_AI / "opencode-docker.sh"
    config: Path = TOOLS_AI / "docker" / "opencode.jsonc"
    state_root: Path = REPO_ROOT / "container" / "reports" / "delegate"
    fetch: Callable[[], set[str]] = field(default=models.fetch_live)
    free_list: list[str] = field(default_factory=lambda: models.read_free_list(models.FREE_LIST))
    spawn: Callable[[list[str], Path], subprocess.Popen[bytes]] = field(default=spawn_detached)


@dataclass
class Summary:  # pylint: disable=too-many-instance-attributes  # one field per reported value
    """What one round's JSON event stream says."""

    session: str | None = None
    errors: list[str] = field(default_factory=list)
    answer: str = ""
    events: Counter[str] = field(default_factory=Counter)
    finish: str | None = None
    cost: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    bad_lines: int = 0


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def summarize(lines: Iterable[str]) -> Summary:
    """Session id, errors, last answer and totals from `opencode run --format json`."""
    summary = Summary()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            summary.bad_lines += 1
            continue
        if not isinstance(event, dict):
            summary.bad_lines += 1
            continue
        kind = str(event.get("type", "unknown"))
        summary.events[kind] += 1
        summary.session = summary.session or event.get("sessionID")
        part = _dict(event.get("part"))
        if kind == "error":
            error = _dict(event.get("error"))
            text = str(error.get("type", "error"))
            if error.get("message"):
                text += f": {error['message']}"
            summary.errors.append(text)
        elif kind == "text" and part.get("text"):
            summary.answer = str(part["text"])
        elif kind == "step_start":
            # The last step may end without a step_finish (OpenCode 2.1.4).
            summary.finish = None
        elif kind == "step_finish":
            summary.finish = part.get("reason")
            summary.cost += float(part.get("cost") or 0)
            tokens = _dict(part.get("tokens"))
            summary.tokens_in += int(tokens.get("input") or 0)
            summary.tokens_out += int(tokens.get("output") or 0)
    return summary


def config_model(config: Path) -> str:
    """The model the container's opencode.jsonc pins."""
    match = MODEL_RE.search(config.read_text(encoding="utf-8")) if config.is_file() else None
    return match.group(1) if match else "the default of OpenCode"


def check_model(model: str, env: Env) -> tuple[bool, str]:
    """False when an opencode/<id> model is missing from the catalog."""
    provider, _, name = model.partition("/")
    name = name.split("#", 1)[0]
    if provider != "opencode" or not name:
        return True, "not checked: only opencode/<id> models have a public catalog"
    try:
        live = env.fetch()
    except (OSError, ValueError, KeyError) as error:
        return True, f"catalog unreachable ({error}); starting anyway"
    if name in live:
        return True, "live in the OpenCode catalog"
    following = next((m for m in env.free_list if m in live), None)
    hint = f"next live free id: opencode/{following}" if following else "no id of free-models.txt is live"
    return False, f"not in the OpenCode catalog; {hint}"


def git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise TaskError(f"git {' '.join(args)} in {path}: {result.stderr.strip()}")
    return result.stdout.strip()


def state_dir(env: Env, slug: str) -> Path:
    return env.state_root / slug


def load_task(env: Env, slug: str) -> dict[str, Any]:
    path = state_dir(env, slug) / "state.json"
    if not path.is_file():
        raise TaskError(f"no task {slug}: {path} not found; start it first")
    task: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return task


def save_task(env: Env, task: dict[str, Any]) -> None:
    """Rename a complete temp file over state.json (container/docs/atomic-file-writes.md)."""
    folder = state_dir(env, task["slug"])
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".state.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(task, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, folder / "state.json")
    except BaseException:
        os.unlink(tmp)
        raise


def all_tasks(env: Env) -> list[dict[str, Any]]:
    if not env.state_root.is_dir():
        return []
    return [load_task(env, path.parent.name) for path in sorted(env.state_root.glob("*/state.json"))]


def pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def settle(task: dict[str, Any]) -> bool:
    """Mark a round left running by a process that is gone as interrupted."""
    last = task["rounds"][-1] if task["rounds"] else None
    if last and last["status"] == "running" and not pid_alive(last.get("pid")):
        last["status"] = "interrupted"
        last.pop("pid", None)
        return True
    return False


def read_message(args: argparse.Namespace) -> str:
    text = args.brief.read_text(encoding="utf-8") if args.brief else args.message
    text = text.strip()
    if not text:
        raise TaskError("the task text is empty")
    if len(text.encode("utf-8")) > MAX_MESSAGE:
        raise TaskError(f"the task text is over {MAX_MESSAGE} bytes; shorten it or point the worker to files")
    # OpenCode would read a leading dash as an option.
    return f"Task:\n{text}" if text.startswith("-") else text


def launcher_command(env: Env, task: dict[str, Any], message: str) -> list[str]:
    command = [str(env.launcher)]
    for path in task["rw"]:
        command += ["--rw", path]
    for path in task.get("ro_ext", []):
        command += ["--ro-ext", path]
    if task.get("opencode_key"):
        command += ["--opencode-key", task["opencode_key"]]
    if task.get("openrouter_key"):
        command += ["--openrouter-key", task["openrouter_key"]]
    if task.get("image") in IMAGES:
        command.append(f"--{task['image']}")
    command += [task["worktree"], "run", "--standalone", "--format", "json"]
    command += ["--session", task["session"]] if task.get("session") else ["--title", task["slug"]]
    if task.get("model_arg"):
        command += ["-m", task["model"]]
    command.append(message)
    return command


def ignored_files(path: Path) -> list[str]:
    """Ignored files the launcher refuses to mount (it allows only __pycache__)."""
    paths = ("container", *LAUNCHER_READ_ONLY)
    result = subprocess.run(["git", "-C", str(path), "status", "--short", "--ignored", "--", *paths], capture_output=True, text=True, check=False)
    return [line[3:] for line in result.stdout.splitlines() if line.startswith("!! ") and not line.endswith("__pycache__/")]


def refuse_ignored(path: Path) -> None:
    found = ignored_files(path)
    if found:
        listing = "\n  ".join(found[:10])
        raise TaskError(f"{path} holds ignored files, which opencode-docker.sh refuses; remove what the host checks left (run tests with the main checkout's venv and -p no:cacheprovider):\n  {listing}")


def worktree_changes(path: str) -> list[str]:
    result = subprocess.run(["git", "-C", path, "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True, check=False)
    if result.returncode:
        return [f"(git status failed: {result.stderr.strip()})"]
    return result.stdout.splitlines()


def round_summary(env: Env, task: dict[str, Any], number: int) -> Summary:
    path = state_dir(env, task["slug"]) / f"round-{number}.jsonl"
    return summarize(path.read_text(encoding="utf-8", errors="replace").splitlines()) if path.is_file() else Summary()


def stderr_tail(path: Path, lines: int = 8) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    body = [line for line in text if line.strip() and not line.startswith("  ") and not line.startswith("OpenCode in Docker:")]
    return body[-lines:]


def clip(text: str, limit: int, where: str) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... ({len(text) - limit} more characters in {where})"


def print_report(env: Env, task: dict[str, Any]) -> None:
    last = task["rounds"][-1]
    number = last["round"]
    folder = state_dir(env, task["slug"])
    summary = round_summary(env, task, number)
    status = last["status"]
    if "exit" in last:
        status += f", exit {last['exit']}"
    if summary.finish:
        status += f", finish {summary.finish}"
    events = ", ".join(f"{kind} {count}" for kind, count in sorted(summary.events.items())) or "none"
    print(f"task:      {task['slug']}, round {number} ({last['kind']}), branch {task['branch']}")
    print(f"worktree:  {task['worktree']}")
    print(f"model:     {last.get('model', task['model'])}")
    print(f"session:   {task.get('session') or 'none yet'}")
    print(f"started:   {last['started']}" + (f", ended {last['ended']}" if last.get("ended") else ""))
    print(f"status:    {status}")
    print(f"events:    {events}" + (f"; {summary.bad_lines} lines not JSON" if summary.bad_lines else ""))
    print(f"cost:      ${summary.cost:.4f}, tokens {summary.tokens_in} in / {summary.tokens_out} out")
    for error in summary.errors:
        print(f"error:     {error}")
    tail = stderr_tail(folder / f"round-{number}.err")
    if tail and (last.get("exit") or not summary.events):
        print("stderr:")
        print("\n".join(f"  {line}" for line in tail))
    print("worker's last answer:" if summary.answer else "worker's last answer: none")
    if summary.answer:
        answer = clip(summary.answer, ANSWER_LIMIT, f"{folder}/round-{number}.jsonl")
        print("\n".join(f"  {line}" for line in answer.splitlines()))
    changes = worktree_changes(task["worktree"]) if Path(task["worktree"]).is_dir() else ["(the worktree is gone)"]
    print(f"worktree changes: {len(changes)} paths" if changes else "worktree changes: none")
    for line in changes[:STATUS_LIMIT]:
        print(f"  {line}")
    if len(changes) > STATUS_LIMIT:
        print(f"  ... {len(changes) - STATUS_LIMIT} more: git -C {task['worktree']} status --short")
    print(f"files:     {folder}/round-{number}.{{prompt.md,jsonl,err}}")


def run_round(env: Env, task: dict[str, Any], message: str) -> int:
    folder = state_dir(env, task["slug"])
    folder.mkdir(parents=True, exist_ok=True)
    number = len(task["rounds"]) + 1
    (folder / f"round-{number}.prompt.md").write_text(message + "\n", encoding="utf-8")
    command = launcher_command(env, task, message)
    record: dict[str, Any] = {
        "round": number,
        "kind": "start" if number == 1 else "fix",
        "started": now(),
        "status": "running",
        "pid": os.getpid(),
        "model": task["model"],
        "command": command[:-1],
    }
    task["rounds"].append(record)
    save_task(env, task)
    print(f"round {number} of {task['slug']} started {record['started']}; events: {folder}/round-{number}.jsonl", flush=True)
    with (folder / f"round-{number}.jsonl").open("wb") as out, (folder / f"round-{number}.err").open("wb") as err:
        code = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, check=False).returncode
    summary = round_summary(env, task, number)
    task["session"] = task.get("session") or summary.session
    record.pop("pid", None)
    record.update(
        ended=now(),
        status="done" if code == 0 and not summary.errors else "failed",
        exit=code,
        session=summary.session,
        errors=summary.errors,
        finish=summary.finish,
        cost=round(summary.cost, 6),
        tokens_in=summary.tokens_in,
        tokens_out=summary.tokens_out,
    )
    save_task(env, task)
    print_report(env, task)
    return 0 if record["status"] == "done" else 1


def existing_worktree(env: Env, path: Path) -> str:
    """The branch of a worktree of this repo, or TaskError."""
    if not (path / "container" / "src").is_dir():
        raise TaskError(f"{path}/container/src not found")
    ours = git(env.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    theirs = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if Path(ours).resolve() != Path(theirs).resolve():
        raise TaskError(f"{path} is not a worktree of {env.repo}")
    return git(path, "branch", "--show-current") or "(detached)"


def command_start(env: Env, args: argparse.Namespace) -> int:
    slug = args.slug
    if not SLUG_RE.match(slug):
        raise TaskError(f"slug {slug!r}: use lower-case letters, digits and dashes, at most 48")
    if (state_dir(env, slug) / "state.json").exists():
        raise TaskError(f"task {slug} exists: send the next round with fix, or see status {slug}")
    message = read_message(args)
    model = args.model or config_model(env.config)
    live, note = check_model(model, env)
    print(f"model:     {model}: {note}", flush=True)
    if not live:
        return 3
    if args.worktree:
        worktree = args.worktree.resolve()
        branch = existing_worktree(env, worktree)
        refuse_ignored(worktree)
        for other in all_tasks(env):
            settle(other)
            if Path(other["worktree"]) == worktree and other["rounds"] and other["rounds"][-1]["status"] == "running":
                raise TaskError(f"task {other['slug']} is running in {worktree}")
    else:
        worktree = env.repo.parent / f"{project_name(env.repo)}-agent-{slug}"
        branch = f"agent/{slug}"
        if worktree.exists():
            raise TaskError(f"{worktree} exists: pass --worktree {worktree} to give the task to it")
        git(env.repo, "worktree", "add", "-q", "-b", branch, str(worktree), args.base)
        print(f"worktree:  created {worktree} on {branch} from {args.base}", flush=True)
    task: dict[str, Any] = {
        "slug": slug,
        "worktree": str(worktree),
        "branch": branch,
        "base": None if args.worktree else args.base,
        "created": now(),
        "model": model,
        "model_arg": bool(args.model),
        "image": args.image,
        "rw": args.rw,
        "ro_ext": args.ro_ext,
        "opencode_key": str(args.opencode_key.resolve()) if args.opencode_key else None,
        "openrouter_key": str(args.openrouter_key.resolve()) if args.openrouter_key else None,
        "session": None,
        "rounds": [],
    }
    return run_round(env, task, message)


def command_fix(env: Env, args: argparse.Namespace) -> int:
    task = load_task(env, args.slug)
    if settle(task):
        save_task(env, task)
    last = task["rounds"][-1] if task["rounds"] else None
    if last and last["status"] == "running":
        raise TaskError(f"round {last['round']} of {args.slug} is still running (pid {last.get('pid')})")
    if not Path(task["worktree"]).is_dir():
        raise TaskError(f"the worktree {task['worktree']} is gone")
    refuse_ignored(Path(task["worktree"]))
    message = read_message(args)
    if args.model:
        live, note = check_model(args.model, env)
        print(f"model:     {args.model}: {note}", flush=True)
        if not live:
            return 3
        task["model"] = args.model
        task["model_arg"] = True
    # Only the path is kept; the launcher reads the key for each run.
    for name in ("opencode_key", "openrouter_key"):
        if getattr(args, name):
            task[name] = str(getattr(args, name).resolve())
    return run_round(env, task, message)


def command_status(env: Env, args: argparse.Namespace) -> int:
    if args.slug:
        task = load_task(env, args.slug)
        if settle(task):
            save_task(env, task)
        if not task["rounds"]:
            raise TaskError(f"task {args.slug} has no rounds")
        print_report(env, task)
        return 0
    tasks = all_tasks(env)
    if not tasks:
        print(f"no tasks in {env.state_root}")
        return 0
    print(f"{'task':<28} {'rounds':>6}  {'last':<12} {'session':<32} worktree")
    for task in tasks:
        if settle(task):
            save_task(env, task)
        last = task["rounds"][-1]["status"] if task["rounds"] else "-"
        gone = "" if Path(task["worktree"]).is_dir() else " (gone)"
        print(f"{task['slug']:<28} {len(task['rounds']):>6}  {last:<12} {task.get('session') or '-':<32} {task['worktree']}{gone}")
    return 0


def round_count(env: Env, slug: str) -> int:
    path = state_dir(env, slug) / "state.json"
    return len(load_task(env, slug)["rounds"]) if path.is_file() else 0


def log_tail(path: Path, lines: int = 12) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    return "\n".join(text[-lines:])


def detach(env: Env, args: argparse.Namespace, argv: list[str]) -> int:
    """Run this start or fix in the background; return once its round is recorded."""
    slug = args.slug
    if args.command == "start":
        if not SLUG_RE.match(slug):
            raise TaskError(f"slug {slug!r}: use lower-case letters, digits and dashes, at most 48")
        if (state_dir(env, slug) / "state.json").exists():
            raise TaskError(f"task {slug} exists: send the next round with fix, or see status {slug}")
    else:
        task = load_task(env, slug)
        if settle(task):
            save_task(env, task)
        last = task["rounds"][-1] if task["rounds"] else None
        if last and last["status"] == "running":
            raise TaskError(f"round {last['round']} of {slug} is still running (pid {last.get('pid')})")
    before = round_count(env, slug)
    folder = state_dir(env, slug)
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "detach.log"
    process = env.spawn([arg for arg in argv if arg != "--detach"], log)
    deadline = time.monotonic() + DETACH_WAIT
    while True:
        if round_count(env, slug) > before:
            number = round_count(env, slug)
            print(f"round {number} of {slug} runs in the background (pid {process.pid}); log: {log}")
            print(f"wait for it: python3 container/tools/ai/delegate.py wait {slug}")
            return 0
        code = process.poll()
        if code is not None:
            print(f"ERROR: the background run ended with exit {code} before its round started:\n{log_tail(log)}", file=sys.stderr)
            return code or 2
        if time.monotonic() >= deadline:
            raise TaskError(f"no round recorded after {DETACH_WAIT:.0f} s; the background run (pid {process.pid}) may still start it: see {log} and status {slug} before you run it again")
        time.sleep(0.2)


def command_wait(env: Env, args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    while True:
        task = load_task(env, args.slug)
        if settle(task):
            save_task(env, task)
        if not task["rounds"]:
            raise TaskError(f"task {args.slug} has no rounds")
        last = task["rounds"][-1]
        if last["status"] != "running":
            print_report(env, task)
            return 0 if last["status"] == "done" else 1
        left = deadline - time.monotonic()
        if left <= 0:
            print(f"round {last['round']} of {args.slug} is still running (pid {last.get('pid')}, started {last['started']}); wait again")
            return STILL_RUNNING
        time.sleep(min(WAIT_POLL, left))


EXAMPLES = """examples:
  delegate.py start fix-typo --brief /tmp/brief.md
  delegate.py start ruby-batch --ruby --rw container/tools/translate --brief b.md
  delegate.py fix fix-typo --brief /tmp/fix-1.md
  delegate.py status fix-typo
  delegate.py fix fix-typo --detach --brief /tmp/opencode/fix-1.md
  delegate.py wait fix-typo

The worker never commits and has no git; review the worktree on the host,
then send what is wrong with fix. The skills assign-to-opencode (Claude Code)
and delegate-to-container (OpenCode) in .claude/skills/ describe the loop.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0], epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def task_text(sub: argparse.ArgumentParser) -> None:
        group = sub.add_mutually_exclusive_group(required=True)
        group.add_argument("--brief", type=Path, help="file with the task text")
        group.add_argument("--message", help="the task text itself")

    def key_files(sub: argparse.ArgumentParser, when: str) -> None:
        sub.add_argument("--opencode-key", type=Path, metavar="FILE", help=f"OpenCode Zen or Go key file outside the repo, {when}")
        sub.add_argument("--openrouter-key", type=Path, metavar="FILE", help=f"OpenRouter key file outside the repo, {when}")

    # No shortened options: --det would pass the argv filter of detach().
    start = subparsers.add_parser("start", help="new task: worktree, model check, round 1", allow_abbrev=False)
    start.add_argument("slug", help="task name: worktree ../PROJECT-agent-SLUG, branch agent/SLUG")
    task_text(start)
    start.add_argument("--worktree", type=Path, help="give the task to this existing worktree instead")
    start.add_argument("--base", default="main", help="branch or commit of the new worktree (default: main)")
    start.add_argument("-m", "--model", help="provider/model for every round (default: the container's opencode.jsonc)")
    image = start.add_mutually_exclusive_group()
    for image_help, flags in IMAGE_GROUPS:
        for flag in flags:
            image.add_argument(f"--{flag}", dest="image", action="store_const", const=flag, help=image_help)
    start.add_argument("--rw", action="append", default=[], metavar="PATH", help="also writable, a path inside the worktree; repeatable")
    start.add_argument("--ro-ext", action="append", default=[], metavar="DIR", help="another checkout shown read-only to the worker at /work/external/<name>; repeatable")
    key_files(start, "for every round")
    start.add_argument("--detach", action="store_true", help="run the round in the background; follow it with wait")

    fix = subparsers.add_parser("fix", help="next round in the same session", allow_abbrev=False)
    fix.add_argument("slug")
    task_text(fix)
    fix.add_argument("-m", "--model", help="switch to this provider/model for this round and the ones after it")
    key_files(fix, "for this round and the ones after it")
    fix.add_argument("--detach", action="store_true", help="run the round in the background; follow it with wait")

    status = subparsers.add_parser("status", help="every task, or one task's last round", allow_abbrev=False)
    status.add_argument("slug", nargs="?")

    wait = subparsers.add_parser("wait", help="block until the running round ends, then report", allow_abbrev=False)
    wait.add_argument("slug")
    wait.add_argument("--timeout", type=float, default=540.0, metavar="SECONDS", help="give up after this long, exit 4 (default: 540)")
    return parser


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(raw)
    handlers = {"start": command_start, "fix": command_fix, "status": command_status, "wait": command_wait}
    try:
        if getattr(args, "detach", False) and not os.environ.get(DETACHED_ENV):
            return detach(env or Env(), args, raw)
        return handlers[args.command](env or Env(), args)
    except (TaskError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
