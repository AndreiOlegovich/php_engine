#!/usr/bin/env python3
# version 0.1.3
"""Download the hosting provider's logs of a time window over SSH.

Finds every log on the hosting account and downloads the files that hold
lines from the chosen window:

  ~/logs/<domain>/http/, ~/logs/<domain>/https/
      Apache access.log and error.log with their rotated copies
      (error.log.2026-11-05, ...). PHP messages land in error.log, marked
      "mod_fcgid: stderr", while error_log in phprc is empty.
  error_log set in ~/.php/<version>/phprc
      the PHP error log, when one is set, with its rotated copies.
  *.log, *_log and their rotated copies in the home folder (docroots included)
      PHP error_log files and application logs; hidden folders are skipped.

Window (local time of this machine, one flag at most):
  --today        since 00:00 today
  --yesterday    00:00 yesterday to 00:00 today (the default)
  --week         since 00:00 Monday of this week
  --days N       the last N days, today included (--days 1 = --today)

A file is taken when its lines overlap the window: rotated copies of one
log follow each other, so a file covers the time from the previous copy's
last write to its own. Whole files are downloaded, so the first and last
file can hold lines just outside the window.

Files land under the external log root with the remote home layout kept:
  ${SITE_LOG_DIR:-~/.local/share/<project>/logs}/hosting/logs/<domain>/https/access.log
rsync copies only files that changed since the last run.

SSH target, from a flag, the environment or ~/.config/<project>/hosting-access.env
(template: container/tools/deploy/hosting-access.env.template):
  HOSTING_SSH=<user>@<server>.example.com   (or a Host alias of ~/.ssh/config)
  HOSTING_KEY=~/.ssh/hosting-upload         (default when the file exists)

Usage:
  fetch_hosting_logs.py [--today | --yesterday | --week | --days N]
                          [--host TARGET] [--key PATH] [--config PATH]
                          [--dest DIR] [--list] [-v]

Exit 0 when done (also when the window holds no log), 1 on SSH or rsync
errors, 2 on bad options or a missing SSH target.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from log_paths import log_root
from project_name import project_name  # pylint: disable=import-error

DEFAULT_CONFIG = Path(f"~/.config/{project_name()}/hosting-access.env").expanduser()
DEFAULT_KEY = Path("~/.ssh/hosting-upload").expanduser()
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=15")

# Runs on the hosting with sh; prints "<mtime>\t<size>\t<path>" per file,
# paths relative to the remote home. GNU find (-printf) is on the hosting server.
REMOTE_LIST = r"""
cd "$HOME" || exit 1
if [ -d logs ]; then
  # logs/<domain>/http and https are symlinks into /home/_domain_logs/;
  # access.log.0 is a symlink to the newest dated copy, so skip file links.
  find -L logs -type f ! -xtype l -printf '%T@\t%s\t%p\n' 2>/dev/null
fi
find . -mindepth 1 -maxdepth 6 \
  \( -name '.*' -o -name node_modules -o -path ./logs \) -prune -o \
  -type f \( -name '*.log' -o -name '*_log' -o -name '*.log.[0-9]*' -o -name '*_log.[0-9]*' \
  -o -name '*.log.gz' -o -name '*_log.gz' \) \
  -printf '%T@\t%s\t%P\n' 2>/dev/null
