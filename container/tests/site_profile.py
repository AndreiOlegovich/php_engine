"""Read container/site-profile.env once; every page test asks here.

A key that is missing returns the default, and a test whose key is unset
skips with a clear message instead of guessing one site's markup.
"""

from __future__ import annotations

import os
from pathlib import Path

PROFILE = Path(os.environ.get("SITE_PROFILE", Path(__file__).resolve().parents[1] / "site-profile.env"))


def _read() -> dict[str, str]:
    values: dict[str, str] = {}
    if not PROFILE.is_file():
        return values
    for line in PROFILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_VALUES = _read()


def get(key: str, default: str = "") -> str:
    """Env wins over the profile, the profile over the default."""
    return os.environ.get(key, _VALUES.get(key, default))


def get_list(key: str, default: str = "") -> list[str]:
    """A comma or space separated list from env or profile."""
    raw = get(key, default).replace(",", " ")
    return [item for item in raw.split() if item]
