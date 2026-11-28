#!/usr/bin/env python3
# version 0.1.2
"""Read the sessions OpenCode keeps in the agent container's Docker volume.

container/tools/ai/opencode-docker.sh mounts the named volume
<project>-agent-data at /home/agent/.local/share/opencode, so every TUI or
`run` session started in that container lives in one SQLite database there.
This tool copies that database (main file, WAL and SHM) out of the volume
into a temporary directory, opens the copy read-only and prints:

  list              sessions, newest first, with id, time, message count
  search TEXT       sessions whose user prompts contain TEXT
  show SESSION      the transcript: user prompts, assistant texts, tool calls

Usage (any working directory):
  python3 container/tools/opencode/agent_sessions.py list
  python3 container/tools/opencode/agent_sessions.py search "php functions"
  python3 container/tools/opencode/agent_sessions.py show ses_...
  python3 container/tools/opencode/agent_sessions.py show ses_... --prompts

A database copied out earlier can be read with --db FILE. --volume NAME
selects another Docker volume, --image NAME another image that has `sh` and
`cp` (the default is the image opencode-docker.sh builds).

Exit: 0 printed, 2 a database or Docker volume could not be read.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from project_name import container_name, project_name  # pylint: disable=wrong-import-position,import-error  # noqa: E402

DEFAULT_VOLUME = f"{project_name()}-agent-data"
DEFAULT_IMAGE = f"{project_name()}-agent"
TEXT_LIMIT = 3000
TOOL_LIMIT = 500

EXAMPLES = """examples:
  agent_sessions.py list -n 10
  agent_sessions.py search "create pages"
  agent_sessions.py show ses_... --prompts
  agent_sessions.py list --db /tmp/exported.db

The prompt of a session is its first `user` message; `search` looks only at
those, `show` prints the whole transcript unless --prompts is given.
"""


class ToolError(RuntimeError):
    """A problem the user can fix: no Docker, no volume, no database."""


def copy_volume(volume: str, image: str, out_dir: Path) -> Path:
    """Copy the SQLite files of a Docker volume into out_dir, return the .db."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # The wildcard copies the WAL and SHM too, so recent messages survive;
    # both are optional, hence `|| true`.
    script = "cp /data/*.db* /out/ 2>/dev/null || true"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name("agent-sessions"),
        "-v",
        f"{volume}:/data:ro",
        "-v",
        f"{out_dir}:/out",
        "--entrypoint",
        "sh",
        image,
        "-c",
        script,
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ToolError("docker is not installed; pass --db FILE with an exported database") from exc
    if completed.returncode != 0:
        lines = (completed.stderr or completed.stdout).strip().splitlines()
        detail = lines[-1] if lines else "docker run failed"
        raise ToolError(f"cannot read Docker volume {volume!r}: {detail}")
    candidates = sorted(out_dir.glob("*.db"))
    if not candidates:
        raise ToolError(f"no SQLite database in volume {volume!r}; is {image!r} the agent image?")
    return candidates[0]


