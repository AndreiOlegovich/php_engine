# version 0.1.1
"""Check that the repository's Python toolchain is consistently on 3.12.

Run from the repository root:

    python3 container/tools/linters/check_python_consistency.py

The pre-commit hook adds ``--require-precommit`` because the hook proves that a
working pre-commit executable is present.

The checker is deliberately stdlib-only so it also works before the project
virtualenv exists. It inspects:

* the interpreter running the checker and the root ``.python-version`` pin;
* the project ``pyproject.toml`` and its ``uv.lock`` file;
* the Python Docker images;
* the pre-commit default language and hook wiring;
* the stale-venv recreation logic in ``container/tools/run-tests-venv.sh``;
* the existing ``container/.venv`` and the uv-installed pre-commit
  environment.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

TARGET = (3, 12)
TARGET_TEXT = "3.12"
RUFF_TARGET = "py312"
DOCKER_IMAGE = "python:3.12-slim"

REPO_ROOT = Path(__file__).resolve().parents[3]

PYTHON_VERSION_FILE = ".python-version"
PYPROJECTS = ("container/pyproject.toml",)
LOCKFILES = ("container/uv.lock",)
DOCKERFILES = ("container/pytest_web/Dockerfile",)
VENVS = ("container/.venv",)


def _run(args: Sequence[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a command from the repository root and capture combined output."""
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )


