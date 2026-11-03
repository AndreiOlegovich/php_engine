# version 0.1.2
"""Tests for the bot-signature rule of check_bots.py.

A scraper glues old card image names onto page paths (/ru/qa/it/gulp_card)
and forges a two-URL Referer, one request per IP, so only a signature can
list it. Dummy log lines only; the client addresses are from the
documentation ranges.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

ANALYZE_LOGS = Path(__file__).resolve().parents[2] / "tools" / "analyze_logs"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_2; rv:52.2.0) Gecko/20100101 Firefox/52.2.0"


def _module():
    sys.path.insert(0, str(ANALYZE_LOGS))
    try:
        spec = importlib.util.spec_from_file_location("php_engine_check_bots", ANALYZE_LOGS / "check_bots.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(ANALYZE_LOGS))


bots = _module()


def _line(ip: str, target: str, status: int = 404, referer: str = "-") -> str:
    return f'example.com {ip} - - [30/Oct/2026:23:47:27 +0300] "GET {target} HTTP/1.1" {status} 5234 "{referer}" "{BROWSER_UA}" 26655 23709:3387\n'


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/ru/code/python/packages/layout/it/postgresql_card", True),
        ("/ru/qa/it/gulp_card", True),
        ("/testing/robot/it/ms_powerpoint_card?x=1", True),
        ("/sport/injuries/meniscus/it/testing_edu_card/", True),
        ("/img/cards/it/postgresql_card.png", False),
        ("/img/cards//it/postgresql_card.webp", False),
        ("/ru/i/postgres/", False),
        ("/it/", False),
    ],
)
def test_is_card_path(target: str, expected: bool) -> None:
    assert bots.is_card_path(target) is expected


@pytest.mark.parametrize(
    ("referer", "expected"),
    [
        ("https://google.com, https://old.example.invalid/testing/robot/?utm_source=example.invalid&utm_campaign=rblock", True),
        ("https://google.com, https://example.invalid/windows/network/telnet/", True),
        ("-", False),
        ("", False),
        ("https://old.example.invalid/testing/jira/", False),
        ("https://example.com/list?ids=1,2,3", False),
        ("https://example.com/?next=a,https://example.org/", False),
    ],
)
def test_is_forged_referer(referer: str, expected: bool) -> None:
    assert bots.is_forged_referer(referer) is expected


def test_one_signature_request_lists_the_ip(tmp_path: Path) -> None:
    logs = tmp_path / "access"
    logs.mkdir()
    (logs / "example.com.access.log").write_text(
        _line("203.0.113.10", "/ru/code/python/packages/layout/it/postgresql_card") + _line("203.0.113.11", "/ru/qa/robot/", 200, "https://google.com, https://old.example.invalid/testing/robot/") + _line("203.0.113.12", "/ru/code/python/class/variables.php") + _line("203.0.113.13", "/img/cards/it/postgresql_card.png", 200, "https://example.com/ru/i/postgres/") + _line("198.51.100.20", "/ru/git"),
        encoding="utf-8",
    )
    output = tmp_path / "botlist.csv"
    assert bots.main(["--log-dir", str(logs), "--output", str(output)]) == 0
    with open(output, encoding="utf-8", newline="") as handle:
        listed = {row["ip"] for row in csv.DictReader(handle)}
    assert listed == {"203.0.113.10", "203.0.113.11"}


def test_failed_write_keeps_the_old_botlist(tmp_path: Path) -> None:
    output = tmp_path / "botlist.csv"
    output.write_text("ip\n203.0.113.10\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bots.write_botlist([{"ip": "203.0.113.11", "not_a_column": 1}], str(output))
    assert output.read_text(encoding="utf-8") == "ip\n203.0.113.10\n"
    assert [path.name for path in tmp_path.iterdir()] == ["botlist.csv"]
