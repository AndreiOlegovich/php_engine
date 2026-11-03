# version 0.2.1
"""Resolve the external log root shared by the prod-log pipeline.

Default: ${XDG_DATA_HOME:-$HOME/.local/share}/php_engine/logs
Override: SITE_LOG_DIR=/absolute/path

Downloaded logs and device profiles live outside the repository; generated
databases (metrika.db, users.db) and reference lists stay beside the scripts.
"""

from __future__ import annotations

import os


def log_root() -> str:
    """Absolute path of the external log directory."""
    override = os.environ.get("SITE_LOG_DIR", "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
    base = os.path.expanduser(xdg_data) if xdg_data else os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.abspath(os.path.join(base, "php_engine", "logs"))


def prod_dir() -> str:
    """Prod log tree root (access/ and error/ below it)."""
    return os.path.join(log_root(), "prod")


def prod_access_dir() -> str:
    """Prod Apache access logs."""
    return os.path.join(prod_dir(), "access")


def prod_error_dir() -> str:
    """Prod Apache error logs."""
    return os.path.join(prod_dir(), "error")


def devices_dir() -> str:
    """Owner device profiles pulled from Dev."""
    return os.path.join(log_root(), "devices")


def user_profiles_dir() -> str:
    """Staged user-profile logs pulled from Dev."""
    return os.path.join(log_root(), "user_profiles")


def botlist_path() -> str:
    """Bot IP list written by check_bots.py; it holds client IPs, so it lives with the logs."""
    return os.path.join(log_root(), "botlist.csv")
