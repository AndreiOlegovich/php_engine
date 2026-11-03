# version 0.2.0
"""Self-test gate for the phpcs gate: container/tools/linters/lint-static.sh
(phpcs mode), the container/phpcs.xml ruleset it enforces, and the phpcs-src
hook wiring in .pre-commit-config.yaml.

Regression (found 2026-10-19): the `--staged`/`--committed` selections
resolved git paths from `container/tools` instead of `container/` and the
Docker mount (`APP_ROOT`) pointed there too, so the pre-commit phpcs gate
reported "nothing to check" and style errors were committed unchecked.
Same incident class as lint-php.sh (2026-10-12). These tests run the real
script against deliberately broken fixtures and require every one of them
to be reported.

The wiring tests guard the gate itself: the phpcs-src hook must keep
running lint-static.sh --tool phpcs --staged with a `files:` pattern that
covers staged src PHP, and its configured entry must stay the thing that
reports the staged fixtures. The self-test hook must keep firing for every
file the gate depends on (GATE_FILES) — a narrowed trigger would leave
changes to the gating unchecked.
"""

import contextlib
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CONTAINER = REPO / "container"
SRC = CONTAINER / "src"
LINT_STATIC = CONTAINER / "tools" / "linters" / "lint-static.sh"
CONFIG = REPO / ".pre-commit-config.yaml"
SELFTEST_ENTRY = "container/tests/linters/test_lint_static_sh.py"

# Every file the phpcs gate depends on: the self-test hook must re-run
# this file when any of them changes.
GATE_FILES = (
    "container/tools/linters/lint-static.sh",
    "container/phpcs.xml",
    ".pre-commit-config.yaml",
)

TIMEOUT = 600

BROKEN = {
    # PSR12.Files.FileHeader: one blank line required after `<?php`.
    "__lint_static_selftest_header__.php": "<?php\n// no blank line after the opening tag\n",
    # PSR12.Operators.OperatorSpacing: the `.` operator needs spaces.
    "__lint_static_selftest_dot__.php": "<?php\n\n$title = 'a'.$title_suffix;\n",
}
CLEAN = {
    "__lint_static_selftest_clean__.php": '<?php\n\necho "ok";\n',
}

ALL_FIXTURES = {**BROKEN, **CLEAN}

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is required to run lint-static.sh")


def write_fixtures(names):
    written = []
    for name in names:
        path = SRC / name
        path.write_text(ALL_FIXTURES[name], encoding="utf-8")
        written.append(path)
    return written


