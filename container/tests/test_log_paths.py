# version 0.1.2
"""Tests for the external log-root resolver."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ANALYZE_LOGS = Path(__file__).resolve().parents[1] / "tools" / "analyze_logs"


def _load_log_paths():
    spec = importlib.util.spec_from_file_location("php_engine_log_paths", ANALYZE_LOGS / "log_paths.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_log_root_uses_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("SITE_LOG_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    log_paths = _load_log_paths()

    assert log_paths.log_root() == str(tmp_path / "xdg" / "php_engine" / "logs")
    assert log_paths.prod_access_dir() == str(tmp_path / "xdg" / "php_engine" / "logs" / "prod" / "access")
    assert log_paths.prod_error_dir() == str(tmp_path / "xdg" / "php_engine" / "logs" / "prod" / "error")
    assert log_paths.devices_dir() == str(tmp_path / "xdg" / "php_engine" / "logs" / "devices")
    assert log_paths.user_profiles_dir() == str(tmp_path / "xdg" / "php_engine" / "logs" / "user_profiles")
    assert log_paths.botlist_path() == str(tmp_path / "xdg" / "php_engine" / "logs" / "botlist.csv")


def test_override_expands_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SITE_LOG_DIR", "~/custom-logs")

    log_paths = _load_log_paths()

    assert log_paths.log_root() == str(tmp_path / "home" / "custom-logs")
