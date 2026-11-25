# version 0.2.1
"""Shared configuration: defaults, env overrides, tool URLs, constants."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

VALID_TOOLS = ("phpstan", "psalm", "phpcs")
FAILED_LIST_NAME = "phpcs-latest-commit-failed-list.log"

PHPSTAN_URL = "https://github.com/phpstan/phpstan/releases/latest/download/phpstan.phar"
PSALM_URL = "https://github.com/vimeo/psalm/releases/latest/download/psalm.phar"
PHPCS_URL = "https://github.com/PHPCSStandards/PHP_CodeSniffer/releases/latest/download/phpcs.phar"
PHPCBF_URL = "https://github.com/PHPCSStandards/PHP_CodeSniffer/releases/latest/download/phpcbf.phar"


@dataclass
class Config:  # pylint: disable=too-many-instance-attributes
    """All runtime options (mirrors lint-static.sh flags + env)."""

    src_dir: str = "src"
    src_set: bool = False
    tools: list[str] = field(default_factory=lambda: ["phpstan"])
    level: str = "1"
    standard: str = "/app/phpcs.xml"
    standard_set: bool = False
    report: str = ""
    fix: bool = False
    fix_page: bool = False
    no_failure_report: bool = False
    verbose: bool = False
    all: bool = False
    staged: bool = False
    committed: bool = False
    files: list[str] = field(default_factory=list)
    positional: list[str] = field(default_factory=list)
    worktree: str = ""
    fix_failed: bool = False
    # Failed list of the commit check; '' = <app_root>/log/FAILED_LIST_NAME.
    failed_list: str = field(default_factory=lambda: os.environ.get("PHPCS_FAILED_LIST", ""))
    tools_given: bool = False

    # Env-overridable runtime settings.
    php_image: str = field(default_factory=lambda: os.environ.get("PHP_ENGINE_STATIC_PHP_IMAGE", "php:8.5-cli"))
    tool_cache: str = field(
        default_factory=lambda: os.environ.get(
            "PHP_ENGINE_STATIC_TOOL_CACHE",
            os.path.join(os.path.expanduser("~"), ".cache", "php_engine-tools"),
        )
    )
    failed_to_fix_log: str = field(default_factory=lambda: os.environ.get("FAILED_TO_FIX_LOG", "log"))

    # Resolved at runtime (not CLI flags).
    start_dir: str = ""
    script_dir: str = ""
    app_root: str = ""  # container/ that is linted; --worktree moves it
    own_root: str = ""  # container/ of the checkout this script lives in
    ruff_rc: int = 0  # result of a ruff run offered by --fix
    src_abs: str = ""
    ruleset_file: str = ""


def default_config() -> Config:
    """Return a Config with env-based defaults applied."""
    return Config()


def validate_config(cfg: Config) -> None:
    """Validate flag combinations; raise ValueError on usage errors."""
    # main() offers the fixers first (fix_offer.offer_fix); this guards
    # direct callers.
    if cfg.fix and "phpcs" not in cfg.tools:
        raise ValueError("--fix needs --tool phpcs.")
    if cfg.fix_page and not cfg.fix:
        raise ValueError("--fix-page needs --fix.")
    if cfg.no_failure_report and not cfg.fix:
        raise ValueError("--no-failure-report needs --fix.")
    if cfg.fix and not (cfg.all or cfg.files or cfg.positional or cfg.staged or cfg.committed or cfg.src_set):
        raise ValueError("--fix refuses the whole-tree default (no scope given). Pass explicit paths (--files ... / paths...), --staged, --committed, --src DIR, or --all to fix everything.")
    if len(cfg.level) != 1 or not cfg.level.isdigit():
        raise ValueError(f"bad --level '{cfg.level}' (want 0-9).")
