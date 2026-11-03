# version 0.1.0
"""Self-test gate for container/tools/linters/check-version.sh.

The gate requires staged .sh files carrying a "# version" header to bump
it on every change, and staged page files carrying an HTML version marker
to do the same. These tests run the real script in scratch git repos: an
unbumped staged change must fail naming the file, a bumped one must pass,
and a Markdown example must not be mistaken for a page marker. The sample
file itself is never modified (tests copy it).
"""

import subprocess
from pathlib import Path

CHECK_VERSION = Path(__file__).resolve().parents[2] / "tools" / "linters" / "check-version.sh"
SAMPLE = Path(__file__).resolve().parent / "check_version_sample.sh"

TIMEOUT = 60


def make_repo(path, tracked, filename="tool.sh"):
    """Fresh git repo with one committed file; returns its dir."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / filename).write_text(tracked, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True, capture_output=True)
    return path


def run_gate(repo):
    return subprocess.run(
        [str(CHECK_VERSION)],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )


def stage_change(repo, content, filename="tool.sh"):
    (repo / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)


def test_gate_fails_on_unbumped_change(tmp_path):
    repo = make_repo(tmp_path / "fail", SAMPLE.read_text(encoding="utf-8"))
    stage_change(repo, SAMPLE.read_text(encoding="utf-8") + "# touched\n")

    result = run_gate(repo)

    assert result.returncode == 1
    assert "tool.sh" in result.stdout
    assert "unchanged" in result.stdout


def test_gate_passes_on_bumped_change(tmp_path):
    repo = make_repo(tmp_path / "pass", SAMPLE.read_text(encoding="utf-8"))
    bumped = SAMPLE.read_text(encoding="utf-8").replace("# version 0.0.1", "# version 0.0.2", 1)
    stage_change(repo, bumped + "# touched\n")

    result = run_gate(repo)

    assert result.returncode == 0, result.stdout


def test_gate_ignores_scripts_without_header(tmp_path):
    repo = make_repo(tmp_path / "plain", "#!/usr/bin/env bash\nset -euo pipefail\ntrue\n")
    stage_change(repo, "#!/usr/bin/env bash\nset -euo pipefail\ntrue\n# touched\n")

    result = run_gate(repo)

    assert result.returncode == 0, result.stdout


def test_gate_fails_on_unbumped_php_marker(tmp_path):
    original = "<?php echo '<!-- version 0.0.1 -->';\n"
    repo = make_repo(tmp_path / "php-fail", original, filename="page.php")
    stage_change(repo, original + "// touched\n", filename="page.php")

    result = run_gate(repo)

    assert result.returncode == 1
    assert "page.php" in result.stdout
    assert "unchanged" in result.stdout


def test_gate_ignores_markdown_version_example(tmp_path):
    original = "Example: `<!-- version … -->`\n"
    repo = make_repo(tmp_path / "markdown", original, filename="doc.md")
    stage_change(repo, original + "\nMore text.\n", filename="doc.md")

    result = run_gate(repo)

    assert result.returncode == 0, result.stdout
