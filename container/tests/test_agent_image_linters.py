# version 0.1.0
"""The agent images pin the linter versions of .pre-commit-config.yaml.

Every agent image carries ruff, mypy and pylint from /opt/lint, and the host
runs the same three through pre-commit. A version that drifts makes the
worker's gate and the supervisor's gate disagree, so these tests read the
rev of the ruff-pre-commit, mirrors-mypy and pylint repos and require the
same version in each container/containerfiles/Dockerfile*.opencode* file.
No docker is needed: the Dockerfile text is enough.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PRECOMMIT = REPO_ROOT / ".pre-commit-config.yaml"
CONTAINERFILES = REPO_ROOT / "container" / "containerfiles"

#: Package name -> fragment of the pre-commit repo URL that pins it.
PACKAGES = {
    "ruff": "ruff-pre-commit",
    "mypy": "mirrors-mypy",
    "pylint": "pylint",
}

REPO_LINE = re.compile(r"^\s*-\s*repo:\s*(?P<url>\S+)\s*$", re.MULTILINE)
REV_LINE = re.compile(r"^\s*rev:\s*(?P<rev>\S+)\s*$", re.MULTILINE)
PIN = re.compile(r"\b(?P<name>ruff|mypy|pylint)==(?P<version>[0-9][^\s,\\\"']*)")


def precommit_revs(text: str) -> dict[str, str]:
    """Return package -> rev, without the leading ``v``, from a pre-commit config."""
    revs: dict[str, str] = {}
    starts = list(REPO_LINE.finditer(text))
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[match.start() : end]
        rev = REV_LINE.search(block)
        if rev is None:
            continue
        for name, needle in PACKAGES.items():
            if needle in match.group("url"):
                revs[name] = rev.group("rev").removeprefix("v")
    return revs


def dockerfile_pins(text: str) -> dict[str, list[str]]:
    """Return package -> every version the Dockerfile text pins for it."""
    pins: dict[str, list[str]] = {}
    for match in PIN.finditer(text):
        pins.setdefault(match.group("name"), []).append(match.group("version"))
    return pins


def test_precommit_revs_reads_the_three_repos():
    """Read the three revs out of a pre-commit config text."""
    text = "repos:\n- repo: https://github.com/astral-sh/ruff-pre-commit\n  rev: v0.16.8\n  hooks:\n  - id: ruff\n- repo: https://github.com/pre-commit/mirrors-mypy\n  rev: v2.3.1\n  hooks:\n  - id: mypy\n- repo: https://github.com/pylint-dev/pylint\n  rev: v4.0.8\n  hooks:\n  - id: pylint\n"

    assert precommit_revs(text) == {"ruff": "0.16.8", "mypy": "2.3.1", "pylint": "4.0.8"}


def test_precommit_revs_skips_a_repo_without_a_rev():
    """A repo block without a rev adds nothing."""
    text = "- repo: https://github.com/astral-sh/ruff-pre-commit\n  hooks:\n  - id: ruff\n"

    assert not precommit_revs(text)


def test_dockerfile_pins_reads_a_pinned_run_line():
    """Read a wrapped RUN line that installs the three packages."""
    text = "FROM alpine:3.22\nRUN python3 -m venv /opt/lint \\\n    && /opt/lint/bin/pip install --no-cache-dir ruff==0.16.8 mypy==2.3.1 pylint==4.0.8\n"

    assert dockerfile_pins(text) == {
        "ruff": ["0.16.8"],
        "mypy": ["2.3.1"],
        "pylint": ["4.0.8"],
    }


def test_dockerfile_pins_lists_every_pin():
    """Keep every pin, so a duplicate is visible to the caller."""
    assert dockerfile_pins("ruff==1.2.3 ruff==1.2.4")["ruff"] == ["1.2.3", "1.2.4"]


def test_dockerfile_pins_ignores_another_package_with_a_longer_name():
    """A package whose name starts with a linter name is not that linter."""
    assert not dockerfile_pins("RUN pip install ruff-extensions==1.0\n")


def test_agent_images_pin_precommit_linter_versions():
    """Each agent image carries ruff, mypy and pylint at the pre-commit revs."""
    if not PRECOMMIT.is_file():
        pytest.fail(f"{PRECOMMIT} is missing, so there is nothing to compare with")
    revs = precommit_revs(PRECOMMIT.read_text(encoding="utf-8"))
    missing = sorted(set(PACKAGES) - set(revs))
    assert not missing, f"{PRECOMMIT}: no rev found for {', '.join(missing)}"

    dockerfiles = sorted(CONTAINERFILES.glob("Dockerfile*.opencode*"))
    assert dockerfiles, f"no Dockerfile*.opencode* under {CONTAINERFILES}"

    problems = []
    for path in dockerfiles:
        pins = dockerfile_pins(path.read_text(encoding="utf-8"))
        for name, expected in revs.items():
            found = pins.get(name, [])
            if found != [expected]:
                have = ", ".join(found) if found else "no pin"
                problems.append(f"{path.name}: {name} is {have}, pre-commit has {expected}")

    assert not problems, "\n".join(problems)
