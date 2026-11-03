# version 0.1.2
"""Tests for the dates, --since and --check-prod parts of analyze_errors.py.

Dummy log lines only; the client addresses are from the documentation range.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

ANALYZE_LOGS = Path(__file__).resolve().parents[2] / "tools" / "analyze_logs"
PREFIX = "/var/www/html/"


def _module():
    sys.path.insert(0, str(ANALYZE_LOGS))
    try:
        spec = importlib.util.spec_from_file_location("php_engine_analyze_errors", ANALYZE_LOGS / "analyze_errors.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(ANALYZE_LOGS))


def _fatal(ts: str, client: str, rel: str) -> str:
    return f'[{ts}] [error] [client {client}:1234] PHP Fatal error:  Uncaught Error: Class "RelatedArticles" not found in {PREFIX}{rel}:12\n'


def _logs(tmp_path: Path) -> Path:
    log = tmp_path / "error.log"
    log.write_text(
        _fatal("Thu Oct 01 10:00:00.123456 2026", "203.0.113.5", "old/index.php") + _fatal("Fri Oct 02 11:00:00 2026", "203.0.113.6", "old/index.php") + _fatal("Thu Oct 22 09:30:00.000001 2026", "203.0.113.7", "current/index.php"),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "botlist.csv").write_text("ip\n", encoding="utf-8")
    return log


def test_timestamps_with_and_without_microseconds_are_read():
    analyze = _module()
    assert analyze.parse_ts("Thu Oct 22 21:11:34.663857 2026") == datetime(2026, 10, 22, 21, 11, 34, 663857)
    assert analyze.parse_ts("Fri Oct 02 11:00:00 2026") == datetime(2026, 10, 2, 11, 0, 0)
    assert analyze.parse_ts("not a time") is None


def test_report_shows_first_and_last_seen(tmp_path, capsys):
    analyze = _module()
    log = _logs(tmp_path)
    assert analyze.main(["--log-file", str(log), "--src", str(tmp_path / "src"), "--botlist", str(tmp_path / "botlist.csv")]) == 0
    out = capsys.readouterr().out
    assert "[2x, 2 clients] old/index.php\n  seen: first 2026-10-01 10:00, last 2026-10-02 11:00" in out
    assert "[1x, 1 clients] current/index.php\n  seen: first 2026-10-22 09:30, last 2026-10-22 09:30" in out


def test_since_drops_errors_from_before_a_deploy(tmp_path, capsys):
    analyze = _module()
    log = _logs(tmp_path)
    assert analyze.main(["--log-file", str(log), "--src", str(tmp_path / "src"), "--botlist", str(tmp_path / "botlist.csv"), "--since", "2026-10-15"]) == 0
    out = capsys.readouterr().out
    assert "since 2026-10-15 00:00: dropped 2 older records" in out
    assert "current/index.php" in out
    assert "old/index.php" not in out
    assert analyze.main(["--log-file", str(log), "--src", str(tmp_path / "src"), "--botlist", str(tmp_path / "botlist.csv"), "--since", "15.10.2026"]) == 2


def test_check_prod_asks_prod_once_per_fatal_page_within_the_cap(tmp_path, monkeypatch):
    analyze = _module()
    asked: list[str] = []
    monkeypatch.setattr(analyze, "check_dev_url", lambda url, timeout=10: asked.append(url) or "200")
    records = [analyze.parse_line(line) for line in _logs(tmp_path).read_text(encoding="utf-8").splitlines()]
    report = analyze.analyze(records, str(tmp_path / "src"), "https://dev.example.invalid", False, 0, 30, "https://prod.example.invalid", True, 1)
    assert asked == ["https://prod.example.invalid/old/"]
    assert "prod now: https://prod.example.invalid/old/ -> 200" in report
    assert "prod now: https://prod.example.invalid/current/" not in report
