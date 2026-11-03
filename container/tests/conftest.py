# version 0.0.3
"""Terminal progress for long runs (the suite lives here).

Default: print `tested N/TOTAL urls` every 100 tests (stdout, flushed,
so it shows through `docker compose run` buffering).
Verbose URL listing comes from pytest's own `-v` (see `--verbose` in
run_test.sh), not from here.
"""

# pylint: disable=invalid-name
_state = {"tested": 0, "total": 0}


def pytest_collection_modifyitems(items):
    """Remember the test count for the progress denominator."""
    _state["total"] = len(items)


def pytest_runtest_logreport(report):
    """Count finished calls; print progress every 100."""
    if report.when != "call":
        return
    _state["tested"] += 1
    if _state["total"] and _state["tested"] % 100 == 0:
        print(f"\ntested {_state['tested']}/{_state['total']} urls", flush=True)


def pytest_addoption(parser):
    """Add --tag: body substring to check (default: bottomid marker)."""
    parser.addoption(
        "--tag",
        action="store",
        default=None,
        help="literal substring to find in the page body",
    )
    parser.addoption(
        "--base-url",
        action="store",
        default=None,
        help="base URL for UI tests (overrides BASE_URL env, default: http://127.0.0.1:8080)",
    )
    parser.addoption(
        "--headed",
        action="store_true",
        default=False,
        help="run browser headed (visible) instead of headless",
    )