def remove_fixtures():
    subprocess.run(
        ["git", "rm", "--cached", "-q", "--ignore-unmatch"] + [f"container/src/{name}" for name in ALL_FIXTURES],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    for name in ALL_FIXTURES:
        with contextlib.suppress(FileNotFoundError):
            (SRC / name).unlink()


def run_lint_static(*args):
    return subprocess.run(
        [str(LINT_STATIC), "--tool", "phpcs", *args],
        cwd=CONTAINER,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )


def load_hooks():
    """All hooks from .pre-commit-config.yaml."""
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return [hook for repo in config["repos"] for hook in repo.get("hooks", [])]


def find_gate_hooks(hooks):
    """(hook, tokens) for every hook running lint-static.sh ... phpcs."""
    found = []
    for hook in hooks:
        tokens = shlex.split(hook.get("entry", ""))
        if "phpcs" in tokens and any(token.endswith("lint-static.sh") for token in tokens):
            found.append((hook, tokens))
    return found


def require_single_gate_hook():
    found = find_gate_hooks(load_hooks())
    ids = [hook.get("id") for hook, _ in found]
    assert len(found) == 1, f"expected exactly one hook running lint-static.sh ... phpcs, found: {ids or 'none'}"
    return found[0]


@pytest.fixture
def fixtures():
    remove_fixtures()
    try:
        yield write_fixtures(ALL_FIXTURES)
    finally:
        remove_fixtures()


@needs_docker
def test_files_mode_reports_every_broken_fixture(fixtures):
    result = run_lint_static("--files", *[f"src/{path.name}" for path in fixtures])
    assert result.returncode != 0, f"phpcs passed on broken fixtures:\n{result.stdout}"
    for name in BROKEN:
        assert name in result.stdout, f"{name} was not reported. Output:\n{result.stdout}"
    for name in CLEAN:
        assert name not in result.stdout, f"clean file {name} was wrongly reported. Output:\n{result.stdout}"


@needs_docker
def test_staged_mode_detects_staged_broken_files(fixtures):
    staged = [f"container/src/{path.name}" for path in fixtures]
    subprocess.run(["git", "add", *staged], cwd=REPO, check=True)
    try:
        result = run_lint_static("--staged")
    finally:
        subprocess.run(["git", "rm", "--cached", "-q", *staged], cwd=REPO, check=False)
    assert result.returncode != 0, f"staged phpcs passed on broken files:\n{result.stdout}"
    assert "nothing to check" not in result.stdout, "staged selection found no files (the empty-selection regression is back)"
    for name in BROKEN:
        assert name in result.stdout, f"staged {name} was not reported. Output:\n{result.stdout}"
    for name in CLEAN:
        assert name not in result.stdout, f"staged clean file {name} was wrongly reported. Output:\n{result.stdout}"


def test_phpcs_gate_hook_is_wired():
    """The phpcs-src hook must keep selecting and reporting staged src PHP."""
    hook, tokens = require_single_gate_hook()

    script = next(token for token in tokens if token.endswith("lint-static.sh"))
    script_path = REPO / script
    assert script_path.is_file(), f"gate entry points at a missing script: {script}"
    assert os.access(script_path, os.X_OK), f"gate script is not executable: {script}"

    pairs = list(zip(tokens, tokens[1:], strict=False))
    assert ("--tool", "phpcs") in pairs, f"gate entry no longer runs phpcs: {hook.get('entry')}"
    assert "--staged" in tokens, f"gate entry no longer selects staged files: {hook.get('entry')}"
    assert hook.get("pass_filenames") is False, "the gate must select files itself (pass_filenames: false)"
    assert hook.get("stages") in (None, ["pre-commit"]), "the gate must run in the pre-commit stage"

    files = hook.get("files")
    assert files, "the gate hook needs a `files:` pattern"
    for probe in ("container/src/page.php", "container/src/a/b/lesson.php"):
        assert re.search(files, probe), f"gate `files:` pattern {files!r} does not match {probe!r} — staged src PHP would silently skip the phpcs gate"


def test_selftest_triggers_on_every_phpcs_gating_file():
    """Any change to the phpcs gating files must re-run this self-test."""
    matching = [hook for hook in load_hooks() if SELFTEST_ENTRY in hook.get("entry", "")]
    assert len(matching) == 1, "this self-test must be wired into exactly one pre-commit hook"
    files = matching[0].get("files")
    assert files, "the self-test hook needs a `files:` pattern"
    for path in GATE_FILES:
        assert re.search(files, path), f"{path} no longer triggers the phpcs gate self-test — changes to it would be guarded by nothing"


@needs_docker
def test_configured_gate_entry_reports_staged_broken_fixtures(fixtures):
    """The entry as written in .pre-commit-config.yaml must catch staged breaks."""
    tokens = require_single_gate_hook()[1]
    staged = [f"container/src/{path.name}" for path in fixtures]
    subprocess.run(["git", "add", *staged], cwd=REPO, check=True)
    try:
        result = subprocess.run(
            tokens,
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    finally:
        subprocess.run(["git", "rm", "--cached", "-q", *staged], cwd=REPO, check=False)
    assert result.returncode != 0, f"configured gate entry passed on broken fixtures:\n{result.stdout}"
    assert "nothing to check" not in result.stdout, "gate entry selected no files (the empty-selection regression is back)"
    for name in BROKEN:
        assert name in result.stdout, f"{name} was not reported by the configured gate entry. Output:\n{result.stdout}"
    for name in CLEAN:
        assert name not in result.stdout, f"clean file {name} was wrongly reported by the configured gate entry. Output:\n{result.stdout}"


def test_no_fixture_leftovers():
    leftovers = [name for name in ALL_FIXTURES if (SRC / name).exists()]
    assert not leftovers, f"leftover self-test fixtures: {leftovers}"
    tracked = subprocess.run(
        ["git", "ls-files", "container/src/__lint_static_selftest_*"],
        cwd=REPO,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    ).stdout.strip()
    assert not tracked, f"self-test fixtures are tracked: {tracked}"


if __name__ == "__main__":
    raise SystemExit("run with: pytest container/tests/linters/test_lint_static_sh.py -q")
