#!/usr/bin/env bash
# version 0.1.0
# Sitemap link checker (kept as the short name of the old runner).
#
# Equivalent to: container/tests/run_sitemap_tests.sh --test link "$@"
# Use run_sitemap_tests.sh directly for the other suites (--test all).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/../tests/run_sitemap_tests.sh" --test link "$@"