def open_db(path: Path) -> sqlite3.Connection:
    """Open an existing database read-only."""
    if not path.is_file():
        raise ToolError(f"database not found: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def database(args: argparse.Namespace) -> Iterator[sqlite3.Connection]:
    """A read-only connection to --db, or to a copy of the Docker volume."""
    if args.db is not None:
        connection = open_db(args.db)
        try:
            yield connection
        finally:
            connection.close()
        return
    with tempfile.TemporaryDirectory(prefix="opencode-sessions-") as tmp:
        path = copy_volume(args.volume, args.image, Path(tmp))
        connection = open_db(path)
        try:
            yield connection
        finally:
            connection.close()


def timestamp(milliseconds: int | None) -> str:
    """A millisecond epoch as local date and time, or '-' when absent."""
    if not milliseconds:
        return "-"
    return datetime.datetime.fromtimestamp(milliseconds / 1000).strftime("%Y-%m-%d %H:%M")


def session_rows(connection: sqlite3.Connection, limit: int | None) -> list[dict[str, Any]]:
    """Sessions, newest first, with directory, title, times and message count."""
    query = """
        select s.id, s.directory, s.title, s.parent_id,
               s.time_created, s.time_updated,
               (select count(*) from session_message m where m.session_id = s.id) as messages
        from session_v2 s
        order by s.time_updated desc
    """
    parameters: tuple[Any, ...] = ()
    if limit is not None:
        query += " limit ?"
        parameters = (limit,)
    return [
        {
            "id": row["id"],
            "directory": row["directory"],
            "title": row["title"],
            "parent": row["parent_id"],
            "created": row["time_created"],
            "updated": row["time_updated"],
            "messages": row["messages"],
        }
        for row in connection.execute(query, parameters)
    ]


def session_info(connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """One session row in the shape session_rows returns, or ToolError."""
    for row in session_rows(connection, None):
        if row["id"] == session_id:
            return row
    raise ToolError(f"session not found: {session_id}")


def message_data(row: sqlite3.Row) -> dict[str, Any]:
    """The JSON payload of a session_message row, never raising on bad JSON."""
    data = row["data"]
    if isinstance(data, dict):
        return data
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return {"text": data}
        if isinstance(parsed, dict):
            return parsed
    return {}


def message_rows(connection: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    """Every message of a session in sequence order."""
    return list(
        connection.execute(
            "select seq, type, time_created, data from session_message where session_id = ? order by seq",
            (session_id,),
        )
    )


def prompt_text(data: dict[str, Any]) -> str:
    """The text of a user message, whichever field the version stores it in."""
    for key in ("text", "prompt"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def search_prompts(connection: sqlite3.Connection, needle: str) -> list[dict[str, Any]]:
    """One hit per user message containing needle, ignoring case."""
    query = """
        select m.session_id, m.seq, m.time_created, m.data,
               s.title, s.directory, s.time_updated
        from session_message m
        join session_v2 s on s.id = m.session_id
        where m.type = 'user'
        order by s.time_updated desc, m.seq
    """
    lowered = needle.casefold()
    hits: list[dict[str, Any]] = []
    for row in connection.execute(query):
        text = prompt_text(message_data(row))
        if lowered in text.casefold():
            hits.append(
                {
                    "id": row["session_id"],
                    "title": row["title"],
                    "directory": row["directory"],
                    "updated": row["time_updated"],
                    "seq": row["seq"],
                    "time": row["time_created"],
                    "prompt": text,
                }
            )
    return hits


def clip(text: str, limit: int, full: bool) -> str:
    """Shorten text unless --full is on."""
    if full or len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… [{len(text) - limit} characters cut]"


def tool_input(value: Any) -> str:
    """One-line rendering of a tool call's input."""
    if isinstance(value, dict) and list(value) == ["command"]:
        return str(value["command"])
    return json.dumps(value, ensure_ascii=False)


def print_session_header(info: dict[str, Any]) -> None:
    """The three lines above a transcript."""
    print(f"SESSION {info['id']} — {info['title'] or '(untitled)'}")
    print(f"directory: {info['directory'] or '-'}")
    print(f"updated:   {timestamp(info['updated'])} · messages: {info['messages']}")
    if info["parent"]:
        print(f"parent:    {info['parent']}")


def print_message(row: sqlite3.Row, args: argparse.Namespace) -> None:
    """One transcript block: user text, assistant parts or an idle result."""
    kind = row["type"]
    data = message_data(row)
    print()
    print(f"── {row['seq']} · {kind} · {timestamp(row['time_created'])}")
    if kind == "user":
        print(prompt_text(data) or "(no text)")
        return
    if kind == "assistant":
        for part in data.get("content", []):
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text = str(part.get("text", "")).strip()
                if text:
                    print(clip(text, TEXT_LIMIT, args.full))
            elif part_type == "tool":
                state = part.get("state", {})
                name = part.get("name", "?")
                print(f"  · {name}: {clip(tool_input(state.get('input', {})), TOOL_LIMIT, args.full)}")
                if args.tool_output:
                    result = "".join(str(chunk.get("text", "")) for chunk in state.get("content", []) if isinstance(chunk, dict)).strip()
                    if result:
                        print(clip(result, TOOL_LIMIT, args.full))
            elif part_type == "reasoning" and args.reasoning:
                text = str(part.get("text", "")).strip()
                if text:
                    print(f"  (thinking) {clip(text, TEXT_LIMIT, args.full)}")
        return
    print(clip(json.dumps(data, ensure_ascii=False), TEXT_LIMIT, args.full))


def command_list(args: argparse.Namespace) -> None:
    """Print sessions as a table or JSON."""
    with database(args) as connection:
        rows = session_rows(connection, args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("no sessions")
        return
    for row in rows:
        print(f"{row['id']}  {timestamp(row['updated'])}  {row['messages']:>4}  {row['title'] or '(untitled)'}")


def command_search(args: argparse.Namespace) -> None:
    """Print sessions whose user prompts contain args.text."""
    with database(args) as connection:
        hits = search_prompts(connection, args.text)
    if args.json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
        return
    if not hits:
        print("no matching prompts")
        return
    for hit in hits:
        print(f"{hit['id']}  {timestamp(hit['updated'])}  {hit['title'] or '(untitled)'}")
        print(f"  [seq {hit['seq']}] {clip(hit['prompt'], TEXT_LIMIT, args.full)}")


def command_show(args: argparse.Namespace) -> None:
    """Print one transcript, user prompts only with --prompts."""
    with database(args) as connection:
        info = session_info(connection, args.session)
        rows = message_rows(connection, args.session)
    if args.prompts:
        rows = [row for row in rows if row["type"] == "user"]
    if args.json:
        payload = {
            "session": info,
            "messages": [{"seq": row["seq"], "type": row["type"], "time": row["time_created"], "data": message_data(row)} for row in rows],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print_session_header(info)
    if not rows:
        print("\nno messages")
        return
    for row in rows:
        print_message(row, args)


def build_parser() -> argparse.ArgumentParser:
    """The CLI: shared --db/--volume/--image on every subcommand."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", type=Path, default=None, help="read an exported SQLite file instead of the Docker volume")
    common.add_argument(
        "--volume",
        default=os.environ.get("OPENCODE_AGENT_VOLUME", DEFAULT_VOLUME),
        help=f"Docker volume with the agent database (default: {DEFAULT_VOLUME})",
    )
    common.add_argument(
        "--image",
        default=os.environ.get("OPENCODE_AGENT_IMAGE", DEFAULT_IMAGE),
        help=f"image with sh and cp (default: {DEFAULT_IMAGE})",
    )
    parser = argparse.ArgumentParser(
        description="List, search and show sessions of the OpenCode agent container.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("list", parents=[common], help="sessions, newest first")
    listing.add_argument("-n", "--limit", type=int, default=30, help="show at most N sessions (default: 30)")
    listing.add_argument("--json", action="store_true", help="print JSON instead of a table")
    listing.set_defaults(func=command_list)

    search = subparsers.add_parser("search", parents=[common], help="sessions whose user prompts contain TEXT")
    search.add_argument("text", help="text to look for, ignoring case")
    search.add_argument("--json", action="store_true", help="print JSON instead of lines")
    search.add_argument("--full", action="store_true", help="do not shorten long prompts")
    search.set_defaults(func=command_search)

    show = subparsers.add_parser("show", parents=[common], help="the transcript of one session")
    show.add_argument("session", help="session id, ses_...")
    show.add_argument("--prompts", action="store_true", help="print only the user prompts")
    show.add_argument("--tool-output", action="store_true", help="print what each tool call returned")
    show.add_argument("--reasoning", action="store_true", help="print the model's thinking blocks")
    show.add_argument("--full", action="store_true", help="do not shorten long texts")
    show.add_argument("--json", action="store_true", help="print JSON instead of text")
    show.set_defaults(func=command_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; ToolError becomes a message and exit code 2."""
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ToolError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
