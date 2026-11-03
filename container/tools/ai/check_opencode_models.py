"""Check that every OpenCode model id this repo pins is still live.

Reads the model ids pinned in opencode.jsonc and .opencode/agents/*.md, then
asks the OpenCode models endpoint which ids exist right now. A missing free
id (ending in -free) is replaced by the first live id from free-models.txt.
When no listed free id is live, a paid id is gone, or the endpoint cannot be
reached, it asks before going on. Paid ids are never switched on its own.

Usage (any working directory):
  python3 container/tools/ai/check_opencode_models.py          # check, switch, ask
  python3 container/tools/ai/check_opencode_models.py --check  # report only

Exit: 0 every pinned id is live (after a switch, or confirmed by you),
1 a paid id is missing, 2 a free id is missing and nothing replaced it,
3 the models endpoint could not be reached.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

MODELS_URL = "https://opencode.ai/inference/v1/models"
REPO_ROOT = Path(__file__).resolve().parents[3]
FREE_LIST = Path(__file__).resolve().with_name("free-models.txt")
# Offered only when no free id is live; chosen by a person, never automatically.
PAID_FALLBACKS = ("gpt-6-luna", "deepseek-v4.1-flash")

_JSON_MODEL = re.compile(r'"model"\s*:\s*"opencode/([^"#]+)')
_MD_MODEL = re.compile(r"""^model:\s*["']?opencode/([^\s"'#]+)""", re.MULTILINE)

Ask = Callable[[str], str]
Fetch = Callable[[], set[str]]


def config_files(root: Path) -> list[Path]:
    """Files that may pin an opencode/<id> model."""
    files = [root / "opencode.jsonc", root / ".opencode" / "opencode.jsonc"]
    for sub in ("agents", "commands"):
        files += sorted((root / ".opencode" / sub).rglob("*.md"))
    return [path for path in files if path.is_file()]


def pinned_ids(root: Path) -> dict[str, list[Path]]:
    """Model id (without provider and variant) -> files that pin it."""
    pins: dict[str, list[Path]] = {}
    for path in config_files(root):
        pattern = _JSON_MODEL if path.suffix == ".jsonc" else _MD_MODEL
        for model in dict.fromkeys(pattern.findall(path.read_text(encoding="utf-8"))):
            pins.setdefault(model, []).append(path)
    return pins


def read_free_list(path: Path) -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line.removeprefix("opencode/") for line in lines if line]


def fetch_live(url: str = MODELS_URL, timeout: float = 15.0) -> set[str]:
    """Ids the endpoint lists right now; OSError or ValueError when unreachable."""
    # The endpoint answers 403 to the default Python-urllib User-Agent.
    request = urllib.request.Request(url, headers={"User-Agent": "php_engine-model-check/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https URL)
        payload = json.load(response)
    return {item["id"] for item in payload["data"]}


def write_atomic(path: Path, text: str) -> None:
    """Rename a complete temp file over path (container/docs/atomic-file-writes.md)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def switch_model(files: list[Path], old: str, new: str) -> None:
    pattern = re.compile(rf"opencode/{re.escape(old)}(?![\w.-])")
    for path in files:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
        write_atomic(path, pattern.sub(f"opencode/{new}", text))


def _relative(files: list[Path], root: Path) -> str:
    return ", ".join(str(path.relative_to(root)) for path in files)


def _pick_by_hand(live: set[str], ask: Ask) -> str | None:
    """Let a person choose any live free id or a paid fallback."""
    choices = sorted(model for model in live if model.endswith("-free"))
    choices += [model for model in PAID_FALLBACKS if model in live]
    for number, model in enumerate(choices, 1):
        cost = "free" if model.endswith("-free") else "paid"
        print(f"  {number}. opencode/{model} ({cost})")
    answer = ask("Pick a number, or press Enter to stop: ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(choices):
        return choices[int(answer) - 1]
    return None


def _replace_free(old: str, files: list[Path], live: set[str], ask: Ask | None, free_list: list[str]) -> bool:
    """Switch a missing free id; True when it was replaced."""
    new = next((model for model in free_list if model in live), None)
    if new is not None:
        how = "first live id in free-models.txt"
    elif ask is None:
        print(f"no id from free-models.txt is live; edit the list or pin a model by hand for opencode/{old}")
        return False
    else:
        print(f"opencode/{old} is gone and no id from free-models.txt is live. Live options:")
        new, how = _pick_by_hand(live, ask), "chosen by hand"
        if new is None:
            return False
    print(f"switched: opencode/{old} -> opencode/{new} ({how})")
    switch_model(files, old, new)
    return True


def _confirm(ask: Ask | None, question: str) -> bool:
    return ask is not None and ask(question).strip().lower() in {"y", "yes"}


def run(root: Path, fetch: Fetch, ask: Ask | None, free_list: list[str], check_only: bool = False) -> int:
    """Check, switch missing free ids, ask when stuck; return the exit code."""
    pins = pinned_ids(root)
    if not pins:
        print("no opencode/<id> model pinned; nothing to check")
        return 0
    try:
        live = fetch()
    except (OSError, ValueError, KeyError) as error:
        print(f"cannot read {MODELS_URL}: {error}")
        return 0 if _confirm(ask, "Start anyway with the current models? [y/N] ") else 3

    status = 0
    for model, files in sorted(pins.items()):
        where = _relative(files, root)
        if model in live:
            print(f"ok:      opencode/{model} ({where})")
            preferred = next((m for m in free_list if m in live), None)
            if model.endswith("-free") and preferred not in (None, model):
                print(f"note:    opencode/{preferred} ranks higher in free-models.txt and is live again")
        elif not model.endswith("-free"):
            print(f"missing: opencode/{model} ({where}) is a paid id; choose its replacement by hand")
            status = max(status, 0 if _confirm(ask, "Start anyway? [y/N] ") else 1)
        elif check_only:
            nxt = next((m for m in free_list if m in live), None)
            print(f"missing: opencode/{model} ({where}); next live free id: {nxt or 'none in free-models.txt'}")
            status = max(status, 2)
        elif not _replace_free(model, files, live, ask, free_list):
            status = max(status, 2)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="report only: never write files or ask")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: this checkout)")
    args = parser.parse_args(argv)
    interactive = not args.check and sys.stdin.isatty()
    return run(args.root, fetch_live, input if interactive else None, read_free_list(FREE_LIST), args.check)


if __name__ == "__main__":
    sys.exit(main())
