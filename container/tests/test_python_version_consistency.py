# version 0.1.1
"""Guards for the Python 3.12 toolchain upgrade.

The 2026-10 pre-commit failure came from a venv whose ``pyvenv.cfg`` still
said 3.10 while ``bin/python`` and ``lib/python3.12`` were already 3.12.
These tests fail on that mixed state and on config/lockfile drift, so the
next Python upgrade must update every layer together.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

LINTERS = Path(__file__).resolve().parents[1] / "tools" / "linters"
sys.path.insert(0, str(LINTERS))

import check_python_consistency as consistency  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def test_checker_runs_on_python_312():
    assert not consistency.check_interpreter()


def test_repository_configuration_matches_python_312():
    errors = consistency.check_repository(require_precommit=False)
    assert not errors


def test_container_venv_is_consistent():
    assert not consistency.check_venv(consistency.REPO_ROOT / "container" / ".venv")


def test_precommit_tool_is_consistent_when_installed():
    if shutil.which("pre-commit") is None:
        pytest.skip("pre-commit is not installed in this environment")
    assert not consistency.check_precommit_tool(require=True)


def test_checker_detects_old_python_version_pin(tmp_path):
    path = tmp_path / ".python-version"
    path.write_text("3.10\n", encoding="utf-8")

    output = "\n".join(consistency.check_python_version_file(path))

    assert "3.10" in output
    assert "3.12" in output


def test_checker_detects_old_pyproject(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[project]\nrequires-python = ">=3.10"\n\n[tool.ruff]\ntarget-version = "py310"\n\n[tool.mypy]\npython_version = "3.10"\n',
        encoding="utf-8",
    )

    output = "\n".join(consistency.check_pyproject(path))

    assert "requires-python" in output
    assert "py312" in output
    assert "mypy python_version" in output


def test_checker_detects_old_lockfile(tmp_path):
    path = tmp_path / "uv.lock"
    path.write_text(
        'version = 1\nrequires-python = ">=3.10"\nresolution-markers = [\n    "python_full_version < \'3.12\'",\n    "python_full_version == \'3.11.*\'",\n]\n',
        encoding="utf-8",
    )

    output = "\n".join(consistency.check_lockfile(path))

    assert "requires-python" in output
    assert "pre-3.12 resolution fork" in output
    assert "Python 3.11 resolution fork" in output


def test_checker_detects_old_dockerfile(tmp_path):
    path = tmp_path / "Dockerfile"
    path.write_text("FROM python:3.10-slim\n", encoding="utf-8")

    output = "\n".join(consistency.check_dockerfile(path))

    assert "python:3.12-slim" in output


def test_checker_detects_stale_pyvenv_and_site_packages(tmp_path):
    venv = tmp_path / "stale"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    (venv / "pyvenv.cfg").write_text("version_info = 3.10.12\n", encoding="utf-8")
    (venv / "lib" / "python3.10").mkdir(parents=True)
    (venv / "lib" / "python3.12").mkdir()

    errors = consistency.check_venv(venv)
    output = "\n".join(errors)

    assert "pyvenv.cfg version_info=3.10.12" in output
    assert "pyvenv.cfg says 3.10.12 but bin/python reports" in output
    assert "stale site-packages dir(s): python3.12" in output


def test_checker_accepts_a_consistent_venv(tmp_path):
    venv = tmp_path / "valid"
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    (venv / "pyvenv.cfg").write_text(f"version_info = {version}.final.0\n", encoding="utf-8")
    (venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}").mkdir(parents=True)

    assert not consistency.check_venv(venv)
