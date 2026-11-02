# version 0.1.0
"""Regression tests for the size policy in tools/analyze_logs/download-logs.sh.

The downloader is a single self-contained shell script with an embedded
Python FTP client. These tests extract the `run()` function via `ast` and
drive it with a fake FTP so the incremental rule stays honest:

- a missing local log is downloaded;
- a local log smaller than the remote one is incomplete and is replaced;
- an equal-size local log is skipped (unchanged);
- a larger local log is also replaced: only a same-size copy is skipped;

Tests do not connect to FTP and do not need credentials.
"""

import ast
import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "analyze_logs" / "download-logs.sh"
PYTHON_BLOCK = re.compile(r"python3 - <<'EOF'\n(.*?)\nEOF\n", re.S)


class FakeFTP:
    """Minimal FTP stand-in that returns the configured payload."""

    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def retrbinary(self, command, callback):
        self.requests.append(command)
        callback(self.payload)


def load_run_function(tmp_path):
    """Write just the embedded `run()` definition to a module and import it."""
    match = PYTHON_BLOCK.search(SCRIPT.read_text(encoding="utf-8"))
    assert match, f"embedded Python block not found in {SCRIPT}"
    embedded = match.group(1)
    tree = ast.parse(embedded)
    run_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run")
    source = ast.get_source_segment(embedded, run_node)
    assert source, "could not extract the run() source"
    module_path = tmp_path / "download_logs_run.py"
    module_path.write_text(
        "import os\n\nforce = False\ndryrun = False\nverbose = False\n\n" + source + "\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("download_logs_run", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


@pytest.mark.parametrize(
    ("existing_size", "remote_size", "expect_download", "expect_size"),
    [
        (None, 10, True, 10),
        (5, 10, True, 10),
        (10, 10, False, 10),
        (15, 10, True, 10),
    ],
    ids=["missing", "smaller-replaced", "equal-skipped", "larger-replaced"],
)
def test_local_log_size_policy(tmp_path, existing_size, remote_size, expect_download, expect_size):
    """Replace an incomplete local copy, keep equal or larger local copies."""
    local = tmp_path / "access.log"
    if existing_size is not None:
        local.write_bytes(b"x" * existing_size)

    run = load_run_function(tmp_path)
    ftp = FakeFTP(b"r" * remote_size)
    downloaded, skipped, failed = run([("prod_logs/access.log", str(local), remote_size)], ftp)

    assert failed == 0
    assert downloaded == int(expect_download)
    assert skipped == int(not expect_download)
    assert local.stat().st_size == expect_size
    assert bool(ftp.requests) is expect_download
    if expect_download:
        assert local.read_bytes() == ftp.payload