def _label(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _normalize_version(value: str) -> str:
    """Reduce ``3.12.3`` / ``3.12.3.final.0`` to major.minor.micro."""
    match = re.match(r"^(\d+\.\d+\.\d+)", value)
    return match.group(1) if match else value


def check_interpreter() -> list[str]:
    """The checker itself must run under the same minor version as the repo."""
    if sys.version_info[:2] != TARGET:
        return [f"current interpreter is {sys.version.split()[0]} ({sys.executable}); expected {TARGET_TEXT}.x"]
    return []


def check_python_version_file(path: Path) -> list[str]:
    """Check the uv/pyenv version pin at the repository root."""
    label = _label(path)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return [f"{label}: cannot read Python version pin: {exc}"]

    if value != TARGET_TEXT:
        return [f"{label}: pins Python {value!r}; expected {TARGET_TEXT!r}"]
    return []


def check_pyproject(path: Path) -> list[str]:
    """Check requires-python, ruff target and optional mypy target."""
    label = _label(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"{label}: cannot read TOML: {exc}"]

    errors: list[str] = []
    project = data.get("project", {})
    requires = project.get("requires-python")
    expected_requires = f">={TARGET_TEXT}"
    if requires != expected_requires:
        errors.append(f"{label}: requires-python is {requires!r}; expected {expected_requires!r}")

    tool = data.get("tool", {})
    ruff_target = tool.get("ruff", {}).get("target-version")
    if ruff_target != RUFF_TARGET:
        errors.append(f"{label}: ruff target-version is {ruff_target!r}; expected {RUFF_TARGET!r}")

    mypy_target = tool.get("mypy", {}).get("python_version")
    if mypy_target is not None and mypy_target != TARGET_TEXT:
        errors.append(f"{label}: mypy python_version is {mypy_target!r}; expected {TARGET_TEXT!r}")

    return errors


def check_lockfile(path: Path) -> list[str]:
    """Check the lockfile's Python floor and reject pre-3.12 resolution forks."""
    label = _label(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{label}: cannot read lockfile: {exc}"]

    errors: list[str] = []
    match = re.search(r'^requires-python = "([^"]+)"', text, re.MULTILINE)
    expected = f">={TARGET_TEXT}"
    if match is None:
        errors.append(f"{label}: missing requires-python header")
    elif match.group(1) != expected:
        errors.append(f"{label}: requires-python is {match.group(1)!r}; expected {expected!r}")

    if "python_full_version < '3.12'" in text:
        errors.append(f"{label}: still contains a pre-3.12 resolution fork")
    for old_minor in ("3.10", "3.11"):
        if f"python_full_version == '{old_minor}.*'" in text:
            errors.append(f"{label}: still contains a Python {old_minor} resolution fork")

    return errors


def check_dockerfile(path: Path) -> list[str]:
    """Check the first FROM line uses the Python 3.12 slim image."""
    label = _label(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{label}: cannot read Dockerfile: {exc}"]

    match = re.search(r"^FROM\s+(\S+)", text, re.MULTILINE)
    if match is None:
        return [f"{label}: no FROM line found"]
    image = match.group(1)
    if image != DOCKER_IMAGE:
        return [f"{label}: Python image is {image!r}; expected {DOCKER_IMAGE!r}"]
    return []


def check_precommit_config(path: Path) -> list[str]:
    """Check the default hook interpreter and the consistency hook wiring."""
    label = _label(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{label}: cannot read config: {exc}"]

    errors: list[str] = []
    if not re.search(r"^default_language_version:\s*$", text, re.MULTILINE):
        errors.append(f"{label}: default_language_version is missing")
    if not re.search(r"^\s+python:\s*python3\.12\s*$", text, re.MULTILINE):
        errors.append(f"{label}: default Python is not python3.12")
    if "check-python-consistency" not in text:
        errors.append(f"{label}: check-python-consistency hook is missing")
    if "container/tools/linters/check_python_consistency.py" not in text:
        errors.append(f"{label}: consistency hook does not call the checker")
    return errors


def check_test_runner(path: Path) -> list[str]:
    """Check run-tests-venv.sh recreates a venv built for another Python."""
    label = _label(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{label}: cannot read test runner: {exc}"]

    errors: list[str] = []
    if 'PYTHON_VERSION="${PYTHON_VERSION:-3.12}"' not in text:
        errors.append(f"{label}: default PYTHON_VERSION is not 3.12")
    if 'uv venv --python "$PYTHON_VERSION"' not in text:
        errors.append(f'{label}: venv creation does not pass --python "$PYTHON_VERSION"')
    if "venv_python_version" not in text:
        errors.append(f"{label}: stale venv detection is missing")
    return errors


def _read_pyvenv_version(path: Path, label: str) -> tuple[str | None, list[str]]:
    """Read and normalize the version_info line from pyvenv.cfg."""
    cfg = path / "pyvenv.cfg"
    if not cfg.is_file():
        return None, [f"{label}: pyvenv.cfg is missing"]

    raw_version: str | None = None
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.startswith("version_info") and "=" in line:
                raw_version = line.split("=", 1)[1].strip()
                break
    except OSError as exc:
        return None, [f"{label}: cannot read pyvenv.cfg: {exc}"]

    if raw_version is None:
        return None, [f"{label}: pyvenv.cfg has no version_info"]

    version = _normalize_version(raw_version)
    if not raw_version.startswith(f"{TARGET_TEXT}."):
        return version, [f"{label}: pyvenv.cfg version_info={raw_version}; expected {TARGET_TEXT}.x"]
    return version, []


def _check_venv_python(path: Path, label: str) -> tuple[str | None, list[str]]:
    """Run bin/python and return its actual version plus diagnostics."""
    python = path / "bin" / "python"
    if not python.is_file():
        return None, [f"{label}: bin/python is missing"]

    try:
        result = _run(
            [
                str(python),
                "-c",
                'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")',
            ]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, [f"{label}: cannot run bin/python: {exc}"]

    if result.returncode != 0:
        return None, [f"{label}: bin/python failed: {result.stdout.strip()}"]

    actual_version = result.stdout.strip().splitlines()[-1]
    if not actual_version.startswith(f"{TARGET_TEXT}."):
        return actual_version, [f"{label}: bin/python reports {actual_version}; expected {TARGET_TEXT}.x"]
    return actual_version, []


def _check_venv_lib(path: Path, version: str | None, label: str) -> list[str]:
    """Check that lib/ contains exactly the expected pythonX.Y directory."""
    lib = path / "lib"
    if not lib.is_dir():
        return [f"{label}: lib/ is missing"]

    errors: list[str] = []
    site_dirs = sorted(child.name for child in lib.glob("python*") if child.is_dir())
    expected_dir = f"python{version.rsplit('.', 1)[0]}" if version and "." in version else f"python{TARGET_TEXT}"
    if expected_dir not in site_dirs:
        errors.append(f"{label}: expected {expected_dir}/, found {site_dirs or 'none'}")
    stale_dirs = [name for name in site_dirs if name != expected_dir]
    if stale_dirs:
        errors.append(f"{label}: stale site-packages dir(s): {', '.join(stale_dirs)}")
    return errors


def check_venv(path: Path) -> list[str]:
    """Validate an existing virtualenv; absent venvs are simply skipped."""
    if not path.exists():
        return []

    label = _label(path)
    version, errors = _read_pyvenv_version(path, label)
    actual_version, python_errors = _check_venv_python(path, label)
    errors.extend(python_errors)

    if version is not None and actual_version is not None and actual_version != version:
        errors.append(f"{label}: pyvenv.cfg says {version} but bin/python reports {actual_version}")

    errors.extend(_check_venv_lib(path, version, label))
    return errors


def _precommit_env() -> Path | None:
    """Locate the uv/pipx-style environment that owns the pre-commit executable."""
    executable = shutil.which("pre-commit")
    if executable is None:
        return None

    candidates: list[Path] = []
    uv = shutil.which("uv")
    if uv is not None:
        try:
            result = _run([uv, "tool", "dir"], timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            candidates.append(Path(result.stdout.strip()) / "pre-commit")

    resolved = Path(executable).resolve()
    if resolved.parent.name == "bin":
        candidates.append(resolved.parent.parent)

    for candidate in candidates:
        if (candidate / "pyvenv.cfg").is_file():
            return candidate
    return None


def check_precommit_tool(require: bool = False) -> list[str]:
    """Check the executable works and, when locatable, its venv is 3.12."""
    executable = shutil.which("pre-commit")
    if executable is None:
        if require:
            return ["pre-commit executable not found on PATH"]
        return []

    errors: list[str] = []
    try:
        result = _run([executable, "--version"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"cannot run pre-commit: {exc}"]

    if result.returncode != 0:
        errors.append(f"pre-commit --version failed: {result.stdout.strip()}")
    elif not result.stdout.startswith("pre-commit "):
        errors.append(f"unexpected pre-commit --version output: {result.stdout.strip()!r}")

    env = _precommit_env()
    if env is not None:
        errors.extend(check_venv(env))

    return errors


def check_repository(root: Path = REPO_ROOT, require_precommit: bool = False) -> list[str]:
    """Run every check against a repository checkout."""
    errors: list[str] = []
    errors.extend(check_interpreter())
    errors.extend(check_python_version_file(root / PYTHON_VERSION_FILE))
    for relative in PYPROJECTS:
        errors.extend(check_pyproject(root / relative))
    for relative in LOCKFILES:
        errors.extend(check_lockfile(root / relative))
    for relative in DOCKERFILES:
        errors.extend(check_dockerfile(root / relative))
    errors.extend(check_precommit_config(root / ".pre-commit-config.yaml"))
    errors.extend(check_test_runner(root / "container/tools/run-tests-venv.sh"))
    for relative in VENVS:
        errors.extend(check_venv(root / relative))
    errors.extend(check_precommit_tool(require=require_precommit))
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-precommit",
        action="store_true",
        help="fail when pre-commit is not installed (the pre-commit hook uses this)",
    )
    args = parser.parse_args(argv)

    errors = check_repository(require_precommit=args.require_precommit)
    if errors:
        print(f"Python {TARGET_TEXT} consistency check failed ({len(errors)} issue(s)):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Python {TARGET_TEXT} consistency check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
