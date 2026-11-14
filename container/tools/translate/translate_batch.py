"""Run the verified RU-to-EN pipeline on a backoffice-selected file list."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

from ru_en_translate import cmd_apply, cmd_extract, cmd_translate, cmd_verify, load_manifest

CONTAINER_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = CONTAINER_ROOT.parent
SRC_ROOT = CONTAINER_ROOT / "src"
DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "php_engine" / "translation-runs"
RUN_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
MAX_FILES = 250


def source_path(relative: str) -> Path:
    """Resolve only ordinary public PHP source files below container/src/."""
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or "\\" in relative:
        raise ValueError(f"Unsafe translation source path: {relative!r}")
    if any(part in {"", ".", ".."} or part.startswith((".", "_", "-")) for part in pure.parts[:-1]) or pure.name.startswith((".", "_", "-", "sess_")) or pure.suffix != ".php" or any(ord(character) < 32 for character in relative):
        raise ValueError(f"Unsafe translation source path: {relative!r}")
    candidate = SRC_ROOT.joinpath(*pure.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"Translation source is missing or is a symlink: {relative}")
    resolved = candidate.resolve()
    if SRC_ROOT.resolve() not in resolved.parents:
        raise ValueError(f"Translation source resolves outside src/: {relative}")
    return resolved


def load_selection(run_id: str) -> tuple[Path, tuple[str, ...]]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid translation run ID.")
    data_root = Path(os.environ.get("BACKOFFICE_TRANSLATION_DATA_DIR", "") or DEFAULT_DATA_ROOT).expanduser().resolve()
    if data_root == REPO_ROOT or REPO_ROOT in data_root.parents:
        raise ValueError("Translation work data must be stored outside the repository.")
    run_directory = (data_root / run_id).resolve()
    if run_directory.parent != data_root or not run_directory.is_dir():
        raise ValueError("Translation run selection was not found.")
    selection_file = run_directory / "selected_files.json"
    if selection_file.is_symlink() or not selection_file.is_file():
        raise ValueError("Translation run selection is missing or invalid.")
    selected = json.loads(selection_file.read_text(encoding="utf-8"))
    if not isinstance(selected, list) or not selected or len(selected) > MAX_FILES:
        raise ValueError("Translation selection is empty or exceeds the safety limit.")
    if any(not isinstance(item, str) for item in selected):
        raise ValueError("Translation selection contains an invalid path.")
    return run_directory, tuple(dict.fromkeys(selected))


def run_file(source: Path, relative: str, run_directory: Path, engine: str, model: str) -> str:
    if not CYRILLIC.search(source.read_text(encoding="utf-8")):
        print(f"[skip] {relative}: no Russian text remains")
        return "skipped"

    workdir = run_directory / "work" / Path(*PurePosixPath(relative).parts)
    extract_args = argparse.Namespace(file=str(source), workdir=str(workdir))
    cmd_extract(extract_args)
    manifest = load_manifest(workdir)
    if not manifest.segments:
        print(f"[skip] {relative}: extractor found no translatable segments")
        return "skipped"

    translate_args = argparse.Namespace(
        workdir=str(workdir),
        engine=engine,
        model=model,
        no_glossary=False,
        glossary="",
    )
    cmd_translate(translate_args)
    verify_status = cmd_verify(argparse.Namespace(workdir=str(workdir)))
    if verify_status != 0:
        print(f"[not applied] {relative}: verification failed; inspect {workdir}")
        return "failed"

    cmd_apply(argparse.Namespace(workdir=str(workdir), yes=True))
    print(f"[backup] {workdir / 'backup' / source.name}")
    return "translated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--engine", required=True, choices=("argos", "ollama"))
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)

    if args.engine == "ollama" and not args.model.strip():
        parser.error("--engine ollama requires --model")

    try:
        run_directory, selected = load_selection(args.run_id)
        sources = [(relative, source_path(relative)) for relative in selected]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    print(f"Translation engine: {args.engine}" + (f" ({args.model})" if args.model else ""))
    print(f"Persistent run data and backups: {run_directory}")
    translated = skipped = failed = 0
    for number, (relative, source) in enumerate(sources, 1):
        print(f"\n[{number}/{len(sources)}] {relative}")
        try:
            result = run_file(source, relative, run_directory, args.engine, args.model.strip())
        # Report engine and pipeline failures in the job console (deliberately broad).
        except Exception as error:  # pylint: disable=broad-exception-caught
            print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
            failed += 1
            print("Stopping the batch; earlier verified files remain applied with backups.", file=sys.stderr)
            break
        if result == "translated":
            translated += 1
        elif result == "skipped":
            skipped += 1
        else:
            failed += 1

    print(f"\nSummary: translated {translated}, skipped {skipped}, failed {failed} of {len(sources)} files.")
    print(f"Review translated changes with git diff. Backups are retained under {run_directory}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
