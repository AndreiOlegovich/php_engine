# version 0.1.1
"""Optional page subset for the sitemap tests (run_sitemap_tests.sh --paths-file).

PATHS_FILE names a file with one URL path per line, exact match (e.g.
/about/ or /section/page.php); lines not starting with / are ignored.
Unset or empty -> every URL is selected. The backoffice Tests tab writes
the file for its Directories / Latest commits page scope.
"""

import os
from urllib.parse import unquote, urlparse

import pytest

PATHS_FILE = os.environ.get("PATHS_FILE", "").strip()


def _key(url_or_path):
    """Compare decoded paths; /dir/index.php and /dir/ are the same page."""
    path = unquote(urlparse(url_or_path).path or "/")
    return path.removesuffix("index.php") if path.endswith("/index.php") else path


def _load(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        return frozenset(_key(line.strip()) for line in handle if line.startswith("/"))


SELECTED = _load(PATHS_FILE)


def in_selection(url_or_path):
    return SELECTED is None or _key(url_or_path) in SELECTED


def skip_if_nothing_selected(locs):
    """A page subset that leaves this test nothing to check skips it, not fails."""
    if SELECTED is not None and not locs:
        pytest.skip(f"none of the {len(SELECTED)} paths in PATHS_FILE apply to this test", allow_module_level=True)


def selection_label():
    return "(all)" if SELECTED is None else f"{len(SELECTED)} paths from {PATHS_FILE}"
