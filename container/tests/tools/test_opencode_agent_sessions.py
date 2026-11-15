# version 0.1.0
"""Tests for tools/opencode/agent_sessions.py (no Docker needed)."""

import json
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent.parent / "tools" / "opencode"
sys.path.insert(0, str(TOOLS))

import agent_sessions as sessions  # pylint: disable=wrong-import-position,import-error  # noqa: E402


def make_db(root: Path) -> Path:
    """An OpenCode-shaped database with one session and three messages."""
    path = root / "opencode.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table session_v2 (
            id text primary key,
            directory text,
            title text,
            parent_id text,
            time_created integer,
            time_updated integer
        );
        create table session_message (
            session_id text,
            type text,
            seq integer,
            time_created integer,
            data text
        );
        """
    )
    connection.execute(
        "insert into session_v2 values (?, ?, ?, ?, ?, ?)",
        ("ses_1", "/work", "PHP 8.2 function pages", None, 1_000, 9_000),
    )
    connection.execute(
        "insert into session_message values (?, ?, ?, ?, ?)",
        (
            "ses_1",
            "user",
            4,
            1_000,
            json.dumps({"text": "inside src/section/functions create index.php"}),
        ),
    )
    connection.execute(
        "insert into session_message values (?, ?, ?, ?, ?)",
        (
            "ses_1",
            "assistant",
            7,
            2_000,
            json.dumps(
                {
                    "content": [
                        {"type": "text", "text": "I will create the pages."},
                        {
                            "type": "tool",
                            "name": "shell",
                            "state": {"input": {"command": "ls /work"}, "content": [{"type": "text", "text": "container"}]},
                        },
                    ]
                }
            ),
        ),
    )
    connection.execute(
        "insert into session_message values (?, ?, ?, ?, ?)",
        (
            "ses_1",
            "idle",
            9,
            3_000,
            json.dumps({"outcome": "failed", "error": {"type": "provider.free-limit"}}),
        ),
    )
    connection.commit()
    connection.close()
    return path


def test_list_prints_session(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["list", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "ses_1" in out
    assert "PHP 8.2 function pages" in out
    assert "3" in out


def test_list_json_is_machine_readable(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["list", "--db", str(db), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["id"] == "ses_1"
    assert rows[0]["messages"] == 3
    assert rows[0]["directory"] == "/work"


def test_search_matches_prompt_ignoring_case(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["search", "--db", str(db), "SRC/SECTION/FUNCTIONS"]) == 0
    out = capsys.readouterr().out
    assert "ses_1" in out
    assert "create index.php" in out


def test_search_reports_no_match(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["search", "--db", str(db), "openrouter"]) == 0
    assert "no matching prompts" in capsys.readouterr().out


def test_show_prompts_only_prints_first_user_message(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["show", "--db", str(db), "ses_1", "--prompts"]) == 0
    out = capsys.readouterr().out
    assert "inside src/section/functions create index.php" in out
    assert "I will create the pages." not in out
    assert "provider.free-limit" not in out


def test_show_prints_assistant_text_tool_and_idle(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["show", "--db", str(db), "ses_1"]) == 0
    out = capsys.readouterr().out
    assert "I will create the pages." in out
    assert "shell: ls /work" in out
    assert "provider.free-limit" in out


def test_show_tool_output_flag(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["show", "--db", str(db), "ses_1", "--tool-output"]) == 0
    assert "container" in capsys.readouterr().out


def test_show_json_keeps_message_payloads(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["show", "--db", str(db), "ses_1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session"]["id"] == "ses_1"
    assert [message["type"] for message in payload["messages"]] == ["user", "assistant", "idle"]
    assert payload["messages"][0]["data"]["text"].startswith("inside")


def test_unknown_session_is_an_error(tmp_path, capsys):
    db = make_db(tmp_path)
    assert sessions.main(["show", "--db", str(db), "ses_missing"]) == 2
    assert "session not found" in capsys.readouterr().err


def test_missing_database_is_an_error(tmp_path, capsys):
    assert sessions.main(["list", "--db", str(tmp_path / "absent.db")]) == 2
    assert "database not found" in capsys.readouterr().err