for rc in .php/*/phprc; do
  [ -f "$rc" ] || continue
  sed -n 's/^[[:space:]]*error_log[[:space:]]*=[[:space:]]*//p' "$rc" | tr -d "\"'\r" |
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in "$HOME"/*) p=${p#"$HOME"/} ;; esac
    for f in "$p" "$p".*; do
      [ -f "$f" ] && find "$f" -maxdepth 0 -printf '%T@\t%s\t%p\n' 2>/dev/null
    done
  done
done
exit 0
"""

# Rotation suffix of a log name: .1, .2026-11-05, -20261105, then .gz etc.
ROTATION_SUFFIX = re.compile(r"(?:[.-](?:\d{4}-?\d{2}-?\d{2}|\d+))?(?:\.(?:gz|bz2|xz|zst))?$")


@dataclass(frozen=True)
class RemoteFile:
    path: str  # relative to the remote home
    size: int
    mtime: float


def kind_of(path: str) -> str:
    name = posixpath.basename(path).lower()
    if "access" in name:
        return "access"
    if "php" in name:
        return "php"
    if "error" in name:
        return "error"
    return "other"


def series_key(path: str) -> tuple[str, str]:
    """Folder and log name without the rotation suffix: one key per log."""
    folder, name = posixpath.split(path)
    return folder, ROTATION_SUFFIX.sub("", name, count=1)


def parse_listing(text: str) -> tuple[list[RemoteFile], list[str]]:
    """Parse REMOTE_LIST output; returns the files and the skipped paths."""
    files: dict[str, RemoteFile] = {}
    skipped: list[str] = []
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        mtime, size, path = parts
        path = path.removeprefix("./")
        if path.startswith("/") or path.startswith("../"):
            skipped.append(path)  # outside the home folder
            continue
        try:
            files[path] = RemoteFile(path, int(size), float(mtime))
        except ValueError:
            continue
    return sorted(files.values(), key=lambda f: f.path), skipped


def select(files: list[RemoteFile], start: float, end: float) -> list[RemoteFile]:
    """Files whose lines overlap [start, end).

    Copies of one log follow each other in time: a copy covers the span from
    the previous copy's last write to its own. The oldest copy of a log, or a
    log never rotated, may start any time before its last write.
    """
    series: dict[tuple[str, str], list[RemoteFile]] = {}
    for item in files:
        series.setdefault(series_key(item.path), []).append(item)
    chosen: list[RemoteFile] = []
    for copies in series.values():
        copies.sort(key=lambda f: f.mtime)
        previous = float("-inf")
        for item in copies:
            if item.mtime >= start and previous < end:
                chosen.append(item)
            previous = item.mtime
    return sorted(chosen, key=lambda f: (kind_of(f.path), f.path))


def window(args: argparse.Namespace, now: datetime) -> tuple[datetime, datetime, str]:
    midnight = datetime.combine(now.date(), time.min)
    if args.today:
        return midnight, now, "today"
    if args.week:
        return midnight - timedelta(days=midnight.weekday()), now, "this week"
    if args.days is not None:
        return midnight - timedelta(days=args.days - 1), now, f"the last {args.days} days"
    return midnight - timedelta(days=1), midnight, "yesterday"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep:
            values[key.strip()] = value.strip().strip("'\"")
    return values


def ssh_command(key: Path | None) -> list[str]:
    command = ["ssh", *SSH_OPTIONS]
    if key is not None:
        command += ["-i", str(key), "-o", "IdentitiesOnly=yes"]
    return command


def list_remote(target: str, ssh: list[str]) -> str:
    result = subprocess.run([*ssh, target, "sh -s"], input=REMOTE_LIST, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"listing the logs over SSH failed: {detail}")
    return result.stdout


def download(files: list[RemoteFile], source: str, dest: Path, ssh: list[str], verbose: bool) -> None:
    """rsync the files (paths relative to source) into dest, keeping mtimes."""
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        handle.write("".join(f"{item.path}\n" for item in files))
        list_file = handle.name
    try:
        # --copy-links: the remote log folders are symlinks; copy what they point to.
        command = ["rsync", "--times", "--partial", "--copy-links", "--chmod=Du=rwx,Dgo=,Fu=rw,Fgo=", f"--files-from={list_file}", "-e", shlex.join(ssh), source, f"{dest}/"]
        if verbose:
            command.insert(1, "--itemize-changes")
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"rsync failed with exit {result.returncode}")
    finally:
        os.unlink(list_file)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    span = parser.add_mutually_exclusive_group()
    span.add_argument("--today", action="store_true", help="since 00:00 today")
    span.add_argument("--yesterday", action="store_true", help="00:00 yesterday to 00:00 today (default)")
    span.add_argument("--week", action="store_true", help="since 00:00 Monday of this week")
    span.add_argument("--days", type=positive_int, metavar="N", help="the last N days, today included")
    parser.add_argument("--host", help="SSH target: user@server or a ~/.ssh/config alias (default: HOSTING_SSH)")
    parser.add_argument("--key", type=Path, help=f"SSH private key (default: HOSTING_KEY, else {DEFAULT_KEY} when it exists)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=f"settings file (default: {DEFAULT_CONFIG})")
    parser.add_argument("--dest", type=Path, help="local folder (default: <log root>/hosting)")
    parser.add_argument("--list", action="store_true", help="list the files of the window, download nothing")
    parser.add_argument("-v", "--verbose", action="store_true", help="show the SSH target and rsync changes")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = read_env_file(args.config)
    target = args.host or os.environ.get("HOSTING_SSH") or settings.get("HOSTING_SSH", "")
    if not target:
        print(f"ERROR: no SSH target. Pass --host user@server, or set HOSTING_SSH in {args.config}.", file=sys.stderr)
        return 2
    key_text = str(args.key) if args.key else os.environ.get("HOSTING_KEY") or settings.get("HOSTING_KEY", "")
    key: Path | None = Path(key_text).expanduser() if key_text else None
    if key is not None and not key.is_file():
        print(f"ERROR: SSH key {key} not found.", file=sys.stderr)
        return 2
    if key is None and DEFAULT_KEY.is_file():
        key = DEFAULT_KEY
    dest: Path = (args.dest or Path(log_root()) / "hosting").expanduser()

    start, end, label = window(args, datetime.now())
    print(f"Window: {label}, {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} (local time)")
    ssh = ssh_command(key)
    if args.verbose:
        print(f"SSH: {shlex.join([*ssh, target])}")

    try:
        files, skipped = parse_listing(list_remote(target, ssh))
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for path in skipped:
        print(f"skipped, outside the home folder: {path}", file=sys.stderr)
    chosen = select(files, start.timestamp(), end.timestamp())
    print(f"Found {len(files)} log files on the hosting, {len(chosen)} in the window.")
    for item in chosen:
        stamp = datetime.fromtimestamp(item.mtime)
        print(f"  {kind_of(item.path):<6} {human_size(item.size):>9}  {stamp:%Y-%m-%d %H:%M}  {item.path}")
    if not chosen or args.list:
        return 0

    try:
        download(chosen, f"{target}:", dest, ssh, args.verbose)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    total = sum(item.size for item in chosen)
    print(f"Downloaded {len(chosen)} files ({human_size(total)}) into {dest}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
